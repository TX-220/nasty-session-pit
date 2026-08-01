"""Export sessions as Obsidian Canvas (.canvas) JSON.

Two independent modes:
  1. Logical session (Phase 3) — one canvas per logical unit
  2. All-sessions overview — simple group→session map (no AI, no deep analysis)

Simple fixed layout — no auto graph layout, no in-app mindmap UI.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from lib.genres import DEFAULT_GENRE_CATALOG, GENRE_ALIASES
from lib.logical_sessions import GENRES, LogicalSession

# Obsidian canvas colors are "1"–"6" (string).
GENRE_COLOR: dict[str, str] = {
    "Information Dominance": "4",
    "Trade Bot": "3",
    "Game Dev": "2",
    "Security": "1",
    "Infra & Environment": "5",
    "Other": "6",
    # Legacy Japanese keys (pre-UI English unification)
    "情報制圧": "4",
    "トレードBOT": "3",
    "ゲーム開発": "2",
    "セキュリティ": "1",
    "インフラ・環境": "5",
    "その他": "6",
}

LILITH_COLOR = "5"  # left branch
VESPER_COLOR = "3"  # right branch
UNCATEGORIZED = "Uncategorized"
UNCATEGORIZED_COLOR = "6"

DEFAULT_CANVAS_DIR = Path.home() / "obsidian-vault/Second-Brain/Sessions/Logical"
ALL_VESPER_CANVAS_DIR = Path.home() / "obsidian-vault/Second-Brain/Sessions/Vesper"
ALL_LILITH_CANVAS_DIR = Path.home() / "obsidian-vault/Second-Brain/Sessions/Lilith"
DEFAULT_CHAT_HISTORY_PROJECTS = Path.home() / "obsidian-vault/Chat_History/Projects"

ALL_VESPER_FILENAME = "All-Vesper-Sessions.canvas"
ALL_LILITH_FILENAME = "All-Lilith-Sessions.canvas"

# Layout constants (px) — fixed grid, user can tweak in Obsidian.
CENTER_W, CENTER_H = 360, 160
CHILD_W, CHILD_H = 300, 110
H_GAP = 120
V_GAP = 24
COL_OFFSET_X = 420

# All-sessions overview layout (intentionally sparse).
GROUP_W, GROUP_H = 280, 88
LEAF_W, LEAF_H = 300, 72
GROUP_H_GAP = 80
GROUP_V_GAP = 48
LEAF_V_GAP = 16


def genre_color(genre: str) -> str:
    return GENRE_COLOR.get(genre, "6")


def sanitize_filename(title: str, logical_id: str, limit: int = 48) -> str:
    base = re.sub(r"[\\/:*?\"<>|\n\r\t]+", "-", (title or "").strip())
    base = re.sub(r"\s+", "-", base).strip("-._") or "logical"
    base = base[:limit].rstrip("-._")
    short = (logical_id or "").replace("ls-", "")[:8]
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{stamp}-{base}-{short}.canvas"


def _node(
    nid: str,
    text: str,
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    color: str,
) -> dict[str, Any]:
    return {
        "id": nid,
        "type": "text",
        "text": text,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "color": color,
    }


def _edge(
    eid: str,
    from_node: str,
    to_node: str,
    *,
    from_side: str,
    to_side: str,
    label: str,
    color: str,
) -> dict[str, Any]:
    return {
        "id": eid,
        "fromNode": from_node,
        "fromSide": from_side,
        "toNode": to_node,
        "toSide": to_side,
        "label": label,
        "color": color,
    }


def _stack_y(count: int, height: int, gap: int) -> list[int]:
    """Top y for each of `count` nodes, centered around 0."""
    if count <= 0:
        return []
    total = count * height + (count - 1) * gap
    start = -total // 2
    return [start + i * (height + gap) for i in range(count)]


def build_logical_canvas(
    ls: LogicalSession,
    *,
    resolve_vesper: Callable[[str], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Build Obsidian Canvas JSON for one logical session.

    resolve_vesper(id) -> optional {display_name, short_id, exists} for labels.
    """
    center_id = "logical"
    color = genre_color(ls.genre)
    desc = (ls.description or "").strip()
    center_text = f"# {ls.title}\n**{ls.genre}** · Logical session"
    if desc:
        center_text += f"\n\n{desc[:400]}"
    center_text += f"\n\n`{ls.id}`"

    nodes: list[dict[str, Any]] = [
        _node(
            center_id,
            center_text,
            x=-CENTER_W // 2,
            y=-CENTER_H // 2,
            width=CENTER_W,
            height=CENTER_H,
            color=color,
        )
    ]
    edges: list[dict[str, Any]] = []

    lilith_refs = list(ls.lilith_sessions)
    vesper_refs = list(ls.vesper_sessions)

    # Left: Lilith
    ys_l = _stack_y(len(lilith_refs), CHILD_H, V_GAP)
    for i, ref in enumerate(lilith_refs):
        nid = f"lilith-{i}"
        title = (ref.title or "").strip() or ref.id
        body = f"### Lilith\n**{title}**\n`{ref.id}`"
        nodes.append(
            _node(
                nid,
                body,
                x=-COL_OFFSET_X - CHILD_W,
                y=ys_l[i],
                width=CHILD_W,
                height=CHILD_H,
                color=LILITH_COLOR,
            )
        )
        edges.append(
            _edge(
                f"e-l-{i}",
                center_id,
                nid,
                from_side="left",
                to_side="right",
                label="lilith",
                color=LILITH_COLOR,
            )
        )

    # Right: Vesper
    ys_v = _stack_y(len(vesper_refs), CHILD_H, V_GAP)
    for i, ref in enumerate(vesper_refs):
        nid = f"vesper-{i}"
        label = (ref.title or "").strip()
        short = ref.id[:8] if ref.id else ""
        if resolve_vesper:
            info = resolve_vesper(ref.id) or {}
            label = (info.get("display_name") or label or short).strip()
            short = info.get("short_id") or short
            missing = info.get("exists") is False
        else:
            missing = False
        if not label:
            label = short or ref.id
        body = f"### Vesper\n**{label}**\n`{short or ref.id}`"
        if missing:
            body += "\n_(missing on disk)_"
        nodes.append(
            _node(
                nid,
                body,
                x=COL_OFFSET_X,
                y=ys_v[i],
                width=CHILD_W,
                height=CHILD_H,
                color=VESPER_COLOR,
            )
        )
        edges.append(
            _edge(
                f"e-v-{i}",
                center_id,
                nid,
                from_side="right",
                to_side="left",
                label="vesper",
                color=VESPER_COLOR,
            )
        )

    # Empty-state hint nodes (still valid canvas)
    if not lilith_refs and not vesper_refs:
        nodes.append(
            _node(
                "hint",
                "### Unlinked\nLink Lilith / Vesper in Hub, then re-export",
                x=COL_OFFSET_X,
                y=-CHILD_H // 2,
                width=CHILD_W,
                height=CHILD_H,
                color="6",
            )
        )

    return {
        "nodes": nodes,
        "edges": edges,
        # Non-standard meta (Obsidian ignores unknown top-level keys usually;
        # keep minimal — actually Obsidian may strip unknown keys; put in a text node only.
        # Do not add unknown keys that could break parsers — stick to nodes/edges.
    }


