# Delve — World Programming Manual

> **Audience:** World authors building zones, rooms, NPCs, items, dialogue, and quests.
> No Python knowledge required. Everything is TOML data files.

---

## Contents

1. [Project Overview](#1-project-overview)
2. [Zone Structure & File Layout](#2-zone-structure--file-layout)
3. [Rooms](#3-rooms)
4. [Exits & Doors](#4-exits--doors)
5. [NPCs](#5-npcs)
6. [Items](#6-items)
7. [The Script Engine](#7-the-script-engine)
8. [The Dialogue System](#8-the-dialogue-system)
9. [The Quest System](#9-the-quest-system)
10. [Skills](#10-skills)
11. [Status Effects](#11-status-effects)
12. [Prestige & Reputation](#12-prestige--reputation)
13. [Fighting Styles](#13-fighting-styles)
14. [Crafting & Commissions](#14-crafting--commissions)
15. [Companions](#15-companions)
16. [Validation Tool](#16-validation-tool)
17. [Light Mechanic](#17-light-mechanic)
18. [World-Defined Player Attributes](#18-world-defined-player-attributes)
19. [Standalone Script Files](#19-standalone-script-files)
20. [Appendix A — Room Flags](#appendix-a--room-flags)
21. [Appendix B — Equipment Slots](#appendix-b--equipment-slots)
22. [Appendix C — All Script Ops (Quick Reference)](#appendix-c--all-script-ops-quick-reference)
23. [Appendix D — Player Fields for Substitution](#appendix-d--player-fields-for-substitution)

---

## 1. Project Overview

Delve is a text MUD engine built around one principle: **everything is data**. You never
touch Python to create content. Rooms, NPCs, items, dialogue, and quests are all defined
in TOML files under `data/`. The engine loads them at runtime.

**Key facts:**
- A **world** is a subfolder of `data/` that contains a `config.py` (e.g. `data/sixfold_realms/`).
- **Zones** are subfolders of the world folder. Each zone streams in/out of memory independently.
- All NPC and item state is lazy — NPCs spawn on first player visit, not at startup.
- Zone state (NPC HP, moved items) persists per-player to `data/players/<name>/zone_state/<zone_id>.json`.
- Zero external dependencies. Run with `python main.py`.

**Test your work** with `python tools/validate.py` before playing.

---

## 2. Zone Structure & File Layout

A **world** is a subfolder of `data/` identified by a `config.py` inside it. **Zones** are
subfolders of the world. The zone folder's name becomes the zone ID.

```
data/
  players/                     # Per-character folders (gitignored)
    <name>/
      player.toml              # Character save (cross-world; records world_id)
      zone_state/              # Live zone snapshots — per-player (gitignored)
        <zone_id>.json
  <world_id>/                  # World folder — identified by config.py
    config.py                  # World name, skills, currency, default style, slots
    <zone_id>/                 # One folder per zone; folder name = zone ID
      zone.toml                # Optional: display name and description
      rooms.toml               # [[room]] entries
      items.toml               # [[item]] entries
      npcs.toml                # [[npc]] entries
      dialogues/
        <npc_id>.toml          # Dialogue tree for one NPC
      quests/
        <quest_id>.toml        # Quest definition
      crafting/
        <npc_id>.toml          # Commission defs for one NPC
      companions/
        <companion_id>.toml
```

### World configuration (config.toml)

Every world folder must contain a `config.toml`. The engine reads it at startup and uses
its values to configure world-specific behaviour:

```toml
world_name        = "Delve"
currency_name     = "gold"
new_char_hp       = 100
default_style     = "brawling"
vision_threshold  = 3           # default player vision threshold (see §17 Light Mechanic)

equipment_slots   = ["weapon", "head", "chest", "legs", "arms",
                     "pack", "ring", "shield", "cape"]

[skills]
stealth    = "Stealth"
survival   = "Survival"
perception = "Perception"
athletics  = "Athletics"
social     = "Social"
arcana     = "Arcana"
mining     = "Mining"

# Optional: world-defined numeric player attributes (see §18 Player Attributes)
# [[player_attrs]]
# id      = "corruption"
# min     = 0
# max     = 100
# default = 0
# display = "bar"          # "bar" shows as [####....] or "number" shows as raw int
```

| Field | Type | Description |
|-------|------|-------------|
| `world_name` | str | Display name shown in menus and the map header |
| `currency_name` | str | Display name for gold (e.g. `"credits"`, `"marks"`) |
| `new_char_hp` | int | Starting HP for every new character |
| `default_style` | str | Fighting style ID every new character starts with |
| `vision_threshold` | int | Effective light level below which a new character is blind |
| `equipment_slots` | list | Ordered list of valid equipment slot names |
| `[skills]` | table | `skill_id = "Display Name"` pairs for skills in this world |
| `[[player_attrs]]` | array | World-defined custom numeric player attributes (optional) |

The engine falls back to built-in defaults for any field that is absent.

> **Legacy:** The engine also accepts a `config.py` file with Python constants (`WORLD_NAME`,
> `SKILLS`, etc.) for backward compatibility, but `config.toml` is the standard format.
> The validator will emit a migration warning for worlds still using `config.py`.

### zone.toml (optional)

```toml
id          = "blackfen"
name        = "Blackfen Mire"
description = """
A vast, poisonous wetland stretching south of the Millhaven road...
"""
```

The engine discovers zones by folder presence. The zone.toml is advisory/flavor only.

### Zone loading rules

1. Zones load on demand when the player first enters a room in that zone.
2. Only the current zone and directly adjacent zones remain in RAM.
3. Zones evict from memory when the player moves away.
4. **Delete `data/players/<name>/zone_state/` files** (or run `tools/clean.py --state`) to reset
   a player's world state to its authored values.

---

## 3. Rooms

Rooms are defined in `[[room]]` blocks inside a zone's `rooms.toml`.

### 3.1 All Room Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `id` | string | **Yes** | — | Unique room identifier across the entire world |
| `name` | string | **Yes** | — | Short display name (shown as room title) |
| `description` | string | **Yes** | — | Room description shown on entry and `look` |
| `desc_long` | string | No | falls back to `description` | Extended description; shown with `examine room` |
| `coord` | [x, y] | No | — | Optional map hint: east = +x, north = +y. If present, pins the room's position on the admin map. If absent, the engine auto-places the room using exit topology. Not required and not validated. |
| `exits` | dict | No | `{}` | See [Section 4](#4-exits--doors) |
| `items` | list | No | `[]` | Item IDs present in the room on zone load |
| `spawns` | list | No | `[]` | NPC IDs (or spawn dicts) to populate on first visit |
| `flags` | list | No | `[]` | Room behaviour flags — see [Appendix A](#appendix-a--room-flags) |
| `start` | bool | Zone needs one | `false` | Marks the starting room; validator requires exactly one per world |
| `on_enter` | array | No | `[]` | Script ops run each time the player enters the room |
| `heal_rate` | int | No | `5` | HP restored per command (only when `healing` flag set) |
| `hazard_damage` | int | No | `2` | Passive HP damage per command (only when `hazard` flag set) |
| `hazard_message` | string | No | `"The environment bites at you."` | Flavour text for each hazard tick |
| `hazard_exempt_flag` | string | No | `""` | Player flag that bypasses hazard damage (e.g. `"has_mining_gear"`) |
| `admin_comment` | string | No | — | Developer notes — ignored by the engine |

### 3.2 Minimal Room Example

```toml
[[room]]
id          = "town_square"
name        = "Millhaven Town Square"
description = "A cobbled square ringed by timber-framed buildings."
flags       = ["town", "no_combat"]
exits       = { north = "barracks", east = "market_row", south = "south_gate" }
spawns      = ["town_guard", "wandering_merchant"]
items       = ["notice_board"]
```

### 3.3 Room with Scripts and Flags

```toml
[[room]]
id             = "poison_swamp"
name           = "Murk Fens"
description    = "Thick, rotten-smelling water comes up to your shins."
flags          = ["hazard"]
hazard_damage  = 3
hazard_message = "The caustic water bites at your legs."
hazard_exempt_flag = "has_waders"
exits          = { north = "dry_causeway" }
on_enter       = [
  { op = "if_not_flag", flag = "swamp_warned", then = [
    { op = "message", tag = "system",
      text = "A local warning: 'Don't linger in the water.'" },
    { op = "set_flag", flag = "swamp_warned" },
  ] },
]
```

### 3.4 Spawn Definitions

Spawns can be simple NPC ID strings or dicts with options:

```toml
# Simple: one instance of each
spawns = ["goblin_scout", "goblin_archer"]

# Dict form: options per spawn
spawns = [
  { id = "goblin_scout",  count = 3 },
  { id = "goblin_chief",  spawn_chance = 0.5 },   # 50% chance to appear
]
```

| Spawn field | Type | Default | Description |
|-------------|------|---------|-------------|
| `id` | string | **required** | NPC template ID |
| `count` | int | `1` | Number of copies to spawn |
| `spawn_chance` | float | `1.0` | Probability 0–1 that this NPC spawns |

---

## 4. Exits & Doors

Exits connect rooms. Each entry in `exits` maps a direction name to a destination.

### 4.1 Plain-string Exit (simplest)

```toml
exits = { north = "town_square", east = "market_row" }
```

The value is the destination room's `id`. Any string is a valid direction name
(`north`, `south`, `east`, `west`, `up`, `down`, `northeast`, `enter`, `climb`, etc.).

### 4.2 Door Object (dict exit)

Use a dict when you need locking, scripts, or a conditional:

```toml
exits = {
  east = {
    to        = "barracks_storeroom",
    locked    = true,
    lock_tag  = "barracks_lock",
    desc      = "a heavy iron door",
  }
}
```

### 4.3 All Exit Dict Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `to` | string | **required** | Destination room ID |
| `desc` | string | `""` | Door description shown when player examines the exit |
| `locked` | bool | `false` | Whether the door starts locked |
| `lock_tag` | string | `""` | Tag matched against item `key_tag` field for unlock |
| `show_if` | condition dict | none | Exit is invisible and impassable unless condition passes |
| `on_look` | script array | `[]` | Script run when player examines this exit (`look north`) |
| `on_exit` | script array | `[]` | Script run in the **source** room before the player leaves |
| `on_enter` | script array | `[]` | Script run in the **destination** room after the player arrives |
| `on_unlock` | script array | `[]` | Script run when a player successfully unlocks this door |
| `on_lock` | script array | `[]` | Script run when a player successfully locks this door |

### 4.4 Full Door Example

```toml
exits = {
  north = {
    to        = "great_hall",
    locked    = true,
    lock_tag  = "hall_key",
    desc      = "a towering oak door banded with iron",
    on_unlock = [{ op = "say", text = "The lock clicks with a resonant boom." }],
    on_lock   = [{ op = "say", text = "The bolt slides home." }],
    on_exit   = [{ op = "message", tag = "system", text = "You step through the archway." }],
    on_enter  = [{ op = "set_flag", flag = "entered_great_hall" }],
    on_look   = [{ op = "say", text = "Through the gap you glimpse a vast torchlit hall." }],
  }
}
```

### 4.5 Conditional Exits (`show_if`)

A conditional exit is **completely invisible** to the player unless the condition passes.
The exit does not appear in the "Exits:" list, cannot be traversed, and returns
"There is nothing to the…" when examined.

```toml
exits = {
  east = {
    to      = "hidden_grotto",
    show_if = { op = "min_skill", skill = "perception", value = 70 },
  }
}
```

**Supported `show_if` ops:**

| op | Required fields | Passes when |
|----|-----------------|-------------|
| `has_flag` | `flag` | player has the named flag |
| `not_flag` | `flag` | player does NOT have the named flag |
| `min_level` | `level` | player.level ≥ level |
| `has_item` | `item_id` | item is in inventory or equipped |
| `min_skill` | `skill`, `value` | player.skills[skill] ≥ value |

Unknown `op` values default to **true** (visible), so old world data is forward-compatible.

**Examples:**

```toml
# Revealed when a quest flag is set
show_if = { op = "has_flag", flag = "spoke_to_watchman" }

# Hidden from low-level players
show_if = { op = "min_level", level = 10 }

# Requires item in inventory or equipped
show_if = { op = "has_item", item_id = "royal_seal" }

# High-perception secret passage
show_if = { op = "min_skill", skill = "perception", value = 60 }
```

---

## 5. NPCs

NPCs are defined in `[[npc]]` blocks in a zone's `npcs.toml`.

### 5.1 All NPC Fields

| Field | Type | Required | Randomizable | Description |
|-------|------|----------|--------------|-------------|
| `id` | string | **Yes** | No | Unique NPC identifier |
| `name` | string / list | **Yes** | **Yes** | Display name |
| `desc_short` | string / list | **Yes** | **Yes** | One-line description (shown in room) |
| `desc_long` | string / list | **Yes** | **Yes** | Full description (shown with `examine <npc>`) |
| `tags` | list | **Yes** | No | Gameplay tags; see [Section 5.4](#54-npc-tags) |
| `style` | string / list | **Yes** | **Yes** | Fighting style ID |
| `style_prof` | float / list | **Yes** | **Yes** | Style proficiency 0–100 |
| `hp` | int / list | **Yes** | **Yes** | Starting HP (syncs to `max_hp` at spawn if omitted) |
| `max_hp` | int / list | **Yes** | **Yes** | Maximum HP |
| `attack` | int / list | **Yes** | **Yes** | Base attack stat |
| `defense` | int / list | **Yes** | **Yes** | Base defense stat |
| `xp_reward` | int / list | **Yes** | **Yes** | XP given to player on NPC death |
| `gold_reward` | int / list | **Yes** | **Yes** | Gold given to player on NPC death |
| `hostile` | bool | **Yes** | No | If `true`, NPC attacks player on sight |
| `dialogue` | string | No | No | Fallback line if no dialogue tree file exists |
| `shop` | list | No | No | Shop inventory; see [Section 5.5](#55-shops) |
| `rest_cost` | int | No | No | Gold cost for player to rest here (innkeeper behaviour) |
| `give_accepts` | list | No | No | Items the NPC accepts; see [Section 5.6](#56-item-acceptance-give_accepts) |
| `kill_script` | array | No | No | Script ops run when NPC HP reaches 0 |
| `round_script` | array | No | No | Script ops run after each combat round |
| `admin_comment` | string | No | No | Developer notes; ignored by engine |

### 5.2 Random Spawn Variation

Any **scalar** field in the Randomizable column can be written as a TOML array.
Each NPC **instance** rolls randomly from the list at spawn time. This creates
variety within a species without duplicating templates.

```toml
[[npc]]
id         = "river_wolf"
name       = ["Lean River Wolf", "Grey River Wolf", "Black River Wolf"]
max_hp     = [18, 22, 26]
attack     = [6, 7, 8]
xp_reward  = [30, 35, 40]
gold_reward = 0          # scalar — all instances get 0 gold
```

> **Rule:** `hp` should usually match `max_hp`. List them both, or omit `hp` and the
> engine will sync it to the rolled `max_hp` at spawn.

> **Not randomizable:** `tags`, `shop`, `give_accepts`, `kill_script`, `round_script`.

### 5.3 Minimal and Full NPC Examples

**Minimal hostile NPC:**
```toml
[[npc]]
id          = "goblin_scout"
name        = "Goblin Scout"
desc_short  = "A wiry goblin in a patched leather vest."
desc_long   = "Its yellow eyes dart between you and the nearest exit."
tags        = ["humanoid", "small", "fast"]
style       = "brawling"
style_prof  = 20
hp          = 14
max_hp      = 14
attack      = 6
defense     = 3
xp_reward   = 25
gold_reward = 5
hostile     = true
```

**NPC with death script, dialogue, and shop:**
```toml
[[npc]]
id          = "bandit_chief"
name        = "Bandit Chief Gorrath"
desc_short  = "A scarred human with a chip on his shoulder and a blade on his hip."
desc_long   = "Gorrath has been running this crew for three years, which is two more than..."
tags        = ["humanoid", "armored", "slow"]
style       = "swordplay"
style_prof  = 55
hp          = 90
max_hp      = 90
attack      = 16
defense     = 10
xp_reward   = 200
gold_reward = 40
hostile     = true
kill_script = [
  { op = "set_flag",       flag = "bandit_chief_slain" },
  { op = "advance_quest",  quest_id = "bandit_problem", step = 3 },
  { op = "message",        tag = "combat_kill",
    text = "Gorrath crumples with a look of surprise." },
  { op = "spawn_item",     item_id = "bandit_chiefs_key" },
]
```

**NPC with `round_script` (surrenders mid-fight):**
```toml
[[npc]]
id           = "smuggler_boss"
...
round_script = [
  { op = "if_combat_round", min = 5, then = [
    { op = "if_npc_hp", max = 20, then = [
      { op = "say", text = "Alright! Alright — I yield! The vault code is 'MIDNIGHT'!" },
      { op = "set_flag", flag = "vault_code_known" },
      { op = "end_combat" },
    ] },
  ] },
]
```

### 5.4 NPC Tags

Tags are plain strings. The engine uses them for:

| Tag | Effect |
|-----|--------|
| `humanoid` | Style proficiency gains apply in full |
| `beast` | Some dialogue conditions can filter by type |
| `armored` | Advisory (style affinity checks) |
| `large` | Blocked by `no_large_companion` room flag |
| `slow` | Advisory |
| `fast` | Advisory |
| `prestige_neutral` | Prestige score does not affect NPC attitude |
| `criminal` | Goes hostile at prestige ≥ +100 (high-honour players) |
| `guard` | Goes hostile at prestige ≤ −50 |
| `coward` | Flees first (extra free attack) at prestige ≥ +60 |
| `spar` | NPC cannot be killed; HP floors at 1 |

Custom tags (any string) work too — style affinity configs reference custom tags.

### 5.5 Shops

An NPC with a `shop` field becomes a merchant. Players buy with `buy <item>`.

```toml
shop = [
  { item_id = "iron_sword",     price = 30 },
  { item_id = "health_potion",  price = 12 },
  { item_id = "torch",          price = 2  },
]
```

| Field | Type | Description |
|-------|------|-------------|
| `item_id` | string | Item template ID to sell |
| `price` | int | Price in gold |

### 5.6 Item Acceptance (`give_accepts`)

NPCs can accept items from the player (quest hand-ins, material deposits):

```toml
give_accepts = [
  {
    item_id = "dragon_fang",
    message = "The elder examines the fang, eyes wide. 'This... this is real.'",
    script  = [
      { op = "set_flag",       flag = "dragon_proof_delivered" },
      { op = "advance_quest",  quest_id = "the_dragon_hunt", step = 4 },
      { op = "give_gold",      amount = 200 },
      { op = "give_xp",        amount = 500 },
      { op = "prestige",       amount = 5, reason = "slew the Ashwood Drake" },
    ],
  },
]
```

| Field | Type | Description |
|-------|------|-------------|
| `item_id` | string | Which item the NPC accepts |
| `message` | string | Narrative text shown on hand-in |
| `script` | array | Script ops (rewards, quest advances, flags) |

---

## 6. Items

Items are defined in `[[item]]` blocks in a zone's `items.toml`.

### 6.1 All Item Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | **Yes** | Unique item identifier |
| `name` | string | **Yes** | Display name |
| `desc_short` | string | Recommended | One-line description |
| `desc_long` | string | Recommended | Full description (shown with `examine <item>`) |
| `slot` | string | No | Equipment slot; `""` = non-equippable — see [Appendix B](#appendix-b--equipment-slots) |
| `weight` | int | No | Carry weight in stones (0 = weightless) |
| `tags` | list | No | Item tags checked by `require_tag` script op and style affinity |
| `weapon_tags` | list | No | Tags for fighting-style weapon affinity matching |
| `armor_tags` | list | No | Tags for fighting-style armor affinity matching |
| `key_tag` | string | No | Unlocks any door with a matching `lock_tag` |
| `respawn` | bool | No | `false` = removed after pickup, tracked in player's looted set |
| `no_drop` | bool | No | `true` = cannot be dropped; stays with player on death |
| `scenery` | bool | No | `true` = visible but cannot be picked up |
| `gold_value` | int | No | Selling price to NPC merchants |
| `effects` | list | No | Passive or triggered effects — see [Section 6.2](#62-item-effects) |
| `on_get` | array | No | Script run when player picks up this item |
| `on_drop` | array | No | Script run when player drops this item |

### 6.2 Item Effects

Effects are objects in the `effects` array. Each has a `type` field.

#### `stat_bonus` — Passive equipment bonus

```toml
{ type = "stat_bonus", stat = "attack",          amount = 8  }
{ type = "stat_bonus", stat = "defense",         amount = 4  }
{ type = "stat_bonus", stat = "max_hp",          amount = 20 }
{ type = "stat_bonus", stat = "carry_capacity",  amount = 5  }
```

Active while the item is equipped. Amount can be negative (penalty items).

#### `on_equip` — Flavour text on equip

```toml
{ type = "on_equip", message = "The spikes bite pleasantly into your palm." }
```

#### `on_hit` — Combat proc on successful hit

```toml
{ type = "on_hit", ability = "stun",  chance = 0.12, magnitude = 1 }
{ type = "on_hit", ability = "bleed", chance = 0.20, magnitude = 3 }
```

| Field | Values | Description |
|-------|--------|-------------|
| `ability` | `"stun"`, `"bleed"` | Which proc fires |
| `chance` | 0.0 – 1.0 | Probability per hit |
| `magnitude` | int | Stun: turns; Bleed: damage per tick |

#### `on_use` — Consumable healing

```toml
{ type = "on_use", heal = 30 }
{ type = "on_use", heal = 30, message = "The tonic floods your limbs with warmth." }
```

### 6.3 Item Examples

**Weapon:**
```toml
[[item]]
id          = "spiked_knuckles"
name        = "Spiked Knuckles"
desc_short  = "Iron knuckles with short brutal spikes."
desc_long   = "Four iron rings connected by a crossbar..."
slot        = "weapon"
weight      = 1
weapon_tags = ["fist", "brawling"]
respawn     = false
effects     = [
  { type = "stat_bonus", stat = "attack", amount = 7 },
  { type = "on_hit",     ability = "stun", chance = 0.12, magnitude = 1 },
  { type = "on_equip",   message = "The spikes bite into your palm. Good." },
]
```

**Consumable:**
```toml
[[item]]
id          = "health_potion"
name        = "Health Potion"
desc_short  = "A thumb-sized vial of glowing red liquid."
slot        = ""
weight      = 0
respawn     = true
effects     = [{ type = "on_use", heal = 30, message = "Warmth floods through you." }]
```

**Key:**
```toml
[[item]]
id          = "barracks_key"
name        = "Barracks Key"
desc_short  = "A heavy iron key stamped with the town crest."
slot        = ""
weight      = 0
key_tag     = "barracks_lock"
respawn     = false
```

**Scenery / Mining node:**
```toml
[[item]]
id       = "iron_vein"
name     = "Iron Ore Vein"
desc_short = "A seam of dark iron ore running through the wall."
slot     = ""
scenery  = true
respawn  = true
on_get   = [
  { op = "require_tag", tag = "pickaxe",
    fail_message = "You need a pickaxe to mine this." },
  { op = "skill_check", skill = "mining", dc = 8,
    on_pass = [
      { op = "give_item",  item_id = "iron_ore" },
      { op = "skill_grow", skill = "mining", amount = 2 },
    ],
    on_fail = [
      { op = "give_item",  item_id = "iron_ore" },
      { op = "message", tag = "system", text = "You chip out a rough piece." },
    ],
  },
]
```

---

## 7. The Script Engine

### 7.1 What Scripts Are

A **script** is a TOML array of operation dicts. Scripts are run synchronously,
top-to-bottom. Branching is done with conditional ops that have `then`/`else` lists.
Scripts can be nested to any depth.

```toml
kill_script = [
  { op = "set_flag",  flag = "wolf_slain" },
  { op = "give_item", item_id = "wolf_pelt" },
]
```

### 7.2 Where Scripts Appear

| Location | Field | Fired when |
|----------|-------|------------|
| NPC | `kill_script` | NPC HP reaches 0 |
| NPC | `give_accepts[].script` | Player gives the item to the NPC |
| NPC | `round_script` | After each combat round (both alive) |
| Item | `on_get` | Player picks up item |
| Item | `on_drop` | Player drops item |
| Room | `on_enter` | Player enters the room |
| Exit dict | `on_exit` | Player leaves via this exit (source room) |
| Exit dict | `on_enter` | Player arrives via this exit (destination room) |
| Exit dict | `on_look` | Player examines the exit direction |
| Exit dict | `on_unlock` | Player unlocks this door |
| Exit dict | `on_lock` | Player locks this door |
| Dialogue node | `script` | Player enters this dialogue node |
| Dialogue response | `script` | Player selects this response |

### 7.3 GameContext

Every script runs with access to the current state:
- `player` — mutable player state (HP, gold, inventory, flags, skills…)
- `world` — rooms, item templates, NPC templates
- `quests` — quest tracker (start/advance/complete)
- `round` — current combat round number (`0` outside combat)
- `npc` — the NPC being fought (only set in `round_script` and `kill_script` context; `None` otherwise)

**Context availability by script location:**

| Script location | `npc` available | `round` > 0 |
|-----------------|:--------------:|:-----------:|
| `kill_script` | Yes (the killed NPC) | Yes |
| `round_script` | Yes (opponent NPC) | Yes |
| `give_accepts[].script` | Yes (the giver NPC) | No |
| `on_get` / `on_drop` (item) | No | No |
| `on_enter` (room / exit) | No | No |
| Dialogue `script` / response `script` | Yes (the speaker NPC) | No |

> **Note:** `if_combat_round`, `if_npc_hp`, and `end_combat` only make sense inside
> `round_script`. They will silently do nothing if `npc` is `None`.

### 7.4 Script Abort

`{ op = "fail" }` aborts the entire script run immediately with no error.
`require_tag` also aborts if the player lacks the required item tag.
Both are caught cleanly — no exception reaches the player.

**Typical `fail` patterns:**

```toml
# Abort if the player doesn't have gold — do nothing silently
{ op = "if_not_flag", flag = "quest_active", then = [{ op = "fail" }] }

# require_tag shows a message and stops
{ op = "require_tag", tag = "pickaxe", fail_message = "You need a pickaxe to mine here." }
```

### 7.5 All Script Operations (~47 ops)

> **Quick tip:** For a condensed reference of every op and its attributes, see
> [Appendix C](#appendix-c--all-script-ops-quick-reference) at the end of this document.

---

#### OUTPUT

**`say`** — Emit dialogue-tagged text (shown in warm parchment colour)

```toml
{ op = "say", text = "Greetings, traveller. The road ahead is not safe." }
```

| Attribute | Type | Required | Description |
|-----------|------|----------|-------------|
| `text` | string | Yes | Text to display |

*Usable in:* all script contexts.
*Example from world:* NPC surrender message in `round_script`.

---

**`message`** — Emit text with any message tag

```toml
{ op = "message", text = "The cave wall groans ominously.", tag = "system" }
{ op = "message", text = "You gain 100 XP!", tag = "reward_xp" }
```

| Attribute | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `text` | string | Yes | — | Text to display |
| `tag` | string | No | `"system"` | Any Tag name: `system`, `combat_hit`, `reward_xp`, `reward_gold`, `item`, `error`, `dialogue`, `quest`, `door`, `shop`, `stats`, `journal`, etc. |

*Usable in:* all script contexts.

---

#### PLAYER FLAGS

**`set_flag`** — Add an arbitrary flag to the player's flag set

```toml
{ op = "set_flag", flag = "met_the_elder" }
```

Flags are persistent strings that track story state. Use them to gate dialogue,
control spawn sequences, and enable conditional exits.

*From world:* `{ op = "set_flag", flag = "ashwood_drake_slain" }` in `kill_script`.

---

**`clear_flag`** — Remove a flag from the player's flag set

```toml
{ op = "clear_flag", flag = "is_wanted" }
```

| Attribute | Type | Required | Description |
|-----------|------|----------|-------------|
| `flag` | string | Yes | Flag name to remove (no-ops if not set) |

---

#### GOLD / XP / HP

**`give_gold`** — Add gold to the player; displays a reward message

```toml
{ op = "give_gold", amount = 80 }
```

| Attribute | Type | Required | Description |
|-----------|------|----------|-------------|
| `amount` | int | Yes | Gold to give (non-zero triggers reward display) |

*From world:* `{ op = "give_gold", amount = 80 }` in `give_accepts` script.

---

**`take_gold`** — Deduct gold; fails with an error if balance is insufficient

```toml
{ op = "take_gold", amount = 50 }
{ op = "take_gold", amount = 50, silent = true }
```

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `amount` | int | — | Gold to deduct |
| `silent` | bool | `false` | Suppress the "You pay…" message |

---

**`give_xp`** — Award XP; triggers level-up message if threshold is crossed

```toml
{ op = "give_xp", amount = 200 }
{ op = "give_xp", amount = 200, silent = true }
```

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `amount` | int | — | XP to award |
| `silent` | bool | `false` | Suppress XP gain message (level-up always shows) |

---

**`heal`** — Restore HP; capped at `max_hp`

```toml
{ op = "heal", amount = 30 }
```

---

**`set_hp`** — Set HP to an exact value (clamped 0 – max_hp)

```toml
{ op = "set_hp", amount = 1 }   # leave at death's door
```

---

**`damage`** — Deal direct damage, bypassing combat calculations

```toml
{ op = "damage", amount = 10 }
{ op = "damage", amount = 10, message = "The trap springs!", silent = true }
```

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `amount` | int | — | HP to remove |
| `message` | string | `""` | Optional flavour text before the HP-loss line |
| `silent` | bool | `false` | Suppress the HP-loss line |

*From world:* Triggered by hazard traps in `on_enter` scripts.

---

#### INVENTORY

**`give_item`** — Deep-copy an item template into the player's inventory

```toml
{ op = "give_item", item_id = "vault_key" }
```

Displays "You receive: [item name]" and displays the item in the ITEM colour.

| Attribute | Type | Required | Description |
|-----------|------|----------|-------------|
| `item_id` | string | Yes | Item template ID (from items.toml) |

*From world:* `{ op = "give_item", item_id = "drakes_fang" }` in `kill_script`.

---

**`take_item`** — Remove the first matching item from inventory (un-equips if needed)

```toml
{ op = "take_item", item_id = "tribute_chest" }
```

Silent if item not found.

---

**`spawn_item`** — Place an item in the current room (or player inventory if room missing)

```toml
{ op = "spawn_item", item_id = "mysterious_orb" }
```

Displays "A [name] falls to the ground."

*From world:* `{ op = "spawn_item", item_id = "groundhog_trophy" }` in `kill_script`.

---

#### QUESTS

**`advance_quest`** — Move an active quest to the given step, or start it at step 1

```toml
{ op = "advance_quest", quest_id = "the_dragon_hunt", step = 3 }
```

If the quest is not yet active, it is started automatically at step 1 first.
Displays the new objective text.

---

**`complete_quest`** — Mark a quest as complete and award its rewards

```toml
{ op = "complete_quest", quest_id = "the_dragon_hunt" }
```

Awards all `[[reward]]` entries (gold, XP, items) and moves the quest to
`completed_quests`. Displays a completion banner.

---

#### STYLES

**`teach_style`** — Add a fighting style to the player's known styles

```toml
{ op = "teach_style", style_id = "swordplay" }
```

Has no effect if the player already knows the style. Displays a learn message.

---

#### WORLD

**`unlock_exit`** — Unlock a door in any loaded room

```toml
{ op = "unlock_exit", room_id = "dungeon_gate", direction = "north" }
```

| Attribute | Type | Required | Description |
|-----------|------|----------|-------------|
| `room_id` | string | Yes | Room containing the door |
| `direction` | string | Yes | Direction of the exit to unlock |

Silently no-ops if the room or exit is not found.

---

**`lock_exit`** — Lock a door in any loaded room

```toml
{ op = "lock_exit", room_id = "dungeon_gate", direction = "north" }
```

---

#### COMPANIONS

**`give_companion`** — Assign a companion to the player

```toml
{ op = "give_companion", companion_id = "ranger_mira" }
```

Displays the companion's `join_message`. Replaces any existing companion.

---

**`dismiss_companion`** — Remove the player's current companion

```toml
{ op = "dismiss_companion", message = "Your companion nods and parts ways." }
```

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `message` | string | `""` | Flavour text shown on dismissal |

---

#### SKILLS

**`skill_check`** — Roll d20 + skill bonus against a DC; branch on result

```toml
{ op = "skill_check", skill = "perception", dc = 12,
  on_pass = [
    { op = "message", tag = "system", text = "You spot a hidden catch in the wall." },
    { op = "give_item", item_id = "hidden_gem" },
  ],
  on_fail = [
    { op = "message", tag = "system", text = "You miss anything unusual." },
  ],
}
```

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `skill` | string | — | A skill ID defined in the world's `config.py` (e.g. `perception`, `stealth`, `mining`) |
| `dc` | int | `10` | Difficulty class (roll ≥ dc to pass) |
| `on_pass` | array | `[]` | Script ops on success |
| `on_fail` | array | `[]` | Script ops on failure |
| `silent` | bool | `false` | Suppress the "[Skill check: N vs DC M — result]" line |
| `grow` | bool | `true` | Apply small passive skill growth (when no `on_pass` branch) |

The roll is `d20 + (skill_value // 10)`. Skill bonus ranges 0–10.

---

**`if_skill`** — Branch on whether skill value meets a minimum (no roll, no growth)

```toml
{ op = "if_skill", skill = "mining", min = 1,
  then = [{ op = "message", tag = "system", text = "You know how to mine." }],
  else = [{ op = "message", tag = "error",  text = "You have no mining skill." }],
}
```

| Attribute | Type | Required | Description |
|-----------|------|----------|-------------|
| `skill` | string | Yes | Skill name |
| `min` | int | `0` | Minimum skill value to pass |
| `then` | array | `[]` | Run if skill ≥ min |
| `else` | array | `[]` | Run if skill < min |

---

**`skill_grow`** — Directly increase a skill value

```toml
{ op = "skill_grow", skill = "mining", amount = 2 }
```

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `skill` | string | — | Skill name |
| `amount` | float | `1` | Amount to add (capped at 100) |

Displays "Mining 5 → 7" when the integer part increases.

---

#### STATUS EFFECTS

**`apply_status`** — Apply a named status effect for N turns

```toml
{ op = "apply_status", effect = "poisoned", duration = 5 }
{ op = "apply_status", effect = "blinded",  duration = -1 }
```

| Attribute | Type | Required | Description |
|-----------|------|----------|-------------|
| `effect` | string | Yes | `poisoned`, `blinded`, `weakened`, `slowed`, `protected` |
| `duration` | int | — | Turns remaining; `-1` = permanent until cleared |

See [Section 11](#11-status-effects) for what each effect does.

---

**`clear_status`** — Remove a status effect

```toml
{ op = "clear_status", effect = "poisoned" }
```

---

**`if_status`** — Branch on whether an effect is currently active

```toml
{ op = "if_status", effect = "protected",
  then = [{ op = "say", text = "Your ward deflects the attack." }],
  else = [{ op = "damage", amount = 15 }],
}
```

---

#### PRESTIGE

**`prestige`** — Adjust the player's prestige score

```toml
{ op = "prestige", amount = 3,  reason = "cleared the infestation" }
{ op = "prestige", amount = -5, reason = "attacked a town guard" }
```

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `amount` | int | — | Positive = good, negative = bad |
| `reason` | string | `""` | Short flavour note shown in the output |

See [Section 12](#12-prestige--reputation) for tier thresholds.

---

**`add_affinity`** — Grant a named prestige affinity tag

```toml
{ op = "add_affinity", tag = "verdant_hero" }
```

---

**`remove_affinity`** — Remove a prestige affinity tag

```toml
{ op = "remove_affinity", tag = "verdant_hero" }
```

---

**`if_prestige`** — Branch on prestige score range

```toml
# Requires score ≥ 20
{ op = "if_prestige", min = 20,
  then = [{ op = "say", text = "I've heard of you — a genuine hero." }],
  else = [{ op = "say", text = "I don't know you. Move along." }],
}

# Requires score ≤ -10 (disgraced)
{ op = "if_prestige", max = -10,
  then = [{ op = "message", tag = "system", text = "The guard eyes you with open hostility." }],
}
```

`min` and `max` can be combined. Both omitted = always passes.

---

**`if_affinity`** — Branch on whether player has a prestige affinity tag

```toml
{ op = "if_affinity", tag = "verdant_hero",
  then = [{ op = "say", text = "Ah, the one who saved the forest!" }],
  else = [{ op = "say", text = "A stranger." }],
}
```

---

#### BANK

**`bank_expand`** — Expand the player's bank slot count (never shrinks)

```toml
{ op = "bank_expand", tier = 20 }
```

| Attribute | Type | Description |
|-----------|------|-------------|
| `tier` | int | New slot count; only applied if greater than current |

---

#### CONDITIONALS

All conditional ops support `then` (runs if condition passes) and `else` (runs if not).
Both are optional — omit whichever branch you don't need.

---

**`if_flag`** (also: **`if`**) — Branch on whether player has a flag

```toml
{ op = "if_flag", flag = "met_the_elder",
  then = [{ op = "say", text = "Back again so soon?" }],
  else = [{ op = "say", text = "I don't think we've met." }],
}
```

---

**`if_not_flag`** — Branch on whether player does NOT have a flag

```toml
{ op = "if_not_flag", flag = "bridge_warned", then = [
  { op = "message", tag = "system", text = "A sign reads: 'Bridge unsafe.'" },
  { op = "set_flag", flag = "bridge_warned" },
] }
```

---

**`if_item`** — Branch on whether player has an item in inventory

```toml
{ op = "if_item", item_id = "royal_seal",
  then = [{ op = "say",      text = "The seal! You found it." }],
  else = [{ op = "say",      text = "You'll need proof of your identity." }],
}
```

---

**`if_quest`** — Branch on whether a quest is active at a specific step

```toml
{ op = "if_quest", quest_id = "the_dragon_hunt", step = 2,
  then = [{ op = "say", text = "You're looking for the dragon's lair?" }],
}
```

---

**`if_quest_complete`** — Branch on whether a quest has been completed

```toml
{ op = "if_quest_complete", quest_id = "the_dragon_hunt",
  then = [{ op = "say", text = "I heard you slew the beast. Well done." }],
  else = [{ op = "say", text = "The dragon still threatens us." }],
}
```

---

#### FLOW CONTROL

**`fail`** — Abort the entire current script immediately

```toml
{ op = "fail" }
```

Nothing after `fail` in the same script (or any parent script) will run. Used to
gate content: check a condition, fail if not met, continue only if met.

```toml
on_get = [
  { op = "if_not_flag", flag = "mine_cleared", then = [
    { op = "message", tag = "error", text = "The mine is too dangerous." },
    { op = "fail" },
  ] },
  # Script only reaches here if mine_cleared is set
  { op = "require_tag", tag = "pickaxe", fail_message = "You need a pickaxe." },
  ...
]
```

---

**`require_tag`** — Abort if player has no item with a given tag

```toml
{ op = "require_tag", tag = "pickaxe",
  fail_message = "You need a pickaxe equipped or in your pack." }
```

| Attribute | Type | Required | Description |
|-----------|------|----------|-------------|
| `tag` | string | Yes | Tag to search for in all inventory + equipped items |
| `fail_message` | string | No | Error message shown before abort |

Checks `tags` array on every inventory and equipped item.

---

#### TELEPORT / WORLD MOVEMENT

**`teleport_player`** — Move the player to any room (even in another zone)

```toml
{ op = "teleport_player", room_id = "throne_room", message = "The portal flares and you are elsewhere." }
```

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `room_id` | string | — | Destination room ID |
| `message` | string | `""` | Optional system-tagged message before teleport |

After teleportation the destination room's description and `on_enter` script run
automatically.

*Use case:* Portals, boss arenas, trap rooms, quest teleporters.

---

**`move_npc`** — Move a live NPC instance from its current room to another

```toml
{ op = "move_npc", npc_id = "dragon_boss", to_room = "battle_arena" }
```

| Attribute | Type | Required | Description |
|-----------|------|----------|-------------|
| `npc_id` | string | Yes | NPC instance ID to find and move |
| `to_room` | string | Yes | Destination room ID |

Searches all loaded zones. No-ops silently if NPC not found.

---

**`move_item`** — Move a ground item from one room to another

```toml
{ op = "move_item", item_id = "ancient_chest",
  from_room = "vault_antechamber", to_room = "vault_inner" }
```

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `item_id` | string | — | Item ID to find |
| `from_room` | string | player's current room | Source room ID |
| `to_room` | string | — | Destination room ID |

---

#### COMBAT CONDITIONALS (round_script only)

These ops are only meaningful inside an NPC's `round_script`.

**`if_combat_round`** — Branch on the current combat round number

```toml
{ op = "if_combat_round", min = 5,
  then = [{ op = "say", text = "You've held on longer than expected." }],
}
```

| Attribute | Type | Description |
|-----------|------|-------------|
| `min` | int | Passes when `round >= min` |

---

**`if_npc_hp`** — Branch on the NPC's current HP

```toml
{ op = "if_npc_hp", max = 25,
  then = [{ op = "say", text = "I'm... not finished yet!" }],
}
```

| Attribute | Type | Description |
|-----------|------|-------------|
| `max` | int | Passes when `npc.hp <= max` |

---

**`end_combat`** — End the current fight without killing the NPC

```toml
{ op = "end_combat" }
```

The NPC's HP is set to at least 1, its `hostile` flag is cleared for this session,
and `player_won = true`. The player receives XP and gold as if they won normally.

*Typical usage:* NPC surrender — check round + HP conditions, emit surrender text,
give a reward, then call `end_combat`.

---

#### JOURNAL

**`journal_entry`** — Append a named entry to the player's journal

```toml
{ op = "journal_entry",
  title = "The Wall Inscription",
  text  = "Carved in old runes: 'Only the worthy may pass the gate of flame.'" }
```

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `title` | string | `"Entry"` | Short header for the entry (shown in the journal list) |
| `text` | string | `""` | Full text of the entry |

Journal entries persist to the save file and display in the `j` / `journal` command.
*Use case:* Found inscriptions, discovered lore, crafting milestones, solved puzzles.

---

## 8. The Dialogue System

### 8.1 File Layout

Each NPC with dialogue has a file at `data/<world_id>/<zone>/dialogues/<npc_id>.toml`.
The filename must exactly match the NPC's `id` field.

### 8.2 Node Structure

Conversations are trees of `[[node]]` blocks. Every tree **must** have a node with
`id = "root"`.

```toml
[[node]]
id        = "root"                           # Required on root node
line      = "Hello, traveller."              # Single fixed line
# OR:
lines     = ["Hello!", "Back again?", "..."] # Randomly chosen each visit
# OR with cycling:
lines     = ["First visit.", "Second visit.", "Third visit..."]
cycle     = true                             # Cycles sequentially instead of random
condition = { flag = "met_the_elder" }       # Optional: skip node if false
script    = [{ op = "set_flag", flag = "..." }]  # Optional: run on node entry
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Node identifier; `"root"` starts every conversation |
| `line` | string | Single text line |
| `lines` | list | Multiple lines: random pick or cycling sequence |
| `cycle` | bool | If `true`, cycle through `lines` sequentially per visit |
| `condition` | dict | If condition fails, this node is skipped and fallback dialogue shows |
| `script` | array | Script ops executed when this node is entered |

### 8.3 Response Structure

Responses are options the player chooses from. They can be defined inline under
a node or in flat `[[response]]` blocks:

```toml
# Inline under node
[[node.response]]
text      = "What do you know about the forest?"
next      = "about_forest"
condition = { flag = "heard_of_forest" }
script    = [{ op = "set_flag", flag = "asked_about_forest" }]

# Or flat format (equivalent)
[[response]]
node      = "root"              # Which node this belongs to
text      = "What do you know about the forest?"
next      = "about_forest"
condition = { flag = "heard_of_forest" }
script    = [{ op = "set_flag", flag = "asked_about_forest" }]
```

| Field | Type | Description |
|-------|------|-------------|
| `node` | string | (flat format) Which node this response belongs to |
| `text` | string | Response option shown to player |
| `next` | string | Node ID to jump to; `""` ends the conversation |
| `condition` | dict | Optional: response hidden if condition fails |
| `script` | array | Script ops run when this response is selected |

### 8.4 Dialogue Conditions

Conditions control whether a node or response is visible.

| Field | Type | Passes when |
|-------|------|-------------|
| `flag` | string | player has this flag |
| `not_flag` | string | player does NOT have this flag |
| `item` | string | player has this item ID in inventory |
| `quest` | string + `step` | quest is active at this step |
| `quest_complete` | string | quest is complete |
| `not_quest` | string | quest is NOT active |
| `level_gte` | int | player.level ≥ N |
| `skill` | string + `min` | player.skills[skill] ≥ min |
| `gold` | int | player has ≥ N gold |
| `prestige_min` | int | prestige ≥ N |
| `prestige_max` | int | prestige ≤ N |
| `affinity` | string | player has this prestige affinity |
| `no_affinity` | string | player lacks this prestige affinity |
| `no_companion` | bool (`true`) | player has no active companion |

```toml
# Multiple conditions can be combined as a list (all must pass — AND logic)
condition = [
  { flag = "heard_the_rumour" },
  { level_gte = 5 },
]
```

### 8.5 Text Substitution

Use `{token}` placeholders in `line`, `lines`, or `text` strings.

**Built-in tokens:**

| Token | Resolves to |
|-------|-------------|
| `{player}` or `{player_name}` | Player's character name |
| `{npc}` or `{npc_name}` | This NPC's name |
| `{gold}` | Player's current gold |
| `{hp}` | Player's current HP |
| `{level}` | Player's current level |
| `{zone}` | Current zone ID |

**Entity field tokens** — any NPC, item, or quest template field:

```toml
line = "Speak with {guard_captain.name} — his {iron_sword.name} is legendary."
# → "Speak with Commander Aldric — his Steel Sword is legendary."
```

Format: `{entity_id.field_name}`. Resolution checks NPCs first, then items, then quests.
Returns empty string if not found.

### 8.6 Fallback Dialogue

If no dialogue tree file exists for an NPC, the engine falls back to:
1. The NPC's `dialogue` field (plain text)
2. Auto-generated lines based on hostility and tags

### 8.7 Complete Dialogue Example

```toml
[[node]]
id = "root"
lines = [
  "You picked a bad week to go east.",
  "The road gets worse the further you go.",
]

[[response]]
node      = "root"
condition = { not_flag = "ranger_briefed" }
text      = "What can you tell me about the road ahead?"
next      = "briefing"

[[response]]
node = "root"
text = "Nothing. Goodbye."
next = ""

[[node]]
id     = "briefing"
line   = "The east road leads to the old garrison ruins. Wolves patrol it — corrupted ones."
script = [{ op = "set_flag", flag = "ranger_briefed" }]

[[response]]
node = "briefing"
text = "How do I get into the armory?"
next = "armory_hint"

[[response]]
node = "briefing"
text = "Thank you. Farewell."
next = ""

[[node]]
id   = "armory_hint"
line = "There's a key in the ruins. Look for the castellan's old office — northwest corner."
```

---

## 9. The Quest System

### 9.1 File Layout

Quests live at `data/<world_id>/<zone>/quests/<quest_id>.toml`. The filename is the quest ID.

### 9.2 Quest Format

```toml
id      = "ashwood_contract"
title   = "The Ashwood Contract"
giver   = "elder_mira"               # Informational only
summary = "Investigate the corruption in Ashwood Forest and slay the Drake."

[[step]]
index     = 1
objective = "Speak with Sergeant Vorn in the Barracks."
hint      = "Head north from the Town Square."

[[step]]
index     = 2
objective = "Travel to the Ashwood Gate."
hint      = "Head south then east past the deep forest."

[[step]]
index     = 6
objective = "Return to Elder Mira with proof of the Drake's death."
hint      = "The Fang should convince her."

[[reward]]
type    = "gold"
amount  = 150

[[reward]]
type    = "xp"
amount  = 400

[[reward]]
type    = "item"
item_id = "millhaven_commendation"
```

### 9.3 Quest Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique quest identifier |
| `title` | string | Display name |
| `giver` | string | Informational NPC ID (not enforced) |
| `summary` | string | Long description shown in journal |
| `[[step]]` | — | One or more objective steps |
| `step.index` | int | Step number (must be ≥ 1); gaps are fine |
| `step.objective` | string | What the player must do |
| `step.hint` | string | Optional hint shown in journal |
| `[[reward]]` | — | Awards given when quest is completed |
| `reward.type` | `"gold"`, `"xp"`, `"item"` | Reward type |
| `reward.amount` | int | For gold/xp |
| `reward.item_id` | string | For item rewards |

### 9.4 Script Integration

Start, advance, and complete quests from any script context:

```toml
# NPC dialogue kicks off the quest (step 1)
script = [{ op = "advance_quest", quest_id = "ashwood_contract", step = 1 }]

# Kill script advances on NPC death
kill_script = [{ op = "advance_quest", quest_id = "ashwood_contract", step = 6 }]

# give_accepts completes the quest on item hand-in
script = [{ op = "complete_quest", quest_id = "ashwood_contract" }]
```

### 9.5 Quest Conditions in Dialogue

```toml
# Show response only when quest is at step 2
condition = { quest = "ashwood_contract", step = 2 }

# Show response only when quest is complete
condition = { quest_complete = "ashwood_contract" }

# Show only if quest is NOT active
condition = { not_quest = "ashwood_contract" }
```

---

## 10. Skills

Skills are **world-configurable** — the full list is defined in `SKILLS` inside the world's
`config.py`. The engine uses whatever skills are listed there; no engine code changes are needed.

### 10.1 Skills in The Sixfold Realms

| Skill ID | Display Name | Used for |
|----------|-------------|----------|
| `perception` | Perception | Noticing hidden exits, items, traps |
| `stealth` | Stealth | Moving quietly, avoiding detection |
| `survival` | Survival | Wilderness hazards, foraging, navigation |
| `athletics` | Athletics | Climbing, swimming, feats of strength |
| `social` | Social | Persuasion, reading people, negotiation |
| `arcana` | Arcana | Magic knowledge, identifying enchantments |
| `mining` | Mining | Extracting ore; required for mining nodes |

All skills start at `0.0` and cap at `100.0`.

### 10.2 Skill Tiers

| Value range | Tier name |
|-------------|-----------|
| 0 – 9 | Untrained |
| 10 – 24 | Novice |
| 25 – 49 | Practiced |
| 50 – 74 | Skilled |
| 75 – 89 | Expert |
| 90 – 99 | Master |
| 100 | Legendary |

### 10.3 Skill Checks

`skill_check` rolls `d20 + bonus` vs a DC:
- **bonus** = `int(skill_value) // 10` — so skill 50 gives +5, skill 100 gives +10.
- **DC guideline:** 8 = easy, 12 = moderate, 15 = hard, 18 = very hard.

### 10.4 Using Skills in World Content

**Gate content on skill level (no roll):**
```toml
{ op = "if_skill", skill = "perception", min = 40,
  then = [{ op = "message", tag = "system", text = "You notice a crack in the wall." }],
}
```

**Randomised skill check:**
```toml
{ op = "skill_check", skill = "athletics", dc = 12,
  on_pass = [{ op = "message", tag = "system", text = "You haul yourself over the wall." }],
  on_fail = [{ op = "damage", amount = 5, message = "You slip and fall." }],
}
```

**Grow a skill manually:**
```toml
{ op = "skill_grow", skill = "mining", amount = 3 }
```

**Gate dialogue responses on skill:**
```toml
[[response]]
condition = { skill = "social", min = 40 }
text      = "Let me negotiate a better deal."
next      = "negotiation_branch"
```

**Gate conditional exits on skill:**
```toml
show_if = { op = "min_skill", skill = "perception", value = 60 }
```

---

## 11. Status Effects

Status effects are applied via script and expire after a set number of turns (or are
permanent until cleared).

| Effect | Applied by | In-combat | Per-move | Expiry message |
|--------|-----------|-----------|----------|----------------|
| `poisoned` | script | — | 3 HP damage | "The poison runs its course." |
| `blinded` | script | −4 attack | — | "Your vision clears." |
| `weakened` | script | −4 attack | — | "Your strength returns." |
| `slowed` | script | — | — | "The sluggishness lifts." |
| `protected` | script | +3 defense | — | "The protective ward fades." |

**Duration:** Turns remaining (one per command). `-1` = permanent until `clear_status`.

```toml
# Apply for 5 turns
{ op = "apply_status", effect = "poisoned", duration = 5 }

# Permanent until cleared
{ op = "apply_status", effect = "protected", duration = -1 }

# Remove immediately
{ op = "clear_status", effect = "poisoned" }

# Branch on effect presence
{ op = "if_status", effect = "protected",
  then = [{ op = "say", text = "Your ward glows briefly." }],
}
```

*Common world uses:* Poison traps in `on_enter` or item `on_get`; protective amulets
via `on_equip`; weakening bosses apply to the player via `round_script`.

---

## 12. Prestige & Reputation

Prestige is a score from −999 to +999 that reflects the player's moral standing.
High prestige opens heroic dialogue options and discounts. Low prestige makes guards
hostile and closes off legitimate quests.

### 12.1 Prestige Tiers

| Score | Tier | NPC attitude | Shop modifier | Notes |
|-------|------|-------------|--------------|-------|
| ≥ 200 | Legend | Friendly | −10% | Criminal NPCs go hostile |
| 100 – 199 | Champion | Friendly | −10% | |
| 50 – 99 | Hero | Friendly | −10% | |
| 20 – 49 | Honoured | Neutral | Normal | |
| 5 – 19 | Respected | Neutral | Normal | |
| −4 to +4 | Neutral | Neutral | Normal | |
| −5 to −19 | Suspicious | Wary | +10% | |
| −20 to −49 | Wanted | Wary | +10% | |
| −50 to −99 | Villain | Hostile | +20% | Guards go hostile at −50 |
| ≤ −100 | Outlaw | Hostile | +20% | |

### 12.2 Typical Prestige Deltas

| Event | Delta |
|-------|-------|
| Small good deed | +1 |
| Completing a community quest | +2 |
| Major heroic act | +5 |
| Petty theft / deliberate harm | −1 |
| Attacking a non-hostile NPC | −3 |
| Killing an innocent | −5 |
| Atrocity | −10 |

### 12.3 Script Usage

```toml
# Reward good behaviour
{ op = "prestige", amount = 3, reason = "cleared the infestation" }

# Penalise bad behaviour
{ op = "prestige", amount = -5, reason = "betrayed the refugees" }

# Gate dialogue on prestige
{ op = "if_prestige", min = 50, then = [
  { op = "say", text = "A Hero! It's an honour to meet you." },
] }

# Gate dialogue on being disgraced
{ op = "if_prestige", max = -20, then = [
  { op = "say", text = "I've heard of you. Get out of my sight." },
] }

# Award and check affinities
{ op = "add_affinity",  tag = "greenhollow_defender" }
{ op = "if_affinity",   tag = "greenhollow_defender", then = [...] }
{ op = "remove_affinity", tag = "greenhollow_defender" }
```

---

## 13. Fighting Styles

Fighting styles define how a character (player or NPC) fights. Each style has
passives that unlock at proficiency thresholds, preferred gear, and a matchup
system (some styles are effective or weak against certain opponent types).

Styles are defined in `data/<world_id>/<zone>/styles/<style_id>.toml`.
Players learn styles via `teach_style` scripts; NPCs have a fixed style at spawn.
The starting style for new characters is set by `default_style` in the world's `config.toml`.

**In world authoring**, styles matter for:
- Setting `style` and `style_prof` on NPCs to control difficulty
- Using `{ op = "teach_style", style_id = "..." }` to teach the player
- Equipping items with the right `weapon_tags` or `armor_tags` for style affinity

### 13.1 Style File Format

```toml
[[style]]
id                = "swordplay"
name              = "Swordplay"
desc_short        = "Classical blade technique. Excels against armored humanoids."
desc_long         = "A formal discipline from military academies..."
strong_vs         = ["armored", "humanoid"]
weak_vs           = ["fast", "beast", "undead"]
strong_multiplier = 1.5
weak_multiplier   = 0.65
attack_bonus      = 2
defense_bonus     = 0
difficulty        = 1.5     # proficiency gain divisor (1.0 = normal, 2.0 = twice as slow)
preferred_weapon_tags = ["blade", "one_handed"]
preferred_armor_tags  = ["chain", "heavy"]
weapon_bonus      = 0.25    # max attack % bonus from matching weapon (at full proficiency)
armor_bonus       = 0.15    # max defense % bonus from matching armor
learned_from      = "drill_sergeant"  # NPC id, or "" for self-taught / innate
learned_at        = 1       # minimum player level to learn

passives = [
  { ability = "parry",   threshold = 50, trigger = "defend",
    message = "You parry the attack cleanly!",
    on_activate = [{op = "block_damage"}] },
  { ability = "riposte", threshold = 75, trigger = "defend", requires = "parry",
    message = "You riposte!",
    on_activate = [{op = "counter_damage", multiplier = 0.6}] },
]
```

### 13.2 Passive Fields

Each entry in the `passives` array defines one passive ability:

| Field | Description |
|-------|-------------|
| `ability` | Identifier matched by the probability function in `engine/styles.py` |
| `threshold` | Proficiency (0–100) required to unlock this passive |
| `trigger` | `"attack"` — fires when this entity attacks; `"defend"` — fires when defending; `"always"` — always-on stat bonus (handled directly, not via script ops) |
| `requires` | Another ability that must have fired this hit for this to trigger (used for chained passives like riposte→parry) |
| `message` | Text emitted when the passive fires. Use `{npc}` as a placeholder for the NPC's name. Messages are written from the player's perspective and automatically adapted when an NPC uses the style. |
| `on_activate` | Array of combat-only script ops to execute when the passive fires (see §13.3) |

### 13.3 Combat-Only Script Ops

These ops are only valid inside a passive `on_activate` array. They are silently
ignored if used outside of combat.

| Op | Attributes | Effect |
|----|-----------|--------|
| `block_damage` | — | Sets the current hit damage to 0 and marks the hit as blocked |
| `multiply_damage` | `multiplier` | Multiplies current hit damage (e.g. `2.0` for vital strike) |
| `reduce_damage` | `percent` | Reduces current hit damage by N% (e.g. `percent = 30`) |
| `counter_damage` | `multiplier` | Deals `attacker_atk * multiplier` back to the opponent |
| `skip_npc_attack` | — | NPC loses its attack this round (stun / knockback) |
| `apply_combat_bleed` | — | Starts a bleed effect on the target (1–3 damage per round) |
| `heal_self` | `multiplier` | Heals the passive user for `counter_damage * multiplier` HP |

**Example — full passive chain (Flowing Water):**
```toml
passives = [
  { ability = "stillness", threshold = 1,  trigger = "always",  message = "" },
  { ability = "redirect",  threshold = 40, trigger = "defend",
    message = "You redirect the attack — using their force against them!",
    on_activate = [{op = "counter_damage", multiplier = 0.7}, {op = "block_damage"}] },
  { ability = "absorb",    threshold = 75, trigger = "defend", requires = "redirect",
    message = "You absorb the impact, recovering HP!",
    on_activate = [{op = "heal_self", multiplier = 0.33}] },
]
```

Here `absorb` only fires if `redirect` also fired this round (`requires = "redirect"`).
The `heal_self` op heals for a fraction of the counter damage `redirect` dealt.

---

## 14. Crafting & Commissions

Crafting lets NPCs produce custom gear when given materials.

### 14.1 File Layout

`data/<world_id>/<zone>/crafting/<npc_id>.toml` — The filename must match the crafter NPC's `id`.

### 14.2 Commission Format

```toml
[[commission]]
id             = "war_sword"
npc_id         = "town_blacksmith"
label          = "War Sword"
desc           = "A heavy fighting sword, well-balanced for sustained combat."
slot           = "weapon"
weapon_tags    = ["sword", "heavy"]
materials      = ["iron_ore", "iron_ore", "coal_chunk"]
turns_required = 30
gold_cost      = 0
xp_reward      = 50

[[quality]]
tier         = "poor"
weight       = 20       # Probability weight
attack_bonus = 2
equip_msg    = "The blade is rough but serviceable."
name_prefix  = "Rough "

[[quality]]
tier         = "standard"
weight       = 55
attack_bonus = 5
equip_msg    = "A solid, workmanlike blade."

[[quality]]
tier         = "exceptional"
weight       = 20
attack_bonus = 8
special      = "sharp"  # Tag appended to weapon_tags on finished item
equip_msg    = "The edge is remarkably fine."
name_prefix  = "Fine "

[[quality]]
tier         = "masterwork"
weight       = 5
attack_bonus = 12
special      = "sharp"
equip_msg    = "This is a masterwork piece."
name_prefix  = "Masterwork "
```

### 14.3 Commission Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Commission identifier |
| `npc_id` | string | Must match the crafter NPC's `id` |
| `label` | string | Item name base (quality prefix prepended) |
| `desc` | string | Item description |
| `slot` | string | Equipment slot for finished item |
| `weapon_tags` / `armor_tags` | list | Tags on finished item |
| `materials` | list | Item IDs the player must provide (may repeat) |
| `turns_required` | int | Player moves before commission is ready |
| `gold_cost` | int | Upfront gold deposit |
| `xp_reward` | int | XP awarded on collection |

### 14.4 Quality Fields

| Field | Type | Description |
|-------|------|-------------|
| `tier` | string | `"poor"`, `"standard"`, `"exceptional"`, `"masterwork"` |
| `weight` | int | Probability weight (higher = more common) |
| `attack_bonus` / `defense_bonus` / `max_hp_bonus` / `carry_bonus` | int | Stat bonuses added as effects |
| `special` | string | Extra tag added to weapon/armor_tags |
| `equip_msg` | string | Message shown when item is equipped |
| `name_prefix` | string | Prepended to `label` for final item name |

---

## 15. Companions

Companions travel with the player and optionally assist in combat.

### 15.1 Three Companion Tiers

| Tier | Carries items? | Fights? | Enters all rooms? |
|------|---------------|---------|-------------------|
| `narrative` | No | No | Yes |
| `utility` | Yes (carry bonus) | No | No (blocked by `no_large_companion`) |
| `combat` | Yes | Yes | No (blocked by `no_large_companion`) |

### 15.2 Companion File Format

`data/<world_id>/<zone>/companions/<companion_id>.toml`:

```toml
id              = "ranger_mira"
name            = "Ranger Mira"
type            = "combat"
desc_short      = "A capable scout with a hunter's eye."
attack          = 10
defense         = 6
hp              = 50
max_hp          = 50
carry_bonus     = 0
style           = "shadowstrike"
style_prof      = 40.0
restrictions    = ["no_large_companion"]
join_message    = "Mira nods. 'Lead the way.'"
wait_message    = "Mira waits at the entrance."
rejoin_message  = "Mira rejoins you."
downed_message  = "Mira is too injured to continue. She will recover with rest."
```

### 15.3 Companion Lifecycle

- **Active** — travels, carries, fights
- **Downed** — HP reached 0; rests at an inn to recover
- **Waiting** — blocked by room restriction; rejoins when player returns to valid room
- **Dismissed** — removed; can be re-acquired through dialogue

### 15.4 Script Usage

```toml
# Give companion via dialogue
{ op = "give_companion", companion_id = "ranger_mira" }

# Dismiss companion
{ op = "dismiss_companion", message = "She nods once and walks away." }
```

---

## 16. Validation Tool

Run `tools/validate.py` after editing data files to catch problems before playing.

```
python tools/validate.py
```

### What It Checks

**Rooms:**
- Every room has `id`, `name`, `description`
- All exit targets are valid room IDs
- All item references in `items` are valid IDs
- All spawn references are valid NPC IDs
**Items:**
- Every item has `id` and `name`

**NPCs:**
- Required fields: `id`, `name`, `hp`, `max_hp`, `attack`, `defense`, `xp_reward`,
  `gold_reward`, `hostile`, `tags`, `style`, `style_prof`, `desc_short`, `desc_long`
- All shop item IDs exist
- All `give_accepts` item IDs exist
- All style references exist

**Dialogue:**
- Every tree has a `root` node
- All `next = "..."` values point to existing nodes
- No orphan nodes unreachable from root

**Quests:**
- Quest files are valid TOML
- Referenced item IDs exist

**World:**
- Exactly one room has `start = true`
- At least one room per zone has the `town` flag (warning)

### Interpreting Output

```
✓  millhaven   — 8 rooms, 10 NPCs, 23 items
✗  greenhollow — room 'secret_cave': exit 'east' → 'missing_room' not found
⚠  blackfen    — NPC 'fog_wraith': no dialogue source
```

- `✓` = zone passed all checks
- `✗` = error (game may break)
- `⚠` = warning (game still works, but something may be wrong)

Run with `--world <id>` to check a single world:
```
python tools/validate.py --world first_world
```

---

## 17. Light Mechanic

Rooms can have a light level that determines what players can see. Items can
carry a light source (or drain). Scripts can change light levels dynamically.

### 17.1 Room Light

Add `light = N` to any room (integer 0–10). Default is `10` (fully lit).
```toml
[[room]]
id          = "cave_entrance"
name        = "Cave Entrance"
description = "A gaping maw of darkness opens before you."
light       = 2
```

### 17.2 Item Light

Add `light_add = N` to any item. Positive values are light sources; negative
values reduce effective light (blindfold, cursed hood, etc.):
```toml
[[item]]
id        = "torch"
name      = "Torch"
light_add = 5      # adds 5 to effective light while equipped
slot      = "cape" # or whatever slot makes sense

[[item]]
id        = "blindfold"
name      = "Blindfold"
light_add = -10    # negates nearly all light
slot      = "head"
```

**Effective light** = `room.light + sum(equipped item light_add values)`.

### 17.3 Player Vision

Each player has a `vision_threshold` (default: `vision_threshold` from `config.toml`,
usually `3`). When `effective_light < vision_threshold` the player is blind.

**Effects of blindness:**
- `look`: "It is pitch black. You cannot see anything."
- `examine <target>`: "You cannot see well enough to make that out."
- `map`: all cells show as `[???]`
- Room items are not listed (but can still be picked up by name)
- Combat: 20% chance to miss per attack

### 17.4 Light Script Ops

```toml
# Set a specific room's light level (absolute)
{op = "set_room_light", room_id = "cave_entrance", value = 2}

# Adjust the current room's light (clamps 0–10)
{op = "adjust_light", amount = -3}

# Branch on effective light (true when effective_light <= max)
{op = "if_light", max = 3, then = [
  {op = "message", text = "It's too dark to read the inscription."}
], else = [
  {op = "message", text = "You examine the inscription carefully."}
]}

# Set player vision threshold (absolute)
{op = "set_vision", amount = 2}

# Adjust vision threshold (darkvision potion, blindness curse, etc.)
{op = "adjust_vision", amount = -1}
```

---

## 18. World-Defined Player Attributes

Worlds can define custom numeric attributes for players. These appear in the
`stats` command and can be read and modified by scripts.

### 18.1 Defining Attributes

In `config.toml`, add one `[[player_attrs]]` entry per attribute:
```toml
[[player_attrs]]
id      = "corruption"
min     = 0
max     = 100
default = 0
display = "bar"     # "bar" = [####....] or "number" = raw integer

[[player_attrs]]
id      = "resonance"
min     = 0
max     = 10
default = 5
display = "number"
```

### 18.2 Stats Display

Attributes appear at the bottom of the `stats` command output.
- `display = "bar"` renders as `[######..........] 60/100`
- `display = "number"` renders as a plain integer

### 18.3 Attribute Script Ops

```toml
# Set an attribute to a specific value (clamped to min/max)
{op = "set_attr", name = "corruption", value = 50}

# Adjust by an amount (positive or negative, clamped)
{op = "adjust_attr", name = "corruption", amount = 10}

# Branch on attribute value
{op = "if_attr", name = "corruption", min = 50, max = 100, then = [
  {op = "message", text = "The darkness in you grows stronger."}
], else = [
  {op = "message", text = "You feel relatively clear-headed."}
]}
```

The `if_attr` condition is true when the attribute's current value falls
within `[min, max]` inclusive.

---

## 19. Standalone Script Files

Script ops can live in separate `.toml` files and be invoked by name. This
lets you create one-shot world events, batch admin changes, or complex post-quest
upgrades without embedding large script arrays in NPC data.

### 19.1 Script File Format

Create a file anywhere under the world root (convention: `scripts/`):
```toml
# data/first_world/scripts/drake_returns.toml
ops = [
  {op = "message",    text = "The ground shakes as the Drake returns to Ashwood!"},
  {op = "set_flag",   flag = "drake_returned"},
  {op = "spawn_item", item_id = "drake_scale", room_id = "ashwood_clearing"},
]
```

### 19.2 Inline Invocation

Any script can run a file using `run_script_file`. The path is relative to the world root:
```toml
{op = "run_script_file", path = "scripts/drake_returns.toml"}
```

### 19.3 CLI Tool

Run a script against a specific character from the command line:
```bash
# Run a script on an existing player
python tools/run_script.py scripts/drake_returns.toml --player Arthen

# Run a script, specifying the world explicitly
python tools/run_script.py scripts/event.toml --player Arthen --world first_world

# Preview without saving
python tools/run_script.py scripts/event.toml --player Arthen --dry-run

# Create the player if they don't exist yet
python tools/run_script.py scripts/welcome.toml --player NewHero --create
```

The tool loads the world, loads the player, executes the ops, and saves the player.
All OUTPUT messages are printed to the console during execution.

---

## Appendix A — Room Flags

Add any of these string values to a room's `flags` array.

| Flag | Effect |
|------|--------|
| `town` | Auto-updates player's respawn point (`bind_room`) on entry. Use on inns, squares, safe shelters. Combine with `no_combat` in most cases. |
| `no_combat` | Blocks the `attack` command entirely. Use in social spaces, inns, council halls. |
| `safe_combat` | Player takes zero damage, but XP and gold are awarded normally. Use for training rooms. |
| `healing` | Player regains `heal_rate` HP per command (default 5). Combine with `heal_rate` field. |
| `reduced_stats` | All combatants fight with half attack and defense. Use for sparring rings or dampened magic areas. |
| `sleep` | Player can `sleep`/`rest` here without an innkeeper NPC. Use for campsites, player homes. |
| `hazard` | Deals `hazard_damage` HP per command (default 2). Use for poison, fire, or caustic areas. Combine with `hazard_damage`, `hazard_message`, `hazard_exempt_flag`. |
| `no_large_companion` | Blocks utility and combat companions. Use for tight caves, cliff faces, or crawl spaces. Narrative companions always enter. |

Multiple flags can be combined:
```toml
flags = ["town", "no_combat", "healing"]
```

---

## Appendix B — Equipment Slots

Equipment slots are **world-configurable** via `equipment_slots` in `config.toml`. The table
below lists the default slots. A world can add, remove, or rename slots freely — `item.slot`
values must match an entry in the world's slot list.

| Slot | Items that use it |
|------|-------------------|
| `weapon` | Swords, axes, knuckles, staves |
| `head` | Helmets, hoods, caps |
| `chest` | Breastplates, robes, shirts |
| `legs` | Greaves, trousers |
| `arms` | Bracers, vambraces, gloves |
| `armor` | Full-body armour (older slot; prefer chest/legs for new content) |
| `shield` | Bucklers, tower shields |
| `cape` | Cloaks, mantles |
| `ring` | Rings, bracelets |
| `pack` | Bags, satchels — adds `carry_capacity` bonus |
| `""` | Non-equippable (consumables, quest items, materials) |

Items with `slot = ""` can only be used via the `use` command (consumables) or given
to NPCs. Equipping an item into a slot replaces the previous item in that slot.

---

## Appendix C — All Script Ops (Quick Reference)

53 ops total. Unknown op names are **silently ignored** — forward-compatible with new engine versions.

**Output**

| Op | Key attributes | Description |
|----|---------------|-------------|
| `say` | `text` | Dialogue-tagged text |
| `message` | `text`, `tag` | Text with any tag (default: `system`) |

**Player flags**

| Op | Key attributes | Description |
|----|---------------|-------------|
| `set_flag` | `flag` | Add flag |
| `clear_flag` | `flag` | Remove flag |
| `if_flag` / `if` | `flag`, `then`, `else` | Branch on flag |
| `if_not_flag` | `flag`, `then`, `else` | Branch on flag absence |

**Gold / XP / HP**

| Op | Key attributes | Description |
|----|---------------|-------------|
| `give_gold` | `amount` | Award gold |
| `take_gold` | `amount`, `silent` | Deduct gold (fails if short) |
| `give_xp` | `amount`, `silent` | Award XP |
| `heal` | `amount` | Restore HP (capped at max) |
| `set_hp` | `amount` | Set HP exactly |
| `damage` | `amount`, `message`, `silent` | Deal direct damage |

**Inventory**

| Op | Key attributes | Description |
|----|---------------|-------------|
| `give_item` | `item_id` | Copy item to inventory |
| `take_item` | `item_id` | Remove item from inventory |
| `spawn_item` | `item_id` | Place item in current room |
| `if_item` | `item_id`, `then`, `else` | Branch on inventory item |
| `require_tag` | `tag`, `fail_message` | Abort if no item with tag |

**Quests**

| Op | Key attributes | Description |
|----|---------------|-------------|
| `advance_quest` | `quest_id`, `step` | Start or advance quest |
| `complete_quest` | `quest_id` | Complete quest and award rewards |
| `if_quest` | `quest_id`, `step`, `then`, `else` | Branch on quest step |
| `if_quest_complete` | `quest_id`, `then`, `else` | Branch on quest completion |

**Styles & world**

| Op | Key attributes | Description |
|----|---------------|-------------|
| `teach_style` | `style_id` | Teach fighting style |
| `unlock_exit` | `room_id`, `direction` | Unlock a door |
| `lock_exit` | `room_id`, `direction` | Lock a door |
| `teleport_player` | `room_id`, `message` | Move player to any room |
| `move_npc` | `npc_id`, `to_room` | Move NPC between rooms |
| `move_item` | `item_id`, `to_room`, `from_room` | Move room item |

**Companions**

| Op | Key attributes | Description |
|----|---------------|-------------|
| `give_companion` | `companion_id` | Assign companion |
| `dismiss_companion` | `message` | Remove companion |

**Skills**

| Op | Key attributes | Description |
|----|---------------|-------------|
| `skill_check` | `skill`, `dc`, `on_pass`, `on_fail`, `silent`, `grow` | d20 skill roll |
| `if_skill` | `skill`, `min`, `then`, `else` | Branch on skill value |
| `skill_grow` | `skill`, `amount` | Directly increase skill |

**Status effects**

| Op | Key attributes | Description |
|----|---------------|-------------|
| `apply_status` | `effect`, `duration` | Apply status effect (poisoned / blinded / weakened / slowed / protected) |
| `clear_status` | `effect` | Remove status effect |
| `if_status` | `effect`, `then`, `else` | Branch on effect presence |

**Prestige**

| Op | Key attributes | Description |
|----|---------------|-------------|
| `prestige` | `amount`, `reason` | Adjust prestige score |
| `add_affinity` | `tag` | Grant affinity tag |
| `remove_affinity` | `tag` | Remove affinity tag |
| `if_prestige` | `min`, `max`, `then`, `else` | Branch on prestige range |
| `if_affinity` | `tag`, `then`, `else` | Branch on affinity |

**Bank & flow control**

| Op | Key attributes | Description |
|----|---------------|-------------|
| `bank_expand` | `tier` | Expand bank slots |
| `fail` | — | Abort script immediately |
| `journal_entry` | `title`, `text` | Add player journal entry |
| `run_script_file` | `path` | Run ops from a world-relative TOML file (see §19) |

**Combat round (NPC `round_script` only)**

| Op | Key attributes | Description |
|----|---------------|-------------|
| `if_combat_round` | `min`, `then`, `else` | Branch on round number |
| `if_npc_hp` | `max`, `then`, `else` | Branch on NPC HP percentage |
| `end_combat` | — | End fight; NPC survives at 1 HP |

**Player attributes (world-defined, see §18)**

| Op | Key attributes | Description |
|----|---------------|-------------|
| `set_attr` | `name`, `value` | Set attribute to value (clamped to min/max) |
| `adjust_attr` | `name`, `amount` | Add/subtract from attribute (clamped) |
| `if_attr` | `name`, `min`, `max`, `then`, `else` | Branch when attribute is in range |

**Light mechanic (see §17)**

| Op | Key attributes | Description |
|----|---------------|-------------|
| `set_room_light` | `room_id`, `value` | Set a room's light level (0–10) |
| `adjust_light` | `amount` | Adjust current room's light (clamped 0–10) |
| `if_light` | `max`, `then`, `else` | True when effective light <= max |
| `set_vision` | `amount` | Set player vision threshold |
| `adjust_vision` | `amount` | Adjust vision threshold (darkvision, blindness) |

**Combat-only passive ops (style `on_activate` only, see §13.3)**

| Op | Key attributes | Description |
|----|---------------|-------------|
| `block_damage` | — | Set current hit damage to 0 (parry, dodge) |
| `multiply_damage` | `multiplier` | Scale current hit damage |
| `reduce_damage` | `percent` | Reduce hit damage by N% |
| `counter_damage` | `multiplier` | Deal `attacker_atk * multiplier` back to opponent |
| `skip_npc_attack` | — | NPC loses its attack this round (stun, knockback) |
| `apply_combat_bleed` | — | Start bleed on target (1–3 damage/round) |
| `heal_self` | `multiplier` | Heal user for `counter_damage * multiplier` HP |

---

## Appendix D — Player Fields for Substitution

These fields can be referenced in dialogue substitutions as `{player.field}` or
via script conditionals.

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Character name |
| `hp` | int | Current HP |
| `max_hp` | int | Maximum HP |
| `attack` | int | Base attack stat |
| `defense` | int | Base defense stat |
| `level` | int | Character level |
| `xp` | int | Current XP toward next level |
| `gold` | int | Gold on hand |
| `room_id` | string | Current room ID |
| `flags` | set | All active flag strings |
| `active_quests` | dict | `{quest_id: step_number}` |
| `completed_quests` | set | Finished quest IDs |
| `skills` | dict | `{skill_id: value_0-100}` |
| `prestige` | int | Prestige score (−999 to +999) |
| `prestige_affinities` | list | Earned affinity tag strings |
| `status_effects` | dict | `{effect: turns_remaining}` |
| `inventory` | list | All carried item dicts |
| `equipped` | dict | Slot → item dict (or None) |
| `journal` | list | `[{"title": str, "text": str}]` |
| `companion` | dict | Active companion state (or None) |
| `bind_room` | string | Respawn room ID |
| `bank_slots` | int | Bank capacity |
| `banked_gold` | int | Gold in bank |

**Shorthand tokens** for dialogue `{token}`:
`{player}`, `{gold}`, `{hp}`, `{level}`, `{zone}`, `{npc}`, `{entity_id.field}`
