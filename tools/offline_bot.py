#!/usr/bin/env python3
"""
tools/offline_bot.py — Offline AI test player for Delve.

Drives the game engine deterministically by reading zone TOML files
directly to plan quest completion paths, NPC dialogue choices, and
item/material gathering.  No LLM or internet connection required.

Usage:
  python tools/offline_bot.py
  python tools/offline_bot.py --world first_world
  python tools/offline_bot.py --quest lost_amulet
  python tools/offline_bot.py --zone millbrook
  python tools/offline_bot.py --name TestBot --turns 500
  python tools/offline_bot.py --verbose
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import engine.world_config as _wc
from engine.commands   import CommandProcessor
from engine.events     import EventBus, Event
from engine.map_builder import exit_dest
from engine.msg        import Msg, Tag
from engine.player     import Player
from engine.quests     import load_all as _load_all_quests
from engine.toml_io    import load as toml_load
from engine.world      import World

DATA_DIR      = ROOT / "data"
SESSIONS_DIR  = ROOT / "tools" / "ai_sessions"
SKIP_DIRS     = {"zone_state", "players", "__pycache__"}

# ── ANSI colours for console output ─────────────────────────────────────────

RESET = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
def _fg(r, g, b): return f"\033[38;2;{r};{g};{b}m"

C_ZONE    = BOLD + _fg(100, 180, 255)
C_MISSION = BOLD + _fg(255, 200,  80)
C_QUEST   = BOLD + _fg( 80, 220, 120)
C_ACTION  = DIM  + _fg(180, 180, 180)
C_SIDE    = DIM  + _fg(160, 120, 200)
C_WARN    = BOLD + _fg(255, 100,  80)

# ── Error-detection heuristics ───────────────────────────────────────────────

_ERROR_SIGNALS = {
    "traceback", "error:", "attributeerror", "keyerror",
    "typeerror", "indexerror", "valueerror", "nameerror",
    "recursionerror", "assertionerror",
}

def _is_error(text: str) -> bool:
    t = text.lower()
    return any(sig in t for sig in _ERROR_SIGNALS)


# ─────────────────────────────────────────────────────────────────────────────
# OutputCapture
# ─────────────────────────────────────────────────────────────────────────────

class OutputCapture:
    """Subscribe to EventBus.OUTPUT and accumulate (tag, text) pairs."""

    def __init__(self, bus: EventBus) -> None:
        self._lines: list[tuple[str, str]] = []
        bus.subscribe(Event.OUTPUT, self._on)

    def _on(self, msg: Msg) -> None:
        self._lines.append((msg.tag, msg.text))

    def flush(self) -> list[tuple[str, str]]:
        lines, self._lines = self._lines, []
        return lines


# ─────────────────────────────────────────────────────────────────────────────
# WorldModel — static analysis of zone TOML files
# ─────────────────────────────────────────────────────────────────────────────

class WorldModel:
    """
    Pre-game static analysis of a world's TOML data.

    Builds indexes for BFS pathfinding, NPC/item locations, quest-giver
    mapping, dialogue → quest-advance mapping, and available commissions —
    all without going through the engine.
    """

    def __init__(self, world_path: Path) -> None:
        self.world_path = world_path

        # Room topology
        self.room_exits: dict[str, dict[str, str]] = {}  # room_id → {dir → dest}
        self.room_names: dict[str, str]            = {}  # room_id → name

        # Object locations
        self.npc_rooms:  dict[str, str]            = {}  # npc_id → first room_id
        self.npc_names:  dict[str, str]            = {}  # npc_id → display name
        self.item_rooms: dict[str, list[str]]      = {}  # item_id → [room_id, ...]
        self.item_names: dict[str, str]            = {}  # item_id → display name

        # Quest planning
        self.quest_givers: dict[str, str]           = {}  # quest_id → npc_id
        # (quest_id, step) → [{npc_id, node_id, choice_idx, choice_text}]
        self.quest_advances: dict[tuple, list[dict]] = {}

        # Crafting
        self.commissions: dict[str, list[dict]]    = {}  # npc_id → [commission dicts]

        self._scan()

    # ── Scanning ──────────────────────────────────────────────────────────────

    def _zone_dirs(self) -> list[Path]:
        return sorted(
            p for p in self.world_path.iterdir()
            if p.is_dir() and p.name not in SKIP_DIRS
        )

    def _scan(self) -> None:
        for zone_dir in self._zone_dirs():
            self._scan_zone(zone_dir)
            self._scan_dialogues(zone_dir)

        # Supplement npc_rooms from canonical zone_state JSON (some NPCs are placed
        # there rather than in TOML spawns — e.g. garrison_ghost in ashwood).
        zone_state_dir = self.world_path / "zone_state"
        if zone_state_dir.is_dir():
            self._scan_zone_states(zone_state_dir)

        # Overlay giver field from engine quest definitions
        for qid, qdef in _load_all_quests().items():
            giver = qdef.get("giver", "")
            if giver:
                self.quest_givers[qid] = giver

    def _scan_zone_states(self, zone_state_dir: Path) -> None:
        """Read canonical zone_state JSON files to find NPC rooms missing from TOML spawns."""
        import json
        for json_path in sorted(zone_state_dir.glob("*.json")):
            try:
                with open(json_path) as f:
                    data = json.load(f)
            except Exception:
                continue
            for room_id, room_data in data.items():
                for npc in room_data.get("npcs", []):
                    nid = npc.get("id", "")
                    if nid and nid not in self.npc_rooms:
                        self.npc_rooms[nid] = room_id

    def _scan_zone(self, zone_dir: Path) -> None:
        for toml_path in sorted(zone_dir.glob("*.toml")):
            try:
                data = toml_load(toml_path)
            except Exception:
                continue

            for room in data.get("room", []):
                rid = room.get("id", "")
                if not rid:
                    continue
                self.room_names[rid] = room.get("name", rid)
                exits: dict[str, str] = {}
                for direction, ev in room.get("exits", {}).items():
                    dest = exit_dest(ev)
                    if dest:
                        exits[direction] = dest
                self.room_exits[rid] = exits

                for spawn in room.get("spawns", []):
                    nid = spawn if isinstance(spawn, str) else spawn.get("id", "")
                    if nid and nid not in self.npc_rooms:
                        self.npc_rooms[nid] = rid
                for it in room.get("items", []):
                    iid = it if isinstance(it, str) else it.get("id", "")
                    if iid:
                        self.item_rooms.setdefault(iid, [])
                        if rid not in self.item_rooms[iid]:
                            self.item_rooms[iid].append(rid)

            for npc in data.get("npc", []):
                nid = npc.get("id", "")
                if nid:
                    self.npc_names[nid] = npc.get("name", nid)

            for item in data.get("item", []):
                iid = item.get("id", "")
                if iid:
                    self.item_names[iid] = item.get("name", iid)

        crafting_dir = zone_dir / "crafting"
        if crafting_dir.is_dir():
            for toml_path in sorted(crafting_dir.glob("*.toml")):
                try:
                    data = toml_load(toml_path)
                except Exception:
                    continue
                for comm in data.get("commission", []):
                    npc_id = comm.get("npc_id", "")
                    if npc_id:
                        self.commissions.setdefault(npc_id, []).append(comm)

    def _scan_dialogues(self, zone_dir: Path) -> None:
        dlg_dir = zone_dir / "dialogues"
        if not dlg_dir.is_dir():
            return
        for toml_path in sorted(dlg_dir.glob("*.toml")):
            npc_id = toml_path.stem
            try:
                data = toml_load(toml_path)
            except Exception:
                continue
            self._index_dialogue_ops(npc_id, data)

    def _index_dialogue_ops(self, npc_id: str, data: dict) -> None:
        """Find advance_quest / complete_quest ops in response and node scripts.

        Two formats exist in the TOML dialogue files:
          a) Script on a [[response]] entry — triggers when player picks that choice.
          b) Script on a [[node]] entry — triggers when player enters that node.
             The triggering choice is whichever [[response]] has next=<node_id>.
        """
        # Build node-id → ops map for nodes that carry scripts.
        node_scripts: dict[str, list[dict]] = {}
        for node in data.get("node", []):
            node_id = node.get("id", "")
            if node_id and node.get("script"):
                node_scripts[node_id] = node["script"]
            # Responses nested inside [[node]] blocks (less common but handle them)
            for ci, resp in enumerate(node.get("response", [])):
                self._check_resp(npc_id, node_id, ci, resp)

        # Top-level [[response]] entries
        for ci, resp in enumerate(data.get("response", [])):
            node_id = resp.get("node", "root")
            # Script directly on the response
            self._check_resp(npc_id, node_id, ci, resp)
            # Script on the destination node (format b above)
            next_node = resp.get("next", "")
            if next_node in node_scripts:
                for op in node_scripts[next_node]:
                    qid = op.get("quest_id", "")
                    if op.get("op") == "advance_quest" and qid:
                        key = (qid, int(op.get("step", 0)))
                        self.quest_advances.setdefault(key, []).append({
                            "npc_id": npc_id, "node_id": node_id,
                            "choice_idx": ci,
                            "choice_text": resp.get("text", ""),
                        })
                    elif op.get("op") == "complete_quest" and qid:
                        key = (qid, 999)
                        self.quest_advances.setdefault(key, []).append({
                            "npc_id": npc_id, "node_id": node_id,
                            "choice_idx": ci,
                            "choice_text": resp.get("text", ""),
                        })

    def _check_resp(self, npc_id: str, node_id: str, ci: int, resp: dict) -> None:
        for op in resp.get("script", []):
            qid = op.get("quest_id", "")
            if op.get("op") == "advance_quest" and qid:
                key = (qid, int(op.get("step", 0)))
                self.quest_advances.setdefault(key, []).append({
                    "npc_id": npc_id, "node_id": node_id,
                    "choice_idx": ci, "choice_text": resp.get("text", ""),
                })
            elif op.get("op") == "complete_quest" and qid:
                key = (qid, 999)
                self.quest_advances.setdefault(key, []).append({
                    "npc_id": npc_id, "node_id": node_id,
                    "choice_idx": ci, "choice_text": resp.get("text", ""),
                })

    # ── Pathfinding ───────────────────────────────────────────────────────────

    def bfs_path(self, start: str, goal: str) -> list[str] | None:
        """Return list of room_ids from start (exclusive) to goal (inclusive)."""
        if start == goal:
            return []
        queue: deque[tuple[str, list[str]]] = deque([(start, [])])
        visited = {start}
        while queue:
            room_id, path = queue.popleft()
            for dest in self.room_exits.get(room_id, {}).values():
                if dest in visited:
                    continue
                new_path = path + [dest]
                if dest == goal:
                    return new_path
                visited.add(dest)
                queue.append((dest, new_path))
        return None

    def path_to_dirs(self, start: str, path: list[str]) -> list[str]:
        """Convert a room_id path to direction command words."""
        dirs: list[str] = []
        cur = start
        for nxt in path:
            exits = self.room_exits.get(cur, {})
            found = next((d for d, dest in exits.items() if dest == nxt), None)
            if found:
                dirs.append(found)
            cur = nxt
        return dirs

    def find_advance_for(self, quest_id: str, to_step: int) -> list[dict]:
        return self.quest_advances.get((quest_id, to_step), [])

    def npc_name(self, npc_id: str) -> str:
        return self.npc_names.get(npc_id, npc_id)

    def item_name(self, item_id: str) -> str:
        return self.item_names.get(item_id, item_id)


# ─────────────────────────────────────────────────────────────────────────────
# HtmlLogger
# ─────────────────────────────────────────────────────────────────────────────

_TAG_COLORS: dict[str, str] = {
    Tag.ROOM_NAME:  "#ffdc64", Tag.ROOM_DESC:  "#cccccc",
    Tag.MOVE:       "#88aaff", Tag.NPC:        "#d4a0e0",
    Tag.ITEM:       "#50dc78", Tag.ITEM_HAVE:  "#50dc78",
    Tag.COMBAT_HIT: "#f05050", Tag.COMBAT_RECV:"#cc8844",
    Tag.COMBAT_KILL:"#ff8844", Tag.COMBAT_DEATH:"#cc0000",
    Tag.DIALOGUE:   "#c8a0e8", Tag.QUEST:      "#80e890",
    Tag.REWARD_XP:  "#80e890", Tag.REWARD_GOLD:"#f0c060",
    Tag.SHOP:       "#88ccff", Tag.DOOR:       "#ff9944",
    Tag.ERROR:      "#ff6666", Tag.SYSTEM:     "#888888",
    Tag.BLANK:      "#333333",
}
_TAG_DEFAULT = "#bbbbbb"


class HtmlLogger:
    """Collects EventBus output and writes a coloured HTML log at session end."""

    def __init__(self, bus: EventBus) -> None:
        self._rows: list[str] = []
        bus.subscribe(Event.OUTPUT, self._on)

    def _esc(self, t: str) -> str:
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _on(self, msg: Msg) -> None:
        color = _TAG_COLORS.get(msg.tag, _TAG_DEFAULT)
        self._rows.append(
            f'<div class="line" style="color:{color}">{self._esc(msg.text)}</div>'
        )

    def banner(self, cls: str, text: str) -> None:
        self._rows.append(f'<div class="banner {cls}">{self._esc(text)}</div>')

    def write(self, path: Path, summary: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(self._rows)
        summary_html = "".join(
            f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in summary.items()
        )
        path.write_text(f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>Delve Bot Session</title>
<style>
  body {{ background:#1a1a2e; color:#ccc; font-family:monospace;
         font-size:13px; padding:16px; line-height:1.4; }}
  .line {{ padding:1px 0; white-space:pre-wrap; word-break:break-word; }}
  .banner.zone    {{ background:#0f3460; color:#88aaff; font-weight:bold;
                     padding:4px 8px; margin:8px 0 2px; border-radius:4px; }}
  .banner.mission {{ background:#332200; color:#ffdc64; font-weight:bold;
                     padding:4px 8px; margin:4px 0; border-radius:4px; }}
  .banner.turn    {{ color:#333; font-size:10px; margin-top:8px;
                     border-top:1px solid #222; padding-top:4px; }}
  h2  {{ color:#88aaff; margin-bottom:16px; }}
  h3  {{ color:#888; margin-top:24px; }}
  table {{ border-collapse:collapse; margin-top:8px; }}
  td  {{ padding:3px 14px 3px 0; }}
  td:first-child {{ color:#888; }}
</style></head><body>
<h2>Delve Offline Bot &mdash; Session Log</h2>
{body}
<h3>Session Summary</h3>
<table>{summary_html}</table>
</body></html>""", encoding="utf-8")
        print(f"  HTML log → {path}")


