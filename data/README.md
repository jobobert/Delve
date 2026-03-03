# Delve Data Folder

World content lives entirely in TOML files under `data/`. No engine changes are
needed to add rooms, NPCs, items, quests, or dialogue — drop the files in and
restart.

A **world** is a subfolder of `data/` that contains a `config.py`. Worlds are
selected at startup; each player save records which world it belongs to.

For a full authoring reference (TOML formats, all script ops, condition keys,
etc.) see [WORLD_MANUAL.md](WORLD_MANUAL.md) and [engine/README.md](../engine/README.md).

---

## Directory structure

```
data/
├── players/                   Per-character folders (auto-created at runtime)
│   └── <name>/                One folder per character
│       ├── player.toml        Character save (cross-world; includes world_id field)
│       └── zone_state/        This player's live zone snapshots (gitignored)
│
└── <world_id>/                World folder — identified by the presence of config.py
    ├── config.py              World name, skills, currency, default style, equipment slots
    │
    ├── <zone_id>/             One folder per zone; folder name = zone ID
    │   ├── zone.toml          Optional zone metadata (display name, start room, …)
    │   ├── rooms.toml         [[room]] entries  (or split across multiple .toml files)
    │   ├── items.toml         [[item]] entries
    │   ├── npcs.toml          [[npc]] entries
    │   ├── dialogues/         <npc_id>.toml  — one file per NPC with a dialogue tree
    │   ├── quests/            <quest_id>.toml — one file per quest
    │   ├── crafting/          <npc_id>.toml  — crafting commission definitions
    │   ├── companions/        <companion_id>.toml — companion definitions
    │   └── styles/            <style_id>.toml — fighting style definitions
    │
    └── …                      Additional zone folders
```

> The engine discovers worlds by scanning `data/` for subfolders containing `config.py`.
> Within each world it discovers zones by subfolder presence. No registry to update.

---

## First World (`data/first_world/`)

The original development/test world. Used for engine development and testing.

**~57 rooms · 6 zones · 69 NPCs · 123 items · 7 fighting styles ·
34 dialogue trees · 12 quests · 9 crafting commissions · 3 companions**

| Zone | Description |
|------|-------------|
| `millhaven` | Starter hub — market town with bank, crafters, and inn |
| `training` | Barracks and training yard — spar system and style trainers |
| `greenhollow` | Valley with mining nodes and the Flowing Water trainer |
| `millbrook` | Farming village — ruffian quests and ore mining |
| `ashwood` | Forest quest zone with garrison ruins |
| `blackfen` | Swamp adventure — skill checks and multi-part mystery quest |

---

## The Sixfold Realms (`data/sixfold_realms/`)

New world — currently under construction. See `world_overview.md` in that
folder for the full backstory and design document.

---

## Data integrity

**Always run the validator after any data change:**

```bash
python tools/validate.py
```

Checks: required fields, exit targets, item refs in shops/scripts/rooms, NPC
styles, dialogue integrity (orphan nodes, dangling `next=` refs, NPC existence),
TOML syntax, `give_item`/`spawn_item` script refs, dialogue coverage (warns for
NPCs without any dialogue source).

Exit code `0` = passed (warnings are OK). Exit code `1` = errors found.

---

## Adding content

### New zone

1. Create `data/<world_id>/my_zone/` with at least a `rooms.toml` containing `[[room]]` entries
2. Connect it to the world by adding an exit in an existing room that points to
   one of the new zone's room IDs
3. Run `python tools/validate.py` to confirm everything resolves

No registry to update — the engine finds the new folder at next startup.

### New room

Add a `[[room]]` block to the zone's `rooms.toml` (or any `.toml` file in the
zone folder that the engine scans):

```toml
[[room]]
id          = "old_mill"
name        = "The Old Mill"
description = "A crumbling watermill. The wheel still turns."
flags       = ["no_combat"]
spawns      = ["miller_ghost"]
items       = ["rusty_key"]
exits       = { north = "millhaven_square", east = { to = "mill_cellar", locked = true, lock_tag = "mill_key" } }
on_enter    = [{ op = "message", tag = "system", text = "The air smells of damp wood." }]
```

