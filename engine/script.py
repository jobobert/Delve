"""
script.py — Lightweight script interpreter for Delve.  (46 ops)

Scripts are arrays of operation tables in TOML data files.  They appear in:
  • NPC dialogue nodes and responses
  • NPC kill_script arrays
  • NPC give_accepts handlers
  • Item on_get / on_drop arrays
  • Room on_enter / on_exit / on_sleep / on_wake arrays

ScriptRunner evaluates a script against a GameContext (player, world, bus,
quests).  Scripts run synchronously and in order.  A `fail` op raises
_ScriptAbort, which is caught by run() — cleanly stopping execution without
raising an exception to callers.  Branching is nestable to any depth.

GameContext
───────────
  ctx.player  — Player instance (mutable)
  ctx.world   — World  instance (rooms, items, NPCs)
  ctx.bus     — EventBus (emit messages to the frontend)
  ctx.quests  — QuestTracker (start/advance/complete quests)

Operation reference  (54 ops)
──────────────────────────────
Output:
  { op = "say",     text = "..." }                  — DIALOGUE-tagged text
  { op = "message", text = "...", tag = "system" }  — any Tag name

Player state:
  { op = "set_flag",          flag = "name" }
  { op = "clear_flag",        flag = "name" }
  { op = "give_gold",         amount = N }
  { op = "take_gold",         amount = N }          — checks balance; errors if short
  { op = "give_xp",           amount = N }
  { op = "give_xp",           amount = N, silent = true }
  { op = "heal",              amount = N }          — caps at max_hp
  { op = "set_hp",            amount = N }
  { op = "damage",            amount = N }          — optional message = "...", silent = true

Inventory:
  { op = "give_item",         item_id = "..." }     — deepcopy template → player
  { op = "take_item",         item_id = "..." }     — remove first match; un-equips
  { op = "spawn_item",        item_id = "..." }     — place item in current room
  { op = "spawn_item_random", items = ["id1", "id2"] } — place a randomly chosen item in current room
  { op = "spawn_npc",         npc_id = "...", room_id = "current" }  — spawn NPC in room

Quests:
  { op = "advance_quest",     quest_id = "...", step = N }
  { op = "complete_quest",    quest_id = "..." }

Styles:
  { op = "teach_style",       style_id = "..." }

World:
  { op = "unlock_exit",  room_id = "...", direction = "..." }
  { op = "lock_exit",    room_id = "...", direction = "..." }

Companions:
  { op = "give_companion",    companion_id = "..." }
  { op = "dismiss_companion", message = "..." }

Skills:
  { op = "skill_check", skill = "mining"|"athletics"|…,
    dc = N, on_pass = [...], on_fail = [...], silent = false, grow = true }
  { op = "if_skill",    skill = "...", min = N, then = [...], else = [...] }
  { op = "skill_grow",  skill = "...", amount = N }

Status effects:
  { op = "apply_status",  effect = "poisoned"|"blinded"|"weakened"|"slowed"|"protected",
    duration = N }                                  — N turns; -1 = permanent
  { op = "clear_status",  effect = "..." }
  { op = "if_status",     effect = "...", then = [...], else = [...] }

Prestige:
  { op = "prestige",          amount = N, reason = "..." }
  { op = "add_affinity",      tag = "..." }
  { op = "remove_affinity",   tag = "..." }
  { op = "if_prestige",       min = N, max = N, then = [...], else = [...] }
  { op = "if_affinity",       tag = "...",       then = [...], else = [...] }

Bank:
  { op = "bank_expand",       tier = N }            — expand bank slots if tier > current

Conditionals (all support then = [...] / else = [...]):
  { op = "if" }          or  { op = "if_flag" }     — flag in player.flags
  { op = "if_not_flag" }                            — flag NOT in player.flags
  { op = "if_item",           item_id = "..." }
  { op = "if_quest",          quest_id = "...", step = N }
  { op = "if_quest_active",   quest_id = "..." }           — quest started, not yet complete
  { op = "if_quest_complete", quest_id = "..." }

Flow control:
  { op = "fail" }                                   — abort entire script run cleanly
  { op = "require_tag",  tag = "...",
    fail_message = "..." }                           — abort if player lacks tagged item

Teleport / world movement:
  { op = "teleport_player", room_id = "...", message = "optional" }
  { op = "move_npc",  npc_id = "...", to_room = "..." }
  { op = "move_item", item_id = "...", to_room = "...", from_room = "current_room_id" }

Combat round (round_script on NPCs only):
  { op = "if_combat_round", min = N, then = [...], else = [...] }
  { op = "if_npc_hp",       max = N, then = [...], else = [...] }
  { op = "end_combat" }                             — stop fight; NPC survives at 1 HP

Journal:
  { op = "journal_entry", title = "...", text = "..." }

World-defined player attributes (defined in config.toml [[player_attrs]]):
  { op = "set_attr",    name = "attr_id", value = N }
  { op = "adjust_attr", name = "attr_id", amount = N }    — clamps to attr min/max
  { op = "if_attr",     name = "attr_id", min = N, max = N, then = [...], else = [...] }

Light mechanic:
  { op = "set_room_light", room_id = "...", value = N }   — set a room's light (0-10)
  { op = "adjust_light",   amount = N }                   — adjust current room light (clamps 0-10)
  { op = "if_light",       max = N, then = [...], else = [...] }  — true if effective light <= max
  { op = "set_vision",     amount = N }                   — set player.vision_threshold
  { op = "adjust_vision",  amount = N }                   — adjust vision_threshold (darkvision etc.)

Standalone script files:
  { op = "run_script_file", path = "scripts/event.toml" } — run ops from a world-relative TOML file

World processes (see engine/processes.py):
  { op = "process_start", process_id = "..." }   — activate (or resume) a process
  { op = "process_stop",  process_id = "..." }   — deactivate and reset tick counters
  { op = "process_pause", process_id = "..." }   — suspend without resetting counters

Notes:
  - Unknown ops are silently ignored (forward-compatibility).
  - fail and require_tag raise _ScriptAbort, caught by run() — no traceback.
  - `if` is an alias for `if_flag`; both are valid.
"""

