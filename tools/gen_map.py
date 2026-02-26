#!/usr/bin/env python3
"""
gen_map.py — Generate a self-contained HTML admin map from live world data.

Usage:
    python tools/gen_map.py [output.html]

Default output: tools/admin_map.html
Open the resulting file in any browser. No server required.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from engine.world import World
from engine.toml_io import load as toml_load


def build_world_data() -> dict:
    w = World()

    all_rooms: dict[str, dict] = {}
    for meta in w._zone_index.values():
        for room_file in meta.room_files:
            for room in toml_load(room_file).get("room", []):
                rid = room.get("id", "")
                if not rid:
                    continue
                exits: dict[str, str] = {}
                exit_locks: dict[str, bool] = {}
                for direction, val in room.get("exits", {}).items():
                    if isinstance(val, dict):
                        exits[direction] = val.get("to", "")
                        exit_locks[direction] = val.get("locked", False)
                    else:
                        exits[direction] = val or ""
                all_rooms[rid] = {
                    "id":          rid,
                    "name":        room.get("name", "?"),
                    "description": room.get("description", ""),
                    "zone":        meta.zone_id,
                    "coord":       room.get("coord"),
                    "flags":       room.get("flags", []),
                    "exits":       exits,
                    "exit_locks":  exit_locks,
                    "items":       room.get("items", []),
                    "spawns":      room.get("spawns", []),
                    "start":       room.get("start", False),
                }

    all_npcs = {
        nid: {
            "name":    n["name"],
            "hostile": n.get("hostile", False),
            "tags":    n.get("tags", []),
        }
        for nid, n in w.npcs.items()
    }

    all_items = {
        iid: {
            "name":       i["name"],
            "slot":       i.get("slot", ""),
            "no_drop":    i.get("no_drop", False),
            "desc_short": i.get("desc_short", ""),
        }
        for iid, i in w.items.items()
    }

    return {"rooms": all_rooms, "npcs": all_npcs, "items": all_items}


def generate(output_path: Path) -> None:
    template_path = Path(__file__).parent / "map.html"
    if not template_path.exists():
        print(f"[error] Template not found: {template_path}", file=sys.stderr)
        sys.exit(1)

    print("Loading world data...", end=" ", flush=True)
    data = build_world_data()
    room_count = len(data["rooms"])
    npc_count  = len(data["npcs"])
    item_count = len(data["items"])
    print(f"{room_count} rooms, {npc_count} NPCs, {item_count} items")

    template = template_path.read_text(encoding="utf-8")
    json_blob = json.dumps(data, ensure_ascii=False)
    html = template.replace("WORLD_DATA_PLACEHOLDER", json_blob)

    output_path.write_text(html, encoding="utf-8")
    print(f"Written → {output_path}")
    print(f"Open in your browser: file:///{output_path.resolve()}")


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tools" / "admin_map.html"
    generate(out)


