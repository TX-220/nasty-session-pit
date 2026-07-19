#!/usr/bin/env python3
# Copyright (c) 2026 TX-220 with assistance from Grok (xAI) - https://x.ai
"""VesperForge — local session browser for Grok Build."""

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
DEFAULT_OBSIDIAN_DIR = Path.home() / "obsidian-vault/Second-Brain/Sessions/Vesper"
SYNC_LOG_PATH = Path("/tmp/vesper-forge-obsidian-sync.log")
SYNC_TIMEOUT_SEC = 300

logger = logging.getLogger("vesperforge")

store = HubStore.load()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global store
    HubStore.migrate_legacy_aliases()
    store = HubStore.load()
    yield


app = FastAPI(title="VesperForge", version="1.1.1", lifespan=lifespan)


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
    """Run grok-export-to-obsidian --all --import-remote --force and wait for result."""
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

    # Prefer explicit python3 for non-executable scripts
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
    except subprocess.TimeoutExpired as exc:
        partial = ""
        if exc.stdout:
            partial += str(exc.stdout)
        if exc.stderr:
            partial += "\n" + str(exc.stderr)
        msg = f"Obsidian sync timed out after {SYNC_TIMEOUT_SEC}s"
        _write_sync_log(f"{msg}\n{partial}")
        return SyncResponse(
            ok=False,
            message=msg,
            log_path=str(log_path),
            output_dir=str(output_dir),
        )
    except OSError as exc:
        _write_sync_log(f"OSError: {exc}")
        return SyncResponse(
            ok=False,
            message=f"failed to start export: {exc}",
            log_path=str(log_path),
            output_dir=str(output_dir),
        )

    combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    _write_sync_log(f"exit={proc.returncode}\ncmd={' '.join(cmd)}\n\n{combined}")
    synced, skipped, empty, failed = _parse_export_done_line(combined)

    if proc.returncode != 0:
        tail = "\n".join(combined.splitlines()[-12:]) if combined else f"exit {proc.returncode}"
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
            f"Synced {synced} session(s) → {output_dir} "
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
    import uvicorn

    port = int(os.environ.get("VESPER_HUB_PORT", store.settings.get("port", 7420)))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
