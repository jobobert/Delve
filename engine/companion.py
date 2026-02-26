"""
companion.py — Companion system for Delve.

Companions are persistent NPCs that travel with the player. They come in
three types, each a superset of the last:

  narrative   — Sets a flag, provides dialogue hints and flavour. No combat,
                no carry bonus. Grimwick is this type in Blackfen.
                Example: a guide who knows secret paths.

  utility     — Narrative + carry capacity bonus. Has room restrictions
                (can't enter rooms flagged no_large_companion). Stays at
                the last valid room and rejoins automatically when you return.
                Example: Dust the Mule (+15 carry, can't enter tight caves).

  combat      — Utility + attacks in combat after the player each round.
                Has HP. If reduced to 0, becomes "downed" — they won't fight
                until you rest at an inn. They don't die permanently.
                Example: Aonn Tesk (veteran swordsman, quest companion).

Data model
──────────
Companion definitions live in data/<zone>/companions/<companion_id>.toml.

Each definition looks like:

    id           = "aonn_tesk"
    name         = "Aonn Tesk"
    type         = "combat"           # narrative | utility | combat
    desc_short   = "A scarred elf veteran with one arm."
    attack       = 8
    defense      = 4
    hp           = 40
    max_hp       = 40
    carry_bonus  = 0                  # extra carry stones (utility/combat only)
    style        = "swordplay"
    style_prof   = 30.0
    restrictions = ["no_large_companion"]   # room flags that block this companion
    join_message = "Aonn falls into step beside you, hand on his sword hilt."
    wait_message = "Aonn waits here — the way ahead won't accommodate him."
    rejoin_message = "Aonn rejoins you, scanning the area with practised eyes."
    downed_message = "Aonn is down. He needs rest before he can fight again."

Active companion state (stored on Player.companion):
    {
        "id":           "aonn_tesk",
        "status":       "active" | "downed" | "waiting" | "dismissed",
        "hp":           35,
        "waiting_room": "blackfen_gate",    # room they're waiting in (if waiting)
        "_def":         { ...full definition dict... }   # stripped on save, re-attached on load
    }
"""

from __future__ import annotations
from pathlib import Path
from typing import Any
import random

from engine.toml_io import load as toml_load


DATA_DIR = Path("data")


# ── Definition loading ────────────────────────────────────────────────────────

_DEF_CACHE: dict[str, dict | None] = {}


def _scan_companion_defs() -> dict[str, Path]:
    """Walk all zone folders for companions/<id>.toml files."""
    result: dict[str, Path] = {}
    for zone_dir in DATA_DIR.iterdir():
        if not zone_dir.is_dir():
            continue
        comp_dir = zone_dir / "companions"
        if not comp_dir.exists():
            continue
        for path in comp_dir.glob("*.toml"):
            cid = path.stem
            if cid not in result:
                result[cid] = path
    return result


_PATH_MAP: dict[str, Path] = {}
_PATH_MAP_BUILT = False


def _ensure_path_map() -> None:
    global _PATH_MAP, _PATH_MAP_BUILT
    if not _PATH_MAP_BUILT:
        _PATH_MAP = _scan_companion_defs()
        _PATH_MAP_BUILT = True


def load_def(companion_id: str) -> dict | None:
    """Load and cache a companion definition by id."""
    _ensure_path_map()
    if companion_id in _DEF_CACHE:
        return _DEF_CACHE[companion_id]
    path = _PATH_MAP.get(companion_id)
    if not path:
        _DEF_CACHE[companion_id] = None
        return None
    data = toml_load(path)
    _DEF_CACHE[companion_id] = data
    return data


def all_companion_ids() -> list[str]:
    """Return all known companion ids (for validation)."""
    _ensure_path_map()
    return list(_PATH_MAP.keys())


# ── Active companion helpers ───────────────────────────────────────────────────

def is_active(companion: dict | None) -> bool:
    return companion is not None and companion.get("status") == "active"


def is_waiting(companion: dict | None) -> bool:
    return companion is not None and companion.get("status") == "waiting"


def is_downed(companion: dict | None) -> bool:
    return companion is not None and companion.get("status") == "downed"


def companion_type(companion: dict | None) -> str:
    """Return 'narrative', 'utility', or 'combat' (or '' if no companion)."""
    if not companion:
        return ""
    cdef = companion.get("_def", {})
    return cdef.get("type", "narrative")


def carry_bonus(companion: dict | None) -> int:
    """Extra carry capacity granted by an active utility/combat companion."""
    if not is_active(companion):
        return 0
    cdef = companion.get("_def", {})
    return int(cdef.get("carry_bonus", 0))


def display_name(companion: dict | None) -> str:
    if not companion:
        return ""
    cdef = companion.get("_def", {})
    return companion.get("name", cdef.get("name", "?"))


def short_desc(companion: dict | None) -> str:
    if not companion:
        return ""
    cdef = companion.get("_def", {})
    return cdef.get("desc_short", "")


# ── Room restriction check ────────────────────────────────────────────────────

