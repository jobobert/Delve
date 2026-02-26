"""
styles.py — Fighting style system for Delve.

Proficiency progression
───────────────────────
  gain = BASE_GAIN / difficulty * (1 - prof/100)

  difficulty (from styles.toml):
    1.0 = Brawling, Evasion          (beginner-friendly)
    1.5 = Swordplay, Iron Root       (intermediate)
    2.0 = Serpent Strike, Whirlwind  (advanced, slow mastery)

Gear affinity
─────────────
  Styles define preferred_weapon_tags / preferred_armor_tags.
  Items define weapon_tags / armor_tags.
  Matching gear gives a scaling attack/defense multiplier bonus.

NPC styles
──────────
  NPCs carry style="swordplay" and style_prof=40 in their dict.
  check_passive() and matchup() work identically for both sides.
  NPCs do not gain proficiency — their prof is fixed at spawn.
"""

from __future__ import annotations
import random
from pathlib import Path
from engine.toml_io import load as toml_load

DATA_DIR      = Path(__file__).parent.parent / "data"
BASE_GAIN     = 12.0
TRAINING_RATE = 0.4
MAX_PROF      = 100.0

_SKIP_DIRS    = {"zone_state", "players"}
_STYLES: dict[str, dict] = {}


def reload() -> None:
    """
    Scan all zone folders for styles/ subdirectories and load every
    *.toml file found. First definition of a style id wins.
    Zone folders are processed alphabetically.
    """
    global _STYLES
    _STYLES = {}
    for zone_folder in sorted(DATA_DIR.iterdir()):
        if not zone_folder.is_dir() or zone_folder.name in _SKIP_DIRS:
            continue
        styles_dir = zone_folder / "styles"
        if not styles_dir.exists():
            continue
        for path in sorted(styles_dir.glob("*.toml")):
            try:
                data = toml_load(path)
            except Exception:
                continue
            for style in data.get("style", []):
                sid = style.get("id", "")
                if sid and sid not in _STYLES:
                    _STYLES[sid] = style


def get_all() -> dict[str, dict]:
    if not _STYLES:
        reload()
    return _STYLES


def get(style_id: str) -> dict | None:
    return get_all().get(style_id)


# ── Matchup ───────────────────────────────────────────────────────────────────

def matchup(style: dict, target: dict) -> tuple[float, str]:
    """Damage multiplier for style vs a target (NPC or player tag-dict)."""
    target_tags = set(target.get("tags", []))
    strong_hits = target_tags & set(style.get("strong_vs", []))
    weak_hits   = target_tags & set(style.get("weak_vs",   []))
    if strong_hits:
        return float(style.get("strong_multiplier", 1.0)), \
               f"effective vs {', '.join(sorted(strong_hits))}"
    if weak_hits:
        return float(style.get("weak_multiplier", 1.0)), \
               f"poor vs {', '.join(sorted(weak_hits))}"
    return 1.0, ""


# ── Gear affinity ─────────────────────────────────────────────────────────────

def gear_bonus(style: dict, weapon: dict | None, armor: dict | None,
               prof: float) -> tuple[float, float]:
    """
    Return (attack_mult, defense_mult) from gear matching style affinities.
    Both 1.0 when no match. Bonus scales linearly with proficiency.
    """
    pref_wpn = set(style.get("preferred_weapon_tags", []))
    pref_arm = set(style.get("preferred_armor_tags",  []))
    w_bonus  = float(style.get("weapon_bonus", 0.0))
    a_bonus  = float(style.get("armor_bonus",  0.0))
    scale    = prof / MAX_PROF

    atk_mult = 1.0
    if weapon and pref_wpn and (set(weapon.get("weapon_tags", [])) & pref_wpn):
        atk_mult = 1.0 + w_bonus * scale

    def_mult = 1.0
    if armor and pref_arm and (set(armor.get("armor_tags", [])) & pref_arm):
        def_mult = 1.0 + a_bonus * scale

    return atk_mult, def_mult


# ── Proficiency ───────────────────────────────────────────────────────────────

def proficiency_gain(style: dict, npc: dict, current_prof: float,
                     is_training: bool = False) -> float:
    npc_tags       = set(npc.get("tags", []))
    all_style_tags = set(style.get("strong_vs", [])) | set(style.get("weak_vs", []))
    if not (npc_tags & all_style_tags):
        return 0.0
    difficulty = float(style.get("difficulty", 1.0))
    gain = (BASE_GAIN / difficulty) * (1.0 - current_prof / MAX_PROF)
    if is_training:
        gain *= TRAINING_RATE
    return max(0.0, gain)


def apply_gain(player_prof: dict, style_id: str, gain: float) -> float:
    current = player_prof.get(style_id, 0.0)
    new_val = min(MAX_PROF, current + gain)
    player_prof[style_id] = new_val
    return new_val


