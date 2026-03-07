# Delve — session handoff

You are working on **Delve**, a Python 3.10+ MUD engine with TOML world data.
This document is the complete project context. Read it carefully before doing
anything, then ask what the user wants next.

---

## What this project is

A terminal MUD engine with zero external dependencies. Two core principles:

1. **Everything is data** — rooms, NPCs, items, quests, dialogue, skills, and
   script logic all live in TOML files. No code changes needed to add content.
2. **Engine/frontend decoupled** — the engine never calls `print()`. All output
   is `Msg(tag, text)` objects on an `EventBus`. The CLI subscribes and renders.

**Two worlds currently exist:**
- `first_world` — original dev/test world (57 rooms · 6 zones · 69 NPCs · 123
  items · 7 styles · 34 dialogue trees · 12 quests · 9 crafting commissions · 3
  companions). Zones: millhaven, training, greenhollow, millbrook, ashwood, blackfen.
- `sixfold_realms` — new world under active construction. See
  `data/sixfold_realms/world_overview.md` for full backstory and design.

---

## How to get the code

The project is extracted as `mud/` in the working directory. The layout is:

```
mud/
├── engine/              Core systems (combat, dialogue, world, script, …)
├── frontend/            CLI renderer + config + game.html + FRONTEND_MANUAL.md
├── data/                All TOML world data
│   ├── players/         Per-character folders — <name>/player.toml + <name>/zone_state/
│   ├── first_world/     Original dev/test world (6 zones, full content)
│   └── sixfold_realms/  New world under construction (config.py + world_overview.md)
├── tools/               validate.py, map.py, wct_server.py, ai_player.py, clean.py
├── main.py
├── README.md            Partial system reference — READ THIS for any system details, and reference the engine/README.md and data/README.md as necessary
└── HANDOFF.md           This file
```

**Always read `README.md` for authoritative details** on any system before
touching code. It reflects the actual current state of every file.
**Always update the appropriate `README.md` when making changes.**

---

## Key architecture facts

### Engine modules

| File | Role |
|------|------|
| `engine/world.py` | Zone-streaming manager. `world.prepare_room(room_id, player)` loads a zone if needed. `world.npcs` and `world.items` are flat dicts of all known entities (templates). Live room state (including `_npcs` list) lives on the room dict itself. |
| `engine/commands.py` | ~2500-line command dispatcher. `CommandProcessor.process(raw)` is the main entry point. `_resolve_npc(target)` finds a live NPC in the current room (filters `hp > 0`). |
| `engine/combat.py` | `CombatSession(player, npc, bus, room, ctx)`. Call `player_attack()` once per turn. Fully instrumented with `engine/log.py` at DEBUG level. |
| `engine/dialogue.py` | `run_inline(npc, player, quests, ctx, bus, input_fn)`. Loads tree from `data/<world_id>/<zone>/dialogues/<npc_id>.toml` or falls back to `npc["dialogue"]` string or auto-generates a brush-off line. |
| `engine/script.py` | `ScriptRunner(ctx).run(ops)`. 45 ops. `fail` aborts cleanly. `require_tag` gates on item tags. |
| `engine/log.py` | `log.configure(...)` at startup. `log.debug/info/warn(category, msg, **kv)`. `log.section(title)`. `log.enter/exit` for bracketed spans. Writes to `delve.log`. |
| `engine/player.py` | `Player` dataclass. `player.save()` / `Player.load(name)`. Equipped items in `player.equipped` (10 slots). Bank in `player.bank`. Skills in `player.skills`. Prestige in `player.prestige`. |
| `engine/prestige.py` | Score −999…+999, 10 tiers. `apply_delta`, `tier_name`, `shop_modifier`, `hostile_on_sight`. |
| `engine/styles.py` | 7 fighting styles with matchup tables, gear affinity, and passive abilities that unlock at proficiency thresholds. |
| `engine/toml_io.py` | **Custom TOML parser** — superset of spec. Supports multi-line inline tables and triple-quoted strings. Standard `tomllib` will fail on these files. Always use `from engine.toml_io import load`. |

