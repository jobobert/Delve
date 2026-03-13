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
                    data["_file"] = str(path.relative_to(ROOT))
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

        world["zones"][zid] = zone

    return world


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

        elif path == "/api/delete_object":
            world_id = body.get("world_id", "")
            zone_id  = body.get("zone_id", "")
            type_    = body.get("type", "")
            eid      = body.get("id", "")
            file_rel = body.get("file", "")
            if not world_id or not zone_id or not type_ or not eid:
                self._send_json({"ok": False, "error": "world_id, zone_id, type and id required"})
                return

            if type_ in ("room", "npc", "item", "style"):
                if not file_rel:
                    self._send_json({"ok": False, "error": "file required for room/npc/item/style"})
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

            else:
                self._send_json({"ok": False, "error": f"Unsupported type '{type_}'"})

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




