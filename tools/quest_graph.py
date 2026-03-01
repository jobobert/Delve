"""
quest_graph.py — Visualise a quest's step flow as a Graphviz DOT file.

For each quest the graph shows:

  • START oval    — named with the quest's giver NPC
  • Step boxes    — index, objective text, hint, and completion_flag (if any)
  • COMPLETE oval — with all reward types listed
  • Transition edges — which NPC dialogue node triggers each step advance /
                       complete, with flags required and flags set
  • WARNING edges — dashed red when no dialogue trigger is found for a step
                    (i.e. a hole: nothing in the dialogue advances to this step)

Usage
─────
  # All quests  ->  output/quest/<quest_id>.dot
  python tools/quest_graph.py

  # Single quest
  python tools/quest_graph.py --quest ashwood_contract

  # Custom output path
  python tools/quest_graph.py --quest ashwood_contract --out ashwood.dot

  # Auto-render to SVG or PDF (requires graphviz `dot` on PATH)
  python tools/quest_graph.py --quest ashwood_contract --render svg

Render manually
───────────────
  dot -Tsvg output/quest/quest_ashwood_contract.dot -o ashwood.svg
  dot -Tpdf output/quest/quest_ashwood_contract.dot -o ashwood.pdf

How to read warnings
────────────────────
  A dashed red edge means the graph found no advance_quest / complete_quest
  op in any dialogue file that targets this step.  The transition may exist
  in a kill_script, on_enter, or other non-dialogue location not scanned here
  — or it may genuinely be missing.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

_tools = Path(__file__).parent
_root  = _tools.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_tools))

from graph_common import (          # noqa: E402
    find_dialogue_files, find_quest_files,
    load_dialogue_tree, load_quest,
    h, wrap_html, dot_id, dot_attr,
    format_condition, format_script_ops,
    render_dot,
    C_COND_BG,
)


# ── Transition data structure ─────────────────────────────────────────────────

@dataclass
class Transition:
    """One point in a dialogue file where a quest changes state."""
    npc_id:         str
    node_id:        str
    # to_step=None  -> complete_quest
    to_step:        int | None
    flags_required: list[str] = field(default_factory=list)
    flags_set:      list[str] = field(default_factory=list)
    other_ops:      list[str] = field(default_factory=list)


def _flags_required(cond: dict | None) -> list[str]:
    if not cond:
        return []
    parts: list[str] = []
    if "flag"            in cond: parts.append(f"flag:{cond['flag']}")
    if "not_flag"        in cond: parts.append(f"not:{cond['not_flag']}")
    if "item"            in cond: parts.append(f"has_item:{cond['item']}")
    if "level_gte"       in cond: parts.append(f"level≥{cond['level_gte']}")
    if "quest"           in cond: parts.append(f"quest {cond['quest']} step {cond.get('step','?')}")
    if "quest_complete"  in cond: parts.append(f"quest_done:{cond['quest_complete']}")
    if "gold"            in cond: parts.append(f"gold≥{cond['gold']}")
    return parts


def _split_script(ops: list) -> tuple[list[str], list[str]]:
    """Return (flags_set, other_formatted_ops) — excludes advance/complete_quest ops."""
    flags_set:  list[str] = []
    other_ops:  list[str] = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        name = op.get("op", "")
        if name == "set_flag":
            flags_set.append(op.get("flag", "?"))
        elif name not in ("advance_quest", "complete_quest"):
            other_ops.extend(format_script_ops([op]))
    return flags_set, other_ops


# ── Cross-reference builder ───────────────────────────────────────────────────

def build_transitions(quest_id: str) -> list[Transition]:
    """
    Scan all dialogue files and collect every point where quest_id is
    advanced or completed.  Checks both node-level and response-level scripts.
    """
    transitions: list[Transition] = []
    all_files = find_dialogue_files()

    for npc_id, path in sorted(all_files.items()):
        try:
            nodes = load_dialogue_tree(path)
        except Exception:
            continue

        for node_id, node in nodes.items():
            node_cond   = node.get("condition")
            node_script = node.get("script", [])

            # ── Node-level script ─────────────────────────────────────────────
            for op in node_script:
                if not isinstance(op, dict):
                    continue
                _record(op, quest_id, npc_id, node_id,
                        [node_cond], node_script, transitions)

            # ── Response-level scripts ────────────────────────────────────────
            for resp in node.get("response", []):
                resp_cond   = resp.get("condition")
                resp_script = resp.get("script", [])
                for op in resp_script:
                    if not isinstance(op, dict):
                        continue
                    _record(op, quest_id, npc_id, node_id,
                            [node_cond, resp_cond], resp_script, transitions)

    return transitions


def _record(op: dict, quest_id: str, npc_id: str, node_id: str,
            cond_list: list[dict | None], full_script: list,
            out: list[Transition]) -> None:
    """Append a Transition if op is an advance/complete for quest_id."""
    name = op.get("op", "")
    if name == "advance_quest" and op.get("quest_id") == quest_id:
        to_step = int(op.get("step", 0))
    elif name == "complete_quest" and op.get("quest_id") == quest_id:
        to_step = None
    else:
        return

    flags_req: list[str] = []
    for cond in cond_list:
        flags_req.extend(_flags_required(cond))

    flags_set, other = _split_script(full_script)
    out.append(Transition(
        npc_id=npc_id,
        node_id=node_id,
        to_step=to_step,
        flags_required=flags_req,
        flags_set=flags_set,
        other_ops=other,
    ))


# ── Edge label builder ────────────────────────────────────────────────────────

def _transition_label(t: Transition) -> str:
    """Return a multi-line edge label describing a transition."""
    parts = [f"{t.npc_id} / {t.node_id}"]
    if t.flags_required:
        parts.append("req: " + ", ".join(t.flags_required))
    if t.flags_set:
        parts.append("sets: " + ", ".join(t.flags_set))
    parts.extend(t.other_ops)
    return "\n".join(parts)


# ── Graph builder ─────────────────────────────────────────────────────────────

_T_OPEN  = ('<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0"'
            ' CELLPADDING="4" BGCOLOR="white">')
_T_CLOSE = "</TABLE>"


def build_quest_dot(quest_id: str, quest: dict) -> str:
    title   = quest.get("title", quest_id)
    summary = quest.get("summary", "")
    giver   = quest.get("giver", "")
    steps   = {s["index"]: s for s in quest.get("step", [])}
    rewards = quest.get("reward", [])

    # Reward summary line
    reward_parts: list[str] = []
    for r in rewards:
        rt = r.get("type", "")
        if   rt == "gold": reward_parts.append(f"{r.get('amount','?')}g")
        elif rt == "xp":   reward_parts.append(f"{r.get('amount','?')} xp")
        elif rt == "item":  reward_parts.append(r.get("item_id", "?"))
    reward_line = ", ".join(reward_parts) if reward_parts else "no rewards"

    # Collect transitions
    transitions  = build_transitions(quest_id)
    by_to: dict[int | None, list[Transition]] = {}
    for t in transitions:
        by_to.setdefault(t.to_step, []).append(t)

    step_nums = sorted(steps.keys())

    lines: list[str] = []
    w = lines.append

    w(f'digraph {dot_id(f"quest_{quest_id}")} {{')
    title_line = f"{title}\n{summary}"
    w(f'  label={dot_attr(title_line)}')
    w( '  labelloc=t labeljust=l')
    w( '  rankdir=TB')
    w( '  node [fontname="Courier" fontsize=10]')
    w( '  edge [fontname="Courier" fontsize=9]')
    w("")

    # START node
    start_label = ("START\n(" + h(giver) + ")") if giver else "START"
    w(f'  __START__ [shape=oval style=filled fillcolor="#C8E6C9"'
      f' label={dot_attr(start_label)}]')

    # COMPLETE node
    w(f'  __COMPLETE__ [shape=oval style=filled fillcolor="#A5D6A7"'
      f' label={dot_attr("COMPLETE" + chr(10) + "Rewards: " + reward_line)}]')
    w("")

    # Step nodes
    for idx in step_nums:
        s     = steps[idx]
        obj   = s.get("objective", "")
        hint  = s.get("hint", "")
        cflag = s.get("completion_flag", "")

        rows: list[str] = []
        rows.append(f'<TR><TD BGCOLOR="{C_COND_BG}"><B>Step {idx}</B></TD></TR>')
        rows.append(
            f'<TR><TD ALIGN="LEFT" BALIGN="LEFT">{wrap_html(obj, 52)}</TD></TR>'
        )
        if hint:
            rows.append(
                f'<TR><TD ALIGN="LEFT" BALIGN="LEFT">'
                f'<FONT COLOR="#666666" POINT-SIZE="9">Hint: {wrap_html(hint, 52)}</FONT>'
                f'</TD></TR>'
            )
        if cflag:
            rows.append(
                f'<TR><TD ALIGN="LEFT" BGCOLOR="#FFF9C4">'
                f'<FONT COLOR="#555555" POINT-SIZE="9">sets: {h(cflag)}</FONT>'
                f'</TD></TR>'
            )

        inner = "\n      ".join(rows)
        tbl   = f"<\n    {_T_OPEN}\n      {inner}\n    {_T_CLOSE}\n  >"
        w(f'  {dot_id(f"__step_{idx}__")} [shape=none margin=0 label={tbl}]')

    w("")

    # ── Edges ────────────────────────────────────────────────────────────────

    def _src(step_num: int) -> str:
        """Source node for the edge arriving AT step_num."""
        idx = step_nums.index(step_num)
        if idx == 0:
            return "__START__"
        prev = step_nums[idx - 1]
        return f"__step_{prev}__"

    def _emit_edges(src: str, dst: str,
                    found: list[Transition], is_complete: bool = False) -> None:
        if found:
            color = "darkgreen" if is_complete else "#1565C0"
            for t in found:
                w(f'  {dot_id(src)} -> {dot_id(dst)}'
                  f' [label={dot_attr(_transition_label(t))} color={dot_attr(color)}]')
        else:
            note = "⚠ no complete_quest trigger found" if is_complete \
                   else "⚠ no advance_quest trigger found"
            w(f'  {dot_id(src)} -> {dot_id(dst)}'
              f' [label={dot_attr(note)} style=dashed color=red fontcolor=red]')

    # Step advancement edges
    for step_num in step_nums:
        src = _src(step_num)
        dst = f"__step_{step_num}__"
        _emit_edges(src, dst, by_to.get(step_num, []))

    # Completion edge (from last step)
    if step_nums:
        last = step_nums[-1]
        _emit_edges(f"__step_{last}__", "__COMPLETE__",
                    by_to.get(None, []), is_complete=True)
    else:
        # Quest with no steps — just start -> complete
        _emit_edges("__START__", "__COMPLETE__",
                    by_to.get(None, []), is_complete=True)

    w("")
    w("}")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate Graphviz DOT files for quest flow.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python tools/quest_graph.py\n"
            "  python tools/quest_graph.py --quest ashwood_contract --render svg\n"
            "\n"
            "Render manually:\n"
            "  dot -Tsvg output/quest/quest_ashwood_contract.dot -o ashwood.svg\n"
            "  dot -Tpdf output/quest/quest_ashwood_contract.dot -o ashwood.pdf"
        ),
    )
    ap.add_argument("--quest",  metavar="QUEST_ID", help="Visualise a single quest")
    ap.add_argument("--out",    metavar="FILE",      help="Output .dot path (single quest only)")
    ap.add_argument("--render", choices=["svg", "pdf"],
                    help="Auto-render via graphviz dot CLI")
    args = ap.parse_args()

    quest_files = find_quest_files()
    if not quest_files:
        print("No quest files found.", file=sys.stderr)
        sys.exit(1)

    if args.quest:
        if args.quest not in quest_files:
            print(f"Quest '{args.quest}' not found.  Available: {', '.join(sorted(quest_files))}",
                  file=sys.stderr)
            sys.exit(1)
        targets = {args.quest: quest_files[args.quest]}
    else:
        targets = quest_files

    if args.out:
        out_dir = Path(args.out).parent
        out_dir.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = Path("output") / "quest"
        out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Writing {len(targets)} quest graph(s) -> {out_dir}/")
    for quest_id, path in sorted(targets.items()):
        try:
            quest = load_quest(path)
        except Exception as e:
            print(f"  {quest_id}: load error — {e}", file=sys.stderr)
            continue

        dot_str = build_quest_dot(quest_id, quest)
        out_path = Path(args.out) if (args.out and len(targets) == 1) \
                   else out_dir / f"quest_{quest_id}.dot"

        out_path.write_text(dot_str, encoding="utf-8")
        print(f"  {quest_id} -> {out_path}")

        if args.render:
            render_dot(out_path, args.render)


if __name__ == "__main__":
    main()
