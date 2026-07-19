#!/usr/bin/env python3
"""VesperForge — local session browser for Grok Build."""

from __future__ import annotations

import os
import re
import subprocess
import sys
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
from lib.sessions import (
    HubStore,
    count_empty_sessions,
    delete_session,
    discover_sessions,
    get_session,
    purge_empty_sessions,
)

STATIC_DIR = ROOT / "static"
EXPORT_SCRIPT = Path.home() / "bin/grok-export-to-obsidian"

store = HubStore.load()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global store
    HubStore.migrate_legacy_aliases()
    store = HubStore.load()
    yield


app = FastAPI(title="VesperForge", version="1.1.0", lifespan=lifespan)


class AliasUpdate(BaseModel):
    alias: str


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
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        if "imported" in output.lower():
            imported = output.lower().count("imported")
    except (subprocess.TimeoutExpired, OSError) as exc:
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
def update_alias(session_id: str, body: AliasUpdate) -> dict:
    row = get_session(session_id, store)
    if not row:
        raise HTTPException(404, "session not found")
    store.set_alias(row.id, body.alias)
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


def _parse_export_done_line(output: str) -> tuple[int, int, int, int]:
    """Parse: done: 21 synced, 0 skipped, 0 empty, 0 failed"""
    synced = skipped = empty = failed = 0
    m = re.search(
        r"done:\s*(\d+)\s*synced,\s*(\d+)\s*skipped,\s*(\d+)\s*empty,\s*(\d+)\s*failed",
        output,
    )
    if m:
        synced, skipped, empty, failed = (int(m.group(i)) for i in range(1, 5))
    return synced, skipped, empty, failed


@app.post("/api/sync")
def sync_obsidian() -> SyncResponse:
    """Run grok-export-to-obsidian --all --import-remote --force and wait for result."""
    output_dir = Path.home() / "obsidian-vault/Second-Brain/Sessions/Vesper"
    log_path = Path("/tmp/vesper-hub-obsidian-sync.log")

    if not EXPORT_SCRIPT.exists():
        return SyncResponse(
            ok=False,
            message=f"export script not found: {EXPORT_SCRIPT}",
            log_path=str(log_path),
            output_dir=str(output_dir),
        )

    cmd = [str(EXPORT_SCRIPT), "--all", "--import-remote", "--force"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            env=_hub_env(),
            cwd=str(Path.home()),
        )
    except subprocess.TimeoutExpired:
        msg = "Obsidian sync timed out after 300s"
        log_path.write_text(msg + "\n", encoding="utf-8")
        return SyncResponse(
            ok=False,
            message=msg,
            log_path=str(log_path),
            output_dir=str(output_dir),
        )
    except OSError as exc:
        return SyncResponse(
            ok=False,
            message=str(exc),
            log_path=str(log_path),
            output_dir=str(output_dir),
        )

    combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    log_path.write_text(combined + "\n", encoding="utf-8")
    synced, skipped, empty, failed = _parse_export_done_line(combined)

    if proc.returncode != 0:
        tail = "\n".join(combined.splitlines()[-8:]) if combined else f"exit {proc.returncode}"
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

    if not re.search(r"done:\s*\d+\s*synced", combined):
        # Script finished 0 but no done line — still treat as suspicious
        return SyncResponse(
            ok=False,
            message=f"Sync finished without done line. Log: {log_path}",
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


@app.get("/favicon.ico")
def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "favicon.ico")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> None:
    import os

    import uvicorn

    port = int(os.environ.get("VESPER_HUB_PORT", store.settings.get("port", 7420)))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()