### Frontend

| File | Role |
|------|------|
| `frontend/config.py` | **All tunables** live here: `WRAP_WIDTH`, `AUTO_ATTACK`, `AUTO_ATTACK_STOP_HP_PCT`, `COLOR_OVERRIDES`, `STARTUP_ALIASES`, `LOG_ENABLED`, `LOG_FILE`, `LOG_LEVEL`, `LOG_CATEGORIES`. |
| `frontend/cli.py` | `CLIFrontend` class. `_on_output(msg)` renders to terminal. `_run_auto_attack` loop. Logging wired: `_elog.section("CMD: ...")` before every command. |

### TOML data conventions

**Rooms** (`rooms.toml`):
- Must have `id`, `name`, `description`
- Optional: `coord = [x, y]` (east=+x, north=+y) — pins position on admin map; not required, not validated
- `spawns = ["npc_id", ...]` — NPCs spawned when zone loads
- `items = ["item_id", ...]` — items present at load
- `flags = ["safe_combat", "no_combat", "healing", "town", ...]`
- `exits = { north = "room_id" }` or locked: `{ north = { to = "room_id", locked = true, lock_tag = "tag" } }`
- `on_enter = [...]` — script ops run on room entry

**NPCs** (`npcs.toml`):
- Required: `id`, `name`, `desc_short`, `desc_long`, `hostile`, `tags`, `style`,
  `style_prof`, `attack`, `defense`, `hp`, `max_hp`, `xp_reward`, `gold_reward`
- Optional: `dialogue` (fallback string), `shop = [...]`, `give_accepts = [...]`,
  `kill_script = [...]`, `respawn = true`, `respawn_time = N`
- Hostile NPCs with no `dialogue` field and no dialogue file get auto-generated
  brush-off lines. Validator warns about these.

**Items** (`items.toml`):
- Required: `id`, `name`, `desc_short`, `slot`, `weight`
- Optional: `scenery`, `respawn`, `no_drop`, `key_tag`, `on_get = [...]`,
  `on_use = [...]`, `effects = [...]`, `on_hit = [...]`

**Dialogue** (`dialogues/<npc_id>.toml`):
- Every tree needs a `[[node]]` with `id = "root"`
- Responses can be nested `[[node.response]]` or flat `[[response]]` with `node =`
- Both formats can coexist in the same file
- `next = ""` ends the conversation

**Script ops** — 53 ops (see `engine/script.py` docstring for full list):
`say`, `message`, `set_flag`, `clear_flag`, `give_gold`, `take_gold`, `give_xp`,
`heal`, `set_hp`, `damage`, `give_item`, `take_item`, `spawn_item`,
`advance_quest`, `complete_quest`, `teach_style`, `unlock_exit`, `lock_exit`,
`skill_check`, `if_skill`, `skill_grow`, `apply_status`, `clear_status`,
`if_status`, `prestige`, `add_affinity`, `remove_affinity`, `if_prestige`,
`if_affinity`, `give_companion`, `dismiss_companion`, `bank_expand`,
`if_flag`, `if_not_flag`, `if_item`, `if_quest`, `if_quest_complete`, `fail`,
`require_tag`, `teleport_player`, `move_npc`, `move_item`,
`if_combat_round`, `if_npc_hp`, `end_combat`, `journal_entry`,
`set_attr`, `adjust_attr`, `if_attr`,
`set_room_light`, `adjust_light`, `if_light`, `set_vision`, `adjust_vision`,
`run_script_file`,
`block_damage`, `multiply_damage`, `counter_damage`, `reduce_damage`,
`skip_npc_attack`, `apply_combat_bleed`, `heal_self`

---

## Debugging workflow

Logging is **enabled by default** (`LOG_ENABLED = True` in `frontend/config.py`).
Categories active: `combat`, `autoattack`, `dialogue`.

