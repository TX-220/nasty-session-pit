#!/usr/bin/env python3
"""VesperForge — local session browser for Grok Build."""
# Copyright (c) 2026 TX-220 with assistance from Grok (xAI) - https://x.ai

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.launcher import launch_grok, launch_grok_resume, open_obsidian_note
from lib.canvas_export import (
    ALL_LILITH_CANVAS_DIR,
    ALL_LILITH_FILENAME,
    ALL_VESPER_CANVAS_DIR,
    ALL_VESPER_FILENAME,
    build_all_lilith_canvas,
    build_all_vesper_canvas,
    discover_lilith_chat_sessions,
    write_canvas_to_path,
)
from lib.logical_sessions import (
    find_logical_for_vesper,
    get_logical_session,
    list_summary,
)
from lib.sessions import (
    HubStore,
    count_empty_sessions,
    delete_session,
    discover_sessions,
    get_session,
    purge_empty_sessions,
)

STATIC_DIR = ROOT / "static"


def _resolve_export_script() -> Path:
    """Prefer repo-bundled exporter, then ~/bin install."""
    candidates = (
        ROOT / "scripts" / "grok-export-to-obsidian",
        Path.home() / "bin" / "grok-export-to-obsidian",
    )
    for path in candidates:
        if path.is_file():
            return path
    return candidates[0]


EXPORT_SCRIPT = _resolve_export_script()
DEFAULT_OBSIDIAN_DIR = Path.home() / "obsidian-vault/Second-Brain/Sessions/Vesper"
# Keep legacy log name for existing tooling; also readable as VesperForge sync log.
SYNC_LOG_PATH = Path("/tmp/vesper-hub-obsidian-sync.log")
SYNC_TIMEOUT_SEC = 300

logger = logging.getLogger("vesperforge")

store = HubStore.load()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global store
    HubStore.migrate_legacy_aliases()
    store = HubStore.load()
    yield


app = FastAPI(
    title="VesperForge",
    version="1.7.0",
    lifespan=lifespan,
    description="Local Grok Build session browser with Obsidian sync, related Lilith links, and Canvas export",
)


class SessionUpdate(BaseModel):
    """Patch Vesper session metadata. Omit a field to leave it unchanged."""

    alias: str | None = None
    genre: str | None = None  # "" clears direct genre; None = no change


class GenreCreate(BaseModel):
    name: str
    color: str | None = None


class GenreRename(BaseModel):
    old_name: str
    new_name: str
    color: str | None = None  # optional color update on rename


class GenreColorUpdate(BaseModel):
    name: str
    color: str


class GenreDelete(BaseModel):
    name: str


class ProjectAliasUpdate(BaseModel):
    cwd: str
    alias: str


class LaunchResponse(BaseModel):
    ok: bool
    command: str
    message: str
    window_name: str = ""


class SyncResponse(BaseModel):
    ok: bool
    message: str
    synced: int = 0
    skipped: int = 0
    empty: int = 0
    failed: int = 0
    log_path: str = ""
    output_dir: str = ""


class RefreshResponse(BaseModel):
    ok: bool
    message: str
    total: int = 0
    imported: int = 0


class SettingsUpdate(BaseModel):
    hide_empty: bool | None = None


class PurgeEmptyResponse(BaseModel):
    ok: bool
    deleted: int
    failed: int
    message: str
    deleted_ids: list[str] = []
    errors: list[dict[str, str]] = []


class DeleteSessionResponse(BaseModel):
    ok: bool
    message: str
    id: str = ""


class LogicalSessionCreate(BaseModel):
    title: str
    genre: str = "Other"
    description: str = ""


class LogicalSessionUpdate(BaseModel):
    title: str | None = None
    genre: str | None = None
    description: str | None = None


class LinkVesperBody(BaseModel):
    session_id: str
    title: str = ""


class LinkLilithBody(BaseModel):
    id: str
    title: str = ""


class CanvasExportBody(BaseModel):
    """Phase 3: save canvas to vault (default) and/or return JSON for download."""

    save: bool = True


