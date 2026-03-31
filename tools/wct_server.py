#!/usr/bin/env python3
"""
tools/wct_server.py — Delve World Creation Tool (WCT) server.

A lightweight local HTTP server that serves the WCT frontend and exposes a
JSON API for reading and writing TOML data files.

Usage:
    python tools/wct_server.py            # starts on http://localhost:7373
    python tools/wct_server.py --port 8080

The WCT does NOT open a browser automatically. Navigate to the URL shown on startup,
or use --browser to open it automatically.
"""

from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import queue
import threading
from socketserver import ThreadingMixIn

ROOT         = Path(__file__).parent.parent
DATA_DIR     = ROOT / "data"
TOOLS_DIR    = Path(__file__).parent
FRONTEND_DIR = ROOT / "frontend"

_ZONE_COMMENT_TEMPLATE_PATH = TOOLS_DIR / "zone_comment_template.md"
def _zone_comment_template() -> str:
    try:
        return _ZONE_COMMENT_TEMPLATE_PATH.read_text(encoding="utf-8")
    except Exception:
        return "## Story Driver ##\n"

sys.path.insert(0, str(ROOT))
from engine.toml_io import load as toml_load, dump as toml_dump
import engine.world_config as wc


# ── Threading HTTP server ─────────────────────────────────────────────────────

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTP server that handles each connection in a dedicated thread."""
    daemon_threads = True


# ── Game session helpers ──────────────────────────────────────────────────────

def _char_snapshot_data(player) -> dict:
    """Build a char_snapshot payload dict from a live Player object."""
    equipped = []
    equipped_obj_ids: set[int] = set()
    for slot, item in (player.equipped or {}).items():
        if item:
            equipped_obj_ids.add(id(item))
            equipped.append({
                "slot": slot,
                "id":   item.get("id", ""),
                "name": item.get("name", slot),
            })

    # player.inventory contains ALL items including equipped ones (same object
    # references).  Filter by Python identity so equipped items don't appear
    # twice — once in Equipped, once in Inventory.
    inventory = []
    for item in (player.inventory or []):
        if id(item) in equipped_obj_ids:
            continue
        inventory.append({
            "id":         item.get("id", ""),
            "name":       item.get("name", item.get("id", "")),
            "equip_slot": item.get("equip_slot", ""),
        })

    # Status effects: wc.STATUS_EFFECTS uses "label" not "name".
    status_effects = []
    for eid, turns in (player.status_effects or {}).items():
        effect_def = wc.get_status_effect(eid) or {}
        status_effects.append({
            "id":         eid,
            "name":       effect_def.get("label", eid),
            "turns_left": turns if isinstance(turns, int) else 0,
        })

    world_attrs = []
    for attr_def in (wc.PLAYER_ATTRS or []):
        aid   = attr_def.get("id", "")
        value = (player.world_attrs or {}).get(aid, attr_def.get("default", 0))
        world_attrs.append({
            "id":      aid,
            "name":    attr_def.get("name", aid),
            "value":   value,
            "min":     attr_def.get("min", 0),
            "max":     attr_def.get("max", 100),
            "display": attr_def.get("display", "number"),
        })

    return {
        "type":           "char_snapshot",
        "equipped":       equipped,
        "inventory":      inventory,
        "status_effects": status_effects,
        "world_attrs":    world_attrs,
    }


# ── Game session ──────────────────────────────────────────────────────────────

_game_session: "GameSession | None" = None
_session_lock = threading.Lock()


class GameSession:
    """Runs the Delve engine in a background thread and bridges it to HTTP.

    Input (browser → engine): call ``send(cmd)`` to push a command string.
    Output (engine → browser): read SSE events from ``output_queue``.

    Event dicts pushed to ``output_queue``:
      {"type": "msg",         "tag": str, "text": str}   — engine Msg
      {"type": "prompt",      "text": str}                — blocking input prompt
      {"type": "player_died"}                             — player HP reached 0
      {"type": "error",       "text": str}                — engine crash
      {"type": "quit"}                                    — session ended
    """

    def __init__(self, world_path: Path, player_name: str) -> None:
        self.world_path   = world_path
        self.player_name  = player_name
        self.input_queue  = queue.Queue()
        self.output_queue = queue.Queue()
        self.alive              = True
        self._player            = None   # set once engine initialises in _run
        self._processor         = None   # set once engine initialises in _run
        self._last_char_snapshot = None  # cached by game thread; read safely by HTTP thread
        self._thread = threading.Thread(
            target=self._run, daemon=True, name=f"game-{player_name}"
        )
        self._thread.start()

    # ── Engine thread ─────────────────────────────────────────────────────────

    def _run(self) -> None:
        try:
            import engine.world_config as wc
            from engine.events import EventBus, Event
            from engine.world import World
            from engine.player import Player
            from engine.commands import CommandProcessor

            bus = EventBus()
            bus.subscribe(Event.OUTPUT,      self._on_output)
            bus.subscribe(Event.PLAYER_DIED, self._on_player_died)

            wc.init(self.world_path)
            world = World(self.world_path)

            player = Player.load(self.player_name)
            if player is None:
                player = Player.create_new(self.player_name)
                player.world_id = self.world_path.name
                player.room_id  = world.start_room
                player.save()

            world.attach_player(player)
            self._player    = player
            self._processor = CommandProcessor(world, player, bus,
                                               input_fn=self._input_fn)
            self._processor.do_look()
            self._emit_room_snapshot(world, player)
            self._emit_char_snapshot(player)

            while not self._processor.quit_requested:
                try:
                    cmd = self.input_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                self._processor.process(cmd)
                self._emit_room_snapshot(world, player)
                self._emit_char_snapshot(player)

        except Exception as exc:
            import traceback
            self.output_queue.put({
                "type": "error",
                "text": f"Engine error: {exc}\n{traceback.format_exc()}",
            })
        finally:
            self.alive = False
            self.output_queue.put({"type": "quit"})

    def _input_fn(self, prompt: str = "", choices=None) -> str:
        """Called by the engine when blocking input is needed (dialogue, crafting).

        Queues a ``prompt`` event so the browser can display the prompt, then
        blocks until the client sends a response via POST /game/command.
        """
        if prompt and prompt.strip():
            self.output_queue.put({"type": "prompt", "text": prompt.strip()})
        return self.input_queue.get()   # blocks until the client responds

    def _emit_char_snapshot(self, player) -> None:
        """Push a char_snapshot event so the browser can update the character panel.

        Also caches the snapshot so the GET /game/char_snapshot endpoint can return
        it without accessing player state from the HTTP handler thread.
        """
        try:
            snap = _char_snapshot_data(player)
            self._last_char_snapshot = snap  # written only from game thread
            self.output_queue.put(snap)
        except Exception as exc:
            import traceback
            print(f"[wct] char_snapshot error: {exc}\n{traceback.format_exc()}",
                  file=sys.stderr)

    def _emit_room_snapshot(self, world, player) -> None:
        """Push a room_snapshot event so the browser can update the sidebar."""
        try:
            room = world.prepare_room(player.room_id, player)
            if not room:
                return
            exits = []
            for direction, v in room.get("exits", {}).items():
                if isinstance(v, dict):
                    exits.append({
                        "dir":    direction,
                        "target": v.get("to", ""),
                        "locked": bool(v.get("locked", False)),
                    })
                else:
                    exits.append({"dir": direction, "target": v, "locked": False})

            npcs = []
            for npc in (room.get("_npcs") or []):
                if npc.get("hp", 1) <= 0:
                    continue
                npcs.append({
                    "id":      npc.get("id", ""),
                    "name":    npc.get("name", ""),
                    "hostile": bool(npc.get("hostile", False)),
                })

            items = []
            for item in (room.get("items") or []):
                items.append({
                    "id":   item.get("id", ""),
                    "name": item.get("name", item.get("id", "")),
                })

            self.output_queue.put({
                "type":  "room_snapshot",
                "exits": exits,
                "npcs":  npcs,
                "items": items,
            })
        except Exception as exc:
            import traceback
            print(f"[wct] room_snapshot error: {exc}\n{traceback.format_exc()}",
                  file=sys.stderr)

    def _on_output(self, msg) -> None:
        self.output_queue.put({"type": "msg", "tag": msg.tag, "text": msg.text})

    def _on_player_died(self) -> None:
        self.output_queue.put({"type": "player_died"})
        # Mirror what the CLI does: show the room at the respawn point.
        if self._processor:
            self._processor.process("look")

    # ── Public interface ──────────────────────────────────────────────────────

    def send(self, cmd: str) -> None:
        """Push a command string into the engine's input queue."""
        self.input_queue.put(cmd)

    @property
    def status(self) -> dict:
        p = self._player
        return {
            "alive":  self.alive,
            "world":  self.world_path.name,
            "player": p.name    if p else "",
            "hp":     p.hp      if p else 0,
            "max_hp": p.max_hp  if p else 0,
            "room":   p.room_id if p else "",
        }


