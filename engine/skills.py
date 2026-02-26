"""
skills.py — Player skill system for Delve.

Skills are separate from fighting styles. They represent general adventuring
competencies that grow through use and affect skill checks against room events,
dialogue conditions, and scripted encounters.

The six core skills
───────────────────
  stealth    — Moving quietly, hiding, avoiding detection
  survival   — Wilderness navigation, avoiding natural hazards, foraging
  perception — Noticing hidden things, reading situations
  athletics  — Climbing, swimming, feats of strength and endurance
  social     — Persuasion, deception, reading people
  arcana     — Knowledge of magic, identifying enchantments, resisting spells

Skill values
────────────
  Each skill runs 0–100 (matching the style proficiency scale).
  A value of 0 means untrained; 100 is legendary mastery.

  Bonus formula:   bonus = skill_value // 10         (0–10 range)
  A DC check rolls: d20 + bonus  vs  DC

  So an untrained character (skill=0) rolls pure d20 vs DC.
  A master (skill=100) adds +10, which is meaningful but not dominant.

Skill growth
────────────
  Skills improve by use — both successes and failures award a small amount,
  with success awarding slightly more. Growth is faster at low levels and
  slower at high levels (logarithmic slow-down after 50).

  Called from ScriptRunner after every skill_check op.
  Players see growth messages at thresholds: 10, 25, 50, 75, 90, 100.

Thresholds / tier names
───────────────────────
   0–9   Untrained
  10–24  Novice
  25–49  Practiced
  50–74  Skilled
  75–89  Expert
  90–99  Master
    100  Legendary

Usage in TOML scripts
─────────────────────
  { op = "skill_check", skill = "stealth", dc = 12,
    on_pass = [...],   on_fail = [...] }

  op             required  — must be "skill_check"
  skill          required  — one of the six skill names
  dc             required  — difficulty class (integer)
  on_pass        optional  — script ops to run on success
  on_fail        optional  — script ops to run on failure
  silent         optional  — if true, suppress the roll message
  grow           optional  — if false, skill does not improve (e.g. one-time events)

Usage in dialogue conditions
────────────────────────────
  condition = { skill = "social", min = 40 }
  → The response/node only appears if player.skills["social"] >= 40.
"""

from __future__ import annotations
import random

SKILLS: list[str] = ["stealth", "survival", "perception", "athletics", "social", "arcana", "mining"]

SKILL_NAMES: dict[str, str] = {
    "stealth":    "Stealth",
    "survival":   "Survival",
    "perception": "Perception",
    "athletics":  "Athletics",
    "social":     "Social",
    "arcana":     "Arcana",
    "mining":     "Mining",
}

TIERS: list[tuple[int, str]] = [
    (100, "Legendary"),
    (90,  "Master"),
    (75,  "Expert"),
    (50,  "Skilled"),
    (25,  "Practiced"),
    (10,  "Novice"),
    (0,   "Untrained"),
]


def tier_name(value: float) -> str:
    for threshold, name in TIERS:
        if value >= threshold:
            return name
    return "Untrained"


def skill_bonus(value: float) -> int:
    """Convert a 0–100 skill value to a d20 check bonus (0–10)."""
    return int(value) // 10


def roll_check(skill_value: float, dc: int) -> tuple[bool, int, int]:
    """
    Roll a skill check.
    Returns (passed, roll, bonus) where roll is the raw d20 result.
    """
    bonus  = skill_bonus(skill_value)
    roll   = random.randint(1, 20)
    total  = roll + bonus
    return total >= dc, roll, bonus


def _growth_amount(current: float, success: bool) -> float:
    """
    How much the skill improves after a check.
    Growth slows as skill rises (harder to improve when already good).
    Success gives slightly more than failure.
    """
    base = 3.0 if success else 1.5
    # Slow down above 50
    if current >= 75:
        base *= 0.3
    elif current >= 50:
        base *= 0.6
    return base


def _old_tier(value: float) -> str:
    return tier_name(value)


def apply_growth(current: float, success: bool) -> tuple[float, str | None]:
    """
    Apply growth to a skill value. Returns (new_value, tier_up_message | None).
    tier_up_message is non-None only when the player crosses a tier boundary.
    """
    old_tier = tier_name(current)
    gain     = _growth_amount(current, success)
    new_val  = min(100.0, current + gain)
    new_tier = tier_name(new_val)
    if new_tier != old_tier:
        return new_val, new_tier
    return new_val, None


def check_condition(condition: dict, player) -> bool:
    """
    Evaluate a skill condition for dialogue node/response visibility.
    Condition format: { skill = "social", min = 40 }
    """
    if not condition:
        return True
    skill = condition.get("skill")
    if not skill:
        return True
    minimum = int(condition.get("min", 0))
    return player.skills.get(skill, 0.0) >= minimum


def default_skills() -> dict[str, float]:
    return {s: 0.0 for s in SKILLS}




