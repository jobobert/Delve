"""
room_flags.py — Room flag constants and helpers.

Flags are a plain list of strings on each room's TOML entry:

    flags = ["safe_combat", "no_combat"]

Using a list (not a dict) keeps authoring simple — you just add a string.
New flags should be added as class constants here and documented below, then
checked in the engine via RoomFlags.has().

Current flags
─────────────
safe_combat
    NPC attacks still occur and XP/gold are awarded normally, but the player
    takes zero damage. Used for the Training Yard so new players can learn
    mechanics without risking death.

no_combat
    The `attack` command is blocked entirely. Good for inns, town squares,
    council halls, and anywhere violence would be narratively wrong.

healing
    Player regenerates HP passively — `heal_rate` HP per command (defaults to 5
    if the field is absent). Hook is present in CommandProcessor._apply_room_effects().

reduced_stats
    All combatants use half their effective attack and defense. Useful for
    challenge rooms, sparring rings, or magically dampened areas.

sleep
    The `sleep`/`rest` command works here without needing an innkeeper NPC
    present. Useful for safe campsites, player homes, etc. Rest is free.

no_large_companion
    Blocks utility and combat companions from entering (tight caves, cliff
    faces, narrow passages). The companion waits at the last valid room and
    rejoins automatically when the player returns. Narrative companions
    are always allowed through — they exist as a flag, not a physical being.

town
    Marks the room as a town/settlement anchor. On entering a town room the
    player's bind_room is automatically updated to this room — no command
    needed. On death the player respawns at their bind_room. Every zone that
    has dangerous content should have at least one reachable town room so
    the player always has a sensible respawn point. Town rooms should also
    have no_combat set (they're separate flags so you can have one without
    the other in edge cases, but normally both appear together).
"""

from __future__ import annotations


class RoomFlags:
    SAFE_COMBAT    = "safe_combat"    # Player takes no damage
    NO_COMBAT      = "no_combat"      # Attack command blocked
    HEALING        = "healing"        # Passive HP regen each action
    REDUCED_STATS  = "reduced_stats"  # Halved attack/defense for everyone
    SLEEP          = "sleep"          # Free rest without an innkeeper
    TOWN           = "town"           # Auto-binds player respawn on entry
    HAZARD             = "hazard"             # Deals passive damage each command
    NO_LARGE_COMPANION = "no_large_companion" # Utility/combat companions can't enter

    @staticmethod
    def has(room: dict, flag: str) -> bool:
        """Return True if the room has the given flag set."""
        return flag in room.get("flags", [])

    @staticmethod
    def all_flags(room: dict) -> list[str]:
        """Return all flags set on this room."""
        return list(room.get("flags", []))
