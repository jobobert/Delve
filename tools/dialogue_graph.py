"""
dialogue_graph.py — Visualise one NPC's dialogue tree as a Graphviz DOT file.

Usage
─────
  # All NPCs  ->  output/dialogue/<npc_id>.dot
  python tools/dialogue_graph.py

  # Single NPC
  python tools/dialogue_graph.py --npc garrison_ghost

  # All NPCs in a zone folder
  python tools/dialogue_graph.py --zone ashwood

  # Custom output path (single NPC only)
  python tools/dialogue_graph.py --npc garrison_ghost --out ghost.dot

  # Auto-render to SVG or PDF (requires graphviz `dot` on PATH)
  python tools/dialogue_graph.py --npc garrison_ghost --render svg

Render manually
───────────────
  dot -Tsvg output/dialogue/dialogue_garrison_ghost.dot -o ghost.svg
  dot -Tpdf output/dialogue/dialogue_garrison_ghost.dot -o ghost.pdf

Node header colours
───────────────────
  Light gray   no script ops (pure narrative)
  Pale yellow  sets a flag
  Pale orange  gives item / gold / xp
  Amber        advances or starts a quest
  Green        completes a quest
  Pink-red     damage or fail op

Node border
───────────
  Solid        reachable from root, no entry condition
  Dashed blue  node has an entry condition (may be skipped)
  Gray         unreachable from root (orphaned node)

Edge colours
────────────
  Black        unconditional response
  Blue         response gated by a condition  (condition shown on label)
  Gray dashed  next="" — ends the conversation
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_tools = Path(__file__).parent
_root  = _tools.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_tools))

from graph_common import (          # noqa: E402
    find_worlds, pick_world, peek_world_name,
    find_dialogue_files, load_dialogue_tree, reachable_nodes,
    node_header_color, wrap_html, h, dot_id, dot_attr,
    format_condition, format_script_ops,
    render_dot,
    C_COND_BG, C_SCRIPT_BG,
)


# ── HTML label builder ────────────────────────────────────────────────────────

_T_OPEN  = ('<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0"'
            ' CELLPADDING="4" BGCOLOR="white">')
_T_CLOSE = "</TABLE>"


def _node_label(node_id: str, node: dict, reachable: set[str]) -> str:
    """Build a DOT HTML label for one dialogue node."""
    script = node.get("script", [])
    cond   = node.get("condition")
    line   = node.get("line", "")
    lines  = node.get("lines", [])
    cycle  = node.get("cycle", False)

    header_color = node_header_color(script)
    is_reachable = node_id in reachable or node_id == "root"

    rows: list[str] = []

    # ── Entry condition row (pale blue) ───────────────────────────────────────
    if cond:
        cond_str = h(format_condition(cond))
        rows.append(
            f'<TR><TD BGCOLOR="{C_COND_BG}" ALIGN="LEFT" BALIGN="LEFT">'
            f'<FONT COLOR="#1565C0" POINT-SIZE="9">entry if: {cond_str}</FONT></TD></TR>'
        )

    # ── Node ID header row ────────────────────────────────────────────────────
    id_color = "#999999" if not is_reachable else "black"
    rows.append(
        f'<TR><TD BGCOLOR="{header_color}">'
        f'<FONT COLOR="{id_color}"><B>{h(node_id)}</B></FONT></TD></TR>'
    )

    # ── Dialogue text rows ────────────────────────────────────────────────────
    if line:
        rows.append(
            f'<TR><TD ALIGN="LEFT" BALIGN="LEFT"><I>{wrap_html(line)}</I></TD></TR>'
        )

    if lines:
        for ln in lines:
            rows.append(
                f'<TR><TD ALIGN="LEFT" BALIGN="LEFT"><I>{wrap_html(ln)}</I></TD></TR>'
            )
        variant_note = "[cycle]" if cycle else "[random pick]"
        rows.append(
            f'<TR><TD ALIGN="LEFT"><FONT COLOR="#999999" POINT-SIZE="8">'
            f'{h(variant_note)}</FONT></TD></TR>'
        )

    if not line and not lines:
        rows.append('<TR><TD ALIGN="LEFT"><FONT COLOR="#AAAAAA"><I>(no text)</I></FONT></TD></TR>')

    # ── Script ops row ────────────────────────────────────────────────────────
    ops = format_script_ops(script)
    if ops:
        ops_html = "<BR/>".join(f"▸ {h(op)}" for op in ops)
        rows.append(
            f'<TR><TD BGCOLOR="{C_SCRIPT_BG}" ALIGN="LEFT" BALIGN="LEFT">'
            f'<FONT COLOR="#444444" POINT-SIZE="9">{ops_html}</FONT></TD></TR>'
        )

    inner = "\n      ".join(rows)
    return f"<\n    {_T_OPEN}\n      {inner}\n    {_T_CLOSE}\n  >"


# ── Graph builder ─────────────────────────────────────────────────────────────

_END_ID  = "__END__"


def build_dialogue_dot(npc_id: str, path: Path, world_name: str = "") -> str:
    """Return a complete DOT digraph string for one NPC's dialogue tree."""
    nodes = load_dialogue_tree(path)
    if not nodes:
        return ""

    reachable = reachable_nodes(nodes)
    lines: list[str] = []
    w = lines.append

    title = f"{npc_id} — dialogue tree"
    if world_name:
        title += f"\n{world_name}"
    w(f'digraph {dot_id(f"dialogue_{npc_id}")} {{')
    w(f'  // Source: {path}')
    w(f'  label={dot_attr(title)}')
    w( '  labelloc=t labeljust=l')
    w( '  rankdir=TB')
    w( '  node [fontname="Courier" fontsize=10 shape=none margin=0]')
    w( '  edge [fontname="Courier" fontsize=9]')
    w("")

    # END sentinel
    w(f'  {dot_id(_END_ID)} [label="END" shape=oval'
      f' style=filled fillcolor="#DDDDDD" fontname="Courier" fontsize=10]')
    w("")

    # Dialogue nodes
    for node_id, node in nodes.items():
        label  = _node_label(node_id, node, reachable)
        cond   = node.get("condition")

        # Border style: dashed-blue if entry condition, gray if unreachable
        if node_id not in reachable and node_id != "root":
            extra = ' color="#BBBBBB"'
        elif cond:
            extra = ' style="dashed" color="#1565C0" penwidth="1.5"'
        else:
            extra = ""

        w(f'  {dot_id(node_id)} [label={label}{extra}]')

    w("")

    # Edges (one per response)
    for node_id, node in nodes.items():
        for resp in node.get("response", []):
            nxt      = resp.get("next", "")
            text     = resp.get("text", "")
            rcond    = resp.get("condition")
            rscript  = resp.get("script", [])

            target = dot_id(nxt) if nxt else dot_id(_END_ID)

            # Edge label: response text, then condition, then response scripts
            label_parts: list[str] = [text]
            if rcond:
                label_parts.append(f"[if: {format_condition(rcond)}]")
            for op_str in format_script_ops(rscript):
                label_parts.append(f"▸ {op_str}")
            edge_label = dot_attr("\n".join(label_parts))

            # Style
            if not nxt:
                # Ends conversation
                attrs = f'style=dashed color="#AAAAAA" fontcolor="#888888" label={edge_label}'
            elif rcond:
                # Condition-gated response
                attrs = f'color=blue fontcolor=blue label={edge_label}'
            else:
                attrs = f'label={edge_label}'

            w(f'  {dot_id(node_id)} -> {target} [{attrs}]')

    # Legend
    w("")
    w('  subgraph cluster_legend {')
    w('    label="Legend" style=rounded color=black')
    w('    fontname="Courier" fontsize=9')
    w('    node [shape=box fontname="Courier" fontsize=9]')
    w('    LL1 [label="narrative"          style=filled fillcolor="#EEEEEE"]')
    w('    LL2 [label="sets flag"          style=filled fillcolor="#FFF9C4"]')
    w('    LL3 [label="gives item/gp/xp"  style=filled fillcolor="#FFE0B2"]')
    w('    LL4 [label="advances quest"    style=filled fillcolor="#FFD54F"]')
    w('    LL5 [label="completes quest"   style=filled fillcolor="#A5D6A7"]')
    w('    LL6 [label="damage / fail"     style=filled fillcolor="#FFCDD2"]')
    w('    LL7 [label="END" shape=oval    style=filled fillcolor="#DDDDDD"]')
    w('    LL8 [label="has entry cond"    style=dashed color="#1565C0"]')
    w('    LL9 [label="unreachable"       color="#BBBBBB" fontcolor="#AAAAAA"]')
    w('    LL1 -> LL2 -> LL3 -> LL4 -> LL5 -> LL6 -> LL7 -> LL8 -> LL9 [style=invis]')
    w('  }')
    w("")
    w("}")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate Graphviz DOT files for NPC dialogue trees.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python tools/dialogue_graph.py\n"
            "  python tools/dialogue_graph.py --npc garrison_ghost --render svg\n"
            "  python tools/dialogue_graph.py --zone ashwood\n"
            "\n"
            "Render manually:\n"
            "  dot -Tsvg output/dialogue/dialogue_garrison_ghost.dot -o ghost.svg\n"
            "  dot -Tpdf output/dialogue/dialogue_garrison_ghost.dot -o ghost.pdf"
        ),
    )
    ap.add_argument("--npc",    metavar="NPC_ID", help="Visualise a single NPC")
    ap.add_argument("--zone",   metavar="ZONE",   help="Only NPCs in this zone folder")
    ap.add_argument("--world",  metavar="WORLD",  help="World folder name (default: first found)")
    ap.add_argument("--out",    metavar="FILE",   help="Output .dot path (single NPC only)")
    ap.add_argument("--render", choices=["svg", "pdf"],
                    help="Auto-render via graphviz dot CLI")
    args = ap.parse_args()

    world_path  = pick_world(args.world)
    world_name  = peek_world_name(world_path)
    all_files   = find_dialogue_files(world_path)

    # Filter
    if args.npc:
        if args.npc not in all_files:
            print(f"NPC '{args.npc}' not found.  Available: {', '.join(sorted(all_files))}",
                  file=sys.stderr)
            sys.exit(1)
        files = {args.npc: all_files[args.npc]}
    elif args.zone:
        files = {k: v for k, v in all_files.items()
                 if v.parent.parent.name == args.zone}
        if not files:
            print(f"No dialogue files found in zone '{args.zone}'.", file=sys.stderr)
            sys.exit(1)
    else:
        files = all_files

    # Output directory
    if args.out:
        out_dir = Path(args.out).parent
        out_dir.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = Path("output") / "dialogue"
        out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Writing {len(files)} dialogue graph(s) -> {out_dir}/")
    for npc_id, path in sorted(files.items()):
        dot_str = build_dialogue_dot(npc_id, path, world_name)
        if not dot_str:
            print(f"  {npc_id}: empty tree, skipped")
            continue

        out_path = Path(args.out) if (args.out and len(files) == 1) \
                   else out_dir / f"dialogue_{npc_id}.dot"

        out_path.write_text(dot_str, encoding="utf-8")
        print(f"  {npc_id} -> {out_path}")

        if args.render:
            render_dot(out_path, args.render)


if __name__ == "__main__":
    main()
