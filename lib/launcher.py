from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from .sessions import OBSIDIAN_SESSIONS_DIR, build_resume_command, short_id

VAULT_ROOT = Path.home() / "obsidian-vault"


@dataclass
class LaunchResult:
    ok: bool
    command: str
    message: str
    window_name: str = ""


def _tmux_available() -> bool:
    return shutil.which("tmux") is not None


def _tmux_args() -> list[str]:
    args = ["tmux"]
    tmux_env = os.environ.get("TMUX", "")
    if tmux_env:
        args.extend(["-S", tmux_env.split(",", 1)[0]])
    else:
        default = Path(f"/tmp/tmux-{os.getuid()}/default")
        if default.exists():
            args.extend(["-S", str(default)])
    return args


def _obsidian_uri(note: Path) -> str:
    try:
        rel = note.relative_to(VAULT_ROOT)
        file_path = str(rel.with_suffix("")).replace("\\", "/")
        return f"obsidian://open?vault={quote(VAULT_ROOT.name)}&file={quote(file_path)}"
    except ValueError:
        return f"obsidian://open?path={quote(str(note), safe='')}"


def _open_obsidian_uri(uri: str, note: Path) -> LaunchResult:
    opener = shutil.which("xdg-open")
    if not opener:
        return LaunchResult(False, uri, "xdg-open not found on PATH")
    proc = subprocess.run([opener, uri], capture_output=True, text=True, timeout=10)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "failed to open Obsidian URI").strip()
        return LaunchResult(False, uri, err)
    return LaunchResult(True, uri, f"Opened in Obsidian: {note.name}")


def _build_resume_command(session_id: str, sandbox_profile: str = "off") -> str:
    return build_resume_command(session_id, sandbox_profile)


def _build_new_command() -> str:
    return "grok"


def launch_in_tmux(command: str, window_name: str) -> LaunchResult:
    if not _tmux_available():
        return LaunchResult(False, command, "tmux not found on PATH")

    safe_name = window_name[:32]
    proc = subprocess.run(
        [*_tmux_args(), "new-window", "-n", safe_name, "--", "bash", "-lc", command],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "tmux new-window failed").strip()
        return LaunchResult(False, command, err, safe_name)

    return LaunchResult(True, command, f"Opened tmux window '{safe_name}'", safe_name)


def launch_grok_resume(session_id: str, sandbox_profile: str = "off") -> LaunchResult:
    command = _build_resume_command(session_id, sandbox_profile)
    return launch_in_tmux(command, f"vesper-{short_id(session_id)}")


def launch_grok() -> LaunchResult:
    command = _build_new_command()
    return launch_in_tmux(command, "vesper-new")


def open_obsidian_note(path: str) -> LaunchResult:
    note = Path(path)
    if not note.is_absolute():
        note = OBSIDIAN_SESSIONS_DIR / note
    if not note.exists():
        return LaunchResult(False, str(note), f"file not found: {note}")
    uri = _obsidian_uri(note)
    return _open_obsidian_uri(uri, note)