# ── TOML helpers ──────────────────────────────────────────────────────────────

def _load_world(world_id: str) -> dict:
    """Load a world's zones into a JSON-serialisable structure."""
    world_base = DATA_DIR / world_id
    if not world_base.is_dir():
        return {"error": f"World '{world_id}' not found", "zones": {}}
    world = {
        "zones":  {},   # zone_id → {rooms, items, npcs, quests, dialogues, companions, crafting}
    }
    skip = {"zone_state", "players", "__pycache__"}
    for zone_dir in sorted(world_base.iterdir()):
        if not zone_dir.is_dir() or zone_dir.name in skip:
            continue
        # Skip non-zone dirs (config.py is a file, not a dir)
        zid = zone_dir.name
        zone: dict = {
            "id":         zid,
            "rooms":      [],
            "items":      [],
            "npcs":       [],
            "quests":     [],
            "dialogues":  [],
            "companions": [],
            "crafting":   [],
            "styles":     [],
            "processes":  [],
            "files":      [],   # all .toml files found
        }

        # Main TOML files (rooms, items, npcs, etc.)
        for path in sorted(zone_dir.glob("*.toml")):
            try:
                data = toml_load(path)
            except Exception as e:
                data = {"_error": str(e)}
            if path.name == "zone.toml":
                zone["meta"] = {
                    "id":            data.get("id", zid),
                    "name":          data.get("name", zid),
                    "description":   data.get("description", ""),
                    "admin_comment": data.get("admin_comment", _zone_comment_template()),
                    "prefix":        data.get("prefix", ""),
                    "_file":         str(path.relative_to(ROOT)),
                }
                continue
            zone["files"].append(str(path.relative_to(ROOT)))
            for room in data.get("room", []):
                room["_file"] = str(path.relative_to(ROOT))
                zone["rooms"].append(room)
            for item in data.get("item", []):
                item["_file"] = str(path.relative_to(ROOT))
                zone["items"].append(item)
            for npc in data.get("npc", []):
                npc["_file"] = str(path.relative_to(ROOT))
                zone["npcs"].append(npc)

        # Quests
        quest_dir = zone_dir / "quests"
        if quest_dir.exists():
            for path in sorted(quest_dir.glob("*.toml")):
                try:
                    data = toml_load(path)
                    data["_file"] = str(path.relative_to(ROOT))
                    data["_id"]   = path.stem
                    zone["quests"].append(data)
                except Exception:
                    pass

        # Dialogues
        dlg_dir = zone_dir / "dialogues"
        if dlg_dir.exists():
            for path in sorted(dlg_dir.glob("*.toml")):
                try:
                    data = toml_load(path)
                    data["_file"]   = str(path.relative_to(ROOT))
                    data["_npc_id"] = path.stem
                    zone["dialogues"].append(data)
                except Exception:
                    pass

        # Companions
        comp_dir = zone_dir / "companions"
        if comp_dir.exists():
            for path in sorted(comp_dir.glob("*.toml")):
                try:
                    data = toml_load(path)
                    data["_file"] = str(path.relative_to(ROOT))
                    zone["companions"].append(data)
                except Exception:
                    pass

        # Crafting
        craft_dir = zone_dir / "crafting"
        if craft_dir.exists():
            for path in sorted(craft_dir.glob("*.toml")):
                try:
                    data = toml_load(path)
                    data["_file"]   = str(path.relative_to(ROOT))
                    data["_npc_id"] = path.stem
                    zone["crafting"].append(data)
                except Exception:
                    pass

        # Styles
        styles_dir = zone_dir / "styles"
        if styles_dir.exists():
            for path in sorted(styles_dir.glob("*.toml")):
                try:
                    data = toml_load(path)
                    for style in data.get("style", []):
                        style["_file"] = str(path.relative_to(ROOT))
                        zone["styles"].append(style)
                except Exception:
                    pass

        # Processes
        proc_file = zone_dir / "processes.toml"
        if proc_file.exists():
            try:
                data = toml_load(proc_file)
                for proc in data.get("process", []):
                    proc["_file"] = str(proc_file.relative_to(ROOT))
                    zone["processes"].append(proc)
            except Exception:
                pass

        world["zones"][zid] = zone

    return world


