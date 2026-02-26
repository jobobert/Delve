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

**Current world stats:** 57 rooms · 6 zones · 69 NPCs · 123 items · 7 fighting
styles · 34 dialogue trees · 12 quests · 9 crafting commissions · 3 companions.

---

## How to get the code

The project is extracted as `mud/` in the working directory. The layout is:

```
mud/
├── engine/          Core systems (combat, dialogue, world, script, …)
├── frontend/        CLI renderer + config
├── data/            All TOML world data (zones, items, NPCs, quests, dialogues)
├── tools/           validate.py, map.py, wct_server.py, ai_player.py, clean.py
├── main.py
├── README.md        Full system reference — READ THIS for any system details
└── HANDOFF.md       This file
```

**Always read `README.md` for authoritative details** on any system before
touching code. It reflects the actual current state of every file.

---

## Key architecture facts

### Engine modules

| File | Role |
|------|------|
| `engine/world.py` | Zone-streaming manager. `world.prepare_room(room_id, player)` loads a zone if needed. `world.npcs` and `world.items` are flat dicts of all known entities (templates). Live room state (including `_npcs` list) lives on the room dict itself. |
| `engine/commands.py` | ~2500-line command dispatcher. `CommandProcessor.process(raw)` is the main entry point. `_resolve_npc(target)` finds a live NPC in the current room (filters `hp > 0`). |
| `engine/combat.py` | `CombatSession(player, npc, bus, room, ctx)`. Call `player_attack()` once per turn. Fully instrumented with `engine/log.py` at DEBUG level. |
| `engine/dialogue.py` | `run_inline(npc, player, quests, ctx, bus, input_fn)`. Loads tree from `data/<zone>/dialogues/<npc_id>.toml` or falls back to `npc["dialogue"]` string or auto-generates a brush-off line. |
| `engine/script.py` | `ScriptRunner(ctx).run(ops)`. 37 ops. `fail` aborts cleanly. `require_tag` gates on item tags. |
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
- Should have `coord = [x, y]` (east=+x, north=+y) — validator warns if missing
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

**Script ops** — all 37:
`say`, `message`, `set_flag`, `clear_flag`, `give_gold`, `take_gold`, `give_xp`,
`heal`, `set_hp`, `damage`, `give_item`, `take_item`, `spawn_item`,
`advance_quest`, `complete_quest`, `teach_style`, `unlock_exit`, `lock_exit`,
`skill_check`, `if_skill`, `skill_grow`, `apply_status`, `clear_status`,
`if_status`, `prestige`, `add_affinity`, `remove_affinity`, `if_prestige`,
`if_affinity`, `give_companion`, `dismiss_companion`, `bank_expand`,
`if_flag`, `if_not_flag`, `if_item`, `if_quest`, `if_quest_complete`, `fail`,
`require_tag`

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
- Zone state (NPC HP, room items) persists in `data/zone_state/<zone_id>.json`
- Player saves in `data/players/<name>.json`
- `tools/clean.py --all` resets everything for a fresh test

---

## Known state / recent work

The most recent session addressed:

1. **Engine logging system** (`engine/log.py`) — structured per-category debug
   logger. Wired into combat (full round-by-round), auto-attack loop, and
   dialogue resolution. `LOG_ENABLED = True` by default.

2. **Dialogue fallback brush-offs** (`engine/dialogue.py`) — NPCs with no
   dialogue tree and no `dialogue` field now emit a random flavoured line instead
   of a blank. Hostile pool (8 lines) and friendly pool (7 lines). Validator
   warns for all NPCs lacking any dialogue source.

3. **`run_inline` loop fix** — single-line fallback sessions (done=True on init)
   now always emit their output. Previously the `while not session.done` guard
   prevented the first output from being emitted when the session was immediately
   complete.

4. **Room coordinates** — all rooms in `greenhollow` (12) and `millbrook` (14)
   now have `coord = [x, y]` fields. Validator warns on missing coords.

5. **Validator enhancements** — warns for missing coords, warns for NPCs without
   any dialogue source (hostile and friendly separately), TOML file inventory
   table, dialogue coverage report.

---

## Tools reference

```bash
python main.py                        # play the game
python tools/validate.py              # check data integrity (run after every edit)
python tools/map.py [--zone Z] [--full]   # ASCII map
python tools/gen_map.py               # interactive HTML map → tools/admin_map.html
python tools/wct_server.py            # browser TOML editor → http://localhost:7373
python tools/ai_player.py play [--goal "..."] [--verbose]
python tools/clean.py [--all | --cache | --state | --players]
```
