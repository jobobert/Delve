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
    Layout: data/<world_id>/<zone>/styles/*.toml
    World and zone folders are processed alphabetically.
    """
    global _STYLES
    _STYLES = {}
    for world_dir in sorted(DATA_DIR.iterdir()):
        if not world_dir.is_dir() or world_dir.name in _SKIP_DIRS:
            continue
        for zone_folder in sorted(world_dir.iterdir()):
            if not zone_folder.is_dir():
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


def check_passive(passive: dict, prof: float) -> bool:
    """
    Roll whether a passive fires this round.

    Reads `chance` and `chance_scaling` from the passive dict:
      chance         — base probability (0.0–1.0); default 0.15
      chance_scaling — additional probability per 100 prof; default 0.0

    Returns True if the passive fires.
    """
    chance  = float(passive.get("chance", 0.15))
    scaling = float(passive.get("chance_scaling", 0.0))
    return random.random() < (chance + scaling * prof / 100)


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




