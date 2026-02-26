"""
crafting.py — Commission-based crafting system for Delve.

Players commission items from specialist NPCs (blacksmiths, leatherworkers,
alchemists). The crafter requires specific materials and a number of turns to
work. When complete, the player collects a finished item whose stats are
determined by a weighted quality roll.

Commission definitions live in data/<zone>/crafting/<npc_id>.toml. Each file
holds [[commission]] blocks describing what that NPC can make.

Data format
───────────
[[commission]]
id          = "war_sword"           # unique identifier
npc_id      = "blacksmith_npc"      # who makes it (must match dialogue)
label       = "War Sword"           # shown in menu
desc        = "A heavy fighting sword, well-balanced."
slot        = "weapon"              # item slot for the finished piece
weapon_tags = ["sword", "heavy"]    # optional — copied to finished item
armor_tags  = []                    # optional — copied to finished item
materials   = ["iron_ore", "iron_ore", "coal_chunk"]  # item IDs required
turns_required = 30                 # room-moves until ready (rest = 20 turns)
gold_cost   = 0                     # upfront gold deposit (can be 0)
xp_reward   = 50                    # XP awarded on collection

[[quality]]
tier      = "poor"
weight    = 20              # probability weight (higher = more likely)
# At least one of these per quality tier:
attack_bonus   = 2          # added as stat_bonus effect (weapons)
defense_bonus  = 0
max_hp_bonus   = 0
carry_bonus    = 0          # carry_capacity bonus (bags/packs)
special        = ""         # tag appended to weapon_tags/armor_tags
equip_msg      = "The blade is rough, but serviceable."
name_prefix    = ""         # "Rough " → "Rough War Sword"  (optional)

[[quality]]
tier      = "standard"
weight    = 55
attack_bonus = 5
equip_msg = "A solid blade. Dorin knows his craft."

[[quality]]
tier      = "exceptional"
weight    = 20
attack_bonus = 8
special   = "sharp"         # added as weapon tag, readable by styles
equip_msg = "The edge is notably keen. You can hear it hum."
name_prefix = "Fine "

[[quality]]
tier      = "masterwork"
weight    = 5
attack_bonus = 12
special   = "sharp"
equip_msg = "Dorin eyes it for a long moment before handing it over."
name_prefix = "Masterwork "
craft_message = "Dorin holds it up to the light. 'I won't make another one that good for a year,' he says quietly."

Player commission state (stored in player.commissions)
───────────────────────────────────────────────────────
Each active commission is a dict:
{
  "commission_id": "war_sword",
  "npc_id":        "blacksmith_npc",
  "zone_id":       "millhaven",
  "turns_remaining": 28,
  "materials_given": ["iron_ore", "iron_ore", "coal_chunk"],   # deposited
  "materials_needed": ["iron_ore", "iron_ore", "coal_chunk"],  # required
  "status":  "waiting_materials" | "in_progress" | "ready",
  "label":   "War Sword",
  "slot":    "weapon",
}

Commands
────────
  commissions            — list all active commissions
  commission <npc>       — open the commission menu for an NPC
  give <item> to <npc>   — deposit a material for a pending commission
  collect                — collect a finished item from a nearby crafter NPC
"""

from __future__ import annotations

import copy
import random
from pathlib import Path
from typing import TYPE_CHECKING

from engine.toml_io import load as toml_load

if TYPE_CHECKING:
    from engine.player import Player

# ── Loading ────────────────────────────────────────────────────────────────────

_DATA_ROOT = Path(__file__).parent.parent / "data"
_SKIP_DIRS = {"zone_state", "players"}

# Cache: npc_id → list of commission defs
_COMMISSION_CACHE: dict[str, list[dict]] | None = None


