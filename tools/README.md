# Delve — Developer Tools

All scripts run from the **project root** unless noted otherwise.
None require external packages except where marked.

---

## Quick reference

| Script | What it does | External dep |
|--------|-------------|-------------|
| `validate.py` | Check all world TOML for errors | — |
| `clean.py` | Delete caches, zone state, player saves | — |
| `map.py` | ASCII map in terminal (WCT has richer HTML map) | — |
| `dialogue_graph.py` | Graphviz DOT graph of an NPC's dialogue tree (also built into WCT) | graphviz CLI (for `--render`) |
| `quest_graph.py` | Graphviz DOT graph of a quest's step flow (also built into WCT) | graphviz CLI |
| `ai_player.py` | Autonomous AI playtester via Anthropic API | `ANTHROPIC_API_KEY` |
| `offline_bot.py` | Deterministic offline playtester (no API key needed) | — |
| `world2html.py` | Export a full world to a self-contained HTML review document | graphviz CLI (optional, for dialogue graphs) |
| `wct_server.py` | Local web server: World Creation Tool + web game client | — |
| `run_script.py` | Run a world script file against a named player from the CLI | — |
| `md2html.py` | Convert a Markdown file to a self-contained HTML page | — |

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

Renders the world map as an ASCII diagram in the terminal.

```
python tools/map.py                          # ASCII map, all zones
python tools/map.py --zone ashwood           # ASCII map, one zone
python tools/map.py --full                   # ASCII map with item/NPC counts per room
python tools/map.py --world <name>           # select world by folder name
python tools/map.py --dot                    # export Graphviz .dot file
```

Auto-placed rooms appear with a `~` marker.  An optional `coord = [x, y]`
field (east = +x, north = +y) in the room TOML can pin a room's position.

> **The WCT map view is the recommended map tool.** It renders an interactive
> SVG map with pan/zoom, room detail panel, NPC/item count badges, exit-direction
> labels, per-zone colour coding, and one-click navigation to the editor.
> Use `map.py` when you need a quick terminal view or a headless `.dot` export.

---

## dialogue_graph.py

Generates a Graphviz DOT file visualising one NPC's complete dialogue tree.
Every node, every response path, every condition and script op is shown.

> **Also available in the WCT.** Open any dialogue in the WCT editor and click
> **Graph** for an interactive in-browser SVG view, or **Export DOT** to download
> the `.dot` file without leaving the browser.

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

> **Also available in the WCT.** Open any quest in the WCT editor and click
> **Graph** for an interactive in-browser SVG view, or **Export DOT** to download
> the `.dot` file without leaving the browser.

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
- **Dashed red edges** — no trigger found for this transition anywhere
  (dialogue, kill_script, on_enter, on_get). The quest step is unimplemented.

Trigger scanning covers **dialogue files**, NPC `kill_script`, room `on_enter`,
and item `on_get` — so warning edges only appear for steps with no trigger anywhere.

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

## offline_bot.py

A deterministic offline playtester — no AI, no API key. Reads zone TOML data
directly to make decisions (explore rooms, fight enemies, pick up items, talk
to NPCs). Useful for smoke-testing engine changes and data integrity without
any external services.

```
python tools/offline_bot.py
python tools/offline_bot.py --world first_world --turns 500 --name BotName
python tools/offline_bot.py --world first_world --quest ashwood_contract
python tools/offline_bot.py --world first_world --zone ashwood --verbose
```

| Flag | Default | Description |
|------|---------|-------------|
| `--world` | first found | World folder name |
| `--turns` | `200` | Max commands to run |
| `--name` | `Bot` | Character name |
| `--quest` | — | Focus exploration toward completing this quest |
| `--zone` | — | Restrict exploration to a single zone |
| `--verbose` | off | Print command-by-command trace to stdout |

Outputs an HTML session log to `tools/ai_sessions/ai_<world>_<timestamp>.html`
and appends stats to `tools/ai_sessions/stats.jsonl`.

---

## world2html.py

Exports a full world to a single self-contained HTML file for content review
and flow checking — suitable for printing or reading in a browser.

```
python tools/world2html.py                           # first world found
python tools/world2html.py --world first_world
python tools/world2html.py --world first_world --output review.html
python tools/world2html.py --world first_world --zone ashwood
```

| Flag | Default | Description |
|------|---------|-------------|
| `--world` | first found | World folder name |
| `--zone` | — | Limit output to one zone |
| `--output` | `tools/world_review.html` | Output file path |

**Sections per zone:**

