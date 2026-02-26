#!/usr/bin/env python3
"""
tools/wct_server.py — Delve World Creation Tool (WCT) server.

A lightweight local HTTP server that serves the WCT frontend and exposes a
JSON API for reading and writing TOML data files.

Usage:
    python tools/wct_server.py            # starts on http://localhost:7373
    python tools/wct_server.py --port 8080

The WCT opens automatically in your default browser.
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

ROOT     = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
TOOLS_DIR = Path(__file__).parent

sys.path.insert(0, str(ROOT))
from engine.toml_io import load as toml_load, dump as toml_dump


# ── TOML helpers ──────────────────────────────────────────────────────────────

def _load_world() -> dict:
    """Load the entire world into a JSON-serialisable structure."""
    world = {
        "zones":  {},   # zone_id → {rooms, items, npcs, quests, dialogues, companions, crafting}
    }
    skip = {"zone_state", "players"}
    for zone_dir in sorted(DATA_DIR.iterdir()):
        if not zone_dir.is_dir() or zone_dir.name in skip:
            continue
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
            "files":      [],   # all .toml files found
        }

        # Main TOML files (rooms, items, npcs, etc.)
        for path in sorted(zone_dir.glob("*.toml")):
            try:
                data = toml_load(path)
            except Exception as e:
                data = {"_error": str(e)}
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

        elif path == "/api/world":
            self._send_json(_load_world())

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

        elif path == "/api/create_room":
            zone_id  = body.get("zone_id", "")
            room_data = body.get("room", {})
            if not zone_id or not room_data.get("id"):
                self._send_json({"ok": False, "error": "zone_id and room.id required"})
                return
            # Append to zone's rooms.toml (or create it)
            file_path = DATA_DIR / zone_id / "rooms.toml"
            try:
                existing = toml_load(file_path) if file_path.exists() else {}
            except Exception:
                existing = {}
            existing.setdefault("room", []).append(room_data)
            ok, err = _write_file(str(file_path.relative_to(ROOT)), existing)
            self._send_json({"ok": ok, "error": err})

        elif path == "/api/create_npc":
            zone_id  = body.get("zone_id", "")
            npc_data = body.get("npc", {})
            if not zone_id or not npc_data.get("id"):
                self._send_json({"ok": False, "error": "zone_id and npc.id required"})
                return
            file_path = DATA_DIR / zone_id / "npcs.toml"
            try:
                existing = toml_load(file_path) if file_path.exists() else {}
            except Exception:
                existing = {}
            existing.setdefault("npc", []).append(npc_data)
            ok, err = _write_file(str(file_path.relative_to(ROOT)), existing)
            self._send_json({"ok": ok, "error": err})

        elif path == "/api/create_item":
            zone_id   = body.get("zone_id", "")
            item_data = body.get("item", {})
            if not zone_id or not item_data.get("id"):
                self._send_json({"ok": False, "error": "zone_id and item.id required"})
                return
            file_path = DATA_DIR / zone_id / "items.toml"
            try:
                existing = toml_load(file_path) if file_path.exists() else {}
            except Exception:
                existing = {}
            existing.setdefault("item", []).append(item_data)
            ok, err = _write_file(str(file_path.relative_to(ROOT)), existing)
            self._send_json({"ok": ok, "error": err})

        elif path == "/api/create_zone":
            zone_id = body.get("zone_id", "").strip().lower().replace(" ", "_")
            if not zone_id:
                self._send_json({"ok": False, "error": "zone_id required"})
                return
            zone_dir = DATA_DIR / zone_id
            if zone_dir.exists():
                self._send_json({"ok": False, "error": f"Zone '{zone_id}' already exists"})
                return
            zone_dir.mkdir(parents=True)
            # Create stub files
            (zone_dir / "rooms.toml").write_text(f"# {zone_id} rooms\n")
            (zone_dir / "items.toml").write_text(f"# {zone_id} items\n")
            (zone_dir / "npcs.toml").write_text(f"# {zone_id} NPCs\n")
            self._send_json({"ok": True, "zone_id": zone_id})

        else:
            self._send_json({"error": f"Unknown endpoint: {path}"}, 404)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Delve World Creation Tool server")
    parser.add_argument("--port", type=int, default=7373)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    server = HTTPServer(("localhost", args.port), WCTHandler)
    url    = f"http://localhost:{args.port}"
    print(f"Delve WCT  →  {url}")
    print("Press Ctrl+C to stop.\n")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == "__main__":
    main()