When a bug is reported:
1. Ask the user to reproduce it and paste the relevant section from `delve.log`
2. Find the `CMD: <command>` section divider
3. The structured `key=value` lines give full state at each step
4. `CLI._on_output` lines show exactly what the renderer received and whether it printed

To add logging to a new area: `import engine.log as log` then call
`log.debug("category", "description", key=value, ...)`.

---

## Development conventions

- **Always run `python tools/validate.py`** after any data change
- TOML multi-line inline tables and triple-quoted strings are engine extensions —
  use them freely, but be aware standard tools won't parse them
- NPC numbers (Wolf 1, Wolf 2) are assigned at runtime by `_numbered_npcs()` —
  no TOML changes needed for multi-spawn NPCs
- Zone state (NPC HP, room items) persists in `data/players/<name>/zone_state/<zone_id>.json`
- Player saves in `data/players/<name>/player.toml` (cross-world; includes `world_id` field)
- `tools/clean.py --all` resets everything for a fresh test

---

## Known state / recent work

The most recent session completed **Engine Feature Batch (config.toml, light mechanic, player attrs, TOML passives, standalone scripts)**:

1. **`config.py` → `config.toml`** — world config converted from Python to TOML for both
   worlds. `engine/world_config.py` tries `config.toml` first, falls back to legacy `config.py`.
   New fields: `vision_threshold` (default player vision), `[[player_attrs]]` (world-defined numeric
   player attributes). `engine/toml_io.py` fixed to correctly handle `[table]` section headers.

2. **validate.py fixes** — `--world <id>` flag to validate a single world; `round_script` added
   to known NPC fields so it no longer triggers "unrecognised field" warnings.

3. **World-defined player attributes** — worlds define custom numeric attrs in `config.toml`
   `[[player_attrs]]`. Persisted in `player.toml` under `[world_attrs]`. Script ops: `set_attr`,
   `adjust_attr`, `if_attr`. Stats display: `display = "bar"` or `display = "number"`.

4. **Light mechanic** — rooms have `light = N` (0–10, default 10). Players have
   `vision_threshold` (default 3, per-player, script-modifiable). Items have `light_add = N`.
   Blind players see darkness messages in look/examine/map; 20% combat miss chance. Script ops:
   `set_room_light`, `adjust_light`, `if_light`, `set_vision`, `adjust_vision`.

5. **TOML-driven fighting style passives** — all 15 passives across 7 styles moved from
   hardcoded `combat.py` blocks to `styles.toml` using `trigger`, `threshold`, `message`, and
   `on_activate` fields. New `CombatSession._run_passives()` method replaces ~200 lines of
   hardcoded logic. New combat-only script ops: `block_damage`, `multiply_damage`,
   `counter_damage`, `reduce_damage`, `skip_npc_attack`, `apply_combat_bleed`, `heal_self`.
   Chained passives (riposte→parry, counter→dodge, absorb→redirect) use `requires` field.

6. **Standalone script files** — `{ op = "run_script_file", path = "scripts/event.toml" }`
   runs a world-relative script file inline. New tool `tools/run_script.py` runs a script
   against a named player from the CLI: `python tools/run_script.py <file> --player <name>`.

---

The session before that completed **WCT Graph Views + Bug Fixes**:

1. **In-browser dialogue graph** (`_showDlgGraph`) — interactive pan/zoom SVG tree of any
   dialogue file, toggled by a "Graph" button in the dialogue editor. Nodes are colour-coded
   by script op type; edges show conditional vs unconditional responses; a slide-in detail
   panel shows full node text, conditions, script ops, and responses.

2. **In-browser quest graph** (`_showQuestGraph`) — vertical chain SVG for quest step flow,
   toggled by a "Graph" button in the quest editor. Scans all loaded dialogue files to find
   `advance_quest`/`complete_quest` triggers and annotates edges with NPC/node source or a
   ⚠ "no trigger found" warning edge.

3. **CSS extraction** — the entire `<style>` block extracted from `tools/wct.html` into
   `tools/wct_common.css`, served by wct_server.py at `/css/wct_common.css`.