# ─────────────────────────────────────────────────────────────────────────────
# StatsLogger
# ─────────────────────────────────────────────────────────────────────────────

class StatsLogger:
    """Appends JSON records to a shared stats.jsonl file."""

    def __init__(self, path: Path, instance: str, world: str) -> None:
        self._path     = path
        self._instance = instance
        self._world    = world
        path.parent.mkdir(parents=True, exist_ok=True)
        self._f = path.open("a", encoding="utf-8")

    def _w(self, record: dict) -> None:
        record.setdefault("ts",       datetime.now(timezone.utc).isoformat())
        record.setdefault("world",    self._world)
        record.setdefault("instance", self._instance)
        self._f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._f.flush()

    def session_start(self, name: str, turns_max: int,
                      quest_filter: str | None, zone_filter: str | None) -> None:
        self._w({"type": "session_start", "name": name, "turns_max": turns_max,
                 "quest_filter": quest_filter, "zone_filter": zone_filter})

    def action(self, turn: int, zone: str, room: str,
               command: str, mission: str) -> None:
        self._w({"type": "action", "turn": turn, "zone": zone,
                 "room": room, "command": command, "mission": mission})

    def player_state(self, turn: int, p: Player) -> None:
        self._w({"type": "player_state", "turn": turn,
                 "hp": p.hp, "max_hp": p.max_hp,
                 "level": p.level, "xp": p.xp, "gold": p.gold,
                 "style": p.active_style, "style_prof": dict(p.style_prof),
                 "skills": dict(p.skills),
                 "active_quests": dict(p.active_quests),
                 "completed_quests": sorted(p.completed_quests)})

    def equip_change(self, turn: int, slot: str, item: dict | None,
                     atk_b: int, atk_a: int, def_b: int, def_a: int) -> None:
        self._w({"type": "equip_change", "turn": turn, "slot": slot,
                 "item_id":   (item or {}).get("id", ""),
                 "item_name": (item or {}).get("name", ""),
                 "attack_before": atk_b, "attack_after": atk_a,
                 "defense_before": def_b, "defense_after": def_a})

    def combat(self, turn: int, npc_id: str, npc_name: str,
               hp_b: int, hp_a: int, npc_dead: bool, fled: bool) -> None:
        self._w({"type": "combat", "turn": turn,
                 "npc_id": npc_id, "npc_name": npc_name,
                 "hp_before": hp_b, "hp_after": hp_a,
                 "npc_dead": npc_dead, "fled": fled})

    def quest_update(self, turn: int, quest_id: str,
                     from_step: int, to_step: int, completed: bool) -> None:
        self._w({"type": "quest_update", "turn": turn, "quest_id": quest_id,
                 "from_step": from_step, "to_step": to_step, "completed": completed})

    def zone_change(self, turn: int, from_z: str, to_z: str, room: str) -> None:
        self._w({"type": "zone_change", "turn": turn,
                 "from_zone": from_z, "to_zone": to_z, "room": room})

    def error(self, turn: int, command: str, snippet: str) -> None:
        self._w({"type": "error", "turn": turn,
                 "command": command, "output_snippet": snippet[:200]})

    def session_end(self, turns: int, quests: list[str],
                    level: int, xp: int, gold: int, rooms: int) -> None:
        self._w({"type": "session_end", "turns_played": turns,
                 "quests_completed": quests, "final_level": level,
                 "final_xp": xp, "final_gold": gold, "rooms_visited": rooms})
        self._f.close()


