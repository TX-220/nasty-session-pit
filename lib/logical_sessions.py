"""Logical Session — manual grouping of Lilith (リラ) + Vesper sessions.

Phase 1 only: CRUD + manual link/unlink. No auto-linking, no AI classification.
Genre is metadata on the logical session (fixed enum). Physical folders are not used.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from lib.genres import DEFAULT_GENRE_CATALOG, GENRE_ALIASES

# Seed / legacy logical-session defaults (user catalog lives in HubStore.genre_catalog).
GENRES: tuple[str, ...] = DEFAULT_GENRE_CATALOG
DEFAULT_GENRE = "Other"

# Schema version for future migrations of the logical_sessions payload.
SCHEMA_VERSION = 1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_logical_id() -> str:
    """Stable unique id (ulid-like uuid4 hex with ls- prefix)."""
    return f"ls-{uuid.uuid4().hex}"


def normalize_genre(genre: str | None) -> str:
    g = (genre or "").strip()
    if g in GENRES:
        return g
    mapped = GENRE_ALIASES.get(g)
    if mapped in GENRES:
        return mapped
    return DEFAULT_GENRE


def _clean_str(value: Any, limit: int = 500) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) > limit:
        return text[:limit]
    return text


def _normalize_ref_id(raw: str) -> str:
    """Normalize a session ref id (vesper UUID or freeform lilith id/url)."""
    text = (raw or "").strip()
    # Collapse whitespace; keep URL-ish strings intact.
    text = re.sub(r"\s+", " ", text)
    return text


@dataclass
class LinkedRef:
    """A manually linked external or Vesper session reference."""

    id: str
    title: str = ""

    def to_dict(self) -> dict[str, str]:
        d = {"id": self.id}
        if self.title:
            d["title"] = self.title
        return d

    @classmethod
    def from_dict(cls, data: Any) -> LinkedRef | None:
        if isinstance(data, str):
            rid = _normalize_ref_id(data)
            if not rid:
                return None
            return cls(id=rid)
        if not isinstance(data, dict):
            return None
        rid = _normalize_ref_id(str(data.get("id") or ""))
        if not rid:
            return None
        return cls(id=rid, title=_clean_str(data.get("title"), 200))


@dataclass
class LogicalSession:
    id: str
    title: str
    genre: str = DEFAULT_GENRE
    description: str = ""
    lilith_sessions: list[LinkedRef] = field(default_factory=list)
    vesper_sessions: list[LinkedRef] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "genre": self.genre,
            "description": self.description,
            "lilith_sessions": [r.to_dict() for r in self.lilith_sessions],
            "vesper_sessions": [r.to_dict() for r in self.vesper_sessions],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "lilith_count": len(self.lilith_sessions),
            "vesper_count": len(self.vesper_sessions),
        }

    def to_storage(self) -> dict[str, Any]:
        """Persist shape (no derived counts)."""
        return {
            "id": self.id,
            "title": self.title,
            "genre": self.genre,
            "description": self.description,
            "lilith_sessions": [r.to_dict() for r in self.lilith_sessions],
            "vesper_sessions": [r.to_dict() for r in self.vesper_sessions],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> LogicalSession | None:
        if not isinstance(data, dict):
            return None
        sid = _clean_str(data.get("id"), 80)
        title = _clean_str(data.get("title"), 200)
        if not sid or not title:
            return None
        lilith: list[LinkedRef] = []
        for item in data.get("lilith_sessions") or []:
            ref = LinkedRef.from_dict(item)
            if ref and not any(r.id == ref.id for r in lilith):
                lilith.append(ref)
        vesper: list[LinkedRef] = []
        for item in data.get("vesper_sessions") or []:
            ref = LinkedRef.from_dict(item)
            if ref and not any(r.id == ref.id for r in vesper):
                vesper.append(ref)
        created = _clean_str(data.get("created_at"), 40) or _utc_now_iso()
        updated = _clean_str(data.get("updated_at"), 40) or created
        return cls(
            id=sid,
            title=title,
            genre=normalize_genre(data.get("genre")),
            description=_clean_str(data.get("description"), 2000),
            lilith_sessions=lilith,
            vesper_sessions=vesper,
            created_at=created,
            updated_at=updated,
        )


def load_logical_sessions(raw: Any) -> list[LogicalSession]:
    """Parse list from HubStore payload. Tolerant of bad entries."""
    if not isinstance(raw, list):
        return []
    rows: list[LogicalSession] = []
    seen: set[str] = set()
    for item in raw:
        ls = LogicalSession.from_dict(item)
        if not ls or ls.id in seen:
            continue
        seen.add(ls.id)
        rows.append(ls)
    return rows


def dump_logical_sessions(rows: list[LogicalSession]) -> list[dict[str, Any]]:
    return [r.to_storage() for r in rows]


def create_logical_session(
    rows: list[LogicalSession],
    *,
    title: str,
    genre: str | None = None,
    description: str = "",
) -> LogicalSession:
    title = _clean_str(title, 200)
    if not title:
        raise ValueError("title is required")
    now = _utc_now_iso()
    ls = LogicalSession(
        id=new_logical_id(),
        title=title,
        genre=normalize_genre(genre),
        description=_clean_str(description, 2000),
        created_at=now,
        updated_at=now,
    )
    rows.append(ls)
    return ls


def get_logical_session(rows: list[LogicalSession], session_id: str) -> LogicalSession | None:
    needle = (session_id or "").strip()
    if not needle:
        return None
    for row in rows:
        if row.id == needle:
            return row
    return None


def update_logical_session(
    ls: LogicalSession,
    *,
    title: str | None = None,
    genre: str | None = None,
    description: str | None = None,
) -> LogicalSession:
    if title is not None:
        t = _clean_str(title, 200)
        if not t:
            raise ValueError("title cannot be empty")
        ls.title = t
    if genre is not None:
        ls.genre = normalize_genre(genre)
    if description is not None:
        ls.description = _clean_str(description, 2000)
    ls.updated_at = _utc_now_iso()
    return ls


def delete_logical_session(rows: list[LogicalSession], session_id: str) -> bool:
    ls = get_logical_session(rows, session_id)
    if not ls:
        return False
    rows[:] = [r for r in rows if r.id != ls.id]
    return True


def link_vesper(ls: LogicalSession, session_id: str, title: str = "") -> LogicalSession:
    rid = _normalize_ref_id(session_id)
    if not rid:
        raise ValueError("session_id is required")
    for ref in ls.vesper_sessions:
        if ref.id == rid:
            if title.strip():
                ref.title = _clean_str(title, 200)
                ls.updated_at = _utc_now_iso()
            return ls
    ls.vesper_sessions.append(LinkedRef(id=rid, title=_clean_str(title, 200)))
    ls.updated_at = _utc_now_iso()
    return ls


def unlink_vesper(ls: LogicalSession, session_id: str) -> bool:
    rid = _normalize_ref_id(session_id)
    before = len(ls.vesper_sessions)
    ls.vesper_sessions = [r for r in ls.vesper_sessions if r.id != rid]
    if len(ls.vesper_sessions) == before:
        return False
    ls.updated_at = _utc_now_iso()
    return True


def link_lilith(ls: LogicalSession, ref_id: str, title: str = "") -> LogicalSession:
    rid = _normalize_ref_id(ref_id)
    if not rid:
        raise ValueError("id is required")
    for ref in ls.lilith_sessions:
        if ref.id == rid:
            if title.strip():
                ref.title = _clean_str(title, 200)
                ls.updated_at = _utc_now_iso()
            return ls
    ls.lilith_sessions.append(LinkedRef(id=rid, title=_clean_str(title, 200)))
    ls.updated_at = _utc_now_iso()
    return ls


def unlink_lilith(ls: LogicalSession, ref_id: str) -> bool:
    rid = _normalize_ref_id(ref_id)
    before = len(ls.lilith_sessions)
    ls.lilith_sessions = [r for r in ls.lilith_sessions if r.id != rid]
    if len(ls.lilith_sessions) == before:
        return False
    ls.updated_at = _utc_now_iso()
    return True


def find_logical_for_vesper(rows: list[LogicalSession], vesper_id: str) -> list[LogicalSession]:
    """Return logical sessions that manually link this Vesper session id."""
    rid = _normalize_ref_id(vesper_id)
    if not rid:
        return []
    short = rid[:8] if len(rid) >= 8 else rid
    hits: list[LogicalSession] = []
    for ls in rows:
        for ref in ls.vesper_sessions:
            if ref.id == rid or ref.id == short or (len(ref.id) >= 8 and rid.startswith(ref.id)):
                hits.append(ls)
                break
    return hits


def unlink_vesper_everywhere(rows: list[LogicalSession], vesper_id: str) -> int:
    """Remove a Vesper session id from all logical sessions (on delete cleanup)."""
    rid = _normalize_ref_id(vesper_id)
    if not rid:
        return 0
    changed = 0
    for ls in rows:
        if unlink_vesper(ls, rid):
            changed += 1
    return changed


def list_summary(rows: list[LogicalSession]) -> list[dict[str, Any]]:
    """Sorted list for API (updated_at desc)."""
    sorted_rows = sorted(rows, key=lambda r: r.updated_at or r.created_at, reverse=True)
    return [r.to_dict() for r in sorted_rows]
