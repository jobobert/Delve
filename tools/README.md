# Delve — Developer Tools

All scripts run from the **project root** unless noted otherwise.
None require external packages except where marked.

---

## Quick reference

| Script | What it does | External dep |
|--------|-------------|-------------|
| `validate.py` | Check all world TOML for errors | — |
| `clean.py` | Delete caches, zone state, player saves | — |
| `map.py` | ASCII or HTML interactive world map | — |
| `dialogue_graph.py` | Graphviz DOT graph of an NPC's dialogue tree | graphviz CLI |
| `quest_graph.py` | Graphviz DOT graph of a quest's step flow | graphviz CLI |
| `ai_player.py` | Autonomous AI playtester via Anthropic API | `ANTHROPIC_API_KEY` |
| `wct_server.py` | Local web server for the World Creation Tool | — |

---

## validate.py

Scans every zone folder and checks:

- All room exit targets are known room IDs
- All room item and spawn references are known IDs
- All NPC and item records have required fields
- Duplicate IDs across zones (warns — first definition wins)
- NPC shop items exist
- Fighting styles referenced by NPCs exist
- Exactly one room is marked `start = true`
- Every `.toml` is valid TOML
- Dialogue trees: all nodes reachable from root, all `next=` references valid

```
python tools/validate.py
```

Exit code `0` = passed (warnings are OK).
Exit code `1` = one or more errors found.

---

## clean.py

Resets the project to a clean runtime state.

```
python tools/clean.py              # interactive — prompts before deleting players
python tools/clean.py --cache      # __pycache__ only
python tools/clean.py --state      # data/zone_state/ only (NPC/item live state)
python tools/clean.py --players    # data/players/ only (character saves)
python tools/clean.py --all        # everything, no prompt
```

---

## map.py

Renders the world map in the terminal or as a self-contained HTML file.

```
python tools/map.py                          # ASCII map, all zones
python tools/map.py --zone ashwood           # ASCII map, one zone
python tools/map.py --full                   # ASCII map with item/NPC counts per room
python tools/map.py --html                   # HTML map -> tools/admin_map.html
python tools/map.py --html --output my.html  # HTML map to a custom path
python tools/map.py --world <name>           # select world by folder name
```

The HTML output (`admin_map.html`) is self-contained — open it directly in any
browser.  It shows rooms as nodes, exits as edges, with room details on click.

Rooms without an explicit `coord = [x, y]` field are placed automatically using
exit topology (BFS from coordinated neighbours).  They appear with a dashed
border in HTML and a `~` marker in ASCII.  Add `coord = [x, y]` to a room's
TOML to fix its position permanently.

---

## dialogue_graph.py

Generates a Graphviz DOT file visualising one NPC's complete dialogue tree.
Every node, every response path, every condition and script op is shown.

