"""
player.py — Player state, persistence helpers, and item-effect utilities.

Player state
────────────
All mutable player data lives in a Player instance and serialises to a TOML
file in data/players/<name>.toml. The save/load cycle is explicit — the game
never auto-saves mid-session except when the player types 'save' or 'quit'.

Equipment slots
───────────────
  weapon  — attack-boosting gear (swords, knuckles, daggers)
  armor   — defense-boosting gear (caps, shirts, plate)
  pack    — carry-capacity items (satchels, bags); equip with 'equip <item>'

Weight system
─────────────
  weight      — int "stones" on each item definition
  carry_capacity — base 10 stones; each equipped pack adds its stat_bonus
  current_weight — sum of all inventory item weights

Picking up an item that would exceed carry_capacity is refused with an error.
Scenery items (scenery = true) can never be picked up regardless of weight.

Item effects model
──────────────────
  { type = "stat_bonus",  stat = "attack"|"defense"|"max_hp"|"carry_capacity", amount = N }
  { type = "on_hit",      ability = "bleed"|"stun", chance = 0.0–1.0, magnitude = N }
  { type = "on_equip",    message = "..." }    — flavour text shown on equip
  { type = "on_use",      heal = N }           — for consumables (potions, food)

Quest / flag state
──────────────────
  flags             — set of arbitrary string flags, set/cleared by scripts
  active_quests     — {quest_id: current_step_int}
  completed_quests  — set of finished quest IDs
  npc_dialogue_index — {npc_id:node_id: int} for cycling dialogue lines
  visited_rooms     — set of room IDs the player has entered (for the map)
  looted_items      — set of "room_id:item_id" strings (suppresses re-spawn)
"""

from __future__ import annotations
from pathlib import Path
from engine.toml_io import load as toml_load, dump as toml_dump
import engine.skills as skills_mod

PLAYERS_DIR = Path(__file__).parent.parent / "data" / "players"
BASE_CARRY   = 10   # stones — base before pack bonuses


def item_stat_bonus(item: dict, stat: str) -> int:
    total = 0
    for fx in item.get("effects", []):
        if fx.get("type") == "stat_bonus" and fx.get("stat") == stat:
            total += int(fx.get("amount", 0))
    if stat == "attack"  and not item.get("effects"):
        total += item.get("attack_bonus", 0)
    if stat == "defense" and not item.get("effects"):
        total += item.get("defense_bonus", 0)
    return total


def item_on_hit_effects(item: dict) -> list[dict]:
    return [fx for fx in item.get("effects", []) if fx.get("type") == "on_hit"]


def item_on_use_effect(item: dict) -> dict | None:
    for fx in item.get("effects", []):
        if fx.get("type") == "on_use":
            return fx
    return None


def item_on_equip_message(item: dict) -> str:
    for fx in item.get("effects", []):
        if fx.get("type") == "on_equip":
            return fx.get("message", "")
    return ""


def item_weight(item: dict) -> int:
    return int(item.get("weight", 0))


