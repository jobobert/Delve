#!/usr/bin/env python3
"""
wct/wct_server.py — Delve World Creation Tool (WCT) server.

A lightweight local HTTP server that serves the WCT frontend and exposes a
JSON API for reading and writing TOML data files.

Usage:
    python wct/wct_server.py            # starts on http://localhost:7373
    python wct/wct_server.py --port 8080
    python launch_wct.py                # recommended: opens server + browser

The WCT does NOT open a browser automatically. Navigate to the URL shown on startup,
or use --browser to open it automatically.

Game web frontend is served separately — see launch_web.py / frontend/web_server.py.
"""

from __future__ import annotations
import argparse
import copy
import json
import os
import re
import shutil
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
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


# ── TOML helpers ──────────────────────────────────────────────────────────────

def _load_world(world_id: str) -> dict:
    """Load a world's zones into a JSON-serialisable structure."""
    world_base = DATA_DIR / world_id
    if not world_base.is_dir():
        return {"error": f"World '{world_id}' not found", "zones": {}}
    world = {
        "zones":  {},   # zone_id → {rooms, items, npcs, quests, dialogues, companions, crafting}
    }
    skip = {"zone_state", "players", "__pycache__", "shared"}
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

    # Shared dialogues — data/<world_id>/shared/*.toml
    # Scanned separately so they don't appear as a game zone, but are
    # accessible via the NPC editor "Open/Create Shared Dialogue" button.
    shared_dir = world_base / "shared"
    if shared_dir.exists():
        shared_dlgs = []
        for path in sorted(shared_dir.glob("*.toml")):
            try:
                data = toml_load(path)
                data["_file"]   = str(path.relative_to(ROOT))
                data["_npc_id"] = path.stem
                shared_dlgs.append(data)
            except Exception:
                pass
        if shared_dlgs:
            world["zones"]["shared"] = {
                "id":         "shared",
                "_is_shared": True,
                "rooms":      [], "items":    [], "npcs":      [],
                "quests":     [], "crafting": [], "companions":[], "styles": [],
                "processes":  [], "files":    [],
                "dialogues":  shared_dlgs,
            }

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