def _load_all() -> dict[str, list[dict]]:
    """
    Scan all zone crafting/ subdirs and build a map of npc_id → [commission, ...].
    """
    global _COMMISSION_CACHE
    if _COMMISSION_CACHE is not None:
        return _COMMISSION_CACHE

    result: dict[str, list[dict]] = {}

    for zone_dir in sorted(_DATA_ROOT.iterdir()):
        if not zone_dir.is_dir() or zone_dir.name in _SKIP_DIRS:
            continue
        crafting_dir = zone_dir / "crafting"
        if not crafting_dir.exists():
            continue
        for path in sorted(crafting_dir.glob("*.toml")):
            try:
                data = toml_load(path)
            except Exception:
                continue
            for commission in data.get("commission", []):
                npc_id = commission.get("npc_id", "")
                if not npc_id:
                    continue
                # Attach zone_id so we can reference it later
                commission = {**commission, "_zone": zone_dir.name}
                result.setdefault(npc_id, []).append(commission)

    _COMMISSION_CACHE = result
    return result


def commissions_for_npc(npc_id: str) -> list[dict]:
    """Return all commission definitions available from a given NPC."""
    return _load_all().get(npc_id, [])


def commission_by_id(commission_id: str) -> dict | None:
    """Find a commission definition by its id across all NPCs."""
    for commissions in _load_all().values():
        for c in commissions:
            if c.get("id") == commission_id:
                return c
    return None


# ── Quality rolling ────────────────────────────────────────────────────────────

def roll_quality(commission_def: dict) -> dict:
    """
    Pick a quality tier using weighted random selection.
    Returns the chosen [[quality]] block.
    """
    tiers   = commission_def.get("qualities", commission_def.get("quality", []))
    if not tiers:
        # Fallback: plain item with no bonus
        return {"tier": "standard", "weight": 1}

    weights = [int(t.get("weight", 1)) for t in tiers]
    total   = sum(weights)
    roll    = random.randint(1, total)
    cumulative = 0
    for tier, weight in zip(tiers, weights):
        cumulative += weight
        if roll <= cumulative:
            return tier
    return tiers[-1]


def build_item(commission_def: dict, quality: dict) -> dict:
    """
    Construct a finished item dict from a commission definition and a rolled quality tier.
    The result is a standard Delve item dict with effects[], ready to be given to the player.
    """
    base_name = commission_def.get("label", "Commissioned Item")
    prefix    = quality.get("name_prefix", "")
    name      = f"{prefix}{base_name}".strip()

    slot         = commission_def.get("slot", "weapon")
    weapon_tags  = list(commission_def.get("weapon_tags", []))
    armor_tags   = list(commission_def.get("armor_tags", []))
    special      = quality.get("special", "")
    if special:
        if slot == "weapon":
            weapon_tags.append(special)
        elif slot in ("armor", "shield", "cape"):
            armor_tags.append(special)

    effects: list[dict] = []

    attack_bonus   = int(quality.get("attack_bonus",  0))
    defense_bonus  = int(quality.get("defense_bonus", 0))
    max_hp_bonus   = int(quality.get("max_hp_bonus",  0))
    carry_bonus    = int(quality.get("carry_bonus",   0))

    if attack_bonus:
        effects.append({"type": "stat_bonus", "stat": "attack",  "amount": attack_bonus})
    if defense_bonus:
        effects.append({"type": "stat_bonus", "stat": "defense", "amount": defense_bonus})
    if max_hp_bonus:
        effects.append({"type": "stat_bonus", "stat": "max_hp",  "amount": max_hp_bonus})
    if carry_bonus:
        effects.append({"type": "stat_bonus", "stat": "carry_capacity", "amount": carry_bonus})

    equip_msg = quality.get("equip_msg", "")
    if equip_msg:
        effects.append({"type": "on_equip", "message": equip_msg})

    tier = quality.get("tier", "standard")

    item: dict = {
        "id":          f"commissioned_{commission_def.get('id', 'item')}_{tier}",
        "name":        name,
        "desc_short":  f"A {tier} {base_name.lower()}, commissioned from a crafter.",
        "desc_long":   commission_def.get("desc", "A crafted item."),
        "slot":        slot,
        "weight":      int(commission_def.get("weight", 2)),
        "effects":     effects,
        "_commissioned": True,
        "_quality_tier": tier,
    }
    if weapon_tags:
        item["weapon_tags"] = weapon_tags
    if armor_tags:
        item["armor_tags"] = armor_tags

    return item