@app.get("/api/sessions")
def list_sessions() -> dict:
    store.reload_settings()
    rows = discover_sessions(store)
    all_count = len(list((Path.home() / ".grok/sessions").rglob("summary.json")))
    return {
        "sessions": [r.to_dict() for r in rows],
        "total": all_count,
        "shown": len(rows),
        "empty_count": count_empty_sessions(),
        "hide_empty": store.settings.get("hide_empty", True),
        "project_aliases": store.project_aliases,
        "app_name": "VesperForge",
    }


@app.patch("/api/projects/alias")
def update_project_alias(body: ProjectAliasUpdate) -> dict:
    if not body.cwd.strip():
        raise HTTPException(400, "cwd is required")
    store.set_project_alias(body.cwd.strip(), body.alias)
    rows = discover_sessions(store)
    return {
        "cwd": body.cwd.strip(),
        "alias": body.alias.strip(),
        "project_display": store.project_aliases.get(body.cwd.strip()) or "",
        "sessions": [r.to_dict() for r in rows],
    }


@app.post("/api/sessions/purge-empty")
def purge_empty() -> PurgeEmptyResponse:
    result = purge_empty_sessions(store)
    return PurgeEmptyResponse(**result.to_dict())


@app.delete("/api/sessions/{session_id}")
def remove_session(session_id: str) -> DeleteSessionResponse:
    result = delete_session(session_id, store)
    return DeleteSessionResponse(**result.to_dict())


@app.get("/api/settings")
def get_settings() -> dict:
    store.reload_settings()
    return {
        **store.settings,
        "app_name": "VesperForge",
    }


@app.patch("/api/settings")
def update_settings(body: SettingsUpdate) -> dict:
    if body.hide_empty is not None:
        store.settings["hide_empty"] = body.hide_empty
        store.save()
    return get_settings()


