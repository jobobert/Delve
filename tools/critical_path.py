#!/usr/bin/env python3
"""
tools/critical_path.py — Static critical-path analyser for Delve worlds.

Reads a world's TOML data and traces the critical path to completion,
assuming the player wins every fight, collects all reachable items, and
makes all available dialogue choices. Detects blocking issues such as
circular lock/key dependencies, quest steps with no trigger, flags
consumed but never set, and unreachable content.

Usage:
  python tools/critical_path.py --world first_world
  python tools/critical_path.py --world first_world --out report.txt
  python tools/critical_path.py --world first_world --quest ashwood_contract
  python tools/critical_path.py --world first_world --verbose
"""

from __future__ import annotations

import argparse
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).parent.parent
_TOOLS = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_TOOLS))

import engine.world_config as _wc
from engine.map_builder import exit_dest
from engine.toml_io import load as toml_load
from graph_common import (
    find_dialogue_files,
    find_quest_files,
    load_dialogue_tree,
    load_quest,
)

DATA_DIR  = ROOT / "data"
SKIP_DIRS = {"zone_state", "players", "__pycache__"}


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FlagSource:
    flag: str
    kind: str        # "room_enter" | "item_get" | "npc_kill" | "dialogue" | "give_accepts"
    location_id: str # room_id / item_id / npc_id
    node_id: str = ""
    conditions: list[str] = field(default_factory=list)  # prerequisite flags/items


@dataclass
class ItemSource:
    item_id: str
    kind: str        # "room_floor" | "npc_kill" | "dialogue_give" | "give_item_script"
    location_id: str
    conditions: list[str] = field(default_factory=list)


@dataclass
class LockChain:
    room_id: str
    direction: str
    lock_tag: str
    dest_room: str
    key_items: list[str]      # item IDs whose tags include lock_tag
    key_sources: list[ItemSource]
    is_circular: bool = False # True if key is only reachable through the locked door


@dataclass
class Issue:
    severity: str   # "BLOCKING" | "WARNING"
    category: str
    description: str
    detail: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# SimState — monotonic forward simulation state
# ─────────────────────────────────────────────────────────────────────────────

class SimState:
    def __init__(self) -> None:
        self.reachable_rooms: set[str] = set()
        self.flags: set[str]           = set()
        self.items: set[str]           = set()
        self.killed_npcs: set[str]     = set()
        self.quest_steps: dict[str, int] = {}   # quest_id → current step
        self.completed_quests: set[str]  = set()

    def add_flag(self, flag: str) -> bool:
        if flag and flag not in self.flags:
            self.flags.add(flag)
            return True
        return False

    def add_item(self, item_id: str) -> bool:
        if item_id and item_id not in self.items:
            self.items.add(item_id)
            return True
        return False

    def add_room(self, room_id: str) -> bool:
        if room_id and room_id not in self.reachable_rooms:
            self.reachable_rooms.add(room_id)
            return True
        return False

    def advance_quest(self, quest_id: str, step: int) -> bool:
        cur = self.quest_steps.get(quest_id, 0)
        if step > cur:
            self.quest_steps[quest_id] = step
            return True
        return False

    def complete_quest(self, quest_id: str) -> bool:
        if quest_id not in self.completed_quests:
            self.completed_quests.add(quest_id)
            return True
        return False

    def has_item_with_tag(self, tag: str, items_raw: dict) -> bool:
        for iid in self.items:
            if tag in items_raw.get(iid, {}).get("tags", []):
                return True
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CriticalPathAnalyzer
# ─────────────────────────────────────────────────────────────────────────────