def _clone_entity(src_zone_path: Path, type_: str, source_id: str,
                  new_id: str, file_rel: str,
                  dest_zone_path: Path) -> dict:
    """
    Clone entity `source_id` of `type_` from `src_zone_path` into `dest_zone_path`
    under `new_id`.  Works for both same-zone clones and cross-world copies.
    Returns {"ok": bool, "error": str, "new_id": str}.
    """
    if type_ in ("room", "npc", "item", "style", "process"):
        if not file_rel:
            return {"ok": False, "error": "file required for room/npc/item/style/process", "new_id": ""}
        src_file = ROOT / file_rel
        if not src_file.exists():
            return {"ok": False, "error": f"Source file not found: {file_rel}", "new_id": ""}
        try:
            data = toml_load(src_file)
        except Exception as e:
            return {"ok": False, "error": f"Parse error: {e}", "new_id": ""}

        key = type_  # "room", "npc", "item", "style", "process"
        arr = data.get(key, [])
        src_obj = next((o for o in arr if o.get("id") == source_id), None)
        if src_obj is None:
            return {"ok": False, "error": f"{type_} '{source_id}' not found in source file", "new_id": ""}

        new_obj = copy.deepcopy(src_obj)
        new_obj["id"] = new_id

        if type_ == "process":
            target_file = dest_zone_path / "processes.toml"
        elif type_ == "style":
            target_file = dest_zone_path / "styles" / "styles.toml"
        else:
            target_file = dest_zone_path / f"{type_}s.toml"  # rooms.toml, npcs.toml, items.toml

        target_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            target_data = toml_load(target_file) if target_file.exists() else {}
        except Exception:
            target_data = {}

        # Check for duplicate ID in target
        existing_ids = [o.get("id") for o in target_data.get(key, [])]
        if new_id in existing_ids:
            return {"ok": False, "error": f"{type_} '{new_id}' already exists in target", "new_id": ""}

        target_data.setdefault(key, []).append(new_obj)
        ok, err = _write_file(str(target_file.relative_to(ROOT)), target_data)
        return {"ok": ok, "error": err, "new_id": new_id}

    elif type_ == "quest":
        src_file = src_zone_path / "quests" / f"{source_id}.toml"
        if not src_file.exists():
            return {"ok": False, "error": f"Quest '{source_id}.toml' not found", "new_id": ""}
        try:
            data = copy.deepcopy(toml_load(src_file))
        except Exception as e:
            return {"ok": False, "error": f"Parse error: {e}", "new_id": ""}
        data["id"] = new_id
        dest_dir = dest_zone_path / "quests"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_dir / f"{new_id}.toml"
        if dest_file.exists():
            return {"ok": False, "error": f"Quest '{new_id}' already exists in target", "new_id": ""}
        ok, err = _write_file(str(dest_file.relative_to(ROOT)), data)
        return {"ok": ok, "error": err, "new_id": new_id}

    elif type_ == "dialogue":
        src_file = src_zone_path / "dialogues" / f"{source_id}.toml"
        if not src_file.exists():
            return {"ok": False, "error": f"Dialogue '{source_id}.toml' not found", "new_id": ""}
        try:
            data = copy.deepcopy(toml_load(src_file))
        except Exception as e:
            return {"ok": False, "error": f"Parse error: {e}", "new_id": ""}
        dest_dir = dest_zone_path / "dialogues"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_dir / f"{new_id}.toml"
        if dest_file.exists():
            return {"ok": False, "error": f"Dialogue '{new_id}.toml' already exists in target", "new_id": ""}
        ok, err = _write_file(str(dest_file.relative_to(ROOT)), data)
        return {"ok": ok, "error": err, "new_id": new_id}

    elif type_ == "crafting":
        src_file = src_zone_path / "crafting" / f"{source_id}.toml"
        if not src_file.exists():
            return {"ok": False, "error": f"Crafting '{source_id}.toml' not found", "new_id": ""}
        try:
            data = copy.deepcopy(toml_load(src_file))
        except Exception as e:
            return {"ok": False, "error": f"Parse error: {e}", "new_id": ""}
        # Rewrite npc_id on every commission entry so they reference the new NPC
        for commission in data.get("commission", []):
            if "npc_id" in commission:
                commission["npc_id"] = new_id
        dest_dir = dest_zone_path / "crafting"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_dir / f"{new_id}.toml"
        if dest_file.exists():
            return {"ok": False, "error": f"Crafting '{new_id}.toml' already exists in target", "new_id": ""}
        ok, err = _write_file(str(dest_file.relative_to(ROOT)), data)
        return {"ok": ok, "error": err, "new_id": new_id}

    else:
        return {"ok": False, "error": f"Unsupported type '{type_}'", "new_id": ""}


# ── Markdown → HTML renderer (for /manual endpoint) ──────────────────────────

def _slugify(text: str) -> str:
    """Convert heading text to a stable, URL-safe anchor ID."""
    text = text.lower()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')


