"""
commands.py — Command parser and game-logic dispatcher.

All output goes through EventBus as Msg objects with semantic tags. The engine
never calls print() or decides how anything looks — that is entirely the
frontend's responsibility.

Command dispatch
────────────────
CommandProcessor.__init__ builds a flat dict mapping verb strings → handler
methods. process(raw) splits the input, looks up the verb, and calls the
handler. Aliases (e.g. "n" → "north", "i" → "inventory") are registered in
the same dict.

Item resolution
───────────────
_resolve_item_inv(query) handles partial name matching across the player's
inventory. It deduplicates identical items (same id) to avoid false ambiguity,
and returns a sentinel _ALREADY_HANDLED when it has already emitted an error
message (so callers don't double-report).

Door / exit handling
────────────────────
Exits are either plain room-ID strings or door dicts:
  { to = "room_id", locked = bool, lock_tag = "tag", desc = "..." }
_move() resolves both forms. Locked doors block movement and print a DOOR
message. unlock/lock commands check the player's inventory for a matching
key_tag.

Combat integration
──────────────────
_cmd_attack() creates a CombatSession and loops until one side is dead or the
player flees. The session is stored on self._combat so that auto-attack on
NPC entry can continue an existing fight rather than start a new one.

Context object
──────────────
CommandProcessor holds a GameContext (player + world + bus + quests) and
passes it into ScriptRunner, DialogueSession, and CombatSession. This is
the single shared context that wires all subsystems together.

New command checklist (when adding a command):
  1. Add verb → self._cmd_foo in __init__ dispatch dict
  2. Write _cmd_foo(self, verb, args)
  3. Add a line to _cmd_help's lines list
  4. Register aliases if needed
"""

from __future__ import annotations
from typing import Callable
from engine.events import EventBus, Event
from engine.world import World, _short, _long
from engine.player import Player, item_on_use_effect, item_on_equip_message, item_weight
from engine.combat import CombatSession
from engine.msg import Msg, Tag
from engine.room_flags import RoomFlags
import engine.styles as styles_mod
import engine.dialogue as dialogue_mod
import engine.crafting  as crafting_mod
import engine.quests as quests_mod
from engine.quests import QuestTracker
from engine.script import GameContext, ScriptRunner


DIRECTIONS = {
    # Cardinals
    "n": "north", "s": "south", "e": "east", "w": "west",
    "u": "up",    "d": "down",
    "north": "north", "south": "south", "east": "east", "west": "west",
    "up": "up",   "down": "down",
    # Diagonals
    "ne": "northeast", "nw": "northwest", "se": "southeast", "sw": "southwest",
    "northeast": "northeast", "northwest": "northwest",
    "southeast": "southeast", "southwest": "southwest",
    # Named movement verbs (for narrative exits)
    "climb":  "climb",  "descend": "descend",
    "enter":  "enter",  "leave":   "leave",
    "jump":   "jump",   "cross":   "cross",
    "swim":   "swim",   "crawl":   "crawl",
    "push":   "push",   "pull":    "pull",
    "portal": "portal", "gate":    "gate",
    "inside": "inside", "outside": "outside",
}


