#!/usr/bin/env python3
"""
tools/map_html.py — HTML admin map for Delve.

Generates a single self-contained HTML file with:
  - Grid-based room layout using coord = [x, y] from TOML
  - SVG connection lines between rooms (solid = open, dashed = locked)
  - Zone color coding
  - Click any room to see full details: description, items, NPCs, exits
  - Rooms without coords listed in a sidebar

Usage:
    python tools/map_html.py                        # outputs map.html
    python tools/map_html.py --out path/to/out.html
    python tools/map_html.py --zone ashwood         # single zone
"""

import sys
import json
import argparse
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from engine.toml_io import load as toml_load

DATA_DIR  = ROOT / "data"
SKIP_DIRS = {"zone_state", "players"}

# ── Zone colour palette ────────────────────────────────────────────────────────

ZONE_COLORS = [
    "#4a7fd4",  # blue       — millhaven
    "#7db87d",  # green      — training
    "#c27a3a",  # amber      — ashwood
    "#a05090",  # purple     — future zone 4
    "#5bb8c4",  # teal       — future zone 5
    "#c44f4f",  # red        — future zone 6
    "#888888",  # grey       — overflow
]


def load_world() -> dict:
    """Load all rooms, items, NPCs from zone folders."""
    world = {
        "rooms":      {},   # room_id → room dict with _zone injected
        "items":      {},   # item_id → item template
        "npcs":       {},   # npc_id  → npc template
        "zone_rooms": {},   # zone_id → [room_id, ...]
        "zone_list":  [],   # ordered zone ids
    }
    for zone_folder in sorted(DATA_DIR.iterdir()):
        if not zone_folder.is_dir() or zone_folder.name in SKIP_DIRS:
            continue
        zone_id = zone_folder.name
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
                room = dict(room)
                room["_zone"] = zone_id
                world["rooms"][rid] = room
                world["zone_rooms"].setdefault(zone_id, []).append(rid)
                has_rooms = True

        if has_rooms and zone_id not in world["zone_list"]:
            world["zone_list"].append(zone_id)
    return world


def exit_dest(exit_val) -> str:
    if isinstance(exit_val, dict):
        return exit_val.get("to", "")
    return exit_val or ""


def exit_locked(exit_val) -> bool:
    return isinstance(exit_val, dict) and exit_val.get("locked", False)


