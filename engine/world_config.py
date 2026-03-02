"""
engine/world_config.py — Engine-side loader for a world's config.py.

Usage
──────
Call init(world_path) once at startup — before World() is instantiated or any
game logic runs — to load the selected world's configuration.

All engine modules that need world-specific configuration import from here.
Do NOT import a world's config.py directly.

Helper functions
──────────────────
  list_worlds(data_dir)       → sorted list of world folder Paths
                                (subfolders that contain config.py)
  peek_world_name(world_path) → WORLD_NAME string without updating module state
                                (used for world-selection menus)

Default values (used when config.py is absent or a field is missing)
──────────────────────────────────────────────────────────────────────
  WORLD_NAME       "The World"
  SKILLS           {"perception": "Perception"}   — one skill so checks never error
  NEW_CHAR_HP      100
  CURRENCY_NAME    "gold"
  DEFAULT_STYLE    "brawling"
  EQUIPMENT_SLOTS  ("weapon","head","chest","legs","arms","armor","pack","ring","shield","cape")
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

# ── Defaults ──────────────────────────────────────────────────────────────────

_DEFAULTS: dict = {
    "WORLD_NAME":      "The World",
    "SKILLS":          {"perception": "Perception"},
    "NEW_CHAR_HP":     100,
    "CURRENCY_NAME":   "gold",
    "DEFAULT_STYLE":   "brawling",
    "EQUIPMENT_SLOTS": (
        "weapon", "head", "chest", "legs", "arms",
        "armor",  "pack", "ring", "shield", "cape",
    ),
}

# ── Module-level constants — defaults until init() is called ──────────────────

WORLD_NAME:      str            = _DEFAULTS["WORLD_NAME"]
SKILLS:          dict[str, str] = dict(_DEFAULTS["SKILLS"])
NEW_CHAR_HP:     int            = _DEFAULTS["NEW_CHAR_HP"]
CURRENCY_NAME:   str            = _DEFAULTS["CURRENCY_NAME"]
DEFAULT_STYLE:   str            = _DEFAULTS["DEFAULT_STYLE"]
EQUIPMENT_SLOTS: tuple[str,...] = _DEFAULTS["EQUIPMENT_SLOTS"]

_world_path: Path | None = None

# ── Internal helpers ──────────────────────────────────────────────────────────

def _load_cfg(cfg_path: Path):
    """Load a config.py by path; return the module or None on failure."""
    if not cfg_path.exists():
        return None
    spec = importlib.util.spec_from_file_location("_delve_world_config", cfg_path)
    mod  = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def _get(cfg, attr: str, default):
    v = getattr(cfg, attr, None) if cfg else None
    return v if v is not None else default


# ── Public API ────────────────────────────────────────────────────────────────

def init(world_path: Path) -> None:
    """Load world_path/config.py and update all module-level constants.

    Must be called before World() is instantiated or any game logic runs.
    Safe to call multiple times (e.g., switching worlds between sessions).
    """
    global _world_path, WORLD_NAME, SKILLS, NEW_CHAR_HP
    global CURRENCY_NAME, DEFAULT_STYLE, EQUIPMENT_SLOTS

    _world_path = world_path
    cfg = _load_cfg(world_path / "config.py")

    WORLD_NAME    = str(_get(cfg, "WORLD_NAME",    _DEFAULTS["WORLD_NAME"]))
    NEW_CHAR_HP   = int(_get(cfg, "NEW_CHAR_HP",   _DEFAULTS["NEW_CHAR_HP"]))
    CURRENCY_NAME = str(_get(cfg, "CURRENCY_NAME", _DEFAULTS["CURRENCY_NAME"]))
    DEFAULT_STYLE = str(_get(cfg, "DEFAULT_STYLE", _DEFAULTS["DEFAULT_STYLE"]))

    raw_skills = _get(cfg, "SKILLS", None)
    if isinstance(raw_skills, dict) and raw_skills:
        SKILLS = {str(k): str(v) for k, v in raw_skills.items()}
    else:
        SKILLS = dict(_DEFAULTS["SKILLS"])

    raw_slots = _get(cfg, "EQUIPMENT_SLOTS", None)
    if isinstance(raw_slots, (list, tuple)) and raw_slots:
        EQUIPMENT_SLOTS = tuple(str(s) for s in raw_slots)
    else:
        EQUIPMENT_SLOTS = _DEFAULTS["EQUIPMENT_SLOTS"]


def list_worlds(data_dir: Path) -> list[Path]:
    """Return sorted world folder Paths — subfolders of data_dir that contain config.py."""
    if not data_dir.is_dir():
        return []
    return sorted(
        p for p in data_dir.iterdir()
        if p.is_dir() and (p / "config.py").exists()
    )


def peek_world_name(world_path: Path) -> str:
    """Return WORLD_NAME from world_path/config.py without updating module state.

    Used for world-selection menus. Falls back to the folder name on failure.
    """
    cfg = _load_cfg(world_path / "config.py")
    return str(_get(cfg, "WORLD_NAME", world_path.name))
