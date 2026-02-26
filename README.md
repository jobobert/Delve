# Delve

A modular, zero-dependency Python 3.10+ MUD engine with TOML-based world data.

Delve is built around two principles:

1. **Everything is data.** Rooms, NPCs, items, quests, dialogue, fighting styles,
   skills, and script logic live in human-readable TOML files. No code changes
   needed to add content.

2. **Engine and frontend are decoupled.** The engine never calls `print()` or reads
   `stdin`. All output flows through a typed `EventBus` as semantic `Msg` objects.

**World:** 57 rooms · 6 zones · 69 NPCs · 123 items · 7 fighting styles ·
34 dialogue trees · 12 quests · 9 crafting commissions · 3 companions

---

## Directory structure

```
mud/
├── engine/
│   ├── commands.py         Command parser and dispatcher (~2 500 lines)
│   ├── combat.py           Turn-based combat, style passives, spar system
│   ├── companion.py        Companion system (narrative / utility / combat tiers)
│   ├── dialogue.py         Branching dialogue engine + auto-generated brush-off fallbacks
│   ├── events.py           Lightweight publish/subscribe event bus
│   ├── log.py              Structured debug logger (writes delve.log)
│   ├── msg.py              Msg dataclass + Tag constants
│   ├── player.py           Player state, persistence, item-effect helpers
│   ├── prestige.py         Reputation system (−999…+999, 10 tiers, NPC reactions)
│   ├── quests.py           Quest tracking, journal, step rewards
│   ├── room_flags.py       Room flag constants (safe_combat, no_combat, healing, …)
│   ├── script.py           Script interpreter — 37 ops, clean abort via fail/require_tag
│   ├── skills.py           Seven adventuring skills (0–100, grow through use)
│   ├── styles.py           Fighting style system (matchups, gear affinity, passives)
│   ├── toml_io.py          Zero-dependency TOML reader/writer (engine extension superset)
│   └── world.py            Zone-streaming world manager (lazy load/evict)
│
├── frontend/
│   ├── cli.py              ANSI colour CLI with word-wrap and auto-attack loop
│   └── config.py           All tunables: wrap width, auto-attack, colours, logging
│
├── data/                   Contains the details of the world (see data/README.md for more info)
│
├── tools/
│   ├── validate.py         Data integrity checker — TOML syntax, refs, dialogue coverage
│   ├── map.py              ASCII map generator (all zones or single zone)
│   ├── gen_map.py          Interactive HTML map generator → tools/admin_map.html
│   ├── wct_server.py       World Creation Tool — browser-based TOML editor
│   ├── ai_player.py        Autonomous AI playtester (requires ANTHROPIC_API_KEY)
│   └── clean.py            Reset helper: player saves, zone state, caches
│
├── main.py
├── config.py               Root-level re-export of frontend/config.py
├── requirements.txt        (empty — zero external dependencies)
└── README.md
```

---

## Quick start

```bash
python main.py
```

New players start in Millhaven's Town Square. Type `help` for commands, or
`talk mira` in the Council Hall to begin the tutorial quest.

---

## Systems

### Zone-first architecture (world.py)

Each zone is a folder under `data/` — rooms, items, NPCs, quests, and dialogues
all colocated. Each zone can be created and modified independently, though objects can interact between zones.

### Dialogue system (dialogue.py)

A branching dialogue system provides a realistic NPC interaction experience.

### Skill system (skills.py)

The Skill System provides a framework for non-combat skill testing.

### Prestige system (prestige.py)

The Prestige provides a framework for informing the world about the choices a player makes, providing bonuses or challenges depending on the player's actions.

### Script engine (script.py)

The Script Engine provides a lightweight system for automating tasks based on engine events, such as `on_get`.

### Fighting styles (styles.py)

Fighting styles provide combat modifiers with gear affinity bonuses and passive abilities
that unlock at proficiency thresholds.

### Logging (log.py)

Multiple system events can be logged to `delve.log` with full detail. Configured in `frontend/config.py`:

```python
LOG_ENABLED    = True            # master switch (True by default)
LOG_FILE       = "delve.log"     # relative to mud/ root
LOG_LEVEL      = "DEBUG"         # DEBUG | INFO | WARN
LOG_CATEGORIES = ["combat", "autoattack", "dialogue"]
```

Available categories: `combat`, `autoattack`, `dialogue`, `script`, `world`,
`player`, `command`. Paste any `CMD:` section from the log into chat to debug.

### Mining system

Mining yields ores, which are useful for crafting, and requires appropriate skill and tooling.

### Spar system

Enables NPCs to be attacked without actually being able to be killed. Useful especially for quests.

### Death and respawn

On death: a timed corpse spawns in the death room containing dropped
items (except gold, which is lost); player respawns at their bound room at full HP. XP debt blocks new XP accrual until repaid through kills. Bind point auto-sets on first entry to any town.

### Bank system

Global account accessible from any banker. Players start with 10 slots and can acquire more.

### Equipment slots (10)

Only equipped items can be used and may impact stats. 
Equipped items do not count toward carry weight. 
Equipping or unequipping in a room with hostile NPCs may trigger a free opportunistic attack.

### Crafting commissions

NPCs with crafting definitions accept materials and produce quality-tiered items
after a turn-based delay. Four tiers: poor / standard / exceptional / masterwork.

