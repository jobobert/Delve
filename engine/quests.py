"""
quests.py — Quest definition loading and state tracking for Delve.

Quest definitions live in data/quests/<quest_id>.toml. Each file describes one
quest: its steps, win conditions, and rewards. Definitions are loaded once
and cached. Player progress (which step they're on, what's complete) lives
entirely in the Player object.

Quest definition format (TOML)
──────────────────────────────
  id      = "ashwood_contract"
  title   = "The Ashwood Contract"
  giver   = "elder_mira"            # NPC id (informational — not enforced)
  summary = "One-line description shown in journal and quest banners."

  [[step]]
  index     = 1                     # Steps are numbered from 1
  objective = "Speak with Sergeant Vorn."
  hint      = "He's in the barracks — go up from the Town Square."

  [[step]]
  index     = 2
  objective = "Enter the Ashwood."
  hint      = "Head south then east past the Deep Forest."

  [[reward]]
  type   = "gold"
  amount = 150

  [[reward]]
  type   = "xp"
  amount = 400

  [[reward]]
  type    = "item"
  item_id = "millhaven_commendation"

Rewards are granted by QuestTracker.complete() via ScriptRunner ops. The
item reward calls give_item, gold calls give_gold, xp calls give_xp.

Player quest state
──────────────────
  player.active_quests    = { "ashwood_contract": 3 }  # quest_id → current step
  player.completed_quests = { "ashwood_contract" }      # set of finished ids

QuestTracker wraps these dicts and provides the full quest-management API.
Scripts and dialogue call tracker methods (start, advance, complete) rather
than touching the player dicts directly, so the tracker can emit notifications.

Quest advancement
─────────────────
Quests typically advance through dialogue scripts:
  { op = "advance_quest", quest_id = "...", step = N }
  { op = "complete_quest", quest_id = "..." }

The final step is usually marked complete by the quest-giver NPC's dialogue.
"""

from __future__ import annotations
import copy
from pathlib import Path
from engine.events import Event
from engine.msg import Msg, Tag
from engine.toml_io import load as toml_load
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.script import GameContext

_DATA_ROOT  = Path(__file__).parent.parent / "data"
_SKIP_DIRS  = {"zone_state", "players"}

_QUEST_CACHE: dict[str, dict] = {}


def load_all() -> dict[str, dict]:
    """
    Scan every zone folder for a quests/ subdirectory and load all *.toml
    files found. First definition of a quest id wins (alphabetical zone order).
    """
    global _QUEST_CACHE
    if _QUEST_CACHE:
        return _QUEST_CACHE
    result: dict[str, dict] = {}
    for zone_folder in sorted(_DATA_ROOT.iterdir()):
        if not zone_folder.is_dir() or zone_folder.name in _SKIP_DIRS:
            continue
        quests_dir = zone_folder / "quests"
        if not quests_dir.exists():
            continue
        for path in sorted(quests_dir.glob("*.toml")):
            try:
                raw = toml_load(path)
            except Exception:
                continue
            qid = raw.get("id", "")
            if qid and qid not in result:
                result[qid] = raw
    _QUEST_CACHE = result
    return result


def get(quest_id: str) -> dict | None:
    return load_all().get(quest_id)


def reload():
    global _QUEST_CACHE
    _QUEST_CACHE = {}
    load_all()


