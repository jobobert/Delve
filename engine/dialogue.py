"""
dialogue.py — Branching dialogue system for Delve.

Dialogue trees live in data/dialogues/<npc_id>.toml. If a file exists, it is
used; otherwise the NPC's plain `dialogue` string is shown and the conversation
ends. Trees are cached in memory after first load; call reload_tree() or
reload_all() after editing files during a running session.

Dialogue node format
────────────────────
Each tree has one or more [[node]] sections:

  [[node]]
  id       = "root"                   # Required entry point — every tree needs this
  lines    = ["Hello!", "Greetings!"] # Random pick each visit (rolling dialogue)
  # OR:
  line     = "Hello!"                 # Single fixed line
  condition = { flag = "met_mira" }   # Optional: skip node entirely if false
  script   = [...]                    # Optional script ops run on node entry

  [[node.response]]                   # Nested directly under its node
  text = "What do you know?"
  next = "about_forest"               # node id to jump to; "" = end conversation
  condition = { flag = "quest_started" }
  script   = [{ op = "set_flag", flag = "asked_forest" }]

Flat format (alternative — useful for many responses on one node):

  [[response]]
  node = "root"                       # parent node id
  text = "What do you know?"
  next = "about_forest"

Both formats are supported and can be mixed in the same file.

Special node IDs:
  "root"  — required conversation entry point
  ""      — end conversation immediately when chosen as `next`

Conditions (on nodes or responses):
  { flag = "name" }           — player.flags contains "name"
  { not_flag = "name" }       — player.flags does NOT contain "name"
  { item = "item_id" }        — player carries item with that id
  { quest = "qid", step = N } — active quest is at step N
  { quest_complete = "qid" }  — quest is complete
  { level_gte = N }           — player.level >= N

Rolling / cycling dialogue:
  lines = ["a", "b", "c"]        — random pick every time
  lines = [...], cycle = true    — advance sequentially (tracked per NPC+node
                                   in player.npc_dialogue_index)
"""

from __future__ import annotations
import random
from pathlib import Path
from engine.toml_io import load as toml_load
import engine.log as log

_DATA_ROOT = Path(__file__).parent.parent / "data"
_SKIP_DIRS = {"zone_state", "players"}


# ── Fallback dialogue lines ────────────────────────────────────────────────────

_HOSTILE_BRUSHOFFS = [
    "{name} bares its teeth and lunges — not the talking type.",
    "{name} doesn't hear you. Or doesn't care.",
    "{name} fixes you with a cold stare and says nothing.",
    "{name} growls low in its throat. Words aren't on the menu.",
    "{name} ignores you entirely, eyes tracking your every move.",
    "{name} snarls. Conversation is not what it has in mind.",
    "You open your mouth. {name} opens its jaws wider.",
    "{name} tilts its head, then charges.",
]

_GENERIC_BRUSHOFFS = [
    "{name} doesn't seem interested in talking.",
    "{name} gives you a queer look and shrugs you off.",
    "{name} waves you away without a word.",
    "{name} isn't listening.",
    "{name} stares through you as if you aren't there.",
    "With a quick shake of the head, {name} looks away.",
    "{name} mutters something under its breath and turns aside.",
]


def _no_dialogue_line(npc: dict) -> str:
    """
    Return a randomised flavour line for an NPC with no dialogue configured.
    Hostile NPCs get suitably aggressive brush-offs; others get neutral ones.
    """
    name = npc.get("name", "They")
    pool = _HOSTILE_BRUSHOFFS if npc.get("hostile") else _GENERIC_BRUSHOFFS
    return random.choice(pool).replace("{name}", name)

# Two-level cache: npc_id → tree dict (None = confirmed absent)
_CACHE:    dict[str, dict | None] = {}
# Path cache: npc_id → file path, built once on first miss
_PATH_MAP: dict[str, Path] | None = None


