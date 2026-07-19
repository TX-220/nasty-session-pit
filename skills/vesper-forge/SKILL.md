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
| Refresh | `grok import` + reload session list |
| New | Open a new Grok session in tmux |
| Sync to Obsidian | `grok-export-to-obsidian --all --import-remote --force` |

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

## Repo

https://github.com/TX-220/vesper-forge
