from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from lib.genres import (
    DEFAULT_GENRE_CATALOG,
    DEFAULT_GENRE_COLORS,
    RESERVED_GENRE_NAMES,
    UNCATEGORIZED_COLOR,
    apply_genre_alias,
    catalog_names,
    clean_genre_name,
    color_map,
    default_color_for,
    merge_orphan_genres,
    normalize_color,
    normalize_session_genre as _normalize_session_genre,
    parse_genre_catalog,
    validate_new_genre_name,
)
from lib.sandbox_config import effective_profile, normalize_profile
from lib.logical_sessions import (
    GENRES,
    LinkedRef,
    LogicalSession,
    dump_logical_sessions,
    load_logical_sessions,
    unlink_vesper_everywhere,
)

GROK_HOME = Path.home() / ".grok"
HUB_META_PATH = GROK_HOME / "vesper-hub.json"
LEGACY_ALIASES_PATH = GROK_HOME / "session-aliases.json"
OBSIDIAN_SESSIONS_DIR = Path.home() / "obsidian-vault/Second-Brain/Sessions/Vesper"
JST = ZoneInfo("Asia/Tokyo")


def normalize_session_genre(
    genre: str | None,
    catalog: list[str] | list[dict[str, str]] | tuple[str, ...] | None = None,
) -> str:
    """Direct Vesper genre: empty = unset; must be in the user catalog (no default to Other)."""
    if catalog is not None and catalog and isinstance(catalog[0], dict):
        names = catalog_names(catalog)
    else:
        names = list(catalog) if catalog is not None else None
    return _normalize_session_genre(genre, names)