# ── Passive management ────────────────────────────────────────────────────────

def unlocked_passives(style: dict, proficiency: float) -> list[str]:
    return [p["ability"] for p in style.get("passives", [])
            if proficiency >= p.get("threshold", 999)]


def newly_unlocked(style: dict, old_prof: float, new_prof: float) -> list[str]:
    return list(set(unlocked_passives(style, new_prof)) -
                set(unlocked_passives(style, old_prof)))


# ── Passive functions  (used by BOTH player AND NPC) ─────────────────────────
# Signature: (prof: float) -> (triggered: bool, raw_fragment: str)
# Caller formats the fragment into a proper sentence.

def passive_stun(prof: float) -> tuple[bool, str]:
    if random.random() < 0.08 + (prof/100)*0.12:
        return True, "staggers — can't counter!"
    return False, ""

def passive_haymaker(prof: float) -> tuple[bool, str]:
    if random.random() < 0.10:
        return True, "winds up for a haymaker!"
    return False, ""

def passive_parry(prof: float) -> tuple[bool, str]:
    if random.random() < 0.12 + (prof/100)*0.15:
        return True, "parries cleanly."
    return False, ""

def passive_riposte(prof: float) -> tuple[bool, str]:
    if random.random() < 0.40:
        return True, "ripostes!"
    return False, ""

def passive_knockback(prof: float) -> tuple[bool, str]:
    if random.random() < 0.15:
        return True, "sends the target stumbling back!"
    return False, ""

def passive_iron_skin(prof: float) -> tuple[bool, str]:
    return True, f"+{int(2+(prof/100)*4)} iron skin"  # always-active; combat reads directly

def passive_bleed(prof: float) -> tuple[bool, str]:
    if random.random() < 0.25:
        return True, "finds a gap — the wound will bleed."
    return False, ""

def passive_vital_strike(prof: float) -> tuple[bool, str]:
    if random.random() < 0.15:
        return True, "strikes a vital point!"
    return False, ""

def passive_cleave(prof: float) -> tuple[bool, str]:
    return True, ""

def passive_cyclone(prof: float) -> tuple[bool, str]:
    if random.random() < 0.10:
        return True, "spins into a cyclone of strikes!"
    return False, ""

def passive_dodge(prof: float) -> tuple[bool, str]:
    if random.random() < 0.15 + (prof/100)*0.20:
        return True, "sidesteps the attack!"
    return False, ""

def passive_counter(prof: float) -> tuple[bool, str]:
    if random.random() < 0.50:
        return True, "counters as the attacker overextends!"
    return False, ""

def passive_redirect(prof: float) -> tuple[bool, str]:
    """Flowing Water: use the enemy's own momentum against them.
    Chance scales with proficiency. Against fast enemies the bonus is higher.
    Combat reads the 'redirect' passive and applies extra damage when it fires."""
    if random.random() < 0.12 + (prof/100)*0.18:
        return True, "redirects the attack — using their force against them!"
    return False, ""

def passive_stillness(prof: float) -> tuple[bool, str]:
    """Flowing Water: inner stillness adds a defense bonus. Always-active.
    Combat reads this directly like iron_skin."""
    return True, f"+{int(1+(prof/100)*5)} stillness"

def passive_absorb(prof: float) -> tuple[bool, str]:
    """Flowing Water: on a successful redirect, partially heal from the impact.
    Only fires when redirect also fires (combat chains them)."""
    if random.random() < 0.30 + (prof/100)*0.20:
        return True, "absorbs the impact — recovering some HP!"
    return False, ""


PASSIVE_FNS: dict[str, callable] = {
    "stun":         passive_stun,
    "haymaker":     passive_haymaker,
    "parry":        passive_parry,
    "riposte":      passive_riposte,
    "knockback":    passive_knockback,
    "iron_skin":    passive_iron_skin,
    "bleed":        passive_bleed,
    "vital_strike": passive_vital_strike,
    "cleave":       passive_cleave,
    "cyclone":      passive_cyclone,
    "dodge":        passive_dodge,
    "counter":      passive_counter,
    "redirect":     passive_redirect,
    "stillness":    passive_stillness,
    "absorb":       passive_absorb,
}


def check_passive(ability_id: str, prof: float) -> tuple[bool, str]:
    fn = PASSIVE_FNS.get(ability_id)
    return fn(prof) if fn else (False, "")


# ── Style learning ────────────────────────────────────────────────────────────

def can_learn(style: dict, player_level: int,
              teacher_npc_id: str | None) -> tuple[bool, str]:
    req_level   = style.get("learned_at", 0)
    req_teacher = style.get("learned_from", "")
    if player_level < req_level:
        return False, f"You must be level {req_level} to learn {style['name']}."
    if req_teacher and teacher_npc_id != req_teacher:
        return False, f"{style['name']} must be taught by a specific trainer."
    return True, ""