4. **Critical bug fixes** found and fixed:
   - *Temporal dead zone (TDZ)*: `const showDirs` used before declaration inside `renderMap`
     caused a ReferenceError → blank map canvas. Fixed by hoisting declaration above the edge loop.
   - *Flat response format*: TOML `[[response]]` entries are top-level with a `node = "parent_id"`
     field — they are NOT nested in `[[node]]`. The graph layout function now populates
     `nodeMap[nid].response` from `d.response[]` the same way `exportDialogueDot` does.
   - *Array vs object*: `_dlgRecordTrans` calls `out.push(...)` so `out` must be an array.
     Quest graph now collects into `tempTrans[]` then groups into `byStep{}`.
   - *Missing module-level functions*: `_dotFmtScriptOps` and `_dotFmtCondition` were called
     but never defined; added as module-level helpers.

5. **"Export DOT" + "Graph" buttons** added to both dialogue editor and quest editor headers.
   Each editor now has: **Save** · **Export DOT** · **Graph**.

---

The session before that completed **World Modularity Phase 2**:

1. **Multi-world data layout** — zone folders moved from `data/<zone>/` into
   `data/sixfold_realms/<zone>/`. The engine discovers worlds by scanning `data/`
   for subfolders containing `config.py`. Players stay at `data/players/` with a
   `world_id` field linking them to their world.

2. **World config extended** (`data/sixfold_realms/config.py`) — three new
   configurable fields: `CURRENCY_NAME`, `DEFAULT_STYLE`, `EQUIPMENT_SLOTS`.

3. **`engine/world_config.py`** — `init(world_path)` loads a world's config.py
   at startup; `list_worlds(data_dir)` and `peek_world_name(world_path)` support
   world-selection menus.

4. **`engine/world.py`** — `World(world_path)` parameterized; `World.__init__`
   accepts an optional `zone_state_dir` parameter (defaults to `world_path/zone_state/`
   for backward compat). `world.attach_player(player)` switches zone state dir to the
   player's personal folder after login.

5. **`engine/player.py`** — `world_id` field; equipped slots and default style
   read from `wc.EQUIPMENT_SLOTS` / `wc.DEFAULT_STYLE` at creation/load.

6. **`engine/commands.py`** — `_EQUIPMENT_SLOTS` constant removed; all slot/
   currency references now use `_wc.EQUIPMENT_SLOTS` / `_wc.CURRENCY_NAME`.

7. **`frontend/cli.py`** — `_select_world()` added; `run()` now calls
   `wc.init()` → `World(path)` → `_login(world_path)` in sequence.

8. **`tools/validate.py`** — discovers all world folders; validates each
   independently with per-world error/warning summary.

The session after that completed **Per-Player Zone State**:

9. **Per-player zone state** — zone state moved from `data/<world_id>/zone_state/`
   into `data/players/<name>/zone_state/`. Each player session is fully isolated;
   killing a wolf for one player doesn't affect another player's zone. Designed to
   support future concurrent server sessions.

10. **`engine/player.py`** — `player_dir` and `zone_state_dir` properties added.
    `_save_path` now returns `player_dir / "player.toml"`. `save()` creates the
    folder before writing. `load()` auto-migrates old flat `data/players/<name>.toml`
    saves into the new folder layout on first run. `exists()` checks both paths.

11. **`frontend/cli.py`** — `run()` calls `world.attach_player(player)` after login
    to switch the World's zone_state_dir to the player's personal folder.

12. **`tools/clean.py`** — `_zone_state_files()` and `_player_files()` updated to
    scan player subfolders. `clean_players()` removes entire player folders.

13. **`tools/dialogue_graph.py` / `tools/quest_graph.py`** — multi-world support
    added: `--world` flag, world name shown in DOT graph labels. Discovery functions
    in `tools/graph_common.py` updated to take `world_path` and scan zone subfolders.

14. **`tools/map.py`** — rewritten with `--world` flag and `--html` output (self-
    contained). Old `gen_map.py` and `map_html.py` deleted.