def _rename_id_in_world(world_path: Path, old_id: str, new_id: str) -> list[str]:
    """
    Rename an entity ID across all TOML files in a world.
    Updates known reference fields (item_id, npc_id, room_id, exits, spawns, etc.)
    and renames any dialogues/<old_id>.toml file.
    Returns list of changed file paths (relative to ROOT).
    """
    SCALAR_FIELDS = {
        'id', 'item_id', 'npc_id', 'room_id', 'to_room', 'from_room',
        'quest_id', 'style_id', 'companion_id', 'learned_from', 'style',
        'giver', 'location', 'default_style', 'result',
    }
    LIST_FIELDS = {'reward_items', 'spawns', 'items', 'components'}

    def _replace(obj):
        """Return (new_obj, changed). Only replaces in known fields, not arbitrary strings."""
        if isinstance(obj, list):
            changed = False
            result = []
            for item in obj:
                new_item, c = _replace(item)
                result.append(new_item)
                changed = changed or c
            return result, changed

        if isinstance(obj, dict):
            changed = False
            result = {}
            for k, v in obj.items():
                if k in SCALAR_FIELDS and v == old_id:
                    result[k] = new_id
                    changed = True
                elif k in LIST_FIELDS and isinstance(v, list):
                    new_list = []
                    for item in v:
                        if item == old_id:
                            new_list.append(new_id)
                            changed = True
                        else:
                            new_item, c = _replace(item)
                            new_list.append(new_item)
                            changed = changed or c
                    result[k] = new_list
                elif k == 'exits' and isinstance(v, dict):
                    new_exits = {}
                    for dir_, dest in v.items():
                        if isinstance(dest, str) and dest == old_id:
                            new_exits[dir_] = new_id
                            changed = True
                        elif isinstance(dest, dict):
                            new_dest, c = _replace(dest)
                            new_exits[dir_] = new_dest
                            changed = changed or c
                        else:
                            new_exits[dir_] = dest
                    result[k] = new_exits
                elif isinstance(v, (dict, list)):
                    new_v, c = _replace(v)
                    result[k] = new_v
                    changed = changed or c
                else:
                    result[k] = v
            return result, changed

        return obj, False

    changed_files: list[str] = []

    for toml_path in sorted(world_path.rglob('*.toml')):
        try:
            data = toml_load(toml_path)
        except Exception:
            continue
        new_data, changed = _replace(data)
        if changed:
            try:
                toml_dump(toml_path, new_data)
                changed_files.append(str(toml_path.relative_to(ROOT)))
            except Exception:
                pass

    # Rename dialogue file if one exists for old_id
    for dlg_path in world_path.rglob(f'dialogues/{old_id}.toml'):
        new_path = dlg_path.parent / f'{new_id}.toml'
        try:
            dlg_path.rename(new_path)
            changed_files.append(f'{dlg_path.relative_to(ROOT)} -> dialogues/{new_id}.toml')
        except Exception:
            pass

    # Rename quest file if one exists for old_id
    for quest_path in world_path.rglob(f'quests/{old_id}.toml'):
        new_path = quest_path.parent / f'{new_id}.toml'
        try:
            quest_path.rename(new_path)
            changed_files.append(f'{quest_path.relative_to(ROOT)} -> quests/{new_id}.toml')
        except Exception:
            pass

    return changed_files


def _write_field(file_rel: str, record_type: str, record_id: str,
                 field: str, value) -> tuple[bool, str]:
    """
    Write a single field change back to a TOML file.

    Strategy: load the file as raw text, find the [[record_type]] block with
    matching id, update the field, write back.  For complex structures (lists,
    dicts) this is non-trivial; we re-serialize via our toml_dump.

    Returns (success, error_message).
    """
    path = ROOT / file_rel
    if not path.exists():
        return False, f"File not found: {file_rel}"
    try:
        data = toml_load(path)
    except Exception as e:
        return False, f"Parse error: {e}"

    # Find the right record
    changed = False
    for record in data.get(record_type, []):
        if record.get("id") == record_id:
            record[field] = value
            changed = True
            break

    if not changed:
        return False, f"No {record_type} with id='{record_id}' in {file_rel}"

    try:
        toml_dump(path, data)
        return True, ""
    except Exception as e:
        return False, f"Write error: {e}"


def _write_file(file_rel: str, data: dict) -> tuple[bool, str]:
    """Overwrite an entire TOML file with new data."""
    path = ROOT / file_rel
    try:
        toml_dump(path, data)
        return True, ""
    except Exception as e:
        return False, f"Write error: {e}"


