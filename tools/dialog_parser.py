from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from graphviz import Digraph
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from engine.toml_io import load as toml_load



# ------------------------------------------------------------
# Quest detection tuned to your actual TOML patterns
# ------------------------------------------------------------

def _flag_is_questy(flag: str | None) -> bool:
    if not flag:
        return False
    return "quest" in flag.lower()


def is_quest_condition(cond: dict | None) -> bool:
    if not cond:
        return False
    if "flag" in cond and _flag_is_questy(cond["flag"]):
        return True
    if "not_flag" in cond and _flag_is_questy(cond["not_flag"]):
        return True
    if "quest" in cond or "quest_complete" in cond or "not_quest" in cond:
        return True
    return False


def is_quest_script(script_list: list[Any] | None) -> bool:
    if not script_list:
        return False
    for op in script_list:
        if not isinstance(op, dict):
            continue
        if op.get("op") == "advance_quest":
            return True
        if "quest_id" in op:
            return True
        if _flag_is_questy(op.get("flag", "")):
            return True
    return False


def response_is_questy(resp: dict) -> bool:
    return (
        is_quest_condition(resp.get("condition"))
        or is_quest_script(resp.get("script", []))
    )


# ------------------------------------------------------------
# Load a single dialogue file like your engine
# ------------------------------------------------------------

def load_dialog_tree(path: Path) -> dict[str, dict]:
    raw = toml_load(path)
    nodes = {n["id"]: dict(n) for n in raw.get("node", []) if n.get("id")}
    for resp in raw.get("response", []):
        parent = resp.get("node")
        if parent in nodes:
            nodes[parent].setdefault("response", []).append(resp)
    return nodes


# ------------------------------------------------------------
# Scan folder tree for dialogues/<npc>.toml
# ------------------------------------------------------------