class Player:
    def __init__(self, name: str):
        self.name       = name
        self.room_id:  str  = ""
        self.hp:       int  = 100
        self.max_hp:   int  = 100
        self.attack:   int  = 5    # base before equipment; use effective_attack for display
        self.defense:  int  = 2    # base before equipment; use effective_defense for display
        self.level:    int  = 1
        self.xp:       int  = 0
        self.xp_next:  int  = 100
        self.gold:     int  = 0
        self.inventory: list[dict]             = []
        self.equipped:  dict[str, dict | None] = {
            "weapon": None,
            "head":   None, "chest": None, "legs": None, "arms": None,
            "armor":  None,
            "pack":   None, "ring":  None,
            "shield": None, "cape":  None,
        }

        # Style system
        self.active_style: str              = "brawling"
        self.known_styles: list[str]        = ["brawling"]
        self.style_prof:   dict[str, float] = {"brawling": 0.0}

        # Item tracking
        self.looted_items:  set[str] = set()
        # Map tracking
        self.visited_rooms: set[str] = set()
        # Script flags (set/cleared by scripts)
        self.flags: set[str] = set()
        # Quest state
        self.active_quests:    dict[str, int] = {}   # quest_id → current step
        self.completed_quests: set[str]        = set()
        # Dialogue cycle tracking (npc_id:node_id → cycle index)
        self.npc_dialogue_index: dict[str, int] = {}

        # Death / respawn
        self.bind_room: str = ""     # room to respawn at (auto-set on town entry)
        self.xp_debt:   int = 0      # XP owed before earning resumes (25% of XP on death)

        # Bank storage — global account, no weight limit, persisted to save file
        self.bank: list[dict]  = []
        self.bank_slots: int   = 10   # base capacity; expandable via banker
        self.banked_gold: int  = 0    # gold stored with banker
        self.prestige: int             = 0    # signed prestige score (-500 to +500)
        self.prestige_affinities: list = []   # earned reputation tags

        # Player-defined aliases: {alias_word: expansion_string}
        # e.g. {"atk": "attack", "h": "help", "gs": "attack goblin"}
        self.aliases: dict[str, str] = {}

        # Adventuring skills (0–100): stealth, survival, perception, athletics, social, arcana
        self.skills: dict[str, float] = skills_mod.default_skills()

        # Status effects: {effect_name: turns_remaining}  (-1 = until manually cleared)
        # Effects: poisoned, blinded, weakened, slowed, protected
        self.status_effects: dict[str, int] = {}

        # Active commissions: list of commission state dicts (see engine/crafting.py)
        self.commissions: list[dict] = []

        # Active companion (at most one at a time).
        # See engine/companion.py for full schema.
        self.companion: dict | None = None

        # Journal entries written by scripts (e.g. wall inscriptions, milestones)
        self.journal: list[dict] = []   # [{"title": str, "text": str}, ...]

    # ── Weight ────────────────────────────────────────────────────────────────

    @property
    def carry_capacity(self) -> int:
        """Carry capacity is base + bonuses from pack-slot items and active companion."""
        bonus = sum(
            item_stat_bonus(item, "carry_capacity")
            for item in self.equipped.values() if item
        )
        import engine.companion as companion_mod
        bonus += companion_mod.carry_bonus(self.companion)
        return BASE_CARRY + bonus

    @property
    def current_weight(self) -> int:
        """Weight of items in inventory only. Equipped items don't count —
        they're on your body, not in your pack."""
        equipped_ids = {id(i) for i in self.equipped.values() if i is not None}
        return sum(item_weight(i) for i in self.inventory
                   if id(i) not in equipped_ids)

    def can_carry(self, item: dict) -> bool:
        return self.current_weight + item_weight(item) <= self.carry_capacity

    # ── Loot tracking ─────────────────────────────────────────────────────────

    def loot_key(self, room_id: str, item_id: str) -> str:
        return f"{room_id}:{item_id}"

    def record_looted(self, room_id: str, item_id: str) -> None:
        self.looted_items.add(self.loot_key(room_id, item_id))

    def has_looted(self, room_id: str, item_id: str) -> bool:
        return self.loot_key(room_id, item_id) in self.looted_items

    # ── Persistence ───────────────────────────────────────────────────────────

    @property
    def _save_path(self) -> Path:
        return PLAYERS_DIR / f"{self.name.lower()}.toml"

    def save(self) -> None:
        data = {
            "name":          self.name,
            "room_id":       self.room_id,
            "hp":            self.hp,
            "max_hp":        self.max_hp,
            "attack":        self.attack,
            "defense":       self.defense,
            "level":         self.level,
            "xp":            self.xp,
            "xp_next":       self.xp_next,
            "gold":          self.gold,
            "inventory":     self.inventory,
            "equipped": {
                slot: item if item else {}
                for slot, item in self.equipped.items()
            },
            "active_style":  self.active_style,
            "known_styles":  self.known_styles,
            "style_prof":    {k: round(v, 2) for k, v in self.style_prof.items()},
            "looted_items":  sorted(self.looted_items),
            "visited_rooms":       sorted(self.visited_rooms),
            "flags":               sorted(self.flags),
            "active_quests":       self.active_quests,
            "completed_quests":    sorted(self.completed_quests),
            "npc_dialogue_index":  self.npc_dialogue_index,
            "bind_room":           self.bind_room,
            "xp_debt":             self.xp_debt,
            "bank":                self.bank,
            "bank_slots":          self.bank_slots,
            "banked_gold":         self.banked_gold,
            "prestige":            self.prestige,
            "prestige_affinities": self.prestige_affinities,
            "aliases":             self.aliases,
            "skills":              {k: round(v, 2) for k, v in self.skills.items()},
            "status_effects":      self.status_effects,
            "commissions":         self._serialise_commissions(),
            "companion":           self._serialise_companion(),
            "journal":             self.journal,
        }
        toml_dump(self._save_path, data)

    @classmethod
    def load(cls, name: str) -> "Player | None":
        path = PLAYERS_DIR / f"{name.lower()}.toml"
        if not path.exists():
            return None
        data = toml_load(path)
        p = cls(data["name"])
        p.room_id       = data.get("room_id", "")
        p.hp            = data.get("hp", 100)
        p.max_hp        = data.get("max_hp", 100)
        p.attack        = data.get("attack", 10)
        p.defense       = data.get("defense", 5)
        p.level         = data.get("level", 1)
        p.xp            = data.get("xp", 0)
        p.xp_next       = data.get("xp_next", 100)
        p.gold          = data.get("gold", 0)
        p.inventory     = data.get("inventory", [])
        equipped        = data.get("equipped", {})
        _ALL_SLOTS = ("weapon", "armor", "pack", "ring", "shield", "cape")
        p.equipped  = {slot: (item if item else None)
                       for slot, item in equipped.items()
                       if slot in _ALL_SLOTS}
        for slot in _ALL_SLOTS:
            p.equipped.setdefault(slot, None)
        # Migrate: ensure new armor slots exist for old saves
        for _new_slot in ("head", "chest", "legs", "arms"):
            p.equipped.setdefault(_new_slot, None)
        p.active_style  = data.get("active_style", "brawling")
        p.known_styles  = data.get("known_styles", ["brawling"])
        raw_prof        = data.get("style_prof", {})
        p.style_prof    = {k: float(v) for k, v in raw_prof.items()} \
                          if isinstance(raw_prof, dict) else {"brawling": 0.0}
        p.looted_items  = set(data.get("looted_items",  []))
        p.visited_rooms       = set(data.get("visited_rooms", []))
        p.flags               = set(data.get("flags", []))
        p.active_quests       = dict(data.get("active_quests", {}))
        p.completed_quests    = set(data.get("completed_quests", []))
        p.npc_dialogue_index  = dict(data.get("npc_dialogue_index", {}))
        p.bind_room           = data.get("bind_room", "")
        p.xp_debt             = data.get("xp_debt", 0)
        p.bank                = data.get("bank", [])
        p.bank_slots          = int(data.get("bank_slots", 10))
        p.banked_gold         = int(data.get("banked_gold", 0))
        p.prestige            = int(data.get("prestige", 0))
        p.prestige_affinities = list(data.get("prestige_affinities", []))
        p.aliases             = dict(data.get("aliases", {}))
        raw_skills            = data.get("skills", {})
        p.skills = skills_mod.default_skills()
        for k, v in raw_skills.items():
            if k in p.skills:
                p.skills[k] = float(v)
        p.status_effects = dict(data.get("status_effects", {}))
        p.commissions = p._deserialise_commissions(data.get("commissions", []))
        p.companion   = p._deserialise_companion(data.get("companion", {}))
        p.journal     = data.get("journal", [])
        return p

    @classmethod
    def exists(cls, name: str) -> bool:
        return (PLAYERS_DIR / f"{name.lower()}.toml").exists()

    # ── Commission persistence helpers ──────────────────────────────────────────

    def _serialise_commissions(self) -> list[dict]:
        """Strip the embedded commission_def (a large dict) before saving to TOML.
        The def is reloaded from data files on load; only the mutable state is stored."""
        out = []
        for rec in self.commissions:
            r = {k: v for k, v in rec.items() if k != "commission_def"}
            out.append(r)
        return out

    def _deserialise_commissions(self, raw: list) -> list[dict]:
        """Re-attach commission_def to each saved commission record."""
        import engine.crafting as crafting_mod
        out = []
        for r in raw:
            cid = r.get("commission_id", "")
            cdef = crafting_mod.commission_by_id(cid)
            if cdef is None:
                continue  # commission removed from data files — drop silently
            out.append({**r, "commission_def": cdef})
        return out

    def _serialise_companion(self) -> dict:
        import engine.companion as companion_mod
        return companion_mod.serialise(self.companion)

    def _deserialise_companion(self, raw: dict) -> "dict | None":
        import engine.companion as companion_mod
        return companion_mod.deserialise(raw)

    # ── Style helpers ─────────────────────────────────────────────────────────

    def learn_style(self, style_id: str) -> None:
        if style_id not in self.known_styles:
            self.known_styles.append(style_id)
        if style_id not in self.style_prof:
            self.style_prof[style_id] = 0.0

    def style_proficiency(self, style_id: str | None = None) -> float:
        sid = style_id or self.active_style
        return self.style_prof.get(sid, 0.0)

    # ── Inventory helpers ─────────────────────────────────────────────────────

    def find_item(self, name: str) -> dict | None:
        name_l = name.lower()
        for item in self.inventory:
            if name_l in item.get("name", "").lower():
                return item
        return None

    def add_item(self, item: dict) -> None:
        self.inventory.append(item)

    def remove_item(self, item: dict) -> None:
        self.inventory.remove(item)

    # ── Combat helpers ────────────────────────────────────────────────────────

    @property
    def effective_attack(self) -> int:
        return self.attack + sum(
            item_stat_bonus(i, "attack") for i in self.equipped.values() if i
        )

    @property
    def effective_defense(self) -> int:
        return self.defense + sum(
            item_stat_bonus(i, "defense") for i in self.equipped.values() if i
        )

    @property
    def effective_max_hp(self) -> int:
        return self.max_hp + sum(
            item_stat_bonus(i, "max_hp") for i in self.equipped.values() if i
        )

    @property
    def is_alive(self) -> bool:
        return self.hp > 0

    def gain_xp(self, amount: int) -> bool:
        """Add XP, draining xp_debt first. Returns True if the player levelled up.

        If xp_debt > 0 (a death penalty is active), all XP earned goes toward
        paying off the debt rather than toward the next level. Once the debt
        reaches zero, normal XP gain resumes in the same kill.
        """
        if self.xp_debt > 0:
            if amount <= self.xp_debt:
                self.xp_debt -= amount
                return False
            # Partially pays off debt; remainder goes to real XP
            amount -= self.xp_debt
            self.xp_debt = 0

        self.xp += amount
        if self.xp >= self.xp_next:
            self.xp      -= self.xp_next
            self.level   += 1
            self.xp_next  = int(self.xp_next * 1.5)
            self.max_hp  += 10
            self.hp       = self.max_hp
            self.attack  += 2
            self.defense += 1
            return True
        return False




