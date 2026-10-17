---
name: vesper-forge
description: >
  Manage local Grok Build sessions with VesperForge — a localhost web UI for
  browsing, pinning, renaming, opening sessions in tmux, and syncing notes to
  Obsidian. Use when the user asks about VesperForge, session history UI,
  Sync to Obsidian, or managing ~/.grok/sessions via a browser.
---

# VesperForge

VesperForge is a **local** FastAPI web UI (`127.0.0.1:7420`) for Grok Build session management.

## When to use

- User wants a browser UI for Grok session history
- User wants one-click (or CLI) export of sessions into Obsidian
- User asks how to start, stop, or troubleshoot VesperForge

## Start

```bash
# From the VesperForge install / clone directory:
python3 -m pip install -r requirements.txt --user
python3 server.py
# open http://127.0.0.1:7420/
```

Optional launcher (if installed):

```bash
vesper-forge
vesper-forge --stop
```

## UI actions

| Button | Effect |
|--------|--------|
| Refresh | Rescan local `~/.grok/sessions` (no `grok import`; that subcommand is gone) |
| New | Open a new Grok session in tmux |
| Sync to Obsidian | `grok-export-to-obsidian --all --import-remote --force` |
| All Vesper / Lilith Canvas | Overview canvas export |

### Session features

- **Direct genre:** List/detail dropdown; user-managed catalog (**Manage** in sidebar); empty = Unset (filter: Uncategorized); `vesper-hub.json` → `genre_catalog` + `genres`
- **Related Lilith:** On Vesper session detail — add id/URL + optional title; multi; Unlink. Stored in `vesper-hub.json` → `related_lilith[session_id]`
- **API:** `POST/DELETE /api/sessions/{id}/lilith`
- **All-sessions overview Canvas:** Top bar **All Vesper Canvas** / **All Lilith Canvas**
- **Delete:** `grok sessions delete` with local-dir fallback on remote/network failure
- **Logical sessions:** retired — no UI; writes return 410; data retained read-only

## Obsidian export (CLI equivalent)

```bash
grok-export-to-obsidian --all --import-remote --force
```

Default output directory:

`~/obsidian-vault/Second-Brain/Sessions/Vesper/`

## Requirements

- Linux, Python 3.10+, Grok Build CLI (`grok`)
- Optional: `tmux`, Obsidian vault at the conventional path

## Out of scope

- Multi-user / remote hosting
- Replacing the Grok TUI
- Non-Grok session sources

## Obsidian sync (genre folders)

UI **Sync to Obsidian** runs `scripts/grok-export-to-obsidian` (or `~/bin/grok-export-to-obsidian`):

```bash
scripts/grok-export-to-obsidian --all --import-remote --force
```

Notes land under `Sessions/Vesper/<Genre>/` (or `Uncategorized/`).

## Repo

https://github.com/TX-220/vesper-forge
