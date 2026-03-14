#!/usr/bin/env python3
"""
tools/world2html.py — Export a world's TOML data to a single self-contained HTML file.

The document is intended for content review and flow checking — suitable for
printing or reading in a browser.  It includes a per-zone map, entity tables
(rooms, NPCs, items), quest/dialogue sections, scripts, flags, and a global
flag cross-reference index.

Usage:
  python tools/world2html.py                          # first world found
  python tools/world2html.py --world first_world
  python tools/world2html.py --world first_world --output review.html
  python tools/world2html.py --world first_world --zone ashwood

Output: tools/world_review.html  (or path given by --output)

Dialogue/quest graphs require Graphviz `dot` on PATH; HTML tree fallback is
used when graphviz is not available.
"""

from __future__ import annotations

import argparse
import html as _html_mod
import importlib.util
import subprocess
import sys
from pathlib import Path

_TOOLS = Path(__file__).parent
_ROOT  = _TOOLS.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_TOOLS))

from engine.toml_io     import load as toml_load                                   # noqa: E402
from engine.map_builder import apply_auto_layout, exit_dest, FACE_OFFSET, REVERSE_DIR  # noqa: E402
from graph_common       import (                                                    # noqa: E402
    load_dialogue_tree, reachable_nodes,
    format_script_ops, format_condition,
    node_header_color,
)
import md2html                                                                      # noqa: E402

# ── Constants ─────────────────────────────────────────────────────────────────

_DATA_DIR  = _ROOT / "data"
_SKIP_DIRS = {"zone_state", "players"}

_ZONE_COLORS = [
    "#4a7fd4", "#7db87d", "#c27a3a", "#a05090",
    "#5bb8c4", "#c44f4f", "#c4a83a", "#7a7aaa",
]

# ── World discovery ───────────────────────────────────────────────────────────

def _find_worlds(data_dir: Path = _DATA_DIR) -> list[Path]:
    if not data_dir.is_dir():
        return []
    return sorted(
        p for p in data_dir.iterdir()
        if p.is_dir() and ((p / "config.toml").exists() or (p / "config.py").exists())
    )


def _pick_world(world_arg: str | None) -> Path:
    worlds = _find_worlds()
    if not worlds:
        print("[error] No world folders found in data/.", file=sys.stderr)
        sys.exit(1)
    if world_arg:
        for w in worlds:
            if w.name == world_arg:
                return w
        names = [w.name for w in worlds]
        print(f"[error] World '{world_arg}' not found. Available: {names}", file=sys.stderr)
        sys.exit(1)
    return worlds[0]


def _peek_world_name(world_path: Path) -> str:
    cfg_toml = world_path / "config.toml"
    if cfg_toml.exists():
        try:
            return toml_load(cfg_toml).get("world_name", world_path.name)
        except Exception:
            pass
    cfg_py = world_path / "config.py"
    if cfg_py.exists():
        try:
            spec = importlib.util.spec_from_file_location("_wc_peek", cfg_py)
            mod  = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return getattr(mod, "WORLD_NAME", world_path.name)
        except Exception:
            pass
    return world_path.name


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_zone(zone_dir: Path) -> dict:
    """Load all data from a zone folder and return a zone dict."""
    zid = zone_dir.name
    zone_meta: dict = {"id": zid, "name": zid, "description": "", "admin_comment": ""}
    zone_toml = zone_dir / "zone.toml"
    if zone_toml.exists():
        try:
            zm = toml_load(zone_toml)
            zone_meta["name"]          = zm.get("name", zid)
            zone_meta["description"]   = zm.get("description", "")
            zone_meta["admin_comment"] = zm.get("admin_comment", "")
        except Exception:
            pass

    rooms: list[dict] = []
    npcs:  list[dict] = []
    items: list[dict] = []

    for f in sorted(zone_dir.glob("*.toml")):
        if f.name == "zone.toml":
            continue
        try:
            data = toml_load(f)
        except Exception as e:
            print(f"  [warn] {f}: {e}", file=sys.stderr)
            continue
        for r in data.get("room", []):
            r.setdefault("_zone", zid)
            rooms.append(r)
        npcs.extend(data.get("npc", []))
        items.extend(data.get("item", []))

    quests: list[dict] = []
    quests_dir = zone_dir / "quests"
    if quests_dir.exists():
        for f in sorted(quests_dir.glob("*.toml")):
            try:
                q = toml_load(f)
                q.setdefault("_path", str(f))
                quests.append(q)
            except Exception:
                pass

    dialogue_nodes: dict[str, dict] = {}
    dialogue_paths: dict[str, Path] = {}
    dlg_dir = zone_dir / "dialogues"
    if dlg_dir.exists():
        for f in sorted(dlg_dir.glob("*.toml")):
            nid = f.stem
            try:
                dialogue_nodes[nid] = load_dialogue_tree(f)
                dialogue_paths[nid] = f
            except Exception:
                pass

    styles: list[dict] = []
    styles_file = zone_dir / "styles" / "styles.toml"
    if styles_file.exists():
        try:
            styles = toml_load(styles_file).get("style", [])
        except Exception:
            pass

    return {
        **zone_meta,
        "rooms":          rooms,
        "npcs":           npcs,
        "items":          items,
        "quests":         quests,
        "dialogue_nodes": dialogue_nodes,
        "dialogue_paths": dialogue_paths,
        "styles":         styles,
    }


