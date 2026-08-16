"""Read/write Grok Build sandbox default (config.toml + bashrc + launch wrapper).

Nasty Session Pit "New default" must control plain `grok` launches.

Grok resolution order (official docs):
  1. --sandbox flag or GROK_SANDBOX env
  2. [sandbox] profile in ~/.grok/config.toml
  3. off

Pitfalls this module fixes:
  - Exporting GROK_SANDBOX in bashrc *pins* the value for the whole shell lifetime.
    Toggling Pit updates the file, but already-open terminals keep the old env →
    new sessions silently start as off (or whatever was stale).
  - Non-interactive shells return early from bashrc, so they never see the export;
    they rely on config.toml (good) or a stale inherited env (bad).

Strategy:
  - config.toml [sandbox] profile = source of truth for NEW DEFAULT (persists).
  - bashrc: unset GROK_SANDBOX so plain grok is not pinned by a frozen export.
  - ~/bin/grok wrapper: if no --sandbox on CLI, inject profile from config.toml
    (works even in long-lived shells that still have stale GROK_SANDBOX=off).
"""
from __future__ import annotations

import os
import re
import shutil
import stat
from datetime import datetime
from pathlib import Path
from typing import Any

CONFIG_PATH = Path.home() / ".grok" / "config.toml"
SANDBOX_TOML_PATH = Path.home() / ".grok" / "sandbox.toml"
BASHRC_PATH = Path.home() / ".bashrc"
WRAPPER_PATH = Path.home() / "bin" / "grok"
LOCAL_BIN_GROK = Path.home() / ".local" / "bin" / "grok"
REAL_GROK_BIN = Path.home() / ".grok" / "bin" / "grok"
HUB_ROOT = Path(__file__).resolve().parent.parent

# Forge toolbar modes (click to set; no need to remember CLI flags).
OFF_PROFILE = "off"
CODING_PROFILE = "coding"
MAXSEC_PROFILE = "maxsec"
# Everyday "ON" alias → coding
ON_PROFILE = CODING_PROFILE
# P0 fortress default (config.toml + bashrc should match)
DEFAULT_PROFILE = MAXSEC_PROFILE

# Primary UI cycle / segmented control order — fortress first
UI_PROFILES: tuple[str, ...] = (MAXSEC_PROFILE, CODING_PROFILE, OFF_PROFILE)

PROFILE_META: dict[str, dict[str, str]] = {
    MAXSEC_PROFILE: {
        "label": "maxsec",
        "short": "maxsec",
        "desc": "Default fortress — secrets denied, child network blocked",
    },
    CODING_PROFILE: {
        "label": "coding",
        "short": "coding",
        "desc": "Everyday isolation — secrets denied, network allowed",
    },
    OFF_PROFILE: {
        "label": "OFF",
        "short": "off",
        "desc": "Full host access — fastest parallel build / GPU / net",
    },
}

ALLOWED_PROFILES = frozenset(
    {
        OFF_PROFILE,
        CODING_PROFILE,
        MAXSEC_PROFILE,
        "workspace",
        "strict",
        "read-only",
        "devbox",
        "lilith",
    }
)

_PROFILE_LINE = re.compile(
    r'^(\s*profile\s*=\s*)(["\'])([^"\']*)\2(\s*(?:#.*)?)$',
    re.MULTILINE,
)
_BASH_EXPORT = re.compile(
    r'^(\s*export\s+GROK_SANDBOX=)(["\']?)([^\s"\'#]+)\2(\s*(?:#.*)?)?$',
    re.MULTILINE,
)
_BASH_UNSET = re.compile(
    r'^\s*unset\s+GROK_SANDBOX\b',
    re.MULTILINE,
)

# Marker block we manage inside ~/.bashrc
_BASH_BLOCK_BEGIN = "# >>> grok security (Vesper) >>>"
_BASH_BLOCK_END = "# <<< grok security <<<"


