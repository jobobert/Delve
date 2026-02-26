"""
engine/prestige.py — Prestige / Reputation system.

Prestige is a single integer on the player (-999 … +999) that reflects the
narrative weight of their choices.  It is NOT a skill — it doesn't grow on use
and cannot be trained.  It moves only through story events.

  Positive prestige → "hero" arc
    · Friendly / helpful NPCs warm up faster
    · Merchants offer small discounts above +25
    · Some quests only open at +20 or higher
    · Hostile NPCs may yield/flee at high prestige

  Negative prestige → "outlaw / feared" arc
    · Cowardly NPCs panic (extra first-strike window for player)
    · Merchants add a surcharge below -25
    · Criminal / villainous NPCs may offer exclusive quests
    · Guards become pre-hostile at -50 or lower

Typical event deltas
    +1  small good deed (helping a stranger)
    +2  completing a community quest
    +5  major heroic act (saving a life, defeating a great threat)
   -1   petty theft or deliberate harm
   -3   attacking a non-hostile NPC
   -5   killing an innocent
   -10  atrocity / catastrophic betrayal

TOML script ops:
    { op = "prestige", delta = 3 }
    { op = "if_prestige", min = 20, then = [...] }
    { op = "if_prestige", max = -10, then = [...] }
"""

from __future__ import annotations

TIERS: list[tuple[int, str]] = [
    (200,  "Legend"),
    (100,  "Champion"),
    (50,   "Hero"),
    (20,   "Honoured"),
    (5,    "Respected"),
    (-4,   "Neutral"),
    (-19,  "Suspicious"),
    (-49,  "Wanted"),
    (-99,  "Villain"),
    (-999, "Outlaw"),
]


def clamp(val: int, lo: int = -999, hi: int = 999) -> int:
    """Clamp prestige to its valid range."""
    return max(lo, min(hi, val))


def tier_name(prestige: int) -> str:
    for threshold, name in TIERS:
        if prestige >= threshold:
            return name
    return "Outlaw"


def npc_attitude(prestige: int, npc: dict) -> str:
    """Returns one of: friendly / neutral / wary / hostile"""
    tags = npc.get("tags", [])
    if "prestige_neutral" in tags:
        return "neutral"
    if "prestige_wary" in tags:
        if prestige <= -20:
            return "friendly"
        if prestige >= 30:
            return "wary"
        return "neutral"
    # Default: NPCs appreciate good reputation
    if prestige >= 50:
        return "friendly"
    if prestige <= -50:
        return "hostile"
    if prestige <= -20:
        return "wary"
    return "neutral"


def shop_modifier(prestige: int) -> float:
    """Price multiplier for merchants."""
    if prestige >= 25:
        return 0.90
    if prestige >= 0:
        return 1.00
    if prestige >= -25:
        return 1.10
    return 1.20


def should_flee(prestige: int, npc: dict) -> bool:
    if "coward" not in npc.get("tags", []):
        return False
    return prestige >= 60


def apply_delta(player, delta: int, reason: str = "") -> int:
    before = player.prestige
    player.prestige = max(-999, min(999, player.prestige + delta))
    return player.prestige


def meets_gate(player, min_val: int | None = None,
               max_val: int | None = None) -> bool:
    p = player.prestige
    if min_val is not None and p < min_val:
        return False
    if max_val is not None and p > max_val:
        return False
    return True


def prestige_line(player) -> str:
    """One-line prestige summary for the character sheet."""
    p    = player.prestige
    tier = tier_name(p)
    bar_len  = 20
    norm     = (p + 999) / 1998          # 0.0 … 1.0
    filled   = int(bar_len * norm)
    bar      = "█" * filled + "░" * (bar_len - filled)
    sign     = f"+{p}" if p >= 0 else str(p)
    return f"  Prestige [{bar}] {sign:>5}  ({tier})"


def hostile_on_sight(npc: dict, player) -> bool:
    """Return True if this NPC should immediately attack the player on entry."""
    tags   = npc.get("tags", [])
    p      = player.prestige

    # Explicitly prestige-neutral NPCs are never auto-hostile via prestige
    if "prestige_neutral" in tags:
        return False

    # Guards go hostile if prestige is deeply negative
    if "guard" in tags and p <= -50:
        return True

    # NPCs with coward tag flee toward high prestige, but aren't hostile
    # 'prestige_hostile' tag marks NPCs that react to negative reputation
    if "prestige_hostile" in tags and p <= -30:
        return True

    # Criminals and outlaws hate high-prestige heroes
    if "criminal" in tags and p >= 60:
        return True

    return False