# ─────────────────────────────────────────────────────────────────────────────
# BotRunner
# ─────────────────────────────────────────────────────────────────────────────

class BotRunner:
    """
    Drives the engine via a priority-based mission stack.

    Reads zone data (via WorldModel) to plan optimal paths through quests,
    dialogue, item gathering, and combat — entirely offline.
    """

    def __init__(self, world: World, player: Player,
                 processor: CommandProcessor, capture: OutputCapture,
                 html: HtmlLogger, stats: StatsLogger,
                 model: WorldModel,
                 quest_filter: str | None, zone_filter: str | None,
                 verbose: bool) -> None:
        self._world   = world
        self._player  = player
        self._proc    = processor
        self._capture = capture
        self._html    = html
        self._stats   = stats
        self._model   = model
        self._qfilter = quest_filter
        self._zfilter = zone_filter
        self._verbose = verbose

        # Mission stack: [(type, {kwargs}), ...]  — top = last element
        self._stack: list[tuple[str, dict]] = []
        self._label = "Starting"

        # Tracking
        self._visited:         set[str]             = set()
        self._talked:          set[str]             = set()
        self._pending_dirs:    list[str]            = []
        self._pending_gives:   list[str]            = []
        self._commissioned:    set[str]             = set()
        self._stuck_quests:    set[str]             = set()  # quests where no advance found
        self._failed_gathers:  set[str]             = set()  # item_ids we tried to gather but couldn't get

        # Pending routing state for _input_fn (set by _exec_talk_npc before cmd runs)
        self._pending_comm_label:   str       = ""
        self._pending_quest_id:     str | None = None
        self._pending_target_step:  int | None = None

        # State snapshots for delta detection
        self._last_zone:  str             = ""
        self._last_quests: dict[str, int] = {}
        self._last_done:  set[str]        = set()
        self._last_equip: dict            = {}
        self._last_atk:   int             = 0
        self._last_def:   int             = 0
        self._last_snap_turn: int         = -1

        self._turn   = 0
        self._errors = 0

    # ── Room/NPC helpers ──────────────────────────────────────────────────────

    def _room(self) -> dict | None:
        return self._world.prepare_room(self._player.room_id, self._player)

    def _zone(self) -> str:
        return self._world.zone_for_room(self._player.room_id) or ""

    def _npcs(self) -> list[dict]:
        r = self._room()
        return r.get("_npcs", []) if r else []

    def _items(self) -> list[dict]:
        r = self._room()
        return r.get("items", []) if r else []

    def _hostile(self) -> dict | None:
        for n in self._npcs():
            if n.get("hostile") and n.get("hp", 0) > 0:
                return n
        return None

    # ── Mission stack helpers ─────────────────────────────────────────────────

    def _push(self, mtype: str, **kw: object) -> None:
        self._stack.append((mtype, kw))

    def _set_label(self, label: str) -> None:
        if label == self._label:
            return
        self._label = label
        print(f"{C_MISSION}[MISSION] {label}{RESET}")
        self._html.banner("mission", f"MISSION: {label}")

    # ── Command execution ─────────────────────────────────────────────────────

    def _cmd(self, command: str) -> list[tuple[str, str]]:
        """Issue a command, capture output, run delta detection."""
        self._capture.flush()
        if self._verbose:
            print(f"{C_ACTION}  > {command}{RESET}")
        self._proc.process(command)
        lines = self._capture.flush()
        if self._verbose:
            for _, text in lines:
                if text:
                    print(f"    {text}")
        self._stats.action(self._turn, self._zone(), self._player.room_id,
                           command, self._label)
        self._detect_deltas(lines)
        return lines

    def _detect_deltas(self, lines: list[tuple[str, str]]) -> None:
        p    = self._player
        zone = self._zone()

        # Zone change
        if zone != self._last_zone and self._last_zone:
            self._stats.zone_change(self._turn, self._last_zone, zone, p.room_id)
            room_name = self._model.room_names.get(p.room_id, p.room_id)
            print(f"{C_ZONE}[ZONE] {zone} — {room_name}{RESET}")
            self._html.banner("zone", f"ZONE: {zone} — {room_name}")
        self._last_zone = zone

        # Quest progress
        for qid, step in p.active_quests.items():
            prev = self._last_quests.get(qid, 0)
            if step != prev:
                self._stats.quest_update(self._turn, qid, prev, step, False)
                self._stats.player_state(self._turn, p)
                self._last_snap_turn = self._turn
        for qid in p.completed_quests - self._last_done:
            prev = self._last_quests.get(qid, 0)
            self._stats.quest_update(self._turn, qid, prev, 0, True)
            print(f"{C_QUEST}[QUEST] ✓ {_quest_title(qid)} complete{RESET}")
            self._html.banner("mission", f"QUEST COMPLETE: {_quest_title(qid)}")
            self._stats.player_state(self._turn, p)
            self._last_snap_turn = self._turn
        self._last_quests = dict(p.active_quests)
        self._last_done   = set(p.completed_quests)

        # Equipment change
        cur_atk, cur_def = p.effective_attack, p.effective_defense
        if dict(p.equipped) != self._last_equip:
            for slot, item in p.equipped.items():
                if item != self._last_equip.get(slot):
                    self._stats.equip_change(self._turn, slot, item,
                                             self._last_atk, cur_atk,
                                             self._last_def, cur_def)
            self._last_equip = dict(p.equipped)
            self._last_atk   = cur_atk
            self._last_def   = cur_def

        # Periodic state snapshot
        if self._turn - self._last_snap_turn >= 20:
            self._stats.player_state(self._turn, p)
            self._last_snap_turn = self._turn

        # Error detection
        for _, text in lines:
            if _is_error(text):
                self._stats.error(self._turn, "_", text)
                print(f"{C_WARN}[ERROR]{RESET} {text[:100]}")
                self._errors += 1

    # ── Priority-based mission planning ──────────────────────────────────────

    def _decide(self) -> None:
        """Fill the stack when empty."""
        p = self._player
        all_q = _load_all_quests()

        # 1. Quests
        if self._qfilter:
            candidates = [self._qfilter] if self._qfilter not in p.completed_quests else []
        else:
            active_sorted = sorted(p.active_quests.items(), key=lambda kv: kv[1])
            unstarted = [
                qid for qid in all_q
                if qid not in p.active_quests and qid not in p.completed_quests
            ]
            candidates = [qid for qid, _ in active_sorted] + unstarted

        for qid in candidates:
            if qid in p.completed_quests or qid in self._stuck_quests:
                continue
            self._push("complete_quest_chain", quest_id=qid)
            self._set_label(f"Complete quest: {_quest_title(qid)}")
            return

        # 2. Commissions
        for npc_id, comms in self._model.commissions.items():
            for comm in comms:
                cid = comm.get("id", "")
                if cid in self._commissioned:
                    continue
                mats   = list(comm.get("materials", []))
                inv_ids = [i.get("id", "") for i in p.inventory]
                missing = []
                for mat in mats:
                    if mat in inv_ids:
                        inv_ids.remove(mat)
                    else:
                        missing.append(mat)
                if not missing:
                    self._push("commission_start", npc_id=npc_id, commission=dict(comm))
                    self._set_label(f"Commission: {comm.get('label', cid)}")
                    return
                # Actively gather missing materials (only obtainable ones with known rooms)
                stack_before = len(self._stack)
                for mat_id in missing:
                    if mat_id in self._failed_gathers:
                        continue  # already tried and couldn't get it (e.g. shop item)
                    rooms = self._model.item_rooms.get(mat_id, [])
                    if rooms:
                        name = self._model.item_name(mat_id)
                        print(f"{C_SIDE}  Side-job: gather {name} (for {comm.get('label', cid)}){RESET}")
                        self._push("gather_material", item_id=mat_id, item_name=name)
                if len(self._stack) > stack_before:
                    self._set_label(f"Gather materials for: {comm.get('label', cid)}")
                    return
                # All missing materials are unobtainable — skip this commission

        # 3. Items in current room
        for item in self._items():
            if item.get("scenery"):
                continue
            iid  = item.get("id", "")
            name = item.get("name", iid)
            if iid:
                self._push("pick_up", item_id=iid, item_name=name)
                self._set_label(f"Collect: {name}")
                return

        # 4. Explore unvisited rooms (skip rooms BFS can't reach to avoid wasting turns)
        for rid in self._rooms_in_scope():
            if rid not in self._visited:
                if self._model.bfs_path(self._player.room_id, rid) is not None:
                    self._push("go_to", room=rid)
                    self._set_label(f"Explore: {self._model.room_names.get(rid, rid)}")
                    return
                else:
                    # Mark unreachable rooms as "visited" so we don't keep retrying
                    self._visited.add(rid)

        # 5. Talk to untouched NPCs
        for npc in self._npcs():
            nid = npc.get("id", "")
            if nid and nid not in self._talked:
                self._push("talk_npc", npc_id=nid)
                self._set_label(f"Talk to: {npc.get('name', nid)}")
                return

        self._set_label("All objectives complete")

    def _rooms_in_scope(self) -> list[str]:
        if self._zfilter:
            return [
                rid for rid in self._model.room_exits
                if (self._world.zone_for_room(rid) or "") == self._zfilter
            ]
        return list(self._model.room_exits.keys())

    # ── Mission executors ─────────────────────────────────────────────────────

    def _execute_top(self) -> str | None:
        if not self._stack:
            return None
        mtype, args = self._stack[-1]
        dispatch = {
            "go_to":                  self._exec_go_to,
            "talk_npc":               self._exec_talk_npc,
            "pick_up":                self._exec_pick_up,
            "equip_check":            self._exec_equip_check,
            "gather_material":        self._exec_gather,
            "commission_start":       self._exec_commission_start,
            "commission_collect":     self._exec_commission_collect,
            "complete_quest_chain":   self._exec_quest_chain,
            "_check_quest_advance":   self._exec_check_advance,
        }
        fn = dispatch.get(mtype)
        if fn:
            return fn(args)
        self._stack.pop()
        return None

    def _exec_go_to(self, args: dict) -> str | None:
        target = args["room"]
        if self._player.room_id == target:
            self._stack.pop()
            self._visited.add(target)
            return None
        if self._pending_dirs:
            return self._pending_dirs.pop(0)
        path = self._model.bfs_path(self._player.room_id, target)
        if not path:
            self._stack.pop()
            return None
        self._pending_dirs = self._model.path_to_dirs(self._player.room_id, path)
        if not self._pending_dirs:
            self._stack.pop()
            return None
        return self._pending_dirs.pop(0)

    def _exec_talk_npc(self, args: dict) -> str | None:
        npc_id      = args["npc_id"]
        quest_id    = args.get("quest_id")
        target_step = args.get("target_step")
        npc = next((n for n in self._npcs() if n.get("id") == npc_id), None)
        if not npc:
            npc_room = self._model.npc_rooms.get(npc_id)
            reachable = (npc_room and npc_room != self._player.room_id
                         and self._model.bfs_path(self._player.room_id, npc_room) is not None)
            if reachable:
                # Navigate to the NPC's known room
                self._pending_dirs = []
                self._push("go_to", room=npc_room)
            else:
                # NPC absent, unreachable, or same room — pop mission and mark quest stuck
                step_before = self._player.active_quests.get(quest_id, 0) if quest_id else None
                self._stack.pop()
                if quest_id and target_step:
                    self._push("_check_quest_advance",
                               quest_id=quest_id, step_before=step_before,
                               target_step=target_step)
            return None
        self._stack.pop()
        step_before = self._player.active_quests.get(quest_id, 0) if quest_id else None
        self._talked.add(npc_id)
        # Store quest context so _input_fn can pick the right dialogue choice
        self._pending_quest_id    = quest_id
        self._pending_target_step = target_step
        cmd = f"talk {npc.get('name', npc_id)}"
        # After the talk resolves, detect if quest failed to advance and mark stuck
        if quest_id and target_step:
            self._push("_check_quest_advance",
                       quest_id=quest_id, step_before=step_before, target_step=target_step)
        return cmd

    def _exec_pick_up(self, args: dict) -> str | None:
        iid  = args.get("item_id", "")
        name = args.get("item_name", iid)
        present = any(
            (i.get("id", "") == iid) or (i.get("name", "") == name)
            for i in self._items()
        )
        self._stack.pop()
        return f"get {name}" if present else None

    def _exec_equip_check(self, _args: dict) -> str | None:
        for item in list(self._player.inventory):
            slot = item.get("slot", "")
            if not slot:
                continue
            equipped = self._player.equipped.get(slot)
            if equipped is None or item.get("value", 0) > equipped.get("value", 0):
                self._stack.pop()
                return f"equip {item.get('name', item.get('id', ''))}"
        self._stack.pop()
        return None

    def _exec_gather(self, args: dict) -> str | None:
        iid  = args["item_id"]
        name = args["item_name"]
        if any(i.get("id", "") == iid for i in self._player.inventory):
            # Already have one; if all spawn rooms were visited, can't get more
            rooms = self._model.item_rooms.get(iid, [])
            if not rooms or all(r in self._visited for r in rooms):
                self._failed_gathers.add(iid)
            self._stack.pop()
            return None
        if any(i.get("id", "") == iid for i in self._items()):
            self._stack.pop()
            return f"get {name}"
        rooms = self._model.item_rooms.get(iid, [])
        # If we're already at a known spawn room and item isn't here, it was
        # consumed/missing (e.g. shop item, not a floor pickup) — give up.
        if rooms and self._player.room_id not in rooms:
            self._push("go_to", room=rooms[0])
        else:
            self._failed_gathers.add(iid)
            self._stack.pop()
        return None

    def _exec_commission_start(self, args: dict) -> str | None:
        npc_id = args["npc_id"]
        comm   = args["commission"]
        cid    = comm.get("id", "")
        mats   = list(comm.get("materials", []))
        npc_name = self._model.npc_name(npc_id)

        npc = next((n for n in self._npcs() if n.get("id") == npc_id), None)
        if not npc:
            npc_room = self._model.npc_rooms.get(npc_id)
            if npc_room:
                self._push("go_to", room=npc_room)
            else:
                self._stack.pop()
            return None

        if self._pending_gives:
            mat_name = self._pending_gives.pop(0)
            if not self._pending_gives:
                # All materials given — pop this, push collect
                self._stack.pop()
                self._commissioned.add(cid)
                turns = comm.get("turns_required", 1)
                self._push("commission_collect", npc_id=npc_id,
                           npc_name=npc_name, turns_wait=turns)
            return f"give {mat_name} to {npc_name}"

        # Build give list from inventory
        inv = {i.get("id", ""): i.get("name", i.get("id", ""))
               for i in self._player.inventory}
        gives = []
        for mat_id in mats:
            if mat_id in inv:
                gives.append(inv.pop(mat_id))
        if not gives:
            self._stack.pop()
            return None

        # Open commission menu; input_fn will pick the right one
        self._pending_gives     = gives[1:]
        self._pending_comm_label = comm.get("label", "")
        # First, open the commission menu (uses input_fn for selection)
        return f"commission {npc_name}"

    def _exec_commission_collect(self, args: dict) -> str | None:
        npc_id   = args["npc_id"]
        npc_name = args.get("npc_name", npc_id)
        turns    = args.get("turns_wait", 0)
        if turns > 0:
            args["turns_wait"] = turns - 1
            return "look"
        npc = next((n for n in self._npcs() if n.get("id") == npc_id), None)
        if not npc:
            npc_room = self._model.npc_rooms.get(npc_id)
            if npc_room:
                self._push("go_to", room=npc_room)
            else:
                self._stack.pop()
            return None
        self._stack.pop()
        return "collect"

    def _exec_quest_chain(self, args: dict) -> None:
        """Expand complete_quest_chain into concrete sub-missions."""
        self._stack.pop()
        qid   = args["quest_id"]
        p     = self._player
        all_q = _load_all_quests()
        qdef  = all_q.get(qid)
        if not qdef or qid in p.completed_quests:
            return

        current_step = p.active_quests.get(qid, 0)
        steps = sorted(qdef.get("step", []), key=lambda s: s.get("index", 0))

        def _best_npc(advs: list[dict]) -> str:
            """Return the NPC from advances with the best known room, or first NPC."""
            for adv in advs:
                nid = adv["npc_id"]
                if self._model.npc_rooms.get(nid):
                    return nid
            return advs[0]["npc_id"] if advs else ""

        if current_step == 0:
            # Start the quest: find giver NPC via quest_advances(step=1) or quest_givers
            advances = self._model.find_advance_for(qid, 1)
            if advances:
                npc_id = _best_npc(advances)
            else:
                npc_id = self._model.quest_givers.get(qid, "")
            if npc_id:
                self._push("talk_npc", npc_id=npc_id, quest_id=qid, target_step=1)
            return

        # Find the next incomplete step
        for step in steps:
            idx = step.get("index", 0)
            if idx <= current_step:
                continue
            # Check if advance info is available in dialogue
            advances = self._model.find_advance_for(qid, idx)
            if not advances:
                # Try completion op
                advances = self._model.find_advance_for(qid, 999)
            if advances:
                npc_id = _best_npc(advances)
                self._push("talk_npc", npc_id=npc_id, quest_id=qid, target_step=idx)
            else:
                # No dialogue mapping for this step — try the quest giver as a fallback.
                # _exec_check_advance will mark stuck if the talk doesn't advance the quest.
                giver = self._model.quest_givers.get(qid, "")
                if giver and qid not in self._stuck_quests:
                    self._push("talk_npc", npc_id=giver, quest_id=qid, target_step=idx)
                # else: silently skip; _decide() will move to the next objective
            break  # Only handle the next step; re-plan after it's done

    def _exec_check_advance(self, args: dict) -> str | None:
        """Called after a quest-targeted talk to detect if the quest advanced."""
        self._stack.pop()
        quest_id    = args["quest_id"]
        step_before = args["step_before"]
        step_now    = self._player.active_quests.get(quest_id, 0)
        done        = quest_id in self._player.completed_quests
        if done or step_now != step_before:
            # Quest advanced or completed — clear any stuck mark so next steps can proceed
            self._stuck_quests.discard(quest_id)
        else:
            # Quest didn't advance — mark stuck so we don't keep retrying this step
            self._stuck_quests.add(quest_id)
        # Clear pending quest context now that dialogue is done
        self._pending_quest_id    = None
        self._pending_target_step = None
        return None

    # ── Dialogue input function ───────────────────────────────────────────────

    def _make_input_fn(self):
        model   = self._model
        runner  = self
        call_counts: list[int] = [0]   # mutable cell shared with closure

        def _input_fn(prompt: str, choices: list[tuple[str, str]]) -> str:
            if not choices:
                return ""

            call_counts[0] += 1

            # Commission menu selection
            if runner._pending_comm_label:
                label = runner._pending_comm_label.lower()
                runner._pending_comm_label = ""
                for key, text in choices:
                    if label in text.lower():
                        return key
                return choices[0][0]

            # Quest-advance selection — use context stored by _exec_talk_npc
            quest_id    = runner._pending_quest_id
            target_step = runner._pending_target_step

            if quest_id and target_step:
                advances = model.find_advance_for(quest_id, target_step)
                if not advances:
                    advances = model.find_advance_for(quest_id, 999)
                for adv in advances:
                    adv_text = adv.get("choice_text", "").lower()
                    if not adv_text:
                        continue
                    for key, text in choices:
                        if adv_text in text.lower() or text.lower() in adv_text:
                            call_counts[0] = 0
                            return key

            # Fallback: even if the current quest has no mapped advance, prefer
            # any choice that is known to advance SOME quest (avoids cycling).
            choice_texts_lower = {text.lower() for _, text in choices}
            for advs in model.quest_advances.values():
                for adv in advs:
                    adv_text = adv.get("choice_text", "").lower()
                    if not adv_text:
                        continue
                    for key, text in choices:
                        if adv_text in text.lower() or text.lower() in adv_text:
                            call_counts[0] = 0
                            return key

            # Safety valve: too many calls without finding the quest-advance choice
            # — bail out of the dialogue to avoid infinite loops.
            if call_counts[0] > 20:
                call_counts[0] = 0
                raise EOFError("dialogue loop limit")

            # Default: first choice
            return choices[0][0]

        return _input_fn

    # ── Main game loop ────────────────────────────────────────────────────────

    def run(self, max_turns: int) -> dict:
        self._proc._input_fn = self._make_input_fn()

        # Initial state setup
        self._cmd("look")
        self._last_zone   = self._zone()
        self._last_quests = dict(self._player.active_quests)
        self._last_done   = set(self._player.completed_quests)
        self._last_equip  = dict(self._player.equipped)
        self._last_atk    = self._player.effective_attack
        self._last_def    = self._player.effective_defense

        zone = self._zone()
        room_name = self._model.room_names.get(self._player.room_id, self._player.room_id)
        print(f"{C_ZONE}[ZONE] {zone} — {room_name}{RESET}")
        self._html.banner("zone", f"ZONE: {zone} — {room_name}")

        for self._turn in range(1, max_turns + 1):
            self._html.banner("turn", f"T{self._turn:04d}")
            self._visited.add(self._player.room_id)
            p = self._player

            if p.hp <= 0:
                print(f"{C_WARN}[WARN] Player died — stopping.{RESET}")
                break

            # 1. Low-HP flee check
            if p.hp < p.max_hp * 0.20 and self._hostile():
                exits = self._model.room_exits.get(p.room_id, {})
                if exits:
                    flee_dir = next(iter(exits))
                    print(f"{C_SIDE}  [flee] {flee_dir}{RESET}")
                    self._cmd(flee_dir)
                continue

            # 2. Hostile NPC in room — attack it
            hostile = self._hostile()
            if hostile:
                npc_id   = hostile.get("id", "")
                npc_name = hostile.get("name", npc_id)
                hp_before = p.hp
                lines = self._cmd(f"attack {npc_name}")
                hp_after  = p.hp
                npc_dead = not any(
                    n.get("id") == npc_id and n.get("hp", 0) > 0
                    for n in self._npcs()
                )
                self._stats.combat(self._turn, npc_id, npc_name,
                                   hp_before, hp_after, npc_dead, False)
                continue

            # 3. Decide mission if stack is empty
            if not self._stack:
                self._decide()
                if not self._stack:
                    print(f"{C_ZONE}[BOT] All objectives complete.{RESET}")
                    break

            # 4. Execute top of mission stack
            command = self._execute_top()
            if command is None:
                continue  # sub-mission was pushed; re-evaluate

            # Snapshot attack/defense before any equip change
            self._cmd(command)

            # After picking up, try to equip
            if command.startswith("get "):
                self._push("equip_check")

        self._player.save()
        return {
            "turns":  self._turn,
            "quests": sorted(self._player.completed_quests),
            "level":  self._player.level,
            "xp":     self._player.xp,
            "gold":   self._player.gold,
            "rooms":  len(self._visited),
            "errors": self._errors,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _quest_title(quest_id: str) -> str:
    qdef = _load_all_quests().get(quest_id, {})
    return qdef.get("title", quest_id)


def _discover_worlds() -> list[Path]:
    return sorted(
        p for p in DATA_DIR.iterdir()
        if p.is_dir() and (p / "config.py").exists()
    )


def _pick_world(world_arg: str | None) -> Path:
    worlds = _discover_worlds()
    if not worlds:
        print("[error] No worlds found in data/.", file=sys.stderr)
        sys.exit(1)
    if world_arg:
        for w in worlds:
            if w.name == world_arg:
                return w
        print(f"[error] World '{world_arg}' not found.", file=sys.stderr)
        sys.exit(1)
    if len(worlds) == 1:
        return worlds[0]
    print("Available worlds:")
    for i, w in enumerate(worlds):
        print(f"  {i+1}. {w.name}")
    try:
        idx = int(input("Select: ").strip()) - 1
        return worlds[idx]
    except (ValueError, IndexError):
        return worlds[0]


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    # On Windows cp1252 consoles, box-drawing chars and arrows crash; use UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="Delve offline AI test player")
    ap.add_argument("--world",   default=None,  help="World folder name")
    ap.add_argument("--name",    default="OfflineBot", help="Character name")
    ap.add_argument("--turns",   type=int, default=500, help="Max turns (default 500)")
    ap.add_argument("--quest",   default=None,  help="Focus on this quest ID")
    ap.add_argument("--zone",    default=None,  help="Restrict to this zone ID")
    ap.add_argument("--verbose", action="store_true", help="Print all game output")
    args = ap.parse_args()

    world_path = _pick_world(args.world)
    print(f"World: {world_path.name}")

    # ── Engine setup ──────────────────────────────────────────────────────────
    _wc.init(world_path)
    world = World(world_path)

    if Player.exists(args.name):
        player = Player.load(args.name)
    else:
        player = Player.create_new(args.name)
        player.world_id = world_path.name
        player.room_id  = world.start_room
        player.save()

    world.attach_player(player)
    bus       = EventBus()
    capture   = OutputCapture(bus)
    html_log  = HtmlLogger(bus)
    processor = CommandProcessor(world, player, bus)

    # ── Static world analysis ─────────────────────────────────────────────────
    print("Analysing world data...", end=" ", flush=True)
    model = WorldModel(world_path)
    print(
        f"{len(model.room_exits)} rooms, {len(model.npc_rooms)} NPCs, "
        f"{len(model.quest_givers)} quest givers, "
        f"{len(model.quest_advances)} advance mappings"
    )

    # ── Logging ───────────────────────────────────────────────────────────────
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    ts_str     = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path  = SESSIONS_DIR / f"ai_{world_path.name}_{ts_str}.html"
    stats_path = SESSIONS_DIR / "stats.jsonl"
    instance   = str(uuid.uuid4())

    stats = StatsLogger(stats_path, instance, world_path.name)
    stats.session_start(args.name, args.turns, args.quest, args.zone)

    # ── Run ───────────────────────────────────────────────────────────────────
    runner = BotRunner(
        world=world, player=player, processor=processor,
        capture=capture, html=html_log, stats=stats, model=model,
        quest_filter=args.quest, zone_filter=args.zone, verbose=args.verbose,
    )

    print(
        f"\nStarting: {args.name}  turns={args.turns}"
        + (f"  quest={args.quest}" if args.quest else "")
        + (f"  zone={args.zone}"   if args.zone  else "")
    )
    print()

    summary = runner.run(args.turns)

    # ── Wrap-up ───────────────────────────────────────────────────────────────
    stats.session_end(
        summary["turns"], summary["quests"],
        summary["level"], summary["xp"], summary["gold"], summary["rooms"],
    )

    print(f"\n{'-'*52}")
    print(f"Turns:    {summary['turns']}")
    print(f"Level:    {summary['level']}  XP: {summary['xp']}  Gold: {summary['gold']}")
    print(f"Rooms:    {summary['rooms']}")
    print(f"Quests:   {', '.join(summary['quests']) or 'none'}")
    if summary["errors"]:
        print(f"{C_WARN}Errors:   {summary['errors']}{RESET}")
    print(f"Stats  ->  {stats_path}")

    html_log.write(html_path, {
        "World":         world_path.name,
        "Character":     args.name,
        "Turns":         summary["turns"],
        "Level":         summary["level"],
        "XP":            summary["xp"],
        "Gold":          summary["gold"],
        "Rooms visited": summary["rooms"],
        "Quests done":   ", ".join(summary["quests"]) or "none",
        "Errors":        summary["errors"],
    })


if __name__ == "__main__":
    main()