from __future__ import annotations
import copy
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from engine.events import Event
from engine.msg import Msg, Tag

if TYPE_CHECKING:
    from engine.player import Player
    from engine.world import World
    from engine.events import EventBus
    from engine.quests import QuestTracker


@dataclass
class GameContext:
    player: "Player"
    world:  "World"
    bus:    "EventBus"
    quests: "QuestTracker"
    round:  int = 0              # current combat round (0 = outside combat)
    npc:    "dict | None" = None # NPC being fought, set by CombatSession
    # Mutable state dict shared between CombatSession and combat-only script ops.
    # Set by _run_passives(); None outside of passive execution.
    combat_ctx: "dict | None" = None
    # ProcessManager instance; None when running outside CommandProcessor
    # (e.g. tools, validators).  Script ops silently skip if None.
    processes: "object | None" = None


def eval_exit_condition(cond, ctx: "GameContext") -> bool:
    """Evaluate a show_if condition against the current player state.

    Returns True (exit is visible/usable) if the condition passes.
    Unknown op values default to True so future world data doesn't break
    older engine versions.

    Accepts a dict or a string shorthand:
      "has_flag:flag_name"   — player has the flag set
      "not_flag:flag_name"   — player does NOT have the flag
      "flag_name"            — bare flag name, treated as has_flag

    Supported dict ops:
      has_flag   flag (str)              — player has the flag set
      not_flag   flag (str)              — player does NOT have the flag
      min_level  level (int)             — player.level >= level
      has_item   item_id (str)           — item in inventory or equipped
      min_skill  skill (str), value (N)  — player.skills[skill] >= value
    """
    if isinstance(cond, str):
        if ":" in cond:
            op_part, _, flag_part = cond.partition(":")
            cond = {"op": op_part.strip(), "flag": flag_part.strip()}
        else:
            cond = {"op": "has_flag", "flag": cond.strip()}
    op = cond.get("op", "")
    p  = ctx.player
    if op == "has_flag":
        return cond.get("flag", "") in p.flags
    if op == "not_flag":
        return cond.get("flag", "") not in p.flags
    if op == "min_level":
        return p.level >= int(cond.get("level", 1))
    if op == "has_item":
        item_id     = cond.get("item_id", "")
        in_inv      = any(i.get("id") == item_id for i in p.inventory)
        in_equipped = any(i.get("id") == item_id for i in p.equipped.values() if i)
        return in_inv or in_equipped
    if op == "min_skill":
        skill = cond.get("skill", "")
        return p.skills.get(skill, 0.0) >= float(cond.get("value", 0))
    return True   # unknown op — don't hide the exit