def can_enter_room(companion: dict | None, room: dict) -> bool:
    """
    Return True if the companion can enter this room.
    Narrative companions always can. Utility/combat companions check
    room flags against their restrictions list.
    """
    if not companion:
        return True
    ctype = companion_type(companion)
    if ctype == "narrative":
        return True
    cdef = companion.get("_def", {})
    restrictions: list[str] = cdef.get("restrictions", [])
    room_flags: list[str] = room.get("flags", [])
    # If any companion restriction matches a room flag, blocked
    for r in restrictions:
        if r in room_flags:
            return False
    return True


# ── Companion creation ────────────────────────────────────────────────────────

def create_active(companion_id: str) -> dict | None:
    """
    Build a fresh active companion state dict from a definition.
    Returns None if the definition doesn't exist.
    """
    cdef = load_def(companion_id)
    if not cdef:
        return None
    return {
        "id":           companion_id,
        "name":         cdef.get("name", companion_id),
        "status":       "active",
        "hp":           cdef.get("max_hp", cdef.get("hp", 0)),
        "waiting_room": "",
        "_def":         cdef,
    }


# ── Combat contribution ───────────────────────────────────────────────────────

def companion_attack(companion: dict, npc: dict, bus, safe_room: bool) -> None:
    """
    Execute one companion attack against the NPC.
    Only called if companion type == 'combat' and status == 'active'.
    Emits output via the bus. Modifies npc['hp'] in place.
    """
    from engine.msg import Msg, Tag
    from engine.events import Event

    def _out(tag: str, text: str) -> None:
        bus.emit(Event.OUTPUT, Msg(tag, text))

    cdef = companion.get("_def", {})
    name = companion.get("name", cdef.get("name", "Your companion"))
    atk  = int(cdef.get("attack", 5))
    npc_def = int(npc.get("defense", 0))

    # Style matchup bonus (simple: swordplay vs humanoid gets +1)
    style_id = cdef.get("style", "")
    bonus = 0
    if style_id == "swordplay" and "humanoid" in npc.get("tags", []):
        bonus = 1
    elif style_id == "brawling":
        bonus = 0

    dmg = max(1, atk + bonus - npc_def + random.randint(-1, 3))
    npc_max = npc.get("max_hp", 10)

    if safe_room:
        _out(Tag.COMBAT_HIT,
             f"{name} strikes the {npc['name']} for {dmg} — but it shrugs it off. [safe zone]")
        return

    npc["hp"] -= dmg
    hp_now = max(0, npc["hp"])
    _out(Tag.COMBAT_HIT,
         f"{name} strikes {npc['name']} for {dmg}. ({hp_now}/{npc_max} HP)")


def companion_take_hit(companion: dict, npc: dict, bus) -> bool:
    """
    NPC lands a retaliatory blow on the companion (combat type only).
    Returns True if the companion was just downed.
    Only called occasionally — companions are not always in the line of fire.
    """
    from engine.msg import Msg, Tag
    from engine.events import Event

    def _out(tag: str, text: str) -> None:
        bus.emit(Event.OUTPUT, Msg(tag, text))

    cdef    = companion.get("_def", {})
    name    = companion.get("name", cdef.get("name", "Your companion"))
    n_atk   = int(npc.get("attack", 5))
    c_def   = int(cdef.get("defense", 0))
    dmg     = max(1, n_atk - c_def + random.randint(-2, 3))
    max_hp  = int(cdef.get("max_hp", companion.get("hp", 10)))

    companion["hp"] = max(0, companion.get("hp", max_hp) - dmg)
    hp_now = companion["hp"]
    _out(Tag.COMBAT_RECV,
         f"{npc['name']} also strikes {name} for {dmg}. "
         f"({hp_now}/{max_hp} HP)")

    if hp_now <= 0:
        companion["status"] = "downed"
        down_msg = cdef.get("downed_message",
                            f"{name} is downed! They need rest to recover.")
        _out(Tag.COMBAT_RECV, down_msg)
        return True
    return False


# ── Recovery (at inn rest) ────────────────────────────────────────────────────

def rest_companion(companion: dict | None) -> bool:
    """
    Restore a downed companion to active. Returns True if they recovered.
    Called when the player rests at an inn.
    """
    if not companion:
        return False
    if companion.get("status") == "downed":
        cdef   = companion.get("_def", {})
        max_hp = int(cdef.get("max_hp", 40))
        companion["hp"]     = max_hp
        companion["status"] = "active"
        return True
    # Also top up HP if active but wounded
    if companion.get("status") == "active":
        cdef   = companion.get("_def", {})
        max_hp = int(cdef.get("max_hp", 40))
        companion["hp"] = max_hp
    return False


# ── Persistence helpers ───────────────────────────────────────────────────────

def serialise(companion: dict | None) -> dict:
    """Strip the _def blob before saving; it's re-attached on load."""
    if not companion:
        return {}
    return {k: v for k, v in companion.items() if k != "_def"}


def deserialise(raw: dict) -> dict | None:
    """Re-attach _def from data files when loading a saved companion."""
    if not raw:
        return None
    cid  = raw.get("id", "")
    cdef = load_def(cid)
    if not cdef:
        return None   # companion definition removed — drop silently
    return {**raw, "_def": cdef}


