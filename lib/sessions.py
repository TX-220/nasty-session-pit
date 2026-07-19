from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from zoneinfo import ZoneInfo

GROK_HOME = Path.home() / ".grok"
HUB_META_PATH = GROK_HOME / "vesper-hub.json"
LEGACY_ALIASES_PATH = GROK_HOME / "session-aliases.json"
OBSIDIAN_SESSIONS_DIR = Path.home() / "obsidian-vault/Second-Brain/Sessions/Vesper"
JST = ZoneInfo("Asia/Tokyo")


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "short_id": short_id(self.id),
            "title": self.title,
            "display_name": self.display_name,
            "alias": self.alias,
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
    return f"grok -r {session_id} --sandbox {profile}"


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
        parts.append(f"開始: {truncate_text(first_query, 420)}")

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
        parts.append("最近の話題:")
        parts.extend(f"• {snippet}" for snippet in recent_users)

    if last_assistant:
        parts.append(f"最新の応答:\n{truncate_text(last_assistant, 1000)}")

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
    if not OBSIDIAN_SESSIONS_DIR.exists():
        return None
    sid = short_id(session_id)
    matches = sorted(OBSIDIAN_SESSIONS_DIR.glob(f"*-{sid}.md"))
    if not matches:
        matches = sorted(OBSIDIAN_SESSIONS_DIR.glob(f"*{sid}*.md"))
    for path in matches:
        if path.name.endswith("-transcript.md"):
            continue
        return str(path)
    return None


@dataclass
class HubStore:
    aliases: dict[str, str] = field(default_factory=dict)
    project_aliases: dict[str, str] = field(default_factory=dict)
    pinned: list[str] = field(default_factory=list)
    settings: dict[str, Any] = field(
        default_factory=lambda: {
            "port": 7420,
            "hide_empty": True,
        }
    )

    @classmethod
    def load(cls) -> HubStore:
        cls.migrate_legacy_aliases()
        if not HUB_META_PATH.exists():
            return cls()
        data = json.loads(HUB_META_PATH.read_text(encoding="utf-8"))
        raw_settings = data.get("settings") or {}
        return cls(
            aliases={k: v for k, v in (data.get("aliases") or {}).items() if isinstance(v, str)},
            project_aliases={
                k: v for k, v in (data.get("project_aliases") or {}).items() if isinstance(v, str)
            },
            pinned=[p for p in (data.get("pinned") or []) if isinstance(p, str)],
            settings={
                "port": int(raw_settings.get("port", 7420)),
                "hide_empty": bool(raw_settings.get("hide_empty", True)),
            },
        )

    def save(self) -> None:
        HUB_META_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "aliases": dict(sorted(self.aliases.items())),
            "project_aliases": dict(sorted(self.project_aliases.items())),
            "pinned": self.pinned,
            "settings": self.settings,
        }
        HUB_META_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def reload_settings(self) -> None:
        """Reload settings from disk without full store rebuild."""
        if not HUB_META_PATH.exists():
            return
        data = json.loads(HUB_META_PATH.read_text(encoding="utf-8"))
        raw = data.get("settings") or {}
        self.settings["port"] = int(raw.get("port", self.settings.get("port", 7420)))
        self.settings["hide_empty"] = bool(raw.get("hide_empty", self.settings.get("hide_empty", True)))

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
        sandbox = (data.get("sandbox_profile") or "off").strip() or "off"
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
    sandbox = (data.get("sandbox_profile") or "off").strip() or "off"
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
    if session_id in store.pinned:
        store.pinned = [p for p in store.pinned if p != session_id]


def delete_session(session_id: str, store: HubStore | None = None) -> DeleteSessionResult:
    store = store or HubStore.load()
    row = get_session(session_id, store)
    if not row:
        return DeleteSessionResult(ok=False, message="session not found", id=session_id)

    try:
        proc = subprocess.run(
            ["grok", "sessions", "delete", row.id],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return DeleteSessionResult(ok=False, message=str(exc), id=row.id)

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "delete failed").strip()
        return DeleteSessionResult(ok=False, message=err, id=row.id)

    _cleanup_session_meta(store, row.id)
    store.save()
    return DeleteSessionResult(ok=True, message=f"Deleted session {short_id(row.id)}", id=row.id)


def purge_empty_sessions(store: HubStore | None = None) -> PurgeEmptyResult:
    store = store or HubStore.load()
    targets = find_empty_session_ids()
    if not targets:
        return PurgeEmptyResult(ok=True, deleted=0, failed=0, message="No empty sessions on disk")

    deleted_ids: list[str] = []
    errors: list[dict[str, str]] = []

    for sid in targets:
        try:
            proc = subprocess.run(
                ["grok", "sessions", "delete", sid],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            errors.append({"id": sid, "error": str(exc)})
            continue

        if proc.returncode == 0:
            deleted_ids.append(sid)
            _cleanup_session_meta(store, sid)
            continue

        err = (proc.stderr or proc.stdout or "delete failed").strip()
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