class _ScriptAbort(Exception):
    """Raised by the 'fail' op to abort the current script run.

    Using an exception rather than a return value means deeply-nested branching
    (conditionals inside conditionals) can abort immediately without threading
    a flag through every level of _exec recursion.
    """


class ScriptRunner:
    def __init__(self, ctx: GameContext):
        self.ctx = ctx

    def run(self, ops: list[dict]) -> None:
        try:
            for op in ops:
                self._exec(op)
        except _ScriptAbort:
            pass  # 'fail' op fired — stop silently

    def _run_branch(self, condition: bool, op: dict) -> None:
        """Execute the 'then' or 'else' sub-list of a conditional op."""
        for sub in op.get("then" if condition else "else", []):
            self._exec(sub)

    # TODO: _exec() is a 45-branch elif chain (~400 lines). Future refactor
    #       candidate: dispatch table {op_name: handler_method} to make each
    #       op independently testable and easier to extend.
    def _exec(self, op: dict) -> None:
        name = op.get("op", "")
        p    = self.ctx.player
        w    = self.ctx.world
        bus  = self.ctx.bus
        q    = self.ctx.quests
        ctx  = self.ctx

        def emit(tag, text):
            bus.emit(Event.OUTPUT, Msg(tag, text))

        # ── Output ────────────────────────────────────────────────────────────
        if name == "say":
            emit(Tag.DIALOGUE, f'  "{op.get("text","")}"  ')

        elif name == "message":
            tag_str = op.get("tag", "system")
            tag = getattr(Tag, tag_str.upper(), Tag.SYSTEM)
            emit(tag, op.get("text", ""))

        # ── Player flags ──────────────────────────────────────────────────────
        elif name == "set_flag":
            p.flags.add(op["flag"])

        elif name == "clear_flag":
            p.flags.discard(op.get("flag", ""))

        # ── Gold / XP / HP ────────────────────────────────────────────────────
        elif name == "give_gold":
            import engine.world_config as _wc
            amount = int(op.get("amount", 0))
            p.gold += amount
            if amount:
                cur = _wc.CURRENCY_NAME
                emit(Tag.REWARD_GOLD, f"  You receive {amount} {cur}. ({p.gold} {cur} total)")

        elif name == "take_gold":
            # Deducts gold; checks balance first.
            import engine.world_config as _wc
            amount = int(op.get("amount", 0))
            cur = _wc.CURRENCY_NAME
            if p.gold >= amount:
                p.gold -= amount
                if not op.get("silent"):
                    emit(Tag.REWARD_GOLD, f"  You pay {amount} {cur}.")
            else:
                emit(Tag.ERROR,
                     f"  You don't have enough {cur}! (need {amount}, have {p.gold})")

        elif name == "give_xp":
            amount = int(op.get("amount", 0))
            leveled = p.gain_xp(amount)
            if not op.get("silent"):
                emit(Tag.REWARD_XP, f"  You gain {amount} XP.")
            if leveled:
                emit(Tag.REWARD_XP,
                     f"✦ Level up! You are now level {p.level}. HP fully restored.")

        elif name == "heal":
            amount = int(op.get("amount", 0))
            p.hp = min(p.max_hp, p.hp + amount)
            emit(Tag.SYSTEM, f"  You feel better. ({p.hp}/{p.max_hp} HP)")

        elif name == "set_hp":
            p.hp = min(p.max_hp, max(0, int(op.get("amount", p.hp))))

        elif name == "damage":
            # { op = "damage", amount = N }  optional: message = "...", silent = true
            amount = int(op.get("amount", 0))
            p.hp   = max(0, p.hp - amount)
            msg    = op.get("message", "")
            if msg:
                emit(Tag.COMBAT_RECV, msg)
            if not op.get("silent"):
                emit(Tag.COMBAT_RECV, f"  You take {amount} damage. ({p.hp}/{p.max_hp} HP)")

        # ── Inventory ─────────────────────────────────────────────────────────
        elif name == "give_item":
            item_id = op.get("item_id", "")
            tmpl    = w.items.get(item_id)
            if tmpl:
                p.add_item(copy.deepcopy(tmpl))
                emit(Tag.ITEM, f"  You receive: {tmpl['name']}")

        elif name == "take_item":
            item_id = op.get("item_id", "")
            for item in list(p.inventory):
                if item.get("id") == item_id:
                    p.remove_item(item)
                    for slot, eq in p.equipped.items():
                        if eq and eq.get("id") == item_id:
                            p.equipped[slot] = None
                    break

        elif name == "spawn_item":
            # Spawn item into current room; falls back to player inventory.
            item_id = op.get("item_id", "")
            item    = w.spawn_item(item_id) if w else None
            if item:
                room = w.get_room(p.room_id)
                if room is not None and isinstance(room.get("items"), list):
                    room["items"].append(item)
                    emit(Tag.ITEM, f"  A {item.get('name', item_id)} falls to the ground.")
                else:
                    p.inventory.append(item)

        elif name == "spawn_item_random":
            # Pick one item at random from a list, then spawn it in the current room.
            items = op.get("items", [])
            if items:
                item_id = random.choice(items)
                item    = w.spawn_item(item_id) if w else None
                if item:
                    room = w.get_room(p.room_id)
                    if room is not None and isinstance(room.get("items"), list):
                        room["items"].append(item)
                        emit(Tag.ITEM, f"  A {item.get('name', item_id)} falls to the ground.")
                    else:
                        p.inventory.append(item)

        elif name == "spawn_npc":
            # { op = "spawn_npc", npc_id = "...", room_id = "current" }
            # room_id defaults to the player's current room when "current" or omitted.
            npc_id  = op.get("npc_id", "")
            room_id = op.get("room_id", "current")
            if room_id == "current":
                room_id = p.room_id
            if w and npc_id:
                w.spawn_npc_in_room(npc_id, room_id)

        # ── Quests ────────────────────────────────────────────────────────────
        elif name == "advance_quest":
            q_id = op.get("quest_id", "")
            step = int(op.get("step", 1))
            q.advance(q_id, step, ctx=ctx)

        elif name == "complete_quest":
            q_id = op.get("quest_id", "")
            q.complete(q_id, ctx=ctx)

        # ── Styles ────────────────────────────────────────────────────────────
        elif name == "teach_style":
            style_id = op.get("style_id", "")
            from engine import styles as styles_mod
            if style_id and styles_mod.get(style_id):
                if style_id not in p.known_styles:
                    p.known_styles.append(style_id)
                    emit(Tag.REWARD_XP,
                         f"  ✦ You have learned the {styles_mod.get(style_id)['name']} style!")
            else:
                emit(Tag.ERROR, f"  [Script error: style '{style_id}' not found]")

        # ── World ─────────────────────────────────────────────────────────────
        elif name == "unlock_exit":
            room_id   = op.get("room_id", "")
            direction = op.get("direction", "")
            room      = w.get_room(room_id)
            if room:
                exit_val = room.get("exits", {}).get(direction)
                if isinstance(exit_val, dict):
                    exit_val["locked"] = False

        elif name == "lock_exit":
            room_id   = op.get("room_id", "")
            direction = op.get("direction", "")
            room      = w.get_room(room_id)
            if room:
                exit_val = room.get("exits", {}).get(direction)
                if isinstance(exit_val, dict):
                    exit_val["locked"] = True

        # ── Companions ────────────────────────────────────────────────────────
        elif name == "give_companion":
            import engine.companion as companion_mod
            cid  = op.get("companion_id", "")
            comp = companion_mod.create_active(cid)
            if comp:
                p.companion = comp
                cdef     = comp.get("_def", {})
                join_msg = cdef.get("join_message",
                                    f"{comp['name']} joins you as a companion.")
                emit(Tag.SYSTEM, join_msg)
            else:
                emit(Tag.ERROR, f"[Script error: companion '{cid}' not found]")

        elif name == "dismiss_companion":
            if p.companion:
                msg = op.get("message", "")
                if msg:
                    emit(Tag.SYSTEM, msg)
                p.companion = None

        # ── Skills ────────────────────────────────────────────────────────────
        elif name == "skill_grow":
            # { op = "skill_grow", skill = "mining", amount = 1 }
            skill_id = op.get("skill", "")
            amount   = float(op.get("amount", 1))
            if skill_id in p.skills:
                from engine.skills import tier_name
                old_val = p.skills[skill_id]
                p.skills[skill_id] = min(100.0, old_val + amount)
                new_val = p.skills[skill_id]
                if int(new_val) > int(old_val):
                    emit(Tag.REWARD_XP,
                         f"  {skill_id.capitalize()} {int(old_val)} → {int(new_val)}")

        elif name == "skill_check":
            from engine.skills import roll_check, apply_growth, SKILL_NAMES
            skill_id  = op.get("skill", "")
            dc        = int(op.get("dc", 10))
            grow      = op.get("grow", True)
            silent    = op.get("silent", False)
            skill_val = p.skills.get(skill_id, 0.0)
            passed, roll, bonus = roll_check(skill_val, dc)
            if not silent:
                result = "success" if passed else "failure"
                emit(Tag.SYSTEM,
                     f"  [{skill_id.capitalize()} check: {roll + bonus} vs DC {dc} — {result}]")
            branch = op.get("on_pass" if passed else "on_fail", [])
            for sub in branch:
                self._exec(sub)
            if grow and skill_id in p.skills and not op.get("on_pass"):
                new_val, tier_msg = apply_growth(p.skills[skill_id], passed)
                p.skills[skill_id] = new_val
                if tier_msg:
                    sname = SKILL_NAMES.get(skill_id, skill_id.title())
                    emit(Tag.SYSTEM, f"  Your {sname} skill reaches {tier_msg}!")

        elif name == "if_skill":
            # { op = "if_skill", skill = "mining", min = 5, then = [...], else = [...] }
            skill_id  = op.get("skill", "")
            min_level = int(op.get("min", 0))
            skill_val = p.skills.get(skill_id, 0.0)
            passed    = int(skill_val) >= min_level
            self._run_branch(passed, op)

        # ── Status effects ────────────────────────────────────────────────────
        elif name == "apply_status":
            effect   = op.get("effect", "")
            duration = int(op.get("duration", 3))
            if effect:
                p.status_effects[effect] = duration
                import engine.world_config as _wc
                se_def = _wc.get_status_effect(effect)
                msg = se_def["apply_msg"] if se_def else f"You are now {effect}."
                emit(Tag.SYSTEM, msg)

        elif name == "clear_status":
            effect = op.get("effect", "")
            p.status_effects.pop(effect, None)

        elif name == "if_status":
            effect = op.get("effect", "")
            has_it = effect in p.status_effects
            self._run_branch(has_it, op)

        # ── Prestige ──────────────────────────────────────────────────────────
        elif name == "prestige":
            # { op = "prestige", amount = N, reason = "..." }
            import engine.prestige as pr
            amount = int(op.get("amount", 0))
            old_s  = getattr(p, "prestige", 0)
            new_s  = pr.clamp(old_s + amount)
            p.prestige = new_s
            sign    = "+" if amount >= 0 else ""
            reason  = op.get("reason", "")
            reason_str = f" — {reason}" if reason else ""
            old_tier = pr.tier_name(old_s)
            new_tier = pr.tier_name(new_s)
            emit(Tag.SYSTEM,
                 f"  Prestige: {sign}{amount}{reason_str}  ({old_s:+} → {new_s:+})")
            if old_tier != new_tier:
                emit(Tag.REWARD_XP,
                     f"  ✦ Prestige standing changed: {old_tier} → {new_tier}")

        elif name == "add_affinity":
            # { op = "add_affinity", tag = "verdant_hero" }
            tag = op.get("tag", "")
            if tag and tag not in getattr(p, "prestige_affinities", []):
                p.prestige_affinities.append(tag)
                emit(Tag.SYSTEM, f"  You are now known as: {tag.replace('_',' ').title()}")

        elif name == "remove_affinity":
            tag = op.get("tag", "")
            if tag in getattr(p, "prestige_affinities", []):
                p.prestige_affinities.remove(tag)

        elif name == "if_prestige":
            # { op = "if_prestige", min = 20, then = [...], else = [...] }
            import engine.prestige as pr
            score   = getattr(p, "prestige", 0)
            min_req = op.get("min")
            max_req = op.get("max")
            ok      = True
            if min_req is not None and score < int(min_req): ok = False
            if max_req is not None and score > int(max_req): ok = False
            self._run_branch(ok, op)

        elif name == "if_affinity":
            tag    = op.get("tag", "")
            has_it = tag in getattr(p, "prestige_affinities", [])
            self._run_branch(has_it, op)

        # ── Conditionals ──────────────────────────────────────────────────────
        elif name in ("if", "if_flag"):
            # { op = "if"/"if_flag", flag = "...", then = [...], else = [...] }
            flag   = op.get("flag", "")
            passed = flag in p.flags
            self._run_branch(passed, op)

        elif name == "if_not_flag":
            flag   = op.get("flag", "")
            passed = flag not in p.flags
            self._run_branch(passed, op)

        elif name == "if_item":
            item_id = op.get("item_id", "")
            has_it  = any(i.get("id") == item_id for i in p.inventory)
            self._run_branch(has_it, op)

        elif name == "if_quest":
            q_id   = op.get("quest_id", "")
            step   = int(op.get("step", 0))
            passed = q.at_step(q_id, step)
            self._run_branch(passed, op)

        elif name == "if_quest_active":
            q_id   = op.get("quest_id", "")
            passed = q.is_active(q_id)
            self._run_branch(passed, op)

        elif name == "if_quest_complete":
            q_id   = op.get("quest_id", "")
            passed = q.is_complete(q_id)
            self._run_branch(passed, op)

        # ── Flow control ──────────────────────────────────────────────────────
        elif name == "fail":
            # Abort the entire script run immediately.
            raise _ScriptAbort()

        elif name == "require_tag":
            # { op = "require_tag", tag = "pickaxe", fail_message = "..." }
            req_tag = op.get("tag", "")
            has_tag = any(
                req_tag in (item.get("tags", []) if item else [])
                for item in list(p.inventory) + [v for v in p.equipped.values() if v]
            )
            if not has_tag:
                msg = op.get("fail_message", f"You need an item with the '{req_tag}' tag.")
                emit(Tag.ERROR, msg)
                raise _ScriptAbort()

        # ── Bank ──────────────────────────────────────────────────────────────
        elif name == "bank_expand":
            # { op = "bank_expand", tier = 20 }  Only expands, never shrinks.
            tier = int(op.get("tier", 10))
            current = getattr(p, "bank_slots", 10)
            if tier > current:
                p.bank_slots = tier
                emit(Tag.REWARD_GOLD,
                     f"Your bank account has been expanded to {tier} slots.")
            else:
                emit(Tag.SYSTEM,
                     f"Your account already has {current} slots.")

        # ── Teleport / move ───────────────────────────────────────────────────
        elif name == "teleport_player":
            # { op = "teleport_player", room_id = "...", message = "optional" }
            room_id = op.get("room_id", "")
            message = op.get("message", "")
            if message:
                emit(Tag.SYSTEM, message)
            dest = ctx.world.prepare_room(room_id, ctx.player)
            if dest:
                ctx.player.room_id = room_id
                ctx.player.visited_rooms.add(room_id)
                zone = ctx.world.zone_for_room(room_id)
                if zone:
                    ctx.world.evict_distant_zones(zone)
                ctx.bus.emit(Event.LOOK_ROOM)   # CommandProcessor handles look + on_enter
            else:
                emit(Tag.ERROR, f"[teleport_player: room '{room_id}' not found]")

        elif name == "move_npc":
            # { op = "move_npc", npc_id = "...", to_room = "..." }
            # Finds the first live instance of npc_id in any loaded room and moves it.
            npc_id  = op.get("npc_id", "")
            dest_id = op.get("to_room", "")
            found = None
            for zone_rooms in ctx.world._loaded_zones.values():
                for room in zone_rooms.values():
                    for npc in list(room.get("_npcs") or []):
                        if npc.get("id") == npc_id:
                            room["_npcs"].remove(npc)
                            found = npc
                            break
                    if found:
                        break
                if found:
                    break
            if found:
                dest = ctx.world.prepare_room(dest_id, ctx.player)
                if dest:
                    if dest.get("_npcs") is None:
                        dest["_npcs"] = []
                    dest["_npcs"].append(found)

        elif name == "move_item":
            # { op = "move_item", item_id = "...", to_room = "...", from_room = "current" }
            item_id   = op.get("item_id", "")
            from_id   = op.get("from_room", ctx.player.room_id)
            dest_id   = op.get("to_room", "")
            src_room  = ctx.world.prepare_room(from_id, ctx.player)
            dest_room = ctx.world.prepare_room(dest_id, ctx.player)
            if src_room and dest_room:
                items = src_room.get("items", [])
                for item in list(items):
                    if item.get("id") == item_id:
                        items.remove(item)
                        dest_room.setdefault("items", []).append(item)
                        break

        # ── Combat conditionals ────────────────────────────────────────────────
        elif name == "if_combat_round":
            # { op = "if_combat_round", min = N, then = [...], else = [...] }
            passed = ctx.round >= op.get("min", 0)
            self._run_branch(passed, op)

        elif name == "if_npc_hp":
            # { op = "if_npc_hp", max = N, then = [...], else = [...] }
            npc_hp = ctx.npc.get("hp", 0) if ctx.npc else 0
            passed = npc_hp <= op.get("max", 0)
            self._run_branch(passed, op)

        elif name == "end_combat":
            # Signals the active CombatSession to end the fight after this script run.
            # CombatSession clears the flag and closes the round.
            p.flags.add("_end_combat")

        # ── Cutscene pause ────────────────────────────────────────────────────
        elif name == "pause":
            # { op = "pause", seconds = 0.5 }
            # Emits a PAUSE message; the frontend is responsible for sleeping.
            # The engine itself never calls time.sleep() to keep it decoupled.
            seconds = op.get("seconds", 0.5)
            emit(Tag.PAUSE, str(float(seconds)))

        # ── Journal ───────────────────────────────────────────────────────────
        elif name == "journal_entry":
            title = op.get("title", "Entry")
            text  = op.get("text", "")
            ctx.player.journal.append({"title": title, "text": text})
            emit(Tag.JOURNAL, f"[Journal] {title} recorded.")

        # ── Combat-only passive ops ───────────────────────────────────────────
        # These ops are only valid during passive execution (ctx.combat_ctx set).
        # They mutate the shared combat_ctx dict to communicate back to CombatSession.
        # Silently ignored outside of combat (combat_ctx is None).

        elif name == "block_damage":
            # Set current hit damage to 0 and mark the hit as blocked.
            if ctx.combat_ctx is not None:
                ctx.combat_ctx["hit_damage"] = 0
                ctx.combat_ctx["blocked"] = True

        elif name == "multiply_damage":
            # Scale current hit damage by multiplier.
            # { op = "multiply_damage", multiplier = 2.0 }
            if ctx.combat_ctx is not None:
                mult = float(op.get("multiplier", 1.0))
                ctx.combat_ctx["hit_damage"] = int(ctx.combat_ctx["hit_damage"] * mult)

        elif name == "counter_damage":
            # Deal back-damage to the current opponent (NPC or player depending on side).
            # multiplier is applied to the attacker's base attack stat.
            # { op = "counter_damage", multiplier = 0.6 }
            if ctx.combat_ctx is not None:
                mult = float(op.get("multiplier", 0.5))
                base_atk = int(ctx.combat_ctx.get("attacker_atk", 0))
                cdmg = max(1, int(base_atk * mult))
                ctx.combat_ctx["counter_damage"] = (
                    ctx.combat_ctx.get("counter_damage", 0) + cdmg
                )

        elif name == "reduce_damage":
            # Reduce current hit damage by a percentage.
            # { op = "reduce_damage", percent = 30 }
            if ctx.combat_ctx is not None:
                pct = float(op.get("percent", 0)) / 100.0
                cur = ctx.combat_ctx["hit_damage"]
                ctx.combat_ctx["hit_damage"] = max(0, int(cur * (1.0 - pct)))

        elif name == "skip_npc_attack":
            # Signal that the NPC should skip its attack this round (stun/knockback).
            if ctx.combat_ctx is not None:
                ctx.combat_ctx["skip_npc_turn"] = True

        elif name == "apply_combat_bleed":
            # Mark the defender as bleeding (1-3 damage per round going forward).
            if ctx.combat_ctx is not None:
                ctx.combat_ctx["apply_bleed"] = True

        elif name == "heal_self":
            # Heal the passive user for a fraction of counter damage dealt this hit.
            # { op = "heal_self", multiplier = 0.33 }
            if ctx.combat_ctx is not None:
                mult = float(op.get("multiplier", 0.33))
                cdmg = ctx.combat_ctx.get("counter_damage", 0)
                ctx.combat_ctx["counter_heal"] = max(1, int(cdmg * mult))

        # ── World-defined player attributes ───────────────────────────────────
        elif name == "set_attr":
            # { op = "set_attr", name = "attr_id", value = N }
            import engine.world_config as _wc
            attr_name = op.get("name", "")
            value     = int(op.get("value", 0))
            attr_def  = next((a for a in _wc.PLAYER_ATTRS if a.get("id") == attr_name), None)
            if attr_def:
                lo = int(attr_def.get("min", 0))
                hi = int(attr_def.get("max", 100))
                p.world_attrs[attr_name] = max(lo, min(hi, value))

        elif name == "adjust_attr":
            # { op = "adjust_attr", name = "attr_id", amount = N }
            import engine.world_config as _wc
            attr_name = op.get("name", "")
            amount    = int(op.get("amount", 0))
            attr_def  = next((a for a in _wc.PLAYER_ATTRS if a.get("id") == attr_name), None)
            if attr_def:
                lo  = int(attr_def.get("min", 0))
                hi  = int(attr_def.get("max", 100))
                cur = p.world_attrs.get(attr_name, int(attr_def.get("default", 0)))
                p.world_attrs[attr_name] = max(lo, min(hi, cur + amount))

        elif name == "if_attr":
            # { op = "if_attr", name = "attr_id", min = N, max = N, then = [...], else = [...] }
            import engine.world_config as _wc
            attr_name = op.get("name", "")
            attr_def  = next((a for a in _wc.PLAYER_ATTRS if a.get("id") == attr_name), None)
            if attr_def:
                cur     = p.world_attrs.get(attr_name, int(attr_def.get("default", 0)))
                lo      = op.get("min", None)
                hi      = op.get("max", None)
                passed  = True
                if lo is not None and cur < int(lo):
                    passed = False
                if hi is not None and cur > int(hi):
                    passed = False
                self._run_branch(passed, op)

        # ── Light mechanic ────────────────────────────────────────────────────
        elif name == "set_room_light":
            # { op = "set_room_light", room_id = "...", value = N }
            import engine.log as log
            room_id = op.get("room_id", ctx.player.room_id)
            value   = max(0, min(10, int(op.get("value", 10))))
            target  = ctx.world.prepare_room(room_id, ctx.player)
            if target is not None:
                target["light"] = value
                log.debug("light", "set_room_light", room_id=room_id, value=value)

        elif name == "adjust_light":
            # { op = "adjust_light", amount = N }  — affects current room
            import engine.log as log
            room   = ctx.world.prepare_room(ctx.player.room_id, ctx.player)
            amount = int(op.get("amount", 0))
            if room is not None:
                current = int(room.get("light", 10))
                room["light"] = max(0, min(10, current + amount))
                log.debug("light", "adjust_light",
                          room_id=ctx.player.room_id,
                          old=current, new=room["light"])

        elif name == "if_light":
            # { op = "if_light", max = N, then = [...], else = [...] }
            # True when effective light (room + equipped items) <= max.
            from engine.player import effective_light
            room   = ctx.world.prepare_room(ctx.player.room_id, ctx.player)
            eff    = effective_light(ctx.player, room) if room else 10
            passed = eff <= int(op.get("max", 10))
            self._run_branch(passed, op)

        elif name == "set_vision":
            # { op = "set_vision", amount = N }
            ctx.player.vision_threshold = max(0, int(op.get("amount", 3)))

        elif name == "adjust_vision":
            # { op = "adjust_vision", amount = N }
            ctx.player.vision_threshold = max(
                0, ctx.player.vision_threshold + int(op.get("amount", 0))
            )

        # ── Standalone script files ───────────────────────────────────────────
        elif name == "run_script_file":
            # { op = "run_script_file", path = "scripts/event.toml" }
            # Path is relative to the world root folder.
            import engine.log as log
            import engine.world_config as _wc
            from engine.toml_io import load as _toml_load
            rel_path = op.get("path", "")
            if rel_path and _wc._world_path:
                script_path = _wc._world_path / rel_path
                try:
                    sdata = _toml_load(script_path)
                    sub_ops = sdata.get("ops", [])
                    log.debug("script", "run_script_file",
                              path=str(script_path), ops=len(sub_ops))
                    self.run(sub_ops)
                except Exception as e:
                    import engine.log as log
                    log.warn("script", f"run_script_file failed: {e}", path=rel_path)

        # ── World processes ───────────────────────────────────────────────────
        elif name == "process_start":
            # { op = "process_start", process_id = "..." }
            pid = op.get("process_id", "")
            if pid and ctx.processes is not None:
                ctx.processes.start(pid)

        elif name == "process_stop":
            # { op = "process_stop", process_id = "..." }
            pid = op.get("process_id", "")
            if pid and ctx.processes is not None:
                ctx.processes.stop(pid)

        elif name == "process_pause":
            # { op = "process_pause", process_id = "..." }
            pid = op.get("process_id", "")
            if pid and ctx.processes is not None:
                ctx.processes.pause(pid)

        # ── Unknown ops ───────────────────────────────────────────────────────
        # Unknown ops are silently ignored for forward-compatibility.