Commission states: `waiting_materials` → `in_progress` → `ready`.

### Auto-attack

Frontend-controlled ability to automate attacks against an opponent.

### Companion system

Three tiers:

- **Narrative** — story presence only
- **Utility** — special abilities usable in exploration
- **Combat** — attacks once per round alongside the player; NPCs have a 30% chance
  per round to strike the companion instead

Only one companion active at a time. Acquired through quests and dialogue.

### Doors and keys

Doors can be bi-directional, mono-directional, and be locked

Multiple doors can share the same key, multiple keys can open the same door

### Alias system

Enables players to customize shorthand commands to meet their needs

Startup aliases (diagonals, named exits) live in `frontend/config.py →
STARTUP_ALIASES`. Character aliases saved per-player take priority on conflict.

---


## Tools

```bash
# World Creation Tool — browser-based TOML editor with visual dialogue node graph
python tools/wct_server.py                   # → http://localhost:7373
python tools/wct_server.py --port 8080
python tools/wct_server.py --no-browser

# Data integrity
python tools/validate.py

# Maps
python tools/map.py                          # ASCII map, all zones
python tools/map.py --zone greenhollow
python tools/map.py --full                   # with item/NPC counts
python tools/gen_map.py                      # → tools/admin_map.html (interactive)

# AI playtester (requires ANTHROPIC_API_KEY)
python tools/ai_player.py play
python tools/ai_player.py play --goal "Complete the Groundhog King quest" --verbose
python tools/ai_player.py analyse

# Maintenance
python tools/clean.py                        # interactive reset menu
python tools/clean.py --all
python tools/clean.py --cache / --state / --players
```

---

## In-game commands

| Category | Command | Description |
|---|---|---|
| **Navigation** | `north`/`n`, `south`, `east`, `west`, `up`, `down` | Move |
| | `ne`, `nw`, `se`, `sw` | Diagonal move |
| | `climb`, `enter`, `cross`, `swim`, … | Named exits |
| | `look` / `l` | Describe current room |
| | `look at <target>` | Examine item or NPC |
| **Character** | `inventory` / `i` / `status` | Stats, gear, carry weight |
| | `skills` | Skill levels and tiers |
| | `get <item>` | Pick up item |
| | `drop <item>` | Drop item |
| | `equip <item>` / `unequip <item>` | Wear / remove gear |
| | `use` / `drink <item>` | Use a consumable |
| **Combat** | `attack <npc>` / `kill` | Attack an NPC |
| | `autoattack` / `aa` / `auto` | Toggle auto-attack loop |
| | `style` | Show active style and proficiencies |
| | `style <n>` | Switch fighting style |
| | `learn <style>` | Learn from a trainer NPC |
| **Doors** | `unlock <dir>` / `lock <dir>` | Open / close locked doors |
| **Commerce** | `list` / `buy <item>` / `sell <item>` | Shop |
| | `sleep` / `rest` | Rest at inn |
| | `commission <npc>` | List crafting commissions |
| | `commissions` | Active jobs with material status |
| | `give <item> <npc>` | Submit materials or quest items |
| | `collect <npc>` | Pick up finished commission |
| **Bank** | `deposit <item>` / `deposit gold <N>` | Store |
| | `withdraw <item>` / `withdraw gold <N>` | Retrieve |
| | `bank` | Balance view |
| | `upgrade [confirm]` | Expand slot capacity |
| **Quests** | `journal` / `j` | Quest log with commission status |
| | `talk <npc>` | Branching dialogue |
| **World** | `map` | Fog-of-war ASCII map |
| | `alias <n> <cmd>` / `unalias` / `aliases` | Shorthand commands |
| | `save` / `quit` | Save and exit |

---

## Architecture notes

### EventBus (events.py)

All output is `Msg(tag, text)` on `Event.OUTPUT`. The frontend subscribes and
renders by tag. Adding a new frontend (web client, etc.) requires subscribing to
the same bus — zero engine changes needed.

### Tag system (msg.py)

Every output line is tagged: `ROOM_NAME`, `ROOM_DESC`, `NPC`, `NPC_LOOK`,
`ITEM`, `COMBAT_HIT`, `COMBAT_RECV`, `COMBAT_KILL`, `COMBAT_DEATH`, `REWARD_XP`,
`REWARD_GOLD`, `DIALOGUE`, `SYSTEM`, `ERROR`, `STATS`, `QUEST`, `SHOP`,
`STYLE`, `MAP`, `BLANK`, and others. Override colours in `frontend/config.py →
COLOR_OVERRIDES`.

### TOML engine extension (toml_io.py)

The engine uses its own TOML reader that extends the spec with multi-line inline
tables (used for exit definitions with lock data) and triple-quoted strings (for
dialogue text with embedded quotes). Standard `tomllib` / `tomli` cannot parse
these files.

---

## Development workflow

```bash
python main.py                   # normal play session

python tools/validate.py         # run after any data change

python tools/map.py --full       # quick zone overview
python tools/gen_map.py          # interactive HTML map (opens browser)

# Debugging a bug:
# 1. LOG_ENABLED = True in frontend/config.py  (already True by default)
# 2. Reproduce the bug
# 3. Find the relevant CMD: section in delve.log
# 4. Paste into chat

python tools/clean.py --all      # full reset (wipes saves and zone state)
```

Requires Python 3.10+. Zero external dependencies.
