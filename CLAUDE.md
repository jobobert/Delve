# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Common commands

```bash
python launch_cli.py                      # play the game (CLI)
python launch_cli.py --admin              # CLI with admin commands
python launch_wct.py                      # World Creation Tool → http://localhost:7373 (new window + browser)
python launch_web.py                      # game web frontend → http://localhost:7374 (new window + browser)
python tools/validate.py                  # data integrity check — run after every data change
python tools/validate.py --world first_world  # validate one world only
python tools/clean.py --all              # wipe players, zone state, caches
python tools/run_script.py <file> --player <name>  # run a standalone script file
python tools/offline_bot.py --world first_world --turns 500
```

No build step, no test runner. Correctness is checked by `validate.py` (exit 0 = pass, 1 = errors).

---

## Architecture

### Two hard rules
1. **Everything is TOML data.** Content (rooms, NPCs, items, quests, dialogue, scripts, fighting styles) lives in `data/`. No engine changes needed to add or modify content.
2. **Engine never calls `print()` or reads `stdin`.** All output is `bus.emit(Event.OUTPUT, Msg(tag, text))`. Frontends subscribe to `Event.OUTPUT`.

### Critical: custom TOML parser
`engine/toml_io.py` is a non-standard TOML parser that supports multi-line inline tables and triple-quoted strings. **Always use `from engine.toml_io import load`** — standard `tomllib` will reject many data files.

### Startup sequence (must follow this order)
```python
import engine.world_config as wc
wc.init(world_path)          # MUST be first — sets WORLD_NAME, SKILLS, CURRENCY_NAME, etc.
world = World(world_path)    # zone-streaming manager
player = Player.load(name)   # or Player.create_new(name)
world.attach_player(player)  # switches zone_state_dir to player's folder
processor = CommandProcessor(world, player, bus)
```

### Key modules and their roles
| Module | Role |
|--------|------|
| `engine/commands.py` | ~2500 lines; `CommandProcessor.process(raw)` is the main game loop entry. Holds `GameContext`, dispatches all verbs. |
| `engine/world_config.py` | Loads `config.toml`; exposes `WORLD_NAME`, `SKILLS`, `CURRENCY_NAME`, `DEFAULT_STYLE`, `VISION_THRESHOLD`, `EQUIPMENT_SLOTS`, `PLAYER_ATTRS`, `STATUS_EFFECTS`. |
| `engine/world.py` | Zone-streaming: loads zones on demand, evicts non-adjacent ones. `prepare_room(room_id, player)` is the main entry point. |
| `engine/script.py` | `ScriptRunner(ctx).run(ops)` — 63 ops. Combat-only ops require `GameContext.combat_ctx`. `fail` aborts cleanly. |
| `engine/combat.py` | `CombatSession(player, npc, bus, room, ctx)`. Call `player_attack()` once per round. `_run_passives()` executes TOML-driven passive abilities. |
| `engine/player.py` | `Player` dataclass; `player.inventory` contains ALL items including equipped ones (filter by `id(item)` to separate). `effective_light(player, room)` and `is_blind(player, room)` are module-level helpers. |
| `engine/dialogue.py` | `run_inline(npc, player, quests, ctx, bus, input_fn)`. Loads `dialogues/<npc_id>.toml`, evaluates conditions, runs scripts on response. |
| `engine/toml_io.py` | Custom TOML parser — always use this, never `tomllib`. |
| `wct/wct_server.py` | Lightweight HTTP server (port 7373) for the WCT browser tool. WCT-only routes; no game routes. |
| `wct/wct.html` + `wct/wct_common.css` | Single-page WCT app; all world editing in the browser. |
| `frontend/web_server.py` | Standalone HTTP server (port 7374) for the game web frontend; hosts `GameSession` + SSE stream. |
| `frontend/game.html` + `frontend/game.css` | Game web frontend; API paths are root-relative (`/login`, `/stream`, etc.). |

### Data layout
```
data/
  players/<name>/
    player.toml          # character save (cross-world; has world_id field)
    zone_state/          # per-player live zone snapshots (gitignored)
  <world_id>/
    config.toml          # world_name, skills, currency, [[player_attrs]], [[status_effect]]
    <zone_id>/
      rooms.toml / items.toml / npcs.toml
      dialogues/<npc_id>.toml
      quests/<quest_id>.toml
      crafting/<npc_id>.toml
      styles/styles.toml
```
Worlds and zones are discovered by folder scan — no registry to update.

### Status effects
`wc.STATUS_EFFECTS` dicts use `"label"` (not `"name"`) for the display name. Use `wc.get_status_effect(id)` to look up by id.

### Read-only commands
`_NO_TICK_COMMANDS` (frozenset at the top of `commands.py`) lists verbs that must not tick status effects or hazards (e.g. `look`, `inventory`, `map`, `stats`). Add new read-only verbs there.

### Adding a new script op
Add an `elif name == "my_op":` branch in `ScriptRunner._exec()` in `engine/script.py`, then document it in the module docstring's op table.

### WCT (World Creation Tool)
- Server: `wct/wct_server.py` on port 7373; serves `wct.html`; WCT API only (no game routes)
- Launch with `python launch_wct.py` (starts server in new window + opens browser)
- Zone collapse state, tag comments persisted in `localStorage` keyed by world ID
- `FLAG_MAP`: global `{flag_name: [usages]}` built by `buildXRef()` after world load
- `ALL_TAGS` / `rebuildTagPalette()`: collects unique tags from all NPCs/items for the tag palette

### Web frontend
- Server: `frontend/web_server.py` on port 7374; serves `game.html` + `game.css` + live play API
- Launch with `python launch_web.py` (starts server in new window + opens browser)
- `GameSession` runs the engine in a background thread; output flows via SSE at `/stream`
- API routes: `/login`, `/command`, `/quit`, `/stream`, `/status`, `/worlds`, `/players`, `/char_snapshot`

### Windows encoding
Box-drawing chars (U+2500+), ✓/✗, ⚠ crash on cp1252 consoles. Use ASCII equivalents in runtime-printed strings. Em-dash (U+2014) is safe.

### toml file updates
- Always reference wct/WORLD_MANUAL.md when updating toml files to ensure compliance syntax is utilized.

### After adding/updating/removing features
- Update validate.py - if the change alters how TOML files are written and/or interact with the engine
- Update the World Creation Tool (WCT) - if the change alters how TOML files are written, including scripting changes, new features, etc. 
- Update applicable README files - for the section of the code that was altered
- Update wct/WORLD_MANUAL.md - if the change alters how TOML files are written, including scripting changes, new features, etc.
- Update FRONTEND_MANUAL.md - if change alters how frontends need to be coded to interact with the engine