def _render_manual_html(md_text: str, title: str = "Manual") -> str:
    """
    Convert a subset of Markdown to a full HTML page.
    Handles: fenced code blocks, tables, ATX headings, horizontal rules,
    unordered/ordered lists, blockquotes, and inline bold/italic/code/links.
    Returns a complete HTML string with embedded CSS and a generated TOC.
    """

    # ── inline processor ─────────────────────────────────────────────────────
    def _inline(text: str) -> str:
        # Escape HTML entities first
        text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        # Inline code (restore angle brackets inside)
        text = re.sub(r'`([^`]+)`',
                      lambda m: f'<code>{m.group(1)}</code>', text)
        # Bold
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        # Italic
        text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
        # Links [text](url)
        text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)',
                      r'<a href="\2">\1</a>', text)
        return text

    # ── slug tracker (handles duplicates) ────────────────────────────────────
    seen_slugs: dict[str, int] = {}
    def _unique_slug(text: str) -> str:
        base = _slugify(text)
        if base not in seen_slugs:
            seen_slugs[base] = 0
            return base
        seen_slugs[base] += 1
        return f"{base}-{seen_slugs[base]}"

    # ── line-by-line state machine ────────────────────────────────────────────
    lines      = md_text.split('\n')
    body_parts: list[str] = []
    toc_items:  list[tuple[int, str, str]] = []  # (level, text, slug)

    state  = 'normal'   # 'normal' | 'code' | 'table' | 'ul' | 'ol' | 'bq' | 'para'
    buf:   list[str] = []

    def flush_buf() -> None:
        if not buf:
            return
        if state == 'para':
            body_parts.append(f'<p>{_inline(" ".join(buf))}</p>')
        elif state == 'ul':
            items = '\n'.join(f'  <li>{_inline(l)}</li>' for l in buf)
            body_parts.append(f'<ul>\n{items}\n</ul>')
        elif state == 'ol':
            items = '\n'.join(f'  <li>{_inline(l)}</li>' for l in buf)
            body_parts.append(f'<ol>\n{items}\n</ol>')
        elif state == 'bq':
            inner = _inline(' '.join(buf))
            body_parts.append(f'<blockquote><p>{inner}</p></blockquote>')
        elif state == 'table':
            rows_html = []
            header_done = False
            for row in buf:
                cells = [c.strip() for c in row.strip('|').split('|')]
                if all(re.match(r'^[-: ]+$', c) for c in cells if c):
                    if not header_done:
                        header_done = True  # separator row consumed
                    continue
                tag = 'th' if not header_done else 'td'
                row_html = ''.join(f'<{tag}>{_inline(c)}</{tag}>' for c in cells)
                rows_html.append(f'<tr>{row_html}</tr>')
            body_parts.append('<table>\n' + '\n'.join(rows_html) + '\n</table>')
        buf.clear()

    def set_state(new_state: str) -> None:
        nonlocal state
        if state != new_state:
            flush_buf()
            state = new_state

    for line in lines:
        # ── code block ───────────────────────────────────────────────────────
        if state == 'code':
            if re.match(r'^\s*```', line):
                body_parts.append('</code></pre>')
                state = 'normal'
            else:
                escaped = line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                body_parts.append(escaped)
            continue

        if re.match(r'^\s*```', line):
            flush_buf(); state = 'normal'
            lang = line.strip()[3:].strip()
            cls  = f' class="language-{lang}"' if lang else ''
            body_parts.append(f'<pre><code{cls}>')
            state = 'code'
            continue

        # ── headings ─────────────────────────────────────────────────────────
        m = re.match(r'^(#{1,6})\s+(.+)$', line)
        if m:
            flush_buf(); state = 'normal'
            level = len(m.group(1))
            text  = m.group(2).strip()
            slug  = _unique_slug(text)
            if level <= 3:
                toc_items.append((level, text, slug))
            body_parts.append(
                f'<h{level} id="{slug}">'
                f'<a class="anchor" href="#{slug}">#</a> {_inline(text)}'
                f'</h{level}>')
            continue

        # ── horizontal rule ──────────────────────────────────────────────────
        if re.match(r'^-{3,}\s*$', line) or re.match(r'^\*{3,}\s*$', line):
            flush_buf(); state = 'normal'
            body_parts.append('<hr>')
            continue

        # ── blank line ───────────────────────────────────────────────────────
        if not line.strip():
            flush_buf(); state = 'normal'
            continue

        # ── table row ────────────────────────────────────────────────────────
        if line.lstrip().startswith('|') and '|' in line:
            if state != 'table':
                flush_buf(); state = 'table'
            buf.append(line)
            continue
        else:
            if state == 'table':
                flush_buf(); state = 'normal'

        # ── unordered list ───────────────────────────────────────────────────
        m = re.match(r'^[ \t]*[-*+]\s+(.+)$', line)
        if m:
            if state != 'ul':
                flush_buf(); state = 'ul'
            buf.append(m.group(1))
            continue

        # ── ordered list ─────────────────────────────────────────────────────
        m = re.match(r'^[ \t]*\d+\.\s+(.+)$', line)
        if m:
            if state != 'ol':
                flush_buf(); state = 'ol'
            buf.append(m.group(1))
            continue

        # ── blockquote ───────────────────────────────────────────────────────
        m = re.match(r'^>\s*(.*)', line)
        if m:
            if state != 'bq':
                flush_buf(); state = 'bq'
            buf.append(m.group(1))
            continue

        # ── paragraph text ───────────────────────────────────────────────────
        if state != 'para':
            flush_buf(); state = 'para'
        buf.append(line)

    flush_buf()

    # ── build TOC HTML ───────────────────────────────────────────────────────
    toc_html_parts = ['<nav id="toc"><div id="toc-inner">',
                      f'<div class="toc-title">{title}</div><ul>']
    for level, text, slug in toc_items:
        indent = 'toc-h3' if level == 3 else ''
        clean  = re.sub(r'<[^>]+>', '', _inline(text))
        toc_html_parts.append(
            f'<li class="{indent}"><a href="#{slug}">{clean}</a></li>')
    toc_html_parts.append('</ul></div></nav>')
    toc_html = '\n'.join(toc_html_parts)

    body_html = '\n'.join(body_parts)

    # ── full page ─────────────────────────────────────────────────────────────
    css = """
:root{--bg:#1a1a1a;--panel:#111;--border:#2a2a2a;--text:#d4d4d4;--dim:#888;
  --amber:#F29E00;--code-bg:#0d0d0d;--table-hdr:#222;}
*{box-sizing:border-box;margin:0;padding:0;}
html{scroll-behavior:smooth;}
body{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;
  font-size:15px;line-height:1.65;display:flex;min-height:100vh;}
#toc{width:240px;min-width:240px;background:var(--panel);border-right:1px solid var(--border);
  position:sticky;top:0;height:100vh;overflow-y:auto;flex-shrink:0;}
#toc-inner{padding:14px 0;}
.toc-title{color:var(--amber);font-weight:700;font-size:13px;
  padding:0 14px 10px;border-bottom:1px solid var(--border);margin-bottom:8px;}
#toc ul{list-style:none;}
#toc li a{display:block;padding:3px 14px;color:var(--dim);text-decoration:none;font-size:12px;}
#toc li a:hover{color:var(--amber);}
#toc .toc-h3 a{padding-left:26px;font-size:11px;}
#content{flex:1;max-width:860px;padding:40px 50px;overflow-x:hidden;}
h1,h2,h3,h4,h5,h6{color:var(--text);margin:1.8em 0 0.6em;}
h1,h2{color:var(--amber);border-bottom:1px solid var(--border);padding-bottom:4px;}
h3{color:#bbb;}
a.anchor{opacity:0;font-size:0.75em;text-decoration:none;color:var(--dim);margin-right:6px;}
h1:hover a.anchor,h2:hover a.anchor,h3:hover a.anchor,
h4:hover a.anchor,h5:hover a.anchor,h6:hover a.anchor{opacity:1;}
p{margin:0.6em 0;}
a{color:var(--amber);}
a:hover{text-decoration:underline;}
code{background:var(--code-bg);color:var(--amber);padding:1px 5px;border-radius:3px;
  font-family:Consolas,'Courier New',monospace;font-size:0.9em;}
pre{background:var(--code-bg);border:1px solid var(--border);border-radius:5px;
  padding:14px 16px;overflow-x:auto;margin:0.8em 0;}
pre code{background:none;padding:0;color:#c8c8c8;}
table{border-collapse:collapse;width:100%;margin:0.8em 0;font-size:14px;}
th,td{border:1px solid var(--border);padding:6px 12px;text-align:left;}
th{background:var(--table-hdr);color:var(--amber);}
tr:nth-child(even) td{background:rgba(255,255,255,.03);}
ul,ol{margin:0.5em 0 0.5em 1.6em;}
li{margin:0.25em 0;}
blockquote{border-left:3px solid var(--amber);margin:0.8em 0;
  padding:8px 14px;background:rgba(242,158,0,.06);color:var(--dim);}
blockquote p{margin:0;}
hr{border:none;border-top:1px solid var(--border);margin:1.6em 0;}
@media(max-width:700px){
  body{flex-direction:column;}
  #toc{width:100%;height:auto;position:static;border-right:none;border-bottom:1px solid var(--border);}
}
@media print{
  body{background:#fff;color:#000;display:block;}
  #toc{display:none;}
  #content{max-width:100%;padding:0;margin:0;}
  h1,h2{color:#000;border-bottom:1px solid #999;}
  h2{page-break-before:always;}
  h2:first-of-type{page-break-before:avoid;}
  code,pre{background:#f5f5f5;color:#000;border-color:#ccc;}
  th{background:#eee;}
  a{color:#000;}
  a.anchor{display:none;}
}
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
{toc_html}
<main id="content">
{body_html}
</main>
</body>
</html>"""


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

        elif path == "/game/worlds":
            from engine.world_config import list_worlds, peek_world_name
            worlds = [
                {"id": w.name, "name": peek_world_name(w)}
                for w in list_worlds(DATA_DIR)
            ]
            self._send_json({"worlds": worlds})

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

        elif path == "/manual":
            md_path = TOOLS_DIR / "WORLD_MANUAL.md"
            try:
                md_text = md_path.read_text(encoding="utf-8")
                html    = _render_manual_html(md_text, "Delve World Manual")
                body    = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", len(body))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self._send_json({"error": f"Could not render manual: {e}"}, 500)

        elif path == "/api/list_zones":
            qs = parse_qs(parsed.query)
            world_id = (qs.get("world_id") or [""])[0].strip()
            if not world_id:
                self._send_json({"error": "world_id required"}, 400)
                return
            world_path = DATA_DIR / world_id
            if not world_path.is_dir():
                self._send_json({"error": f"World '{world_id}' not found"}, 404)
                return
            skip = {"zone_state", "players", "__pycache__"}
            zones = []
            for z in sorted(world_path.iterdir()):
                if not z.is_dir() or z.name in skip:
                    continue
                zone_toml = z / "zone.toml"
                name = z.name
                if zone_toml.exists():
                    try:
                        zdata = toml_load(zone_toml)
                        name = zdata.get("name", z.name)
                    except Exception:
                        pass
                zones.append({"id": z.name, "name": name})
            self._send_json({"zones": zones})

        elif path == "/api/validate":
            # Run the validator and return its output
            import subprocess
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "validate.py")],
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
            dlg_dir   = (DATA_DIR / world_id / "shared") if zone_id == "shared" \
                        else (DATA_DIR / world_id / zone_id / "dialogues")
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
            name = body.get("name", None)
            if name is not None:
                data["name"] = name or zone_id
            description = body.get("description", None)
            if description is not None:
                if description:
                    data["description"] = description
                else:
                    data.pop("description", None)
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
                dlg_path = (DATA_DIR / world_id / "shared" / f"{eid}.toml") if zone_id == "shared" \
                           else (DATA_DIR / world_id / zone_id / "dialogues" / f"{eid}.toml")
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

        elif path == "/api/clone_object":
            world_id  = body.get("world_id", "")
            zone_id   = body.get("zone_id", "")
            type_     = body.get("type", "")
            source_id = body.get("source_id", "").strip()
            new_id    = body.get("new_id", "").strip()
            file_rel  = body.get("file", "")
            if not world_id or not zone_id or not type_ or not source_id or not new_id:
                self._send_json({"ok": False, "error": "world_id, zone_id, type, source_id, new_id required"})
                return
            zone_path = DATA_DIR / world_id / zone_id
            result = _clone_entity(zone_path, type_, source_id, new_id, file_rel, zone_path)
            self._send_json(result)

        elif path == "/api/copy_to_world":
            src_world = body.get("source_world_id", "")
            src_zone  = body.get("source_zone_id", "")
            type_     = body.get("type", "")
            source_id = body.get("source_id", "").strip()
            new_id    = body.get("new_id", "").strip()
            tgt_world = body.get("target_world_id", "")
            tgt_zone  = body.get("target_zone_id", "")
            file_rel  = body.get("file", "")
            if not src_world or not src_zone or not type_ or not source_id or not new_id or not tgt_world or not tgt_zone:
                self._send_json({"ok": False, "error": "source_world_id, source_zone_id, type, source_id, new_id, target_world_id, target_zone_id required"})
                return
            src_zone_path = DATA_DIR / src_world / src_zone
            tgt_zone_path = DATA_DIR / tgt_world / tgt_zone
            if not tgt_zone_path.exists():
                self._send_json({"ok": False, "error": f"Target zone '{tgt_world}/{tgt_zone}' not found"})
                return
            result = _clone_entity(src_zone_path, type_, source_id, new_id, file_rel, tgt_zone_path)
            self._send_json(result)

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

        elif path == "/shutdown":
            self._send_json({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()

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
    print(f"Delve WCT  →  {url}")
    print("Press Ctrl+C to stop.\n")
    if args.browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == "__main__":
    main()




