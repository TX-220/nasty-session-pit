from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from .sandbox_config import effective_profile
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


def _runtime_dir() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))


def _find_xauthority() -> str | None:
    """Resolve XAUTHORITY for GUI apps launched from systemd (no session env)."""
    existing = os.environ.get("XAUTHORITY")
    if existing and Path(existing).exists():
        return existing
    runtime = _runtime_dir()
    mutter = sorted(
        runtime.glob(".mutter-Xwaylandauth.*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if mutter:
        return str(mutter[0])
    home_xa = Path.home() / ".Xauthority"
    if home_xa.exists():
        return str(home_xa)
    return None


def _gui_env() -> dict[str, str]:
    """
    Build an env for opening GUI apps / Obsidian from a headless systemd unit.

    User services often lack DISPLAY / WAYLAND_DISPLAY / XAUTHORITY; without
    them, xdg-open may "succeed" while Obsidian never focuses the note, and
    the Obsidian CLI can segfault.
    """
    env = os.environ.copy()
    if not env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
        # Prefer Wayland when the socket exists, keep Xwayland DISPLAY too.
        wayland_sock = _runtime_dir() / "wayland-0"
        if wayland_sock.exists():
            env.setdefault("WAYLAND_DISPLAY", "wayland-0")
        env.setdefault("DISPLAY", ":0")
    if "XAUTHORITY" not in env or not Path(env.get("XAUTHORITY", "")).exists():
        xa = _find_xauthority()
        if xa:
            env["XAUTHORITY"] = xa
    env.setdefault("XDG_RUNTIME_DIR", str(_runtime_dir()))
    if "DBUS_SESSION_BUS_ADDRESS" not in env:
        bus = _runtime_dir() / "bus"
        if bus.exists():
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"
    return env


def _obsidian_bin() -> str | None:
    for cand in ("obsidian", "/usr/bin/obsidian", "/opt/Obsidian/obsidian"):
        if cand.startswith("/"):
            if Path(cand).is_file() and os.access(cand, os.X_OK):
                return cand
        else:
            found = shutil.which(cand)
            if found:
                return found
    return None


def _vault_rel_path(note: Path) -> str | None:
    try:
        return str(note.relative_to(VAULT_ROOT)).replace("\\", "/")
    except ValueError:
        return None


def _obsidian_uri(note: Path) -> str:
    rel = _vault_rel_path(note)
    if rel is not None:
        # file= is without extension (Obsidian convention)
        file_path = str(Path(rel).with_suffix("")).replace("\\", "/")
        return f"obsidian://open?vault={quote(VAULT_ROOT.name)}&file={quote(file_path)}"
    return f"obsidian://open?path={quote(str(note), safe='')}"


def _run(cmd: list[str], *, timeout: float = 15) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_gui_env(),
    )


def _open_via_cli(note: Path) -> LaunchResult | None:
    """Prefer official Obsidian CLI (reliable IPC to running app)."""
    binary = _obsidian_bin()
    if not binary:
        return None

    rel = _vault_rel_path(note)
    if rel:
        cmd = [
            binary,
            "open",
            f"path={rel}",
            f"vault={VAULT_ROOT.name}",
            "newtab",
        ]
    else:
        # Outside vault: absolute path URI is the CLI-compatible fallback path
        cmd = [binary, "open", f"path={note}"]

    try:
        proc = _run(cmd, timeout=15)
    except subprocess.TimeoutExpired:
        return LaunchResult(False, " ".join(cmd), "Obsidian CLI timed out")
    except OSError as exc:
        return LaunchResult(False, " ".join(cmd), f"Obsidian CLI failed: {exc}")

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    # CLI prints "Opened: …" on success; non-zero or empty may still mean IPC issues
    if proc.returncode == 0 and (out.lower().startswith("opened") or not err):
        msg = out or f"Opened in Obsidian: {note.name}"
        return LaunchResult(True, " ".join(cmd), msg)
    if proc.returncode == 0:
        return LaunchResult(True, " ".join(cmd), out or f"Opened in Obsidian: {note.name}")
    # Soft-fail to URI fallback (segfault / not running / etc.)
    detail = err or out or f"exit {proc.returncode}"
    return LaunchResult(False, " ".join(cmd), detail)


def _open_obsidian_uri(uri: str, note: Path) -> LaunchResult:
    last_err = "xdg-open/gio not found on PATH"
    tried = False
    for opener_name in ("xdg-open", "gio"):
        opener = shutil.which(opener_name)
        if not opener:
            continue
        tried = True
        cmd = [opener, "open", uri] if opener_name == "gio" else [opener, uri]
        try:
            proc = _run(cmd, timeout=10)
        except (subprocess.TimeoutExpired, OSError) as exc:
            last_err = f"{opener_name} failed: {exc}"
            continue
        if proc.returncode == 0:
            return LaunchResult(True, uri, f"Opened in Obsidian: {note.name}")
        last_err = (proc.stderr or proc.stdout or f"{opener_name} failed").strip()
    if not tried:
        return LaunchResult(False, uri, "xdg-open/gio not found on PATH")
    return LaunchResult(False, uri, last_err)


def _raise_obsidian_window() -> None:
    """Best-effort: focus an existing Obsidian window (non-fatal)."""
    env = _gui_env()
    wmctrl = shutil.which("wmctrl")
    if not wmctrl:
        return
    try:
        subprocess.run(
            [wmctrl, "-xa", "obsidian.Obsidian"],
            capture_output=True,
            timeout=3,
            env=env,
        )
    except Exception:
        pass


def _build_resume_command(session_id: str, sandbox_profile: str | None = None) -> str:
    profile = sandbox_profile if sandbox_profile is not None else effective_profile()
    return build_resume_command(session_id, profile)


def _build_new_command(sandbox_profile: str | None = None) -> str:
    profile = sandbox_profile if sandbox_profile is not None else effective_profile()
    profile = (profile or "off").strip() or "off"
    # Force CLI flag so systemd/tmux env cannot silently re-pin maxsec/off.
    return f"GROK_SANDBOX={profile} grok --sandbox {profile}"


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


def launch_grok_resume(session_id: str, sandbox_profile: str | None = None) -> LaunchResult:
    """Resume with Forge global sandbox default (toggle), not session summary alone."""
    profile = sandbox_profile if sandbox_profile is not None else effective_profile()
    command = _build_resume_command(session_id, profile)
    return launch_in_tmux(command, f"vesper-{short_id(session_id)}")


def launch_grok(sandbox_profile: str | None = None) -> LaunchResult:
    command = _build_new_command(sandbox_profile)
    return launch_in_tmux(command, "vesper-new")


def open_obsidian_note(path: str) -> LaunchResult:
    note = Path(path).expanduser()
    if not note.is_absolute():
        note = OBSIDIAN_SESSIONS_DIR / note
    note = note.resolve()
    if not note.exists():
        return LaunchResult(False, str(note), f"file not found: {note}")

    # 1) Official CLI — talks to running Obsidian via IPC (most reliable)
    cli_result = _open_via_cli(note)
    if cli_result is not None and cli_result.ok:
        _raise_obsidian_window()
        return cli_result

    # 2) Fallback: obsidian:// URI via xdg-open / gio
    uri = _obsidian_uri(note)
    uri_result = _open_obsidian_uri(uri, note)
    if uri_result.ok:
        _raise_obsidian_window()
        return uri_result

    # Prefer the more informative error
    parts = []
    if cli_result is not None and not cli_result.ok:
        parts.append(f"CLI: {cli_result.message}")
    parts.append(f"URI: {uri_result.message}")
    return LaunchResult(False, uri, " · ".join(parts))
