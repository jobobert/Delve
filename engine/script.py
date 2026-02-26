"""
script.py — Lightweight script interpreter for Delve.  (37 ops)

Scripts are arrays of operation tables in TOML data files.  They appear in:
  • NPC dialogue nodes and responses
  • NPC kill_script arrays
  • NPC give_accepts handlers
  • Item on_get arrays
  • Room on_enter arrays

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

Operation reference  (37 ops)
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
  { op = "if_quest_complete", quest_id = "..." }

Flow control:
  { op = "fail" }                                   — abort entire script run cleanly
  { op = "require_tag",  tag = "...",
    fail_message = "..." }                           — abort if player lacks tagged item

Notes:
  - Unknown ops are silently ignored (forward-compatibility).
  - fail and require_tag raise _ScriptAbort, caught by run() — no traceback.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

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


class _ScriptAbort(Exception):
    """Raised by the 'fail' op to abort the current script run."""


class ScriptRunner:
    def __init__(self, ctx: GameContext):
        self.ctx = ctx

    def run(self, ops: list[dict]) -> None:
        try:
            for op in ops:
                self._exec(op)
        except _ScriptAbort:
            pass  # 'fail' op fired — stop silently

    def _exec(self, op: dict) -> None:
        from engine.events import Event
        from engine.msg import Msg, Tag
        import copy

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
            from engine.msg import Tag as T
            tag_str = op.get("tag", "system")
            tag = getattr(T, tag_str.upper(), T.SYSTEM)
            emit(tag, op.get("text", ""))

        # ── Player flags ──────────────────────────────────────────────────────
        elif name == "set_flag":
            p.flags.add(op["flag"])

        elif name == "clear_flag":
            p.flags.discard(op.get("flag", ""))

        # ── Gold / XP / HP ────────────────────────────────────────────────────
        elif name == "give_gold":
            amount = int(op.get("amount", 0))
            p.gold += amount
            if amount:
                emit(Tag.REWARD_GOLD, f"  You receive {amount} gold. ({p.gold}g total)")

        elif name == "take_gold":
            # Deducts gold; checks balance first.
            amount = int(op.get("amount", 0))
            if p.gold >= amount:
                p.gold -= amount
                if not op.get("silent"):
                    emit(Tag.REWARD_GOLD, f"  You pay {amount} gold.")
            else:
                emit(Tag.ERROR,
                     f"  You don't have enough gold! (need {amount}, have {p.gold})")

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
            import random
            skill_id = op.get("skill", "")
            dc       = int(op.get("dc", 10))
            grow     = op.get("grow", True)
            silent   = op.get("silent", False)
            skill_val = p.skills.get(skill_id, 0.0)
            bonus     = int(skill_val) // 10
            roll      = random.randint(1, 20) + bonus
            passed    = roll >= dc
            if not silent:
                result = "success" if passed else "failure"
                emit(Tag.SYSTEM, f"  [{skill_id.capitalize()} check: {roll} vs DC {dc} — {result}]")
            branch = op.get("on_pass" if passed else "on_fail", [])
            for sub in branch:
                self._exec(sub)
            if grow and not op.get("on_pass"):
                # grow when no on_pass branch (simple check)
                if skill_id in p.skills:
                    p.skills[skill_id] = min(100.0, p.skills[skill_id] + 0.5)

        elif name == "if_skill":
            # { op = "if_skill", skill = "mining", min = 5, then = [...], else = [...] }
            skill_id  = op.get("skill", "")
            min_level = int(op.get("min", 0))
            skill_val = p.skills.get(skill_id, 0.0)
            passed    = int(skill_val) >= min_level
            branch    = op.get("then", []) if passed else op.get("else", [])
            for sub in branch:
                self._exec(sub)

        # ── Status effects ────────────────────────────────────────────────────
        elif name == "apply_status":
            effect   = op.get("effect", "")
            duration = int(op.get("duration", 3))
            if effect:
                p.status_effects[effect] = duration
                emit(Tag.SYSTEM, f"  You are now {effect}.")

        elif name == "clear_status":
            effect = op.get("effect", "")
            p.status_effects.pop(effect, None)

        elif name == "if_status":
            effect = op.get("effect", "")
            has_it = effect in p.status_effects
            branch = op.get("then", []) if has_it else op.get("else", [])
            for sub in branch:
                self._exec(sub)

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
            branch  = op.get("then", []) if ok else op.get("else", [])
            for sub in branch:
                self._exec(sub)

        elif name == "if_affinity":
            tag    = op.get("tag", "")
            has_it = tag in getattr(p, "prestige_affinities", [])
            branch = op.get("then", []) if has_it else op.get("else", [])
            for sub in branch:
                self._exec(sub)

        # ── Conditionals ──────────────────────────────────────────────────────
        elif name in ("if", "if_flag"):
            # { op = "if"/"if_flag", flag = "...", then = [...], else = [...] }
            flag   = op.get("flag", "")
            passed = flag in p.flags
            branch = op.get("then", []) if passed else op.get("else", [])
            for sub in branch:
                self._exec(sub)

        elif name == "if_not_flag":
            flag   = op.get("flag", "")
            passed = flag not in p.flags
            branch = op.get("then", []) if passed else op.get("else", [])
            for sub in branch:
                self._exec(sub)

        elif name == "if_item":
            item_id = op.get("item_id", "")
            has_it  = any(i.get("id") == item_id for i in p.inventory)
            branch  = op.get("then", []) if has_it else op.get("else", [])
            for sub in branch:
                self._exec(sub)

        elif name == "if_quest":
            q_id   = op.get("quest_id", "")
            step   = int(op.get("step", 0))
            passed = q.at_step(q_id, step)
            branch = op.get("then", []) if passed else op.get("else", [])
            for sub in branch:
                self._exec(sub)

        elif name == "if_quest_complete":
            q_id   = op.get("quest_id", "")
            passed = q.is_complete(q_id)
            branch = op.get("then", []) if passed else op.get("else", [])
            for sub in branch:
                self._exec(sub)

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

        # ── Unknown ops ───────────────────────────────────────────────────────
        # Unknown ops are silently ignored for forward-compatibility.