class QuestTracker:
    """Thin wrapper around player quest dicts — handles game logic."""

    def __init__(self, player):
        self.player = player

    # ── Queries ───────────────────────────────────────────────────────────────

    def is_active(self, quest_id: str) -> bool:
        return quest_id in self.player.active_quests

    def is_complete(self, quest_id: str) -> bool:
        return quest_id in self.player.completed_quests

    def current_step(self, quest_id: str) -> int:
        return self.player.active_quests.get(quest_id, 0)

    def at_step(self, quest_id: str, step: int) -> bool:
        return self.player.active_quests.get(quest_id) == step

    def has_started(self, quest_id: str) -> bool:
        return self.is_active(quest_id) or self.is_complete(quest_id)

    # ── Mutations ─────────────────────────────────────────────────────────────

    def start(self, quest_id: str, ctx: "GameContext | None" = None) -> bool:
        """Start a quest at step 1. Returns False if already started."""
        if self.has_started(quest_id):
            return False
        quest = get(quest_id)
        if not quest:
            return False
        self.player.active_quests[quest_id] = 1
        if ctx:
            self._emit_step(quest, 1, ctx, just_started=True)
        return True

    def advance(self, quest_id: str, step: int,
                ctx: "GameContext | None" = None) -> bool:
        """Move quest to a specific step and run on_advance scripts for that step."""
        quest = get(quest_id)
        if not quest or self.is_complete(quest_id):
            return False
        self.player.active_quests[quest_id] = step
        if ctx:
            self._emit_step(quest, step, ctx)
            # Run on_advance script ops for this step if defined
            steps = {s["index"]: s for s in quest.get("step", [])}
            on_advance = steps.get(step, {}).get("on_advance", [])
            if on_advance:
                from engine.script import ScriptRunner
                ScriptRunner(ctx).run(on_advance)
        return True

    def complete(self, quest_id: str, ctx: "GameContext | None" = None) -> bool:
        """Mark quest complete and award rewards."""
        if self.is_complete(quest_id):
            return False
        quest = get(quest_id)
        if not quest:
            return False
        self.player.active_quests.pop(quest_id, None)
        self.player.completed_quests.add(quest_id)
        if ctx:
            self._emit_completion(quest, ctx)
        return True

    # ── Output ────────────────────────────────────────────────────────────────

    def _emit_step(self, quest: dict, step: int,
                   ctx: "GameContext", just_started: bool = False) -> None:
        title = quest.get("title","Quest")
        steps = {s["index"]: s for s in quest.get("step", [])}
        step_data = steps.get(step, {})
        obj   = step_data.get("objective","")
        hint  = step_data.get("hint","")
        if just_started:
            ctx.bus.emit(Event.OUTPUT, Msg(Tag.QUEST,
                f"╔══ Quest: {title} ══"))
            ctx.bus.emit(Event.OUTPUT, Msg(Tag.QUEST,
                f"║  {quest.get('summary','')}"))
            start_msg = quest.get("start_message", "")
            if start_msg:
                ctx.bus.emit(Event.OUTPUT, Msg(Tag.QUEST, f"║  {start_msg}"))
        else:
            ctx.bus.emit(Event.OUTPUT, Msg(Tag.QUEST,
                f"╔══ Quest Updated: {title} ══"))
        if obj:
            ctx.bus.emit(Event.OUTPUT, Msg(Tag.QUEST, f"║  ► {obj}"))
        if hint:
            ctx.bus.emit(Event.OUTPUT, Msg(Tag.QUEST, f"║  Hint: {hint}"))
        ctx.bus.emit(Event.OUTPUT, Msg(Tag.QUEST, "╚" + "═" * 36))

    def _emit_completion(self, quest: dict, ctx: "GameContext") -> None:
        # ScriptRunner is imported locally to avoid a circular import:
        # script.py imports QuestTracker (from quests) under TYPE_CHECKING,
        # so at runtime the dependency is one-way (quests → script only).
        from engine.script import ScriptRunner

        title = quest.get("title","Quest")
        ctx.bus.emit(Event.OUTPUT, Msg(Tag.QUEST,
            f"╔══ Quest Complete: {title} ══"))
        complete_msg = quest.get("complete_message", "Well done, adventurer.")
        ctx.bus.emit(Event.OUTPUT, Msg(Tag.QUEST,
            f"║  {complete_msg}"))

        # Award rewards
        runner = ScriptRunner(ctx)
        for reward in quest.get("reward", []):
            rtype  = reward.get("type","")
            if rtype == "gold":
                runner._exec({"op":"give_gold","amount":reward.get("amount",0)})
            elif rtype == "xp":
                runner._exec({"op":"give_xp","amount":reward.get("amount",0)})
            elif rtype == "item":
                runner._exec({"op":"give_item","item_id":reward.get("item_id","")})

        ctx.bus.emit(Event.OUTPUT, Msg(Tag.QUEST, "╚" + "═" * 36))

    # ── Journal ───────────────────────────────────────────────────────────────

    def journal_lines(self) -> list[tuple[str,str]]:
        """Return list of (tag, text) lines for the journal display."""
        from engine.msg import Tag
        lines = []
        all_quests = load_all()

        active = {qid: self.current_step(qid)
                  for qid in self.player.active_quests}
        completed = self.player.completed_quests

        if not active and not completed:
            lines.append((Tag.SYSTEM, "  You have no quests."))
            return lines

        if active:
            lines.append((Tag.QUEST, "── Active Quests ──────────────────"))
            for qid, step in active.items():
                quest = all_quests.get(qid)
                if not quest:
                    continue
                lines.append((Tag.QUEST, f"  {quest.get('title',qid)}"))
                steps = {s["index"]: s for s in quest.get("step",[])}
                step_data = steps.get(step, {})
                lines.append((Tag.SYSTEM,
                               f"    ► {step_data.get('objective','...')}"))
                hint = step_data.get("hint","")
                if hint:
                    lines.append((Tag.SYSTEM, f"      ({hint})"))

        if completed:
            lines.append((Tag.SYSTEM, ""))
            lines.append((Tag.QUEST, "── Completed ──────────────────────"))
            for qid in sorted(completed):
                quest = all_quests.get(qid)
                title = quest.get("title", qid) if quest else qid
                lines.append((Tag.SYSTEM, f"  ✓ {title}"))

        return lines