def build_html(world: dict, zone_filter: str | None) -> str:
    rooms      = world["rooms"]
    zone_list  = world["zone_list"]
    zone_color = {zid: ZONE_COLORS[i % len(ZONE_COLORS)]
                  for i, zid in enumerate(zone_list)}

    # Filter to zone if requested
    if zone_filter:
        rooms = {rid: r for rid, r in rooms.items()
                 if r.get("_zone") == zone_filter}

    # Partition by coord availability
    with_coords    = {rid: r for rid, r in rooms.items() if r.get("coord")}
    without_coords = {rid: r for rid, r in rooms.items() if not r.get("coord")}

    # Grid bounds
    if with_coords:
        xs = [r["coord"][0] for r in with_coords.values()]
        ys = [r["coord"][1] for r in with_coords.values()]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
    else:
        min_x = max_x = min_y = max_y = 0

    CELL_W, CELL_H = 140, 80
    PAD_X,  PAD_Y  = 60, 60
    grid_w = (max_x - min_x + 1) * CELL_W + 2 * PAD_X
    grid_h = (max_y - min_y + 1) * CELL_H + 2 * PAD_Y

    def cell_center(x, y):
        cx = PAD_X + (x - min_x) * CELL_W + CELL_W // 2
        # SVG Y axis is inverted vs game Y axis
        cy = PAD_Y + (max_y - y) * CELL_H + CELL_H // 2
        return cx, cy

    # Build room detail data for JS
    room_data = {}
    for rid, room in rooms.items():
        items_list = [world["items"].get(i, {}).get("name", i)
                      if isinstance(i, str) else i.get("name", "?")
                      for i in room.get("items", [])]
        npcs_list  = [world["npcs"].get(n, {}).get("name", n)
                      if isinstance(n, str) else n.get("name", "?")
                      for n in room.get("spawns", [])]
        exits_info = {}
        for direction, ev in room.get("exits", {}).items():
            dest   = exit_dest(ev)
            locked = exit_locked(ev)
            exits_info[direction] = {"dest": dest, "locked": locked}

        room_data[rid] = {
            "id":          rid,
            "name":        room.get("name", rid),
            "zone":        room.get("_zone", ""),
            "description": room.get("description", ""),
            "flags":       room.get("flags", []),
            "items":       items_list,
            "npcs":        npcs_list,
            "exits":       exits_info,
            "start":       bool(room.get("start")),
            "coord":       room.get("coord"),
        }

    # SVG: connection lines
    drawn_pairs = set()
    svg_lines   = []
    for rid, room in with_coords.items():
        x1, y1 = room["coord"][0], room["coord"][1]
        cx1, cy1 = cell_center(x1, y1)
        for direction, ev in room.get("exits", {}).items():
            dest = exit_dest(ev)
            if not dest or dest not in with_coords:
                continue
            pair = tuple(sorted([rid, dest]))
            if pair in drawn_pairs:
                continue
            drawn_pairs.add(pair)
            x2, y2 = with_coords[dest]["coord"][0], with_coords[dest]["coord"][1]
            cx2, cy2 = cell_center(x2, y2)
            locked = exit_locked(ev)
            color  = "#e05050" if locked else "#555"
            dash   = 'stroke-dasharray="6,4"' if locked else ""
            svg_lines.append(
                f'<line x1="{cx1}" y1="{cy1}" x2="{cx2}" y2="{cy2}" '
                f'stroke="{color}" stroke-width="2" {dash}/>'
            )

    # SVG: room nodes
    svg_nodes = []
    for rid, room in with_coords.items():
        x, y = room["coord"][0], room["coord"][1]
        cx, cy = cell_center(x, y)
        zone   = room.get("_zone", "")
        color  = zone_color.get(zone, "#888")
        flags  = room.get("flags", [])
        label  = room.get("name", rid)[:14]
        items  = room.get("items", [])
        npcs   = room.get("spawns", [])
        is_start = room.get("start", False)
        border = "#fff" if is_start else color
        bw     = 3 if is_start else 1.5
        # Flag badges
        badges = ""
        if "no_combat" in flags:
            badges += '<text x="4" y="12" font-size="9" fill="#aaa">⚔✗</text>'
        if "town" in flags:
            badges += '<text x="4" y="22" font-size="9" fill="#88f">⌂</text>'
        if "safe_combat" in flags:
            badges += '<text x="4" y="22" font-size="9" fill="#4f4">~</text>'
        counts = ""
        if items:
            counts += f'<text x="{CELL_W-6}" y="12" font-size="9" fill="#fa0" text-anchor="end">📦{len(items)}</text>'
        if npcs:
            counts += f'<text x="{CELL_W-6}" y="24" font-size="9" fill="#f88" text-anchor="end">👤{len(npcs)}</text>'

        svg_nodes.append(f'''
  <g class="room-node" data-id="{rid}"
     transform="translate({cx - CELL_W//2},{cy - CELL_H//2})"
     onclick="showRoom('{rid}')" style="cursor:pointer">
    <rect width="{CELL_W-6}" height="{CELL_H-6}" rx="6" x="3" y="3"
          fill="{color}22" stroke="{border}" stroke-width="{bw}"/>
    {badges}{counts}
    <text x="{CELL_W//2}" y="{CELL_H//2 - 4}" text-anchor="middle"
          font-size="10" font-weight="bold" fill="#eee">{label}</text>
    <text x="{CELL_W//2}" y="{CELL_H//2 + 10}" text-anchor="middle"
          font-size="9" fill="#aaa">{rid[:18]}</text>
  </g>''')

    # Zone legend
    legend_items = ""
    for zid in zone_list:
        c = zone_color.get(zid, "#888")
        n = len(world["zone_rooms"].get(zid, []))
        legend_items += (
            f'<span style="display:inline-flex;align-items:center;margin:0 10px 4px 0">'
            f'<span style="background:{c};width:14px;height:14px;border-radius:3px;'
            f'display:inline-block;margin-right:5px"></span>'
            f'<span style="color:#ccc">{zid} ({n})</span></span>'
        )

    # Sidebar: rooms without coords
    nocoord_html = ""
    if without_coords:
        nocoord_html = "<h3 style='color:#aaa;margin-top:20px'>Rooms without coords</h3><ul style='color:#888;font-size:12px'>"
        for rid, room in without_coords.items():
            nocoord_html += f"<li onclick=\"showRoom('{rid}')\" style='cursor:pointer;padding:2px 0'>{room.get('name',rid)} <span style='color:#555'>({rid})</span></li>"
        nocoord_html += "</ul>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Delve — Admin Map</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #1a1a2e; color: #eee; font-family: monospace; display: flex; height: 100vh; overflow: hidden; }}
  #sidebar {{ width: 320px; min-width: 260px; background: #16213e; border-right: 1px solid #333; display: flex; flex-direction: column; overflow: hidden; }}
  #sidebar-header {{ padding: 14px 16px; background: #0f3460; border-bottom: 1px solid #333; }}
  #sidebar-header h2 {{ font-size: 15px; color: #88aaff; }}
  #sidebar-header p {{ font-size: 11px; color: #666; margin-top: 4px; }}
  #room-detail {{ flex: 1; overflow-y: auto; padding: 16px; }}
  #room-detail h3 {{ color: #88aaff; font-size: 14px; margin-bottom: 4px; }}
  #room-detail .room-id {{ color: #555; font-size: 11px; margin-bottom: 12px; }}
  #room-detail .section {{ margin-top: 12px; }}
  #room-detail .section h4 {{ color: #aaa; font-size: 11px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; }}
  #room-detail .desc {{ color: #bbb; font-size: 12px; line-height: 1.5; }}
  #room-detail .tag {{ display: inline-block; background: #333; color: #aaa; font-size: 10px; padding: 1px 6px; border-radius: 3px; margin: 2px 2px 2px 0; }}
  #room-detail .tag.town {{ background: #223; color: #88f; }}
  #room-detail .tag.no_combat {{ background: #322; color: #f88; }}
  #room-detail .tag.safe_combat {{ background: #232; color: #8f8; }}
  #room-detail .exit {{ font-size: 12px; color: #ccc; margin: 2px 0; }}
  #room-detail .exit .locked {{ color: #e05050; font-size: 10px; }}
  #room-detail .item {{ font-size: 12px; color: #fa0; margin: 1px 0; }}
  #room-detail .npc {{ font-size: 12px; color: #f88; margin: 1px 0; }}
  #room-detail .placeholder {{ color: #444; font-size: 13px; margin-top: 40px; text-align: center; }}
  #main {{ flex: 1; display: flex; flex-direction: column; overflow: hidden; }}
  #toolbar {{ padding: 8px 14px; background: #0f3460; border-bottom: 1px solid #333; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
  #toolbar .legend {{ display: flex; flex-wrap: wrap; align-items: center; gap: 0; }}
  #toolbar label {{ color: #888; font-size: 11px; }}
  #toolbar input {{ background: #1a1a2e; border: 1px solid #444; color: #eee; padding: 3px 8px; border-radius: 4px; font-size: 12px; width: 120px; }}
  #map-container {{ flex: 1; overflow: auto; position: relative; }}
  #map-svg {{ display: block; }}
  .room-node rect {{ transition: stroke 0.15s, fill 0.15s; }}
  .room-node:hover rect {{ fill: rgba(255,255,255,0.08) !important; stroke-width: 2.5 !important; }}
  .room-node.selected rect {{ stroke: #fff !important; stroke-width: 3 !important; }}
</style>
</head>
<body>
<div id="sidebar">
  <div id="sidebar-header">
    <h2>Delve Admin Map</h2>
    <p>{len(rooms)} rooms · {len(world["items"])} items · {len(world["npcs"])} NPCs</p>
  </div>
  <div id="room-detail">
    <div class="placeholder">Click a room to inspect it</div>
  </div>
  {nocoord_html}
</div>
<div id="main">
  <div id="toolbar">
    <label>Filter: <input type="text" id="search" placeholder="room id or name…" oninput="filterRooms(this.value)"/></label>
    <div class="legend">{legend_items}</div>
    <span style="color:#555;font-size:11px;margin-left:auto">⚔✗ no_combat · ⌂ town · ~ safe · 📦 items · 👤 NPCs · <span style="color:#e05050">dashed</span>=locked</span>
  </div>
  <div id="map-container">
    <svg id="map-svg" width="{grid_w}" height="{grid_h}">
      <!-- Connection lines -->
      {''.join(svg_lines)}
      <!-- Room nodes -->
      {''.join(svg_nodes)}
    </svg>
  </div>
</div>

<script>
const ROOMS = {json.dumps(room_data, indent=2)};

function showRoom(rid) {{
  const r = ROOMS[rid];
  if (!r) return;
  // Highlight
  document.querySelectorAll('.room-node').forEach(n => n.classList.remove('selected'));
  const node = document.querySelector(`.room-node[data-id="${{rid}}"]`);
  if (node) node.classList.add('selected');

  const flagHtml = (r.flags || []).map(f =>
    `<span class="tag ${{f}}">${{f}}</span>`
  ).join('') || '<span style="color:#555">none</span>';

  const exitsHtml = Object.entries(r.exits || {{}}).map(([dir, info]) => {{
    const dest  = ROOMS[info.dest];
    const dname = dest ? dest.name : (info.dest || '?');
    const lock  = info.locked ? ' <span class="locked">[locked]</span>' : '';
    return `<div class="exit">→ <b>${{dir}}</b>: ${{dname}} <span style="color:#555">(${{info.dest}})</span>${{lock}}</div>`;
  }}).join('') || '<span style="color:#555">none</span>';

  const itemsHtml = (r.items || []).length
    ? r.items.map(i => `<div class="item">📦 ${{i}}</div>`).join('')
    : '<span style="color:#555">none</span>';

  const npcsHtml = (r.npcs || []).length
    ? r.npcs.map(n => `<div class="npc">👤 ${{n}}</div>`).join('')
    : '<span style="color:#555">none</span>';

  const start = r.start ? ' <span style="color:#fff;background:#333;padding:1px 6px;border-radius:3px;font-size:10px">START</span>' : '';
  const coord = r.coord ? `[${{r.coord[0]}}, ${{r.coord[1]}}]` : 'no coord';

  document.getElementById('room-detail').innerHTML = `
    <h3>${{r.name}}${{start}}</h3>
    <div class="room-id">${{r.id}} · ${{r.zone}} · ${{coord}}</div>
    <div class="section">
      <h4>Description</h4>
      <div class="desc">${{r.description || '<i>none</i>'}}</div>
    </div>
    <div class="section">
      <h4>Flags</h4>
      <div>${{flagHtml}}</div>
    </div>
    <div class="section">
      <h4>Exits (${{Object.keys(r.exits||{{}}).length}})</h4>
      ${{exitsHtml}}
    </div>
    <div class="section">
      <h4>Items (${{(r.items||[]).length}})</h4>
      ${{itemsHtml}}
    </div>
    <div class="section">
      <h4>NPC Spawns (${{(r.npcs||[]).length}})</h4>
      ${{npcsHtml}}
    </div>
  `;
}}

function filterRooms(q) {{
  q = q.toLowerCase().trim();
  document.querySelectorAll('.room-node').forEach(node => {{
    const rid = node.getAttribute('data-id');
    const r   = ROOMS[rid];
    if (!r) return;
    const match = !q || rid.includes(q) || r.name.toLowerCase().includes(q) || r.zone.includes(q);
    node.style.opacity = match ? '1' : '0.15';
    node.style.pointerEvents = match ? '' : 'none';
  }});
}}
</script>
</body>
</html>
"""
    return html


def main():
    parser = argparse.ArgumentParser(description="Generate Delve HTML admin map")
    parser.add_argument("--out",  default="map.html", help="Output file path")
    parser.add_argument("--zone", default=None,        help="Only show one zone")
    args = parser.parse_args()

    world  = load_world()
    html   = build_html(world, args.zone)
    out    = Path(args.out)
    out.write_text(html, encoding="utf-8")

    n_rooms = len(world["rooms"])
    print(f"✓ Map written to {out} ({n_rooms} rooms, {len(world['zone_list'])} zones)")
    print(f"  Open in any browser: file://{out.resolve()}")


if __name__ == "__main__":
    main()
