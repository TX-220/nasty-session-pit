# VesperForge

Local web UI for browsing, launching, and syncing **Grok Build** sessions to **Obsidian**.

**Audience:** People who run [Grok Build](https://x.ai) (CLI/TUI) on Linux and keep notes in Obsidian.

**Not for:** Cloud multi-user hosting, non-Grok agents, or generic chat history apps.

---

## Features

- List local Grok sessions (`~/.grok/sessions/`)
- Search, pin, rename (display aliases stored locally)
- Open sessions in **tmux**
- **Sync to Obsidian** — one click (or CLI) export of session notes + transcripts
- Project grouping by session working directory (cwd)
- Optional empty-session purge

UI stays minimal: **Refresh · New · Sync to Obsidian**.

---

## Requirements

| Item | Notes |
|------|--------|
| OS | Linux (tested on Ubuntu) |
| Python | 3.10+ |
| Grok Build CLI | `grok` on `PATH` |
| Optional | `tmux` (Open in tmux), Obsidian vault for sync |

---

## Quick start

```bash
# 1. Clone
git clone <REPO_URL> vesper-forge
cd vesper-forge

# 2. Dependencies
python3 -m pip install -r requirements.txt --user

# 3. Run (listens on 127.0.0.1 only)
python3 server.py
# open http://127.0.0.1:7420/
```

### Optional launcher (this machine)

If installed as `~/bin/vesper-forge`:

```bash
vesper-forge              # start + open browser
vesper-forge --no-open    # server only
vesper-forge --stop       # stop
```

systemd user unit (optional): point `WorkingDirectory` / `ExecStart` at this repo’s `server.py`.

---

## Configuration

| Item | Default |
|------|---------|
| Port | `7420` (`VESPER_HUB_PORT` or `~/.grok/vesper-hub.json` → `settings.port`) |
| Bind | `127.0.0.1` only (local use) |
| Aliases / pins | `~/.grok/vesper-hub.json` |
| Obsidian export dir | `~/obsidian-vault/Second-Brain/Sessions/Vesper/` |

Display names are **aliases** in Hub metadata; they do not rewrite Grok’s `summary.json`.

---

## Main commands

| Action | How |
|--------|-----|
| Open UI | `python3 server.py` or `vesper-forge` |
| Sync all sessions → Obsidian | UI **Sync to Obsidian**, or: |
| | `grok-export-to-obsidian --all --import-remote --force` |
| Resume session (CLI) | `grok -r <SESSION_ID> --sandbox off` |
| List sessions (CLI) | `grok sessions list` |

Sync output:

- `YYYY-MM-DD-<title>-<id8>.md` — short summary note  
- `YYYY-MM-DD-<title>-<id8>-transcript.md` — full transcript  

---

## Out of scope (by design)

- Remote multi-user / reverse proxy auth
- Editing Grok session transcripts in place
- Non-Grok session sources
- Heavy plugin marketplace packaging (review pending)

---

## Known limits

- Linux-focused; other OS untested
- Obsidian path is fixed to a conventional vault layout (override requires code change today)
- “Open in tmux” requires a working tmux server
- Session delete uses `grok sessions delete` (irreversible)

---

## Security

- Server binds to **localhost only**
- Does not ship credentials; uses local Grok session store
- Treat export notes as private if sessions contain secrets

---

## License

MIT — see [LICENSE](./LICENSE).
Copyright (c) 2026 TX-220 with assistance from Grok (xAI) - https://x.ai

---

## Version

`1.1.0` (app title: VesperForge)