def _build_path_map() -> dict[str, Path]:
    """
    Walk every zone folder looking for dialogues/<npc_id>.toml files.
    Build a flat {npc_id: path} mapping. If the same npc_id appears in
    multiple zones (shouldn't happen, but could), first alphabetical zone wins.
    """
    result: dict[str, Path] = {}
    for zone_folder in sorted(_DATA_ROOT.iterdir()):
        if not zone_folder.is_dir() or zone_folder.name in _SKIP_DIRS:
            continue
        dialogues_dir = zone_folder / "dialogues"
        if not dialogues_dir.exists():
            continue
        for path in sorted(dialogues_dir.glob("*.toml")):
            npc_id = path.stem
            if npc_id not in result:
                result[npc_id] = path
    return result


def load_tree(npc_id: str) -> dict | None:
    """Return {node_id: node_dict} for an NPC's dialogue tree, or None.

    Searches all zone folders' dialogues/ subdirectories. Results are cached
    after first load. Supports flat [[response]] with node= field — responses
    are attached to their parent node dict under the "response" key.
    """
    global _PATH_MAP
    if npc_id in _CACHE:
        return _CACHE[npc_id]

    if _PATH_MAP is None:
        _PATH_MAP = _build_path_map()

    path = _PATH_MAP.get(npc_id)
    if not path or not path.exists():
        _CACHE[npc_id] = None
        return None

    raw   = toml_load(path)
    nodes = {n["id"]: n for n in raw.get("node", []) if n.get("id")}
    for resp in raw.get("response", []):
        parent_id = resp.get("node", "")
        if parent_id in nodes:
            nodes[parent_id].setdefault("response", []).append(resp)
    _CACHE[npc_id] = nodes
    return nodes


def reload_tree(npc_id: str) -> None:
    """Invalidate cached tree for one NPC (re-scans on next access)."""
    _CACHE.pop(npc_id, None)


def reload_all() -> None:
    """Invalidate all cached trees and the path map (re-scans everything)."""
    global _PATH_MAP
    _CACHE.clear()
    _PATH_MAP = None


# ── Condition evaluation ──────────────────────────────────────────────────────

def _check_condition(cond: dict | None, player, quests) -> bool:
    if not cond:
        return True
    if "flag" in cond and cond["flag"] not in player.flags:
        return False
    if "not_flag" in cond and cond["not_flag"] in player.flags:
        return False
    if "item" in cond:
        has = any(i.get("id") == cond["item"] for i in player.inventory)
        if not has:
            return False
    if "not_quest" in cond:
        # { not_quest = "quest_id" } — only show if quest is NOT active at any step
        if quests.at_step(cond["not_quest"], 0) or quests.is_complete(cond["not_quest"]):
            return False
    if "quest" in cond:
        step = int(cond.get("step", 0))
        if not quests.at_step(cond["quest"], step):
            return False
    if "quest_complete" in cond:
        if not quests.is_complete(cond["quest_complete"]):
            return False
    if "level_gte" in cond:
        if player.level < int(cond["level_gte"]):
            return False
    if "skill" in cond:
        # { skill = "social", min = 40 }
        skill_id = cond["skill"]
        minimum  = int(cond.get("min", 0))
        if player.skills.get(skill_id, 0.0) < minimum:
            return False
    if "gold" in cond:
        # { gold = 50 }  — player must have at least this much gold
        if player.gold < int(cond["gold"]):
            return False
    if "no_companion" in cond:
        # { no_companion = true }  — only show if player has no companion
        if cond["no_companion"] and player.companion:
            return False
    if "prestige_min" in cond or "min_prestige" in cond:
        # { prestige_min = 75 } or { min_prestige = 75 }  — player must have prestige >= this value
        threshold = int(cond.get("prestige_min", cond.get("min_prestige", 0)))
        if getattr(player, "prestige", 0) < threshold:
            return False
    if "prestige_max" in cond or "max_prestige" in cond:
        # { prestige_max = -75 } or { max_prestige = -75 }  — player must have prestige <= this value
        threshold = int(cond.get("prestige_max", cond.get("max_prestige", 0)))
        if getattr(player, "prestige", 0) > threshold:
            return False
    if "affinity" in cond:
        # { affinity = "verdant_hero" }  — player must have this affinity tag
        if cond["affinity"] not in getattr(player, "prestige_affinities", []):
            return False
    if "no_affinity" in cond:
        # { no_affinity = "outlaw" }  — player must NOT have this affinity tag
        if cond["no_affinity"] in getattr(player, "prestige_affinities", []):
            return False
    return True