def canvas_to_json(canvas: dict[str, Any]) -> str:
    return json.dumps(canvas, ensure_ascii=False, indent=2) + "\n"


def write_canvas_file(
    canvas: dict[str, Any],
    *,
    title: str,
    logical_id: str,
    output_dir: Path | None = None,
) -> Path:
    out_dir = output_dir or DEFAULT_CANVAS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / sanitize_filename(title, logical_id)
    path.write_text(canvas_to_json(canvas), encoding="utf-8")
    return path


def write_canvas_to_path(canvas: dict[str, Any], path: Path) -> Path:
    """Write canvas JSON to an explicit path (creates parent dirs)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canvas_to_json(canvas), encoding="utf-8")
    return path


def genre_color_table() -> dict[str, str]:
    return {g: GENRE_COLOR.get(g, "6") for g in GENRES}


# ---------------------------------------------------------------------------
# All-sessions overview (independent of logical-session linking UI)
# ---------------------------------------------------------------------------


def _date_prefix(value: str) -> str:
    """Extract YYYY-MM-DD if present at start of string."""
    text = (value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        maybe = text[:10]
        if maybe[0:4].isdigit() and maybe[5:7].isdigit() and maybe[8:10].isdigit():
            return maybe
    return ""


def _session_label(title: str, date: str = "", sid: str = "") -> str:
    title = (title or "").strip() or (sid[:8] if sid else "untitled")
    date = (date or "").strip()
    if date and date not in title:
        return f"**{title}**\n{date}"
    return f"**{title}**"


def build_grouped_overview_canvas(
    *,
    root_title: str,
    root_subtitle: str,
    groups: list[dict[str, Any]],
    root_color: str = "4",
) -> dict[str, Any]:
    """Build a simple overview canvas: root → group → leaf.

    Each group dict: {name, color?, items: [{title, date?, id?}]}
    Empty groups are skipped. Layout is a vertical stack of groups.
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    # Count leaves for root subtitle
    active_groups = [g for g in groups if g.get("items")]
    leaf_total = sum(len(g.get("items") or []) for g in active_groups)

    root_id = "root"
    root_text = f"# {root_title}\n{root_subtitle}\n\n{leaf_total} sessions · {len(active_groups)} groups"
    nodes.append(
        _node(
            root_id,
            root_text,
            x=-GROUP_W // 2,
            y=-GROUP_H - 40,
            width=max(GROUP_W, 360),
            height=GROUP_H + 20,
            color=root_color,
        )
    )

    if not active_groups:
        nodes.append(
            _node(
                "empty",
                "### Empty\nNo sessions found",
                x=GROUP_W + GROUP_H_GAP,
                y=0,
                width=LEAF_W,
                height=LEAF_H,
                color="6",
            )
        )
        return {"nodes": nodes, "edges": edges}

    y_cursor = 0
    for gi, group in enumerate(active_groups):
        gname = str(group.get("name") or "Other")
        default_color = genre_color(gname) if gname in GENRE_COLOR else "6"
        gcolor = str(group.get("color") or default_color)
        items = list(group.get("items") or [])
        gid = f"group-{gi}"
        gtext = f"### {gname}\n{len(items)} sessions"
        nodes.append(
            _node(
                gid,
                gtext,
                x=0,
                y=y_cursor,
                width=GROUP_W,
                height=GROUP_H,
                color=gcolor,
            )
        )
        edges.append(
            _edge(
                f"e-root-{gi}",
                root_id,
                gid,
                from_side="bottom",
                to_side="top",
                label="",
                color=gcolor,
            )
        )

        for ii, item in enumerate(items):
            title = str(item.get("title") or item.get("id") or "untitled")
            date = str(item.get("date") or "")
            sid = str(item.get("id") or "")
            nid = f"leaf-{gi}-{ii}"
            body = _session_label(title, date, sid)
            if sid and len(sid) <= 40:
                body += f"\n`{sid[:12]}`" if len(sid) > 12 else f"\n`{sid}`"
            nodes.append(
                _node(
                    nid,
                    body,
                    x=GROUP_W + GROUP_H_GAP,
                    y=y_cursor + ii * (LEAF_H + LEAF_V_GAP),
                    width=LEAF_W,
                    height=LEAF_H,
                    color=gcolor,
                )
            )
            edges.append(
                _edge(
                    f"e-{gi}-{ii}",
                    gid,
                    nid,
                    from_side="right",
                    to_side="left",
                    label="",
                    color=gcolor,
                )
            )

        block_h = max(GROUP_H, len(items) * LEAF_H + max(0, len(items) - 1) * LEAF_V_GAP)
        y_cursor += block_h + GROUP_V_GAP

    return {"nodes": nodes, "edges": edges}