def normalize_profile(value: str | None) -> str:
    p = (value or DEFAULT_PROFILE).strip().lower() or DEFAULT_PROFILE
    if p in ("on", "true", "1", "yes"):
        return CODING_PROFILE
    if p in ("false", "0", "no"):
        return OFF_PROFILE
    if p in ("secure", "fortress", "lockdown"):
        return MAXSEC_PROFILE
    return p


def is_enabled(profile: str | None) -> bool:
    return normalize_profile(profile) != OFF_PROFILE


def is_ui_profile(profile: str | None) -> bool:
    return normalize_profile(profile) in UI_PROFILES


def read_config_profile() -> str:
    if not CONFIG_PATH.is_file():
        return DEFAULT_PROFILE
    text = CONFIG_PATH.read_text(encoding="utf-8")
    # Prefer [sandbox] section profile=
    in_sandbox = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_sandbox = stripped == "[sandbox]"
            continue
        if not in_sandbox:
            continue
        m = re.match(r'profile\s*=\s*["\']([^"\']+)["\']', stripped)
        if m:
            return normalize_profile(m.group(1))
    # Fallback: first profile= near sandbox mention
    m = _PROFILE_LINE.search(text)
    if m:
        return normalize_profile(m.group(3))
    return DEFAULT_PROFILE


def read_bashrc_profile() -> str | None:
    """Legacy: value of export GROK_SANDBOX=… if still present.

    After migration bashrc *unsets* GROK_SANDBOX; this returns None.
    """
    if not BASHRC_PATH.is_file():
        return None
    text = BASHRC_PATH.read_text(encoding="utf-8")
    m = _BASH_EXPORT.search(text)
    if not m:
        return None
    return normalize_profile(m.group(3))


def effective_profile() -> str:
    """Pit NEW DEFAULT — always config.toml (persisted, restart-safe).

    Do not prefer bashrc export: that was the stale-env footgun.
    """
    return read_config_profile()


def _backup(path: Path) -> Path | None:
    if not path.is_file():
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = path.with_name(f"{path.name}.bak.forge-sandbox-{stamp}")
    shutil.copy2(path, bak)
    return bak


def write_config_profile(profile: str) -> None:
    profile = normalize_profile(profile)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.is_file():
        text = CONFIG_PATH.read_text(encoding="utf-8")
    else:
        text = ""

    _backup(CONFIG_PATH)

    if "[sandbox]" in text:
        # Replace profile inside [sandbox] only
        lines = text.splitlines(keepends=True)
        out: list[str] = []
        in_sandbox = False
        replaced = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                if in_sandbox and not replaced:
                    out.append(f'profile = "{profile}"\n')
                    replaced = True
                in_sandbox = stripped == "[sandbox]"
                out.append(line)
                continue
            if in_sandbox and re.match(r'profile\s*=', stripped):
                # preserve line ending
                nl = "\n" if line.endswith("\n") else ""
                out.append(f'profile = "{profile}"{nl}')
                replaced = True
                continue
            out.append(line)
        if in_sandbox and not replaced:
            out.append(f'profile = "{profile}"\n')
            replaced = True
        if not replaced:
            out.append(f'\n[sandbox]\nprofile = "{profile}"\n')
        CONFIG_PATH.write_text("".join(out), encoding="utf-8")
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += f'\n[sandbox]\nprofile = "{profile}"\n'
        CONFIG_PATH.write_text(text, encoding="utf-8")


