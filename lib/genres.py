"""User-managed genre catalog for VesperForge.

Session genres are freeform labels from this catalog (empty = Unset / Uncategorized filter).
Each catalog entry has a display color (#RRGGBB).
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# Seed list when hub meta has no genre_catalog yet (first run / upgrade).
DEFAULT_GENRE_CATALOG: tuple[str, ...] = (
    "Information Dominance",
    "Trade Bot",
    "Game Dev",
    "Security",
    "Infra & Environment",
    "Other",
)

# Default colors for seed genres (and fallback palette for new genres).
DEFAULT_GENRE_COLORS: dict[str, str] = {
    "Information Dominance": "#7c6cf0",
    "Trade Bot": "#4ade80",
    "Game Dev": "#f0c040",
    "Security": "#f87171",
    "Infra & Environment": "#38bdf8",
    "Other": "#888894",
}

PALETTE: tuple[str, ...] = (
    "#7c6cf0",
    "#4ade80",
    "#f0c040",
    "#f87171",
    "#38bdf8",
    "#f472b6",
    "#a78bfa",
    "#34d399",
    "#fb923c",
    "#60a5fa",
    "#e879f9",
    "#888894",
)

UNCATEGORIZED_COLOR = "#6b6b78"

# Legacy Japanese labels → English seed names (read-side only).
GENRE_ALIASES: dict[str, str] = {
    "情報制圧": "Information Dominance",
    "トレードBOT": "Trade Bot",
    "ゲーム開発": "Game Dev",
    "セキュリティ": "Security",
    "インフラ・環境": "Infra & Environment",
    "その他": "Other",
}

# Not a real genre — reserved for filter / empty state.
UNCATEGORIZED_LABEL = "Uncategorized"
RESERVED_GENRE_NAMES = frozenset(
    {
        "",
        UNCATEGORIZED_LABEL,
        "未分類",
        "Unset",
        "未設定",
    }
)

MAX_GENRE_LEN = 64
_WS = re.compile(r"\s+")
_HEX = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def clean_genre_name(name: str | None) -> str:
    """Strip and collapse internal whitespace. Does not validate membership."""
    g = _WS.sub(" ", (name or "").strip())
    if len(g) > MAX_GENRE_LEN:
        g = g[:MAX_GENRE_LEN].rstrip()
    return g


def apply_genre_alias(name: str) -> str:
    g = clean_genre_name(name)
    if not g:
        return ""
    return GENRE_ALIASES.get(g, g)


def normalize_color(color: str | None, fallback: str = "#888894") -> str:
    """Return #RRGGBB or fallback."""
    c = (color or "").strip()
    if not c:
        return fallback
    if not c.startswith("#"):
        c = "#" + c
    if not _HEX.match(c):
        return fallback
    if len(c) == 4:
        # #RGB → #RRGGBB
        r, g, b = c[1], c[2], c[3]
        c = f"#{r}{r}{g}{g}{b}{b}"
    return c.lower()


def default_color_for(name: str, index: int = 0) -> str:
    g = apply_genre_alias(name) or clean_genre_name(name)
    if g in DEFAULT_GENRE_COLORS:
        return DEFAULT_GENRE_COLORS[g]
    return PALETTE[index % len(PALETTE)]


def normalize_session_genre(
    genre: str | None,
    catalog: Iterable[str] | None = None,
) -> str:
    """Empty = unset. Only catalog members allowed (after alias map)."""
    g = apply_genre_alias(genre or "")
    if not g:
        return ""
    cat = list(catalog) if catalog is not None else list(DEFAULT_GENRE_CATALOG)
    if g in cat:
        return g
    gl = g.lower()
    for c in cat:
        if c.lower() == gl:
            return c
    return ""


def catalog_names(catalog: Iterable[Any]) -> list[str]:
    """Extract ordered names from string list or entry dicts."""
    out: list[str] = []
    seen: set[str] = set()
    for item in catalog:
        if isinstance(item, dict):
            name = clean_genre_name(str(item.get("name") or ""))
        elif isinstance(item, str):
            name = clean_genre_name(item)
        else:
            continue
        if not name or name in RESERVED_GENRE_NAMES:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def validate_new_genre_name(name: str | None, catalog: Iterable[Any]) -> str:
    """Return cleaned name or raise ValueError."""
    g = clean_genre_name(name)
    if not g:
        raise ValueError("genre name is required")
    if g in RESERVED_GENRE_NAMES or g.lower() in {r.lower() for r in RESERVED_GENRE_NAMES if r}:
        raise ValueError(f"reserved name: {g}")
    names = catalog_names(catalog)
    if any(c.lower() == g.lower() for c in names):
        raise ValueError(f"genre already exists: {g}")
    return g


def parse_genre_catalog(raw: object) -> list[dict[str, str]]:
    """Parse genre_catalog from hub meta → [{name, color}, ...].

    Accepts legacy string lists or object lists. Empty/missing → default seed.
    """
    if not isinstance(raw, list) or not raw:
        return [
            {"name": n, "color": DEFAULT_GENRE_COLORS.get(n, default_color_for(n, i))}
            for i, n in enumerate(DEFAULT_GENRE_CATALOG)
        ]

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        if isinstance(item, str):
            name = clean_genre_name(item)
            color = default_color_for(name, i)
        elif isinstance(item, dict):
            name = clean_genre_name(str(item.get("name") or ""))
            color = normalize_color(
                str(item.get("color") or ""),
                fallback=default_color_for(name, i),
            )
        else:
            continue
        if not name or name in RESERVED_GENRE_NAMES:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        # Prefer known default color when upgrading plain strings without color key
        if isinstance(item, str) and name in DEFAULT_GENRE_COLORS:
            color = DEFAULT_GENRE_COLORS[name]
        out.append({"name": name, "color": color})

    if not out:
        return parse_genre_catalog(None)
    return out


def merge_orphan_genres(
    catalog: list[dict[str, str]],
    session_genres: Iterable[str],
) -> list[dict[str, str]]:
    """Append unknown session genre values so nothing is silently dropped on load."""
    out = [dict(e) for e in catalog]
    seen = {e["name"].lower() for e in out}
    for raw in session_genres:
        g = apply_genre_alias(raw)
        if not g or g in RESERVED_GENRE_NAMES:
            continue
        key = g.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": g, "color": default_color_for(g, len(out))})
    return out


def color_map(catalog: list[dict[str, str]]) -> dict[str, str]:
    return {e["name"]: e["color"] for e in catalog}
