"""
engine/world_config.py — Engine-side loader for a world's config.toml (or legacy config.py).

Usage
──────
Call init(world_path) once at startup — before World() is instantiated or any
game logic runs — to load the selected world's configuration.

All engine modules that need world-specific configuration import from here.
Do NOT import a world's config files directly.

Config file format
───────────────────
Worlds use data/<world_id>/config.toml (preferred). Legacy config.py is still
accepted as a fallback so existing worlds continue to work without changes.
See data/WORLD_MANUAL.md §1 for the full config.toml field reference.

Helper functions
──────────────────
  list_worlds(data_dir)       → sorted list of world folder Paths
                                (subfolders that contain config.toml or config.py)
  peek_world_name(world_path) → WORLD_NAME string without updating module state
                                (used for world-selection menus)

Default values (used when no config file is found or a field is missing)
──────────────────────────────────────────────────────────────────────────
  WORLD_NAME        "The World"
  SKILLS            {"perception": "Perception"}   — one skill so checks never error
  NEW_CHAR_HP       100
  CURRENCY_NAME     "gold"
  CURRENCY_ABBREV   "g"    — short suffix used in compact price displays (e.g. "50g")
  DEFAULT_STYLE     "brawling"
  VISION_THRESHOLD  3                              — minimum light level to see clearly
  EQUIPMENT_SLOTS   ("weapon","head","chest","legs","arms","pack","ring","shield","cape")
  PLAYER_ATTRS      []                             — world-defined numeric player attributes
  STATUS_EFFECTS    [...]                          — world-defined status effects (see below)

Status effect fields (per [[status_effect]] block in config.toml):
  id              — string key used in apply_status / clear_status / if_status ops
  label           — display name shown on the stats screen
  apply_msg       — message printed when the effect is applied
  expiry_msg      — message printed when the effect expires naturally
  combat_atk      — flat attack modifier while active (negative = debuff)
  combat_def      — flat defense modifier while active (negative = debuff)
  damage_per_move — HP damage dealt to player on each command tick
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

# ── Defaults ──────────────────────────────────────────────────────────────────

_DEFAULTS: dict = {
    "WORLD_NAME":       "The World",
    "SKILLS":           {"perception": "Perception"},
    "NEW_CHAR_HP":      100,
    "CURRENCY_NAME":    "gold",
    "CURRENCY_ABBREV":  "g",
    "DEFAULT_STYLE":    "brawling",
    "VISION_THRESHOLD": 3,
    "EQUIPMENT_SLOTS":  (
        "weapon", "head", "chest", "legs", "arms",
        "pack", "ring", "shield", "cape",
    ),
    "PLAYER_ATTRS":     [],
    # Built-in status effects — used when the world defines no [[status_effect]] blocks.
    "STATUS_EFFECTS": [
        {"id": "poisoned",  "label": "Poisoned",
         "apply_msg":  "Poison courses through you.",
         "expiry_msg": "The poison runs its course. You feel yourself again.",
         "combat_atk": 0, "combat_def": 0, "damage_per_move": 3},
        {"id": "blinded",   "label": "Blinded",
         "apply_msg":  "Your vision goes dark.",
         "expiry_msg": "Your vision clears.",
         "combat_atk": -4, "combat_def": 0, "damage_per_move": 0},
        {"id": "weakened",  "label": "Weakened",
         "apply_msg":  "Weakness floods your limbs.",
         "expiry_msg": "Your strength returns.",
         "combat_atk": -4, "combat_def": 0, "damage_per_move": 0},
        {"id": "slowed",    "label": "Slowed",
         "apply_msg":  "Your movements become sluggish.",
         "expiry_msg": "The sluggishness lifts.",
         "combat_atk": 0, "combat_def": -3, "damage_per_move": 0},
        {"id": "protected", "label": "Protected",
         "apply_msg":  "A ward settles over you.",
         "expiry_msg": "The protective ward fades.",
         "combat_atk": 0, "combat_def": 3, "damage_per_move": 0},
    ],
}

# ── Module-level constants — defaults until init() is called ──────────────────

WORLD_NAME:       str            = _DEFAULTS["WORLD_NAME"]
SKILLS:           dict[str, str] = dict(_DEFAULTS["SKILLS"])
NEW_CHAR_HP:      int            = _DEFAULTS["NEW_CHAR_HP"]
CURRENCY_NAME:    str            = _DEFAULTS["CURRENCY_NAME"]
CURRENCY_ABBREV:  str            = _DEFAULTS["CURRENCY_ABBREV"]
DEFAULT_STYLE:    str            = _DEFAULTS["DEFAULT_STYLE"]
VISION_THRESHOLD: int            = _DEFAULTS["VISION_THRESHOLD"]
EQUIPMENT_SLOTS:  tuple[str,...] = _DEFAULTS["EQUIPMENT_SLOTS"]
PLAYER_ATTRS:     list[dict]     = list(_DEFAULTS["PLAYER_ATTRS"])
STATUS_EFFECTS:   list[dict]     = list(_DEFAULTS["STATUS_EFFECTS"])

_world_path: Path | None = None

# ── Internal helpers ──────────────────────────────────────────────────────────

def _load_toml_cfg(cfg_path: Path) -> dict | None:
    """Load a config.toml by path; return the parsed dict or None on failure."""
    if not cfg_path.exists():
        return None
    # Import locally to avoid circular imports during module-level setup.
    from engine.toml_io import load as toml_load
    try:
        return toml_load(cfg_path)
    except Exception:
        return None


def _load_py_cfg(cfg_path: Path):
    """Load a legacy config.py by path; return the module or None on failure."""
    if not cfg_path.exists():
        return None
    spec = importlib.util.spec_from_file_location("_delve_world_config", cfg_path)
    mod  = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def _get_t(cfg: dict | None, key: str, default):
    """Get a value from a TOML config dict (keys are lowercase)."""
    if cfg is None:
        return default
    v = cfg.get(key)
    return v if v is not None else default


def _get_p(cfg, attr: str, default):
    """Get an attribute from a legacy Python config module."""
    v = getattr(cfg, attr, None) if cfg else None
    return v if v is not None else default


def _has_config(world_path: Path) -> bool:
    """True if the folder contains a config.toml or legacy config.py."""
    return (world_path / "config.toml").exists() or (world_path / "config.py").exists()


# ── Init helpers ──────────────────────────────────────────────────────────────

def _load_status_effects(raw: list) -> list[dict]:
    """Normalise a list of raw status effect dicts, filling in missing fields."""
    out = []
    for se in raw:
        if not isinstance(se, dict) or not se.get("id"):
            continue
        sid = str(se["id"])
        out.append({
            "id":              sid,
            "label":           str(se.get("label", sid.capitalize())),
            "apply_msg":       str(se.get("apply_msg",  f"You are now {sid}.")),
            "expiry_msg":      str(se.get("expiry_msg", f"The {sid} condition ends.")),
            "combat_atk":      int(se.get("combat_atk",      0)),
            "combat_def":      int(se.get("combat_def",      0)),
            "damage_per_move": int(se.get("damage_per_move", 0)),
        })
    return out


def get_status_effect(effect_id: str) -> "dict | None":
    """Return the status effect definition for the given id, or None if unknown."""
    for se in STATUS_EFFECTS:
        if se["id"] == effect_id:
            return se
    return None


def _init_from_toml(cfg: dict) -> None:
    """Populate module constants from a parsed config.toml dict."""
    global WORLD_NAME, SKILLS, NEW_CHAR_HP, CURRENCY_NAME, CURRENCY_ABBREV
    global DEFAULT_STYLE, VISION_THRESHOLD, EQUIPMENT_SLOTS, PLAYER_ATTRS, STATUS_EFFECTS

    WORLD_NAME       = str(_get_t(cfg, "world_name",       _DEFAULTS["WORLD_NAME"]))
    NEW_CHAR_HP      = int(_get_t(cfg, "new_char_hp",      _DEFAULTS["NEW_CHAR_HP"]))
    CURRENCY_NAME    = str(_get_t(cfg, "currency_name",    _DEFAULTS["CURRENCY_NAME"]))
    CURRENCY_ABBREV  = str(_get_t(cfg, "currency_abbrev",  CURRENCY_NAME[:1] or "g"))
    DEFAULT_STYLE    = str(_get_t(cfg, "default_style",    _DEFAULTS["DEFAULT_STYLE"]))
    VISION_THRESHOLD = int(_get_t(cfg, "vision_threshold", _DEFAULTS["VISION_THRESHOLD"]))

    raw_skills = cfg.get("skills")
    if isinstance(raw_skills, dict) and raw_skills:
        SKILLS = {str(k): str(v) for k, v in raw_skills.items()}
    else:
        SKILLS = dict(_DEFAULTS["SKILLS"])

    raw_slots = cfg.get("equipment_slots")
    if isinstance(raw_slots, list) and raw_slots:
        EQUIPMENT_SLOTS = tuple(str(s) for s in raw_slots)
    else:
        EQUIPMENT_SLOTS = _DEFAULTS["EQUIPMENT_SLOTS"]

    raw_attrs = cfg.get("player_attrs", [])
    if isinstance(raw_attrs, list):
        PLAYER_ATTRS = [dict(a) for a in raw_attrs if isinstance(a, dict)]
    else:
        PLAYER_ATTRS = []

    raw_se = cfg.get("status_effect", [])
    if isinstance(raw_se, list) and raw_se:
        STATUS_EFFECTS = _load_status_effects(raw_se)
    else:
        STATUS_EFFECTS = list(_DEFAULTS["STATUS_EFFECTS"])


def _init_from_py(cfg) -> None:
    """Populate module constants from a legacy config.py module (backward compat)."""
    global WORLD_NAME, SKILLS, NEW_CHAR_HP, CURRENCY_NAME, CURRENCY_ABBREV
    global DEFAULT_STYLE, VISION_THRESHOLD, EQUIPMENT_SLOTS, PLAYER_ATTRS, STATUS_EFFECTS

    WORLD_NAME       = str(_get_p(cfg, "WORLD_NAME",       _DEFAULTS["WORLD_NAME"]))
    NEW_CHAR_HP      = int(_get_p(cfg, "NEW_CHAR_HP",      _DEFAULTS["NEW_CHAR_HP"]))
    CURRENCY_NAME    = str(_get_p(cfg, "CURRENCY_NAME",    _DEFAULTS["CURRENCY_NAME"]))
    CURRENCY_ABBREV  = str(_get_p(cfg, "CURRENCY_ABBREV",  CURRENCY_NAME[:1] or "g"))
    DEFAULT_STYLE    = str(_get_p(cfg, "DEFAULT_STYLE",    _DEFAULTS["DEFAULT_STYLE"]))
    VISION_THRESHOLD = int(_get_p(cfg, "VISION_THRESHOLD", _DEFAULTS["VISION_THRESHOLD"]))

    raw_skills = _get_p(cfg, "SKILLS", None)
    if isinstance(raw_skills, dict) and raw_skills:
        SKILLS = {str(k): str(v) for k, v in raw_skills.items()}
    else:
        SKILLS = dict(_DEFAULTS["SKILLS"])

    raw_slots = _get_p(cfg, "EQUIPMENT_SLOTS", None)
    if isinstance(raw_slots, (list, tuple)) and raw_slots:
        EQUIPMENT_SLOTS = tuple(str(s) for s in raw_slots)
    else:
        EQUIPMENT_SLOTS = _DEFAULTS["EQUIPMENT_SLOTS"]

    raw_attrs = _get_p(cfg, "PLAYER_ATTRS", [])
    if isinstance(raw_attrs, list):
        PLAYER_ATTRS = [dict(a) for a in raw_attrs if isinstance(a, dict)]
    else:
        PLAYER_ATTRS = []

    STATUS_EFFECTS = list(_DEFAULTS["STATUS_EFFECTS"])


# ── Public API ────────────────────────────────────────────────────────────────

def init(world_path: Path) -> None:
    """Load world_path/config.toml (or legacy config.py) and update all constants.

    config.toml is preferred. config.py is accepted as a backward-compatible
    fallback — any world not yet migrated continues to work without changes.

    Must be called before World() is instantiated or any game logic runs.
    Safe to call multiple times (e.g., switching worlds between sessions).
    """
    global _world_path
    _world_path = world_path

    # Propagate world path to content-scanning modules so they look in the
    # right world folder instead of the top-level data/ directory.
    import engine.quests as _q
    import engine.dialogue as _d
    import engine.crafting as _c
    _q._DATA_ROOT        = world_path
    _q._QUEST_CACHE      = {}
    _d._DATA_ROOT        = world_path
    _d._CACHE            = {}
    _d._PATH_MAP         = None
    _c._DATA_ROOT        = world_path
    _c._COMMISSION_CACHE = None

    # Try TOML first, fall back to legacy Python config.
    toml_cfg = _load_toml_cfg(world_path / "config.toml")
    if toml_cfg is not None:
        _init_from_toml(toml_cfg)
    else:
        py_cfg = _load_py_cfg(world_path / "config.py")
        _init_from_py(py_cfg)


def list_worlds(data_dir: Path) -> list[Path]:
    """Return sorted world folder Paths — subfolders with config.toml or config.py."""
    if not data_dir.is_dir():
        return []
    return sorted(p for p in data_dir.iterdir() if p.is_dir() and _has_config(p))


def peek_world_name(world_path: Path) -> str:
    """Return WORLD_NAME from world_path/config.toml (or config.py) without updating state.

    Used for world-selection menus. Falls back to the folder name on failure.
    """
    toml_cfg = _load_toml_cfg(world_path / "config.toml")
    if toml_cfg is not None:
        return str(_get_t(toml_cfg, "world_name", world_path.name))
    py_cfg = _load_py_cfg(world_path / "config.py")
    return str(_get_p(py_cfg, "WORLD_NAME", world_path.name))