**Required:** `id`, `name`, `description`

### New NPC

Add a `[[npc]]` block to the zone's `npcs.toml`:

```toml
[[npc]]
id          = "marsh_hermit"
name        = "The Marsh Hermit"
desc_short  = "An old man in waders who squints at you."
desc_long   = "He knows the mire by feel alone, every hummock and sinkhole."
hostile     = false
tags        = ["humanoid", "slow"]
style       = "brawling"
style_prof  = 10
attack      = 8
defense     = 3
hp          = 50
max_hp      = 50
xp_reward   = 0
gold_reward = 0
dialogue    = "Go away."      # fallback if no dialogues/<npc_id>.toml exists
```

- Hostile NPCs with no `dialogue` field and no dialogue file get an
  auto-generated brush-off line. The validator warns about both cases.
- For branching dialogue → create `data/<world_id>/<zone>/dialogues/<npc_id>.toml`
- For quest item acceptance → add `give_accepts = [...]`
- For kill rewards → add `kill_script = [...]`
- For mid-combat reactions → add `round_script = [...]` (see below)

**NPC tags** (used by fighting style matchups):
`humanoid` · `beast` · `undead` · `armored` · `fast` · `slow` · `large` · `small` · `group`

**Randomised spawn attributes** — Any of the following scalar fields can be written
as a TOML array. When the NPC spawns, one value is chosen at random from the array.
Each NPC instance in the room rolls independently, so a pack of wolves can produce
three different wolves from one template.

Eligible fields: `name` · `desc_short` · `desc_long` · `hp` · `max_hp` · `attack` ·
`defense` · `style` · `style_prof` · `xp_reward` · `gold_reward`

Fields that are intrinsically arrays (`tags`, `shop`, `give_accepts`, `kill_script`)
are never randomised.

```toml
[[npc]]
id         = "wolf"
name       = ["Grey Wolf", "Black Wolf", "Timber Wolf"]
desc_short = ["A lean grey wolf.", "A black wolf with amber eyes.", "A massive timber wolf."]
tags       = ["beast", "fast"]   # plain list — not randomised
style      = "brawling"
style_prof = 20
max_hp     = [18, 22, 28]        # omit hp → setdefault syncs hp = max_hp
attack     = [5, 6, 7]
defense    = [2, 3, 3]
xp_reward  = [8, 10, 14]
gold_reward = 0
hostile    = true
```

> **Tip:** Omit `hp` and list only `max_hp`. The engine sets `hp = max_hp` at spawn,
> keeping the two in sync. If you list both separately they randomise independently.

**`round_script`** — Script array that runs after each combat round where both
combatants are still alive. Use with `if_combat_round` and `if_npc_hp` to create
bosses that react dynamically during a fight.

```toml
round_script = [
    { op = "if_combat_round", min = 5, then = [
        { op = "if_npc_hp", max = 10, then = [
            { op = "say",       text = "Enough! You win — take this and leave me." },
            { op = "give_item", item_id = "boss_key" },
            { op = "end_combat" }
        ] }
    ] }
]
```

### New item

Add a `[[item]]` block to the zone's `items.toml`:

```toml
[[item]]
id         = "iron_sword"
name       = "Iron Sword"
desc_short = "A plain iron sword, well-balanced."
slot       = "weapon"
weight     = 3
tags       = ["sword"]
attack     = 4
```

**Required:** `id`, `name`, `desc_short`, `slot`, `weight`

**Equipment slots:** `weapon` · `head` · `chest` · `legs` · `arms` · `armor` ·
`pack` · `ring` · `shield` · `cape`

Use `slot = ""` for non-equippable items (consumables, quest items, resources).

**Useful optional fields:**

