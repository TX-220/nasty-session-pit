# VesperForge

Local web UI for browsing, launching, and syncing **Grok Build** sessions to **Obsidian**.

**Audience:** People who run [Grok Build](https://x.ai) (CLI/TUI) on Linux and keep notes in Obsidian.

**Not for:** Cloud multi-user hosting, non-Grok agents, or generic chat history apps.

---

## Features

- List local Grok sessions (`~/.grok/sessions/`)
- Search, pin, rename (display aliases stored locally)
- **Direct genre** on each Vesper session + filter
- **Related Lilith sessions** — manual id/url + optional title on each Vesper session
- Open sessions in **tmux**
- **Sync to Obsidian** — one click (or CLI) export of session notes + transcripts
- Project grouping by session working directory (cwd)
- Optional empty-session purge
- **All Vesper / All Lilith Canvas** overview export

UI stays minimal: **Refresh · New · Sync to Obsidian · All Canvas**.

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
git clone https://github.com/TX-220/vesper-forge.git
cd vesper-forge

# 2. Dependencies
python3 -m pip install -r requirements.txt --user

# 3. Optional: install Obsidian exporter on PATH
install -m 755 scripts/grok-export-to-obsidian ~/bin/grok-export-to-obsidian
# (UI Sync also runs scripts/grok-export-to-obsidian from the repo when present)

# 4. Run (listens on 127.0.0.1 only)
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
| Aliases / pins / genres / related Lilith | `~/.grok/vesper-hub.json` |
| Obsidian export dir | `~/obsidian-vault/Second-Brain/Sessions/Vesper/` |

Display names are **aliases** in Hub metadata; they do not rewrite Grok’s `summary.json`.

### Related Lilith sessions

Each Vesper session can hold a list of related Lilith (Grok chat) refs:

- **id** — freeform session id or URL
- **title** — optional display name
- Stored in `~/.grok/vesper-hub.json` → `related_lilith[session_id]`
- API: `POST/DELETE /api/sessions/{id}/lilith`

### Direct genre

Each Vesper session can have one **direct genre** from a **user-managed catalog**.

- **Manage** (sidebar next to Genre filter) — add / rename / delete + **color**
- Seed defaults with colors: Information Dominance (purple) · Trade Bot (green) · Game Dev (gold) · Security (red) · Infra & Environment (sky) · Other (gray)
- List / filter / detail selects show genre color (badge + tinted control)
- Unset / Uncategorized use a gray tone
- Rename updates every session using that label; color can change independently
- Delete clears that label on sessions → **Unset** (filter: **Uncategorized**)
- Catalog: `~/.grok/vesper-hub.json` → `genre_catalog` as `[{name, color}, ...]`
- Per-session values: `~/.grok/vesper-hub.json` → `genres`
- API: `GET/POST/PATCH/DELETE /api/genres`, `PUT /api/genres/color`

### Logical sessions (retired)

Viora’s product model is **simple**: each Vesper session has **direct genre** + **Related Lilith** links only.

- **No Logical tab / create UI** (never re-introduced)
- Mutation APIs (`POST/PATCH/DELETE` under `/api/logical-sessions…`) return **410 Gone**
- Read-only dump may still list retained `logical_sessions[]` in hub meta
- Data is **not deleted** from `~/.grok/vesper-hub.json`
- All Vesper Canvas groups by **direct genre only** (no logical fallback)

### Delete (local fallback)

**Delete** prefers `grok sessions delete`. If remote/network fails, Forge removes the local session directory under `~/.grok/sessions` (with path safety checks) so the UI can clean up offline.

### All-sessions overview Canvas

Top bar **All Vesper Canvas** / **All Lilith Canvas** — independent of related-lilith links. No AI classification. Vault save + browser download.

| Button | Source | Output |
|--------|--------|--------|
| All Vesper Canvas | `~/.grok/sessions` (Forge list), light genre groups | `Second-Brain/Sessions/Vesper/All-Vesper-Sessions.canvas` |
| All Lilith Canvas | `Second-Brain/Lilith/Projects/*` folder map | `Second-Brain/Lilith/All-Lilith-Sessions.canvas` |

Lilith overview is also available from **Viora Vault Hub** → *Canvas: All Lilith Sessions Overview* (`Scripts/export_all_sessions_canvas.py --lilith`).

---

## Main commands

| Action | How |
|--------|-----|
| Open UI | `python3 server.py` or `vesper-forge` |
| Sync all sessions → Obsidian | UI **Sync to Obsidian**, or: |
| | `grok-export-to-obsidian --all --import-remote --force` |
| Resume session (CLI) | `grok -r <SESSION_ID> --sandbox off` |
| List sessions (CLI) | `grok sessions list` |

Sync output (genre folders under `Sessions/Vesper/`):

```text
Sessions/Vesper/
  Information Dominance/
    YYYY-MM-DD-<title>-<id8>.md
    YYYY-MM-DD-<title>-<id8>-transcript.md
  Trade Bot/
    …
  Uncategorized/          # genre unset
    …
```

- Genre comes from Forge hub meta (`vesper-hub.json` → `genres`)
- Re-sync with `--force` relocates notes when the session genre changes
- `All-*-Sessions.canvas` stay at the Vesper/Lilith roots (not inside genre folders)

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

Copyright (c) 2026 TX-220 with assistance from [Grok (xAI)](./https://x.ai).

---

## Version

`1.7.0` — Sidebar genre directory tree (collapsible folders); pin as star only; project/cwd grouping and genre filter select removed

`1.6.0` — Related Lilith; user-managed genres + colors; Obsidian sync into genre folders; Logical sessions retired; All Canvas; delete local fallback; English UI