**Requires:** the `dot` command from [Graphviz](https://graphviz.org/) on your PATH
(only needed for `--render`; you can also render the `.dot` file manually).

```
# All NPCs -> output/dialogue/dialogue_<npc_id>.dot
python tools/dialogue_graph.py

# Single NPC, auto-render to SVG
python tools/dialogue_graph.py --npc garrison_ghost --render svg

# All NPCs in one zone
python tools/dialogue_graph.py --zone ashwood

# Select a specific world (default: first world found)
python tools/dialogue_graph.py --world sixfold_realms

# Custom output file
python tools/dialogue_graph.py --npc elder_mira --out elder.dot

# Render manually
dot -Tsvg output/dialogue/dialogue_garrison_ghost.dot -o ghost.svg
dot -Tpdf output/dialogue/dialogue_garrison_ghost.dot -o ghost.pdf
```

### Reading the output

**Node header color** shows what the node *does*:

| Color | Meaning |
|-------|---------|
| Light gray | Pure narrative — no script ops |
| Pale yellow | Sets a flag |
| Pale orange | Gives item / gold / xp |
| Amber | Advances or starts a quest |
| Green | Completes a quest |
| Pink-red | Damage or `fail` op |

**Node border:**

| Border | Meaning |
|--------|---------|
| Solid | Normal reachable node |
| Dashed blue | Node has an entry condition (may be skipped entirely) |
| Gray | Unreachable from `root` (orphaned) |

**Edge color:**

| Color | Meaning |
|-------|---------|
| Black | Unconditional response |
| Blue | Response gated by a condition (condition shown on the label) |
| Gray dashed | `next = ""` — ends the conversation |

Each node label shows: node ID, full dialogue text, and any script ops fired on
entry.  Each edge label shows: the response text, its condition (if any), and
any response-level script ops.

---

## quest_graph.py

Generates a Graphviz DOT file showing a quest's full step chain.  Scans all
dialogue files to find which NPC / dialogue node triggers each step transition
and annotates every edge with the source NPC, required flags, and flags set.

**The primary authoring value is hole detection:** any step with no dialogue
trigger found is highlighted with a dashed red "⚠ no advance_quest trigger
found" edge, telling you that nothing in any dialogue file currently drives
the quest to that step.

**Requires:** `dot` from Graphviz (only for `--render`).

```
# All quests -> output/quest/quest_<id>.dot
python tools/quest_graph.py

# Single quest, auto-render to SVG
python tools/quest_graph.py --quest ashwood_contract --render svg

# Select a specific world (default: first world found)
python tools/quest_graph.py --world sixfold_realms

# Custom output file
python tools/quest_graph.py --quest ashwood_contract --out ashwood.dot

# Render manually
dot -Tsvg output/quest/quest_ashwood_contract.dot -o ashwood.svg
dot -Tpdf output/quest/quest_ashwood_contract.dot -o ashwood.pdf
```

### Reading the output

- **START (green oval)** — labeled with the `giver` NPC from the quest TOML.
- **Step boxes** — show the step index, objective text, hint, and
  `completion_flag` (if set).
- **COMPLETE (green oval)** — lists all rewards (gold, xp, items).
- **Blue edges** — normal transitions, labeled with NPC / node, flags required,
  and flags set.
- **Green edge** — the final `complete_quest` transition.
- **Dashed red edges** — no dialogue trigger found for this transition.
  The quest either needs a new dialogue node, or the trigger is in a
  `kill_script` / `on_enter` hook not scanned by this tool.

Note: `quest_graph.py` only scans **dialogue files** for triggers.  Quests
advanced via NPC `kill_script`, room `on_enter`, or item `on_get`/`on_drop`
will show warning edges even if a trigger does exist.

---

## ai_player.py

An autonomous AI playtester.  Drives the game through the same
`CommandProcessor` used by a human player, using Claude to decide what to do
next.  Useful for finding dead-ends, untested combat paths, and narrative gaps
at scale.

**Requires:** `ANTHROPIC_API_KEY` environment variable.

```
python tools/ai_player.py
python tools/ai_player.py --name Scout --turns 500
python tools/ai_player.py --goal "complete the ashwood_contract quest"
python tools/ai_player.py --model claude-opus-4-6 --verbose
python tools/ai_player.py --out logs/
```

| Flag | Default | Description |
|------|---------|-------------|
| `--name` | `AI_Tester` | Character name |
| `--turns` | `1000` | Max commands before stopping |
| `--goal` | `"explore and fight"` | High-level directive for the AI |
| `--model` | `claude-haiku-4-5-20251001` | Claude model to use |
| `--out` | `tools/ai_sessions/` | Directory for session logs |
| `--verbose` | off | Also print AI reasoning to stdout |

Session logs (JSON) are written to `tools/ai_sessions/` and are git-ignored.

---

## wct_server.py

Starts a local HTTP server that serves the World Creation Tool (WCT) — a
browser-based editor for TOML world data.  Opens automatically in your default
browser.

```
python tools/wct_server.py            # http://localhost:7373
python tools/wct_server.py --port 8080
```

---

## Internal / supporting files

| File | Role |
|------|------|
| `graph_common.py` | Shared library for `dialogue_graph.py` and `quest_graph.py` |
| `wct.html` | Static frontend asset for the World Creation Tool |
| `dialog_parser.py` | Legacy dialogue DOT generator (superseded by `dialogue_graph.py`) |
| `admin_map.html` | Generated output — not committed |
| `ai_sessions/` | AI playtester logs — not committed |

---

## Output directories

Both graph tools write to `output/` in the project root, which is git-ignored.

```
output/
  dialogue/   dialogue_<npc_id>.dot  (and .svg / .pdf if rendered)
  quest/      quest_<quest_id>.dot   (and .svg / .pdf if rendered)
```