| Field | Purpose |
|-------|---------|
| `scenery = true` | Cannot be picked up; stays in room |
| `respawn = true` | Reappears each zone load |
| `no_drop = true` | Cannot be dropped; excluded from death corpse |
| `key_tag = "tag"` | Unlocks exits with matching `lock_tag` |
| `on_get = [...]` | Script ops run when the item is picked up |
| `on_use = [...]` | Script ops run when the item is used |
| `on_hit = [...]` | Script ops run when the item hits in combat |
| `effects = [...]` | Passive stat effects while equipped |

**Ore node example** (scenery item with mining script):

```toml
[[item]]
id         = "iron_ore_vein"
name       = "Iron Ore Vein"
desc_short = "A seam of dark reddish-brown ore."
slot       = ""
weight     = 0
scenery    = true
respawn    = true
on_get = [
  { op = "require_tag", tag = "pickaxe",
    fail_message = "You need a pickaxe." },
  { op = "skill_check", skill = "mining", dc = 8,
    on_pass = [
      { op = "give_item",  item_id = "iron_ore" },
      { op = "give_item",  item_id = "iron_ore" },
      { op = "skill_grow", skill = "mining", amount = 3 },
    ],
    on_fail = [
      { op = "give_item",  item_id = "iron_ore" },
      { op = "skill_grow", skill = "mining", amount = 1 },
    ],
  },
]
```

### New quest

Create `data/<world_id>/<zone>/quests/<quest_id>.toml`:

```toml
id      = "the_lost_ring"
title   = "The Lost Ring"
giver   = "marsh_hermit"
summary = "Find the hermit's lost signet ring."

[[step]]
index     = 1
objective = "Search the mire for the signet ring."

[[step]]
index     = 2
objective = "Return the ring to the hermit."
```

Advance quest progress via script ops in dialogue or kill scripts:

```toml
{ op = "advance_quest",  quest_id = "the_lost_ring", step = 1 }
{ op = "complete_quest", quest_id = "the_lost_ring" }
```

### New dialogue tree

Create `data/<world_id>/<zone>/dialogues/<npc_id>.toml`. Every tree needs a node with
`id = "root"`.

**Nested format** (responses inside the node block):

```toml
[[node]]
id    = "root"
lines = ["Hello!", "Welcome, traveller!"]   # random pick each visit
# OR: line = "Hello!"                       # single fixed line

  [[node.response]]
  text = "What do you know about the forest?"
  next = "about_forest"
  condition = { flag = "quest_started" }
  script = [{ op = "set_flag", flag = "asked_forest" }]

  [[node.response]]
  text = "Goodbye."
  next = ""                                 # "" ends the conversation
```

**Flat format** (useful when a node has many responses):

```toml
[[node]]
id   = "root"
line = "What do you need?"

[[response]]
node = "root"
text = "I need a guide."
next = "guide_offer"

[[response]]
node = "root"
text = "Never mind."
next = ""
```

Both formats can coexist in the same file.

**Dialogue substitution tokens** — available in any `line` or `lines` string:

| Token | Resolves to |
|-------|-------------|
| `{player}` / `{player_name}` | The player character's name |
| `{npc}` / `{npc_name}` | This NPC's name |
| `{gold}` | Player's current gold |
| `{hp}` | Player's current HP |
| `{level}` | Player's current level |
| `{zone}` | Current zone id |
| `{entity_id.field}` | Any field from an NPC, item, or quest template looked up by id |

The `{entity_id.field}` form resolves in order: NPC → item → quest. Any TOML field
is accessible (`name`, `desc_short`, `title`, `hp`, …). Returns an empty string if
the entity or field is not found.

```toml
# Example — reference another NPC's name so renames stay in sync
line = "Find {guard_captain.name} at the garrison. Their {iron_sword.name} is legendary."
```

### New crafting commission

Create `data/<world_id>/<zone>/crafting/<npc_id>.toml`:

