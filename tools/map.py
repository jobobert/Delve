#!/usr/bin/env python3
"""
tools/map.py — Admin map utility for Delve.

Renders the world as either an ASCII terminal map or a self-contained HTML file.

Rooms without an explicit coord = [x, y] field are placed automatically on the
map using exit topology (BFS from their neighbours). They appear with a dashed
border in HTML and a ~ marker in ASCII. Add coord = [x, y] to a room in TOML
to fix its position permanently.

Usage:
  python tools/map.py                          # ASCII map, all zones
  python tools/map.py --zone NAME              # ASCII map, one zone
  python tools/map.py --full                   # ASCII map with item/NPC counts
  python tools/map.py --html                   # HTML map -> tools/admin_map.html
  python tools/map.py --html --output my.html  # HTML map to custom path
  python tools/map.py --world NAME             # select world by folder name
  python tools/map.py --help
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from engine.toml_io    import load as toml_load
from engine.map_builder import apply_auto_layout, exit_dest

DATA_DIR  = ROOT / "data"
SKIP_DIRS = {"zone_state", "players"}

# ── Colours (ASCII output only) ───────────────────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"

def _fg(r, g, b): return f"\033[38;2;{r};{g};{b}m"

C_ROOM   = BOLD + _fg(255, 220, 100)
C_CONN   = DIM  + _fg(100, 180, 100)
C_LOCKED = BOLD + _fg(255, 100,  80)
C_ZONE   = DIM  + _fg(140, 140, 200)
C_DETAIL = DIM  + _fg(160, 160, 160)
C_AUTO   = DIM  + _fg(200, 140,  60)
C_HEADER =        _fg(180, 200, 255)

# ── World / zone discovery ────────────────────────────────────────────────────

def _discover_worlds() -> list[Path]:
    """Return sorted world folder Paths — subfolders of DATA_DIR with config.py."""
    if not DATA_DIR.is_dir():
        return []
    return sorted(
        p for p in DATA_DIR.iterdir()
        if p.is_dir() and (p / "config.py").exists()
    )


def _pick_world(world_arg: str | None) -> Path:
    worlds = _discover_worlds()
    if not worlds:
        print(
            "[error] No world folders found in data/ "
            "(subfolders must contain config.py).",
            file=sys.stderr,
        )
        sys.exit(1)
    if world_arg:
        for w in worlds:
            if w.name == world_arg:
                return w
        names = [w.name for w in worlds]
        print(f"[error] World '{world_arg}' not found. Available: {names}", file=sys.stderr)
        sys.exit(1)
    return worlds[0]


def _zone_dirs(world_path: Path) -> list[Path]:
    return sorted(
        p for p in world_path.iterdir()
        if p.is_dir() and p.name not in SKIP_DIRS
    )

# ── Exit helpers (rendering-only) ─────────────────────────────────────────────
# exit_dest is imported from engine.map_builder above.

def exit_locked(exit_val) -> bool:
    return isinstance(exit_val, dict) and exit_val.get("locked", False)


# ── World loader ──────────────────────────────────────────────────────────────

def load_world(world_path: Path) -> dict:
    """Load all rooms, items, and NPCs from a world's zone folders."""
    world = {
        "rooms":      {},
        "items":      {},
        "npcs":       {},
        "zone_rooms": {},
        "zone_list":  [],
    }
    for zone_folder in _zone_dirs(world_path):
        zone_id   = zone_folder.name
        has_rooms = False
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
                has_rooms = True
        if has_rooms and zone_id not in world["zone_list"]:
            world["zone_list"].append(zone_id)

    apply_auto_layout(world["rooms"])
    return world


# ── ASCII renderer ────────────────────────────────────────────────────────────

