# Pre-release checklist (10 items) — VesperForge

**Project:** VesperForge  
**Reviewer:** (pending)  
**Date:** 2026-07-19  

Use this before public / Marketplace release. Mark items during review.

## 1. Purpose & audience
- [x] Who / why stated in README (Grok Build users + Obsidian)
- [x] Out of scope listed

## 2. Threat model
- [x] Localhost-only bind
- [x] Worst cases: local data exposure if bound publicly; destructive session delete

## 3. Secrets
- [x] No credentials in repo
- [x] Config lives under `~/.grok/` (user machine)

## 4. Permissions
- [x] Minimal surface: local FS read of sessions, optional tmux/Obsidian launch
- [x] Delete requires confirmation in UI

## 5. Install & run
- [x] README: clone → pip → `python3 server.py`
- [x] Port / OS prerequisites documented

## 6. Failure modes
- [x] Sync returns real success/fail counts (not fire-and-forget)
- [x] Offline banner when server down

## 7. Versioning
- [x] App version in FastAPI title path (`1.1.0`)
- [x] MVP: breaking changes allowed (stated)

## 8. Docs
- [x] README: who / start / main commands
- [x] Known limits listed

## 9. Branding
- [x] Public name: **VesperForge** (technical, neutral)
- [x] MIT LICENSE present

## 10. Channel
- [ ] Private GitHub for design review (in progress)
- [ ] Marketplace decision pending external review

### Review verdict (fill after review)

| Field | Value |
|-------|--------|
| Verdict | GO / GO with fixes / NO-GO |
| Must-fix | |
| Nice-to-have | |
| Allowed publish scope | private / public / marketplace |
