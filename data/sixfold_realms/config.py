"""
data/sixfold_realms/config.py — World configuration for Delve.

Edit this file to customise your world. It is read by the engine at startup
and overrides all engine defaults. If this file is absent the engine falls
back to built-in defaults (world name "The World", one built-in skill set).

This file IS version-controlled — it belongs with your world data.
"""

# ── World identity ─────────────────────────────────────────────────────────────
# Displayed in the map header and world-specific messages.
WORLD_NAME: str = "The Sixfold Realms"

# ── Skills ─────────────────────────────────────────────────────────────────────
# Maps skill_id (used in scripts and dialogue conditions) to display name.
# Remove, add, or rename entries freely; the engine picks up changes on restart.
# If this dict is absent or empty, the engine falls back to a single built-in
# default skill ("perception") so the engine always has at least one skill.
#
# Usage in TOML:
#   condition = { skill = "social", min = 40 }
#   { op = "skill_check", skill = "stealth", dc = 14, on_pass = [...] }
SKILLS: dict = {
    "stealth":    "Stealth",
    "survival":   "Survival",
    "perception": "Perception",
    "athletics":  "Athletics",
    "social":     "Social",
    "arcana":     "Arcana",
    "mining":     "Mining",
}

# ── New character stats ────────────────────────────────────────────────────────
# Starting HP for every new character (fixed — not rolled).
# Attack is rolled as 3d6-drop-lowest (range 2–12).
# Defense is rolled as half of a separate 3d6-drop-lowest (range 1–6), minimum 1.
NEW_CHAR_HP: int = 100

# ── Currency ───────────────────────────────────────────────────────────────────
# Display name for the in-game currency (used in stats, shop headers, etc.).
# Note: command keywords "deposit gold" / "withdraw gold" are grammar and unchanged.
CURRENCY_NAME: str = "gold"

# ── Fighting styles ────────────────────────────────────────────────────────────
# ID of the style every new character starts with.
# Must match a [[style]] id defined in data/sixfold_realms/*/styles/*.toml.
DEFAULT_STYLE: str = "brawling"

# ── Equipment slots ────────────────────────────────────────────────────────────
# Defines which equipment slots exist and their display order.
# Item TOML slot= values must match an entry in this list.
# Add, remove, or rename entries to fit your world's genre.
EQUIPMENT_SLOTS: tuple = (
    "weapon", "head", "chest", "legs", "arms",
    "pack", "ring", "shield", "cape",
)