def _load_world(world_path: Path, zone_filter: str | None = None) -> dict:
    """Load all zones in a world and apply auto-layout. Returns world dict."""
    world_name = _peek_world_name(world_path)
    zones: list[dict] = []
    for zone_dir in sorted(
        p for p in world_path.iterdir()
        if p.is_dir() and p.name not in _SKIP_DIRS
    ):
        if zone_filter and zone_dir.name != zone_filter:
            continue
        print(f"  loading zone: {zone_dir.name}")
        zones.append(_load_zone(zone_dir))

    all_rooms: dict[str, dict] = {}
    for zone in zones:
        for r in zone["rooms"]:
            rid = r.get("id", "")
            if rid:
                all_rooms[rid] = r

    apply_auto_layout(all_rooms)

    return {
        "world_name": world_name,
        "world_path": world_path,
        "zones":      zones,
        "all_rooms":  all_rooms,
    }


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _h(s) -> str:
    return _html_mod.escape(str(s), quote=True)


def _fmt_script_html(ops: list | None) -> str:
    if not ops:
        return '<span class="none">-</span>'
    lines = format_script_ops(ops)
    return "".join(f'<div class="op">{_h(line)}</div>' for line in lines)


def _fmt_cond_html(cond) -> str:
    if not cond:
        return ""
    s = format_condition(cond) if isinstance(cond, dict) else str(cond)
    return f'<span class="cond">{_h(s)}</span>'


# ── DOT → SVG (piped, no temp files) ─────────────────────────────────────────