# ── Commission state helpers ───────────────────────────────────────────────────

def find_active_commission(player: "Player", commission_id: str) -> dict | None:
    """Return the player's active commission record matching commission_id, or None."""
    for c in player.commissions:
        if c.get("commission_id") == commission_id:
            return c
    return None


def find_ready_commission(player: "Player", npc_id: str) -> dict | None:
    """Return the first ready commission for a given NPC, or None."""
    for c in player.commissions:
        if c.get("npc_id") == npc_id and c.get("status") == "ready":
            return c
    return None


def has_pending_commission(player: "Player", npc_id: str) -> bool:
    """True if the player has any non-ready commission with this NPC."""
    return any(
        c.get("npc_id") == npc_id and c.get("status") != "ready"
        for c in player.commissions
    )


def start_commission(player: "Player", commission_def: dict) -> dict:
    """
    Create and register a new commission record on the player.
    Returns the new commission record.
    """
    rec = {
        "commission_id":   commission_def["id"],
        "npc_id":          commission_def["npc_id"],
        "zone_id":         commission_def.get("_zone", ""),
        "turns_remaining": int(commission_def.get("turns_required", 30)),
        "materials_given": [],
        "materials_needed": list(commission_def.get("materials", [])),
        "status":          "waiting_materials",
        "label":           commission_def.get("label", "item"),
        "slot":            commission_def.get("slot", "weapon"),
        "xp_reward":       int(commission_def.get("xp_reward", 0)),
        "commission_def":  commission_def,   # stored for quality roll on collection
    }
    player.commissions.append(rec)
    return rec


def add_material(player: "Player", commission_rec: dict, item_id: str) -> bool:
    """
    Record that a material has been given.
    Returns True if the commission is now fully supplied and transitions to in_progress.
    """
    commission_rec["materials_given"].append(item_id)

    needed  = list(commission_rec["materials_needed"])
    given   = list(commission_rec["materials_given"])

    # Check if all needed materials are now supplied
    for mat in needed:
        if mat in given:
            given.remove(mat)
        else:
            return False  # still missing at least one

    # All materials supplied
    commission_rec["status"] = "in_progress"
    return True


def tick_commissions(player: "Player", turns: int = 1) -> list[str]:
    """
    Advance all in-progress commissions by `turns`.
    Returns a list of NPC names whose commissions just became ready.
    """
    just_ready: list[str] = []
    for rec in player.commissions:
        if rec.get("status") != "in_progress":
            continue
        rec["turns_remaining"] = max(0, rec["turns_remaining"] - turns)
        if rec["turns_remaining"] == 0:
            rec["status"] = "ready"
            just_ready.append(rec.get("label", "item"))
    return just_ready


def collect_commission(player: "Player", commission_rec: dict) -> dict:
    """
    Roll quality, build the finished item, remove the commission record,
    and return the finished item dict (not yet added to inventory).
    """
    commission_def = commission_rec.get("commission_def", {})
    quality        = roll_quality(commission_def)
    item           = build_item(commission_def, quality)
    player.commissions.remove(commission_rec)
    return item, quality


def materials_still_needed(commission_rec: dict) -> list[str]:
    """Return the list of material IDs still to be deposited."""
    needed = list(commission_rec["materials_needed"])
    given  = list(commission_rec["materials_given"])
    for mat in given:
        if mat in needed:
            needed.remove(mat)
    return needed


def reload_cache() -> None:
    """Force-reload all commission definitions (for testing / hot-reload)."""
    global _COMMISSION_CACHE
    _COMMISSION_CACHE = None