def _security_block_body(profile: str) -> str:
    """bashrc block: do NOT export GROK_SANDBOX (pins stale values)."""
    # profile arg kept for comment visibility only
    _ = profile
    return f"""{_BASH_BLOCK_BEGIN}
# Privacy + fortress defaults (P0). NEW DEFAULT lives in config.toml only.
# Priority for plain `grok`: ~/bin/grok wrapper → config.toml [sandbox] profile.
# Do NOT export GROK_SANDBOX here — a fixed export freezes the value for the
# whole shell lifetime and makes Pit "New default" look broken until restart.
# One-shot: grok --sandbox off | coding | maxsec
export GROK_TELEMETRY_ENABLED=0
export GROK_TELEMETRY_TRACE_UPLOAD=0
export GROK_FEEDBACK_ENABLED=0
export GROK_RESPECT_GITIGNORE=1
unset GROK_SANDBOX 2>/dev/null || true
unset GROK_SUBAGENTS GROK_WEB_FETCH GROK_MEMORY 2>/dev/null || true
# Prefer wrapper so NEW DEFAULT applies even in long-lived terminals
export PATH="$HOME/bin:$PATH"
# Trade theory / secrets: explicit fortress alias
alias grok-trade='GROK_MEMORY=0 grok --no-memory --disable-web-search --sandbox maxsec'
alias grok-secure='grok-trade'
alias grok-off='grok --sandbox off'
alias grok-coding='grok --sandbox coding'
{_BASH_BLOCK_END}
"""


def write_bashrc_profile(profile: str) -> bool:
    """Migrate bashrc security block: unset GROK_SANDBOX + PATH for wrapper.

    Returns True if the file changed.
    """
    profile = normalize_profile(profile)
    if not BASHRC_PATH.is_file():
        return False
    text = BASHRC_PATH.read_text(encoding="utf-8")
    new_block = _security_block_body(profile)

    _backup(BASHRC_PATH)

    if _BASH_BLOCK_BEGIN in text and _BASH_BLOCK_END in text:
        pattern = re.compile(
            re.escape(_BASH_BLOCK_BEGIN) + r".*?" + re.escape(_BASH_BLOCK_END),
            re.DOTALL,
        )
        new_text, n = pattern.subn(new_block.rstrip("\n"), text, count=1)
        if n == 0:
            return False
    elif _BASH_EXPORT.search(text):
        # Legacy: only an export line — replace export with unset + keep rest
        def repl(m: re.Match[str]) -> str:
            return 'unset GROK_SANDBOX 2>/dev/null || true'

        new_text, n = _BASH_EXPORT.subn(repl, text, count=1)
        if n == 0:
            return False
    else:
        # Append managed block
        if text and not text.endswith("\n"):
            text += "\n"
        new_text = text + "\n" + new_block

    if new_text == text:
        return False
    BASHRC_PATH.write_text(new_text, encoding="utf-8")
    return True


_WRAPPER_SCRIPT = r'''#!/usr/bin/env bash
# grok wrapper — apply Nasty Session Pit "New default" from config.toml
#
# Grok resolution order: --sandbox / GROK_SANDBOX env > config.toml > off
# Long-lived shells often keep a STALE GROK_SANDBOX (e.g. off), so Pit toggles
# look ignored. This wrapper: when the user did not pass --sandbox, force the
# profile from ~/.grok/config.toml [sandbox] profile= (Pit NEW DEFAULT).
#
# Explicit always wins:
#   grok --sandbox off
#   grok --sandbox coding
#   grok --sandbox maxsec
set -euo pipefail

REAL="${GROK_REAL_BIN:-$HOME/.grok/bin/grok}"
if [[ ! -x "$REAL" ]]; then
  echo "grok: real binary not found (expected $HOME/.grok/bin/grok)" >&2
  exit 127
fi

# Avoid recursive wrap
here="$(readlink -f "$0" 2>/dev/null || echo "$0")"
real_r="$(readlink -f "$REAL" 2>/dev/null || echo "$REAL")"
if [[ "$real_r" == "$here" ]]; then
  echo "grok: wrapper misconfigured (REAL points at itself)" >&2
  exit 127
fi

has_sandbox_flag=0
for arg in "$@"; do
  case "$arg" in
    --sandbox|--sandbox=*)
      has_sandbox_flag=1
      break
      ;;
  esac
done

if [[ "$has_sandbox_flag" -eq 1 ]]; then
  exec "$REAL" "$@"
fi

profile="$(
  python3 - <<'PY'
from pathlib import Path
import re
path = Path.home() / ".grok" / "config.toml"
default = "maxsec"
if not path.is_file():
    print(default)
    raise SystemExit(0)
text = path.read_text(encoding="utf-8")
in_sandbox = False
for line in text.splitlines():
    stripped = line.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        in_sandbox = stripped == "[sandbox]"
        continue
    if not in_sandbox:
        continue
    m = re.match(r'profile\s*=\s*["\']([^"\']+)["\']', stripped)
    if m:
        p = (m.group(1) or default).strip().lower() or default
        if p in ("on", "true", "1", "yes"):
            p = "coding"
        elif p in ("false", "0", "no"):
            p = "off"
        elif p in ("secure", "fortress", "lockdown"):
            p = "maxsec"
        print(p)
        raise SystemExit(0)
print(default)
PY
)"

# Force config default — ignore stale exported GROK_SANDBOX from parent shell
exec env GROK_SANDBOX="$profile" "$REAL" --sandbox "$profile" "$@"
'''