def _dot_to_svg(dot_str: str) -> str | None:
    """Pipe a DOT string to graphviz; return the SVG element string or None."""
    try:
        result = subprocess.run(
            ["dot", "-Tsvg"],
            input=dot_str,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            return None
        svg = result.stdout
        idx = svg.find("<svg")
        return svg[idx:] if idx >= 0 else svg
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


# ── Static SVG zone map ───────────────────────────────────────────────────────

_CELL_W, _CELL_H = 130, 72
_PAD_X,  _PAD_Y  = 50,  50


def _zone_svg(zone_id: str, all_rooms: dict, color: str) -> str:
    """Generate a static (no JS) inline SVG map for one zone."""
    zone_rooms = {
        rid: r for rid, r in all_rooms.items()
        if r.get("_zone") == zone_id and r.get("_display_coord")
    }
    if not zone_rooms:
        return "<p><em>No rooms with coordinates in this zone.</em></p>"

    xs = [r["_display_coord"][0] for r in zone_rooms.values()]
    ys = [r["_display_coord"][1] for r in zone_rooms.values()]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    grid_w = (max_x - min_x + 1) * _CELL_W + 2 * _PAD_X
    grid_h = (max_y - min_y + 1) * _CELL_H + 2 * _PAD_Y

    def cell_center(x: int, y: int) -> tuple[int, int]:
        cx = _PAD_X + (x - min_x) * _CELL_W + _CELL_W // 2
        cy = _PAD_Y + (max_y - y) * _CELL_H + _CELL_H // 2  # SVG Y-down
        return cx, cy

    svg_lines: list[str] = []
    svg_nodes: list[str] = []
    drawn_edges: set[frozenset] = set()

    for rid, room in zone_rooms.items():
        gx, gy = room["_display_coord"]
        cx, cy = cell_center(gx, gy)

        for direction, ev in room.get("exits", {}).items():
            dest = exit_dest(ev)
            if not dest:
                continue
            dest_room = all_rooms.get(dest)
            if not dest_room or not dest_room.get("_display_coord"):
                continue
            edge_key = frozenset([rid, dest])
            if edge_key in drawn_edges:
                continue
            drawn_edges.add(edge_key)

            dgx, dgy = dest_room["_display_coord"]
            dcx, dcy = cell_center(dgx, dgy)

            fo  = FACE_OFFSET.get(direction, (0.0, 0.0))
            x1  = cx + int(fo[0] * _CELL_W / 2)
            y1  = cy + int(fo[1] * _CELL_H / 2)
            rev = REVERSE_DIR.get(direction, direction)
            rfo = FACE_OFFSET.get(rev, (0.0, 0.0))
            x2  = dcx + int(rfo[0] * _CELL_W / 2)
            y2  = dcy + int(rfo[1] * _CELL_H / 2)

            locked  = isinstance(ev, dict) and ev.get("locked")
            lc      = "#c04040" if locked else "#888"
            l_extra = 'stroke-dasharray="5,3" ' if locked else ""
            svg_lines.append(
                f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                f'stroke="{lc}" stroke-width="1.5" {l_extra}/>'
            )
            if dest_room.get("_zone") != zone_id:
                mid_x = (x1 + x2) // 2
                mid_y = (y1 + y2) // 2 - 3
                other_zone = _h(dest_room.get("_zone", ""))
                svg_lines.append(
                    f'<text x="{mid_x}" y="{mid_y}" font-size="8" '
                    f'fill="#999" text-anchor="middle">[{other_zone}]</text>'
                )

        is_start = room.get("start", False)
        is_auto  = room.get("_auto_placed", False)
        border   = "#c07800" if is_start else color
        bw       = "2.5" if is_start else "1.5"
        sdash    = 'stroke-dasharray="5,3"' if is_auto else ""
        label    = _h(room.get("name", rid)[:18])
        rid_lbl  = _h(rid[:22])
        star     = " *" if is_start else ""

        ni = len(room.get("items",  []))
        ns = len(room.get("spawns", []))

        badges = ""
        flags  = room.get("flags", [])
        if "town" in flags:
            badges += '<text x="4" y="13" font-size="8" fill="#4a7fd4">T</text>'
        if "no_combat" in flags:
            badges += '<text x="4" y="23" font-size="8" fill="#888">nc</text>'

        counts = ""
        if ni:
            counts += (f'<text x="{_CELL_W - 6}" y="13" font-size="8" '
                       f'fill="#c07800" text-anchor="end">i:{ni}</text>')
        if ns:
            counts += (f'<text x="{_CELL_W - 6}" y="23" font-size="8" '
                       f'fill="#a05050" text-anchor="end">n:{ns}</text>')

        tx = _CELL_W // 2
        ty_name = _CELL_H // 2 - 5
        ty_id   = _CELL_H // 2 + 9
        svg_nodes.append(
            f'  <g transform="translate({cx - _CELL_W // 2},{cy - _CELL_H // 2})">'
            f'<rect width="{_CELL_W - 6}" height="{_CELL_H - 6}" rx="4" x="3" y="3"'
            f' fill="{color}18" stroke="{border}" stroke-width="{bw}" {sdash}/>'
            f'{badges}{counts}'
            f'<text x="{tx}" y="{ty_name}" text-anchor="middle"'
            f' font-size="10" font-weight="bold" fill="#222">{label}{star}</text>'
            f'<text x="{tx}" y="{ty_id}" text-anchor="middle"'
            f' font-size="8" fill="#666">{rid_lbl}</text>'
            f'</g>'
        )

    lines_svg = "\n".join(svg_lines)
    nodes_svg = "\n".join(svg_nodes)
    return (
        f'<svg width="{grid_w}" height="{grid_h}" '
        f'style="max-width:100%;border:1px solid #ddd;border-radius:4px;background:#fafaf8">'
        f'\n{lines_svg}\n{nodes_svg}\n</svg>'
    )


# ── Flag index builder ────────────────────────────────────────────────────────

def _scan_ops(ops, etype: str, eid: str, idx: dict) -> None:
    if not isinstance(ops, list):
        return
    for op in ops:
        if not isinstance(op, dict):
            continue
        name = op.get("op", "")
        flag = op.get("flag", "")
        if name == "set_flag" and flag:
            idx.setdefault(flag, []).append({"action": "set",   "type": etype, "id": eid})
        elif name == "clear_flag" and flag:
            idx.setdefault(flag, []).append({"action": "clear", "type": etype, "id": eid})
        elif name in ("if_flag", "if_not_flag") and flag:
            idx.setdefault(flag, []).append({"action": "check", "type": etype, "id": eid})
        for sub_key in ("then", "else", "body"):
            _scan_ops(op.get(sub_key, []), etype, eid, idx)


def _scan_cond(cond, etype: str, eid: str, idx: dict) -> None:
    if not isinstance(cond, dict):
        return
    for key in ("flag", "not_flag"):
        val = cond.get(key)
        if val:
            idx.setdefault(val, []).append({"action": "check", "type": etype, "id": eid})


def _scan_show_if(show_if, etype: str, eid: str, idx: dict) -> None:
    if not isinstance(show_if, dict):
        return
    flag = show_if.get("flag", "")
    if flag:
        idx.setdefault(flag, []).append({"action": "show_if", "type": etype, "id": eid})


def _build_flag_index(zones: list[dict]) -> dict[str, list[dict]]:
    idx: dict[str, list[dict]] = {}
    for zone in zones:
        for room in zone["rooms"]:
            rid = room.get("id", "?")
            _scan_ops(room.get("on_enter", []), "room", rid, idx)
            for direction, ev in room.get("exits", {}).items():
                if isinstance(ev, dict):
                    _scan_ops(ev.get("on_exit", []), "exit", f"{rid}.{direction}", idx)
                    _scan_show_if(ev.get("show_if"), "exit", f"{rid}.{direction}", idx)

        for npc in zone["npcs"]:
            nid = npc.get("id", "?")
            _scan_ops(npc.get("kill_script", []), "npc", nid, idx)
            _scan_ops(npc.get("on_talk",     []), "npc", nid, idx)

        for item in zone["items"]:
            iid = item.get("id", "?")
            for hook in ("on_get", "on_use", "on_drop"):
                _scan_ops(item.get(hook, []), "item", iid, idx)

        for quest in zone["quests"]:
            qid = quest.get("id", "?")
            for step in quest.get("step", []):
                _scan_ops(step.get("script",     []), "quest_step", qid, idx)
                _scan_ops(step.get("on_advance", []), "quest_step", qid, idx)
            _scan_ops(quest.get("on_complete", []), "quest", qid, idx)

        for npc_id, nodes in zone["dialogue_nodes"].items():
            for node_id, node in nodes.items():
                eid = f"{npc_id}/{node_id}"
                _scan_ops(node.get("script", []), "dialogue", eid, idx)
                _scan_cond(node.get("condition"), "dialogue", eid, idx)
                for resp in node.get("response", []):
                    _scan_ops(resp.get("script", []), "dialogue", eid, idx)
                    _scan_cond(resp.get("condition"), "dialogue", eid, idx)

    return dict(sorted(idx.items()))


# ── Dialogue section ──────────────────────────────────────────────────────────

def _dialogue_tree_fallback(npc_id: str, nodes: dict) -> str:
    """Render dialogue tree as nested <details> elements (graphviz fallback)."""
    reach = reachable_nodes(nodes)
    parts: list[str] = ['<div class="dlg-tree">']
    for node_id, node in nodes.items():
        unreachable  = node_id not in reach and node_id != "root"
        hcolor       = node_header_color(node.get("script", []))
        cond         = node.get("condition")
        cond_html    = (_fmt_cond_html(cond) + " &rarr; ") if cond else ""
        line         = node.get("line", "") or " | ".join(node.get("lines", []))
        script       = node.get("script", [])
        responses    = node.get("response", [])
        orphan_badge = " <em>(orphan)</em>" if unreachable else ""
        opacity      = ' style="opacity:0.5"' if unreachable else ""

        parts.append(f'<details class="dlg-node"{opacity}>')
        parts.append(
            f'<summary style="background:{hcolor}">'
            f'{cond_html}<b>{_h(node_id)}</b>{orphan_badge}'
            f'</summary>'
        )
        if line:
            parts.append(f'<blockquote class="dlg-line">{_h(line)}</blockquote>')
        if script:
            parts.append(f'<div class="dlg-ops">{_fmt_script_html(script)}</div>')
        if responses:
            parts.append('<ul class="dlg-responses">')
            for resp in responses:
                rtext    = resp.get("text", "(no text)")
                rnext    = resp.get("next", "")
                rcond    = resp.get("condition")
                roscript = resp.get("script", [])
                rcond_s  = (_fmt_cond_html(rcond) + " ") if rcond else ""
                rnext_s  = (f" &rarr; <b>{_h(rnext)}</b>") if rnext else ""
                rscript_s = (f"<br>{_fmt_script_html(roscript)}") if roscript else ""
                parts.append(
                    f'<li>{rcond_s}{_h(rtext)}{rnext_s}{rscript_s}</li>'
                )
            parts.append('</ul>')
        parts.append('</details>')
    parts.append('</div>')
    return "\n".join(parts)


def _dialogue_html(npc_id: str, path: Path | None, nodes: dict, world_name: str) -> str:
    """Return HTML for one NPC's dialogue tree: SVG diagram + always-visible HTML tree."""
    if not nodes:
        return "<p><em>No dialogue nodes.</em></p>"

    parts: list[str] = []

    # Graphviz diagram (shown when available)
    if path is not None:
        try:
            from dialogue_graph import build_dialogue_dot  # noqa: PLC0415
            dot_str = build_dialogue_dot(npc_id, path, world_name)
            if dot_str:
                svg = _dot_to_svg(dot_str)
                if svg:
                    parts.append(
                        f'<details class="quest-graph-toggle" open>'
                        f'<summary>Flow diagram</summary>'
                        f'<div class="dlg-graph" style="overflow:auto">{svg}</div>'
                        f'</details>'
                    )
        except (ImportError, Exception):
            pass

    # HTML tree — always shown (text + ops visible without graphviz)
    parts.append(_dialogue_tree_fallback(npc_id, nodes))
    return "\n".join(parts)


# ── Quest section ─────────────────────────────────────────────────────────────

def _quest_rewards_html(rewards: list) -> str:
    parts: list[str] = []
    for r in rewards:
        rt = r.get("type", "")
        if   rt == "gold": parts.append(f'{r.get("amount", "?")} gold')
        elif rt == "xp":   parts.append(f'{r.get("amount", "?")} XP')
        elif rt == "item": parts.append(r.get("item_id", "?"))
    return ", ".join(parts) if parts else ""


def _extract_tag_actions(script: list) -> tuple[list[str], list[str], list[dict]]:
    """Return (tags_set, tags_cleared, remaining_ops) from a script list."""
    tags_set:   list[str]  = []
    tags_clear: list[str]  = []
    remaining:  list[dict] = []
    for op in script:
        if not isinstance(op, dict):
            continue
        name = op.get("op", "")
        if name == "set_flag":
            tags_set.append(op.get("flag", "?"))
        elif name == "clear_flag":
            tags_clear.append(op.get("flag", "?"))
        else:
            remaining.append(op)
    return tags_set, tags_clear, remaining


def _quest_html(quest: dict, world_path: Path, world_name: str) -> str:
    qid     = quest.get("id", "?")
    title   = quest.get("title", qid)
    giver   = quest.get("giver", "")
    summary = quest.get("summary", "")
    steps   = quest.get("step", [])
    rewards = quest.get("reward", [])

    parts: list[str] = [f'<div class="quest" id="quest-{_h(qid)}">']
    parts.append(f'<h4>{_h(title)} <span class="id-badge">{_h(qid)}</span></h4>')
    if summary:
        parts.append(f'<p class="meta">{_h(summary)}</p>')
    if giver:
        parts.append(f'<p class="meta">Giver: <b>{_h(giver)}</b></p>')

    reward_str = _quest_rewards_html(rewards)
    if reward_str:
        parts.append(
            f'<p class="meta">Rewards: <span class="reward">{_h(reward_str)}</span></p>'
        )

    if steps:
        parts.append('<ol class="quest-steps">')
        for step in steps:
            snum  = step.get("index", step.get("step", "?"))
            obj   = step.get("objective", "")
            hint  = step.get("hint", "")
            cflag = step.get("completion_flag", "")
            trig  = step.get("trigger", {})
            script = step.get("script", []) or step.get("on_advance", [])

            tags_set, tags_clear, other_ops = _extract_tag_actions(script)

            parts.append(f'<li><b>Step {_h(str(snum))}</b>: {_h(obj)}')
            if hint:
                parts.append(f'<br><span class="meta">Hint: {_h(hint)}</span>')
            if trig:
                trig_str = ", ".join(f"{_h(k)}={_h(v)}" for k, v in trig.items())
                parts.append(f'<br><span class="meta">trigger: {trig_str}</span>')
            if cflag:
                parts.append(
                    f'<br><span class="tag-action tag-set">sets: {_h(cflag)}</span>'
                )
            if tags_set:
                badges = " ".join(
                    f'<span class="tag-action tag-set">+{_h(f)}</span>'
                    for f in tags_set
                )
                parts.append(f'<br>{badges}')
            if tags_clear:
                badges = " ".join(
                    f'<span class="tag-action tag-clear">-{_h(f)}</span>'
                    for f in tags_clear
                )
                parts.append(f'<br>{badges}')
            if other_ops:
                parts.append(
                    f'<div class="script-block">{_fmt_script_html(other_ops)}</div>'
                )
            parts.append('</li>')
        parts.append('</ol>')

    complete = quest.get("on_complete", [])
    if complete:
        tags_set, tags_clear, other_ops = _extract_tag_actions(complete)
        parts.append('<div class="section-label">on_complete:</div>')
        if tags_set:
            badges = " ".join(
                f'<span class="tag-action tag-set">+{_h(f)}</span>' for f in tags_set
            )
            parts.append(f'<div style="margin:3px 0">{badges}</div>')
        if tags_clear:
            badges = " ".join(
                f'<span class="tag-action tag-clear">-{_h(f)}</span>' for f in tags_clear
            )
            parts.append(f'<div style="margin:3px 0">{badges}</div>')
        if other_ops:
            parts.append(f'<div class="script-block">{_fmt_script_html(other_ops)}</div>')

    # Quest flow diagram
    try:
        from quest_graph import build_quest_dot  # noqa: PLC0415
        dot_str = build_quest_dot(qid, quest, world_path, world_name)
        if dot_str:
            svg = _dot_to_svg(dot_str)
            if svg:
                parts.append(
                    f'<details class="quest-graph-toggle">'
                    f'<summary>Flow diagram</summary>'
                    f'<div style="overflow:auto">{svg}</div>'
                    f'</details>'
                )
    except (ImportError, Exception):
        pass

    parts.append('</div>')
    return "\n".join(parts)


# ── Entity table builders ─────────────────────────────────────────────────────

def _rooms_section(rooms: list[dict], all_rooms: dict) -> str:
    if not rooms:
        return "<p><em>No rooms.</em></p>"
    rows: list[str] = []
    for room in rooms:
        rid      = room.get("id", "")
        name     = room.get("name", "")
        desc     = room.get("description", "")
        flags    = room.get("flags", [])
        exits    = room.get("exits", {})
        items    = room.get("items", [])
        spwns    = room.get("spawns", [])
        on_enter = room.get("on_enter", [])

        exit_parts = []
        for d, ev in exits.items():
            dest      = exit_dest(ev)
            locked    = isinstance(ev, dict) and ev.get("locked")
            lock_s    = " [locked]" if locked else ""
            dest_name = all_rooms.get(dest, {}).get("name", dest) if dest else "?"
            exit_parts.append(f"{d}&rarr;{_h(dest_name)}{lock_s}")

        desc_trunc = desc[:120] + ("..." if len(desc) > 120 else "")
        desc_html  = (f'<br><span class="desc">{_h(desc_trunc)}</span>') if desc else ""
        flags_str  = ", ".join(str(f) for f in flags)
        exits_str  = " &middot; ".join(exit_parts)
        items_str  = _h(", ".join(str(i) for i in items))
        spwns_str  = _h(", ".join(str(s) for s in spwns))

        rows.append(
            f'<tr>'
            f'<td class="id-cell">{_h(rid)}</td>'
            f'<td><b>{_h(name)}</b>{desc_html}</td>'
            f'<td class="small">{_h(flags_str)}</td>'
            f'<td class="small">{exits_str}</td>'
            f'<td class="small">{items_str}</td>'
            f'<td class="small">{spwns_str}</td>'
            f'<td class="small">{_fmt_script_html(on_enter) if on_enter else "-"}</td>'
            f'</tr>'
        )

    header = (
        '<thead><tr>'
        '<th>ID</th><th>Name / Desc</th><th>Flags</th>'
        '<th>Exits</th><th>Items</th><th>Spawns</th><th>on_enter</th>'
        '</tr></thead>'
    )
    return f'<table class="entity-table"><{header}<tbody>{"".join(rows)}</tbody></table>'


def _npcs_section(npcs: list[dict]) -> str:
    if not npcs:
        return "<p><em>No NPCs.</em></p>"
    rows: list[str] = []
    for npc in npcs:
        nid     = npc.get("id", "")
        name    = npc.get("name", "")
        hp      = npc.get("hp", "")
        atk     = npc.get("attack", "")
        df      = npc.get("defense", "")
        style   = npc.get("style", "")
        tags    = npc.get("tags", [])
        hostile = npc.get("hostile", True)
        kill    = npc.get("kill_script", [])

        hostile_str = "yes" if hostile else '<span class="friendly">no</span>'
        tags_str    = _h(", ".join(str(t) for t in tags))

        rows.append(
            f'<tr>'
            f'<td class="id-cell">{_h(nid)}</td>'
            f'<td><b>{_h(name)}</b></td>'
            f'<td class="center">{_h(str(hp))}</td>'
            f'<td class="center">{_h(str(atk))}</td>'
            f'<td class="center">{_h(str(df))}</td>'
            f'<td class="small">{_h(style)}</td>'
            f'<td class="center">{hostile_str}</td>'
            f'<td class="small">{tags_str}</td>'
            f'<td class="small">{_fmt_script_html(kill) if kill else "-"}</td>'
            f'</tr>'
        )

    header = (
        '<thead><tr>'
        '<th>ID</th><th>Name</th><th>HP</th><th>ATK</th><th>DEF</th>'
        '<th>Style</th><th>Hostile</th><th>Tags</th><th>kill_script</th>'
        '</tr></thead>'
    )
    return f'<table class="entity-table"><{header}<tbody>{"".join(rows)}</tbody></table>'


def _items_section(items: list[dict]) -> str:
    if not items:
        return "<p><em>No items.</em></p>"
    rows: list[str] = []
    for item in items:
        iid     = item.get("id", "")
        name    = item.get("name", "")
        slot    = item.get("slot", "")
        weight  = item.get("weight", "")
        tags    = item.get("tags", [])
        scenery = item.get("scenery", False)
        on_get  = item.get("on_get", [])
        on_use  = item.get("on_use", [])

        scenery_s = "yes" if scenery else ""
        tags_str  = _h(", ".join(str(t) for t in tags))

        rows.append(
            f'<tr>'
            f'<td class="id-cell">{_h(iid)}</td>'
            f'<td><b>{_h(name)}</b></td>'
            f'<td class="center">{_h(str(slot))}</td>'
            f'<td class="center">{_h(str(weight))}</td>'
            f'<td class="center">{scenery_s}</td>'
            f'<td class="small">{tags_str}</td>'
            f'<td class="small">{_fmt_script_html(on_get) if on_get else "-"}</td>'
            f'<td class="small">{_fmt_script_html(on_use) if on_use else "-"}</td>'
            f'</tr>'
        )

    header = (
        '<thead><tr>'
        '<th>ID</th><th>Name</th><th>Slot</th><th>Weight</th>'
        '<th>Scenery</th><th>Tags</th><th>on_get</th><th>on_use</th>'
        '</tr></thead>'
    )
    return f'<table class="entity-table"><{header}<tbody>{"".join(rows)}</tbody></table>'


def _styles_section(styles: list[dict]) -> str:
    if not styles:
        return "<p><em>No styles.</em></p>"
    rows: list[str] = []
    for s in styles:
        sid  = s.get("id", "")
        name = s.get("name", "")
        desc = s.get("description", "")
        rows.append(
            f'<tr>'
            f'<td class="id-cell">{_h(sid)}</td>'
            f'<td><b>{_h(name)}</b><br><span class="desc">{_h(desc)}</span></td>'
            f'</tr>'
        )
    header = '<thead><tr><th>ID</th><th>Name / Description</th></tr></thead>'
    return f'<table class="entity-table"><{header}<tbody>{"".join(rows)}</tbody></table>'


# ── Flag index section ────────────────────────────────────────────────────────

_ACTION_COLOR = {
    "set":     "#2d6a2d",
    "clear":   "#8b2020",
    "check":   "#1a4a8b",
    "show_if": "#6a4a1a",
}


def _flag_index_section(flag_index: dict) -> str:
    if not flag_index:
        return "<p><em>No flags found.</em></p>"
    rows: list[str] = []
    for flag, usages in flag_index.items():
        cells = "".join(
            f'<span class="flag-usage" style="border-color:{_ACTION_COLOR.get(u["action"],"#888")}">'
            f'<b>{_h(u["action"])}</b> &middot; {_h(u["type"])} &middot; {_h(u["id"])}'
            f'</span>'
            for u in usages
        )
        rows.append(
            f'<tr>'
            f'<td class="id-cell">{_h(flag)}</td>'
            f'<td>{cells}</td>'
            f'</tr>'
        )
    header = '<thead><tr><th>Flag</th><th>Usages (action &middot; type &middot; entity ID)</th></tr></thead>'
    return f'<table class="entity-table flag-table"><{header}<tbody>{"".join(rows)}</tbody></table>'


# ── Zone section ──────────────────────────────────────────────────────────────

def _zone_section(zone: dict, color: str, all_rooms: dict, world_name: str, world_path: Path) -> str:
    zid        = zone["id"]
    name       = zone["name"]
    desc       = zone.get("description", "")
    comment_md = zone.get("admin_comment", "")

    parts: list[str] = [f'<section class="zone-section" id="zone-{_h(zid)}">']
    parts.append(
        f'<h2>{_h(name)} <span class="id-badge">{_h(zid)}</span></h2>'
    )
    if desc:
        parts.append(f'<p class="zone-desc">{_h(desc)}</p>')
    if comment_md:
        parts.append(f'<div class="admin-comment">{md2html.convert(comment_md)}</div>')

    # Map
    parts.append('<details class="subsection" open>')
    parts.append(f'<summary>Map ({len(zone["rooms"])} rooms)</summary>')
    parts.append(f'<div style="overflow:auto">{_zone_svg(zid, all_rooms, color)}</div>')
    parts.append('</details>')

    # Rooms
    parts.append('<details class="subsection" open>')
    parts.append(f'<summary>Rooms ({len(zone["rooms"])})</summary>')
    parts.append(_rooms_section(zone["rooms"], all_rooms))
    parts.append('</details>')

    # NPCs
    parts.append('<details class="subsection" open>')
    parts.append(f'<summary>NPCs ({len(zone["npcs"])})</summary>')
    parts.append(_npcs_section(zone["npcs"]))
    parts.append('</details>')

    # Items
    parts.append('<details class="subsection" open>')
    parts.append(f'<summary>Items ({len(zone["items"])})</summary>')
    parts.append(_items_section(zone["items"]))
    parts.append('</details>')

    # Styles (collapsed by default — less commonly reviewed)
    if zone["styles"]:
        parts.append('<details class="subsection">')
        parts.append(f'<summary>Styles ({len(zone["styles"])})</summary>')
        parts.append(_styles_section(zone["styles"]))
        parts.append('</details>')

    # Quests
    if zone["quests"]:
        parts.append('<details class="subsection" open>')
        parts.append(f'<summary>Quests ({len(zone["quests"])})</summary>')
        for quest in zone["quests"]:
            parts.append(_quest_html(quest, world_path, world_name))
        parts.append('</details>')

    # Dialogues (collapsed by default — can be large)
    if zone["dialogue_nodes"]:
        parts.append('<details class="subsection">')
        parts.append(f'<summary>Dialogues ({len(zone["dialogue_nodes"])} NPCs)</summary>')
        for npc_id in sorted(zone["dialogue_nodes"]):
            nodes = zone["dialogue_nodes"][npc_id]
            path  = zone["dialogue_paths"].get(npc_id)
            parts.append('<details class="dlg-section">')
            parts.append(f'<summary>{_h(npc_id)} ({len(nodes)} nodes)</summary>')
            parts.append(_dialogue_html(npc_id, path, nodes, world_name))
            parts.append('</details>')
        parts.append('</details>')

    parts.append('</section>')
    return "\n".join(parts)


# ── CSS ───────────────────────────────────────────────────────────────────────

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: monospace; font-size: 13px; background: #f5f5f0; color: #1a1a2e;
       display: flex; min-height: 100vh; }