- **Map** — static SVG room layout with exit lines, item/NPC counts, locked-exit markers
- **Rooms** — table of ID, name, description, flags, exits, items, spawns, `on_enter` scripts
- **NPCs** — table of ID, name, stats, style, hostile flag, tags, `kill_script`
- **Items** — table of ID, name, slot, weight, scenery flag, tags, `on_get`/`on_use` scripts
- **Quests** — per-quest steps with objectives, triggers, and inline scripts
- **Dialogues** — graphviz SVG graph per NPC (requires `dot` on PATH); falls back to a
  collapsible HTML tree if graphviz is not installed
- **Styles** — fighting styles defined in the zone

**World-level sections:**

- **Flag Index** — every flag across the world, with each usage tagged as `set`, `clear`,
  `check`, or `show_if`, linked to the entity that uses it

Output is a single `.html` file with all CSS inlined (no external dependencies).
A `@media print` stylesheet expands all collapsible sections for clean printing.

---

## wct_server.py

Starts a local HTTP server that serves two tools on the same port:

- **World Creation Tool (WCT)** — browser-based TOML editor with map view, at `/`
- **Web game client** — single-page play interface (`game.html`), at `/game`

```
python tools/wct_server.py            # http://localhost:7373  (no browser auto-open)
python tools/wct_server.py --port 8080
python tools/wct_server.py --browser  # also open browser automatically
```

The server does **not** open a browser automatically. Navigate to the URL printed
on startup, or pass `--browser` to open it.

The server uses a threading model so the SSE stream for the game client can
run concurrently with WCT requests.

### WCT features

- **Editor** — rooms, NPCs, items, quests, dialogues, fighting styles, and status effects
  with full field editing, script op editor, exit editor, ref picker, drag-and-drop
  quest steps and dialogue responses
- **Map view** — interactive SVG map (pan/zoom), per-zone colour coding, room detail
  panel, one-click edit; NPC/item count badges, exit-direction labels, start-room ★ and
  town ⌂ icons, connected-room gold highlight on selection; zone switch resets pan/zoom
- **Dialogue graph** — click **Graph** in any dialogue editor for an in-browser interactive
  SVG tree: nodes colour-coded by script op, conditional edges in blue, click any node
  for a detail panel showing text / conditions / ops / responses
- **Quest graph** — click **Graph** in any quest editor for an in-browser interactive
  SVG step-flow diagram: START → steps → COMPLETE, with edge labels showing the source
  (dialogue / kill_script / on_get / on_enter) that drives each transition; ⚠ warnings
  where no trigger is found anywhere
- **References panel** — right-pane cross-reference index shows all entities that
  reference the selected object, plus a **Flags** section listing every flag the entity
  reads or writes (with world-wide usage count, clickable for a full usage list)
- **Error panel** — collapsible panel at the bottom of the editor; lists ERR/WRN rows
  for missing fields, unknown refs, dialogue issues, and quest giver mismatches;
  each row is clickable and navigates to the offending entity
- **DOT export** — download Graphviz `.dot` files for the full world map (map toolbar),
  any NPC's dialogue tree (dialogue **Export DOT** button), or any quest's step flow
  (quest **Export DOT** button)
- **Validate** — run `validate.py` from inside the browser
- **UI state** — selected object, panel widths, and sidebar collapse state are
  saved per-world in `localStorage`

### Game server endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/game` | GET | Serve game.html |
| `/game/worlds` | GET | List available worlds |
| `/game/players` | GET | List existing characters |
| `/game/status` | GET | Current session state (hp, room, alive) |
| `/game/stream` | GET | Server-Sent Events — streams engine output |
| `/game/login` | POST | Start a new session `{world_id, player_name}` |
| `/game/command` | POST | Send a command `{cmd}` |
| `/game/quit` | POST | End the current session |

See `frontend/FRONTEND_MANUAL.md` for the full protocol reference.

---

## Internal / supporting files

| File | Role |
|------|------|
| `graph_common.py` | Shared library for `dialogue_graph.py` and `quest_graph.py` |
| `wct.html` | WCT browser frontend (HTML + JS) |
| `wct_common.css` | WCT stylesheet — served at `/css/wct_common.css` by `wct_server.py` |
| `md2html.py` | Markdown → self-contained HTML converter |
| `ai_sessions/` | AI playtester logs — not committed |

---

## Output directories

Both graph tools write to `output/` in the project root, which is git-ignored.

```
output/
  dialogue/   dialogue_<npc_id>.dot  (and .svg / .pdf if rendered)
  quest/      quest_<quest_id>.dot   (and .svg / .pdf if rendered)
```
