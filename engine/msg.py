"""
msg.py — Structured message system for Delve.

The engine never emits raw strings. Instead it emits Msg objects tagged with a
semantic label. Each frontend decides how to render each tag — ANSI colours for
the CLI, CSS classes for a web frontend, JSON fields for an API, etc.

This clean separation means you can add a new frontend (e.g. a browser-based
client) without touching any engine code at all.

Tag hierarchy
─────────────
  Room / navigation  — what the player sees when they look around
  Entities           — items and NPCs in the world
  Combat             — blow-by-blow fight narration
  Rewards            — XP, gold, and level-up notifications
  Player / stats     — the character sheet
  Quests             — quest start, update, and completion banners
  Economy / shops    — buy/sell listings and confirmations
  Doors / locks      — lock/unlock feedback
  Map                — ASCII map output lines
  System / feedback  — save confirms, help text, errors, meta info

Usage (engine side):
    self._out(Tag.ROOM_NAME, room["name"])
    self._out(Tag.COMBAT_HIT, f"You strike the goblin for 8 damage.")

Usage (frontend side):
    bus.subscribe(Event.OUTPUT, my_renderer)
    def my_renderer(msg: Msg):
        print(COLORS[msg.tag] + msg.text + RESET)
"""

from __future__ import annotations
from dataclasses import dataclass


class Tag:
    # ── Room / navigation ─────────────────────────────────────────
    ROOM_NAME    = "room_name"     # Room title line
    ROOM_DESC    = "room_desc"     # Descriptive prose paragraph
    ROOM_DIVIDER = "room_divider"  # Separator line under the title
    EXIT         = "exit"          # "Exits: north, south [locked]"
    MOVE         = "move"          # "You head north."

    # ── Entities ──────────────────────────────────────────────────
    NPC          = "npc"           # NPC names in room / dialogue speaker
    ITEM         = "item"          # Item names (on ground, in inventory)
    ITEM_EQUIP   = "item_equip"    # Equipped slot annotation in status
    ITEM_HAVE    = "item_have"     # Commission material: player is carrying it
    ITEM_BANK    = "item_bank"     # Commission material: in bank (not on hand)
    ITEM_MISSING = "item_missing"  # Commission material: not found anywhere

    # ── Combat ────────────────────────────────────────────────────
    COMBAT_HIT   = "combat_hit"    # Player successfully hits
    COMBAT_MISS  = "combat_miss"   # Player's attack misses (blind, etc.)
    COMBAT_RECV  = "combat_recv"   # Player receives damage
    COMBAT_KILL  = "combat_kill"   # Enemy defeated
    COMBAT_DEATH = "combat_death"  # Player is killed

    # ── Rewards ───────────────────────────────────────────────────
    REWARD_XP    = "reward_xp"     # XP gain and level-up messages
    REWARD_GOLD  = "reward_gold"   # Gold gained

    # ── Player / stats ────────────────────────────────────────────
    STATS        = "stats"         # Character sheet lines
    DIALOGUE     = "dialogue"      # NPC speech and player response options

    # ── Quests ────────────────────────────────────────────────────
    QUEST        = "quest"         # Quest start, update, and completion banners
    JOURNAL      = "journal"       # Player journal entries written by scripts

    # ── Economy / shops ───────────────────────────────────────────
    SHOP         = "shop"          # Shop listings and buy/sell confirmations

    # ── Doors / locks ─────────────────────────────────────────────
    DOOR         = "door"          # Lock/unlock feedback and blocked-exit messages

    # ── Map ───────────────────────────────────────────────────────
    MAP          = "map"           # ASCII map output lines

    # ── Fighting styles ───────────────────────────────────────────
    STYLE        = "style"         # Style info, proficiency, and passive unlocks

    # ── System / feedback ─────────────────────────────────────────
    SYSTEM       = "system"        # Save confirms, help text, meta info
    ERROR        = "error"         # Error and invalid-command feedback
    BLANK        = "blank"         # Empty line / visual spacer
    AMBIGUOUS    = "ambiguous"     # Multiple matches — disambiguation prompt
    PAUSE        = "pause"         # Cutscene timing pause; text = seconds as a float string


@dataclass
class Msg:
    tag:  str
    text: str

    def __str__(self) -> str:
        """Fallback plain-text representation (no formatting)."""
        return self.text