def find_dialogue_files(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for p in root.rglob("*"):
        if p.is_dir() and p.name == "dialogues":
            for f in p.glob("*.toml"):
                npc_id = f.stem
                result[npc_id] = f
    return result


# ------------------------------------------------------------
# Reachability from root (for styling only)
# ------------------------------------------------------------

def reachable_from_root(nodes: dict[str, dict]) -> set[str]:
    if "root" not in nodes:
        return set()

    seen: set[str] = set()
    stack = ["root"]

    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        node = nodes.get(nid, {})
        for resp in node.get("response", []):
            next_id = resp.get("next", "")
            if next_id and next_id in nodes and next_id not in seen:
                stack.append(next_id)

    return seen


# ------------------------------------------------------------
# Extract quest flags & quest ids from a file
# ------------------------------------------------------------

def extract_quest_markers(nodes: dict[str, dict]) -> dict[str, set[str]]:
    markers = {
        "flags_set": set(),
        "flags_checked": set(),
        "quest_ids": set(),
    }

    for node in nodes.values():
        cond = node.get("condition")
        if cond:
            if "flag" in cond and _flag_is_questy(cond["flag"]):
                markers["flags_checked"].add(cond["flag"])
            if "not_flag" in cond and _flag_is_questy(cond["not_flag"]):
                markers["flags_checked"].add(cond["not_flag"])
            if "quest" in cond:
                markers["quest_ids"].add(cond["quest"])
            if "quest_complete" in cond:
                markers["quest_ids"].add(cond["quest_complete"])

        script = node.get("script", [])
        for op in script:
            if not isinstance(op, dict):
                continue
            if op.get("op") == "advance_quest":
                markers["quest_ids"].add(op.get("quest_id"))
            if "quest_id" in op:
                markers["quest_ids"].add(op["quest_id"])
            if "flag" in op and _flag_is_questy(op["flag"]):
                markers["flags_set"].add(op["flag"])

        for resp in node.get("response", []):
            cond = resp.get("condition")
            if cond:
                if "flag" in cond and _flag_is_questy(cond["flag"]):
                    markers["flags_checked"].add(cond["flag"])
                if "not_flag" in cond and _flag_is_questy(cond["not_flag"]):
                    markers["flags_checked"].add(cond["not_flag"])
                if "quest" in cond:
                    markers["quest_ids"].add(cond["quest"])
                if "quest_complete" in cond:
                    markers["quest_ids"].add(cond["quest_complete"])

            script = resp.get("script", [])
            for op in script:
                if not isinstance(op, dict):
                    continue
                if op.get("op") == "advance_quest":
                    markers["quest_ids"].add(op.get("quest_id"))
                if "quest_id" in op:
                    markers["quest_ids"].add(op["quest_id"])
                if "flag" in op and _flag_is_questy(op["flag"]):
                    markers["flags_set"].add(op["flag"])

    return markers


def truncate_text(text, max_length):
    """
    Truncates a string to a maximum length and adds an ellipsis 
    if truncation occurs.
    """
    if len(text) > max_length:
        # Slice the string and append the ellipsis
        return text[:max_length] + "..."
    return text

# ------------------------------------------------------------
# Build global graph with clusters
# ------------------------------------------------------------

def build_global_graph(dialogue_files: dict[str, Path],
                       npc_filter: set[str] | None,
                       quest_filter: str | None,
                       quest_only: bool,
                       no_quests: bool) -> Digraph:

    dot = Digraph("WorldDialogue", format="dot")
    dot.attr(rankdir="LR")
    #dot.attr(layout="fdp")

    all_nodes: dict[str, dict[str, dict]] = {}
    all_markers: dict[str, dict[str, set[str]]] = {}

    for npc_id, path in dialogue_files.items():
        if npc_filter and npc_id not in npc_filter:
            continue

        nodes = load_dialog_tree(path)
        markers = extract_quest_markers(nodes)

        if quest_filter and quest_filter not in markers["quest_ids"]:
            continue

        all_nodes[npc_id] = nodes
        all_markers[npc_id] = markers

    npc_hubs: dict[str, str] = {}

    for npc_id, nodes in all_nodes.items():
        hub_id = f"{npc_id}_HUB"
        npc_hubs[npc_id] = hub_id

        reachable = reachable_from_root(nodes)

        with dot.subgraph(name=f"cluster_{npc_id}") as c:
            # Border only, no filled background
            c.attr(label=npc_id, style="rounded", color="gray")

            c.node(hub_id, shape="point", label="", width="0.1")
            end_id = f"{npc_id}_END"
            c.node(end_id, shape="egg", style="filled", fillcolor="lightgray")

            for node_id, node in nodes.items():
                full_id = f"{npc_id}_{node_id}"

                # Build label from line / lines as bullets (Option D)
                line = node.get("line")
                lines = node.get("lines") or []
                all_lines: list[str] = []
                if line:
                    all_lines.append(truncate_text(line, 10))
                all_lines.extend(lines)

                if all_lines:
                    bullet_lines = "\n".join(f"• {l}" for l in all_lines)
                    text = bullet_lines
                else:
                    text = ""

                cond = node.get("condition")
                script = node.get("script", [])
                resps = node.get("response", [])

                quest_related = (
                    is_quest_condition(cond)
                    or is_quest_script(script)
                    or any(response_is_questy(r) for r in resps)
                )

                is_reachable = node_id in reachable or node_id == "root"

                if quest_only and not quest_related:
                    continue

                # Style selection
                node_kwargs: dict[str, str] = {"shape": "box"}

                if not is_reachable:
                    # Unreachable nodes: visually distinct
                    node_kwargs["style"] = "dashed"
                    node_kwargs["color"] = "gray"
                    node_kwargs["fontcolor"] = "gray"
                elif not no_quests and quest_related:
                    node_kwargs["style"] = "filled"
                    node_kwargs["fillcolor"] = "gold"

                c.node(full_id, label=f"{node_id}\n{text}", **node_kwargs)

                if node_id == "root":
                    c.edge(hub_id, full_id, style="dotted", color="gray")

                for resp in resps:
                    next_id = resp.get("next", "")
                    label = resp.get("text", "")
                    edge_attrs: dict[str, str] = {}

                    is_q = response_is_questy(resp)

                    if quest_only and not is_q:
                        continue

                    if not no_quests and is_q:
                        edge_attrs["color"] = "red"
                        edge_attrs["penwidth"] = "2"

                    if not next_id:
                        c.edge(full_id, end_id, label=label, **edge_attrs)
                    else:
                        target = f"{npc_id}_{next_id}"
                        if next_id not in nodes:
                            c.node(target, shape="box", style="dashed")
                        c.edge(full_id, target, label=label, **edge_attrs)

    # Cross‑NPC quest links
    if not no_quests:
        for npc_a in all_nodes:
            for npc_b in all_nodes:
                if npc_a == npc_b:
                    continue

                a = all_markers[npc_a]
                b = all_markers[npc_b]

                shared_flags = a["flags_set"] & b["flags_checked"]
                for flag in shared_flags:
                    dot.edge(npc_hubs[npc_a], npc_hubs[npc_b],
                             label=f"flag: {flag}", color="blue", style="dashed")

                shared_quests = a["quest_ids"] & b["quest_ids"]
                for q in shared_quests:
                    dot.edge(npc_hubs[npc_a], npc_hubs[npc_b],
                             label=f"quest: {q}", color="purple", penwidth="2")

    # Legend
    with dot.subgraph(name="cluster_legend") as c:
        c.attr(label="Legend", style="rounded", color="black")
        c.node("LEGEND_NORMAL", "Normal node", shape="box")
        c.node("LEGEND_QUEST", "Quest-related node", shape="box",
               style="filled", fillcolor="gold")
        c.node("LEGEND_UNREACH", "Unreachable from root", shape="box",
               style="dashed", color="gray", fontcolor="gray")
        c.node("LEGEND_END", "Conversation end", shape="egg",
               style="filled", fillcolor="lightgray")
        c.node("LEGEND_QEDGE", "Quest response edge", shape="plaintext")
        c.node("LEGEND_FLAG", "Cross-NPC flag link", shape="plaintext")
        c.node("LEGEND_QUESTLINK", "Cross-NPC quest link", shape="plaintext")

        c.edge("LEGEND_NORMAL", "LEGEND_QUEST", label="quest highlight")
        c.edge("LEGEND_NORMAL", "LEGEND_UNREACH", style="dashed", color="gray")
        c.edge("LEGEND_NORMAL", "LEGEND_END", label="next = \"\"")
        c.edge("LEGEND_QEDGE", "LEGEND_NORMAL", color="red", penwidth="2",
               label="quest response")
        c.edge("LEGEND_FLAG", "LEGEND_NORMAL", color="blue", style="dashed",
               label="flag link")
        c.edge("LEGEND_QUESTLINK", "LEGEND_NORMAL", color="purple",
               penwidth="2", label="quest id link")

    return dot


# ------------------------------------------------------------
# CLI
# ------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate global dialogue graph.")
    parser.add_argument("--npc", nargs="*", help="Only include specific NPC IDs")
    parser.add_argument("--quest", help="Only include NPCs involved in this quest ID")
    parser.add_argument("--quest-only", action="store_true",
                        help="Include only quest-related nodes and edges")
    parser.add_argument("--no-quests", action="store_true",
                        help="Hide all quest highlighting and quest links")
    parser.add_argument("--out", default="world_dialogue.dot",
                        help="Output DOT filename")

    args = parser.parse_args()

    root = Path.cwd()
    files = find_dialogue_files(root)

    npc_filter = set(args.npc) if args.npc else None

    graph = build_global_graph(
        dialogue_files=files,
        npc_filter=npc_filter,
        quest_filter=args.quest,
        quest_only=args.quest_only,
        no_quests=args.no_quests,
    )

    graph.save(args.out)
    print(f"Wrote {args.out} (root={root})")


if __name__ == "__main__":
    main()