def build_all_vesper_canvas(
    sessions: Iterable[dict[str, Any]],
    *,
    genre_by_session: dict[str, set[str]] | None = None,
    genre_order: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """All Vesper (Grok Build) sessions, lightly grouped by genre.

    Priority:
      1. Direct session.genre (manual on Vesper session)
      2. Logical-session link genres (genre_by_session)
      3. Uncategorized

    Multi-logical-genre without direct genre → first in genre_order only.
    """
    genre_by_session = genre_by_session or {}
    catalog = list(genre_order) if genre_order is not None else list(DEFAULT_GENRE_CATALOG)
    # Preserve catalog order, then Uncategorized
    order = list(catalog) + [UNCATEGORIZED]
    buckets: dict[str, list[dict[str, Any]]] = {g: [] for g in order}

    rows = list(sessions)
    # Newest first within each group
    def sort_key(s: dict[str, Any]) -> str:
        return str(s.get("updated") or s.get("created") or "")

    for s in sorted(rows, key=sort_key, reverse=True):
        sid = str(s.get("id") or "")
        title = (
            str(s.get("display_name") or "").strip()
            or str(s.get("title") or "").strip()
            or (sid[:8] if sid else "untitled")
        )
        date = (
            str(s.get("updated_display") or "").strip()
            or _date_prefix(str(s.get("updated") or s.get("created") or ""))
        )
        # 1) Direct genre wins (accept legacy Japanese labels)
        direct = str(s.get("genre") or "").strip()
        if direct in GENRE_ALIASES:
            direct = GENRE_ALIASES[direct]
        if direct in catalog:
            chosen = direct
        else:
            # 2) Logical link genres
            genres = genre_by_session.get(sid) or genre_by_session.get(sid[:8]) or set()
            chosen = UNCATEGORIZED
            for g in catalog:
                if g in genres:
                    chosen = g
                    break
        buckets.setdefault(chosen, []).append(
            {"title": title, "date": date, "id": sid[:8] if sid else ""}
        )

    groups = [
        {
            "name": name,
            "color": GENRE_COLOR.get(name, UNCATEGORIZED_COLOR),
            "items": buckets.get(name) or [],
        }
        for name in order
        if buckets.get(name)
    ]

    return build_grouped_overview_canvas(
        root_title="All Vesper Sessions",
        root_subtitle="Grok Build · simple overview (not logical links)",
        groups=groups,
        root_color="3",
    )


def discover_lilith_chat_sessions(
    projects_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Scan Chat_History/Projects folder structure (trust folders, no deep parse).

    Returns list of {project, title, date, path, id} sorted by project then date desc.
    """
    root = Path(projects_dir or DEFAULT_CHAT_HISTORY_PROJECTS)
    if not root.is_dir():
        return []

    skip_names = {"readme.md"}
    skip_dirs = {"_uploads", "_drop", ".git"}
    rows: list[dict[str, Any]] = []

    for project_dir in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not project_dir.is_dir():
            continue
        if project_dir.name in skip_dirs or project_dir.name.startswith("."):
            continue
        for path in sorted(project_dir.glob("*.md")):
            if path.name.lower() in skip_names:
                continue
            if path.name.startswith("."):
                continue
            stem = path.stem
            date = _date_prefix(stem)
            # Strip leading date- from title for display
            title = stem
            if date and stem.startswith(date):
                title = stem[len(date) :].lstrip("-_ ") or stem
            rows.append(
                {
                    "project": project_dir.name,
                    "title": title,
                    "date": date,
                    "path": str(path),
                    "id": stem[:40],
                }
            )

    # Newest date first within project (empty dates last)
    rows.sort(key=lambda r: (r["project"].lower(), r["date"] or "", r["title"]), reverse=False)
    # Re-sort per project: date desc
    by_proj: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_proj.setdefault(r["project"], []).append(r)
    out: list[dict[str, Any]] = []
    for proj in sorted(by_proj.keys(), key=str.lower):
        items = by_proj[proj]
        items.sort(key=lambda r: r.get("date") or "", reverse=True)
        out.extend(items)
    return out


# Soft palette cycle for project folder groups (not genre enum)
_PROJECT_COLORS = ("4", "5", "3", "2", "1", "6")


def build_all_lilith_canvas(
    sessions: Iterable[dict[str, Any]] | None = None,
    *,
    projects_dir: Path | None = None,
) -> dict[str, Any]:
    """All Lilith (Grok/Chat) sessions from Vault Chat_History/Projects."""
    rows = list(sessions) if sessions is not None else discover_lilith_chat_sessions(projects_dir)

    # Preserve discovery order of projects (folder scan order)
    project_order: list[str] = []
    buckets: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        proj = str(r.get("project") or "Other")
        if proj not in buckets:
            buckets[proj] = []
            project_order.append(proj)
        buckets[proj].append(
            {
                "title": str(r.get("title") or "untitled"),
                "date": str(r.get("date") or ""),
                "id": "",  # keep nodes clean; path lives in vault only
            }
        )

    groups = []
    for i, name in enumerate(project_order):
        groups.append(
            {
                "name": name,
                "color": _PROJECT_COLORS[i % len(_PROJECT_COLORS)],
                "items": buckets.get(name) or [],
            }
        )

    return build_grouped_overview_canvas(
        root_title="All Lilith Sessions",
        root_subtitle="Grok/Chat · Chat_History/Projects folder map",
        groups=groups,
        root_color="5",
    )