# ── Dialogue session ─────────────────────────────────────────────────────────

class DialogueSession:
    """
    Drives one complete NPC conversation.

    Usage:
        session = DialogueSession(npc, player, quests, ctx)
        while not session.done:
            output = session.current_output()  # list of (tag, text)
            choices = session.current_choices() # list of (key, text) e.g. [("1","...")]
            # show output, show choices, get player input
            session.choose("1")
    """

    def __init__(self, npc: dict, player, quests, ctx):
        self.npc    = npc
        self.player = player
        self.quests = quests
        self.ctx    = ctx
        self._tree  = load_tree(npc.get("id",""))
        self._node: str = "root"
        self.done   = False
        self._pending_output: list[tuple[str,str]] = []
        self._pending_choices: list[tuple[str,str,str,list]] = []  # (key, text, next_id, script)
        log.debug("dialogue", "DialogueSession init",
                  npc_id=npc.get("id","?"), npc_name=npc.get("name","?"),
                  hostile=npc.get("hostile", False),
                  has_tree=self._tree is not None,
                  has_inline_dialogue=bool(npc.get("dialogue","").strip()))
        self._enter_node("root")

    def _get_line(self, node: dict) -> str:
        """Pick a line from the node, respecting random/cycle, then apply substitutions."""
        lines = node.get("lines", [])
        if lines:
            npc_id = self.npc.get("id","")
            if node.get("cycle", False):
                idx_key = f"{npc_id}:{node['id']}"
                idx = self.player.npc_dialogue_index.get(idx_key, 0)
                line = lines[idx % len(lines)]
                self.player.npc_dialogue_index[idx_key] = idx + 1
            else:
                line = random.choice(lines)
        else:
            line = node.get("line", "...")
        return self._substitute(line)

    def _substitute(self, text: str) -> str:
        """
        Replace template tokens in dialogue text with live values.

        Supported tokens:
          {player}      → player's character name
          {player_name} → same
          {npc}         → this NPC's name
          {npc_name}    → same
          {gold}        → player's current gold
          {hp}          → player's current HP
          {level}       → player's current level
          {zone}        → current zone id (for flavour)

        Example TOML:
          line = "Ah, {player}. I've been expecting someone like you."
        """
        if "{" not in text:
            return text   # fast path — no substitutions needed
        return (text
            .replace("{player}",      self.player.name)
            .replace("{player_name}", self.player.name)
            .replace("{npc}",         self.npc.get("name", ""))
            .replace("{npc_name}",    self.npc.get("name", ""))
            .replace("{gold}",        str(self.player.gold))
            .replace("{hp}",          str(self.player.hp))
            .replace("{level}",       str(self.player.level))
            .replace("{zone}",        self.player.room_id.split("_")[0])
        )

    def _enter_node(self, node_id: str) -> None:
        from engine.msg import Tag
        from engine.script import ScriptRunner

        self._pending_output  = []
        self._pending_choices = []

        log.debug("dialogue", "_enter_node called", node_id=repr(node_id),
                  has_tree=self._tree is not None)

        if not node_id:
            log.debug("dialogue", "_enter_node: empty node_id -> done")
            self.done = True
            return

        if not self._tree:
            # Fallback: plain NPC dialogue field
            d = self.npc.get("dialogue", "").strip()
            if not d:
                d = _no_dialogue_line(self.npc)
                log.debug("dialogue", "_enter_node: no tree, no inline -> generated brushoff",
                          line=d)
            else:
                log.debug("dialogue", "_enter_node: no tree, using inline dialogue", line=d)
            self._pending_output = [(Tag.NPC, f"{self.npc['name']} says:"),
                                    (Tag.DIALOGUE, f'  "{d}"')]
            self.done = True
            return

        node = self._tree.get(node_id)
        if not node:
            log.debug("dialogue", "_enter_node: node_id not in tree -> done",
                      node_id=node_id, tree_keys=list(self._tree.keys()))
            self.done = True
            return

        # Check entry condition
        cond = node.get("condition")
        cond_ok = _check_condition(cond, self.player, self.quests)
        log.debug("dialogue", "_enter_node: condition check",
                  node_id=node_id, condition=cond, passed=cond_ok)
        if not cond_ok:
            self.done = True
            return

        # Run node entry script
        node_script = node.get("script", [])
        if node_script:
            runner = ScriptRunner(self.ctx)
            runner.run(node_script)

        # Build output
        line = self._get_line(node)
        log.debug("dialogue", "_enter_node: built line", node_id=node_id, line=line)
        self._pending_output.append((Tag.NPC, f"{self.npc['name']}:"))
        self._pending_output.append((Tag.DIALOGUE, f'  "{line}"'))

        # Build response choices (filter by condition)
        responses = node.get("response", [])
        visible   = []
        for r in responses:
            if _check_condition(r.get("condition"), self.player, self.quests):
                visible.append(r)

        log.debug("dialogue", "_enter_node: responses",
                  total=len(responses), visible=len(visible))
        if not visible:
            self.done = True
            return

        for i, r in enumerate(visible, 1):
            self._pending_choices.append((
                str(i),
                r.get("text","..."),
                r.get("next",""),
                r.get("script", []),
            ))

    def current_output(self) -> list[tuple[str,str]]:
        return list(self._pending_output)

    def current_choices(self) -> list[tuple[str,str]]:
        return [(key, text) for key, text, _, _ in self._pending_choices]

    def choose(self, key: str) -> None:
        """Player selects a response by its key (e.g. "1")."""
        from engine.script import ScriptRunner

        match = next((c for c in self._pending_choices if c[0] == key), None)
        if not match:
            return
        _, text, next_id, resp_script = match

        # Run response script
        if resp_script:
            runner = ScriptRunner(self.ctx)
            runner.run(resp_script)

        self._enter_node(next_id)