def _apply_quest_trigger(t: dict, world_id: str) -> None:
    """Apply one staged trigger entry to the appropriate TOML file.

    Trigger dict keys:
      trigger_type  room_enter | item_get | item_use | npc_kill | npc_dialogue | npc_give
      zone_id       zone the entity lives in
      entity_id     id of the room/item/npc
      quest_id      quest this advances
      step          int step number, or None → complete_quest
      guard_flag    flag name to wrap in if_not_flag guard, or None
      --- dialogue only ---
      node_id           id for the new dialogue node
      node_line         NPC's text for the new node
      parent_node_id    existing node id to attach the response to
      response_text     player's response text
      response_condition  flag name for response condition, or None
      --- npc_give only ---
      item_id         item the player gives
      accept_message  message shown on accept
    """
    ttype     = t.get("trigger_type", "")
    zone_id    = t.get("zone_id", "")
    entity_id  = t.get("entity_id", "")
    quest_id   = t.get("quest_id", "")
    step       = t.get("step")         # None → complete_quest
    guard_type = t.get("guard_type", "flag" if t.get("guard_flag") else "none")
    guard_flag = t.get("guard_flag")   # used when guard_type == "flag"
    guard_step = t.get("guard_step")   # used when guard_type == "quest_active"

    if not all([ttype, zone_id, entity_id, quest_id]):
        raise ValueError("Missing required fields in trigger")

    base = DATA_DIR / world_id / zone_id

    # Build the core quest op
    if step is None:
        quest_op: dict = {"op": "complete_quest", "quest_id": quest_id}
    else:
        quest_op = {"op": "advance_quest", "quest_id": quest_id, "step": int(step)}

    # Optionally wrap in a guard
    if guard_type == "flag" and guard_flag:
        ops_block: list = [{"op": "if_not_flag", "flag": guard_flag,
                            "then": [{"op": "set_flag", "flag": guard_flag}, quest_op]}]
    elif guard_type == "quest_active":
        ops_block = [{"op": "if_quest", "quest_id": quest_id,
                      "step": int(guard_step) if guard_step is not None else 1,
                      "then": [quest_op]}]
    elif guard_type == "quest_complete":
        ops_block = [{"op": "if_quest_complete", "quest_id": quest_id,
                      "then": [quest_op]}]
    else:
        ops_block = [quest_op]

    # ── room/item/npc-kill ──────────────────────────────────────────
    if ttype in ("room_enter", "item_get", "item_use", "npc_kill"):
        if ttype == "room_enter":
            file_path = base / "rooms.toml"; arr_key = "room";  field = "on_enter"
        elif ttype == "item_get":
            file_path = base / "items.toml"; arr_key = "item";  field = "on_get"
        elif ttype == "item_use":
            file_path = base / "items.toml"; arr_key = "item";  field = "on_use"
        else:  # npc_kill
            file_path = base / "npcs.toml";  arr_key = "npc";   field = "kill_script"

        data = toml_load(file_path) if file_path.exists() else {}
        entity = next((e for e in data.get(arr_key, []) if e.get("id") == entity_id), None)
        if entity is None:
            raise ValueError(f"Entity '{entity_id}' not found in {file_path.name}")
        entity[field] = list(entity.get(field, [])) + ops_block
        ok, err = _write_file(str(file_path.relative_to(ROOT)), data)
        if not ok:
            raise RuntimeError(err)

    # ── npc_give ────────────────────────────────────────────────────
    elif ttype == "npc_give":
        item_id = t.get("item_id", "")
        message = t.get("accept_message", "")
        if not item_id:
            raise ValueError("item_id required for npc_give trigger")
        file_path = base / "npcs.toml"
        data = toml_load(file_path) if file_path.exists() else {}
        npc = next((n for n in data.get("npc", []) if n.get("id") == entity_id), None)
        if npc is None:
            raise ValueError(f"NPC '{entity_id}' not found in npcs.toml")
        give_entry: dict = {"item_id": item_id, "message": message, "script": ops_block}
        npc.setdefault("give_accepts", []).append(give_entry)
        ok, err = _write_file(str(file_path.relative_to(ROOT)), data)
        if not ok:
            raise RuntimeError(err)

    # ── npc_dialogue ────────────────────────────────────────────────
    elif ttype == "npc_dialogue":
        node_id    = t.get("node_id", "").strip()
        node_line  = t.get("node_line", "")
        parent_id  = t.get("parent_node_id", "root") or "root"
        resp_text  = t.get("response_text", "")
        resp_cond_flag = t.get("response_condition") or None

        if not node_id:
            raise ValueError("node_id is required for npc_dialogue trigger")

        dlg_dir  = base / "dialogues"
        dlg_file = dlg_dir / f"{entity_id}.toml"
        dlg_dir.mkdir(parents=True, exist_ok=True)
        if dlg_file.exists():
            dlg: dict = toml_load(dlg_file)
        else:
            dlg = {"node": [{"id": "root", "line": "Hello, traveller."}], "response": []}

        # Add the new node
        new_node: dict = {"id": node_id, "line": node_line, "script": ops_block}
        dlg.setdefault("node", []).append(new_node)

        # Add response from parent node
        new_response: dict = {"node": parent_id, "text": resp_text, "next": node_id}
        if resp_cond_flag:
            new_response["condition"] = {"flag": resp_cond_flag}
        dlg.setdefault("response", []).append(new_response)

        ok, err = _write_file(str(dlg_file.relative_to(ROOT)), dlg)
        if not ok:
            raise RuntimeError(err)

    else:
        raise ValueError(f"Unknown trigger type: {ttype}")


# ── HTTP Handler ──────────────────────────────────────────────────────────────

class WCTHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        # Suppress default request logging; print only errors
        if args and str(args[1]) not in ("200", "304"):
            print(f"[wct] {fmt % args}", file=sys.stderr)

    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw) if raw else {}

    def _stream_game(self) -> None:
        """Server-Sent Events stream — pushes engine output to the browser."""
        session = _game_session
        self.send_response(200)
        self.send_header("Content-Type",         "text/event-stream")
        self.send_header("Cache-Control",        "no-cache")
        self.send_header("Connection",           "keep-alive")
        self.send_header("X-Accel-Buffering",    "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        if not session:
            self.wfile.write(b'data: {"type":"no_session"}\n\n')
            self.wfile.flush()
            return

        while True:
            try:
                event = session.output_queue.get(timeout=15)
                data  = json.dumps(event, ensure_ascii=False, default=str)
                self.wfile.write(f"data: {data}\n\n".encode())
                self.wfile.flush()
                if event.get("type") == "quit":
                    break
            except queue.Empty:
                # Keepalive comment line (not a data event; browser ignores it)
                try:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                except OSError:
                    break
            except OSError:
                break

    # ── GET ───────────────────────────────────────────────────────────────────

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/") or "/"

        if path == "/" or path == "/index.html":
            html_path = TOOLS_DIR / "wct.html"
            if html_path.exists():
                self._send_html(html_path.read_text(encoding="utf-8"))
            else:
                self._send_json({"error": "wct.html not found"}, 404)

        elif path.startswith("/css/"):
            fname    = path[5:]
            css_path = TOOLS_DIR / fname
            if css_path.suffix == ".css" and css_path.exists():
                body = css_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/css; charset=utf-8")
                self.send_header("Content-Length", len(body))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._send_json({"error": "not found"}, 404)

        elif path == "/api/world":
            qs = parse_qs(parsed.query)
            world_id = (qs.get("world_id") or [""])[0].strip()
            if not world_id:
                self._send_json({"error": "world_id query param required"}, 400)
                return
            self._send_json(_load_world(world_id))

        elif path == "/api/world_md":
            qs       = parse_qs(parsed.query)
            world_id = (qs.get("world_id") or [""])[0].strip()
            if not world_id:
                self._send_json({"error": "world_id required"}, 400)
                return
            md_path = DATA_DIR / world_id / "world.md"
            content = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
            self._send_json({"content": content})

        elif path == "/api/world_config":
            qs = parse_qs(parsed.query)
            world_id = (qs.get("world_id") or [""])[0].strip()
            if not world_id:
                self._send_json({"error": "world_id query param required"}, 400)
                return
            world_path = DATA_DIR / world_id
            if not world_path.is_dir() or not ((world_path / "config.toml").exists() or (world_path / "config.py").exists()):
                self._send_json({"error": f"World '{world_id}' not found"}, 404)
                return
            wc.init(world_path)
            # Load styles for this world
            import engine.styles as _styles
            _styles._STYLES = {}
            _styles.reload()
            styles_list = [
                {"id": sid, "name": s.get("name", sid)}
                for sid, s in sorted(_styles.get_all().items())
            ]
            self._send_json({
                "world_name":      wc.WORLD_NAME,
                "skills":          wc.SKILLS,
                "new_char_hp":     wc.NEW_CHAR_HP,
                "currency_name":   wc.CURRENCY_NAME,
                "default_style":   wc.DEFAULT_STYLE,
                "equipment_slots": list(wc.EQUIPMENT_SLOTS),
                "player_attrs":    wc.PLAYER_ATTRS,
                "status_effects":  wc.STATUS_EFFECTS,
                "styles":          styles_list,
            })

        elif path == "/api/validate":
            # Run the validator and return its output
            import subprocess
            result = subprocess.run(
                [sys.executable, str(TOOLS_DIR / "validate.py")],
                cwd=str(ROOT),
                capture_output=True, text=True, timeout=30
            )
            self._send_json({
                "stdout": result.stdout,
                "stderr": result.stderr,
                "ok":     result.returncode == 0,
            })

        elif path == "/api/validate_issues":
            # Return structured issue dicts for the WCT error panel.
            # Uses the same engine/validate_world logic as validate.py.
            from engine.validate_world import validate_world as _vw
            qs = parse_qs(parsed.query)
            world_id = qs.get("world_id", [""])[0]
            if not world_id:
                self._send_json({"ok": False, "error": "world_id required", "issues": []})
                return
            world_path_v = DATA_DIR / world_id
            if not world_path_v.is_dir():
                self._send_json({"ok": False, "error": f"World '{world_id}' not found", "issues": []})
                return
            try:
                issues = _vw(world_path_v)
                self._send_json({"ok": True, "issues": issues})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc), "issues": []})

        # ── Game routes ───────────────────────────────────────────────────────

        elif path == "/game":
            html_path = FRONTEND_DIR / "game.html"
            if html_path.exists():
                self._send_html(html_path.read_text(encoding="utf-8"))
            else:
                self._send_json({"error": "game.html not found"}, 404)

        elif path == "/game/worlds":
            from engine.world_config import list_worlds, peek_world_name
            worlds = [
                {"id": w.name, "name": peek_world_name(w)}
                for w in list_worlds(DATA_DIR)
            ]
            self._send_json({"worlds": worlds})

        elif path == "/game/players":
            players_dir = DATA_DIR / "players"
            players = []
            if players_dir.exists():
                for d in sorted(players_dir.iterdir()):
                    if d.is_dir() and (d / "player.toml").exists():
                        try:
                            pdata = toml_load(d / "player.toml")
                            players.append({
                                "name":     pdata.get("name", d.name),
                                "world_id": pdata.get("world_id", ""),
                                "hp":       pdata.get("hp", 0),
                                "max_hp":   pdata.get("max_hp", 0),
                            })
                        except Exception:
                            pass
            self._send_json({"players": players})

        elif path == "/game/status":
            gs = _game_session
            if gs and gs.alive:
                self._send_json({"session": True, **gs.status})
            else:
                self._send_json({"session": False})

        elif path == "/game/char_snapshot":
            gs   = _game_session
            snap = gs._last_char_snapshot if (gs and gs.alive) else None
            if snap:
                self._send_json({"session": True, **snap})
            else:
                self._send_json({"session": False})

        elif path == "/game/stream":
            self._stream_game()

        else:
            self._send_json({"error": f"Not found: {path}"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ── POST ──────────────────────────────────────────────────────────────────

    def do_POST(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/")
        body   = self._read_json_body()

        if path == "/api/write_field":
            ok, err = _write_field(
                body.get("file", ""),
                body.get("record_type", ""),
                body.get("record_id", ""),
                body.get("field", ""),
                body.get("value"),
            )
            self._send_json({"ok": ok, "error": err})

        elif path == "/api/write_file":
            ok, err = _write_file(body.get("file", ""), body.get("data", {}))
            self._send_json({"ok": ok, "error": err})

        elif path == "/api/write_config":
            world_id = body.get("world_id", "").strip()
            cfg      = body.get("config", {})
            if not world_id:
                self._send_json({"ok": False, "error": "world_id required"})
                return
            world_path = DATA_DIR / world_id
            if not world_path.is_dir():
                self._send_json({"ok": False, "error": f"World '{world_id}' not found"})
                return
            cfg_path = world_path / "config.toml"
            ok, err = _write_file(str(cfg_path.relative_to(ROOT)), cfg)
            if ok:
                # Re-init so the running server reflects the new config
                wc.init(world_path)
            self._send_json({"ok": ok, "error": err})

        elif path == "/api/create_room":
            world_id  = body.get("world_id", "")
            zone_id   = body.get("zone_id", "")
            room_data = body.get("room", {})
            if not world_id or not zone_id or not room_data.get("id"):
                self._send_json({"ok": False, "error": "world_id, zone_id and room.id required"})
                return
            file_path = DATA_DIR / world_id / zone_id / "rooms.toml"
            try:
                existing = toml_load(file_path) if file_path.exists() else {}
            except Exception:
                existing = {}
            existing.setdefault("room", []).append(room_data)
            ok, err = _write_file(str(file_path.relative_to(ROOT)), existing)
            self._send_json({"ok": ok, "error": err})

        elif path == "/api/create_npc":
            world_id = body.get("world_id", "")
            zone_id  = body.get("zone_id", "")
            npc_data = body.get("npc", {})
            if not world_id or not zone_id or not npc_data.get("id"):
                self._send_json({"ok": False, "error": "world_id, zone_id and npc.id required"})
                return
            file_path = DATA_DIR / world_id / zone_id / "npcs.toml"
            try:
                existing = toml_load(file_path) if file_path.exists() else {}
            except Exception:
                existing = {}
            existing.setdefault("npc", []).append(npc_data)
            ok, err = _write_file(str(file_path.relative_to(ROOT)), existing)
            self._send_json({"ok": ok, "error": err})

        elif path == "/api/create_item":
            world_id  = body.get("world_id", "")
            zone_id   = body.get("zone_id", "")
            item_data = body.get("item", {})
            if not world_id or not zone_id or not item_data.get("id"):
                self._send_json({"ok": False, "error": "world_id, zone_id and item.id required"})
                return
            file_path = DATA_DIR / world_id / zone_id / "items.toml"
            try:
                existing = toml_load(file_path) if file_path.exists() else {}
            except Exception:
                existing = {}
            existing.setdefault("item", []).append(item_data)
            ok, err = _write_file(str(file_path.relative_to(ROOT)), existing)
            self._send_json({"ok": ok, "error": err})

        elif path == "/api/create_process":
            world_id  = body.get("world_id", "")
            zone_id   = body.get("zone_id", "")
            proc_data = body.get("process", {})
            if not world_id or not zone_id or not proc_data.get("id"):
                self._send_json({"ok": False, "error": "world_id, zone_id and process.id required"})
                return
            file_path = DATA_DIR / world_id / zone_id / "processes.toml"
            try:
                existing = toml_load(file_path) if file_path.exists() else {}
            except Exception:
                existing = {}
            existing.setdefault("process", []).append(proc_data)
            ok, err = _write_file(str(file_path.relative_to(ROOT)), existing)
            self._send_json({"ok": ok, "error": err,
                             "file": str(file_path.relative_to(ROOT))})

        elif path == "/api/create_quest":
            world_id   = body.get("world_id", "")
            zone_id    = body.get("zone_id", "")
            quest_data = body.get("quest", {})
            qid        = quest_data.get("id", "").strip()
            if not world_id or not zone_id or not qid:
                self._send_json({"ok": False, "error": "world_id, zone_id and quest.id required"})
                return
            quest_dir = DATA_DIR / world_id / zone_id / "quests"
            quest_dir.mkdir(parents=True, exist_ok=True)
            file_path = quest_dir / f"{qid}.toml"
            if file_path.exists():
                self._send_json({"ok": False, "error": f"Quest '{qid}' already exists"})
                return
            ok, err = _write_file(str(file_path.relative_to(ROOT)), quest_data)
            self._send_json({"ok": ok, "error": err, "file": str(file_path.relative_to(ROOT))})

        elif path == "/api/create_dialogue":
            world_id = body.get("world_id", "")
            zone_id  = body.get("zone_id", "")
            npc_id   = body.get("npc_id", "").strip()
            if not world_id or not zone_id or not npc_id:
                self._send_json({"ok": False, "error": "world_id, zone_id and npc_id required"})
                return
            dlg_dir   = DATA_DIR / world_id / zone_id / "dialogues"
            dlg_dir.mkdir(parents=True, exist_ok=True)
            file_path = dlg_dir / f"{npc_id}.toml"
            if file_path.exists():
                self._send_json({"ok": False, "error": f"Dialogue '{npc_id}.toml' already exists"})
                return
            starter = {"node": [{"id": "root", "line": "Hello, traveller."}], "response": []}
            ok, err = _write_file(str(file_path.relative_to(ROOT)), starter)
            self._send_json({"ok": ok, "error": err, "file": str(file_path.relative_to(ROOT))})

        elif path == "/api/create_crafting":
            world_id = body.get("world_id", "")
            zone_id  = body.get("zone_id", "")
            npc_id   = body.get("npc_id", "").strip()
            if not world_id or not zone_id or not npc_id:
                self._send_json({"ok": False, "error": "world_id, zone_id and npc_id required"})
                return
            craft_dir  = DATA_DIR / world_id / zone_id / "crafting"
            craft_dir.mkdir(parents=True, exist_ok=True)
            file_path  = craft_dir / f"{npc_id}.toml"
            if file_path.exists():
                self._send_json({"ok": False, "error": f"Crafting '{npc_id}.toml' already exists"})
                return
            starter = {"commission": []}
            ok, err = _write_file(str(file_path.relative_to(ROOT)), starter)
            self._send_json({"ok": ok, "error": err, "file": str(file_path.relative_to(ROOT))})

        elif path == "/api/save_staged_triggers":
            world_id = body.get("world_id", "")
            triggers = body.get("triggers", [])
            if not world_id:
                self._send_json({"ok": False, "error": "world_id required"})
                return
            errors = []
            for trig in triggers:
                try:
                    _apply_quest_trigger(trig, world_id)
                except Exception as e:
                    errors.append(f"{trig.get('entity_id','?')}: {e}")
            self._send_json({"ok": len(errors) == 0, "errors": errors})

        elif path == "/api/zone_comment":
            world_id = body.get("world_id", "")
            zone_id  = body.get("zone_id", "")
            comment  = body.get("admin_comment", "")
            if not world_id or not zone_id:
                self._send_json({"ok": False, "error": "world_id and zone_id required"})
                return
            zone_toml = DATA_DIR / world_id / zone_id / "zone.toml"
            if zone_toml.exists():
                try:
                    data = toml_load(zone_toml)
                except Exception as e:
                    self._send_json({"ok": False, "error": f"Parse error: {e}"})
                    return
            else:
                data = {"id": zone_id, "name": zone_id}
            data["admin_comment"] = comment
            prefix = body.get("prefix", None)
            if prefix is not None:
                if prefix:
                    data["prefix"] = prefix
                else:
                    data.pop("prefix", None)
            ok, err = _write_file(str(zone_toml.relative_to(ROOT)), data)
            self._send_json({"ok": ok, "error": err})

        elif path == "/api/create_zone":
            world_id = body.get("world_id", "")
            zone_id  = body.get("zone_id", "").strip().lower().replace(" ", "_")
            if not world_id or not zone_id:
                self._send_json({"ok": False, "error": "world_id and zone_id required"})
                return
            zone_dir = DATA_DIR / world_id / zone_id
            if zone_dir.exists():
                self._send_json({"ok": False, "error": f"Zone '{zone_id}' already exists"})
                return
            zone_dir.mkdir(parents=True)
            (zone_dir / "rooms.toml").write_text(f"# {zone_id} rooms\n")
            (zone_dir / "items.toml").write_text(f"# {zone_id} items\n")
            (zone_dir / "npcs.toml").write_text(f"# {zone_id} NPCs\n")
            self._send_json({"ok": True, "zone_id": zone_id})

        elif path == "/api/rename_id":
            world_id = body.get("world_id", "")
            old_id   = body.get("old_id", "").strip()
            new_id   = body.get("new_id", "").strip()
            if not world_id or not old_id or not new_id:
                self._send_json({"ok": False, "error": "world_id, old_id, new_id required"})
                return
            if old_id == new_id:
                self._send_json({"ok": True, "changed": [], "error": ""})
                return
            world_path = DATA_DIR / world_id
            if not world_path.is_dir():
                self._send_json({"ok": False, "error": f"World '{world_id}' not found"})
                return
            try:
                changed = _rename_id_in_world(world_path, old_id, new_id)
                self._send_json({"ok": True, "changed": changed, "error": ""})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)})

        elif path == "/api/delete_object":
            world_id = body.get("world_id", "")
            zone_id  = body.get("zone_id", "")
            type_    = body.get("type", "")
            eid      = body.get("id", "")
            file_rel = body.get("file", "")
            if not world_id or not zone_id or not type_ or not eid:
                self._send_json({"ok": False, "error": "world_id, zone_id, type and id required"})
                return

            if type_ in ("room", "npc", "item", "style", "process"):
                if not file_rel:
                    self._send_json({"ok": False, "error": "file required for room/npc/item/style/process"})
                    return
                file_path = ROOT / file_rel
                try:
                    data = toml_load(file_path) if file_path.exists() else {}
                except Exception as e:
                    self._send_json({"ok": False, "error": f"Parse error: {e}"})
                    return
                key      = type_   # "room", "npc", "item", "style"
                original = data.get(key, [])
                filtered = [r for r in original if r.get("id") != eid]
                if len(filtered) == len(original):
                    self._send_json({"ok": False, "error": f"{type_} '{eid}' not found in file"})
                    return
                data[key] = filtered
                ok, err = _write_file(file_rel, data)
                self._send_json({"ok": ok, "error": err})

            elif type_ == "quest":
                quest_path = DATA_DIR / world_id / zone_id / "quests" / f"{eid}.toml"
                if not quest_path.exists():
                    self._send_json({"ok": False, "error": f"Quest file '{eid}.toml' not found"})
                    return
                try:
                    quest_path.unlink()
                    self._send_json({"ok": True, "error": ""})
                except Exception as e:
                    self._send_json({"ok": False, "error": str(e)})

            elif type_ == "dialogue":
                dlg_path = DATA_DIR / world_id / zone_id / "dialogues" / f"{eid}.toml"
                if not dlg_path.exists():
                    self._send_json({"ok": False, "error": f"Dialogue '{eid}.toml' not found"})
                    return
                try:
                    dlg_path.unlink()
                    self._send_json({"ok": True, "error": ""})
                except Exception as e:
                    self._send_json({"ok": False, "error": str(e)})

            elif type_ == "crafting":
                craft_path = DATA_DIR / world_id / zone_id / "crafting" / f"{eid}.toml"
                if not craft_path.exists():
                    self._send_json({"ok": False, "error": f"Crafting '{eid}.toml' not found"})
                    return
                try:
                    craft_path.unlink()
                    self._send_json({"ok": True, "error": ""})
                except Exception as e:
                    self._send_json({"ok": False, "error": str(e)})

            else:
                self._send_json({"ok": False, "error": f"Unsupported type '{type_}'"})

        elif path == "/api/create_world":
            world_id      = body.get("world_id", "").strip().lower().replace(" ", "_")
            world_name    = body.get("world_name", "").strip()
            currency_name = body.get("currency_name", "gold").strip() or "gold"
            if not world_id or not world_name:
                self._send_json({"ok": False, "error": "world_id and world_name required"})
                return
            if not re.match(r'^[a-z0-9_]+$', world_id):
                self._send_json({"ok": False, "error": "world_id must be alphanumeric/underscores only"})
                return
            world_dir = DATA_DIR / world_id
            if world_dir.exists():
                self._send_json({"ok": False, "error": f"World '{world_id}' already exists"})
                return
            world_dir.mkdir(parents=True)
            config_content = (
                f'world_name      = "{world_name}"\n'
                f'currency_name   = "{currency_name}"\n'
                f'default_style   = "brawling"\n'
                f'new_char_hp     = 100\n'
                f'vision_threshold = 3\n'
            )
            (world_dir / "config.toml").write_text(config_content, encoding="utf-8")
            start_zone = world_id + "_start"
            start_dir  = world_dir / start_zone
            start_dir.mkdir()
            (start_dir / "rooms.toml").write_text(f"# {start_zone} rooms\n", encoding="utf-8")
            (start_dir / "items.toml").write_text(f"# {start_zone} items\n", encoding="utf-8")
            (start_dir / "npcs.toml").write_text(f"# {start_zone} NPCs\n",   encoding="utf-8")
            self._send_json({"ok": True, "world_id": world_id})

        elif path == "/api/delete_zone":
            world_id = body.get("world_id", "")
            zone_id  = body.get("zone_id", "")
            actions  = body.get("actions", [])  # [{type, id, action, dest_zone, file}]
            if not world_id or not zone_id:
                self._send_json({"ok": False, "error": "world_id and zone_id required"})
                return
            zone_dir = DATA_DIR / world_id / zone_id
            if not zone_dir.exists():
                self._send_json({"ok": False, "error": f"Zone '{zone_id}' not found"})
                return
            errors = []
            for act in actions:
                atype     = act.get("type", "")
                eid       = act.get("id", "")
                action    = act.get("action", "delete")
                dest_zone = act.get("dest_zone", "")
                file_rel  = act.get("file", "")

                if action == "delete":
                    if atype in ("room", "npc", "item", "style"):
                        if not file_rel:
                            errors.append(f"No file for {atype} {eid}")
                            continue
                        fp = ROOT / file_rel
                        try:
                            data = toml_load(fp) if fp.exists() else {}
                            data[atype] = [r for r in data.get(atype, []) if r.get("id") != eid]
                            _write_file(file_rel, data)
                        except Exception as e:
                            errors.append(str(e))
                    elif atype == "quest":
                        p = zone_dir / "quests" / f"{eid}.toml"
                        if p.exists():
                            try:
                                p.unlink()
                            except Exception as e:
                                errors.append(str(e))
                    elif atype == "dialogue":
                        p = zone_dir / "dialogues" / f"{eid}.toml"
                        if p.exists():
                            try:
                                p.unlink()
                            except Exception as e:
                                errors.append(str(e))

                elif action == "move" and dest_zone:
                    dest_dir = DATA_DIR / world_id / dest_zone
                    if not dest_dir.exists():
                        errors.append(f"Destination zone '{dest_zone}' not found")
                        continue
                    if atype in ("room", "npc", "item", "style"):
                        if not file_rel:
                            errors.append(f"No file for {atype} {eid}")
                            continue
                        src_fp = ROOT / file_rel
                        dest_file = dest_dir / Path(file_rel).name
                        try:
                            src_data  = toml_load(src_fp) if src_fp.exists() else {}
                            dest_data = toml_load(dest_file) if dest_file.exists() else {}
                            objs = src_data.get(atype, [])
                            obj  = next((o for o in objs if o.get("id") == eid), None)
                            if obj:
                                src_data[atype]  = [o for o in objs if o.get("id") != eid]
                                dest_data[atype] = dest_data.get(atype, []) + [obj]
                                _write_file(file_rel, src_data)
                                dest_rel = str(dest_file.relative_to(ROOT))
                                _write_file(dest_rel, dest_data)
                        except Exception as e:
                            errors.append(str(e))
                    elif atype == "quest":
                        src_p  = zone_dir / "quests" / f"{eid}.toml"
                        dest_q = dest_dir / "quests"
                        dest_q.mkdir(exist_ok=True)
                        dest_p = dest_q / f"{eid}.toml"
                        try:
                            shutil.move(str(src_p), str(dest_p))
                        except Exception as e:
                            errors.append(str(e))
                    elif atype == "dialogue":
                        src_p  = zone_dir / "dialogues" / f"{eid}.toml"
                        dest_d = dest_dir / "dialogues"
                        dest_d.mkdir(exist_ok=True)
                        dest_p = dest_d / f"{eid}.toml"
                        try:
                            shutil.move(str(src_p), str(dest_p))
                        except Exception as e:
                            errors.append(str(e))

            if errors:
                self._send_json({"ok": False, "error": "; ".join(errors)})
                return
            # Remove zone directory (now should be empty or only have skeleton files)
            try:
                shutil.rmtree(zone_dir)
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)})

        elif path == "/api/world_md":
            world_id = body.get("world_id", "")
            content  = body.get("content", "")
            if not world_id:
                self._send_json({"ok": False, "error": "world_id required"})
                return
            md_path = DATA_DIR / world_id / "world.md"
            try:
                md_path.write_text(content, encoding="utf-8")
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)})

        # ── Game routes ───────────────────────────────────────────────────────

        elif path == "/game/login":
            world_id    = body.get("world_id", "").strip()
            player_name = body.get("player_name", "").strip()
            if not world_id or not player_name:
                self._send_json({"ok": False, "error": "world_id and player_name required"})
                return
            world_path = DATA_DIR / world_id
            if not world_path.is_dir() or not ((world_path / "config.toml").exists() or (world_path / "config.py").exists()):
                self._send_json({"ok": False, "error": f"World '{world_id}' not found"})
                return
            with _session_lock:
                global _game_session
                if _game_session and _game_session.alive:
                    self._send_json({
                        "ok": False,
                        "error": "A session is already active — quit it first",
                    })
                    return
                _game_session = GameSession(world_path, player_name)
            self._send_json({"ok": True})

        elif path == "/game/command":
            cmd = body.get("cmd", "").strip()
            if not cmd:
                self._send_json({"ok": False, "error": "cmd required"})
                return
            gs = _game_session
            if not gs or not gs.alive:
                self._send_json({"ok": False, "error": "No active session"})
                return
            gs.send(cmd)
            self._send_json({"ok": True})

        elif path == "/game/quit":
            gs = _game_session
            if gs and gs.alive:
                gs.send("quit")
            self._send_json({"ok": True})

        else:
            self._send_json({"error": f"Unknown endpoint: {path}"}, 404)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Delve World Creation Tool server")
    parser.add_argument("--port", type=int, default=7373)
    parser.add_argument("--browser", action="store_true", help="Open the WCT in your browser automatically")
    args = parser.parse_args()

    server = ThreadingHTTPServer(("localhost", args.port), WCTHandler)
    url    = f"http://localhost:{args.port}"
    print(f"Delve WCT   →  {url}")
    print(f"Delve Game  →  {url}/game")
    print("Press Ctrl+C to stop.\n")
    if args.browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == "__main__":
    main()