class CommandProcessor:
    def __init__(self, world: World, player: Player, bus: EventBus,
                 input_fn=None):
        self.world    = world
        self.player   = player
        self.bus      = bus
        self._input_fn = input_fn
        self._combat: CombatSession | None = None
        self._quit_requested = False

        # Build GameContext for scripts/dialogues/quests
        self._quests  = QuestTracker(player)
        self._ctx     = GameContext(player=player, world=world, bus=bus, quests=self._quests)

        self._commands: dict[str, Callable] = {
            "look":      self._cmd_look,
            "l":         self._cmd_look,
            "examine":   self._cmd_look,
            "go":        self._cmd_go,
            "inventory": self._cmd_status,
            "i":         self._cmd_status,
            "stats":     self._cmd_status,
            "score":     self._cmd_status,
            "status":    self._cmd_status,
            "get":       self._cmd_get,
            "take":      self._cmd_get,
            "drop":      self._cmd_drop,
            "equip":     self._cmd_equip,
            "unequip":   self._cmd_unequip,
            "attack":    self._cmd_attack,
            "kill":      self._cmd_attack,
            "talk":      self._cmd_talk,
            "save":      self._cmd_save,
            "help":      self._cmd_help,
            "quit":      self._cmd_quit,
            "exit":      self._cmd_quit,
            "style":     self._cmd_style,
            "learn":     self._cmd_learn,
            "use":       self._cmd_use,
            "drink":     self._cmd_use,
            "unlock":    self._cmd_unlock,
            "lock":      self._cmd_lock,
            "buy":       self._cmd_buy,
            "sell":      self._cmd_sell,
            "list":      self._cmd_list,
            "wares":     self._cmd_list,
            "shop":      self._cmd_list,
            "sleep":     self._cmd_sleep,
            "rest":      self._cmd_sleep,
            "map":       self._cmd_map,
            "journal":   self._cmd_journal,
            "skills":    self._cmd_skills,
            "skill":     self._cmd_skills,
            "quests":    self._cmd_journal,
            "j":         self._cmd_journal,
            # Aliases
            "alias":     self._cmd_alias,
            "aliases":   self._cmd_alias,
            "unalias":   self._cmd_unalias,
            # Bank
            "bank":      self._cmd_bank,
            "deposit":   self._cmd_deposit,
            "withdraw":  self._cmd_withdraw,
            "balance":   self._cmd_bank,
            "upgrade":   self._cmd_bank_upgrade,
            "expand":    self._cmd_bank_upgrade,
            # Corpse recovery
            "loot":      self._cmd_loot_corpse,
            # Crafting commissions
            "commission":    self._cmd_commission,
            "commissions":   self._cmd_commissions,
            "order":         self._cmd_commission,
            "collect":       self._cmd_collect,
            "give":          self._cmd_give,
            "companion":     self._cmd_companion,
            "dismiss":       self._cmd_dismiss,
            "recall":        self._cmd_recall,
        }
        for d in DIRECTIONS:
            self._commands[d] = self._cmd_direction
        self._commands["mine"]  = self._cmd_mine
        self._commands["chop"]  = self._cmd_chop

    # ── Public API ───────────────────────────────────────────────────────────

    @property
    def quit_requested(self) -> bool:
        return self._quit_requested

    def process(self, raw: str) -> None:
        raw = raw.strip()
        if not raw:
            return
        parts = raw.lower().split(None, 1)
        verb  = parts[0]
        args  = parts[1] if len(parts) > 1 else ""

        # Expand player-defined aliases before dispatch
        expansion = self.player.aliases.get(verb)
        if expansion:
            expanded = (expansion + " " + args).strip() if args else expansion
            exp_parts = expanded.split(None, 1)
            verb  = exp_parts[0]
            args  = exp_parts[1] if len(exp_parts) > 1 else ""
        handler = self._commands.get(verb)
        if handler:
            handler(verb, args)
        else:
            self._out(Tag.ERROR, f"Unknown command '{raw}'. Type 'help' for a list.")
        self._apply_room_effects()
        self._tick_status_effects()
        self._apply_status_damage()

    def do_look(self) -> None:
        # Mark current room as visited whenever we look at it
        self.player.visited_rooms.add(self.player.room_id)
        self._cmd_look("look", "")

    # ── Output helpers ───────────────────────────────────────────────────────

    def _out(self, tag: str, text: str) -> None:
        self.bus.emit(Event.OUTPUT, Msg(tag, text))

    def _blank(self) -> None:
        self._out(Tag.BLANK, "")

    # ── Room helpers ─────────────────────────────────────────────────────────

    def _current_room(self) -> dict | None:
        return self.world.prepare_room(self.player.room_id, self.player)

    # ── Room effect hooks ────────────────────────────────────────────────────────

    def _apply_room_effects(self) -> None:
        """Called after every command. Applies passive room flag effects."""
        room = self._current_room()
        if not room:
            return
        if RoomFlags.has(room, RoomFlags.HEALING):
            rate = room.get("heal_rate", 5)
            if self.player.hp < self.player.max_hp:
                healed = min(rate, self.player.max_hp - self.player.hp)
                self.player.hp += healed
                self._out(Tag.SYSTEM, f"The air soothes your wounds. (+{healed} HP)")
        if RoomFlags.has(room, RoomFlags.HAZARD):
            # Passive hazard damage — bypassed if player has an exemption flag
            exempt_flag = room.get("hazard_exempt_flag", "")
            if exempt_flag and exempt_flag in self.player.flags:
                return
            dmg = room.get("hazard_damage", 2)
            self.player.hp -= dmg
            msg = room.get("hazard_message", "The environment bites at you.")
            self._out(Tag.COMBAT_RECV, f"{msg} (-{dmg} HP)")
            if not self.player.is_alive:
                self._out(Tag.COMBAT_DEATH, "The hazard claims you.")

    def _check_hostile_npcs(self) -> None:
        """On room entry: hostile NPCs growl a warning and land a free first strike."""
        room = self._current_room()
        if not room:
            return
        if RoomFlags.has(room, RoomFlags.NO_COMBAT) or RoomFlags.has(room, RoomFlags.SAFE_COMBAT):
            return
        hostile = [n for n in room.get("_npcs", [])
                   if n.get("hp", 1) > 0 and n.get("hostile", False)]
        for npc in hostile:
            import random
            n_atk = npc.get("attack", 5)
            p_def = self.player.effective_defense
            dmg   = max(1, n_atk - p_def + random.randint(-2, 4))
            self._out(Tag.COMBAT_RECV,
                      f"{npc['name']} lunges at you before you can react!")
            self.player.hp -= dmg
            self._out(Tag.COMBAT_RECV,
                      f"  ...for {dmg} damage. ({self.player.hp}/{self.player.max_hp} HP)")
            if not self.player.is_alive:
                from engine.msg import Msg
                self._out(Tag.COMBAT_DEATH, "You have been slain.")
                self._out(Tag.COMBAT_DEATH, "GAME OVER")
                self._quit_requested = True
                return

    # ── Resolvers ────────────────────────────────────────────────────────────────

    # ── NPC numbering helpers ─────────────────────────────────────────────────

    @staticmethod
    def _numbered_npcs(alive: list[dict]) -> list[tuple[dict, str]]:
        """
        Return [(npc, display_name), ...] for a list of live NPCs.

        When two or more NPCs share the same name (e.g. two Corrupted Wolves),
        they are numbered: "Corrupted Wolf 1", "Corrupted Wolf 2".
        Unique names are left unchanged.

        The numbered display_name is used both in room descriptions and as the
        target string for attack/talk, so "attack wolf 1" and "attack wolf 2"
        are unambiguous even though both NPCs have id "corrupted_wolf".
        """
        from collections import Counter
        name_counts = Counter(n["name"] for n in alive)
        seen: dict[str, int] = {}
        result = []
        for npc in alive:
            base = npc["name"]
            if name_counts[base] > 1:
                seen[base] = seen.get(base, 0) + 1
                display = f"{base} {seen[base]}"
            else:
                display = base
            result.append((npc, display))
        return result

    def _resolve_npc(self, target: str) -> "dict | None":
        """Find a live NPC in the current room by partial name or numbered name.

        Supports numbered targets for duplicate-name NPCs:
          "attack wolf"   → first Corrupted Wolf (no ambiguity error)
          "attack wolf 1" → Corrupted Wolf 1 specifically
          "attack wolf 2" → Corrupted Wolf 2 specifically

        Returns the NPC dict on a match, None if not found.
        Never emits AMBIGUOUS — duplicate names are resolved by number or
        by picking the first match.
        """
        room  = self._current_room()
        alive = [n for n in room.get("_npcs", []) if n.get("hp", 1) > 0]
        numbered = self._numbered_npcs(alive)  # [(npc, display_name), ...]
        t = target.lower().strip()

        # Try exact match against display name first ("wolf 2" → Corrupted Wolf 2)
        exact = [(npc, dn) for npc, dn in numbered if dn.lower() == t]
        if exact:
            return exact[0][0]

        # Partial match against display name ("wolf" matches "Corrupted Wolf 1" etc.)
        partial = [(npc, dn) for npc, dn in numbered if t in dn.lower()]
        if partial:
            return partial[0][0]   # first match wins — no ambiguity error

        return None

    def _resolve_item_room(self, target: str) -> "tuple[int, dict] | tuple[None, None]":
        """Find an item on the ground by partial name.
        Returns (index, item) on unique match, (None, None) otherwise.
        Emits AMBIGUOUS if multiple items match.
        """
        room    = self._current_room()
        matches = [
            (i, item) for i, item in enumerate(room.get("items", []))
            if target in item.get("name", "").lower()
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            names = ", ".join(item["name"] for _, item in matches)
            self._out(Tag.AMBIGUOUS, f"Did you mean: {names}?")
        return None, None

    # Sentinel returned when a resolver already emitted its own error message.
    # Callers must check `result is _ALREADY_HANDLED` and stay silent.
    # (Defined as a class attribute so it is a stable singleton.)
    _ALREADY_HANDLED: object = object()

    def _resolve_item_bank(self, target: str) -> "dict | None | object":
        """Find an item in player.bank by partial name. Same semantics as _resolve_item_inv."""
        matches = [
            item for item in self.player.bank
            if target in item.get("name", "").lower()
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            distinct_ids = {i.get("id", i.get("name", "")) for i in matches}
            if len(distinct_ids) == 1:
                return matches[0]
            names = ", ".join(dict.fromkeys(i["name"] for i in matches))
            self._out(Tag.AMBIGUOUS, f"Did you mean: {names}?")
            return self._ALREADY_HANDLED
        return None

    def _resolve_item_inv(self, target: str) -> "dict | None | object":
        """Find an item in inventory by partial name.

        Returns:
          item dict            — unique unambiguous match
          _ALREADY_HANDLED     — multiple distinct items matched; AMBIGUOUS
                                 message already emitted; caller must be silent
          None                 — no match at all; caller should emit an error
        """
        matches = [
            item for item in self.player.inventory
            if target in item.get("name", "").lower()
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            # Deduplicate by item id — if all matches are the same item type,
            # just return the first one (player has duplicates, any will do).
            distinct_ids = {i.get("id", i.get("name", "")) for i in matches}
            if len(distinct_ids) == 1:
                return matches[0]   # identical duplicates — pick the first
            # Genuinely different items match the query → ask for clarification
            names = ", ".join(dict.fromkeys(i["name"] for i in matches))
            self._out(Tag.AMBIGUOUS, f"Did you mean: {names}?")
            return self._ALREADY_HANDLED
        return None

    # ── Commands ─────────────────────────────────────────────────────────────────

    def _cmd_look(self, verb: str, args: str) -> None:
        # Strip leading "at" so "look at sword" and "look sword" both work
        target = args.strip()
        if target.startswith("at "):
            target = target[3:].strip()

        if target:
            self._look_at(target)
        else:
            self._look_room()

    def _look_room(self) -> None:
        room = self._current_room()
        if not room:
            self._out(Tag.ERROR, "[Error: you are in the void. This is a bug.]")
            return

        self._blank()
        self._out(Tag.ROOM_NAME,    room["name"])
        self._out(Tag.ROOM_DIVIDER, "─" * len(room["name"]))
        self._out(Tag.ROOM_DESC,    room.get("description", "You see nothing special."))

        # Items — now always full dicts, so we always have the name
        items = room.get("items", [])
        if items:
            # Group by display_prefix; default is "On the ground"
            from collections import defaultdict
            by_prefix: dict[str, list[str]] = defaultdict(list)
            for i in items:
                prefix = i.get("display_prefix", "On the ground")
                by_prefix[prefix].append(i.get("name", "?"))
            for prefix, names in by_prefix.items():
                self._out(Tag.ITEM, f"{prefix}: {', '.join(names)}")

        # Corpses in the room
        corpses = self.world.get_corpses(self.player.room_id)
        for c in corpses:
            import time as _time
            mins_left = max(0, int((c["expires_at"] - _time.time()) / 60))
            owner_tag = "your" if c["owner"] == self.player.name else f"{c['owner']}'s"
            self._out(Tag.ITEM,
                      f"The corpse of {owner_tag} adventurer lies here. "
                      f"[decays in ~{mins_left}m] (type 'loot corpse')")

        # Active companion travelling with the player
        import engine.companion as companion_mod
        comp = self.player.companion
        if companion_mod.is_active(comp):
            cdef = comp.get("_def", {})
            cname = comp.get("name", "?")
            cdesc = cdef.get("desc_short", "")
            ctype = cdef.get("type", "narrative")
            if ctype == "combat":
                chp   = comp.get("hp", 0)
                cmhp  = cdef.get("max_hp", chp)
                self._out(Tag.NPC, f"{cname} [companion] — {cdesc} ({chp}/{cmhp} HP)")
            else:
                self._out(Tag.NPC, f"{cname} [companion] — {cdesc}")
        elif companion_mod.is_waiting(comp):
            cname = comp.get("name", "?") if comp else "?"
            self._out(Tag.SYSTEM, f"({cname} is waiting — this area won't accommodate them.)")

        # Live NPCs — show name + short description (numbered when names clash)
        alive = [n for n in room.get("_npcs", []) if n.get("hp", 1) > 0]
        if alive:
            for npc, display_name in self._numbered_npcs(alive):
                self._out(Tag.NPC, f"{display_name} — {_short(npc)}")

        # Exits — handle plain string targets and door objects
        exits = room.get("exits", {})
        if exits:
            exit_parts = []
            for direction, dest in exits.items():
                if isinstance(dest, dict):
                    if dest.get("locked", False):
                        exit_parts.append(f"{direction} [locked]")
                    else:
                        exit_parts.append(direction)
                else:
                    exit_parts.append(direction)
            self._out(Tag.EXIT, "Exits: " + ", ".join(exit_parts))
        else:
            self._out(Tag.EXIT, "There are no obvious exits.")

    def _look_at(self, target: str) -> None:
        """Detailed look at a named item (room or inventory) or NPC."""
        room = self._current_room()
        if not room:
            return

        # Check room items
        for item in room.get("items", []):
            if target in item.get("name", "").lower():
                self._blank()
                self._out(Tag.ITEM,      item["name"])
                self._out(Tag.ROOM_DESC, _long(item))
                self._item_stats(item)
                return

        # Check inventory
        for item in self.player.inventory:
            if target in item.get("name", "").lower():
                self._blank()
                self._out(Tag.ITEM,      item["name"])
                self._out(Tag.ROOM_DESC, _long(item))
                self._item_stats(item)
                return

        # Check live NPCs in room
        alive = [n for n in room.get("_npcs", []) if n.get("hp", 1) > 0]
        for npc in alive:
            if target in npc.get("name", "").lower():
                self._blank()
                self._out(Tag.NPC,       npc["name"])
                self._out(Tag.ROOM_DESC, _long(npc))
                return

        # Check if it's a direction — describe the exit/door
        direction = DIRECTIONS.get(target)
        if direction:
            exits = room.get("exits", {})
            exit_val = exits.get(direction)
            if exit_val is None:
                self._out(Tag.SYSTEM, f"There is nothing to the {direction}.")
            elif isinstance(exit_val, dict):
                locked  = exit_val.get("locked", False)
                desc    = exit_val.get("desc", "a door")
                lock_tag = exit_val.get("lock_tag", "")
                dest    = exit_val.get("to", "?")
                status  = "locked" if locked else "unlocked"
                self._out(Tag.DOOR, f"To the {direction}: {desc} [{status}].")
                if locked and lock_tag:
                    self._out(Tag.DOOR, f"  It requires a key tagged '{lock_tag}'.")
            else:
                dest_room = self.world.prepare_room(exit_val, self.player)                             or self.world.get_room(exit_val)
                dest_name = dest_room.get("name", exit_val) if dest_room else exit_val
                self._out(Tag.EXIT, f"To the {direction}: {dest_name}.")
            return

        self._out(Tag.ERROR, f"You don't see '{target}' here.")

    def _item_stats(self, item: dict) -> None:
        """Emit stat and effect lines for an item based on its effects array."""
        slot = item.get("slot", "")
        if slot:
            self._out(Tag.STATS, f"  Slot: {slot}")
        for fx in item.get("effects", []):
            ftype = fx.get("type", "")
            if ftype == "stat_bonus":
                stat   = fx.get("stat", "")
                amount = int(fx.get("amount", 0))
                sign   = "+" if amount >= 0 else ""
                self._out(Tag.STATS, f"  {stat.replace('_',' ').title()}: {sign}{amount}")
            elif ftype == "on_hit":
                ability = fx.get("ability", "").replace("_", " ").title()
                chance  = int(fx.get("chance", 0) * 100)
                mag     = fx.get("magnitude", "")
                mag_str = f" ({mag} dmg/tick)" if str(ability).lower() == "bleed" and mag else ""
                self._out(Tag.STATS, f"  On hit: {ability}{mag_str} ({chance}% chance)")
            elif ftype == "on_use":
                heal = fx.get("heal", 0)
                if heal:
                    self._out(Tag.STATS, f"  Use: Restores {heal} HP")
        wtags = item.get("weapon_tags", [])
        atags = item.get("armor_tags",  [])
        if wtags:
            self._out(Tag.STATS, f"  Weapon type: {', '.join(wtags)}")
        if atags:
            self._out(Tag.STATS, f"  Armor type: {', '.join(atags)}")

    def _cmd_go(self, verb: str, args: str) -> None:
        direction = DIRECTIONS.get(args.strip())
        if not direction:
            self._out(Tag.ERROR, "Go where? (e.g. 'go north')")
            return
        self._move(direction)

    def _cmd_direction(self, verb: str, args: str) -> None:
        self._move(DIRECTIONS.get(verb))

    def _move(self, direction: str) -> None:
        room = self._current_room()
        exits = room.get("exits", {}) if room else {}
        exit_val = exits.get(direction)
        if not exit_val:
            self._out(Tag.ERROR, f"You can't go {direction} from here.")
            return
        # Resolve door object vs plain string
        if isinstance(exit_val, dict):
            door = exit_val
            dest_id = door.get("to", "")
            if door.get("locked", False):
                door_desc = door.get("desc", "the door")
                self._out(Tag.DOOR,
                          f"The way {direction} is locked. ({door_desc})")
                return
        else:
            door    = None
            dest_id = exit_val
        if not dest_id:
            self._out(Tag.ERROR, f"You can't go {direction} from here.")
            return
        # prepare_room triggers zone loading if the destination is in an unloaded zone
        dest = self.world.prepare_room(dest_id, self.player)
        if not dest:
            self._out(Tag.ERROR, f"[Error: room '{dest_id}' not found. This is a bug.]")
            return
        self.player.room_id = dest_id
        self.player.visited_rooms.add(dest_id)
        self._out(Tag.MOVE, f"You head {direction}.")
        # Auto-bind: entering a town room sets the player's respawn point
        if RoomFlags.has(dest, RoomFlags.TOWN):
            if self.player.bind_room != dest_id:
                self.player.bind_room = dest_id
                self._out(Tag.SYSTEM,
                          f"You feel at home here. "
                          f"If you fall, you'll wake in {dest.get('name', dest_id)}.")
        # XP debt reminder on first kill after death
        # (shown in gain_xp path; nothing needed here)
        # Evict zones that are no longer adjacent after the move
        current_zone = self.world.zone_for_room(dest_id)
        if current_zone:
            self.world.evict_distant_zones(current_zone)
        self.do_look()
        self._check_hostile_npcs()
        # Run room on_enter scripts (skill checks, status effects, story beats)
        self._run_room_on_enter(dest)
        # Advance crafting commissions by 1 turn per room move
        self._tick_commissions(1)
        # Handle companion following/waiting based on room restrictions
        self._update_companion_location(dest)

    def _run_room_on_enter(self, room: dict) -> None:
        """Execute the room's on_enter script block, if any.
        Also checks prestige-based hostile-on-sight for NPCs in the room."""
        on_enter = room.get("on_enter", [])
        if on_enter:
            from engine.script import ScriptRunner
            ScriptRunner(self._ctx).run(on_enter)
        # Prestige: turn peaceful NPCs hostile if threshold crossed
        import engine.prestige as prestige_mod
        for npc in room.get("_npcs", []):
            if npc.get("hp", 1) <= 0:
                continue
            if npc.get("hostile"):
                continue   # already hostile
            if prestige_mod.hostile_on_sight(npc, self.player):
                npc["hostile"] = True
                self._out(Tag.COMBAT_RECV,
                          f"{npc['name']} eyes you with recognition — and draws a weapon.")

    def _tick_status_effects(self) -> None:
        """Decrement turn-based status effects; remove expired ones."""
        expired = []
        for effect, turns in list(self.player.status_effects.items()):
            if turns == -1:
                continue   # permanent until cleared by script
            turns -= 1
            if turns <= 0:
                expired.append(effect)
            else:
                self.player.status_effects[effect] = turns
        for eff in expired:
            del self.player.status_effects[eff]
            clear_msgs = {
                "poisoned":  "The poison runs its course. You feel yourself again.",
                "blinded":   "Your vision clears.",
                "weakened":  "Your strength returns.",
                "slowed":    "The sensation of sluggishness lifts.",
                "protected": "The protective ward fades.",
            }
            self._out(Tag.SYSTEM, clear_msgs.get(eff, f"The {eff} condition ends."))

    def _apply_status_damage(self) -> None:
        """Apply per-move damage from active status effects."""
        if "poisoned" in self.player.status_effects:
            dmg = 3
            self.player.hp = max(0, self.player.hp - dmg)
            self._out(Tag.COMBAT_RECV,
                      f"Poison gnaws at you. (-{dmg} HP — {self.player.hp}/{self.player.max_hp})")
            if not self.player.is_alive:
                self._out(Tag.COMBAT_DEATH, "The poison claims you.")

    def _cmd_status(self, verb: str, args: str) -> None:
        """Combined character sheet: stats + equipped + inventory in one view."""
        p     = self.player
        W     = 32   # column width
        ruler = "─" * W

        # ── Header ───────────────────────────────────────────────────
        self._blank()
        self._out(Tag.STATS, ruler)
        self._out(Tag.STATS, f"  {p.name:<14} Level {p.level}")
        self._out(Tag.STATS, ruler)

        # ── Vitals ───────────────────────────────────────────────────
        hp_bar_len = 16
        filled = int(hp_bar_len * p.hp / max(p.max_hp, 1))
        bar    = "█" * filled + "░" * (hp_bar_len - filled)
        self._out(Tag.STATS, f"  HP  [{bar}] {p.hp}/{p.max_hp}")
        self._out(Tag.STATS, f"  XP  {p.xp}/{p.xp_next}   Gold: {p.gold}g")

        # ── Combat stats ─────────────────────────────────────────────
        self._out(Tag.STATS, ruler)
        self._out(Tag.STATS,
                  f"  ATK {p.effective_attack:>3}  (base {p.attack})"
                  f"   DEF {p.effective_defense:>3}  (base {p.defense})")

        # ── Fighting style ───────────────────────────────────────────
        style      = styles_mod.get(p.active_style)
        style_name = style["name"] if style else p.active_style
        prof       = p.style_proficiency()
        passives   = styles_mod.unlocked_passives(style, prof) if style else []
        self._out(Tag.STATS,  ruler)
        self._out(Tag.STYLE,  f"  Style: {style_name}  ({prof:.0f}/100)")
        if passives:
            self._out(Tag.STYLE,
                      f"  Abilities: {', '.join(p.replace('_',' ').title() for p in passives)}")
        if len(p.known_styles) > 1:
            others = [s for s in p.known_styles if s != p.active_style]
            other_names = [styles_mod.get(s)["name"] if styles_mod.get(s) else s for s in others]
            self._out(Tag.STYLE, f"  Also known: {', '.join(other_names)}")

        # ── Carry weight ─────────────────────────────────────────────
        self._out(Tag.STATS, ruler)
        wt  = p.current_weight
        cap = p.carry_capacity
        wt_filled = int(12 * wt / max(cap, 1))
        wt_bar    = "█" * wt_filled + "░" * (12 - wt_filled)
        self._out(Tag.STATS, f"  Carry [{wt_bar}] {wt}/{cap} stones")

        # ── Prestige ─────────────────────────────────────────────────
        import engine.prestige as prestige_mod
        self._out(Tag.STATS, ruler)
        self._out(Tag.STATS, prestige_mod.prestige_line(p))

        # ── Equipment ────────────────────────────────────────────────
        self._out(Tag.STATS, ruler)
        self._out(Tag.STATS, "  Equipment:")
        SLOT_DISPLAY = [
            ("weapon", "Weapon "),
            ("head",   "Head   "),
            ("chest",  "Chest  "),
            ("arms",   "Arms   "),
            ("legs",   "Legs   "),
            ("armor",  "Body   "),   # legacy full-body slot
            ("shield", "Shield "),
            ("ring",   "Ring   "),
            ("cape",   "Cape   "),
            ("pack",   "Pack   "),
        ]
        for slot, label in SLOT_DISPLAY:
            eq = p.equipped.get(slot)
            if eq:
                self._out(Tag.ITEM_EQUIP,
                          f"  {label}◄ {eq['name']} — {_short(eq)}")
            else:
                self._out(Tag.SYSTEM, f"  {label}  (empty)")

        # ── Inventory ────────────────────────────────────────────────
        self._out(Tag.STATS, ruler)
        # Equipped items are on your body — exclude them from the carry list
        equipped_objs = {id(eq) for eq in p.equipped.values() if eq}
        carried = [item for item in p.inventory if id(item) not in equipped_objs]
        if not carried:
            self._out(Tag.SYSTEM, "  Carrying: nothing")
        else:
            self._out(Tag.SYSTEM, "  Carrying:")
            for item in carried:
                self._out(Tag.ITEM,
                          f"    • {item['name']} — {_short(item)}")
        self._out(Tag.STATS, ruler)

    def _cmd_mine(self, verb: str, args: str) -> None:
        """mine <ore> — Mine an ore deposit (requires a pickaxe in inventory/equipped)."""
        has_pickaxe = any(
            "pickaxe" in (item.get("tags", []) if item else [])
            for item in list(self.player.inventory)
            + [v for v in self.player.equipped.values() if v]
        )
        if not has_pickaxe:
            self._out(Tag.ERROR,
                      "You need a pickaxe to mine. (buy one from a local merchant)")
            return
        if not args:
            self._out(Tag.ERROR, "Mine what? (e.g. 'mine iron vein')")
            return
        self._out(Tag.SYSTEM, "You swing your pickaxe against the rock...")
        self._cmd_get("get", args)

    def _cmd_chop(self, verb: str, args: str) -> None:
        """chop <wood> — Chop timber (requires a hatchet or axe)."""
        has_axe = any(
            "axe" in (item.get("tags", []) if item else [])
            for item in list(self.player.inventory)
            + [v for v in self.player.equipped.values() if v]
        )
        if not has_axe:
            self._out(Tag.ERROR,
                      "You need a hatchet or axe to chop wood.")
            return
        if not args:
            self._out(Tag.ERROR, "Chop what? (e.g. 'chop timber')")
            return
        self._out(Tag.SYSTEM, "You raise your axe and swing...")
        self._cmd_get("get", args)

    def _cmd_get(self, verb: str, args: str) -> None:
        if not args:
            self._out(Tag.ERROR, "Get what?")
            return
        room   = self._current_room()
        target = args.lower()
        found_idx, found_item = self._resolve_item_room(target)
        if found_item is None:
            self._out(Tag.ERROR, f"You don't see '{args}' here.")
            return
        # Scenery items cannot be picked up
        if found_item.get("scenery", False):
            self._out(Tag.ERROR,
                      f"You can't pick up {found_item['name']} — it's not going anywhere.")
            return
        # Weight check
        w = item_weight(found_item)
        if w > 0 and not self.player.can_carry(found_item):
            cap  = self.player.carry_capacity
            curr = self.player.current_weight
            self._out(Tag.ERROR,
                      f"Too heavy! Carrying {curr}/{cap} stones. "
                      f"{found_item['name']} weighs {w}.")
            return
        room["items"].pop(found_idx)
        # Auto-convert gold_value items to gold instead of carrying them
        gold_val = found_item.get("gold_value", 0)
        if gold_val:
            self.player.gold += gold_val
            self._out(Tag.REWARD_GOLD,
                      f"You collect {found_item['name']} ({gold_val}g). ({self.player.gold}g total)")
        else:
            self.player.add_item(found_item)
            self._out(Tag.ITEM, f"You pick up {found_item['name']}.")
        # Track unique (non-respawning) items so they don't come back
        if not found_item.get("respawn", False):
            self.player.record_looted(room["id"], found_item.get("id", ""))
        # Run on_get script if defined (e.g. quest flag triggers)
        on_get = found_item.get("on_get", [])
        if on_get:
            from engine.script import ScriptRunner
            ScriptRunner(self._ctx).run(on_get)

    def _cmd_drop(self, verb: str, args: str) -> None:
        if not args:
            self._out(Tag.ERROR, "Drop what?")
            return
        target = args.lower()
        item = self._resolve_item_inv(target)
        if item is self._ALREADY_HANDLED:
            return
        if not item:
            self._out(Tag.ERROR, f"You aren't carrying '{args}'.")
            return
        for slot, eq in self.player.equipped.items():
            if eq and eq.get("id") == item.get("id"):
                self.player.equipped[slot] = None
        self.player.remove_item(item)
        self._current_room().setdefault("items", []).append(item)
        self._out(Tag.ITEM, f"You drop {item['name']}.")


    def _maybe_free_npc_attack(self, action: str = "distracted") -> None:
        """
        Give hostile NPCs in the room a chance at a free attack when the player
        is distracted (equipping, using an item, etc.).

        Chance based on NPC speed tag:
          slow  → 20%   (lumbering enemies can't exploit the opening)
          (none)→ 35%   (default)
          fast  → 50%   (quick enemies punish careless item use)

        Only triggers if there's an active combat session or live hostiles.
        """
        import random
        room = self._current_room()
        if not room:
            return
        alive_hostile = [
            n for n in room.get("_npcs", [])
            if n.get("hp", 0) > 0 and n.get("hostile", False)
        ]
        if not alive_hostile:
            return
        for npc in alive_hostile:
            tags = npc.get("tags", [])
            if "slow" in tags:
                chance = 0.20
            elif "fast" in tags:
                chance = 0.50
            else:
                chance = 0.35
            if random.random() < chance:
                # Quick retaliation — use combat module's npc_attack helper
                import engine.combat as combat_mod
                dmg = combat_mod.npc_damage(npc, self.player)
                if dmg > 0:
                    self.player.hp -= dmg
                    self._out(Tag.COMBAT_RECV,
                              f"{npc['name']} takes advantage while you're {action}! "
                              f"(-{dmg} HP)")
                    if not self.player.is_alive:
                        self._out(Tag.COMBAT_DEATH,
                                  f"{npc['name']} cuts you down while you fumble.")
                        self.bus.emit(Event.PLAYER_DIED, None)
                        self._quit_requested = True
                        return
                else:
                    self._out(Tag.SYSTEM,
                              f"{npc['name']} swipes at you while you're {action}, but misses.")

    def _cmd_equip(self, verb: str, args: str) -> None:
        if not args:
            self._out(Tag.ERROR, "Equip what?")
            return
        target = args.lower()
        item = self._resolve_item_inv(target)
        if item is self._ALREADY_HANDLED:
            return
        if not item:
            self._out(Tag.ERROR, f"You aren't carrying '{args}'.")
            return
        slot = item.get("slot")
        _VALID_SLOTS = ("weapon", "head", "chest", "legs", "arms", "armor", "pack", "ring", "shield", "cape")
        if slot not in _VALID_SLOTS:
            self._out(Tag.ERROR, f"{item['name']} cannot be equipped "
                                 f"(no equipment slot; slot='{slot or 'none'}').")
            return
        # Unequip whatever is already in that slot
        old_item = self.player.equipped.get(slot)
        if old_item:
            self._out(Tag.ITEM_EQUIP, f"You remove {old_item['name']}.")
        self.player.equipped[slot] = item
        self._out(Tag.ITEM_EQUIP, f"You equip {item['name']} [{slot}].")
        msg = item_on_equip_message(item)
        if msg:
            self._out(Tag.ITEM, f"  {msg}")
        self._maybe_free_npc_attack("equipping gear")

    def _cmd_unequip(self, verb: str, args: str) -> None:
        target = args.lower()
        _ALL_SLOTS = ("weapon", "head", "chest", "legs", "arms", "armor", "pack", "ring", "shield", "cape")
        for slot in _ALL_SLOTS:
            eq = self.player.equipped.get(slot)
            if target in slot or (eq and target in eq.get("name", "").lower()):
                if eq is None:
                    self._out(Tag.ERROR, f"Nothing equipped in {slot} slot.")
                    return
                self.player.equipped[slot] = None
                self._out(Tag.ITEM_EQUIP, f"You unequip {eq['name']}.")
                self._maybe_free_npc_attack("removing gear")
                return
        self._out(Tag.ERROR, f"Nothing equipped matching '{args}'.")

    def _cmd_attack(self, verb: str, args: str) -> None:
        if not args:
            self._out(Tag.ERROR, "Attack whom?")
            return
        target = args.lower()
        room = self._current_room()

        # Check no_combat flag before anything else
        if RoomFlags.has(room, RoomFlags.NO_COMBAT):
            self._out(Tag.SYSTEM, "Combat is not permitted here.")
            return

        npc = self._resolve_npc(target)
        if not npc:
            self._out(Tag.ERROR, f"There is no '{args}' here to attack.")
            return
        # Start a new session if: no session, last session is done,
        # OR the target NPC changed (e.g. moved rooms between attacks)
        if (not self._combat
                or self._combat.done
                or self._combat.npc is not npc):
            self._combat = CombatSession(self.player, npc, self.bus, room, ctx=self._ctx)
        self._combat.player_attack()
        if self._combat.done and not self._combat.player_won:
            self._quit_requested = True

    def _cmd_talk(self, verb: str, args: str) -> None:
        if not args:
            self._out(Tag.ERROR, "Talk to whom?")
            return
        target = args.lower()
        npc = self._resolve_npc(target)
        if not npc:
            self._out(Tag.ERROR, f"There is no '{args}' here.")
            return
        # Use branching dialogue engine if a tree exists; fall back to plain string
        dialogue_mod.run_inline(npc, self.player, self._quests, self._ctx, self.bus,
                                   input_fn=self._input_fn)



    def _cmd_save(self, verb: str, args: str) -> None:
        self.player.save()
        self.world.save_all_zone_state()
        self._out(Tag.SYSTEM, "Character saved.")

    # ── Companion system ──────────────────────────────────────────────────────

    def _update_companion_location(self, dest_room: dict) -> None:
        """
        Called after every move. Checks if the companion can follow into
        the new room or must wait at the previous room.
        """
        import engine.companion as companion_mod
        comp = self.player.companion
        if not comp:
            return
        ctype = companion_mod.companion_type(comp)
        if ctype == "narrative":
            return   # narrative companions don't physically block

        status = comp.get("status", "")

        if status == "active":
            if not companion_mod.can_enter_room(comp, dest_room):
                # Companion stays behind
                comp["status"]       = "waiting"
                comp["waiting_room"] = dest_room.get("id", self.player.room_id)
                cdef     = comp.get("_def", {})
                wait_msg = cdef.get("wait_message",
                                    f"{comp['name']} can't follow here — they wait for you.")
                self._out(Tag.SYSTEM, wait_msg)
            # else: companion enters with player, no message needed

        elif status == "waiting":
            # Check if player has returned to the room the companion is waiting in
            # OR a room the companion can reach (any room they're allowed into)
            waiting_at = comp.get("waiting_room", "")
            if companion_mod.can_enter_room(comp, dest_room):
                # Companion can be here — rejoin if they were waiting nearby
                comp["status"]       = "active"
                comp["waiting_room"] = ""
                cdef      = comp.get("_def", {})
                rejoin_msg = cdef.get("rejoin_message",
                                      f"{comp['name']} falls back into step with you.")
                self._out(Tag.SYSTEM, rejoin_msg)

    def _cmd_companion(self, verb: str, args: str) -> None:
        """companion — Show current companion status."""
        import engine.companion as companion_mod
        comp = self.player.companion
        self._blank()
        if not comp:
            self._out(Tag.SYSTEM, "  You have no companion at the moment.")
            self._blank()
            return

        cdef   = comp.get("_def", {})
        cname  = comp.get("name", "?")
        ctype  = cdef.get("type", "narrative")
        status = comp.get("status", "unknown")

        self._out(Tag.NPC,    f"  {cname}")
        self._out(Tag.SYSTEM, f"  Type:   {ctype.capitalize()}")
        self._out(Tag.SYSTEM, f"  Status: {status.capitalize()}")

        if ctype == "combat":
            chp  = comp.get("hp", 0)
            cmhp = cdef.get("max_hp", chp)
            self._out(Tag.STATS, f"  HP:     {chp}/{cmhp}")
            if status == "downed":
                self._out(Tag.ERROR, "  Downed — rest at an inn to recover them.")

        if ctype in ("utility", "combat"):
            carry = int(cdef.get("carry_bonus", 0))
            if carry:
                self._out(Tag.STATS, f"  Carry bonus: +{carry} stones")

        if status == "waiting":
            self._out(Tag.SYSTEM, f"  Waiting at: {comp.get('waiting_room', '?')}")

        self._out(Tag.SYSTEM, "  (Type 'dismiss' to part ways.)")
        self._blank()

    def _cmd_dismiss(self, verb: str, args: str) -> None:
        """dismiss — Part ways with your companion."""
        import engine.companion as companion_mod
        comp = self.player.companion
        if not comp:
            self._out(Tag.ERROR, "You have no companion to dismiss.")
            return
        cname = comp.get("name", "your companion")
        comp["status"] = "dismissed"
        self.player.companion = None
        self._out(Tag.SYSTEM,
                  f"You part ways with {cname}. You can find them again where you first met.")

    def _cmd_recall(self, verb: str, args: str) -> None:
        """recall — Recall a waiting companion to active (if in a compatible room)."""
        import engine.companion as companion_mod
        comp = self.player.companion
        if not comp:
            self._out(Tag.ERROR, "You have no companion.")
            return
        room = self._current_room()
        if companion_mod.is_active(comp):
            self._out(Tag.SYSTEM, f"{comp.get('name','?')} is already with you.")
            return
        if comp.get("status") == "downed":
            self._out(Tag.ERROR,
                      f"{comp.get('name','?')} is downed. Rest at an inn first.")
            return
        if companion_mod.can_enter_room(comp, room):
            comp["status"]       = "active"
            comp["waiting_room"] = ""
            cdef = comp.get("_def", {})
            self._out(Tag.SYSTEM, cdef.get("rejoin_message",
                                           f"{comp.get('name','?')} rejoins you."))
        else:
            self._out(Tag.ERROR,
                      f"{comp.get('name','?')} cannot enter this area. "
                      f"Lead them somewhere they can follow first.")

    def _companion_rest_recovery(self) -> None:
        """Call after inn rest. Recovers downed companions."""
        import engine.companion as companion_mod
        comp = self.player.companion
        if companion_mod.rest_companion(comp):
            cname = comp.get("name", "your companion") if comp else "?"
            self._out(Tag.SYSTEM,
                      f"{cname} has recovered. They're ready to fight again.")

    def _cmd_skills(self, verb: str, args: str) -> None:
        """Display player skill levels."""
        import engine.skills as skills_mod
        p = self.player
        self._blank()
        self._out(Tag.STATS, "── Adventuring Skills ─────────────────")
        for skill_id in skills_mod.SKILLS:
            val   = p.skills.get(skill_id, 0.0)
            bonus = skills_mod.skill_bonus(val)
            tier  = skills_mod.tier_name(val)
            bar_len = 20
            filled  = int(bar_len * val / 100)
            bar     = "█" * filled + "░" * (bar_len - filled)
            name    = skills_mod.SKILL_NAMES[skill_id]
            self._out(Tag.STATS,
                      f"  {name:<12} [{bar}] {int(val):>3}/100  +{bonus}  {tier}")
        # Show active status effects if any
        if p.status_effects:
            self._blank()
            self._out(Tag.STATS, "── Status Effects ─────────────────────")
            for eff, turns in p.status_effects.items():
                dur = "permanent" if turns == -1 else f"{turns} turns"
                self._out(Tag.STATS, f"  {eff.capitalize():<14} ({dur})")
        self._blank()

    def _cmd_help(self, verb: str, args: str) -> None:
        """help [topic]  — topics: move, combat, gear, commerce, crafting, bank, world, system"""
        topic = args.strip().lower()
        sections = {
            "move": [
                "── Movement ──────────────────────────────────────────",
                "  n/s/e/w/u/d                    — Move in a direction",
                "  north/south/east/west/up/down   — Full direction names",
                "  go <direction>                  — Same",
                "  look / l                        — Describe your surroundings",
                "  look at <item|npc|direction>    — Examine something",
                "  examine <item|npc>              — Same as look at",
                "  map                             — Show your explored map",
            ],
            "combat": [
                "── Combat ────────────────────────────────────────────",
                "  attack <npc>      — Attack an NPC (also: kill)",
                "  style             — Show your active fighting style",
                "  style list        — List all known styles",
                "  style set <n>     — Switch to a known style",
                "  learn <style>     — Learn a style from a trainer NPC",
                "  skills            — Show adventuring skill levels",
            ],
            "gear": [
                "── Gear & Inventory ──────────────────────────────────",
                "  inventory / i / stats   — Character sheet",
                "  get <item>              — Pick up an item",
                "  drop <item>             — Drop an item",
                "  equip <item>            — Equip a weapon or armor",
                "  unequip <slot>          — Unequip from a slot",
                "  use / drink <item>      — Use a consumable",
                "  unlock <dir>            — Unlock a door (need key)",
                "  lock <dir>              — Lock a door",
            ],
            "commerce": [
                "── Commerce ──────────────────────────────────────────",
                "  list / wares / shop     — Show nearby shop wares",
                "  buy <item>              — Buy from a merchant",
                "  sell <item>             — Sell to a merchant",
                "  sleep / rest            — Rest at an inn (costs gold)",
            ],
            "crafting": [
                "── Crafting Commissions ──────────────────────────────",
                "  commission              — Browse a crafter commission menu",
                "  commissions             — List your active commissions",
                "  give <item> to <npc>    — Hand a material to a crafter",
                "  collect                 — Collect a finished item",
                "  (commissions also appear in your journal: j)",
            ],
            "bank": [
                "── Banking ───────────────────────────────────────────",
                "  balance / bank          — Check your account (at a bank)",
                "  deposit <item>          — Store an item in the bank",
                "  withdraw <item>         — Retrieve a stored item",
            ],
            "world": [
                "── World & Quests ─────────────────────────────────────",
                "  journal / quests / j    — Quest log, commissions, companion",
                "  talk <npc>              — Talk to an NPC",
                "  loot                    — Recover items from your corpse",
            ],
            "companion": [
                "── Companions ────────────────────────────────────────",
                "  companion               — Show companion status and HP",
                "  dismiss                 — Part ways with your companion",
                "  recall                  — Recall a waiting companion",
                "  (Companions are acquired through quests and dialogue.)",
                "  Companion types:",
                "    narrative  — Guide or lore companion (flag-based, no HP)",
                "    utility    — Pack animal or porter (+carry capacity)",
                "    combat     — Fights alongside you (has own HP, can be downed)",
            ],
            "system": [
                "── System ────────────────────────────────────────────",
                "  save                    — Save your character",
                "  quit / exit             — Save and quit",
                "  alias <word> <cmd>      — Create a command shortcut",
                "  alias                   — List your aliases",
                "  unalias <word>          — Remove an alias",
                "── Bank ──────────────────────────────────────────────",
                "  bank / balance          — View stored items, gold, and capacity",
                "  deposit <item>          — Store item (uses a slot)",
                "  deposit gold <N>        — Store gold with the banker",
                "  deposit all gold        — Store all gold on hand",
                "  withdraw <item>         — Retrieve item",
                "  withdraw gold <N>       — Retrieve stored gold",
                "  upgrade [confirm]       — Expand account capacity for gold",
                "── Auto-attack (frontend) ────────────────────────────",
                "  autoattack / aa         — Toggle auto-attack on/off",
                "  (Auto-attack continues until enemy dies, you drop",
                "   below the HP threshold, or you leave combat.)",
                "  Default and HP threshold set in frontend/config.py",
            ],
            "gear": [
                "── Gear & Inventory ──────────────────────────────────",
                "  inventory / i           — Show carried items",
                "  equip <item>            — Wear/wield an item",
                "  unequip <item/slot>     — Remove equipped item",
                "  drop <item>             — Drop item in room",
                "  get <item>              — Pick up item from room",
                "── Equipment Slots ───────────────────────────────────",
                "  weapon                  — Main weapon",
                "  head                    — Helmet / headgear",
                "  chest                   — Chest armour",
                "  legs                    — Leg armour",
                "  arms                    — Arm/hand armour",
                "  armor                   — Full-body armour (legacy slot)",
                "  shield                  — Off-hand shield",
                "  pack                    — Backpack (increases carry weight)",
                "  ring                    — Ring",
                "  cape                    — Cape / cloak",
            ],
        }
        if topic in sections:
            self._blank()
            for line in sections[topic]:
                self._out(Tag.SYSTEM, line)
            self._blank()
        else:
            self._blank()
            self._out(Tag.SYSTEM, "── Help ──────────────────────────────────────────────")
            self._out(Tag.SYSTEM, "  Type \'help <topic>\' for full command list.")
            self._blank()
            self._out(Tag.SYSTEM, "  Topics:")
            self._out(Tag.SYSTEM, "    move       — Movement and exploration")
            self._out(Tag.SYSTEM, "    combat     — Fighting, styles, skills")
            self._out(Tag.SYSTEM, "    gear       — Inventory, equip, use items")
            self._out(Tag.SYSTEM, "    commerce   — Shops and inns")
            self._out(Tag.SYSTEM, "    crafting   — Commissions from crafters")
            self._out(Tag.SYSTEM, "    bank       — Deposit and withdraw items")
            self._out(Tag.SYSTEM, "    world      — Quests and dialogue")
            self._out(Tag.SYSTEM, "    companion  — Companion status and commands")
            self._out(Tag.SYSTEM, "    system     — Save, quit, aliases")
            self._blank()
            self._out(Tag.SYSTEM, "  Quick reference:")
            self._out(Tag.SYSTEM, "    n/s/e/w      — Move        look / l   — Look around")
            self._out(Tag.SYSTEM, "    i / stats    — Inventory   j          — Journal")
            self._out(Tag.SYSTEM, "    attack <npc> — Fight       talk <npc> — Talk to NPC")
            self._out(Tag.SYSTEM, "    alias        — Command shortcuts")
            self._blank()
    def _cmd_style(self, verb: str, args: str) -> None:
        """style / style list / style set <name>"""
        sub = args.strip().lower()
        all_styles = styles_mod.get_all()

        if sub == "list":
            self._blank()
            known_ids = self.player.known_styles
            if not known_ids:
                self._out(Tag.STYLE, "You have not yet learned any fighting styles.")
                return
            self._out(Tag.STYLE, "Known fighting styles:")
            for sid in known_ids:
                sdata = all_styles.get(sid)
                if not sdata:
                    continue
                prof   = self.player.style_proficiency(sid)
                active = " ◄ active" if sid == self.player.active_style else ""
                self._out(Tag.STYLE,  f"  {sdata['name']:<18} {prof:.0f}/100{active}")
                self._out(Tag.SYSTEM, f"  {'':18} {sdata['desc_short']}")
                self._out(Tag.SYSTEM, f"  {'':18} Strong vs: {', '.join(sdata.get('strong_vs',[]))}"
                                      f"  |  Weak vs: {', '.join(sdata.get('weak_vs',[]))}")
            # Hint that more exist without spoiling them
            unknown_count = len(all_styles) - len(known_ids)
            if unknown_count > 0:
                self._out(Tag.SYSTEM,
                          f"\n  ({unknown_count} other style{'s' if unknown_count>1 else ''} "
                          f"exist — seek out trainers and explore to discover them.)")
            return

        if sub.startswith("set "):
            target = sub[4:].strip()
            # Find matching known style
            matches = [sid for sid in self.player.known_styles
                       if target in sid.lower() or
                          target in all_styles.get(sid, {}).get("name", "").lower()]
            if not matches:
                self._out(Tag.ERROR, f"You don't know a style matching '{target}'.")
                self._out(Tag.SYSTEM, "Use 'style list' to see available styles.")
                return
            if len(matches) > 1:
                names = ", ".join(all_styles[m]["name"] for m in matches)
                self._out(Tag.AMBIGUOUS, f"Did you mean: {names}?")
                return
            chosen = matches[0]
            self.player.active_style = chosen
            sdata = all_styles[chosen]
            prof  = self.player.style_proficiency(chosen)
            self._out(Tag.STYLE, f"You adopt {sdata['name']} stance. (proficiency {prof:.0f}/100)")
            self._out(Tag.STYLE, f"  Strong vs: {', '.join(sdata.get('strong_vs',[]))}")
            self._out(Tag.STYLE, f"  Weak vs:   {', '.join(sdata.get('weak_vs',[]))}")
            return

        # Default: show active style details
        sid   = self.player.active_style
        sdata = all_styles.get(sid)
        if not sdata:
            self._out(Tag.ERROR, f"Unknown style '{sid}'. This is a bug.")
            return
        prof     = self.player.style_proficiency()
        passives = styles_mod.unlocked_passives(sdata, prof)
        self._blank()
        self._out(Tag.STYLE, f"Active style: {sdata['name']}  ({prof:.0f}/100)")
        self._out(Tag.STYLE, f"  {sdata['desc_short']}")
        self._out(Tag.STYLE, f"  Strong vs: {', '.join(sdata.get('strong_vs',[]))}")
        self._out(Tag.STYLE, f"  Weak vs:   {', '.join(sdata.get('weak_vs',[]))}")
        atk = sdata.get("attack_bonus", 0)
        dfn = sdata.get("defense_bonus", 0)
        if atk or dfn:
            self._out(Tag.STYLE,
                      f"  Bonuses:   attack {atk:+d}  defense {dfn:+d}")
        if passives:
            self._out(Tag.STYLE,
                      f"  Passives:  {', '.join(p.replace('_',' ').title() for p in passives)}")
        else:
            # Show next unlock hint
            next_p = next(
                (p for p in sdata.get("passives", []) if p["threshold"] > prof),
                None
            )
            if next_p:
                self._out(Tag.SYSTEM,
                          f"  Next ability at proficiency {next_p['threshold']}: "
                          f"{next_p['ability'].replace('_',' ').title()}")

    def _cmd_learn(self, verb: str, args: str) -> None:
        """learn <style> — learn a style, optionally from a trainer NPC nearby."""
        if not args:
            self._out(Tag.ERROR, "Learn what? ('style list' to see options)")
            return

        target     = args.strip().lower()
        all_styles = styles_mod.get_all()

        # Find matching style
        matches = [
            (sid, sdata) for sid, sdata in all_styles.items()
            if target in sid.lower() or target in sdata.get("name","").lower()
        ]
        if not matches:
            self._out(Tag.ERROR, f"No style matching '{args}'. Try 'style list'.")
            return
        if len(matches) > 1:
            names = ", ".join(sd["name"] for _, sd in matches)
            self._out(Tag.AMBIGUOUS, f"Did you mean: {names}?")
            return

        sid, sdata = matches[0]

        if sid in self.player.known_styles:
            self._out(Tag.STYLE, f"You already know {sdata['name']}.")
            return

        # Check for required trainer in the room
        teacher_id = sdata.get("learned_from", "")
        teacher_npc_id = None
        if teacher_id:
            room  = self._current_room()
            alive = [n for n in room.get("_npcs", []) if n.get("hp", 1) > 0]
            match = next((n for n in alive if n.get("id") == teacher_id), None)
            if not match:
                self._out(Tag.ERROR,
                          f"{sdata['name']} requires a trainer. "
                          f"Find the right teacher and ask them.")
                return
            teacher_npc_id = teacher_id

        ok, reason = styles_mod.can_learn(sdata, self.player.level, teacher_npc_id)
        if not ok:
            self._out(Tag.ERROR, reason)
            return

        self.player.learn_style(sid)
        self.player.active_style = sid
        self._out(Tag.STYLE, f"You learn {sdata['name']}!")
        self._out(Tag.STYLE, f"  {sdata['desc_short']}")
        self._out(Tag.STYLE, f"  Strong vs: {', '.join(sdata.get('strong_vs', []))}")
        self._out(Tag.STYLE, f"  Weak vs:   {', '.join(sdata.get('weak_vs',   []))}")
        self._out(Tag.STYLE, f"  Style set to {sdata['name']}.")

    def _cmd_use(self, verb: str, args: str) -> None:
        """use / drink <item> — consume an item with an on_use effect."""
        if not args:
            self._out(Tag.ERROR, "Use what?")
            return
        target = args.strip().lower()
        item = self._resolve_item_inv(target)
        if item is self._ALREADY_HANDLED:
            return
        if not item:
            self._out(Tag.ERROR, f"You don't have '{args}'.")
            return
        fx = item_on_use_effect(item)
        if not fx:
            self._out(Tag.ERROR, f"You can't use {item['name']} like that.")
            return
        # Collect all effects for this item, not just first on_use
        all_fx = [f for f in item.get("effects", []) if f.get("type") == "on_use"]
        used = False
        messages = []
        for fx in all_fx:
            heal = int(fx.get("heal", 0))
            if heal:
                if self.player.hp < self.player.max_hp:
                    before = self.player.hp
                    self.player.hp = min(self.player.max_hp, self.player.hp + heal)
                    messages.append(f"+{self.player.hp - before} HP ({self.player.hp}/{self.player.max_hp})")
                    used = True
            special = fx.get("special", "")
            if special.startswith("clear_status_"):
                effect_name = special[len("clear_status_"):]
                if effect_name in self.player.status_effects:
                    del self.player.status_effects[effect_name]
                    messages.append(f"{effect_name} cleared")
                    used = True
        if used:
            msg = f"You use the {item['name']}."
            if messages:
                msg += " (" + ", ".join(messages) + ")"
            self._out(Tag.SYSTEM, msg)
            self.player.remove_item(item)
            self._maybe_free_npc_attack("using an item")
        else:
            self._out(Tag.ERROR, f"Nothing happens.")


    # ─────────────────────────────────────────────────────────────────────────
    # DOORS & LOCKS
    # ─────────────────────────────────────────────────────────────────────────

    def _find_door_exit(self, direction: str) -> "tuple[dict, str] | tuple[None, None]":
        """Return (door_dict, dest_id) for a direction, or (None, None).
        Only returns a result when the exit is a door object (has lock_tag)."""
        room = self._current_room()
        if not room:
            return None, None
        exit_val = room.get("exits", {}).get(direction)
        if isinstance(exit_val, dict) and "lock_tag" in exit_val:
            return exit_val, exit_val.get("to", "")
        return None, None

    def _key_for_lock(self, lock_tag: str) -> "dict | None":
        """Return the first inventory item that has a matching key_tag, or None."""
        for item in self.player.inventory:
            if item.get("key_tag") == lock_tag:
                return item
        return None

    def _cmd_unlock(self, verb: str, args: str) -> None:
        direction = args.strip().lower() or "north"
        door, dest_id = self._find_door_exit(direction)
        if not door:
            self._out(Tag.DOOR, f"There's no lockable door to the {direction}.")
            return
        if not door.get("locked", False):
            self._out(Tag.DOOR, "That door is already unlocked.")
            return
        lock_tag = door.get("lock_tag", "")
        key = self._key_for_lock(lock_tag)
        if not key:
            self._out(Tag.DOOR,
                      f"You don't have the right key. (lock: {lock_tag})")
            return
        door["locked"] = False
        # Also unlock the matching exit in the destination room (two-way unlock)
        dest_room = self.world.get_room(dest_id)
        if dest_room:
            for ex_val in dest_room.get("exits", {}).values():
                if isinstance(ex_val, dict) and ex_val.get("lock_tag") == lock_tag:
                    ex_val["locked"] = False
        self._out(Tag.DOOR,
                  f"You unlock the door to the {direction} with your {key['name']}.")

    def _cmd_lock(self, verb: str, args: str) -> None:
        direction = args.strip().lower() or "north"
        door, dest_id = self._find_door_exit(direction)
        if not door:
            self._out(Tag.DOOR, f"There's no lockable door to the {direction}.")
            return
        if door.get("locked", False):
            self._out(Tag.DOOR, "That door is already locked.")
            return
        lock_tag = door.get("lock_tag", "")
        key = self._key_for_lock(lock_tag)
        if not key:
            self._out(Tag.DOOR, f"You don't have the right key.")
            return
        door["locked"] = True
        dest_room = self.world.get_room(dest_id)
        if dest_room:
            for ex_val in dest_room.get("exits", {}).values():
                if isinstance(ex_val, dict) and ex_val.get("lock_tag") == lock_tag:
                    ex_val["locked"] = True
        self._out(Tag.DOOR, f"You lock the door to the {direction}.")

    # ─────────────────────────────────────────────────────────────────────────
    # SHOPS — buy / sell / list
    # ─────────────────────────────────────────────────────────────────────────

    def _shop_npc(self) -> "dict | None":
        """Return a live shop NPC in the current room, or None."""
        room = self._current_room()
        if not room:
            return None
        for npc in room.get("_npcs", []):
            if npc.get("hp", 1) > 0 and npc.get("shop"):
                return npc
        return None

    def _cmd_list(self, verb: str, args: str) -> None:
        npc = self._shop_npc()
        if not npc:
            self._out(Tag.ERROR, "There's no one here to trade with.")
            return
        self._blank()
        self._out(Tag.SHOP, f"{npc['name']} offers:")
        self._out(Tag.SHOP, f"  {'Item':<22} {'Price':>6}  Description")
        self._out(Tag.SHOP, f"  {'─'*22} {'─'*6}  {'─'*24}")
        for entry in npc.get("shop", []):
            item_id = entry.get("item_id", "")
            price   = entry.get("price", 0)
            tmpl    = self.world.items.get(item_id)
            if tmpl:
                w = item_weight(tmpl)
                w_str = f" {w}st" if w else ""
                self._out(Tag.SHOP,
                          f"  {tmpl['name']:<22} {price:>5}g  {_short(tmpl)}{w_str}")
        self._out(Tag.SHOP, f"  (You have {self.player.gold} gold)")

    def _cmd_buy(self, verb: str, args: str) -> None:
        if not args:
            self._out(Tag.ERROR, "Buy what? ('list' to see wares)")
            return
        npc = self._shop_npc()
        if not npc:
            self._out(Tag.ERROR, "There's no one here to buy from.")
            return
        target = args.lower()
        # Find matching entry
        matches = [
            e for e in npc.get("shop", [])
            if target in self.world.items.get(e.get("item_id",""), {}).get("name","").lower()
        ]
        if not matches:
            self._out(Tag.ERROR, f"'{args}' isn't sold here.")
            return
        if len(matches) > 1:
            names = ", ".join(
                self.world.items[e["item_id"]]["name"]
                for e in matches if e["item_id"] in self.world.items
            )
            self._out(Tag.AMBIGUOUS, f"Did you mean: {names}?")
            return
        entry      = matches[0]
        base_price = entry.get("price", 0)
        item_id    = entry.get("item_id", "")
        tmpl       = self.world.items.get(item_id)
        if not tmpl:
            self._out(Tag.ERROR, "That item doesn't exist (data error).")
            return
        import engine.prestige as prestige_mod
        mod   = prestige_mod.shop_modifier(self.player.prestige)
        price = max(1, int(base_price * mod))
        price_note = ""
        if mod < 1.0:
            price_note = " (prestige discount)"
        elif mod > 1.0:
            price_note = " (prestige surcharge)"
        if self.player.gold < price:
            self._out(Tag.ERROR,
                      f"You can't afford {tmpl['name']}. "
                      f"({price}g needed{price_note}, you have {self.player.gold}g)")
            return
        if not self.player.can_carry(tmpl):
            w   = item_weight(tmpl)
            cap = self.player.carry_capacity
            cur = self.player.current_weight
            self._out(Tag.ERROR,
                      f"You can't carry {tmpl['name']} ({w} stones). "
                      f"Already carrying {cur}/{cap}.")
            return
        import copy
        self.player.gold -= price
        self.player.add_item(copy.deepcopy(tmpl))
        self._out(Tag.SHOP,
                  f"You buy {tmpl['name']} for {price}g{price_note}. "
                  f"({self.player.gold}g remaining)")

    def _cmd_sell(self, verb: str, args: str) -> None:
        if not args:
            self._out(Tag.ERROR, "Sell what?")
            return
        npc = self._shop_npc()
        if not npc:
            self._out(Tag.ERROR, "There's no one here to sell to.")
            return
        item = self._resolve_item_inv(args.lower())
        if item is self._ALREADY_HANDLED:
            return
        if not item:
            self._out(Tag.ERROR, f"You aren't carrying '{args}'.")
            return
        # Sell price = half of buy price, rounded down
        buy_price = next(
            (e["price"] for e in npc.get("shop", [])
             if e.get("item_id") == item.get("id")),
            None
        )
        if buy_price is None:
            # Not in shop list — offer 1g for anything equippable, 0 otherwise
            buy_price = 2 if item.get("slot") in ("weapon","armor") else 0
        sell_price = max(1, buy_price // 2)
        self.player.remove_item(item)
        # Un-equip if equipped
        for slot, eq in self.player.equipped.items():
            if eq and eq.get("id") == item.get("id"):
                self.player.equipped[slot] = None
        self.player.gold += sell_price
        self._out(Tag.SHOP,
                  f"You sell {item['name']} for {sell_price}g. "
                  f"({self.player.gold}g total)")

    # ─────────────────────────────────────────────────────────────────────────
    # INN — sleep / rest
    # ─────────────────────────────────────────────────────────────────────────

    def _cmd_sleep(self, verb: str, args: str) -> None:
        room = self._current_room()
        if not room:
            return
        # Check for innkeeper NPC with rest_cost
        innkeeper = next(
            (n for n in room.get("_npcs", [])
             if n.get("hp", 1) > 0 and "rest_cost" in n),
            None
        )
        # Also allow sleeping in rooms with a "sleep" flag
        can_sleep_free = "sleep" in room.get("flags", [])
        if not innkeeper and not can_sleep_free:
            self._out(Tag.SYSTEM,
                      "There's nowhere to sleep here. Find an inn.")
            return
        if innkeeper:
            cost = int(innkeeper.get("rest_cost", 5))
            if self.player.gold < cost:
                self._out(Tag.ERROR,
                          f"{innkeeper['name']} says: 'That'll be {cost} gold for a bed.'")
                self._out(Tag.DIALOGUE,
                          f"  'Come back when you've got the coin.'")
                return
            self.player.gold -= cost
            self._out(Tag.DIALOGUE,
                      f"{innkeeper['name']} takes {cost} gold and hands you a key.")
        self.player.hp = self.player.max_hp
        self._out(Tag.SYSTEM,
                  "You find a comfortable bed and sleep deeply.")
        self._out(Tag.SYSTEM,
                  f"You wake rested and restored. "
                  f"({self.player.hp}/{self.player.max_hp} HP)")
        # Companion recovery
        self._companion_rest_recovery()
        # Rest counts as 20 turns of crafting progress
        self._tick_commissions(20)

    # ─────────────────────────────────────────────────────────────────────────
    # MAP
    # ─────────────────────────────────────────────────────────────────────────

    def _cmd_map(self, verb: str, args: str) -> None:
        """Render a fog-of-war ASCII map.
        Shows: visited rooms + rooms directly connected to visited rooms (as ?).
        Rooms with no connection to explored space are invisible.
        """
        from engine.toml_io import load as toml_load
        visited = self.player.visited_rooms

        # Load all room data (coord + exits) from zone files
        all_rooms: dict[str, dict] = {}
        for meta in self.world._zone_index.values():
            for room_file in meta.room_files:
                for room in toml_load(room_file).get("room", []):
                    rid = room.get("id","")
                    if rid:
                        all_rooms[rid] = room

        # Build set of rooms to show:
        #   1. All visited rooms
        #   2. All rooms reachable in ONE exit from a visited room
        visible_ids: set[str] = set(visited)
        for rid in visited:
            room = all_rooms.get(rid, {})
            for exit_val in room.get("exits", {}).values():
                dest = exit_val.get("to","") if isinstance(exit_val, dict) else exit_val
                if dest:
                    visible_ids.add(dest)

        # Build coord grid — only visible rooms
        coords: dict[tuple[int,int], dict] = {}
        for rid in visible_ids:
            room = all_rooms.get(rid)
            if not room:
                continue
            coord = room.get("coord")
            if not coord or len(coord) < 2:
                continue
            x, y = int(coord[0]), int(coord[1])
            coords[(x,y)] = {
                "id":      rid,
                "name":    room.get("name","?"),
                "visited": rid in visited,
                "here":    rid == self.player.room_id,
                "exits":   room.get("exits", {}),
            }

        if not coords:
            self._out(Tag.MAP, "No map data yet. Explore to reveal the world.")
            return

        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        lines = []
        for y in range(max_y, min_y - 1, -1):
            row_cells  = []
            row_hconns = []
            for x in range(min_x, max_x + 1):
                room = coords.get((x, y))
                if room:
                    if room["here"]:
                        cell = "[@]"
                    elif room["visited"]:
                        abbr = room["name"][:3].upper()
                        cell = f"[{abbr}]"
                    else:
                        cell = "[?]"
                    row_cells.append(cell)

                    # East connection: show if both sides are in visible set
                    east_val  = room["exits"].get("east")
                    east_dest = east_val.get("to","") if isinstance(east_val, dict) else (east_val or "")
                    east_room = coords.get((x+1, y))
                    if east_dest and east_room and (room["visited"] or east_room["visited"]):
                        row_hconns.append("──")
                    else:
                        row_hconns.append("  ")
                else:
                    row_cells.append("   ")
                    row_hconns.append("  ")

            line = "".join(c + h for c, h in zip(row_cells, row_hconns)).rstrip()
            lines.append(line)

            # Vertical connectors
            if y > min_y:
                vrow = []
                for x in range(min_x, max_x + 1):
                    room  = coords.get((x, y))
                    below = coords.get((x, y-1))
                    if room and below and (room["visited"] or below["visited"]):
                        south_val  = room["exits"].get("south")
                        south_dest = south_val.get("to","") if isinstance(south_val, dict) else (south_val or "")
                        # Also check north exit of below room
                        north_val  = below["exits"].get("north")
                        north_dest = north_val.get("to","") if isinstance(north_val, dict) else (north_val or "")
                        if south_dest or north_dest:
                            vrow.append(" | ")
                        else:
                            vrow.append("   ")
                    else:
                        vrow.append("   ")
                vline = "  ".join(vrow).rstrip()
                if vline.strip():
                    lines.append(vline)

        self._blank()
        self._out(Tag.MAP, "── Map ──────────────────────────────")
        for line in lines:
            if line.strip():
                self._out(Tag.MAP, "  " + line)
        self._out(Tag.MAP, "─────────────────────────────────────")
        self._out(Tag.MAP, "  [@]=you  [XXX]=visited  [?]=unexplored")
        explored = len(visited)
        total    = len(all_rooms)
        self._out(Tag.MAP, f"  Explored: {explored}/{total} rooms")

    def _cmd_journal(self, verb: str, args: str) -> None:
        """Show quest journal — quests, active crafting commissions, and companion."""
        self._blank()
        for tag, text in self._quests.journal_lines():
            self._out(tag, text)
        self._commissions_journal_section()
        self._companion_journal_section()
        self._blank()

    def _companion_journal_section(self) -> None:
        """Append companion status to journal if player has one."""
        import engine.companion as companion_mod
        comp = self.player.companion
        if not comp:
            return
        cdef   = comp.get("_def", {})
        cname  = comp.get("name", "?")
        ctype  = cdef.get("type", "narrative")
        status = comp.get("status", "unknown")
        self._blank()
        self._out(Tag.NPC, "── Companion ──────────────────────────────────")
        status_str = {
            "active":    "travelling with you",
            "waiting":   f"waiting at {comp.get('waiting_room', '?')}",
            "downed":    "downed — needs rest at an inn",
            "dismissed": "dismissed",
        }.get(status, status)
        self._out(Tag.SYSTEM, f"  {cname}  [{ctype}]  — {status_str}")
        if ctype == "combat":
            chp  = comp.get("hp", 0)
            cmhp = cdef.get("max_hp", chp)
            bar_len = 12
            filled  = int(bar_len * chp / max(cmhp, 1))
            bar     = "█" * filled + "░" * (bar_len - filled)
            self._out(Tag.STATS, f"  HP  [{bar}] {chp}/{cmhp}")
        if ctype in ("utility", "combat"):
            carry = int(cdef.get("carry_bonus", 0))
            if carry:
                self._out(Tag.STATS, f"  Carry bonus: +{carry} stones")

    def _npc_display_name(self, npc_id: str) -> str:
        """Resolve a stored npc_id to the NPC's display name, fallback to id."""
        npc = self.world.npcs.get(npc_id, {})
        return npc.get("name", npc_id)

    def _material_tag_and_label(self, mat_id: str) -> tuple[str, str]:
        """
        Check where a commission material is. Returns (Tag, display_label).
        Priority: inventory > bank > missing.
        Resolves mat_id to item name via world item catalogue.
        """
        world_item = self.world.items.get(mat_id, {})
        name = world_item.get("name", mat_id)
        # Check inventory
        if any(i.get("id") == mat_id for i in self.player.inventory):
            return Tag.ITEM_HAVE, f"✓ {name}"
        # Check bank
        if any(i.get("id") == mat_id for i in self.player.bank):
            return Tag.ITEM_BANK, f"◈ {name} (in bank)"
        # Not found
        return Tag.ITEM_MISSING, f"✗ {name}"

    def _commissions_journal_section(self) -> None:
        """Append active commissions to the journal display, if any."""
        if not self.player.commissions:
            return
        self._out(Tag.SYSTEM, "")
        self._out(Tag.QUEST, "── Crafting Commissions ────────────────")
        for rec in self.player.commissions:
            status = rec.get("status", "?")
            label  = rec.get("label", "item")
            npc_id = rec.get("npc_id", "?")
            slot   = rec.get("slot", "")
            slot_str = f" [{slot}]" if slot else ""

            if status == "waiting_materials":
                still = crafting_mod.materials_still_needed(rec)
                self._out(Tag.QUEST,  f"  {label}{slot_str}  —  waiting for materials")
                npc_name = self._npc_display_name(npc_id)
                self._out(Tag.SYSTEM, f"    ► Bring to {npc_name}:")
                for mat_id in still:
                    tag, label_str = self._material_tag_and_label(mat_id)
                    self._out(tag, f"        {label_str}")
                self._out(Tag.SYSTEM, f"      (use 'give <item> to <npc>')")
            elif status == "in_progress":
                turns = rec.get("turns_remaining", 0)
                npc_name = self._npc_display_name(npc_id)
                self._out(Tag.QUEST,  f"  {label}{slot_str}  —  being crafted")
                self._out(Tag.SYSTEM, f"    ► Return to {npc_name} in ~{turns} moves")
            elif status == "ready":
                npc_name = self._npc_display_name(npc_id)
                self._out(Tag.QUEST,  f"  {label}{slot_str}  —  READY")
                self._out(Tag.SYSTEM, f"    ► Return to {npc_name} and type 'collect'")

    def _cmd_quit(self, verb: str, args: str) -> None:
        self.player.save()
        self.world.save_all_zone_state()
        self._out(Tag.SYSTEM, "Farewell. Your progress has been saved.")
        self._quit_requested = True

    # ── Bank ─────────────────────────────────────────────────────────────────

    def _banker_npc(self) -> "dict | None":
        """Return the live banker NPC in the current room, or None."""
        room = self._current_room()
        if not room:
            return None
        for npc in room.get("_npcs") or []:
            if npc.get("hp", 1) > 0 and "banker" in npc.get("tags", []):
                return npc
        return None

    def _cmd_bank(self, verb: str, args: str) -> None:
        """Show bank account contents. Requires a banker NPC in the room."""
        if not self._banker_npc():
            self._out(Tag.ERROR, "There is no bank here.")
            return
        self._blank()
        self._out(Tag.SYSTEM, "── Bank Account ──────────────────────────")
        if not self.player.bank:
            self._out(Tag.SYSTEM, "  Your account is empty.")
        else:
            for i, item in enumerate(self.player.bank, 1):
                self._out(Tag.ITEM,
                          f"  {i:2}. {item.get('name','?')}"
                          f"  — {item.get('desc_short','')}")
        used = len(self.player.bank)
        cap  = self.player.bank_slots
        bg   = getattr(self.player, "banked_gold", 0)
        self._out(Tag.SYSTEM, f"  Slots: {used}/{cap}  ·  Banked gold: {bg}g  ·  On hand: {self.player.gold}g")
        if used >= cap:
            self._out(Tag.SYSTEM, "  (Account full — ask the banker about expansion)")
        self._out(Tag.SYSTEM, "──────────────────────────────────────────")
        self._blank()


    # Bank expansion tiers: slots → gold cost
    _BANK_TIERS = [
        (10,  0),     # base (free)
        (20,  150),   # tier 1
        (40,  500),   # tier 2
        (80,  2000),  # tier 3
    ]

    def _cmd_bank_upgrade(self, verb: str, args: str) -> None:
        """upgrade / expand  — Pay to increase bank account capacity."""
        if not self._banker_npc():
            self._out(Tag.ERROR, "There is no bank here.")
            return
        current = self.player.bank_slots
        tiers   = self._BANK_TIERS
        # Find next tier
        next_tier = next(
            ((slots, cost) for slots, cost in tiers if slots > current),
            None
        )
        if not next_tier:
            self._out(Tag.SYSTEM,
                      f"Your account is already at maximum capacity ({current} slots).")
            return
        next_slots, cost = next_tier
        if args.strip().lower() in ("confirm", "yes", "y"):
            if self.player.gold < cost:
                self._out(Tag.ERROR,
                          f"You need {cost}g to expand to {next_slots} slots. "
                          f"(You have {self.player.gold}g)")
                return
            self.player.gold      -= cost
            self.player.bank_slots = next_slots
            self._out(Tag.REWARD_GOLD,
                      f"Account expanded to {next_slots} slots. (-{cost}g)")
        else:
            self._out(Tag.SYSTEM,
                      f"Expand your account from {current} to {next_slots} slots for {cost}g?")
            self._out(Tag.SYSTEM,  "  Type 'upgrade confirm' to proceed.")
            # Show all tiers
            self._out(Tag.SYSTEM, "  Expansion tiers:")
            for slots, c in tiers:
                label = " ← current" if slots == current else (" ← next" if slots == next_slots else "")
                self._out(Tag.SYSTEM, f"    {slots:3} slots  —  {c}g{label}")

    def _cmd_deposit(self, verb: str, args: str) -> None:
        """deposit <item>  — Move an item from inventory to your bank account.
        deposit gold <N>   — Store gold in your account.
        deposit all gold   — Store all gold."""
        if not self._banker_npc():
            self._out(Tag.ERROR, "There is no bank here.")
            return
        if not args.strip():
            self._out(Tag.ERROR, "Deposit what? (e.g. 'deposit sword' or 'deposit gold 50')")
            return
        # ── Gold deposit ───────────────────────────────────────────────────────
        lower_args = args.strip().lower()
        if lower_args == "all gold" or lower_args == "gold all":
            amount = self.player.gold
        elif lower_args.startswith("gold ") or lower_args.startswith("gold"):
            rest = lower_args[4:].strip()
            if not rest or rest == "all":
                amount = self.player.gold
            else:
                try:
                    amount = int(rest)
                except ValueError:
                    amount = None
            if amount is None:
                self._out(Tag.ERROR, "deposit gold <amount>  — e.g. 'deposit gold 50'")
                return
        else:
            amount = None   # not a gold deposit — fall through to item logic

        if amount is not None:
            if amount <= 0:
                self._out(Tag.ERROR, "Specify a positive amount to deposit.")
                return
            if amount > self.player.gold:
                self._out(Tag.ERROR,
                          f"You only have {self.player.gold}g. Cannot deposit {amount}g.")
                return
            self.player.gold         -= amount
            self.player.banked_gold  = getattr(self.player, "banked_gold", 0) + amount
            self._out(Tag.REWARD_GOLD,
                      f"You deposit {amount}g with the banker. "
                      f"(Banked: {self.player.banked_gold}g | On hand: {self.player.gold}g)")
            return
        item = self._resolve_item_inv(args)
        if item is None:
            self._out(Tag.ERROR, f"You're not carrying anything matching '{args}'.")
            return
        if item is self._ALREADY_HANDLED:
            return  # ambiguous — message already emitted
        # Unequip first if needed
        for slot, eq in self.player.equipped.items():
            if eq is item:
                self.player.equipped[slot] = None
                self._out(Tag.SYSTEM, f"You unequip {item['name']} first.")
                break
        max_slots = self.player.bank_slots
        if len(self.player.bank) >= max_slots:
            self._out(Tag.ERROR,
                      f"Your account is full ({max_slots} slots). "
                      f"Ask the banker about expanding your account.")
            return
        self.player.inventory.remove(item)
        self.player.bank.append(item)
        self._out(Tag.SYSTEM,
                  f"You deposit {item['name']} with the banker. "
                  f"({len(self.player.bank)}/{max_slots} slots used)")

    def _cmd_withdraw(self, verb: str, args: str) -> None:
        """withdraw <item>  — Move an item from your bank account to inventory.
        withdraw gold <N>   — Retrieve stored gold."""
        if not self._banker_npc():
            self._out(Tag.ERROR, "There is no bank here.")
            return
        if not args.strip():
            self._out(Tag.ERROR, "Withdraw what? (e.g. 'withdraw sword' or 'withdraw gold 50')")
            return
        # ── Gold withdrawal ────────────────────────────────────────────────────
        lower_args = args.strip().lower()
        if lower_args == "all gold" or lower_args == "gold all":
            amount = getattr(self.player, "banked_gold", 0)
        elif lower_args.startswith("gold"):
            rest = lower_args[4:].strip()
            if not rest or rest == "all":
                amount = getattr(self.player, "banked_gold", 0)
            else:
                try:
                    amount = int(rest)
                except ValueError:
                    amount = None
            if amount is None:
                self._out(Tag.ERROR, "withdraw gold <amount>  — e.g. 'withdraw gold 50'")
                return
        else:
            amount = None

        if amount is not None:
            banked = getattr(self.player, "banked_gold", 0)
            if amount <= 0:
                self._out(Tag.ERROR, "Specify a positive amount.")
                return
            if amount > banked:
                self._out(Tag.ERROR,
                          f"You only have {banked}g banked. Cannot withdraw {amount}g.")
                return
            self.player.banked_gold -= amount
            self.player.gold        += amount
            self._out(Tag.REWARD_GOLD,
                      f"You withdraw {amount}g. "
                      f"(Banked: {self.player.banked_gold}g | On hand: {self.player.gold}g)")
            return
        item = self._resolve_item_bank(args)
        if item is None:
            self._out(Tag.ERROR, f"Your bank account has nothing matching '{args}'.")
            return
        if item is self._ALREADY_HANDLED:
            return  # ambiguous — message already emitted
        if not self.player.can_carry(item):
            self._out(Tag.ERROR,
                      f"You can't carry {item['name']} — too heavy. "
                      f"({self.player.current_weight}/{self.player.carry_capacity} stones)")
            return
        self.player.bank.remove(item)
        self.player.inventory.append(item)
        self._out(Tag.SYSTEM,
                  f"You withdraw {item['name']} from the bank. "
                  f"({len(self.player.bank)} items remaining)")

    # ── Corpse recovery ───────────────────────────────────────────────────────

    def _cmd_loot_corpse(self, verb: str, args: str) -> None:
        """loot corpse  — Recover items from your corpse in this room."""
        room = self._current_room()
        if not room:
            return
        corpses = self.world.get_corpses(self.player.room_id)
        # Find own corpse first; allow looting any if no personal one
        own = [c for c in corpses if c["owner"] == self.player.name]
        target = own[0] if own else (corpses[0] if corpses else None)
        if not target:
            self._out(Tag.ERROR, "There is no corpse here to loot.")
            return
        items = list(target.get("items", []))
        if not items:
            self._out(Tag.SYSTEM, "The corpse is empty.")
            self.world.remove_corpse(self.player.room_id, target)
            return
        taken, skipped = [], []
        for item in items:
            if self.player.can_carry(item):
                self.player.inventory.append(item)
                taken.append(item["name"])
            else:
                skipped.append(item["name"])
        if taken:
            self._out(Tag.ITEM,
                      f"You recover: {', '.join(taken)}.")
        if skipped:
            self._out(Tag.ERROR,
                      f"Too heavy to carry: {', '.join(skipped)}. "
                      f"({self.player.current_weight}/{self.player.carry_capacity} stones)")
        if not skipped:
            self.world.remove_corpse(self.player.room_id, target)
        else:
            # Leave remaining items on corpse
            target["items"] = [i for i in items if i["name"] in skipped]

    # ── Aliases / hotkeys ─────────────────────────────────────────────────────

    def _cmd_alias(self, verb: str, args: str) -> None:
        """alias [word [expansion]]  — View or set command aliases.

        alias               — list all current aliases
        alias atk attack    — make 'atk' expand to 'attack'
        alias gs attack goblin scout  — multi-word expansion works too
        """
        args = args.strip()
        if not args:
            # List all aliases
            self._blank()
            self._out(Tag.SYSTEM, "── Your aliases ──────────────────────────")
            if not self.player.aliases:
                self._out(Tag.SYSTEM, "  No aliases set.")
                self._out(Tag.SYSTEM, "  Use: alias <word> <expansion>")
            else:
                for word, expansion in sorted(self.player.aliases.items()):
                    self._out(Tag.SYSTEM, f"  {word:<12} → {expansion}")
            self._out(Tag.SYSTEM, "──────────────────────────────────────────")
            self._blank()
            return

        parts = args.split(None, 1)
        word = parts[0].lower()
        if len(parts) == 1:
            # Show single alias
            exp = self.player.aliases.get(word)
            if exp:
                self._out(Tag.SYSTEM, f"  '{word}' → '{exp}'")
            else:
                self._out(Tag.SYSTEM, f"  No alias '{word}' set.")
            return

        expansion = parts[1].strip()
        # Guard against aliasing built-in commands that would cause confusion
        _PROTECTED = {"alias", "unalias", "quit", "exit", "save"}
        if word in _PROTECTED:
            self._out(Tag.ERROR, f"Cannot alias '{word}' — it's a protected command.")
            return

        self.player.aliases[word] = expansion
        self._out(Tag.SYSTEM, f"  Alias set: '{word}' → '{expansion}'")
        self._out(Tag.SYSTEM, "  (saved with your character)")
        self.player.save()

    def _cmd_unalias(self, verb: str, args: str) -> None:
        """unalias <word>  — Remove an alias."""
        word = args.strip().lower()
        if not word:
            self._out(Tag.ERROR, "Unalias what? (e.g. 'unalias atk')")
            return
        if word in self.player.aliases:
            del self.player.aliases[word]
            self._out(Tag.SYSTEM, f"  Alias '{word}' removed.")
            self.player.save()
        else:
            self._out(Tag.ERROR, f"No alias '{word}' found.")

    # ─────────────────────────────────────────────────────────────────────────
    # CRAFTING COMMISSIONS
    # ─────────────────────────────────────────────────────────────────────────

    def _crafter_npc(self) -> "dict | None":
        """Return a live crafter NPC in the current room (has commissions defined), or None."""
        room = self._current_room()
        if not room:
            return None
        for npc in room.get("_npcs", []):
            if npc.get("hp", 1) > 0 and crafting_mod.commissions_for_npc(npc.get("id", "")):
                return npc
        return None

    def _cmd_commission(self, verb: str, args: str) -> None:
        """commission [npc]  — Browse a crafter's menu and place a commission."""
        npc = self._crafter_npc()
        if not npc:
            self._out(Tag.ERROR, "There is no crafter here.")
            return

        npc_id      = npc.get("id", "")
        npc_name    = npc.get("name", "the crafter")
        commissions = crafting_mod.commissions_for_npc(npc_id)

        # Check if player already has an in-progress commission with this NPC
        if crafting_mod.has_pending_commission(self.player, npc_id):
            # Show status of existing commissions
            self._out(Tag.SYSTEM, "")
            self._out(Tag.SYSTEM, f"── {npc_name} ──────────────────────────────")
            for rec in self.player.commissions:
                if rec.get("npc_id") != npc_id:
                    continue
                status = rec.get("status", "?")
                label  = rec.get("label", "item")
                if status == "waiting_materials":
                    still = crafting_mod.materials_still_needed(rec)
                    self._out(Tag.SYSTEM, f"  {label}: waiting for materials")
                    self._out(Tag.SYSTEM,  "    Still needed:")
                    for mat_id in still:
                        tag, label_str = self._material_tag_and_label(mat_id)
                        self._out(tag, f"      {label_str}")
                elif status == "in_progress":
                    turns = rec.get("turns_remaining", 0)
                    self._out(Tag.SYSTEM,
                              f"  {label}: in progress (~{turns} moves remaining)")
                elif status == "ready":
                    self._out(Tag.SYSTEM,
                              f"  {label}: READY — type 'collect' to pick it up!")
            self._out(Tag.SYSTEM, "")
            return

        # Show commission menu
        self._blank()
        self._out(Tag.DIALOGUE, f"{npc_name} looks you over.")
        self._out(Tag.SYSTEM, "")
        self._out(Tag.SYSTEM, f"── Available Commissions ─────────────────────")
        for i, c in enumerate(commissions, 1):
            mats  = ', '.join(c.get("materials", []))
            turns = c.get("turns_required", 30)
            gold  = c.get("gold_cost", 0)
            self._out(Tag.SYSTEM,
                      f"  [{i}] {c.get('label', '?')}  —  {c.get('desc', '')}")
            cost_parts = [f"materials: {mats}"]
            if gold:
                cost_parts.append(f"{gold}g deposit")
            cost_parts.append(f"~{turns} moves")
            self._out(Tag.SYSTEM, f"       ({', '.join(cost_parts)})")
        self._out(Tag.SYSTEM,
                  "  [0] Never mind")
        self._out(Tag.SYSTEM, "")

        # Get player choice via input_fn (supports AI player injection)
        choices = [(str(i), c.get("label", "?")) for i, c in enumerate(commissions, 1)]
        choices.append(("0", "Never mind"))

        _input = self._input_fn if self._input_fn is not None else \
                 lambda prompt, _c: input(prompt)
        try:
            raw = _input("  > ", choices).strip()
        except (EOFError, KeyboardInterrupt):
            return

        if raw == "0" or raw.lower() in ("n", "no", "q", "quit", "never mind"):
            self._out(Tag.DIALOGUE, f"{npc_name} nods.")
            return

        try:
            choice_idx = int(raw) - 1
            if not (0 <= choice_idx < len(commissions)):
                raise ValueError
        except ValueError:
            self._out(Tag.ERROR, "Pick a number from the list.")
            return

        chosen = commissions[choice_idx]

        # Deduct upfront gold cost
        gold_cost = int(chosen.get("gold_cost", 0))
        if gold_cost and self.player.gold < gold_cost:
            self._out(Tag.ERROR,
                      f"You need {gold_cost} gold for the deposit. "
                      f"(You have {self.player.gold}g)")
            return
        if gold_cost:
            self.player.gold -= gold_cost
            self._out(Tag.SYSTEM, f"  You pay {gold_cost}g deposit.")

        rec = crafting_mod.start_commission(self.player, chosen)
        mats = crafting_mod.materials_still_needed(rec)

        self._out(Tag.DIALOGUE,
                  f"{npc_name} says: 'Bring me {', '.join(mats)} and I'll get started.'")
        self._out(Tag.SYSTEM,
                  f"  Commission placed: {chosen.get('label', 'item')}")
        self._out(Tag.SYSTEM,
                  f"  Use 'give <item> to {npc.get('name','crafter').split()[0].lower()}' "
                  f"to hand over materials.")
        self._out(Tag.SYSTEM, "")

    def _cmd_commissions(self, verb: str, args: str) -> None:
        """commissions  — List all active commissions across all crafters."""
        if not self.player.commissions:
            self._out(Tag.SYSTEM, "You have no active commissions.")
            return
        self._out(Tag.SYSTEM, "")
        self._out(Tag.SYSTEM, "── Active Commissions ────────────────────────")
        for rec in self.player.commissions:
            status = rec.get("status", "?")
            label  = rec.get("label", "item")
            npc_id = rec.get("npc_id", "?")
            if status == "waiting_materials":
                still    = crafting_mod.materials_still_needed(rec)
                npc_name = self._npc_display_name(npc_id)
                self._out(Tag.SYSTEM,  f"  {label} ({npc_name}): waiting for materials")
                for mat_id in still:
                    tag, label_str = self._material_tag_and_label(mat_id)
                    self._out(tag, f"      {label_str}")
            elif status == "in_progress":
                turns    = rec.get("turns_remaining", 0)
                npc_name = self._npc_display_name(npc_id)
                self._out(Tag.SYSTEM,
                          f"  {label} ({npc_name}): in progress — ~{turns} moves left")
            elif status == "ready":
                npc_name = self._npc_display_name(npc_id)
                self._out(Tag.SYSTEM,
                          f"  {label} ({npc_name}): READY — return to crafter and 'collect'")
        self._out(Tag.SYSTEM, "")

    def _cmd_give(self, verb: str, args: str) -> None:
        """give <item> to <npc>  — Hand a material to a crafter NPC."""
        # Parse "give <item> to <npc>"
        lower = args.lower()
        if " to " not in lower:
            self._out(Tag.ERROR, "Give what to whom? (e.g. 'give iron ore to dorin')")
            return

        idx       = lower.index(" to ")
        item_part = args[:idx].strip()
        npc_part  = args[idx + 4:].strip().lower()

        # Resolve item in inventory
        item = self._resolve_item_inv(item_part)
        if item is None:
            self._out(Tag.ERROR, f"You aren't carrying anything matching '{item_part}'.")
            return
        if item is self._ALREADY_HANDLED:
            return

        # Resolve NPC in room
        room = self._current_room()
        if not room:
            return
        npc = next(
            (n for n in room.get("_npcs", [])
             if n.get("hp", 1) > 0 and npc_part in n.get("name", "").lower()),
            None
        )
        if not npc:
            self._out(Tag.ERROR, f"There's no '{npc_part}' here.")
            return

        npc_id   = npc.get("id", "")
        npc_name = npc.get("name", "the crafter")
        item_id  = item.get("id", "")

        # Find a pending commission from this NPC that needs this material
        matching = None
        for rec in self.player.commissions:
            if rec.get("npc_id") != npc_id:
                continue
            if rec.get("status") != "waiting_materials":
                continue
            still_needed = crafting_mod.materials_still_needed(rec)
            if item_id in still_needed:
                matching = rec
                break

        # ── Check give_accepts (quest hand-ins and generic gifts) ─────────────
        accepts = npc.get("give_accepts", [])
        for entry in accepts:
            if entry.get("item_id") == item_id:
                self.player.inventory.remove(item)
                msg = entry.get("message", f"You hand {item.get('name', item_id)} to {npc_name}.")
                self._out(Tag.ITEM, msg)
                script_ops = entry.get("script", [])
                if script_ops:
                    from engine.script import ScriptRunner
                    ScriptRunner(self._ctx).run(script_ops)
                return

        if not matching:
            self._out(Tag.ERROR,
                      f"{npc_name} has no commission that needs {item.get('name', item_id)}.")
            return

        # Transfer item
        self.player.inventory.remove(item)
        now_started = crafting_mod.add_material(self.player, matching, item_id)

        self._out(Tag.ITEM,
                  f"You hand {item.get('name', item_id)} to {npc_name}.")

        if now_started:
            turns = matching.get("turns_remaining", 30)
            self._out(Tag.DIALOGUE,
                      f"{npc_name} says: 'Good, I have everything I need. "
                      f"Come back in about {turns} moves.'")
        else:
            still = crafting_mod.materials_still_needed(matching)
            self._out(Tag.DIALOGUE,
                      f"{npc_name} says: 'Thanks. Still need: {', '.join(still)}.'")

    def _cmd_collect(self, verb: str, args: str) -> None:
        """collect  — Pick up a finished commissioned item from a crafter."""
        npc = self._crafter_npc()
        if not npc:
            self._out(Tag.ERROR, "There is no crafter here.")
            return

        npc_id   = npc.get("id", "")
        npc_name = npc.get("name", "the crafter")
        rec      = crafting_mod.find_ready_commission(self.player, npc_id)

        if not rec:
            self._out(Tag.ERROR,
                      f"{npc_name} has nothing ready for you yet. "
                      f"('commissions' to check progress)")
            return

        item, quality = crafting_mod.collect_commission(self.player, rec)
        tier = quality.get("tier", "standard")

        # Custom craft message for special tiers
        craft_msg = quality.get("craft_message", "")
        if craft_msg:
            self._out(Tag.DIALOGUE, f"{npc_name}: '{craft_msg}'")
        else:
            tier_lines = {
                "poor":        f"{npc_name} hands it over without meeting your eyes.",
                "standard":    f"{npc_name} wipes it clean and hands it over.",
                "exceptional": f"{npc_name} holds it up to the light before giving it to you.",
                "masterwork":  f"{npc_name} takes a long moment before letting go of it.",
            }
            self._out(Tag.DIALOGUE, tier_lines.get(tier, f"{npc_name} hands it over."))

        if not self.player.can_carry(item):
            self._out(Tag.ERROR,
                      f"You can't carry {item['name']} — too heavy! "
                      f"Drop something first, then 'collect' again.")
            # Put it back so they can collect later
            self.player.commissions.append({**rec, "status": "ready"})
            return

        self.player.inventory.append(item)
        xp = rec.get("xp_reward", 0)
        if xp:
            leveled = self.player.gain_xp(xp)
            self._out(Tag.SYSTEM, f"  You gain {xp} XP.")
            if leveled:
                self._out(Tag.SYSTEM,
                          f"  You reached level {self.player.level}!")

        self._out(Tag.ITEM, f"  You receive: {item['name']} [{tier}]")
        self._out(Tag.SYSTEM, "")

    def _tick_commissions(self, turns: int) -> None:
        """Advance all in-progress commissions; announce when items become ready."""
        just_ready = crafting_mod.tick_commissions(self.player, turns)
        for label in just_ready:
            self._out(Tag.SYSTEM,
                      f"  [Commission ready] Your {label} is finished! "
                      f"Return to the crafter and 'collect' it.")