class CriticalPathAnalyzer:

    def __init__(self, world_path: Path, verbose: bool = False) -> None:
        self.world_path = world_path
        self.verbose    = verbose

        # Raw data indexes
        self.rooms_raw:     dict[str, dict] = {}   # room_id → full dict
        self.items_raw:     dict[str, dict] = {}   # item_id → full dict
        self.npcs_raw:      dict[str, dict] = {}   # npc_id  → full dict
        self.npc_rooms:     dict[str, str]  = {}   # npc_id  → room_id
        self.quests_raw:    dict[str, dict] = {}   # quest_id → full dict
        self.dialogues_raw: dict[str, dict] = {}   # npc_id  → {node_id: node_dict}

        # Dependency indexes
        self.flag_sources:  dict[str, list[FlagSource]]  = {}
        self.item_sources:  dict[str, list[ItemSource]]  = {}
        self.lock_chains:   list[LockChain]              = []

        # What advance_quest/complete_quest triggers exist per (quest_id, step)
        # step 999 = complete_quest
        self.quest_triggers: dict[tuple, list[dict]] = {}

        self.start_room: str = ""

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 1 — Load data
    # ─────────────────────────────────────────────────────────────────────────

    def _zone_dirs(self) -> list[Path]:
        return sorted(
            p for p in self.world_path.iterdir()
            if p.is_dir() and p.name not in SKIP_DIRS
        )

    def _load_all(self) -> None:
        for zone_dir in self._zone_dirs():
            self._load_zone(zone_dir)

        # Load dialogues
        for npc_id, path in find_dialogue_files(self.world_path).items():
            try:
                self.dialogues_raw[npc_id] = load_dialogue_tree(path)
            except Exception:
                pass

        # Load quests
        for quest_id, path in find_quest_files(self.world_path).items():
            try:
                self.quests_raw[quest_id] = load_quest(path)
            except Exception:
                pass

        # Supplement npc_rooms from zone_state JSON (e.g. garrison_ghost)
        zone_state_dir = self.world_path / "zone_state"
        if zone_state_dir.is_dir():
            import json
            for jp in sorted(zone_state_dir.glob("*.json")):
                try:
                    with open(jp) as f:
                        data = json.load(f)
                    for room_id, rdata in data.items():
                        for npc in rdata.get("npcs", []):
                            nid = npc.get("id", "")
                            if nid and nid not in self.npc_rooms:
                                self.npc_rooms[nid] = room_id
                except Exception:
                    pass

        # Find start room
        for rid, room in self.rooms_raw.items():
            if room.get("start"):
                self.start_room = rid
                break
        if not self.start_room and self.rooms_raw:
            self.start_room = next(iter(self.rooms_raw))

    def _load_zone(self, zone_dir: Path) -> None:
        for toml_path in sorted(zone_dir.glob("*.toml")):
            try:
                data = toml_load(toml_path)
            except Exception:
                continue

            for room in data.get("room", []):
                rid = room.get("id", "")
                if rid:
                    self.rooms_raw[rid] = room

            for item in data.get("item", []):
                iid = item.get("id", "")
                if iid:
                    self.items_raw[iid] = item
                    # Index floor item sources
                    pass  # done in _build_item_sources

            for npc in data.get("npc", []):
                nid = npc.get("id", "")
                if nid:
                    self.npcs_raw[nid] = npc

        # Build npc_rooms from room spawns
        for rid, room in self.rooms_raw.items():
            for spawn in room.get("spawns", []):
                nid = spawn if isinstance(spawn, str) else spawn.get("id", "")
                if nid and nid not in self.npc_rooms:
                    self.npc_rooms[nid] = rid

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 2 — Build dependency graph
    # ─────────────────────────────────────────────────────────────────────────

    def _build_deps(self) -> None:
        # Flag sources from rooms
        for rid, room in self.rooms_raw.items():
            for src in self._effects_from_script(
                room.get("on_enter", []), "room_enter", rid, []
            ):
                self.flag_sources.setdefault(src.flag, []).append(src)

        # Flag + item sources from items (both on_get and on_use)
        for iid, item in self.items_raw.items():
            for script_key in ("on_get", "on_use"):
                for src in self._effects_from_script(
                    item.get(script_key, []), "item_get", iid, []
                ):
                    self.flag_sources.setdefault(src.flag, []).append(src)

        # Flag + item sources from NPCs (kill_script, give_accepts)
        for nid, npc in self.npcs_raw.items():
            for src in self._effects_from_script(
                npc.get("kill_script", []), "npc_kill", nid, []
            ):
                self.flag_sources.setdefault(src.flag, []).append(src)
            for ga in npc.get("give_accepts", []):
                for src in self._effects_from_script(
                    ga.get("script", []), "give_accepts", nid, []
                ):
                    self.flag_sources.setdefault(src.flag, []).append(src)

        # Flag + item sources from dialogues
        for npc_id, nodes in self.dialogues_raw.items():
            for node_id, node in nodes.items():
                for src in self._effects_from_script(
                    node.get("script", []), "dialogue", npc_id, [], node_id=node_id
                ):
                    self.flag_sources.setdefault(src.flag, []).append(src)
                for resp in node.get("response", []):
                    for src in self._effects_from_script(
                        resp.get("script", []), "dialogue", npc_id, [], node_id=node_id
                    ):
                        self.flag_sources.setdefault(src.flag, []).append(src)

        # Quest on_advance scripts
        for qid, quest in self.quests_raw.items():
            for step in quest.get("step", []):
                for src in self._effects_from_script(
                    step.get("on_advance", []), "dialogue", qid, []
                ):
                    self.flag_sources.setdefault(src.flag, []).append(src)
                # completion_flag is set when this step is reached
                cf = step.get("completion_flag", "")
                if cf:
                    src = FlagSource(flag=cf, kind="quest_step", location_id=qid)
                    self.flag_sources.setdefault(cf, []).append(src)

        # Item sources
        self._build_item_sources()

        # Lock chains
        self._build_lock_chains()

        # Quest triggers (advance_quest / complete_quest in all script contexts)
        self._build_quest_triggers()

    def _effects_from_script(
        self,
        ops: list,
        kind: str,
        location_id: str,
        conditions: list[str],
        node_id: str = "",
    ) -> list[FlagSource]:
        results: list[FlagSource] = []
        for op in ops:
            if not isinstance(op, dict):
                continue
            name = op.get("op", "")
            if name == "set_flag":
                flag = op.get("flag", "")
                if flag:
                    results.append(FlagSource(
                        flag=flag, kind=kind, location_id=location_id,
                        node_id=node_id, conditions=list(conditions)
                    ))
            elif name in ("if_flag", "if"):
                branch_flag = op.get("flag", "")
                inner_conds_then = conditions + ([f"has:{branch_flag}"] if branch_flag else [])
                inner_conds_else = conditions + ([f"not:{branch_flag}"] if branch_flag else [])
                results.extend(self._effects_from_script(
                    op.get("then", []), kind, location_id, inner_conds_then, node_id
                ))
                results.extend(self._effects_from_script(
                    op.get("else", []), kind, location_id, inner_conds_else, node_id
                ))
            elif name == "if_not_flag":
                branch_flag = op.get("flag", "")
                inner_conds = conditions + ([f"not:{branch_flag}"] if branch_flag else [])
                results.extend(self._effects_from_script(
                    op.get("then", []), kind, location_id, inner_conds, node_id
                ))
                results.extend(self._effects_from_script(
                    op.get("else", []), kind, location_id, conditions, node_id
                ))
            elif name == "skill_check":
                results.extend(self._effects_from_script(
                    op.get("on_pass", []), kind, location_id,
                    conditions + ["skill_check"], node_id
                ))
        return results

    def _build_item_sources(self) -> None:
        # Room floor items
        for rid, room in self.rooms_raw.items():
            for entry in room.get("items", []):
                iid = entry if isinstance(entry, str) else entry.get("id", "")
                if iid:
                    src = ItemSource(item_id=iid, kind="room_floor", location_id=rid)
                    self.item_sources.setdefault(iid, []).append(src)

        # give_item ops in all scripts
        for rid, room in self.rooms_raw.items():
            self._scan_give_items(room.get("on_enter", []), "room_enter", rid)
        for iid, item in self.items_raw.items():
            self._scan_give_items(item.get("on_get", []), "item_get", iid)
            self._scan_give_items(item.get("on_use", []), "item_use", iid)
        for nid, npc in self.npcs_raw.items():
            self._scan_give_items(npc.get("kill_script", []), "npc_kill", nid)
            for ga in npc.get("give_accepts", []):
                self._scan_give_items(ga.get("script", []), "give_accepts", nid)
        for npc_id, nodes in self.dialogues_raw.items():
            for node_id, node in nodes.items():
                self._scan_give_items(node.get("script", []), "dialogue", npc_id)
                for resp in node.get("response", []):
                    self._scan_give_items(resp.get("script", []), "dialogue", npc_id)

    def _scan_give_items(self, ops: list, kind: str, location_id: str) -> None:
        for op in ops:
            if not isinstance(op, dict):
                continue
            name = op.get("op", "")
            if name == "give_item":
                iid = op.get("item_id", "")
                if iid:
                    src = ItemSource(item_id=iid, kind="give_item_script", location_id=location_id)
                    self.item_sources.setdefault(iid, []).append(src)
            elif name in ("if_flag", "if", "if_not_flag", "skill_check"):
                for branch in ("then", "else", "on_pass", "on_fail"):
                    self._scan_give_items(op.get(branch, []), kind, location_id)

    def _build_lock_chains(self) -> None:
        for rid, room in self.rooms_raw.items():
            for direction, ev in room.get("exits", {}).items():
                if not isinstance(ev, dict):
                    continue
                if not ev.get("locked"):
                    continue
                lock_tag = ev.get("lock_tag", "")
                if not lock_tag:
                    continue
                dest = exit_dest(ev)
                key_items = [
                    iid for iid, item in self.items_raw.items()
                    if lock_tag in item.get("tags", [])
                ]
                key_sources: list[ItemSource] = []
                for kid in key_items:
                    key_sources.extend(self.item_sources.get(kid, []))
                self.lock_chains.append(LockChain(
                    room_id=rid, direction=direction, lock_tag=lock_tag,
                    dest_room=dest, key_items=key_items, key_sources=key_sources
                ))

    def _build_quest_triggers(self) -> None:
        """Scan all scripts for advance_quest / complete_quest ops."""
        def _scan(ops: list, ctx: str) -> None:
            for op in ops:
                if not isinstance(op, dict):
                    continue
                name = op.get("op", "")
                qid  = op.get("quest_id", "")
                if name == "advance_quest" and qid:
                    key = (qid, int(op.get("step", 0)))
                    self.quest_triggers.setdefault(key, []).append({"ctx": ctx})
                elif name == "complete_quest" and qid:
                    key = (qid, 999)
                    self.quest_triggers.setdefault(key, []).append({"ctx": ctx})
                for branch in ("then", "else", "on_pass", "on_fail"):
                    _scan(op.get(branch, []), ctx)

        for rid, room in self.rooms_raw.items():
            _scan(room.get("on_enter", []), f"room:{rid}")
        for iid, item in self.items_raw.items():
            _scan(item.get("on_get", []),  f"item_get:{iid}")
            _scan(item.get("on_use", []),  f"item_use:{iid}")
        for nid, npc in self.npcs_raw.items():
            _scan(npc.get("kill_script", []), f"npc_kill:{nid}")
            for ga in npc.get("give_accepts", []):
                _scan(ga.get("script", []), f"give_accepts:{nid}")
        for npc_id, nodes in self.dialogues_raw.items():
            for node_id, node in nodes.items():
                _scan(node.get("script", []), f"dlg:{npc_id}/{node_id}")
                for resp in node.get("response", []):
                    _scan(resp.get("script", []), f"dlg:{npc_id}/{node_id}/resp")
        for qid, quest in self.quests_raw.items():
            for step in quest.get("step", []):
                _scan(step.get("on_advance", []), f"quest_on_advance:{qid}")

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 3 — Forward simulation (monotonic fixed-point BFS)
    # ─────────────────────────────────────────────────────────────────────────

    def _simulate(self) -> SimState:
        state = SimState()
        if self.start_room:
            state.add_room(self.start_room)

        changed = True
        iteration = 0
        while changed:
            changed = False
            iteration += 1

            # 1. For each reachable room, fire on_enter scripts
            for room_id in list(state.reachable_rooms):
                room = self.rooms_raw.get(room_id, {})
                if self._apply_script(room.get("on_enter", []), state, room_id):
                    changed = True

            # 2. Expand via exits
            for room_id in list(state.reachable_rooms):
                room = self.rooms_raw.get(room_id, {})
                for direction, ev in room.get("exits", {}).items():
                    dest = exit_dest(ev)
                    if not dest or dest in state.reachable_rooms:
                        continue
                    if self._exit_traversable(ev, state):
                        if state.add_room(dest):
                            changed = True

            # 3. Collect floor items and fire on_get
            for room_id in list(state.reachable_rooms):
                room = self.rooms_raw.get(room_id, {})
                for entry in room.get("items", []):
                    iid = entry if isinstance(entry, str) else entry.get("id", "")
                    if not iid or iid in state.items:
                        continue
                    item = self.items_raw.get(iid, {})
                    if item.get("scenery"):
                        continue
                    if state.add_item(iid):
                        changed = True
                    if self._apply_script(item.get("on_get", []), state, iid):
                        changed = True

            # 3b. Apply on_use for scenery items in reachable rooms (player will interact)
            for room_id in list(state.reachable_rooms):
                room = self.rooms_raw.get(room_id, {})
                for entry in room.get("items", []):
                    iid = entry if isinstance(entry, str) else entry.get("id", "")
                    if not iid:
                        continue
                    item = self.items_raw.get(iid, {})
                    if item.get("scenery") and item.get("on_use"):
                        if self._apply_script(item.get("on_use", []), state, iid):
                            changed = True

            # 3c. Apply on_use for carried items (player eventually uses all items)
            for iid in list(state.items):
                item = self.items_raw.get(iid, {})
                if not item.get("scenery") and item.get("on_use"):
                    if self._apply_script(item.get("on_use", []), state, iid):
                        changed = True

            # 4. Kill hostile NPCs
            for nid, npc in self.npcs_raw.items():
                if not npc.get("hostile"):
                    continue
                if nid in state.killed_npcs:
                    continue
                npc_room = self.npc_rooms.get(nid, "")
                if npc_room not in state.reachable_rooms:
                    continue
                state.killed_npcs.add(nid)
                changed = True
                if self._apply_script(npc.get("kill_script", []), state, nid):
                    changed = True

            # 5. Walk reachable NPC dialogues
            for npc_id, nodes in self.dialogues_raw.items():
                npc_room = self.npc_rooms.get(npc_id, "")
                if npc_room not in state.reachable_rooms:
                    continue
                for node_id, node in nodes.items():
                    cond = node.get("condition")
                    if not self._cond_satisfied(cond, state):
                        continue
                    if self._apply_script(node.get("script", []), state, npc_id):
                        changed = True
                    for resp in node.get("response", []):
                        if not self._cond_satisfied(resp.get("condition"), state):
                            continue
                        if self._apply_script(resp.get("script", []), state, npc_id):
                            changed = True

            # 6. Process give_accepts when item is in inventory
            for nid, npc in self.npcs_raw.items():
                npc_room = self.npc_rooms.get(nid, "")
                if npc_room not in state.reachable_rooms:
                    continue
                for ga in npc.get("give_accepts", []):
                    req_item = ga.get("item_id", "")
                    if req_item and req_item in state.items:
                        if self._apply_script(ga.get("script", []), state, nid):
                            changed = True

        if self.verbose:
            print(
                f"  [sim] {iteration} iterations, "
                f"{len(state.reachable_rooms)} rooms reachable, "
                f"{len(state.flags)} flags, "
                f"{len(state.items)} items",
                file=sys.stderr,
            )

        return state

    def _apply_script(self, ops: list, state: SimState, ctx: str) -> bool:
        """Apply script ops to state; return True if state changed."""
        changed = False
        for op in ops:
            if not isinstance(op, dict):
                continue
            name = op.get("op", "")
            if name == "set_flag":
                if state.add_flag(op.get("flag", "")):
                    changed = True
            elif name == "give_item":
                iid = op.get("item_id", "")
                if iid and state.add_item(iid):
                    changed = True
                    # Fire on_get for given item
                    item = self.items_raw.get(iid, {})
                    if self._apply_script(item.get("on_get", []), state, iid):
                        changed = True
            elif name == "advance_quest":
                qid  = op.get("quest_id", "")
                step = int(op.get("step", 0))
                if qid and state.advance_quest(qid, step):
                    changed = True
                    # Apply on_advance for this step
                    quest = self.quests_raw.get(qid, {})
                    for s in quest.get("step", []):
                        if s.get("index") == step:
                            cf = s.get("completion_flag", "")
                            if cf and state.add_flag(cf):
                                changed = True
                            if self._apply_script(s.get("on_advance", []), state, qid):
                                changed = True
            elif name == "complete_quest":
                qid = op.get("quest_id", "")
                if qid and state.complete_quest(qid):
                    changed = True
            elif name in ("if_flag", "if"):
                flag = op.get("flag", "")
                branch = op.get("then", []) if (not flag or flag in state.flags) else op.get("else", [])
                if self._apply_script(branch, state, ctx):
                    changed = True
            elif name == "if_not_flag":
                flag = op.get("flag", "")
                branch = op.get("then", []) if (not flag or flag not in state.flags) else op.get("else", [])
                if self._apply_script(branch, state, ctx):
                    changed = True
            elif name == "skill_check":
                # Optimistic: always take on_pass branch
                if self._apply_script(op.get("on_pass", []), state, ctx):
                    changed = True
            elif name == "teleport_player":
                dest = op.get("room_id", "")
                if dest and state.add_room(dest):
                    changed = True
        return changed

    def _exit_traversable(self, ev, state: SimState) -> bool:
        """Return True if this exit can be traversed given current state."""
        if isinstance(ev, str):
            return True
        if not isinstance(ev, dict):
            return True

        # show_if condition
        show_if = ev.get("show_if")
        if show_if:
            if not self._eval_show_if(show_if, state):
                return False

        # locked door
        if ev.get("locked"):
            lock_tag = ev.get("lock_tag", "")
            if lock_tag and not state.has_item_with_tag(lock_tag, self.items_raw):
                return False

        return True

    def _eval_show_if(self, cond, state: SimState) -> bool:
        if isinstance(cond, str):
            if ":" in cond:
                op_part, _, val = cond.partition(":")
                op_part = op_part.strip()
                val = val.strip()
                if op_part == "has_flag":
                    return val in state.flags
                if op_part == "not_flag":
                    return val not in state.flags
            else:
                # bare flag name → has_flag
                return cond.strip() in state.flags
        elif isinstance(cond, dict):
            op = cond.get("op", "")
            if op == "has_flag":
                return cond.get("flag", "") in state.flags
            if op == "not_flag":
                return cond.get("flag", "") not in state.flags
            if op == "min_level":
                return True   # assume player eventually levels
            if op == "has_item":
                return cond.get("item_id", "") in state.items
            if op == "min_skill":
                return True   # assume player eventually skilled
        return True  # unknown → don't block

    def _cond_satisfied(self, cond, state: SimState) -> bool:
        """Evaluate a dialogue condition dict against current state."""
        if not cond:
            return True
        if isinstance(cond, dict):
            flag = cond.get("flag")
            if flag and flag not in state.flags:
                return False
            not_flag = cond.get("not_flag")
            if not_flag and not_flag in state.flags:
                return False
            item = cond.get("item")
            if item and item not in state.items:
                return False
            quest_id = cond.get("quest")
            if quest_id:
                req_step = int(cond.get("step", 1))
                if state.quest_steps.get(quest_id, 0) < req_step:
                    return False
            qc = cond.get("quest_complete")
            if qc and qc not in state.completed_quests:
                return False
            nq = cond.get("not_quest")
            if nq and nq in state.quest_steps:
                return False
            # gold, level_gte, skill, prestige → optimistic pass
        return True

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 4 — Issue detection
    # ─────────────────────────────────────────────────────────────────────────

    def _detect_issues(self, state: SimState) -> list[Issue]:
        issues: list[Issue] = []

        # 1. Locked exits with no key or circular dependency
        for chain in self.lock_chains:
            dest = chain.dest_room
            if not chain.key_items:
                issues.append(Issue(
                    severity="BLOCKING", category="locked_door",
                    description=f"Locked exit {chain.room_id} → {chain.direction} "
                                f"(lock_tag: {chain.lock_tag}) — no item with this tag exists.",
                    detail=f"Destination: {dest or '?'}"
                ))
                continue

            # Check if key is obtainable without this door
            key_obtainable = any(
                k in state.items for k in chain.key_items
            )
            if not key_obtainable and dest in state.reachable_rooms:
                # Dest is reachable but key isn't — door must have been bypassed
                pass
            elif not key_obtainable and dest not in state.reachable_rooms:
                # Check if key sources exist outside the locked door's destination
                has_external_source = False
                dest_zone_rooms = self._rooms_beyond(chain.room_id, chain.direction)
                for src in chain.key_sources:
                    src_room = self._source_room(src)
                    if src_room and src_room not in dest_zone_rooms:
                        has_external_source = True
                        break
                if not has_external_source:
                    issues.append(Issue(
                        severity="BLOCKING", category="locked_door",
                        description=f"Possible circular lock: {chain.room_id} → {chain.direction} "
                                    f"(lock_tag: {chain.lock_tag})",
                        detail=f"Key items: {', '.join(chain.key_items)}. "
                               f"All key sources appear to be behind the locked door. "
                               f"If intentional (room inaccessible by design), this is OK."
                    ))

        # 2. Quest steps with no trigger anywhere in TOML
        for qid, quest in self.quests_raw.items():
            steps = sorted(quest.get("step", []), key=lambda s: s.get("index", 0))
            for step in steps:
                idx = step.get("index", 0)
                key = (qid, idx)
                triggers = self.quest_triggers.get(key, [])
                if not triggers:
                    # No advance_quest for this step — check if maybe it's step 1 with a giver
                    giver = quest.get("giver", "")
                    if idx == 1 and giver:
                        # Step 1 can be triggered by talking to giver — not a blocker
                        continue
                    issues.append(Issue(
                        severity="WARNING", category="quest_step",
                        description=f"Quest '{quest.get('title', qid)}' step {idx} "
                                    f"has no advance_quest trigger in any TOML script.",
                        detail=f"Objective: {step.get('objective', '')}. "
                               f"This step may be triggered by room entry or item pickup "
                               f"not yet reflected in the data, or the trigger is missing."
                    ))

            # Check completion
            comp_key = (qid, 999)
            if not self.quest_triggers.get(comp_key):
                issues.append(Issue(
                    severity="WARNING", category="quest_completion",
                    description=f"Quest '{quest.get('title', qid)}' has no complete_quest trigger.",
                    detail="The quest may never be completable, or it auto-completes on last step advance."
                ))

        # 3. Flags consumed but never produced
        all_consumed_flags: set[str] = set()
        # Collect from dialogue conditions
        for npc_id, nodes in self.dialogues_raw.items():
            for node_id, node in nodes.items():
                cond = node.get("condition", {})
                if isinstance(cond, dict):
                    for key in ("flag", "not_flag"):
                        f = cond.get(key, "")
                        if f:
                            all_consumed_flags.add(f)
                for resp in node.get("response", []):
                    rcond = resp.get("condition", {})
                    if isinstance(rcond, dict):
                        for key in ("flag", "not_flag"):
                            f = rcond.get(key, "")
                            if f:
                                all_consumed_flags.add(f)
        # Collect from exit show_if
        for rid, room in self.rooms_raw.items():
            for direction, ev in room.get("exits", {}).items():
                if isinstance(ev, dict):
                    show_if = ev.get("show_if")
                    f = self._flag_from_show_if(show_if)
                    if f:
                        all_consumed_flags.add(f)
        # Collect from if_flag/if_not_flag ops (all scripts)
        # (less critical to catch all; focus on show_if + dialogue conditions)

        for flag in sorted(all_consumed_flags):
            if flag not in self.flag_sources:
                issues.append(Issue(
                    severity="WARNING", category="orphan_flag",
                    description=f"Flag '{flag}' is checked in conditions but never set by any script.",
                    detail="This gate can never open. Either add a set_flag op somewhere "
                           "or remove the condition."
                ))

        # 4. Unreachable rooms
        unreachable = [
            rid for rid in self.rooms_raw
            if rid not in state.reachable_rooms
        ]
        if unreachable:
            issues.append(Issue(
                severity="WARNING", category="unreachable_rooms",
                description=f"{len(unreachable)} room(s) not reachable from start.",
                detail="Rooms: " + ", ".join(sorted(unreachable)[:20])
                       + (" ..." if len(unreachable) > 20 else "")
            ))

        # 5. Quest steps not reached by simulation
        for qid, quest in self.quests_raw.items():
            steps = sorted(quest.get("step", []), key=lambda s: s.get("index", 0))
            max_step = max((s.get("index", 0) for s in steps), default=0)
            reached_step = state.quest_steps.get(qid, 0)
            if qid in state.completed_quests:
                continue
            if max_step > 0 and reached_step < max_step:
                issues.append(Issue(
                    severity="WARNING", category="quest_incomplete",
                    description=f"Quest '{quest.get('title', qid)}' only reached step "
                                f"{reached_step}/{max_step} in simulation.",
                    detail="Some steps may depend on content outside the starting zone, "
                           "or triggers are gated behind flags not reachable in simulation."
                ))

        return issues

    def _source_room(self, src: ItemSource) -> str:
        """Return the room_id where an item source is located."""
        if src.kind == "room_floor":
            return src.location_id
        if src.kind == "npc_kill":
            return self.npc_rooms.get(src.location_id, "")
        if src.kind == "dialogue":
            return self.npc_rooms.get(src.location_id, "")
        if src.kind == "give_accepts":
            return self.npc_rooms.get(src.location_id, "")
        return ""

    def _rooms_beyond(self, start_room: str, direction: str) -> set[str]:
        """BFS to find all rooms reachable through a specific door (ignoring locks)."""
        dest = exit_dest(self.rooms_raw.get(start_room, {}).get("exits", {}).get(direction, ""))
        if not dest:
            return set()
        visited = {dest}
        queue = deque([dest])
        while queue:
            rid = queue.popleft()
            room = self.rooms_raw.get(rid, {})
            for ev in room.get("exits", {}).values():
                d = exit_dest(ev)
                if d and d not in visited:
                    visited.add(d)
                    queue.append(d)
        return visited

    def _flag_from_show_if(self, show_if) -> str:
        if isinstance(show_if, str):
            if ":" in show_if:
                _, _, flag = show_if.partition(":")
                return flag.strip()
            return show_if.strip()
        elif isinstance(show_if, dict):
            return show_if.get("flag", "")
        return ""

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 5 — Report rendering
    # ─────────────────────────────────────────────────────────────────────────

    def _render_report(self, state: SimState, issues: list[Issue]) -> str:
        lines: list[str] = []
        sep = "=" * 68

        world_name = _wc.WORLD_NAME if hasattr(_wc, "WORLD_NAME") else self.world_path.name
        lines += [
            sep,
            f"CRITICAL PATH ANALYSIS  —  {world_name} ({self.world_path.name})",
            sep,
            f"Rooms:     {len(self.rooms_raw):>4}  reachable: {len(state.reachable_rooms)}",
            f"Quests:    {len(self.quests_raw):>4}  completed in sim: {len(state.completed_quests)}",
            f"Items:     {len(self.items_raw):>4}  collected in sim: {len(state.items)}",
            f"NPCs:      {len(self.npcs_raw):>4}  killed in sim:    {len(state.killed_npcs)}",
            f"Flags set: {len(state.flags):>4}",
            f"Start room: {self.start_room}",
            "",
        ]

        # ── Quest critical path ───────────────────────────────────────────────
        lines.append("─" * 68)
        lines.append("SECTION 1: QUEST CRITICAL PATH")
        lines.append("─" * 68)
        lines.append("(Each quest's trigger chain, in dependency order)")
        lines.append("")

        if not self.quests_raw:
            lines.append("  No quests found.")
        else:
            for qid, quest in sorted(self.quests_raw.items()):
                title  = quest.get("title", qid)
                giver  = quest.get("giver", "")
                steps  = sorted(quest.get("step", []), key=lambda s: s.get("index", 0))
                done   = qid in state.completed_quests
                step_r = state.quest_steps.get(qid, 0)

                status = "COMPLETE" if done else (f"reached step {step_r}" if step_r else "not started")
                lines.append(f"[Quest] {title} ({qid})  [{status}]")
                if giver:
                    giver_room = self.npc_rooms.get(giver, "")
                    lines.append(f"  Giver: {giver}" + (f" @ {giver_room}" if giver_room else ""))

                for step in steps:
                    idx = step.get("index", 0)
                    obj = step.get("objective", "")
                    cf  = step.get("completion_flag", "")
                    triggers = self.quest_triggers.get((qid, idx), [])
                    lines.append(f"  Step {idx}: {obj}")
                    if cf:
                        lines.append(f"    Sets flag: {cf}")
                    if triggers:
                        for t in triggers[:3]:  # limit to 3
                            lines.append(f"    Trigger: {t['ctx']}")
                        if len(triggers) > 3:
                            lines.append(f"    ... and {len(triggers)-3} more")
                    else:
                        if idx == 1 and giver:
                            lines.append(f"    Trigger: talk to {giver} (quest giver)")
                        else:
                            lines.append(f"    Trigger: NONE FOUND in TOML")

                # Completion trigger
                comp_triggers = self.quest_triggers.get((qid, 999), [])
                if comp_triggers:
                    lines.append("  Completion:")
                    for t in comp_triggers[:2]:
                        lines.append(f"    Trigger: {t['ctx']}")
                lines.append("")

        # ── Lock/key chains ───────────────────────────────────────────────────
        lines.append("─" * 68)
        lines.append("SECTION 2: LOCK / KEY CHAIN ANALYSIS")
        lines.append("─" * 68)
        if not self.lock_chains:
            lines.append("  No locked exits found.")
        else:
            for chain in sorted(self.lock_chains, key=lambda c: (c.room_id, c.direction)):
                key_obtainable = any(k in state.items for k in chain.key_items)
                if not chain.key_items:
                    status = "[NO KEY]"
                elif key_obtainable:
                    status = "[OK]"
                else:
                    status = "[BLOCKED]"
                lines.append(
                    f"  {status} {chain.room_id} → {chain.direction}  "
                    f"(lock_tag: {chain.lock_tag})"
                )
                if chain.dest_room:
                    lines.append(f"    Destination: {chain.dest_room}")
                if chain.key_items:
                    lines.append(f"    Key items: {', '.join(chain.key_items)}")
                    for src in chain.key_sources[:3]:
                        lines.append(f"    Key source: {src.kind} @ {src.location_id}")
                lines.append("")

        # ── Flag analysis ─────────────────────────────────────────────────────
        lines.append("─" * 68)
        lines.append("SECTION 3: FLAG ANALYSIS")
        lines.append("─" * 68)
        lines.append(f"  Flags set in simulation ({len(state.flags)}):")
        flag_list = sorted(state.flags)
        for i in range(0, len(flag_list), 4):
            lines.append("    " + "  ".join(flag_list[i:i+4]))
        lines.append("")
        orphan_flags = [
            i.description.split("'")[1]
            for i in issues if i.category == "orphan_flag"
        ]
        if orphan_flags:
            lines.append(f"  Orphan flags (consumed but never set) ({len(orphan_flags)}):")
            for f in orphan_flags:
                lines.append(f"    {f}")
            lines.append("")

        # ── Unreachable rooms ─────────────────────────────────────────────────
        unreachable_issue = next(
            (i for i in issues if i.category == "unreachable_rooms"), None
        )
        if unreachable_issue:
            lines.append("─" * 68)
            lines.append("SECTION 4: UNREACHABLE ROOMS")
            lines.append("─" * 68)
            lines.append(f"  {unreachable_issue.description}")
            lines.append(f"  {unreachable_issue.detail}")
            lines.append("")

        # ── Issues ────────────────────────────────────────────────────────────
        blocking = [i for i in issues if i.severity == "BLOCKING"]
        warnings = [i for i in issues if i.severity == "WARNING" and i.category != "unreachable_rooms"]

        lines.append("─" * 68)
        lines.append(f"SECTION 5: ISSUES  ({len(blocking)} blocking, {len(warnings)} warnings)")
        lines.append("─" * 68)
        if not blocking and not warnings:
            lines.append("  No issues found. Critical path appears complete.")
        else:
            if blocking:
                lines.append(f"\n  BLOCKING ({len(blocking)}):")
                for iss in blocking:
                    lines.append(f"  [BLOCKING] [{iss.category}] {iss.description}")
                    if iss.detail:
                        lines.append(f"    {iss.detail}")
            if warnings:
                lines.append(f"\n  WARNINGS ({len(warnings)}):")
                for iss in warnings:
                    lines.append(f"  [WARN] [{iss.category}] {iss.description}")
                    if iss.detail:
                        lines.append(f"    {iss.detail}")

        lines.append("")
        lines.append(sep)
        lines.append("END OF REPORT")
        lines.append(sep)
        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ─────────────────────────────────────────────────────────────────────────

    def run(self) -> str:
        if self.verbose:
            print("Loading world data...", file=sys.stderr)
        self._load_all()
        if self.verbose:
            print(
                f"  {len(self.rooms_raw)} rooms, {len(self.items_raw)} items, "
                f"{len(self.npcs_raw)} NPCs, {len(self.quests_raw)} quests, "
                f"{len(self.dialogues_raw)} dialogue files",
                file=sys.stderr,
            )

        if self.verbose:
            print("Building dependency graph...", file=sys.stderr)
        self._build_deps()

        if self.verbose:
            print("Running forward simulation...", file=sys.stderr)
        state = self._simulate()

        if self.verbose:
            print("Detecting issues...", file=sys.stderr)
        issues = self._detect_issues(state)

        return self._render_report(state, issues)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def _discover_worlds() -> list[Path]:
    return sorted(
        p for p in DATA_DIR.iterdir()
        if p.is_dir() and (
            (p / "config.toml").exists() or (p / "config.py").exists()
        )
    )


