"""
graph_common.py — Shared utilities for dialogue_graph.py and quest_graph.py.

Import pattern in sibling tools:
    _tools = Path(__file__).parent
    _root  = _tools.parent
    sys.path.insert(0, str(_root))    # engine.*
    sys.path.insert(0, str(_tools))   # graph_common
    from graph_common import ...
"""

from __future__ import annotations

import html
import subprocess
import sys
import textwrap
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────

_TOOLS = Path(__file__).parent
_ROOT  = _TOOLS.parent
sys.path.insert(0, str(_ROOT))

from engine.toml_io import load as _toml_load  # noqa: E402 (after sys.path)

_SKIP_DIRS = {"zone_state", "players"}


def data_root() -> Path:
    return _ROOT / "data"


# ── File discovery ────────────────────────────────────────────────────────────

def find_dialogue_files(root: Path | None = None) -> dict[str, Path]:
    """Return {npc_id: path} for every dialogue TOML found under data/."""
    root = root or data_root()
    result: dict[str, Path] = {}
    for zone in sorted(root.iterdir()):
        if not zone.is_dir() or zone.name in _SKIP_DIRS:
            continue
        d = zone / "dialogues"
        if not d.exists():
            continue
        for f in sorted(d.glob("*.toml")):
            if f.stem not in result:
                result[f.stem] = f
    return result


def find_quest_files(root: Path | None = None) -> dict[str, Path]:
    """Return {quest_id: path} keyed by the file stem."""
    root = root or data_root()
    result: dict[str, Path] = {}
    for zone in sorted(root.iterdir()):
        if not zone.is_dir() or zone.name in _SKIP_DIRS:
            continue
        d = zone / "quests"
        if not d.exists():
            continue
        for f in sorted(d.glob("*.toml")):
            if f.stem not in result:
                result[f.stem] = f
    return result


def load_dialogue_tree(path: Path) -> dict[str, dict]:
    """Load a dialogue TOML into {node_id: node_dict} with responses attached."""
    raw   = _toml_load(path)
    nodes = {n["id"]: dict(n) for n in raw.get("node", []) if n.get("id")}
    for resp in raw.get("response", []):
        pid = resp.get("node", "")
        if pid in nodes:
            nodes[pid].setdefault("response", []).append(resp)
    return nodes


def load_quest(path: Path) -> dict:
    return _toml_load(path)


# ── Reachability ──────────────────────────────────────────────────────────────

def reachable_nodes(nodes: dict[str, dict]) -> set[str]:
    """BFS from 'root'; returns set of reachable node ids."""
    seen:  set[str] = set()
    stack: list[str] = ["root"]
    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        for resp in nodes.get(nid, {}).get("response", []):
            nxt = resp.get("next", "")
            if nxt and nxt in nodes and nxt not in seen:
                stack.append(nxt)
    return seen


# ── Color constants ───────────────────────────────────────────────────────────

C_NEUTRAL     = "#EEEEEE"   # no script ops
C_FLAG        = "#FFF9C4"   # sets a flag (pale yellow)
C_ITEM        = "#FFE0B2"   # gives item / gold / xp (pale orange)
C_QUEST_ADV   = "#FFD54F"   # advances or starts a quest (amber)
C_QUEST_DONE  = "#A5D6A7"   # completes a quest (green)
C_DAMAGE      = "#FFCDD2"   # damage / fail (pink-red)
C_COND_BG     = "#E3F2FD"   # condition row background (pale blue)
C_SCRIPT_BG   = "#F5F5F5"   # script ops row background (light gray)

_QUEST_ADVANCING_OPS = {"advance_quest"}
_QUEST_DONE_OPS      = {"complete_quest"}
_ITEM_OPS            = {"give_item", "take_item", "spawn_item", "give_gold", "give_xp"}
_DAMAGE_OPS          = {"damage", "fail"}


def node_header_color(script: list) -> str:
    """Return the header cell color based on most significant script op."""
    op_names = {op.get("op", "") for op in script if isinstance(op, dict)}
    if op_names & _QUEST_DONE_OPS:
        return C_QUEST_DONE
    if op_names & _QUEST_ADVANCING_OPS:
        return C_QUEST_ADV
    if op_names & _ITEM_OPS:
        return C_ITEM
    if op_names & _DAMAGE_OPS:
        return C_DAMAGE
    if "set_flag" in op_names or "clear_flag" in op_names:
        return C_FLAG
    return C_NEUTRAL


# ── Text / HTML helpers ───────────────────────────────────────────────────────

def h(text: str) -> str:
    """HTML-escape for use in DOT HTML labels."""
    return html.escape(str(text), quote=False)


def wrap_html(text: str, width: int = 55) -> str:
    """Word-wrap text and join lines with <BR/> for DOT HTML labels."""
    wrapped = textwrap.fill(str(text), width=width, break_long_words=True)
    return "<BR/>".join(h(line) for line in wrapped.splitlines())