def render_ascii(world: dict, zone_filter: str = "", full: bool = False) -> None:
    rooms = {
        rid: r for rid, r in world["rooms"].items()
        if not zone_filter or r.get("_zone") == zone_filter
    }
    if not rooms:
        print(f"No rooms found" + (f" for zone '{zone_filter}'" if zone_filter else "") + ".")
        return

    _render_grid(world, rooms, full)

    auto_rooms = {rid: r for rid, r in rooms.items() if r.get("_auto_placed")}
    if auto_rooms:
        print()
        print(C_AUTO + f"  {len(auto_rooms)} room(s) auto-placed (add coord = [x, y] for a fixed position):" + RESET)
        for rid, room in sorted(auto_rooms.items()):
            dc = room.get("_display_coord", [])
            print(C_DETAIL + f"    [{rid}]  {room.get('name','?')}  auto-placed at {dc}" + RESET)

    total = len(rooms)
    zones = sorted({r["_zone"] for r in rooms.values()})
    print()
    print(C_HEADER + f"Total: {total} rooms across {len(zones)} zone(s): "
          + ", ".join(zones) + RESET)


def _render_grid(world: dict, rooms: dict, full: bool) -> None:
    # Build coord map using _display_coord (set by auto-layout for all rooms)
    coords: dict[tuple, dict] = {}
    for rid, room in rooms.items():
        dc = room.get("_display_coord")
        if dc and len(dc) >= 2:
            coords[(int(dc[0]), int(dc[1]))] = room

    if not coords:
        print("No rooms to display.")
        return

    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    zone_list   = sorted({r.get("_zone", "") for r in coords.values()})
    palette     = [
        _fg(255, 200, 100), _fg(100, 220, 255), _fg(200, 100, 255),
        _fg(100, 255, 180), _fg(255, 140, 100), _fg(255, 100, 200),
    ]
    zone_colors = {z: palette[i % len(palette)] for i, z in enumerate(zone_list)}

    print()
    print(C_HEADER + "=" * 60 + RESET)
    print(C_HEADER + "  DELVE Admin Map" + RESET)
    print(C_HEADER + "=" * 60 + RESET)
    print()

    for y in range(max_y, min_y - 1, -1):
        row_cells  = []
        row_hconns = []

        for x in range(min_x, max_x + 1):
            room = coords.get((x, y))
            if room:
                rid  = room.get("id", "")
                zone = room.get("_zone", "")
                zc   = zone_colors.get(zone, "")
                abbr = rid[:4].upper()
                start = "*" if room.get("start") else " "
                auto  = "~" if room.get("_auto_placed") else " "

                if full:
                    ni   = len([i for i in room.get("items",  []) if isinstance(i, str)])
                    ns   = len(room.get("spawns", []))
                    cell = f"{zc}[{abbr}{start}]{RESET}{C_DETAIL}i{ni}n{ns}{RESET}"
                else:
                    cell = f"{zc}[{abbr}{start}]{auto}{RESET}"

                row_cells.append(cell)

                east      = room.get("exits", {}).get("east")
                east_dest = exit_dest(east)
                has_east  = coords.get((x + 1, y)) is not None
                if east_dest and has_east:
                    hconn = C_LOCKED + "-x-" + RESET if exit_locked(east) else C_CONN + "---" + RESET
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
                        vline += C_LOCKED + "  x   " + RESET if exit_locked(south) else C_CONN + "  |   " + RESET
                    else:
                        vline += "      "
                else:
                    vline += "      "
            print("  " + vline)

    print()
    print(C_DETAIL + "  Legend:  [ABCD] room-id   * = start room   ~ = auto-placed   -x- = locked" + RESET)
    if full:
        print(C_DETAIL + "           i# = items   n# = NPCs" + RESET)
    print()

    if len(zone_list) > 1:
        print(C_HEADER + "  Zones:" + RESET)
        for z in zone_list:
            rids = [rid for rid, r in rooms.items() if r.get("_zone") == z]
            print(f"    {zone_colors[z]}#{RESET} {z}  ({len(rids)} rooms)")


# ── HTML generator ────────────────────────────────────────────────────────────

_ZONE_COLORS = [
    "#4a7fd4", "#7db87d", "#c27a3a", "#a05090",
    "#5bb8c4", "#c44f4f", "#c4a83a", "#7a7aaa",
]