def _pick_world(world_arg: str | None) -> Path:
    worlds = _discover_worlds()
    if not worlds:
        print("No worlds found in data/.", file=sys.stderr)
        sys.exit(1)
    if world_arg:
        for w in worlds:
            if w.name == world_arg:
                return w
        names = ", ".join(w.name for w in worlds)
        print(f"World '{world_arg}' not found. Available: {names}", file=sys.stderr)
        sys.exit(1)
    if len(worlds) == 1:
        return worlds[0]
    print("Available worlds:")
    for i, w in enumerate(worlds):
        print(f"  {i+1}. {w.name}")
    try:
        idx = int(input("Select: ").strip()) - 1
        return worlds[max(0, min(idx, len(worlds) - 1))]
    except (ValueError, EOFError):
        return worlds[0]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(
        description="Delve critical path analyser — static world dependency checker"
    )
    ap.add_argument("--world",   default=None, help="World folder name")
    ap.add_argument("--out",     default=None, help="Write report to this file (also prints to stdout)")
    ap.add_argument("--quest",   default=None, help="(reserved) focus on a single quest ID")
    ap.add_argument("--verbose", action="store_true", help="Show analysis progress")
    args = ap.parse_args()

    world_path = _pick_world(args.world)
    _wc.init(world_path)

    analyzer = CriticalPathAnalyzer(world_path, verbose=args.verbose)
    report   = analyzer.run()

    print(report)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"\n[report written to {args.out}]", file=sys.stderr)


if __name__ == "__main__":
    main()