def ensure_wrapper() -> dict[str, Any]:
    """Install/update ~/bin/grok so plain `grok` honors config.toml immediately."""
    WRAPPER_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if WRAPPER_PATH.is_file() and not WRAPPER_PATH.is_symlink():
        existing = WRAPPER_PATH.read_text(encoding="utf-8")
    # Only overwrite if missing, or previous Vesper wrapper, or symlink to real binary
    should_write = False
    if WRAPPER_PATH.is_symlink():
        should_write = True
    elif not WRAPPER_PATH.is_file():
        should_write = True
    elif "Nasty Session Pit" in existing or "grok wrapper" in existing or "GROK_REAL_BIN" in existing:
        should_write = existing != _WRAPPER_SCRIPT
    elif existing.startswith("#!") and "exec" in existing and len(existing) < 500:
        # tiny custom stub — replace
        should_write = True
    else:
        # Unknown large script — do not clobber
        return {
            "wrapper_path": str(WRAPPER_PATH),
            "installed": False,
            "skipped": True,
            "reason": "existing non-wrapper ~/bin/grok left untouched",
        }

    if should_write:
        if WRAPPER_PATH.is_symlink() or WRAPPER_PATH.is_file():
            _backup(WRAPPER_PATH)
            if WRAPPER_PATH.is_symlink():
                WRAPPER_PATH.unlink()
        WRAPPER_PATH.write_text(_WRAPPER_SCRIPT, encoding="utf-8")
    mode = WRAPPER_PATH.stat().st_mode
    WRAPPER_PATH.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # ~/.local/bin/grok is often FIRST on PATH for login shells (.profile).
    # Installer leaves it as a symlink to the raw binary — rewire to our wrapper
    # so desktop / bash -lc / non-interactive launches also honor NEW DEFAULT.
    local_rewired = False
    try:
        LOCAL_BIN_GROK.parent.mkdir(parents=True, exist_ok=True)
        want = WRAPPER_PATH.resolve()
        cur: Path | None = None
        if LOCAL_BIN_GROK.is_symlink() or LOCAL_BIN_GROK.exists():
            try:
                cur = LOCAL_BIN_GROK.resolve()
            except OSError:
                cur = None
        if cur != want:
            if LOCAL_BIN_GROK.is_symlink() or LOCAL_BIN_GROK.exists():
                _backup(LOCAL_BIN_GROK)
                LOCAL_BIN_GROK.unlink()
            LOCAL_BIN_GROK.symlink_to(WRAPPER_PATH)
            local_rewired = True
    except OSError as exc:
        local_rewired = False
        local_err = str(exc)
    else:
        local_err = None

    # Sanity: real binary must exist and not be this wrapper
    real_ok = REAL_GROK_BIN.is_file() or REAL_GROK_BIN.is_symlink()
    return {
        "wrapper_path": str(WRAPPER_PATH),
        "installed": True,
        "real_bin": str(REAL_GROK_BIN),
        "real_ok": real_ok,
        "updated": should_write,
        "local_bin_grok": str(LOCAL_BIN_GROK),
        "local_bin_rewired": local_rewired,
        "local_bin_error": local_err,
    }


