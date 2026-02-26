#!/usr/bin/env python3
"""
tools/map.py — Admin map utility for Delve.

Renders the world as either an ASCII terminal map or an interactive HTML file.

Usage:
  python tools/map.py                        # ASCII map, all zones
  python tools/map.py --zone NAME            # ASCII map, one zone
  python tools/map.py --full                 # ASCII map with item/NPC counts
  python tools/map.py --html                 # Generate HTML map → tools/admin_map.html
  python tools/map.py --html --output my.html  # HTML map to custom path
  python tools/map.py --help
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from engine.toml_io import load as toml_load

DATA_DIR = ROOT / "data"

# ── Colours (ASCII output only) ───────────────────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"

def _fg(r, g, b): return f"\033[38;2;{r};{g};{b}m"

C_ROOM   = BOLD  + _fg(255, 220, 100)
C_CONN   = DIM   + _fg(100, 180, 100)
C_LOCKED = BOLD  + _fg(255, 100,  80)
C_ZONE   = DIM   + _fg(140, 140, 200)
C_DETAIL = DIM   + _fg(160, 160, 160)
C_HEADER =         _fg(180, 200, 255)

SKIP_DIRS = {"zone_state", "players"}

# ── Shared world loader (ASCII path — doesn't need the full World engine) ─────

def load_world() -> dict:
    """Load all rooms, items, and NPCs from zone folders into a flat catalogue."""
    world = {"rooms": {}, "items": {}, "npcs": {}, "zone_rooms": {}}

    for zone_folder in sorted(DATA_DIR.iterdir()):
        if not zone_folder.is_dir() or zone_folder.name in SKIP_DIRS:
            continue
        zone_id = zone_folder.name

        for path in sorted(zone_folder.glob("*.toml")):
            try:
                d = toml_load(path)
            except Exception:
                continue

            for item in d.get("item", []):
                iid = item.get("id", "")
                if iid and iid not in world["items"]:
                    world["items"][iid] = item

            for npc in d.get("npc", []):
                nid = npc.get("id", "")
                if nid and nid not in world["npcs"]:
                    world["npcs"][nid] = npc

            for room in d.get("room", []):
                rid = room.get("id", "")
                if not rid:
                    continue
                world["zone_rooms"].setdefault(zone_id, []).append(rid)
                world["rooms"][rid] = {**room, "_zone": zone_id}

    return world


def exit_dest(exit_val) -> str:
    if isinstance(exit_val, dict):
        return exit_val.get("to", "")
    return exit_val or ""


def exit_locked(exit_val) -> bool:
    return isinstance(exit_val, dict) and exit_val.get("locked", False)


# ── ASCII renderer ─────────────────────────────────────────────────────────────

def render_ascii(world: dict, zone_filter: str = "", full: bool = False) -> None:
    rooms = {
        rid: r for rid, r in world["rooms"].items()
        if not zone_filter or r.get("_zone") == zone_filter
    }

    if not rooms:
        print(f"No rooms found" + (f" for zone '{zone_filter}'" if zone_filter else "") + ".")
        return

    with_coords    = {rid: r for rid, r in rooms.items() if r.get("coord")}
    without_coords = {rid: r for rid, r in rooms.items() if not r.get("coord")}

    if with_coords:
        _render_grid(world, with_coords, full)

    if without_coords:
        print()
        print(C_ZONE + "── Rooms without coordinates ─────" + RESET)
        for rid, room in sorted(without_coords.items()):
            zone = room.get("_zone", "")
            locked_exits = [
                f"{d}→{exit_dest(v)} [LOCKED]"
                for d, v in room.get("exits", {}).items()
                if exit_locked(v)
            ]
            print(f"  {C_ROOM}{room.get('name','?')}{RESET} {C_DETAIL}[{rid}] zone={zone}{RESET}")
            if locked_exits:
                print(f"    {C_LOCKED}Locked: {', '.join(locked_exits)}{RESET}")

    total = len(rooms)
    zones = sorted({r["_zone"] for r in rooms.values()})
    print()
    print(C_HEADER + f"Total: {total} rooms across {len(zones)} zone(s): "
          + ", ".join(zones) + RESET)


def _render_grid(world: dict, rooms: dict, full: bool) -> None:
    coords: dict[tuple, dict] = {}
    for rid, room in rooms.items():
        c = room.get("coord", [])
        if len(c) >= 2:
            coords[(int(c[0]), int(c[1]))] = room

    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    zone_colors: dict[str, str] = {}
    zone_list = sorted({r.get("_zone", "") for r in coords.values()})
    palette = [
        _fg(255, 200, 100), _fg(100, 220, 255), _fg(200, 100, 255),
        _fg(100, 255, 180), _fg(255, 140, 100),
    ]
    for i, z in enumerate(zone_list):
        zone_colors[z] = palette[i % len(palette)]

    print()
    print(C_HEADER + "═" * 60 + RESET)
    print(C_HEADER + "  DELVE Admin Map" + RESET)
    print(C_HEADER + "═" * 60 + RESET)
    print()

    for y in range(max_y, min_y - 1, -1):
        row_cells  = []
        row_hconns = []

        for x in range(min_x, max_x + 1):
            room = coords.get((x, y))
            if room:
                rid   = room.get("id", "")
                zone  = room.get("_zone", "")
                zc    = zone_colors.get(zone, "")
                abbr  = rid[:4].upper()
                start = "★" if room.get("start") else " "

                if full:
                    ni   = len([i for i in room.get("items",  []) if isinstance(i, str)])
                    ns   = len(room.get("spawns", []))
                    cell = f"{zc}[{abbr}{start}]{RESET}{C_DETAIL}i{ni}n{ns}{RESET}"
                else:
                    cell = f"{zc}[{abbr}{start}]{RESET}"

                row_cells.append(cell)

                east      = room.get("exits", {}).get("east")
                east_dest = exit_dest(east)
                if east_dest and coords.get((x + 1, y)):
                    hconn = C_LOCKED + "─✗─" + RESET if exit_locked(east) else C_CONN + "───" + RESET
                else:
                    hconn = "   "
                row_hconns.append(hconn)
            else:
                row_cells.append("      ")
                row_hconns.append("   ")

        line = ""
        for cell, conn in zip(row_cells, row_hconns):
            line += cell + conn
        print("  " + line)

        if y > min_y:
            vline = ""
            for x in range(min_x, max_x + 1):
                room  = coords.get((x, y))
                below = coords.get((x, y - 1))
                if room and below:
                    south  = room.get("exits", {}).get("south")
                    s_dest = exit_dest(south)
                    if s_dest:
                        vline += C_LOCKED + "  ✗   " + RESET if exit_locked(south) else C_CONN + "  │   " + RESET
                    else:
                        vline += "      "
                else:
                    vline += "      "
            print("  " + vline)

    print()
    print(C_DETAIL + "  Legend:  [ABCD] room id   ★ = start room   "
          "─✗─ = locked door" + RESET)
    if full:
        print(C_DETAIL + "           i# = items   n# = NPCs" + RESET)
    print()

    if len(zone_list) > 1:
        print(C_HEADER + "  Zones:" + RESET)
        for z in zone_list:
            rids = [rid for rid, r in rooms.items() if r.get("_zone") == z]
            print(f"    {zone_colors[z]}■{RESET} {z}  ({len(rids)} rooms)")


# ── HTML generator ─────────────────────────────────────────────────────────────

def build_html_data() -> dict:
    """Load world data via the engine for the HTML map (richer than load_world())."""
    from engine.world import World

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
                        exits[direction]      = val.get("to", "")
                        exit_locks[direction] = val.get("locked", False)
                    else:
                        exits[direction]      = val or ""
                all_rooms[rid] = {
                    "id":           rid,
                    "name":         room.get("name", "?"),
                    "description":  room.get("description", ""),
                    "zone":         meta.zone_id,
                    "coord":        room.get("coord"),
                    "flags":        room.get("flags", []),
                    "exits":        exits,
                    "exit_locks":   exit_locks,
                    "items":        room.get("items", []),
                    "spawns":       room.get("spawns", []),
                    "start":        room.get("start", False),
                    "admin_comment": room.get("admin_comment", ""),
                }

    all_npcs = {
        nid: {
            "name":          n["name"],
            "hostile":       n.get("hostile", False),
            "tags":          n.get("tags", []),
            "admin_comment": n.get("admin_comment", ""),
        }
        for nid, n in w.npcs.items()
    }

    all_items = {
        iid: {
            "name":          i["name"],
            "slot":          i.get("slot", ""),
            "no_drop":       i.get("no_drop", False),
            "desc_short":    i.get("desc_short", ""),
            "admin_comment": i.get("admin_comment", ""),
        }
        for iid, i in w.items.items()
    }

    return {"rooms": all_rooms, "npcs": all_npcs, "items": all_items}


def generate_html(output_path: Path) -> None:
    template_path = Path(__file__).parent / "map.html"
    if not template_path.exists():
        print(f"[error] HTML template not found: {template_path}", file=sys.stderr)
        sys.exit(1)

    css_path = Path(__file__).parent / "map.css"
    if not css_path.exists():
        print(f"[error] CSS file not found: {css_path}", file=sys.stderr)
        sys.exit(1)

    print("Loading world data...", end=" ", flush=True)
    data = build_html_data()
    print(f"{len(data['rooms'])} rooms, {len(data['npcs'])} NPCs, {len(data['items'])} items")

    template = template_path.read_text(encoding="utf-8")
    json_blob = json.dumps(data, ensure_ascii=False)
    html = template.replace("WORLD_DATA_PLACEHOLDER", json_blob)

    # Inline CSS so the output is truly self-contained (no external file dependency)
    css = css_path.read_text(encoding="utf-8")
    html = html.replace(
        '<link rel="stylesheet" href="map.css">',
        f'<style>\n{css}\n</style>',
    )

    output_path.write_text(html, encoding="utf-8")
    print(f"Written → {output_path}")
    print(f"Open in your browser: file:///{output_path.resolve()}")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    html_mode = "--html" in args

    # --output / -o  (HTML only)
    output_path = ROOT / "tools" / "admin_map.html"
    if "--output" in args:
        idx = args.index("--output")
        if idx + 1 < len(args):
            output_path = Path(args[idx + 1])
    elif "-o" in args:
        idx = args.index("-o")
        if idx + 1 < len(args):
            output_path = Path(args[idx + 1])

    if html_mode:
        generate_html(output_path)
        return

    # ASCII mode
    zone_filter = ""
    if "--zone" in args:
        idx = args.index("--zone")
        if idx + 1 < len(args):
            zone_filter = args[idx + 1]

    full = "--full" in args or "-f" in args

    world = load_world()
    render_ascii(world, zone_filter=zone_filter, full=full)


if __name__ == "__main__":
    main()