def generate_html(world: dict, world_name: str, output_path: Path) -> None:
    rooms      = world["rooms"]
    zone_list  = world["zone_list"]
    zone_color = {zid: _ZONE_COLORS[i % len(_ZONE_COLORS)]
                  for i, zid in enumerate(zone_list)}

    placed      = {rid: r for rid, r in rooms.items() if r.get("_display_coord")}
    auto_placed = {rid: r for rid, r in placed.items() if r.get("_auto_placed")}

    # Grid bounds (using _display_coord)
    if placed:
        xs = [r["_display_coord"][0] for r in placed.values()]
        ys = [r["_display_coord"][1] for r in placed.values()]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
    else:
        min_x = max_x = min_y = max_y = 0

    CELL_W, CELL_H = 140, 80
    PAD_X,  PAD_Y  = 60,  60
    grid_w = (max_x - min_x + 1) * CELL_W + 2 * PAD_X
    grid_h = (max_y - min_y + 1) * CELL_H + 2 * PAD_Y

    def cell_center(x, y):
        cx = PAD_X + (x - min_x) * CELL_W + CELL_W // 2
        cy = PAD_Y + (max_y - y) * CELL_H + CELL_H // 2   # SVG Y inverted
        return cx, cy

    # Build room detail data passed to JS
    room_data: dict[str, dict] = {}
    for rid, room in rooms.items():
        items_list = [
            world["items"].get(i, {}).get("name", i) if isinstance(i, str) else "?"
            for i in room.get("items", [])
        ]
        npcs_list = [
            world["npcs"].get(n, {}).get("name", n) if isinstance(n, str) else "?"
            for n in room.get("spawns", [])
        ]
        exits_info: dict[str, dict] = {}
        for direction, ev in room.get("exits", {}).items():
            exits_info[direction] = {
                "dest":   exit_dest(ev),
                "locked": exit_locked(ev),
            }
        room_data[rid] = {
            "id":            rid,
            "name":          room.get("name", rid),
            "zone":          room.get("_zone", ""),
            "description":   room.get("description", ""),
            "flags":         room.get("flags", []),
            "items":         items_list,
            "npcs":          npcs_list,
            "exits":         exits_info,
            "start":         bool(room.get("start")),
            "coord":         room.get("coord"),
            "auto_placed":   bool(room.get("_auto_placed")),
            "admin_comment": room.get("admin_comment", ""),
        }

    # SVG: connection lines between placed rooms
    drawn_pairs: set[tuple] = set()
    svg_lines:   list[str]  = []
    for rid, room in placed.items():
        dc1 = room["_display_coord"]
        cx1, cy1 = cell_center(dc1[0], dc1[1])
        for direction, ev in room.get("exits", {}).items():
            dest = exit_dest(ev)
            if not dest or dest not in placed:
                continue
            pair = tuple(sorted([rid, dest]))
            if pair in drawn_pairs:
                continue
            drawn_pairs.add(pair)
            dc2 = placed[dest]["_display_coord"]
            cx2, cy2 = cell_center(dc2[0], dc2[1])
            locked = exit_locked(ev)
            color  = "#e05050" if locked else "#444"
            dash   = 'stroke-dasharray="6,4"' if locked else ""
            svg_lines.append(
                f'<line x1="{cx1}" y1="{cy1}" x2="{cx2}" y2="{cy2}" '
                f'stroke="{color}" stroke-width="2" {dash}/>'
            )

    # SVG: room nodes
    svg_nodes: list[str] = []
    for rid, room in placed.items():
        dc     = room["_display_coord"]
        cx, cy = cell_center(dc[0], dc[1])
        zone   = room.get("_zone", "")
        color  = zone_color.get(zone, "#888")
        flags  = room.get("flags", [])
        label  = room.get("name", rid)[:16]
        is_start = room.get("start", False)
        is_auto  = room.get("_auto_placed", False)
        border   = "#fff" if is_start else color
        bw       = 3 if is_start else 1.5
        sdash    = 'stroke-dasharray="5,3"' if is_auto else ""

        badges = ""
        if "no_combat" in flags:
            badges += '<text x="4" y="12" font-size="9" fill="#999">nc</text>'
        if "town" in flags:
            badges += '<text x="4" y="22" font-size="9" fill="#88f">T</text>'
        if "safe_combat" in flags:
            badges += '<text x="4" y="22" font-size="9" fill="#4f4">SC</text>'

        ni = len(room.get("items", []))
        ns = len(room.get("spawns", []))
        counts = ""
        if ni:
            counts += f'<text x="{CELL_W-6}" y="12" font-size="9" fill="#fa0" text-anchor="end">i:{ni}</text>'
        if ns:
            counts += f'<text x="{CELL_W-6}" y="24" font-size="9" fill="#f88" text-anchor="end">n:{ns}</text>'

        svg_nodes.append(f'''
  <g class="room-node" data-id="{rid}"
     transform="translate({cx - CELL_W//2},{cy - CELL_H//2})"
     onclick="selectRoom('{rid}')" style="cursor:pointer">
    <rect width="{CELL_W-6}" height="{CELL_H-6}" rx="6" x="3" y="3"
          fill="{color}22" stroke="{border}" stroke-width="{bw}" {sdash}/>
    {badges}{counts}
    <text x="{CELL_W//2}" y="{CELL_H//2 - 4}" text-anchor="middle"
          font-size="10" font-weight="bold" fill="#eee">{label}</text>
    <text x="{CELL_W//2}" y="{CELL_H//2 + 10}" text-anchor="middle"
          font-size="9" fill="#aaa">{rid[:20]}</text>
  </g>''')

    # Zone legend
    legend_html = ""
    for zid in zone_list:
        c = zone_color.get(zid, "#888")
        n = len(world["zone_rooms"].get(zid, []))
        legend_html += (
            f'<span style="display:inline-flex;align-items:center;margin:0 10px 4px 0;'
            f'cursor:pointer" onclick="filterTo(\'{zid}\')">'
            f'<span style="background:{c};width:12px;height:12px;border-radius:2px;'
            f'display:inline-block;margin-right:5px"></span>'
            f'<span style="color:#ccc;font-size:11px">{zid} ({n})</span></span>'
        )

    # Auto-placed rooms sidebar section
    auto_sidebar = ""
    if auto_placed:
        rows = "".join(
            f"<li onclick=\"selectRoom('{rid}')\" "
            f"style='cursor:pointer;padding:2px 4px;border-radius:3px'"
            f" onmouseover=\"this.style.background='#222'\""
            f" onmouseout=\"this.style.background=''\">"
            f"{room.get('name', rid)} "
            f"<span style='color:#555'>({rid})</span></li>"
            for rid, room in sorted(auto_placed.items())
        )
        auto_sidebar = (
            f"<h3 style='color:#f80;font-size:11px;margin:16px 0 5px'>"
            f"Auto-placed ({len(auto_placed)}) — no coord field</h3>"
            f"<ul style='list-style:none;color:#888;font-size:11px'>{rows}</ul>"
        )

    room_data_json = json.dumps(room_data, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{world_name} — Admin Map</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #1a1a2e; color: #eee; font-family: monospace;
          display: flex; height: 100vh; overflow: hidden; }}

  #sidebar {{ width: 300px; min-width: 220px; background: #16213e;
              border-right: 1px solid #2a2a4a;
              display: flex; flex-direction: column; overflow: hidden; }}
  #sidebar-header {{ padding: 12px 14px; background: #0f3460;
                     border-bottom: 1px solid #2a2a4a; flex-shrink: 0; }}
  #sidebar-header h2 {{ font-size: 13px; color: #88aaff; }}
  #sidebar-header p  {{ font-size: 10px; color: #555; margin-top: 3px; }}
  #sidebar-body {{ flex: 1; overflow-y: auto; padding: 12px; }}

  #room-detail h3 {{ color: #88aaff; font-size: 13px; margin-bottom: 3px; }}
  .room-id  {{ color: #555; font-size: 10px; margin-bottom: 10px; }}
  .section  {{ margin-top: 10px; }}
  .section h4 {{ color: #888; font-size: 10px; text-transform: uppercase;
                letter-spacing: 1px; margin-bottom: 3px; }}
  .desc   {{ color: #bbb; font-size: 11px; line-height: 1.5; white-space: pre-wrap; }}
  .tag    {{ display: inline-block; background: #2a2a4a; color: #aaa;
             font-size: 10px; padding: 1px 5px; border-radius: 3px; margin: 1px; }}
  .tag.town       {{ color: #88f; }}
  .tag.no_combat  {{ color: #f88; }}
  .tag.safe_combat {{ color: #8f8; }}
  .exit   {{ font-size: 11px; color: #ccc; margin: 1px 0; }}
  .exit .locked {{ color: #e05050; font-size: 10px; }}
  .item   {{ font-size: 11px; color: #fa0; margin: 1px 0; }}
  .npc    {{ font-size: 11px; color: #f88; margin: 1px 0; }}
  .auto-badge {{ font-size: 10px; color: #f80; background: #332;
                 padding: 1px 5px; border-radius: 3px; margin-left: 6px; }}
  .comment    {{ color: #f80; font-size: 10px; font-style: italic;
                 margin-top: 6px; }}
  .placeholder {{ color: #444; font-size: 12px; margin-top: 40px;
                  text-align: center; }}

  #main {{ flex: 1; display: flex; flex-direction: column; overflow: hidden; }}
  #toolbar {{ padding: 7px 12px; background: #0f3460;
              border-bottom: 1px solid #2a2a4a;
              display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
  #toolbar input {{ background: #1a1a2e; border: 1px solid #444;
                    color: #eee; padding: 3px 7px; border-radius: 4px;
                    font-size: 11px; width: 130px; }}
  #toolbar label {{ color: #888; font-size: 11px; }}
  #map-container {{ flex: 1; overflow: auto; }}

  .room-node rect {{ transition: fill 0.1s, stroke 0.1s; }}
  .room-node:hover rect  {{ fill: rgba(255,255,255,0.07) !important; }}
  .room-node.selected rect {{
    stroke: #fff !important; stroke-width: 2.5 !important;
    fill: rgba(255,255,255,0.12) !important;
  }}
</style>
</head>
<body>

<div id="sidebar">
  <div id="sidebar-header">
    <h2>{world_name} — Admin Map</h2>
    <p>{len(rooms)} rooms &middot; {len(world['npcs'])} NPCs &middot; {len(world['items'])} items</p>
  </div>
  <div id="sidebar-body">
    <div id="room-detail"><div class="placeholder">Click a room to inspect it</div></div>
    {auto_sidebar}
  </div>
</div>

<div id="main">
  <div id="toolbar">
    <label>Filter: <input type="text" id="search"
      placeholder="id, name or zone..."
      oninput="filterRooms(this.value)"/></label>
    <div>{legend_html}</div>
    <span style="color:#444;font-size:10px;margin-left:auto">
      dashed border = no coord &nbsp; dashed line = locked exit
    </span>
  </div>
  <div id="map-container">
    <svg id="map-svg" width="{grid_w}" height="{grid_h}">
      {''.join(svg_lines)}
      {''.join(svg_nodes)}
    </svg>
  </div>
</div>

<script>
const ROOMS = {room_data_json};

function selectRoom(rid) {{
  const r = ROOMS[rid];
  if (!r) return;

  document.querySelectorAll('.room-node').forEach(n => n.classList.remove('selected'));
  const node = document.querySelector('.room-node[data-id="' + rid + '"]');
  if (node) {{
    node.classList.add('selected');
    node.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
  }}

  const flags = (r.flags || []).map(f =>
    '<span class="tag ' + f + '">' + f + '</span>'
  ).join('') || '<span style="color:#444">none</span>';

  const exits = Object.entries(r.exits || {{}}).map(([dir, info]) => {{
    const dest  = ROOMS[info.dest];
    const dname = dest ? dest.name : (info.dest || '?');
    const lock  = info.locked ? ' <span class="locked">[locked]</span>' : '';
    return '<div class="exit">&#8594; <b>' + dir + '</b>: ' + dname +
           ' <span style="color:#444">(' + info.dest + ')</span>' + lock + '</div>';
  }}).join('') || '<span style="color:#444">none</span>';

  const items = (r.items || []).length
    ? r.items.map(i => '<div class="item">&#9632; ' + i + '</div>').join('')
    : '<span style="color:#444">none</span>';

  const npcs = (r.npcs || []).length
    ? r.npcs.map(n => '<div class="npc">&#9632; ' + n + '</div>').join('')
    : '<span style="color:#444">none</span>';

  const startBadge = r.start
    ? ' <span style="color:#fff;background:#333;padding:1px 5px;border-radius:3px;font-size:10px">START</span>'
    : '';
  const autoBadge = r.auto_placed
    ? '<span class="auto-badge">auto-placed</span>'
    : '';
  const coord   = r.coord ? '[' + r.coord[0] + ', ' + r.coord[1] + ']' : 'no coord';
  const comment = r.admin_comment
    ? '<div class="comment">&#9650; ' + r.admin_comment + '</div>'
    : '';

  document.getElementById('room-detail').innerHTML =
    '<h3>' + r.name + startBadge + autoBadge + '</h3>' +
    '<div class="room-id">' + r.id + ' &middot; ' + r.zone + ' &middot; ' + coord + '</div>' +
    comment +
    '<div class="section"><h4>Description</h4><div class="desc">' + (r.description || '<i>none</i>') + '</div></div>' +
    '<div class="section"><h4>Flags</h4>' + flags + '</div>' +
    '<div class="section"><h4>Exits (' + Object.keys(r.exits || {{}}).length + ')</h4>' + exits + '</div>' +
    '<div class="section"><h4>Items (' + (r.items || []).length + ')</h4>' + items + '</div>' +
    '<div class="section"><h4>NPC Spawns (' + (r.npcs || []).length + ')</h4>' + npcs + '</div>';
}}

function filterRooms(q) {{
  q = (q || '').toLowerCase().trim();
  document.querySelectorAll('.room-node').forEach(node => {{
    const rid = node.getAttribute('data-id');
    const r   = ROOMS[rid];
    if (!r) return;
    const match = !q || rid.includes(q) || r.name.toLowerCase().includes(q) || r.zone.includes(q);
    node.style.opacity       = match ? '1' : '0.1';
    node.style.pointerEvents = match ? '' : 'none';
  }});
}}

function filterTo(zone) {{
  const inp = document.getElementById('search');
  inp.value = zone;
  filterRooms(zone);
}}
</script>
</body>
</html>"""

    output_path.write_text(html, encoding="utf-8")
    n_auto = len(auto_placed)
    auto_note = f", {n_auto} auto-placed" if n_auto else ""
    print(f"Written -> {output_path}  [{len(rooms)} rooms{auto_note}, {len(world['zone_list'])} zones]")
    print(f"Open in browser: file:///{output_path.resolve()}")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    # --world / -w
    world_arg = None
    for flag in ("--world", "-w"):
        if flag in args:
            idx = args.index(flag)
            if idx + 1 < len(args):
                world_arg = args[idx + 1]

    world_path = _pick_world(world_arg)
    html_mode  = "--html" in args

    print(f"Loading '{world_path.name}'...", end=" ", flush=True)
    world = load_world(world_path)
    n_auto = sum(1 for r in world["rooms"].values() if r.get("_auto_placed"))
    auto_note = f", {n_auto} auto-placed" if n_auto else ""
    print(f"{len(world['rooms'])} rooms{auto_note}, {len(world['npcs'])} NPCs, {len(world['items'])} items")

    if html_mode:
        output_path = ROOT / "tools" / "admin_map.html"
        for flag in ("--output", "-o"):
            if flag in args:
                idx = args.index(flag)
                if idx + 1 < len(args):
                    output_path = Path(args[idx + 1])

        # Read world name from config.py
        world_name = world_path.name
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("_wc", world_path / "config.py")
            mod  = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            world_name = getattr(mod, "WORLD_NAME", world_path.name)
        except Exception:
            pass

        generate_html(world, world_name, output_path)
        return

    # ASCII mode
    zone_filter = ""
    if "--zone" in args:
        idx = args.index("--zone")
        if idx + 1 < len(args):
            zone_filter = args[idx + 1]

    full = "--full" in args or "-f" in args
    render_ascii(world, zone_filter=zone_filter, full=full)


if __name__ == "__main__":
    main()