@app.post("/api/refresh")
def refresh_sessions() -> RefreshResponse:
    before = len(list((Path.home() / ".grok/sessions").rglob("summary.json")))
    imported = 0
    try:
        proc = subprocess.run(
            ["grok", "import"],
            capture_output=True,
            text=True,
            timeout=120,
            env=_hub_env(),
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        if "imported" in output.lower():
            imported = output.lower().count("imported")
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
            return RefreshResponse(
                ok=False,
                message=f"grok import failed: {err}",
                total=before,
            )
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as exc:
        return RefreshResponse(ok=False, message=str(exc), total=before)
    after = len(list((Path.home() / ".grok/sessions").rglob("summary.json")))
    new_count = max(0, after - before)
    rows = discover_sessions(store)
    return RefreshResponse(
        ok=True,
        message=f"Refreshed: {len(rows)} shown ({after} on disk, +{new_count} new)",
        total=after,
        imported=new_count,
    )


@app.get("/api/sessions/{session_id}")
def session_detail(session_id: str) -> dict:
    row = get_session(session_id, store)
    if not row:
        raise HTTPException(404, "session not found")
    return row.to_dict()


@app.patch("/api/sessions/{session_id}")
def update_session_meta(session_id: str, body: SessionUpdate) -> dict:
    """Update alias and/or direct genre on a Vesper session (manual only)."""
    row = get_session(session_id, store)
    if not row:
        raise HTTPException(404, "session not found")
    if body.alias is not None:
        store.set_alias(row.id, body.alias)
    if body.genre is not None:
        # Empty string clears; invalid values become unset (no auto-default to Other)
        store.set_genre(row.id, body.genre)
    updated = get_session(row.id, store)
    return updated.to_dict() if updated else row.to_dict()


@app.post("/api/sessions/{session_id}/lilith")
def api_link_related_lilith(session_id: str, body: LinkLilithBody) -> dict:
    """Attach a related Lilith (リラ) session id/url to a Vesper session (manual only)."""
    row = get_session(session_id, store)
    if not row:
        raise HTTPException(404, "session not found")
    try:
        store.link_related_lilith(row.id, body.id, title=body.title)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    updated = get_session(row.id, store)
    return updated.to_dict() if updated else row.to_dict()


@app.delete("/api/sessions/{session_id}/lilith/{ref_id:path}")
def api_unlink_related_lilith(session_id: str, ref_id: str) -> dict:
    """Remove a related Lilith ref from a Vesper session."""
    row = get_session(session_id, store)
    if not row:
        raise HTTPException(404, "session not found")
    if not store.unlink_related_lilith(row.id, ref_id):
        raise HTTPException(404, "lilith session not linked")
    updated = get_session(row.id, store)
    return updated.to_dict() if updated else row.to_dict()


@app.post("/api/sessions/{session_id}/pin")
def toggle_pin(session_id: str) -> dict:
    row = get_session(session_id, store)
    if not row:
        raise HTTPException(404, "session not found")
    pinned = store.toggle_pin(row.id)
    updated = get_session(row.id, store)
    data = updated.to_dict() if updated else row.to_dict()
    data["pinned"] = pinned
    return data


@app.post("/api/sessions/{session_id}/open")
def open_session(session_id: str) -> LaunchResponse:
    row = get_session(session_id, store)
    if not row:
        raise HTTPException(404, "session not found")
    try:
        result = launch_grok_resume(row.id, row.sandbox_profile)
    except Exception as exc:
        return LaunchResponse(
            ok=False,
            command=row.command,
            message=str(exc),
        )
    return LaunchResponse(
        ok=result.ok,
        command=result.command,
        message=result.message,
        window_name=result.window_name,
    )


@app.post("/api/sessions/{session_id}/obsidian")
def open_obsidian(session_id: str) -> LaunchResponse:
    row = get_session(session_id, store)
    if not row:
        raise HTTPException(404, "session not found")
    if not row.obsidian_path:
        return LaunchResponse(
            ok=False,
            command="",
            message="No Obsidian note yet — click Sync first",
        )
    try:
        result = open_obsidian_note(row.obsidian_path)
    except Exception as exc:
        return LaunchResponse(ok=False, command=row.obsidian_path, message=str(exc))
    return LaunchResponse(
        ok=result.ok,
        command=result.command,
        message=result.message,
    )


@app.post("/api/sessions/new")
def new_session() -> LaunchResponse:
    result = launch_grok()
    return LaunchResponse(
        ok=result.ok,
        command=result.command,
        message=result.message,
        window_name=result.window_name,
    )


def _hub_env() -> dict[str, str]:
    """Ensure grok / user bins are on PATH (systemd often has a minimal PATH)."""
    env = os.environ.copy()
    extras = [
        str(Path.home() / ".local" / "bin"),
        str(Path.home() / ".grok" / "bin"),
        str(Path.home() / "bin"),
        "/usr/local/bin",
        "/usr/bin",
    ]
    path = env.get("PATH", "")
    env["PATH"] = ":".join([*extras, path] if path else extras)
    env.setdefault("HOME", str(Path.home()))
    return env


def _write_sync_log(text: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = f"=== VesperForge sync {stamp} ===\n{text.rstrip()}\n"
    try:
        SYNC_LOG_PATH.write_text(body, encoding="utf-8")
    except OSError as exc:
        logger.warning("could not write sync log %s: %s", SYNC_LOG_PATH, exc)


def _parse_export_done_line(output: str) -> tuple[int, int, int, int]:
    """Parse: done: 21 synced, 0 skipped, 0 empty, 0 failed"""
    synced = skipped = empty = failed = 0
    m = re.search(
        r"done:\s*(\d+)\s*synced,\s*(\d+)\s*skipped,\s*(\d+)\s*empty,\s*(\d+)\s*failed",
        output,
        flags=re.IGNORECASE,
    )
    if m:
        synced, skipped, empty, failed = (int(m.group(i)) for i in range(1, 5))
        return synced, skipped, empty, failed
    # Fallback: count [ok] lines if exporter format drifts
    ok_lines = len(re.findall(r"^\[ok\]\s+", output, flags=re.MULTILINE))
    fail_lines = len(re.findall(r"^\[fail\]\s+", output, flags=re.MULTILINE))
    skip_lines = len(re.findall(r"^\[skip\]\s+", output, flags=re.MULTILINE))
    return ok_lines, skip_lines, 0, fail_lines


def _preflight_sync(output_dir: Path) -> str | None:
    """Return error message if sync cannot start, else None."""
    if not EXPORT_SCRIPT.exists():
        return f"export script not found: {EXPORT_SCRIPT}"
    if not os.access(EXPORT_SCRIPT, os.X_OK) and not EXPORT_SCRIPT.suffix == ".py":
        # Still runnable via python3 if not executable
        pass
    env = _hub_env()
    grok = shutil.which("grok", path=env.get("PATH"))
    if not grok:
        return "grok CLI not found on PATH (install Grok Build and ensure PATH includes it)"
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"cannot create Obsidian output dir {output_dir}: {exc}"
    if not os.access(output_dir, os.W_OK):
        return f"Obsidian output dir not writable: {output_dir}"
    return None


@app.post("/api/sync")
def sync_obsidian() -> SyncResponse:
    """Run grok-export-to-obsidian with preflight, logging, and clear error handling."""
    output_dir = DEFAULT_OBSIDIAN_DIR
    log_path = SYNC_LOG_PATH

    preflight_err = _preflight_sync(output_dir)
    if preflight_err:
        _write_sync_log(f"PREFLIGHT FAIL\n{preflight_err}")
        return SyncResponse(
            ok=False,
            message=preflight_err,
            log_path=str(log_path),
            output_dir=str(output_dir),
        )

    if EXPORT_SCRIPT.suffix == ".py":
        cmd = [sys.executable, str(EXPORT_SCRIPT), "--all", "--import-remote", "--force"]
    else:
        cmd = [str(EXPORT_SCRIPT), "--all", "--import-remote", "--force"]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SYNC_TIMEOUT_SEC,
            env=_hub_env(),
            cwd=str(Path.home()),
        )
        combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        _write_sync_log(f"exit={proc.returncode}\ncmd={' '.join(cmd)}\n\n{combined}")
        synced, skipped, empty, failed = _parse_export_done_line(combined)

        if proc.returncode != 0:
            tail = "\n".join(combined.splitlines()[-10:]) if combined else f"exit {proc.returncode}"
            return SyncResponse(
                ok=False,
                message=f"Sync failed (exit {proc.returncode}): {tail}",
                synced=synced,
                skipped=skipped,
                empty=empty,
                failed=failed,
                log_path=str(log_path),
                output_dir=str(output_dir),
            )

        has_done = bool(re.search(r"done:\s*\d+\s*synced", combined, flags=re.IGNORECASE))
        if not has_done and synced == 0 and failed == 0:
            return SyncResponse(
                ok=False,
                message=f"Sync finished without a clear result line. See log: {log_path}",
                synced=synced,
                skipped=skipped,
                empty=empty,
                failed=failed,
                log_path=str(log_path),
                output_dir=str(output_dir),
            )

        if failed > 0:
            return SyncResponse(
                ok=False,
                message=(
                    f"Partial sync: {synced} ok, {failed} failed "
                    f"(skipped {skipped}, empty {empty}). Log: {log_path}"
                ),
                synced=synced,
                skipped=skipped,
                empty=empty,
                failed=failed,
                log_path=str(log_path),
                output_dir=str(output_dir),
            )

        return SyncResponse(
            ok=True,
            message=(
                f"Synced {synced} session(s) → {output_dir.name}/ "
                f"(skipped {skipped}, empty {empty}, failed {failed})"
            ),
            synced=synced,
            skipped=skipped,
            empty=empty,
            failed=failed,
            log_path=str(log_path),
            output_dir=str(output_dir),
        )

    except subprocess.TimeoutExpired as exc:
        partial = f"{exc.stdout or ''}\n{exc.stderr or ''}".strip()
        msg = f"Obsidian sync timed out after {SYNC_TIMEOUT_SEC}s"
        _write_sync_log(f"{msg}\n{partial}")
        return SyncResponse(
            ok=False,
            message=msg,
            log_path=str(log_path),
            output_dir=str(output_dir),
        )
    except Exception as exc:
        # Catch-all so the UI always gets a structured failure (Lilith review note)
        _write_sync_log(f"Exception: {exc}")
        return SyncResponse(
            ok=False,
            message=f"Sync error: {exc}",
            log_path=str(log_path),
            output_dir=str(output_dir),
        )


# ---------------------------------------------------------------------------
# Logical Sessions — LEGACY (read-only API)
# Product surface is per-Vesper direct genre + Related Lilith only.
# Data kept on disk; mutations and create paths are disabled (410).
# ---------------------------------------------------------------------------

_LOGICAL_GONE = (
    "Logical sessions are retired from the product UI. "
    "Use per-Vesper Related Lilith links and direct genres. "
    "Existing logical_sessions data is retained but not editable via API."
)


def _logical_write_gone() -> None:
    raise HTTPException(status_code=410, detail=_LOGICAL_GONE)


def _enrich_logical(ls_dict: dict) -> dict:
    """Attach display labels for linked Vesper sessions when available."""
    vesper_out = []
    for ref in ls_dict.get("vesper_sessions") or []:
        rid = ref.get("id") if isinstance(ref, dict) else str(ref)
        title = (ref.get("title") if isinstance(ref, dict) else "") or ""
        row = get_session(rid, store) if rid else None
        vesper_out.append(
            {
                "id": rid,
                "title": title,
                "display_name": (row.display_name if row else title) or rid[:8],
                "short_id": (row.id[:8] if row else (rid[:8] if rid else "")),
                "exists": row is not None,
                "updated_display": row.updated_display if row else "",
            }
        )
    ls_dict = {**ls_dict, "vesper_sessions": vesper_out}
    return ls_dict


@app.get("/api/genres")
def list_genres() -> dict:
    """User-managed genre catalog with colors (order preserved)."""
    return store.genres_api_payload()


@app.post("/api/genres")
def api_add_genre(body: GenreCreate) -> dict:
    try:
        name = store.add_genre(body.name, color=body.color)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    payload = store.genres_api_payload()
    return {"ok": True, "name": name, "color": store.get_genre_color(name), **payload}


@app.patch("/api/genres")
def api_rename_genre(body: GenreRename) -> dict:
    try:
        name, updated = store.rename_genre(body.old_name, body.new_name, color=body.color)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    payload = store.genres_api_payload()
    return {
        "ok": True,
        "name": name,
        "updated_sessions": updated,
        "color": store.get_genre_color(name),
        **payload,
    }


@app.put("/api/genres/color")
def api_set_genre_color(body: GenreColorUpdate) -> dict:
    try:
        name = store.set_genre_color(body.name, body.color)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    payload = store.genres_api_payload()
    return {"ok": True, "name": name, "color": store.get_genre_color(name), **payload}


@app.delete("/api/genres")
def api_delete_genre(body: GenreDelete) -> dict:
    """Remove a genre; sessions using it become Unset (Uncategorized in filters)."""
    try:
        name, cleared = store.delete_genre(body.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    payload = store.genres_api_payload()
    return {
        "ok": True,
        "name": name,
        "cleared_sessions": cleared,
        **payload,
    }


@app.get("/api/logical-sessions")
def api_list_logical_sessions() -> dict:
    """Read-only legacy dump of retained logical_sessions[] (no UI)."""
    store.reload_logical_sessions()
    rows = list_summary(store.logical_sessions)
    return {
        "logical_sessions": rows,
        "total": len(rows),
        "genres": store.get_genre_catalog(),
        "deprecated": True,
        "message": "Logical sessions are retired from the UI. Use Related Lilith on each Vesper session.",
    }


@app.post("/api/logical-sessions")
def api_create_logical_session(body: LogicalSessionCreate) -> dict:
    _logical_write_gone()


@app.get("/api/logical-sessions/{logical_id}")
def api_get_logical_session(logical_id: str) -> dict:
    """Read-only legacy: inspect retained logical session data."""
    store.reload_logical_sessions()
    ls = get_logical_session(store.logical_sessions, logical_id)
    if not ls:
        raise HTTPException(404, "logical session not found")
    return _enrich_logical(ls.to_dict())


@app.patch("/api/logical-sessions/{logical_id}")
def api_update_logical_session(logical_id: str, body: LogicalSessionUpdate) -> dict:
    _logical_write_gone()


@app.delete("/api/logical-sessions/{logical_id}")
def api_delete_logical_session(logical_id: str) -> dict:
    _logical_write_gone()


@app.post("/api/logical-sessions/{logical_id}/vesper")
def api_link_vesper(logical_id: str, body: LinkVesperBody) -> dict:
    _logical_write_gone()


@app.delete("/api/logical-sessions/{logical_id}/vesper/{session_id}")
def api_unlink_vesper(logical_id: str, session_id: str) -> dict:
    _logical_write_gone()


@app.post("/api/logical-sessions/{logical_id}/lilith")
def api_link_lilith(logical_id: str, body: LinkLilithBody) -> dict:
    _logical_write_gone()


@app.delete("/api/logical-sessions/{logical_id}/lilith/{ref_id:path}")
def api_unlink_lilith(logical_id: str, ref_id: str) -> dict:
    _logical_write_gone()


@app.get("/api/sessions/{session_id}/logical")
def api_session_logical_links(session_id: str) -> dict:
    """Read-only legacy: which retained logical sessions reference this Vesper id."""
    store.reload_logical_sessions()
    row = get_session(session_id, store)
    lookup_id = row.id if row else session_id
    hits = find_logical_for_vesper(store.logical_sessions, lookup_id)
    return {
        "session_id": lookup_id,
        "logical_sessions": [
            {
                "id": ls.id,
                "title": ls.title,
                "genre": ls.genre,
            }
            for ls in hits
        ],
        "deprecated": True,
        "message": "Logical sessions are retired; use related_lilith on the Vesper session.",
    }


# ---------------------------------------------------------------------------
# Logical Canvas export — retired (use All Vesper / All Lilith Canvas)
# ---------------------------------------------------------------------------


@app.get("/api/logical-sessions/{logical_id}/canvas")
def api_get_logical_canvas(logical_id: str, download: bool = False):
    _logical_write_gone()


@app.post("/api/logical-sessions/{logical_id}/canvas")
def api_export_logical_canvas(logical_id: str, body: CanvasExportBody | None = None) -> dict:
    _logical_write_gone()
    return {}


# ---------------------------------------------------------------------------
# All-sessions overview Canvas (direct genre only — no logical-session concept)
# ---------------------------------------------------------------------------


@app.post("/api/canvas/all-vesper")
def api_export_all_vesper_canvas(body: CanvasExportBody | None = None) -> dict:
    """Export simple overview of all Vesper sessions → Sessions/Vesper/All-Vesper-Sessions.canvas."""
    opts = body or CanvasExportBody()
    store.reload_settings()
    rows = discover_sessions(store)
    # Group by direct session.genre only (Uncategorized when unset).
    canvas = build_all_vesper_canvas(
        [r.to_dict() for r in rows],
        genre_order=store.get_genre_catalog(),
    )
    filename = ALL_VESPER_FILENAME
    out_dir = ALL_VESPER_CANVAS_DIR
    saved_path = ""
    if opts.save:
        try:
            path = write_canvas_to_path(canvas, out_dir / filename)
            saved_path = str(path)
        except OSError as exc:
            raise HTTPException(500, f"failed to write canvas: {exc}") from exc
    return {
        "ok": True,
        "message": (
            f"All Vesper Canvas saved → {saved_path}"
            if saved_path
            else f"All Vesper Canvas built (not saved): {filename}"
        ),
        "filename": filename,
        "path": saved_path,
        "output_dir": str(out_dir),
        "canvas": canvas,
        "session_count": len(rows),
        "node_count": len(canvas.get("nodes") or []),
        "edge_count": len(canvas.get("edges") or []),
    }


@app.post("/api/canvas/all-lilith")
def api_export_all_lilith_canvas(body: CanvasExportBody | None = None) -> dict:
    """Export Chat_History/Projects as simple overview → Sessions/Lilith/All-Lilith-Sessions.canvas."""
    opts = body or CanvasExportBody()
    rows = discover_lilith_chat_sessions()
    canvas = build_all_lilith_canvas(rows)
    filename = ALL_LILITH_FILENAME
    out_dir = ALL_LILITH_CANVAS_DIR
    saved_path = ""
    if opts.save:
        try:
            path = write_canvas_to_path(canvas, out_dir / filename)
            saved_path = str(path)
        except OSError as exc:
            raise HTTPException(500, f"failed to write canvas: {exc}") from exc
    return {
        "ok": True,
        "message": (
            f"All Lilith Canvas saved → {saved_path}"
            if saved_path
            else f"All Lilith Canvas built (not saved): {filename}"
        ),
        "filename": filename,
        "path": saved_path,
        "output_dir": str(out_dir),
        "canvas": canvas,
        "session_count": len(rows),
        "node_count": len(canvas.get("nodes") or []),
        "edge_count": len(canvas.get("edges") or []),
    }


@app.get("/favicon.ico")
def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "favicon.ico")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> None:
    import uvicorn

    port = int(os.environ.get("VESPER_HUB_PORT", store.settings.get("port", 7420)))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