/* Sidebar */
#toc { width: 220px; min-width: 180px; background: #e8e8e0;
       border-right: 1px solid #ccc; position: sticky; top: 0;
       height: 100vh; overflow-y: auto; flex-shrink: 0; }
#toc h1 { font-size: 13px; padding: 12px 10px 6px; color: #333;
          border-bottom: 1px solid #ccc; }
#toc .stats { font-size: 11px; color: #666; padding: 6px 10px 8px;
              border-bottom: 1px solid #ccc; line-height: 1.6; }
#toc ul { list-style: none; padding: 8px 0; }
#toc li a { display: block; padding: 4px 10px; color: #2a5a9a;
            text-decoration: none; font-size: 11px; }
#toc li a:hover { background: #d8d8d0; }
#toc .toc-section { padding: 6px 10px 2px; font-size: 10px; color: #888;
                    text-transform: uppercase; letter-spacing: 1px; }

/* Main */
#main { flex: 1; padding: 24px 32px; max-width: 1200px; overflow-x: auto; }
h1 { font-size: 20px; margin-bottom: 4px; }
h2 { font-size: 15px; margin: 24px 0 8px; color: #1a3a6a;
     border-bottom: 2px solid #c07800; padding-bottom: 4px; }
h3 { font-size: 13px; margin: 14px 0 5px; color: #333; }
h4 { font-size: 12px; margin: 8px 0 3px; color: #222; }

/* Overview */
.overview-table { border-collapse: collapse; margin: 12px 0; font-size: 12px; }
.overview-table td, .overview-table th { padding: 4px 14px; border: 1px solid #ccc; }
.overview-table th { background: #e0e0d8; }

/* Zone section */
.zone-section { margin-bottom: 40px; }
.zone-desc { color: #555; margin-bottom: 8px; font-size: 12px; }
.admin-comment { background: #fffff0; border: 1px solid #d4c84c; border-radius: 4px;
                 padding: 10px 14px; margin: 8px 0 12px; font-size: 12px; line-height: 1.6; }
.admin-comment h1, .admin-comment h2, .admin-comment h3 {
  border: none; font-size: 13px; color: #555; margin: 8px 0 4px; }

/* Subsections */
.subsection { margin: 8px 0; border: 1px solid #ccc; border-radius: 4px; }
.subsection > summary { padding: 5px 10px; background: #e8e8e0; cursor: pointer;
                         font-weight: bold; font-size: 12px; color: #333; user-select: none; }
.subsection > summary:hover { background: #ddd8d0; }
.subsection > *:not(summary) { padding: 8px; }

/* Entity tables */
.entity-table { border-collapse: collapse; width: 100%; font-size: 11px; }
.entity-table th { background: #e8e4d8; text-align: left; padding: 4px 8px;
                   border-bottom: 2px solid #bbb; white-space: nowrap; }
.entity-table td { padding: 3px 8px; border-bottom: 1px solid #e4e0da; vertical-align: top; }
.entity-table tr:hover td { background: #f0ede4; }
.id-cell   { color: #555; font-size: 10px; white-space: nowrap; }
.desc      { color: #666; font-size: 11px; }
.small     { font-size: 10px; color: #555; }
.center    { text-align: center; }
.meta      { font-size: 11px; color: #666; margin: 3px 0; }
.id-badge  { font-size: 10px; background: #e0d8c0; color: #666; padding: 1px 6px;
             border-radius: 3px; font-weight: normal; }
.friendly  { color: #2a7a2a; }
.none      { color: #bbb; }
.section-label { font-size: 10px; color: #888; text-transform: uppercase;
                 letter-spacing: 1px; margin: 6px 0 2px; }

/* Script ops */
.op { font-size: 10px; color: #1a3a6a; background: #e4eef8; border-radius: 2px;
      padding: 1px 4px; margin: 1px 0; display: inline-block; }
.cond { font-size: 10px; color: #7a4a00; background: #fff4d8; border-radius: 2px;
        padding: 1px 4px; }
.script-block { margin: 4px 0; }

/* Quests */
.quest { border: 1px solid #d8d0b8; border-radius: 4px; padding: 10px;
         margin: 6px 0; background: #fffef8; }
.quest-steps { margin: 6px 0 0 20px; }
.quest-steps li { margin: 5px 0; font-size: 11px; }

/* Dialogue */
.dlg-tree { font-size: 11px; }
.dlg-node > summary { padding: 3px 8px; cursor: pointer; border-radius: 2px;
                      list-style: none; }
.dlg-node > summary:hover { filter: brightness(0.95); }
.dlg-line { color: #444; font-style: italic; margin: 4px 16px; font-size: 11px;
            border-left: 3px solid #c07800; padding-left: 8px; }
.dlg-ops  { margin: 3px 8px; }
.dlg-responses { margin: 3px 16px; list-style: disc; color: #333; }
.dlg-responses li { margin: 3px 0; font-size: 11px; }
.dlg-graph svg { max-width: 100%; height: auto; }
.dlg-section > summary { padding: 4px 10px; cursor: pointer; background: #ede8e0;
                          border-radius: 3px; margin: 3px 0; font-size: 11px;
                          user-select: none; }
.dlg-section > summary:hover { background: #e0d8ce; }

/* Flag index */
.flag-table td { vertical-align: top; }
.flag-usage { display: inline-block; border: 1px solid #888; border-radius: 3px;
              padding: 1px 5px; margin: 2px; font-size: 10px; }

/* Rewards */
.reward { color: #7a5000; background: #fff0cc; border-radius: 3px;
          padding: 1px 6px; font-weight: bold; }

/* Tag action badges */
.tag-action { display: inline-block; border-radius: 3px; padding: 1px 5px;
              font-size: 10px; margin: 1px; font-weight: bold; }
.tag-set   { background: #d4edda; color: #1a5c2a; border: 1px solid #8bc89b; }
.tag-clear { background: #f8d7da; color: #721c24; border: 1px solid #e8a0a5; }

/* Quest/dialogue diagram toggle */
.quest-graph-toggle { margin: 8px 0; border: 1px solid #d8d0b8; border-radius: 4px; }
.quest-graph-toggle > summary { padding: 4px 10px; cursor: pointer; background: #f0ebe0;
                                 font-size: 11px; user-select: none; }
.quest-graph-toggle > summary:hover { background: #e8e0d0; }
.quest-graph-toggle > div { padding: 8px; }

/* Print styles */
@media print {
  body    { display: block; }
  #toc    { display: none; }
  #main   { max-width: 100%; padding: 0; }
  details { display: block !important; }
  details > summary { display: none; }
  .zone-section { page-break-before: always; }
  .entity-table tr:hover td { background: none; }
  .admin-comment { border: 1px solid #ccc; background: #fff; }
}
"""


# ── HTML assembly ─────────────────────────────────────────────────────────────

def generate(world_data: dict, output_path: Path) -> None:
    world_name = world_data["world_name"]
    zones      = world_data["zones"]
    all_rooms  = world_data["all_rooms"]
    zone_color = {
        z["id"]: _ZONE_COLORS[i % len(_ZONE_COLORS)]
        for i, z in enumerate(zones)
    }

    total_rooms  = sum(len(z["rooms"])          for z in zones)
    total_npcs   = sum(len(z["npcs"])           for z in zones)
    total_items  = sum(len(z["items"])          for z in zones)
    total_quests = sum(len(z["quests"])         for z in zones)
    total_dlg    = sum(len(z["dialogue_nodes"]) for z in zones)

    print("  building flag index...")
    flag_index = _build_flag_index(zones)

    # TOC
    zone_links = "".join(
        f'<li><a href="#zone-{z["id"]}">'
        f'<span style="display:inline-block;width:8px;height:8px;border-radius:2px;'
        f'background:{zone_color[z["id"]]};margin-right:5px"></span>'
        f'{_h(z["name"])}</a></li>'
        for z in zones
    )
    toc = (
        f'<nav id="toc">'
        f'<h1>{_h(world_name)}</h1>'
        f'<div class="stats">'
        f'{len(zones)} zones &middot; {total_rooms} rooms<br>'
        f'{total_npcs} NPCs &middot; {total_items} items<br>'
        f'{total_quests} quests &middot; {total_dlg} NPCs w/ dialogue<br>'
        f'{len(flag_index)} unique flags'
        f'</div>'
        f'<div class="toc-section">Zones</div>'
        f'<ul>{zone_links}</ul>'
        f'<ul><li><a href="#flag-index">Flag Index ({len(flag_index)})</a></li></ul>'
        f'</nav>'
    )

    # Overview
    overview = (
        f'<section id="overview">'
        f'<h1>{_h(world_name)} &mdash; World Review</h1>'
        f'<p style="color:#666;font-size:11px;margin:4px 0 12px">'
        f'Generated from: {_h(str(world_data["world_path"]))}</p>'
        f'<table class="overview-table">'
        f'<tr><th>Zones</th><th>Rooms</th><th>NPCs</th><th>Items</th>'
        f'<th>Quests</th><th>Dialogue NPCs</th><th>Flags</th></tr>'
        f'<tr>'
        f'<td class="center">{len(zones)}</td>'
        f'<td class="center">{total_rooms}</td>'
        f'<td class="center">{total_npcs}</td>'
        f'<td class="center">{total_items}</td>'
        f'<td class="center">{total_quests}</td>'
        f'<td class="center">{total_dlg}</td>'
        f'<td class="center">{len(flag_index)}</td>'
        f'</tr></table>'
        f'</section>'
    )

    print("  rendering zone sections...")
    zone_sections: list[str] = []
    for zone in zones:
        print(f"    {zone['id']}")
        zone_sections.append(
            _zone_section(zone, zone_color[zone["id"]], all_rooms, world_name, world_data["world_path"])
        )

    flag_section = (
        f'<section class="zone-section" id="flag-index">'
        f'<h2>Flag Index <span class="id-badge">{len(flag_index)} flags</span></h2>'
        f'{_flag_index_section(flag_index)}'
        f'</section>'
    )

    html_out = (
        f'<!DOCTYPE html>\n<html lang="en">\n<head>'
        f'<meta charset="UTF-8">'
        f'<title>{_h(world_name)} &mdash; World Review</title>'
        f'<style>{_CSS}</style>'
        f'</head>\n<body>'
        f'{toc}'
        f'<div id="main">'
        f'{overview}'
        f'{"".join(zone_sections)}'
        f'{flag_section}'
        f'</div>'
        f'</body></html>'
    )

    output_path.write_text(html_out, encoding="utf-8")
    size_kb = output_path.stat().st_size // 1024
    print(f"  wrote {output_path}  ({size_kb} KB)")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export a Delve world to a self-contained HTML review document."
    )
    parser.add_argument("--world",  metavar="NAME",
                        help="World folder name (default: first found)")
    parser.add_argument("--zone",   metavar="NAME",
                        help="Limit output to one zone")
    parser.add_argument("--output", metavar="PATH",
                        help="Output file path (default: tools/world_review.html)")
    args = parser.parse_args()

    world_path  = _pick_world(args.world)
    output_path = Path(args.output) if args.output else _TOOLS / "world_review.html"

    print(f"world2html: {world_path.name} -> {output_path}")
    world_data = _load_world(world_path, zone_filter=args.zone)
    generate(world_data, output_path)
    print("done.")


if __name__ == "__main__":
    main()