def run_inline(npc: dict, player, quests, ctx, bus,
               input_fn=None) -> None:
    """
    Drive a full dialogue session inline, emitting all output to the bus.
    Responses are presented as numbered choices; player picks by typing 1/2/3.

    input_fn, if provided, is called instead of the built-in input() and must
    have the signature:

        input_fn(prompt: str, choices: list[tuple[str, str]]) -> str

    where choices is the list of (key, text) pairs already emitted to the bus.
    This lets non-CLI callers (e.g. the AI playtester) inject their own input
    source without touching stdin.

    Called by commands.py _cmd_talk.
    """
    from engine.events import Event
    from engine.msg import Msg, Tag

    _input = input_fn if input_fn is not None else lambda prompt, _choices: input(prompt)

    session = DialogueSession(npc, player, quests, ctx)
    bus.emit(Event.OUTPUT, Msg(Tag.BLANK, ""))

    log.debug("dialogue", "run_inline: session created",
              npc_id=npc.get("id","?"), done_at_start=session.done,
              pending_output_count=len(session._pending_output),
              pending_choices_count=len(session._pending_choices))

    # Emit the first node's output regardless of done state — fallback lines
    # (e.g. hostile NPCs with no dialogue) set done=True on init but still
    # have pending_output that must reach the player.
    first_output = session.current_output()
    log.debug("dialogue", "run_inline: emitting first output",
              lines=[(t, v[:40]) for t, v in first_output])
    for tag, text in first_output:
        bus.emit(Event.OUTPUT, Msg(tag, text))

    while not session.done:
        choices = session.current_choices()
        log.debug("dialogue", "run_inline: loop iteration",
                  choices_count=len(choices))
        if not choices:
            break

        # Emit numbered choices
        bus.emit(Event.OUTPUT, Msg(Tag.BLANK, ""))
        for key, text in choices:
            bus.emit(Event.OUTPUT, Msg(Tag.DIALOGUE, f"  [{key}] {text}"))
        bus.emit(Event.OUTPUT, Msg(Tag.BLANK, ""))

        # Get player input
        try:
            raw = _input("  > ", choices).strip()
        except (EOFError, KeyboardInterrupt):
            break

        if raw.lower() in ("q", "quit", "exit", "bye", "leave"):
            break

        session.choose(raw)

        # Emit next node output
        for tag, text in session.current_output():
            bus.emit(Event.OUTPUT, Msg(tag, text))

    bus.emit(Event.OUTPUT, Msg(Tag.BLANK, ""))