def load_related_lilith_map(raw: Any) -> dict[str, list[LinkedRef]]:
    """Parse session_id -> related Lilith refs from hub meta."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[LinkedRef]] = {}
    for sid, items in raw.items():
        if not isinstance(sid, str) or not sid.strip():
            continue
        refs: list[LinkedRef] = []
        if isinstance(items, list):
            for item in items:
                ref = LinkedRef.from_dict(item)
                if ref and not any(r.id == ref.id for r in refs):
                    refs.append(ref)
        if refs:
            out[sid] = refs
    return out


def dump_related_lilith_map(data: dict[str, list[LinkedRef]]) -> dict[str, list[dict[str, str]]]:
    return {
        sid: [r.to_dict() for r in refs]
        for sid, refs in sorted(data.items())
        if refs
    }


@dataclass
class SessionRecord:
    id: str
    title: str
    created: str
    updated: str
    updated_display: str
    num_messages: int
    num_chat_messages: int
    path: str
    preview: str = ""
    alias: str = ""
    display_name: str = ""
    pinned: bool = False
    obsidian_path: str | None = None
    sandbox_profile: str = "off"
    command: str = ""
    cwd: str = ""
    project: str = ""
    project_display: str = ""
    is_empty: bool = False
    summary: str = ""
    summary_source: str = ""
    genre: str = ""  # direct genre (empty = unset)
    # Related Lilith (リラ / Grok chat) refs — manual only, freeform id/url + optional title
    related_lilith: list[LinkedRef] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "short_id": short_id(self.id),
            "title": self.title,
            "display_name": self.display_name,
            "alias": self.alias,
            "genre": self.genre,
            "related_lilith": [r.to_dict() for r in self.related_lilith],
            "related_lilith_count": len(self.related_lilith),
            "created": self.created,
            "updated": self.updated,
            "updated_display": self.updated_display,
            "num_messages": self.num_messages,
            "num_chat_messages": self.num_chat_messages,
            "preview": self.preview,
            "summary": self.summary,
            "summary_source": self.summary_source,
            "pinned": self.pinned,
            "obsidian_path": self.obsidian_path,
            "sandbox_profile": self.sandbox_profile,
            "command": self.command,
            "cwd": self.cwd,
            "project": self.project,
            "project_display": self.project_display,
            "is_empty": self.is_empty,
        }


def short_id(session_id: str) -> str:
    return session_id[:8]


def session_cwd(summary_path: Path, data: dict) -> str:
    cwd = (data.get("info") or {}).get("cwd") or ""
    if cwd:
        return cwd
    try:
        idx = summary_path.parts.index("sessions")
        if idx + 1 < len(summary_path.parts):
            return unquote(summary_path.parts[idx + 1])
    except ValueError:
        pass
    return ""


def project_label(cwd: str) -> str:
    if not cwd:
        return "(unknown)"
    home = str(Path.home())
    if cwd == home:
        return "~"
    if cwd.startswith(home + "/"):
        return "~" + cwd[len(home) :]
    return cwd


def project_display_name(cwd: str, store: HubStore) -> str:
    alias = (store.project_aliases.get(cwd) or "").strip()
    if alias:
        return alias
    return project_label(cwd)


def build_resume_command(session_id: str, sandbox_profile: str = "off") -> str:
    profile = (sandbox_profile or "off").strip() or "off"
    return f"GROK_SANDBOX={profile} grok -r {session_id} --sandbox {profile}"


def session_sandbox_profile(data: dict) -> str:
    """Sandbox fixed at session launch (summary.json). Not the global Pit default."""
    raw = data.get("sandbox_profile")
    if raw is not None and str(raw).strip():
        return normalize_profile(str(raw))
    # Older summaries without the field — fall back to current global default
    return effective_profile()


def extract_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return ""


def is_meaningful_user_text(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    if text.startswith("<command-name>") or text.startswith("<local-command-stdout>"):
        return False
    if text.startswith("<system-reminder>") or text.startswith("<user_info>"):
        return False
    return True


def is_empty_session(summary_path: Path, summary: dict) -> bool:
    if summary.get("num_messages", 0) > 0:
        return False

    chat_history = summary_path.parent / "chat_history.jsonl"
    if not chat_history.exists():
        return summary.get("num_chat_messages", 0) <= 1

    meaningful = 0
    for raw in chat_history.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if entry.get("type") not in {"user", "assistant"}:
            continue
        text = extract_message_text(entry.get("content"))
        if entry.get("type") == "assistant" and not text and entry.get("tool_calls"):
            meaningful += 1
            continue
        if is_meaningful_user_text(text):
            meaningful += 1

    return meaningful == 0


def fmt_updated(iso_ts: str) -> str:
    if not iso_ts:
        return "????-??-?? ??:??"
    dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).astimezone(JST)
    return dt.strftime("%Y-%m-%d %H:%M")


SUMMARY_CONTENT_RE = re.compile(r"<summary_content>\s*(.*?)\s*</summary_content>", re.DOTALL)


def truncate_text(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def clean_user_query(text: str) -> str:
    text = re.sub(r"<user_query>\s*", "", text)
    text = re.sub(r"\s*</user_query>", "", text)
    return " ".join(text.split()).strip()


def normalize_summary_text(text: str, limit: int = 3200) -> str:
    raw = text.strip()
    match = SUMMARY_CONTENT_RE.search(raw)
    if match:
        raw = match.group(1).strip()
    raw = re.sub(r"<transcript_location>.*?</transcript_location>", "", raw, flags=re.DOTALL)
    raw = re.sub(r"Total summaries generated so far[^\n]*\n?", "", raw)
    raw = re.sub(
        r"Your conversation was summarized due to context constraints\.[^\n]*\n*",
        "",
        raw,
        count=1,
    )
    raw = re.sub(r"If the task is complete, respond to the user\.[^\n]*\n?", "", raw)
    return truncate_text(raw.strip(), limit)


def extract_compaction_summary(summary_path: Path) -> str:
    chat_history = summary_path.parent / "chat_history.jsonl"
    if not chat_history.exists():
        return ""

    latest = ""
    for raw in chat_history.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "user":
            continue
        text = extract_message_text(entry.get("content"))
        if "<summary_content>" not in text:
            continue
        match = SUMMARY_CONTENT_RE.search(text)
        if match:
            latest = match.group(1).strip()
    return latest


def build_heuristic_summary(summary_path: Path, data: dict, limit: int = 3200) -> str:
    chat_history = summary_path.parent / "chat_history.jsonl"
    title = (data.get("generated_title") or data.get("session_summary") or "").strip()

    if not chat_history.exists():
        return title

    first_query = ""
    user_snippets: list[str] = []
    last_assistant = ""

    for raw in chat_history.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue

        entry_type = entry.get("type")
        if entry_type == "user":
            if entry.get("synthetic_reason") in {"compaction_meta", "system_reminder"}:
                continue
            text = extract_message_text(entry.get("content"))
            if not is_meaningful_user_text(text):
                continue
            text = clean_user_query(text)
            if not text:
                continue
            if not first_query:
                first_query = text
            user_snippets.append(text)
        elif entry_type == "assistant":
            text = extract_message_text(entry.get("content"))
            if not text or text.startswith("Your organization has disabled"):
                continue
            if entry.get("model_id") == "<synthetic>" and len(text) < 40:
                continue
            last_assistant = text

    parts: list[str] = []
    if title and title != "(no title)":
        parts.append(title)

    if first_query and first_query != title:
        parts.append(f"Started: {truncate_text(first_query, 420)}")

    recent_users: list[str] = []
    seen: set[str] = set()
    for snippet in reversed(user_snippets):
        key = snippet[:100]
        if key in seen:
            continue
        seen.add(key)
        recent_users.append(truncate_text(snippet, 320))
        if len(recent_users) >= 3:
            break
    recent_users.reverse()

    if recent_users and (len(recent_users) > 1 or recent_users[0] != first_query):
        parts.append("Recent topics:")
        parts.extend(f"• {snippet}" for snippet in recent_users)

    if last_assistant:
        parts.append(f"Latest response:\n{truncate_text(last_assistant, 1000)}")

    if not parts:
        return title

    return truncate_text("\n\n".join(parts), limit)


def extract_session_summary(summary_path: Path, data: dict, limit: int = 3200) -> tuple[str, str]:
    compaction = extract_compaction_summary(summary_path)
    if compaction:
        return normalize_summary_text(compaction, limit), "compaction"

    heuristic = build_heuristic_summary(summary_path, data, limit)
    if heuristic:
        return heuristic, "heuristic"

    title = (data.get("generated_title") or data.get("session_summary") or "").strip()
    return title, "title"


def extract_preview(summary_path: Path, limit: int = 120) -> str:
    """Short sidebar/detail fallback: latest meaningful user message."""
    chat_history = summary_path.parent / "chat_history.jsonl"
    if not chat_history.exists():
        return ""

    latest = ""
    for raw in chat_history.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "user":
            continue
        if entry.get("synthetic_reason") in {"compaction_meta", "system_reminder"}:
            continue
        text = extract_message_text(entry.get("content"))
        if not is_meaningful_user_text(text):
            continue
        latest = clean_user_query(text)

    if not latest:
        return ""

    return truncate_text(latest, limit)


def find_obsidian_note(session_id: str) -> str | None:
    """Locate curated summary note under Sessions/Vesper/** (genre subfolders included)."""
    if not OBSIDIAN_SESSIONS_DIR.exists():
        return None
    sid = short_id(session_id)
    # Genre folders: …/Vesper/<Genre>/YYYY-MM-DD-…-<sid8>.md
    matches = sorted(OBSIDIAN_SESSIONS_DIR.rglob(f"*-{sid}.md"))
    if not matches:
        matches = sorted(OBSIDIAN_SESSIONS_DIR.rglob(f"*{sid}*.md"))
    for path in matches:
        if path.name.endswith("-transcript.md"):
            continue
        # Prefer notes inside a genre folder; skip top-level canvas/index if any
        if path.suffix != ".md":
            continue
        return str(path)
    return None


@dataclass
class HubStore:
    aliases: dict[str, str] = field(default_factory=dict)
    project_aliases: dict[str, str] = field(default_factory=dict)
    # Direct per-Vesper-session genre (session_id -> catalog label). Empty/missing = unset.
    genres: dict[str, str] = field(default_factory=dict)
    # User-managed genre list: [{name, color}, ...] (order preserved).
    genre_catalog: list[dict[str, str]] = field(
        default_factory=lambda: parse_genre_catalog(None)
    )
    # Direct per-Vesper related Lilith session refs (manual id/url + optional title).
    related_lilith: dict[str, list[LinkedRef]] = field(default_factory=dict)
    pinned: list[str] = field(default_factory=list)
    settings: dict[str, Any] = field(
        default_factory=lambda: {
            "port": 7420,
            "hide_empty": True,
        }
    )
    # Legacy: logical sessions kept on disk; UI no longer exposes create/manage.
    logical_sessions: list[LogicalSession] = field(default_factory=list)

    @classmethod
    def load(cls) -> HubStore:
        cls.migrate_legacy_aliases()
        if not HUB_META_PATH.exists():
            return cls()
        data = json.loads(HUB_META_PATH.read_text(encoding="utf-8"))
        raw_settings = data.get("settings") or {}
        catalog = parse_genre_catalog(data.get("genre_catalog"))

        # First pass: collect raw session genre values (with alias map) to merge orphans.
        raw_genres = data.get("genres") or {}
        raw_values: list[str] = []
        if isinstance(raw_genres, dict):
            for v in raw_genres.values():
                if isinstance(v, str) and v.strip():
                    raw_values.append(apply_genre_alias(v))
        catalog = merge_orphan_genres(catalog, raw_values)

        genres: dict[str, str] = {}
        if isinstance(raw_genres, dict):
            for k, v in raw_genres.items():
                if not isinstance(k, str):
                    continue
                ng = normalize_session_genre(v if isinstance(v, str) else "", catalog)
                if ng:
                    genres[k] = ng
        return cls(
            aliases={k: v for k, v in (data.get("aliases") or {}).items() if isinstance(v, str)},
            project_aliases={
                k: v for k, v in (data.get("project_aliases") or {}).items() if isinstance(v, str)
            },
            genres=genres,
            genre_catalog=catalog,
            related_lilith=load_related_lilith_map(data.get("related_lilith")),
            pinned=[p for p in (data.get("pinned") or []) if isinstance(p, str)],
            settings={
                "port": int(raw_settings.get("port", 7420)),
                "hide_empty": bool(raw_settings.get("hide_empty", True)),
            },
            logical_sessions=load_logical_sessions(data.get("logical_sessions")),
        )

    def save(self) -> None:
        """Atomic write so a crash mid-save cannot wipe meta."""
        HUB_META_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "aliases": dict(sorted(self.aliases.items())),
            "project_aliases": dict(sorted(self.project_aliases.items())),
            "genres": dict(sorted(self.genres.items())),
            "genre_catalog": [
                {"name": e["name"], "color": e["color"]} for e in self.genre_catalog
            ],
            "related_lilith": dump_related_lilith_map(self.related_lilith),
            "pinned": self.pinned,
            "settings": self.settings,
            "logical_sessions": dump_logical_sessions(self.logical_sessions),
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        tmp = HUB_META_PATH.with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, HUB_META_PATH)

    def _genre_names(self) -> list[str]:
        return catalog_names(self.genre_catalog)

    def _find_genre_entry(self, name: str) -> dict[str, str] | None:
        target = apply_genre_alias(name) or clean_genre_name(name)
        if not target:
            return None
        for e in self.genre_catalog:
            if e["name"] == target:
                return e
        t_l = target.lower()
        for e in self.genre_catalog:
            if e["name"].lower() == t_l:
                return e
        return None

    def get_genre_catalog(self) -> list[str]:
        """Ordered genre names (backward-compatible)."""
        return self._genre_names()

    def get_genre_items(self) -> list[dict[str, str]]:
        return [{"name": e["name"], "color": e["color"]} for e in self.genre_catalog]

    def get_genre_colors(self) -> dict[str, str]:
        return color_map(self.genre_catalog)

    def get_genre_color(self, name: str) -> str:
        e = self._find_genre_entry(name)
        if e:
            return e["color"]
        g = apply_genre_alias(name)
        if not g:
            return UNCATEGORIZED_COLOR
        return DEFAULT_GENRE_COLORS.get(g, default_color_for(g))

    def genres_api_payload(self) -> dict[str, Any]:
        items = self.get_genre_items()
        return {
            "genres": [e["name"] for e in items],
            "colors": {e["name"]: e["color"] for e in items},
            "items": items,
            "uncategorized_color": UNCATEGORIZED_COLOR,
            "defaults": list(DEFAULT_GENRE_CATALOG),
            "default_colors": dict(DEFAULT_GENRE_COLORS),
        }

    def add_genre(self, name: str, color: str | None = None) -> str:
        """Append a genre to the catalog. Returns canonical name."""
        g = validate_new_genre_name(name, self.genre_catalog)
        c = normalize_color(color, fallback=default_color_for(g, len(self.genre_catalog)))
        self.genre_catalog.append({"name": g, "color": c})
        self.save()
        return g

    def rename_genre(
        self,
        old_name: str,
        new_name: str,
        color: str | None = None,
    ) -> tuple[str, int]:
        """Rename catalog entry and rewrite session assignments. Returns (new_name, n_sessions)."""
        entry = self._find_genre_entry(old_name)
        if not entry:
            raise ValueError(f"genre not found: {old_name}")
        old = entry["name"]

        new = clean_genre_name(new_name)
        if not new:
            raise ValueError("new genre name is required")
        others = [e["name"] for e in self.genre_catalog if e["name"] != old]
        if any(c.lower() == new.lower() for c in others):
            raise ValueError(f"genre already exists: {new}")
        if new in RESERVED_GENRE_NAMES or new.lower() in {
            r.lower() for r in RESERVED_GENRE_NAMES if r
        }:
            raise ValueError(f"reserved name: {new}")

        entry["name"] = new
        if color is not None and str(color).strip():
            entry["color"] = normalize_color(color, fallback=entry.get("color") or "#888894")
        updated = 0
        for sid, g in list(self.genres.items()):
            if g == old:
                self.genres[sid] = new
                updated += 1
        self.save()
        return new, updated

    def set_genre_color(self, name: str, color: str) -> str:
        """Update only the color of a catalog entry. Returns name."""
        entry = self._find_genre_entry(name)
        if not entry:
            raise ValueError(f"genre not found: {name}")
        entry["color"] = normalize_color(color, fallback=entry.get("color") or "#888894")
        self.save()
        return entry["name"]

    def delete_genre(self, name: str) -> tuple[str, int]:
        """Remove catalog entry; clear matching session genres → Unset. Returns (removed, n_cleared)."""
        entry = self._find_genre_entry(name)
        if not entry:
            raise ValueError(f"genre not found: {name}")
        target = entry["name"]

        self.genre_catalog = [e for e in self.genre_catalog if e["name"] != target]
        cleared = 0
        for sid, g in list(self.genres.items()):
            if g == target:
                del self.genres[sid]
                cleared += 1
        self.save()
        return target, cleared

    def reload_settings(self) -> None:
        """Reload settings from disk without full store rebuild."""
        if not HUB_META_PATH.exists():
            return
        data = json.loads(HUB_META_PATH.read_text(encoding="utf-8"))
        raw = data.get("settings") or {}
        self.settings["port"] = int(raw.get("port", self.settings.get("port", 7420)))
        self.settings["hide_empty"] = bool(raw.get("hide_empty", self.settings.get("hide_empty", True)))

    def reload_logical_sessions(self) -> None:
        """Reload logical sessions from disk (other writers / external edits)."""
        if not HUB_META_PATH.exists():
            self.logical_sessions = []
            return
        data = json.loads(HUB_META_PATH.read_text(encoding="utf-8"))
        self.logical_sessions = load_logical_sessions(data.get("logical_sessions"))

    @classmethod
    def migrate_legacy_aliases(cls) -> None:
        if not LEGACY_ALIASES_PATH.exists() or HUB_META_PATH.exists():
            return
        legacy = json.loads(LEGACY_ALIASES_PATH.read_text(encoding="utf-8"))
        store = cls(aliases={k: v for k, v in legacy.items() if isinstance(v, str)})
        store.save()

    def set_alias(self, session_id: str, name: str) -> None:
        name = name.strip()
        if name:
            self.aliases[session_id] = name
        elif session_id in self.aliases:
            del self.aliases[session_id]
        self.save()

    def set_genre(self, session_id: str, genre: str | None) -> str:
        """Set direct session genre. Empty clears. Returns normalized value stored (or "")."""
        ng = normalize_session_genre(genre, self._genre_names())
        if ng:
            self.genres[session_id] = ng
        elif session_id in self.genres:
            del self.genres[session_id]
        self.save()
        return ng

    def get_genre(self, session_id: str) -> str:
        return normalize_session_genre(self.genres.get(session_id, ""), self._genre_names())

    def get_related_lilith(self, session_id: str) -> list[LinkedRef]:
        return list(self.related_lilith.get(session_id) or [])

    def link_related_lilith(self, session_id: str, ref_id: str, title: str = "") -> LinkedRef:
        """Add or update a related Lilith ref on a Vesper session. Returns the ref."""
        ref = LinkedRef.from_dict({"id": ref_id, "title": title})
        if not ref:
            raise ValueError("id is required")
        rows = list(self.related_lilith.get(session_id) or [])
        for existing in rows:
            if existing.id == ref.id:
                if title.strip():
                    existing.title = ref.title
                    self.related_lilith[session_id] = rows
                    self.save()
                return existing
        rows.append(ref)
        self.related_lilith[session_id] = rows
        self.save()
        return ref

    def unlink_related_lilith(self, session_id: str, ref_id: str) -> bool:
        rid = (ref_id or "").strip()
        if not rid:
            return False
        rows = list(self.related_lilith.get(session_id) or [])
        before = len(rows)
        rows = [r for r in rows if r.id != rid]
        if len(rows) == before:
            return False
        if rows:
            self.related_lilith[session_id] = rows
        elif session_id in self.related_lilith:
            del self.related_lilith[session_id]
        self.save()
        return True

    def set_project_alias(self, cwd: str, name: str) -> None:
        cwd = cwd.strip()
        name = name.strip()
        if not cwd:
            return
        if name:
            self.project_aliases[cwd] = name
        elif cwd in self.project_aliases:
            del self.project_aliases[cwd]
        self.save()

    def toggle_pin(self, session_id: str) -> bool:
        if session_id in self.pinned:
            self.pinned = [p for p in self.pinned if p != session_id]
            pinned = False
        else:
            self.pinned = [session_id, *self.pinned]
            pinned = True
        self.save()
        return pinned


def discover_sessions(store: HubStore | None = None) -> list[SessionRecord]:
    store = store or HubStore.load()
    hide_empty = store.settings.get("hide_empty", True)
    rows: list[SessionRecord] = []

    for summary_path in sorted((GROK_HOME / "sessions").rglob("summary.json")):
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        if hide_empty and is_empty_session(summary_path, data):
            continue

        sid = data.get("info", {}).get("id") or summary_path.parent.name
        title = (data.get("generated_title") or data.get("session_summary") or "(no title)").strip()
        alias = store.aliases.get(sid, "")
        display = alias or title
        # Fixed at launch — from summary.json, not the global Pit toolbar default.
        sandbox = session_sandbox_profile(data)
        cwd = session_cwd(summary_path, data)
        empty = is_empty_session(summary_path, data)
        proj = project_label(cwd)
        summary, summary_source = extract_session_summary(summary_path, data)

        rows.append(
            SessionRecord(
                id=sid,
                title=title,
                created=data.get("created_at", ""),
                updated=data.get("updated_at", data.get("created_at", "")),
                updated_display=fmt_updated(data.get("updated_at", data.get("created_at", ""))),
                num_messages=int(data.get("num_messages", 0)),
                num_chat_messages=int(data.get("num_chat_messages", 0)),
                path=str(summary_path.parent),
                preview=extract_preview(summary_path),
                alias=alias,
                display_name=display,
                pinned=sid in store.pinned,
                obsidian_path=find_obsidian_note(sid),
                sandbox_profile=sandbox,
                command=build_resume_command(sid, sandbox),
                cwd=cwd,
                project=proj,
                project_display=project_display_name(cwd, store),
                is_empty=empty,
                summary=summary,
                summary_source=summary_source,
                genre=store.get_genre(sid),
                related_lilith=store.get_related_lilith(sid),
            )
        )

    pinned_set = set(store.pinned)
    rows.sort(
        key=lambda r: (
            0 if r.id in pinned_set else 1,
            store.pinned.index(r.id) if r.id in pinned_set else 9999,
            r.updated,
        ),
        reverse=False,
    )
    # pinned first in pin order, then others by updated desc
    pinned_rows = [r for r in rows if r.id in pinned_set]
    pinned_rows.sort(key=lambda r: store.pinned.index(r.id))
    other_rows = [r for r in rows if r.id not in pinned_set]
    other_rows.sort(key=lambda r: r.updated, reverse=True)
    return pinned_rows + other_rows


def _session_from_summary(summary_path: Path, data: dict, store: HubStore) -> SessionRecord:
    sid = data.get("info", {}).get("id") or summary_path.parent.name
    title = (data.get("generated_title") or data.get("session_summary") or "(no title)").strip()
    alias = store.aliases.get(sid, "")
    sandbox = session_sandbox_profile(data)
    cwd = session_cwd(summary_path, data)
    proj = project_label(cwd)
    summary, summary_source = extract_session_summary(summary_path, data)
    return SessionRecord(
        id=sid,
        title=title,
        created=data.get("created_at", ""),
        updated=data.get("updated_at", data.get("created_at", "")),
        updated_display=fmt_updated(data.get("updated_at", data.get("created_at", ""))),
        num_messages=int(data.get("num_messages", 0)),
        num_chat_messages=int(data.get("num_chat_messages", 0)),
        path=str(summary_path.parent),
        preview=extract_preview(summary_path),
        alias=alias,
        display_name=alias or title,
        pinned=sid in store.pinned,
        obsidian_path=find_obsidian_note(sid),
        sandbox_profile=sandbox,
        command=build_resume_command(sid, sandbox),
        cwd=cwd,
        project=proj,
        project_display=project_display_name(cwd, store),
        is_empty=is_empty_session(summary_path, data),
        summary=summary,
        summary_source=summary_source,
        genre=store.get_genre(sid),
        related_lilith=store.get_related_lilith(sid),
    )


def get_session(session_id: str, store: HubStore | None = None) -> SessionRecord | None:
    store = store or HubStore.load()
    needle = session_id.casefold()
    for summary_path in sorted((GROK_HOME / "sessions").rglob("summary.json")):
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        sid = data.get("info", {}).get("id") or summary_path.parent.name
        if sid == session_id or short_id(sid).casefold() == needle:
            return _session_from_summary(summary_path, data, store)
    return None


def iter_session_summaries() -> list[tuple[Path, dict]]:
    rows: list[tuple[Path, dict]] = []
    for summary_path in sorted((GROK_HOME / "sessions").rglob("summary.json")):
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        rows.append((summary_path, data))
    return rows


def find_empty_session_ids() -> list[str]:
    ids: list[str] = []
    for summary_path, data in iter_session_summaries():
        if is_empty_session(summary_path, data):
            sid = data.get("info", {}).get("id") or summary_path.parent.name
            ids.append(sid)
    return ids


def count_empty_sessions() -> int:
    return len(find_empty_session_ids())


@dataclass
class PurgeEmptyResult:
    ok: bool
    deleted: int
    failed: int
    message: str
    deleted_ids: list[str] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "deleted": self.deleted,
            "failed": self.failed,
            "message": self.message,
            "deleted_ids": self.deleted_ids,
            "errors": self.errors,
        }


@dataclass
class DeleteSessionResult:
    ok: bool
    message: str
    id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "message": self.message, "id": self.id}


def _cleanup_session_meta(store: HubStore, session_id: str) -> None:
    if session_id in store.aliases:
        del store.aliases[session_id]
    if session_id in store.genres:
        del store.genres[session_id]
    if session_id in store.related_lilith:
        del store.related_lilith[session_id]
    if session_id in store.pinned:
        store.pinned = [p for p in store.pinned if p != session_id]
    # Drop manual logical-session links so dangling refs do not accumulate.
    unlink_vesper_everywhere(store.logical_sessions, session_id)


def _is_remote_or_network_delete_error(err: str) -> bool:
    """True when grok failed on remote/network and local data may still exist."""
    e = (err or "").casefold()
    needles = (
        "failed to delete remote",
        "remote session",
        "network error",
        "connection refused",
        "connect error",
        "tcp connect",
        "timed out",
        "timeout",
        "code.grok.com",
        "error sending request",
        "client error (connect)",
        "name or service not known",
        "temporary failure in name resolution",
        "could not connect",
        "unreachable",
    )
    return any(n in e for n in needles)


def _safe_session_dir(path: str | Path) -> Path | None:
    """Return session dir only if it lives under ~/.grok/sessions/."""
    try:
        sessions_root = (GROK_HOME / "sessions").resolve()
        candidate = Path(path).expanduser().resolve()
    except OSError:
        return None
    if not candidate.is_dir():
        return None
    try:
        candidate.relative_to(sessions_root)
    except ValueError:
        return None
    # Must be a session leaf (…/sessions/<project>/<id>), not sessions root or project root alone.
    if candidate == sessions_root:
        return None
    if candidate.parent == sessions_root:
        # project bucket dir — refuse
        return None
    return candidate


def _delete_local_session_dir(session_path: str | Path) -> tuple[bool, str]:
    """Best-effort local rmtree. Returns (ok, message)."""
    safe = _safe_session_dir(session_path)
    if not safe:
        return False, f"refusing local delete outside ~/.grok/sessions: {session_path}"
    try:
        shutil.rmtree(safe)
    except OSError as exc:
        return False, f"local delete failed: {exc}"
    return True, f"removed {safe}"


def _run_grok_session_delete(session_id: str) -> tuple[int | None, str]:
    """Run `grok sessions delete`. Returns (returncode or None on spawn error, err/out text)."""
    try:
        proc = subprocess.run(
            ["grok", "sessions", "delete", session_id],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, str(exc)
    text = (proc.stderr or proc.stdout or "").strip()
    return proc.returncode, text


def delete_session(session_id: str, store: HubStore | None = None) -> DeleteSessionResult:
    """Delete a session. Prefer `grok sessions delete`; on remote/network failure, delete local dir."""
    store = store or HubStore.load()
    row = get_session(session_id, store)
    if not row:
        return DeleteSessionResult(ok=False, message="session not found", id=session_id)

    rc, text = _run_grok_session_delete(row.id)
    if rc == 0:
        _cleanup_session_meta(store, row.id)
        store.save()
        return DeleteSessionResult(ok=True, message=f"Deleted session {short_id(row.id)}", id=row.id)

    err = text or "delete failed"
    # Remote unreachable / grok unavailable: still remove local session data.
    if rc is None or _is_remote_or_network_delete_error(err):
        ok_local, local_msg = _delete_local_session_dir(row.path)
        if ok_local:
            _cleanup_session_meta(store, row.id)
            store.save()
            return DeleteSessionResult(
                ok=True,
                message=(
                    f"Deleted session {short_id(row.id)} (local only; "
                    f"remote skipped: {err.splitlines()[0][:160]})"
                ),
                id=row.id,
            )
        return DeleteSessionResult(
            ok=False,
            message=f"{err}; local fallback also failed: {local_msg}",
            id=row.id,
        )

    return DeleteSessionResult(ok=False, message=err, id=row.id)


def purge_empty_sessions(store: HubStore | None = None) -> PurgeEmptyResult:
    store = store or HubStore.load()
    targets = find_empty_session_ids()
    if not targets:
        return PurgeEmptyResult(ok=True, deleted=0, failed=0, message="No empty sessions on disk")

    deleted_ids: list[str] = []
    errors: list[dict[str, str]] = []
    local_only = 0

    for sid in targets:
        row = get_session(sid, store)
        if not row:
            errors.append({"id": sid, "error": "session not found during purge"})
            continue

        rc, text = _run_grok_session_delete(row.id)
        if rc == 0:
            deleted_ids.append(sid)
            _cleanup_session_meta(store, sid)
            continue

        err = text or "delete failed"
        if rc is None or _is_remote_or_network_delete_error(err):
            ok_local, local_msg = _delete_local_session_dir(row.path)
            if ok_local:
                deleted_ids.append(sid)
                local_only += 1
                _cleanup_session_meta(store, sid)
                continue
            errors.append({"id": sid, "error": f"{err}; local fallback also failed: {local_msg}"})
            continue

        errors.append({"id": sid, "error": err})

    if deleted_ids:
        store.save()

    deleted = len(deleted_ids)
    failed = len(errors)
    ok = failed == 0
    if deleted and failed:
        message = f"Deleted {deleted} empty session(s), {failed} failed"
    elif deleted:
        message = f"Deleted {deleted} empty session(s)"
    else:
        message = f"Failed to delete {failed} empty session(s)"
    if local_only and deleted:
        message += f" ({local_only} local-only; remote skipped)"

    return PurgeEmptyResult(
        ok=ok,
        deleted=deleted,
        failed=failed,
        message=message,
        deleted_ids=deleted_ids,
        errors=errors,
    )


def score_match(query: str, row: SessionRecord) -> int:
    q = query.casefold()
    haystacks = [row.display_name, row.title, row.alias, row.id, short_id(row.id)]
    best = 0
    for h in haystacks:
        hcf = h.casefold()
        if hcf == q:
            best = max(best, 100)
        elif q in hcf:
            best = max(best, 70 + min(len(q), 20))
    return best