def dot_id(s: str) -> str:
    """Return a quoted DOT node identifier (always safe)."""
    return '"' + s.replace('"', '\\"') + '"'


def dot_attr(s: str) -> str:
    """Quote an arbitrary string for use as a DOT attribute value."""
    escaped = str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


# ── Condition formatting ──────────────────────────────────────────────────────

def format_condition(cond: dict | None) -> str:
    """Return a compact, human-readable condition string."""
    if not cond:
        return ""
    parts: list[str] = []
    for k, v in cond.items():
        if   k == "flag":           parts.append(f"flag: {v}")
        elif k == "not_flag":       parts.append(f"not: {v}")
        elif k == "item":           parts.append(f"has_item: {v}")
        elif k == "quest":          parts.append(f"quest {v} step {cond.get('step', '?')}")
        elif k == "not_quest":      parts.append(f"not_quest: {v}")
        elif k == "quest_complete": parts.append(f"quest_done: {v}")
        elif k == "level_gte":      parts.append(f"level≥{v}")
        elif k == "skill":          parts.append(f"skill {v}≥{cond.get('min', '?')}")
        elif k == "gold":           parts.append(f"gold≥{v}")
        elif k == "no_companion":   parts.append("no companion")
        elif k in ("prestige_min", "min_prestige"): parts.append(f"prestige≥{v}")
        elif k in ("prestige_max", "max_prestige"): parts.append(f"prestige≤{v}")
        elif k == "affinity":       parts.append(f"affinity: {v}")
        elif k == "no_affinity":    parts.append(f"no_affinity: {v}")
        elif k == "step":           pass   # consumed by "quest" branch
    return ", ".join(parts)


# ── Script op formatting ──────────────────────────────────────────────────────

def format_script_op(op: dict) -> str:
    """Return a compact one-line description of a single script op."""
    if not isinstance(op, dict):
        return str(op)
    name = op.get("op", "?")
    if name == "set_flag":        return f"set_flag: {op.get('flag', '?')}"
    if name == "clear_flag":      return f"clear_flag: {op.get('flag', '?')}"
    if name == "give_item":       return f"give_item: {op.get('item_id', '?')}"
    if name == "take_item":       return f"take_item: {op.get('item_id', '?')}"
    if name == "spawn_item":      return f"spawn_item: {op.get('item_id', '?')}"
    if name == "give_gold":       return f"give_gold: {op.get('amount', '?')}"
    if name == "give_xp":         return f"give_xp: {op.get('amount', '?')}"
    if name == "heal":            return f"heal: {op.get('amount', '?')}"
    if name == "damage":          return f"damage: {op.get('amount', '?')}"
    if name == "advance_quest":   return f"advance_quest: {op.get('quest_id','?')} -> step {op.get('step','?')}"
    if name == "complete_quest":  return f"complete_quest: {op.get('quest_id','?')}"
    if name == "fail":            return "fail (abort script)"
    if name == "teleport_player": return f"teleport: {op.get('room_id','?')}"
    if name == "skill_grow":      return f"skill_grow: {op.get('skill','?')} +{op.get('amount','?')}"
    if name == "prestige":        return f"prestige: {op.get('amount', 0):+}"
    if name == "give_companion":  return f"give_companion: {op.get('companion_id','?')}"
    if name == "dismiss_companion": return "dismiss_companion"
    if name == "journal_entry":   return f"journal: \"{op.get('title','')}\""
    if name == "say":             txt = op.get("text",""); return f"say: \"{txt[:35]}{'…' if len(txt)>35 else ''}\""
    if name == "message":         txt = op.get("text",""); return f"msg: \"{txt[:35]}{'…' if len(txt)>35 else ''}\""
    if name in ("if_flag", "if", "if_not_flag", "if_skill", "if_prestige",
                "if_affinity", "if_combat_round", "if_npc_hp"):
        return f"{name}: (branch)"
    return name


def format_script_ops(ops: list) -> list[str]:
    """Return formatted strings for all op dicts in a script list."""
    return [format_script_op(op) for op in ops if isinstance(op, dict)]


# ── DOT rendering ─────────────────────────────────────────────────────────────

def render_dot(dot_path: Path, fmt: str) -> bool:
    """
    Invoke the graphviz `dot` CLI to render dot_path to dot_path.<fmt>.
    Returns True on success. Prints a message and returns False on failure.
    """
    out = dot_path.with_suffix(f".{fmt}")
    try:
        result = subprocess.run(
            ["dot", f"-T{fmt}", str(dot_path), "-o", str(out)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"    rendered -> {out}")
            return True
        print(f"    dot error: {result.stderr.strip()}", file=sys.stderr)
        return False
    except FileNotFoundError:
        print("    (dot not found on PATH — skipping render)", file=sys.stderr)
        return False
