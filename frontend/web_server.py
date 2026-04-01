#!/usr/bin/env python3
"""
frontend/web_server.py — Delve game web frontend server.

A lightweight local HTTP server that serves the game web frontend (game.html)
and exposes the live play API (SSE stream, commands, session management).

Usage:
    python frontend/web_server.py           # starts on http://localhost:7374
    python frontend/web_server.py --port 8080
    python launch_web.py                    # recommended: opens server + browser

For the World Creation Tool (world editor), see launch_wct.py / wct/wct_server.py.
"""

from __future__ import annotations
import argparse
import json
import queue
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import urlparse

ROOT         = Path(__file__).parent.parent
DATA_DIR     = ROOT / "data"
FRONTEND_DIR = Path(__file__).parent

sys.path.insert(0, str(ROOT))
from engine.toml_io import load as toml_load
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
            "equip_slot": item.get("slot", ""),
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
        self.alive               = True
        self._player             = None   # set once engine initialises in _run
        self._processor          = None   # set once engine initialises in _run
        self._last_char_snapshot = None   # cached by game thread; read safely by HTTP thread
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
        blocks until the client sends a response via POST /command.
        """
        if prompt and prompt.strip():
            self.output_queue.put({"type": "prompt", "text": prompt.strip()})
        return self.input_queue.get()   # blocks until the client responds

    def _emit_char_snapshot(self, player) -> None:
        """Push a char_snapshot event and cache it for GET /char_snapshot."""
        try:
            snap = _char_snapshot_data(player)
            self._last_char_snapshot = snap  # written only from game thread
            self.output_queue.put(snap)
        except Exception as exc:
            import traceback
            print(f"[web] char_snapshot error: {exc}\n{traceback.format_exc()}",
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
            print(f"[web] room_snapshot error: {exc}\n{traceback.format_exc()}",
                  file=sys.stderr)

    def _on_output(self, msg) -> None:
        self.output_queue.put({"type": "msg", "tag": msg.tag, "text": msg.text})

    def _on_player_died(self) -> None:
        self.output_queue.put({"type": "player_died"})
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


# ── HTTP handler ──────────────────────────────────────────────────────────────

class GameHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # suppress default access log; errors still go to stderr

    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
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
        self.send_header("Content-Type",                 "text/event-stream")
        self.send_header("Cache-Control",                "no-cache")
        self.send_header("Connection",                   "keep-alive")
        self.send_header("X-Accel-Buffering",            "no")
        self.send_header("Access-Control-Allow-Origin",  "*")
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
            html_path = FRONTEND_DIR / "game.html"
            if html_path.exists():
                self._send_file(html_path, "text/html; charset=utf-8")
            else:
                self._send_json({"error": "game.html not found"}, 404)

        elif path == "/game.css":
            css_path = FRONTEND_DIR / "game.css"
            if css_path.exists():
                self._send_file(css_path, "text/css")
            else:
                self._send_json({"error": "game.css not found"}, 404)

        elif path == "/worlds":
            from engine.world_config import list_worlds, peek_world_name
            worlds = [
                {"id": w.name, "name": peek_world_name(w)}
                for w in list_worlds(DATA_DIR)
            ]
            self._send_json({"worlds": worlds})

        elif path == "/players":
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

        elif path == "/status":
            gs = _game_session
            if gs and gs.alive:
                self._send_json({"session": True, **gs.status})
            else:
                self._send_json({"session": False})

        elif path == "/char_snapshot":
            gs   = _game_session
            snap = gs._last_char_snapshot if (gs and gs.alive) else None
            if snap:
                self._send_json({"session": True, **snap})
            else:
                self._send_json({"session": False})

        elif path == "/stream":
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

        if path == "/login":
            world_id    = body.get("world_id", "").strip()
            player_name = body.get("player_name", "").strip()
            if not world_id or not player_name:
                self._send_json({"ok": False, "error": "world_id and player_name required"})
                return
            world_path = DATA_DIR / world_id
            if not world_path.is_dir() or not (
                (world_path / "config.toml").exists() or
                (world_path / "config.py").exists()
            ):
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

        elif path == "/command":
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

        elif path == "/quit":
            gs = _game_session
            if gs and gs.alive:
                gs.send("quit")
            self._send_json({"ok": True})

        elif path == "/shutdown":
            self._send_json({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        else:
            self._send_json({"error": f"Unknown endpoint: {path}"}, 404)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Delve game web frontend server")
    parser.add_argument("--port", type=int, default=7374)
    parser.add_argument("--browser", action="store_true",
                        help="Open the game in your browser automatically")
    args = parser.parse_args()

    server = ThreadingHTTPServer(("localhost", args.port), GameHandler)
    url    = f"http://localhost:{args.port}"
    print(f"Delve Web  →  {url}")
    print("Press Ctrl+C to stop.\n")
    if args.browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == "__main__":
    main()