def ensure_launch_wiring(profile: str | None = None) -> dict[str, Any]:
    """Make NEW DEFAULT actually control plain `grok` (config + bashrc + wrapper)."""
    p = normalize_profile(profile) if profile else read_config_profile()
    write_config_profile(p)
    bashrc_updated = write_bashrc_profile(p)
    wrap = ensure_wrapper()
    return {
        "profile": p,
        "bashrc_updated": bashrc_updated,
        "wrapper": wrap,
        "config_path": str(CONFIG_PATH),
    }


def set_profile(profile: str) -> dict[str, Any]:
    profile = normalize_profile(profile)
    if profile not in ALLOWED_PROFILES:
        raise ValueError(
            f"unknown sandbox profile: {profile!r} "
            f"(UI modes: {', '.join(UI_PROFILES)})"
        )
    wiring = ensure_launch_wiring(profile)
    meta = PROFILE_META.get(profile, {})
    st = status()
    return st | {
        "profile": profile,
        "bashrc_updated": wiring["bashrc_updated"],
        "wrapper": wiring["wrapper"],
        "message": (
            f"New default → {meta.get('label', profile)} "
            f"(config.toml + wrapper; plain `grok` uses this immediately). "
            f"Explicit: grok --sandbox off|coding|maxsec"
        ),
    }


def toggle() -> dict[str, Any]:
    """Cycle maxsec → coding → OFF → maxsec (for one-button advance)."""
    current = effective_profile()
    if current not in UI_PROFILES:
        # Unknown / custom → snap back to fortress default
        nxt = DEFAULT_PROFILE
    else:
        idx = UI_PROFILES.index(current)
        nxt = UI_PROFILES[(idx + 1) % len(UI_PROFILES)]
    return set_profile(nxt)


def status() -> dict[str, Any]:
    config_p = read_config_profile()
    bashrc_p = read_bashrc_profile()
    eff = effective_profile()
    env_p = os.environ.get("GROK_SANDBOX")
    env_norm = normalize_profile(env_p) if env_p is not None else None
    wrap_ok = (
        WRAPPER_PATH.is_file()
        and os.access(WRAPPER_PATH, os.X_OK)
        and not WRAPPER_PATH.is_symlink()
    )
    wrap_is_ours = False
    if wrap_ok:
        try:
            head = WRAPPER_PATH.read_text(encoding="utf-8", errors="replace")[:400]
            wrap_is_ours = "New default" in head or "GROK_REAL_BIN" in head or "Nasty Session Pit" in head
        except OSError:
            wrap_is_ours = False

    modes = [
        {
            "id": pid,
            "label": PROFILE_META[pid]["label"],
            "desc": PROFILE_META[pid]["desc"],
            "active": eff == pid,
        }
        for pid in UI_PROFILES
    ]
    stale_env = env_norm is not None and env_norm != eff
    return {
        "profile": eff,
        "enabled": is_enabled(eff),
        "is_maxsec": eff == MAXSEC_PROFILE,
        "on_profile": CODING_PROFILE,
        "off_profile": OFF_PROFILE,
        "maxsec_profile": MAXSEC_PROFILE,
        "ui_profiles": list(UI_PROFILES),
        "modes": modes,
        "config_profile": config_p,
        "bashrc_profile": bashrc_p,
        "bashrc_exports": bashrc_p is not None,
        "process_env_profile": env_norm,
        "stale_env_warning": stale_env,
        "sources_match": bashrc_p is None or bashrc_p == config_p,
        "wrapper_path": str(WRAPPER_PATH),
        "wrapper_ok": wrap_ok and wrap_is_ours,
        "config_path": str(CONFIG_PATH),
        "bashrc_path": str(BASHRC_PATH),
        "note": (
            "NEW DEFAULT = config.toml [sandbox] profile (restart-safe). "
            "~/bin/grok injects that profile when you omit --sandbox "
            "(so stale shell GROK_SANDBOX cannot force off). "
            "One-shot: grok --sandbox off|coding|maxsec."
        ),
    }