```toml
[[commission]]
id          = "iron_sword_standard"
name        = "Iron Sword"
description = "A plain iron sword."
delay       = 3          # turns to complete

[[commission.tier]]
name    = "standard"
result  = "iron_sword"
materials = [
  { item_id = "iron_ore", qty = 2 },
  { item_id = "wood_handle", qty = 1 },
]
```

### New companion

Create `data/<world_id>/<zone>/companions/<companion_id>.toml`. See existing companion
files for the full format. Three tiers: `narrative`, `utility`, `combat`.
Grant via `{ op = "give_companion", companion_id = "..." }` in a dialogue script.

---

## TOML conventions

### Custom TOML extensions

The engine uses its own TOML parser (`engine/toml_io.py`) that extends the spec:

- **Multi-line inline tables** — used for exit definitions with lock data
- **Triple-quoted strings** — for dialogue text containing embedded quotes

Standard tools (`tomllib`, `tomli`, most linters) will reject these. Always
parse data files with `from engine.toml_io import load`.

### Exit formats

```toml
# Simple exit
exits = { north = "town_square" }

# Locked door
exits = { east = { to = "armory", locked = true, lock_tag = "garrison_lock" } }

# With description
exits = { west = { to = "cave_entrance", desc = "A narrow crack in the cliff face." } }
```

**Door script events** — Any door-dict exit can carry script arrays for five events:

| Field | Fires when… | Player is in… |
|-------|-------------|---------------|
| `on_unlock` | Player unlocks the door | Source room |
| `on_lock` | Player locks the door | Source room |
| `on_exit` | Player passes through (before moving) | Source room |
| `on_enter` | Player passes through (after arriving) | Destination room |
| `on_look` | Player types `look <direction>` at the door | Source room |

```toml
exits = { north = {
    to        = "great_hall",
    locked    = true,
    lock_tag  = "hall_key",
    desc      = "a heavy iron door",
    on_unlock = [{ op = "message", tag = "system", text = "The lock booms open." }],
    on_look   = [{ op = "say",    text = "Through the gap you glimpse torchlit vaulting." }],
    on_enter  = [{ op = "set_flag", flag = "entered_great_hall" }],
} }
```

### Room flags

| Flag | Effect |
|------|--------|
| `safe_combat` | Player takes zero damage (training rooms) |
| `no_combat` | Attack command blocked entirely |
| `healing` | Player regenerates HP while in this room |
| `town` | Auto-sets player bind point on first entry |
| `reduced_stats` | Both sides use half attack/defense |

---

## Runtime data

### Zone state (`data/players/<name>/zone_state/`)

Zone state is per-player. When a zone is evicted from memory, live NPC HP and
current room item lists are written to `data/players/<name>/zone_state/<zone_id>.json`.
On next load the sidecar is applied over fresh TOML so the world remembers what
that player did — independently of any other player session.

Delete these files (or run `python tools/clean.py --state`) to reset a player's
world state to its authored state.

### Player saves (`data/players/<name>/`)

Each character occupies a folder. `player.toml` is written by `player.save()`.
Saves are cross-world — each records a `world_id` field so the engine knows which
world to load. On first run after an upgrade, old flat `data/players/<name>.toml`
files are automatically migrated into the new folder layout.

Delete a player folder (or run `python tools/clean.py --players`) to wipe characters.

---

## Tools

```bash
# Data integrity — run after every change
python tools/validate.py

# Maps
python tools/map.py                          # ASCII map, all zones
python tools/map.py --zone <zone_id>         # single zone
python tools/map.py --full                   # with NPC/item counts
python tools/map.py --html                   # HTML map → tools/admin_map.html
python tools/map.py --html --output my.html  # HTML map to custom path
python tools/map.py --world <name>           # select world by folder name

# World Creation Tool — browser-based TOML editor with map and dialogue graph
python tools/wct_server.py                   # → http://localhost:7373  (no auto-open)
python tools/wct_server.py --browser         # also open browser

# Reset
python tools/clean.py --all                  # wipe players, zone state, and caches
python tools/clean.py --state                # zone state only
python tools/clean.py --players              # player saves only
```