15. **World reorganization** — existing zones moved from `data/sixfold_realms/`
    into `data/first_world/` (the original dev/test world, `WORLD_NAME = "Delve"`).
    `data/sixfold_realms/` is now a fresh world shell (`config.py` +
    `world_overview.md`) for the new Sixfold Realms content under construction.

The session after that completed **Coordinate Removal + Web Frontend**:

16. **`engine/map_builder.py`** (new) — topology-aware map data builder extracted
    from `tools/map.py`. Public API: `DIR_DELTA`, `exit_dest()`,
    `apply_auto_layout(rooms)`, `build_map_data(rooms, visited, current)`.
    Returns a renderer-agnostic `{(x,y): cell}` grid usable by CLI, HTML, or
    future graphical clients.

17. **Zone-scoped in-game map** — `_cmd_map` in `engine/commands.py` now scopes
    the map to the current zone only. Cross-zone frontier rooms appear as `[ ? ]`.
    Zone display name comes from `ZoneMeta.name` (read from `zone.toml`,
    falls back to title-cased zone_id). `engine/world.py` `ZoneMeta` dataclass
    now carries a `name` field.

18. **Coordinate removal** — all 65 explicit `coord` fields stripped from
    `data/first_world/*.toml`. Rooms are placed automatically by BFS auto-layout.
    Validator no longer warns about missing coords. `data/WORLD_MANUAL.md` and
    all READMEs updated.

19. **`tools/md2html.py`** (new) — stdlib-only Markdown→HTML converter.
    Usage: `python tools/md2html.py <input.md> [output.html] [--stdout]`.

20. **Web frontend** — `tools/wct_server.py` extended with a threading HTTP
    server (`ThreadingMixIn`) and a `GameSession` class that runs the engine in
    a background thread. New endpoints: `GET /game`, `/game/worlds`,
    `/game/players`, `/game/status`, `/game/stream` (SSE), `POST /game/login`,
    `/game/command`, `/game/quit`.

21. **`tools/game.html`** (new) — single-page webapp game client. Login panel
    (world/character select), scrollable colored output area, command input with
    history (↑/↓), status bar (HP + room), SSE-driven output, map buffering.

22. **`frontend/FRONTEND_MANUAL.md`** (new) — programmer's manual covering engine
    embedding, Tag reference, HTTP API, SSE event format, map data, dialogue
    handling, CLI color palette, and step-by-step frontend guide.

---

## Planned engine work (next session)

### WCT (World Creation Tool)
- Add editing of fighting style passives (`on_activate`, `trigger`, `message`, `requires`)
- Add editing of light mechanic (room `light` field, item `light_add`)
- Add editing of player attributes (`[[player_attrs]]` in config.toml)
- World-global options editor: styles, skills, currency, equipment slots, etc.
- Audit all object types for missing fields (compare TOML spec vs editor forms)
- Fix fighting-style picker (styles not loading in editor)
- TOML output formatting: human-readable multi-line instead of single-line saves

### commands.py
- Banking UI
- Crafting commands
- sleep/rest improvements

### data / world content
- `ashwood_contract` step 3 requires `swordplay` style — check why it's not loading in first_world
- `tools/clean.py --ai` flag to clean AI bot player saves and output files

---

## Tools reference

```bash
python main.py                        # play the game
python tools/validate.py              # check data integrity (run after every edit)
python tools/validate.py --world first_world   # validate a single world
python tools/map.py [--zone Z] [--full]          # ASCII map
python tools/map.py --html [--world W] [--output F]  # self-contained HTML map
python tools/wct_server.py            # WCT → http://localhost:7373  |  Game → /game
python tools/offline_bot.py [--world W] [--turns N] [--quest Q]  # offline playtester
python tools/run_script.py <script.toml> --player <name> [--world <id>]  # run one-shot script
python tools/ai_player.py play [--goal "..."] [--verbose]
python tools/clean.py [--all | --cache | --state | --players]
python tools/md2html.py <input.md>    # convert Markdown to HTML
```
