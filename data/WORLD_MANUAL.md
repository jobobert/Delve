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
20. [World Notes File](#20-world-notes-file-worldmd)
21. [World Processes](#21-world-processes)
22. [Appendix A — Room Flags](#appendix-a--room-flags)
23. [Appendix B — Equipment Slots](#appendix-b--equipment-slots)
24. [Appendix C — All Script Ops (Quick Reference)](#appendix-c--all-script-ops-quick-reference)
25. [Appendix D — Player Fields for Substitution](#appendix-d--player-fields-for-substitution)
26. [Appendix E — Quest Design Walkthrough](#appendix-e--quest-design-walkthrough)
27. [Appendix F — Companion Design Walkthrough](#appendix-f--companion-design-walkthrough)
28. [Appendix G — Crafting & Commission Design Walkthrough](#appendix-g--crafting--commission-design-walkthrough)
29. [Appendix H — Fighting Style Design Walkthrough](#appendix-h--fighting-style-design-walkthrough)

---

## 1. Project Overview

Delve is a text MUD engine built around one principle: **everything is data**. You never
touch Python to create content. Rooms, NPCs, items, dialogue, and quests are all defined
in TOML files under `data/`. The engine loads them at runtime.

**Key facts:**
- A **world** is a subfolder of `data/` that contains a `config.toml` (e.g. `data/sixfold_realms/`).
- **Zones** are subfolders of the world folder. Each zone streams in/out of memory independently.
- All NPC and item state is lazy — NPCs spawn on first player visit, not at startup.
- Zone state (NPC HP, moved items) persists per-player to `data/players/<name>/zone_state/<zone_id>.json`.
- Zero external dependencies. Run with `python main.py`.

**Test your work** with `python tools/validate.py` before playing.

---

## 2. Zone Structure & File Layout

A **world** is a subfolder of `data/` identified by a `config.toml` inside it. **Zones** are
subfolders of the world. The zone folder's name becomes the zone ID.

```
data/
  players/                     # Per-character folders (gitignored)
    <name>/
      player.toml              # Character save (cross-world; records world_id)
      zone_state/              # Live zone snapshots — per-player (gitignored)
        <zone_id>.json
  <world_id>/                  # World folder — identified by config.toml
    config.toml                # World name, skills, currency, default style, slots, vision_threshold, player_attrs, status_effects
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
| `[[status_effect]]` | array | World-defined status effects — see [Section 11](#11-status-effects) |

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
admin_comment = """
## Story Driver ##
Document the zone's narrative purpose, faction involvement, and design notes here.
This field is edited in the WCT Zone Notes panel and is not read by the engine.
"""
```

The engine discovers zones by folder presence. The zone.toml is advisory/flavor only.

`admin_comment` is an optional free-form markdown string for world-building notes. It is
written and read by the WCT's "Zone Notes" panel (accessible via the **Notes** button on
each zone header). The engine never reads this field. If `zone.toml` does not exist, the
WCT will create it when you first save notes.

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
| `on_sleep` | array | No | `[]` | Script ops run when the player sleeps here (before healing — use for flavor, check conditions) |
| `on_wake` | array | No | `[]` | Script ops run when the player wakes up here (after healing — use for morning flavor, weather, events) |
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
| `lock_tag` | string | `""` | Tag matched against item `tags` array for unlock |
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
| `tags` | list | No | Item tags; used by `require_tag` script op, style affinity, and door unlocking (any tag matching a door's `lock_tag` opens it) |
| `weapon_tags` | list | No | Tags for fighting-style weapon affinity matching |
| `armor_tags` | list | No | Tags for fighting-style armor affinity matching |
| `respawn` | bool | No | `false` = removed after pickup, tracked in player's looted set |
| `no_drop` | bool | No | `true` = cannot be dropped; stays with player on death |
| `scenery` | bool | No | `true` = visible but cannot be picked up |
| `gold_value` | int | No | Selling price to NPC merchants |
| `effects` | list | No | Passive or triggered effects — see [Section 6.2](#62-item-effects) |
| `on_get` | array | No | Script run when player picks up this item |
| `on_drop` | array | No | Script run when player drops this item |
| `commands` | list | No | Custom verbs the player can type — see [Section 6.4](#64-item-commands) |

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
tags        = ["barracks_lock"]
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

### 6.4 Item Commands

Items can define custom verbs that the player can type when they have the item in
their inventory **or** are in the same room as it. This lets you create interactive
scenery and puzzle objects without any engine changes.

Each command is a `[[commands]]` block inside the `[[item]]` block:

```toml
[[item]]
id         = "old_chest"
name       = "Old Chest"
desc_short = "A heavy oak chest, banded with rusted iron."
slot       = ""
scenery    = true      # can't be picked up

[[item.commands]]
verb    = "open"
visible = true         # shows as "(you can: open)" in room and examine output
ops     = [
  { op = "if_flag", flag = "chest_unlocked",
    then = [
      { op = "message", text = "You lift the heavy lid." },
      { op = "give_item", item_id = "silver_key" },
      { op = "set_flag",  flag = "chest_looted" },
    ],
    else = [
      { op = "message", text = "The chest is locked tight." },
    ]
  },
]

[[item.commands]]
verb    = "examine"
visible = false        # hidden — player must discover it
ops     = [
  { op = "if_flag", flag = "chest_unlocked",
    then = [{ op = "message", text = "The lock mechanism has been forced." }],
    else = [{ op = "message", text = "The lock bears a crest: three moons." }],
  },
]
```

**Command fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `verb` | string | **Yes** | The command word the player types |
| `visible` | bool | No | `true` = shown in room and examine output; `false` (default) = hidden |
| `ops` | array | No | Script ops to run (empty ops = no-op but verb is still consumed) |

**Scope rules:**
- Inventory items: command available anywhere (player carries the item)
- Room items (including scenery): command available only in that room
- If both an inventory item and a room item define the same verb, the inventory
  item wins (checked first)
- Built-in engine verbs always take priority over item commands

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
| Item | `commands[].ops` | Player types the item's custom verb |
| Room | `on_enter` | Player enters the room |
| Room | `on_sleep` | Player sleeps/rests here — fires before healing |
| Room | `on_wake` | Player wakes up here — fires after healing |
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
| `on_sleep` / `on_wake` (room) | No | No |
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

### 7.5 All Script Operations (~63 ops)

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

**`spawn_npc`** — Spawn a live copy of an NPC into a room

```toml
{ op = "spawn_npc", npc_id = "training_dummy" }
{ op = "spawn_npc", npc_id = "patrol_guard", room_id = "barracks" }
```

`room_id` defaults to `"current"` (the player's current room).  The NPC is added to the room's live `_npcs` list immediately.  The room must already be loaded; if the zone has been evicted the call is a silent no-op.  Spawned NPCs with `respawn = false` do not reappear after being defeated.

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
| `skill` | string | — | A skill ID defined in the world's `config.toml` (e.g. `perception`, `stealth`, `mining`) |
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
| `effect` | string | Yes | A status effect `id` defined in the world's `config.toml` `[[status_effect]]` blocks |
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

#### CUTSCENE

**`pause`** — Pause briefly for dramatic effect

```toml
{ op = "pause", seconds = 0.5 }
```

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `seconds` | float | `0.5` | Duration in seconds; reasonable values are 0.2–1.0 |

Emits a timing signal that the frontend sleeps on. The engine itself never
calls `time.sleep()` — a headless or web frontend can ignore it safely.

*Typical pattern — gated first-entry cutscene:*
```toml
on_enter = [
  { op = "if_not_flag", flag = "cave_intro_seen", then = [
    { op = "message", tag = "system",   text = "The cold hits you before you see the chamber." },
    { op = "pause", seconds = 0.5 },
    { op = "message", tag = "room_desc", text = "Something massive stirs in the darkness." },
    { op = "pause", seconds = 0.4 },
    { op = "message", tag = "room_desc", text = "Eyes open. Many of them." },
    { op = "set_flag", flag = "cave_intro_seen" },
  ] },
]
```

The `if_not_flag` guard ensures the cutscene plays only on the first visit.

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
id               = "ashwood_contract"
title            = "The Ashwood Contract"
giver            = "elder_mira"               # Informational only
summary          = "Investigate the corruption in Ashwood Forest and slay the Drake."
start_message    = "The forest roads are closed — travel carefully."  # optional
complete_message = "Ashwood breathes easier. Well done."              # optional

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
| `start_message` | string | **Optional.** Extra line shown in the quest start banner (after summary). If omitted, only the summary is shown. |
| `complete_message` | string | **Optional.** Line shown in the quest completion banner. Default: `"Well done, adventurer."` |
| `[[step]]` | — | One or more objective steps |
| `step.index` | int | Step number (must be ≥ 1); gaps are fine |
| `step.objective` | string | What the player must do |
| `step.hint` | string | Optional hint shown in journal |
| `step.on_advance` | array | Script ops run automatically when this step is reached |
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

**`on_advance` — automatic step scripts**

Each quest step can carry an `on_advance` array that fires automatically the moment
the player reaches that step (run by the engine as part of `advance_quest`). Use it
to give items, send messages, or trigger world changes the instant a milestone is hit:

```toml
[[step]]
index      = 3
objective  = "Retrieve the signet ring."
on_advance = [
  { op = "message", text = "The hermit's directions flash through your memory.", tag = "quest" },
  { op = "give_item", item_id = "hermit_map" },
]
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

Skills are **world-configurable** — the full list is defined in the `[skills]` table inside the
world's `config.toml`. The engine uses whatever skills are listed there; no engine code changes are needed.

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

Status effects are **fully world-defined** in `config.toml` as `[[status_effect]]` blocks.
Each world ships its own set; the engine has a fallback effect list.

### Defining status effects in config.toml

```toml
[[status_effect]]
id              = "poisoned"       # key used in script ops
label           = "Poisoned"       # display name on stats screen
apply_msg       = "Poison courses through you."
expiry_msg      = "The poison runs its course."
combat_atk      = 0               # attack modifier while active (negative = debuff)
combat_def      = 0               # defense modifier while active
damage_per_move = 3               # HP damage per command tick (0 = none)

[[status_effect]]
id              = "hexed"
label           = "Hexed"
apply_msg       = "A dark curse settles on you."
expiry_msg      = "The hex lifts."
combat_atk      = -3
combat_def      = -2
damage_per_move = 0
```

If a world defines no `[[status_effect]]` blocks, the engine falls back to five built-in
defaults (poisoned, blinded, weakened, slowed, protected) so the game remains playable
without configuration.

### Default effects (first_world baseline)

| Effect | `combat_atk` | `combat_def` | `damage_per_move` |
|--------|:------------:|:------------:|:-----------------:|
| `poisoned` | 0 | 0 | 3 HP/move |
| `blinded` | −4 | 0 | — |
| `weakened` | −4 | 0 | — |
| `slowed` | 0 | −3 | — |
| `protected` | 0 | +3 | — |

**Duration:** Turns remaining (one per active command). `-1` = permanent until `clear_status`.

> **Read-only commands don't tick.** Passive information commands (`look`, `inventory`,
> `stats`, `map`, `journal`, `skills`, `help`, `examine`, `commissions`, `bank`,
> `alias`/`aliases`, `save`) do **not** advance status effect durations or deal
> `damage_per_move` damage. Only commands that interact with the world (movement, combat,
> picking up items, talking, buying, crafting) count as a turn for status purposes.
> This means a player checking their stats or map while poisoned is not penalised for
> doing so.

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

### 16.2 Inspecting Objects in Python

You can load world objects directly in a Python script for debugging and one-off
testing — the same way `validate.py` does it internally. Run these scripts from
the project root so that `import engine.*` resolves correctly.

**Minimal bootstrap — load config and inspect a TOML record:**

```python
import sys
from pathlib import Path

sys.path.insert(0, ".")                          # project root on sys.path
from engine.toml_io import load as toml_load
import engine.world_config as wc

DATA_DIR  = Path("data")
WORLD_ID  = "first_world"
world_path = DATA_DIR / WORLD_ID

wc.init(world_path)   # load config.toml — must be called before any engine code

# Load a TOML file and pull one record by id
data = toml_load(world_path / "blackfen" / "npcs.toml")
npc  = next((n for n in data.get("npc", []) if n["id"] == "anurus_the_returned"), None)
print(npc)
```

**Load the live World object (room lookups, exits, NPC spawns):**

```python
from engine.world import World

world = World(world_path)
room  = world.get_room("millhaven_square")
print(room["name"], "light:", room.get("light", 10))

# Iterate every item in every zone
for zone_id, zone in world.zones.items():
    for item in zone.get("items", {}).values():
        print(zone_id, item["id"], item.get("slot", "—"))
```

**Load a saved player:**

```python
from engine.player import Player

player = Player.load("YourCharacter")
print(player.name, "HP:", player.hp, "/", player.max_hp)
print("Equipped:", player.equipped)
print("Skills:",  player.skills)
```

**Create a throwaway player for testing (not saved unless you call `player.save()`):**

```python
player = Player.create_new("TestChar")
player.world_id = WORLD_ID
# Inspect or mutate freely; omit player.save() to discard all changes.
```

**Tip:** the engine never calls `print()` directly — all output flows through
`EventBus` / `Msg`. Inspecting engine state in a script is silent by default
unless you `print()` the results yourself.

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

## 20. World Notes File (world.md)

Each world can have an optional `world.md` file at `data/<world_id>/world.md`. This is a Markdown document for world-level lore, design notes, NPC backstory, cross-zone trade routes, quest relationships, and anything else a world creator wants to record.

### 20.1 Location

```
data/<world_id>/world.md
```

### 20.2 Usage

- **WCT**: Click **World Notes** in the top bar to open a fullscreen Markdown editor. Changes are saved to `world.md` immediately.
- **world2html**: The content of `world.md` is rendered between the summary table and the first zone section when generating the review HTML.
- **Engine**: `world.md` is not read by the engine at runtime — it is purely a tool for world authors.

### 20.3 Recommended Sections

```markdown
# World Name

## Overview
Brief pitch for the world.

## Regions
Per-zone narrative summaries and design goals.

## Quest Relationships
Cross-zone quest dependencies and flag usage.

## Item Notes
Unusual item behaviours, cross-zone commission materials.

## Tags
Tag naming conventions used in this world.
```

---

## 21. World Processes

World processes are recurring scripts or NPC-route drivers that fire automatically on player-action ticks. They enable time-passing simulation — weather changes, NPC patrols, shifting exits, caravan routes — without any background threads or clock dependency.

Processes fire on the same tick used by status effects (every non-read-only player command). They are per-player: each character has their own process state persisted in their zone_state folder.

### 21.1 Defining a Process

Create a `processes.toml` file inside any zone folder. Each `[[process]]` block defines one process.

```toml
[[process]]
id            = "caravan_route"
name          = "Merchant Caravan"
interval      = 5          # fire every 5 action ticks (default 1)
autostart     = false      # start automatically on world load (default false)
admin_comment = "Caravan moves through town on a loop"
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | **Yes** | Unique process identifier |
| `name` | string | No | Display name (for tooling) |
| `interval` | int | No | Fire every N action ticks; default `1` |
| `autostart` | bool | No | If `true`, process starts active immediately; default `false` |
| `admin_comment` | string | No | Design notes (not shown in-game) |
| `script` | array | No | Inline script ops to run on each fire (Option A) |
| `script_file` | string | No | World-relative path to a TOML ops file (Option A2) |
| `script_py` | string | No | World-relative path to a Python `.py` file (Option A3) |
| `route_npc` | string | No | NPC id to move along the route (Option B) |
| `route_loop` | string | No | `"cycle"` (default) or `"reverse"` |
| `route` | array | No | List of `{room_id, ticks}` waypoints (Option B) |

### 21.2 Option A — Script-Based Process

A script process runs a list of ops every time it fires. Use this for weather messages, periodic flag checks, room-light changes, or any timed effect.

```toml
[[process]]
id       = "storm_cycle"
name     = "Storm Cycle"
interval = 20
autostart = true
script = [
  { op = "message", text = "Thunder rolls across the hills.", tag = "system" },
  { op = "adjust_light", amount = -1 },
]
```

For longer or reusable scripts, use `script_file` to point to an external TOML ops file instead of embedding an inline array.

#### 21.2.1 External Script Files (script_file)

`script_file` takes a world-relative path to a TOML file whose top-level key is `ops` or `script`. The engine loads and runs the file through the same ScriptRunner that handles inline ops — any op that works inline works in a file.

**When to use `script_file`:**
- The process logic is more than 5–6 ops (inline becomes hard to read)
- The same script is shared by more than one process
- You want to edit the logic without touching `processes.toml`

**Recommended layout:**

```
data/<world_id>/
  scripts/
    weather_tick.toml
    patrol_events.toml
    caravan_checks.toml
  <zone_id>/
    processes.toml
    ...
```

**Process definition:**
```toml
[[process]]
id          = "patrol_events"
name        = "Patrol Events"
interval    = 10
autostart   = true
script_file = "scripts/patrol_events.toml"
```

**Script file (`data/<world_id>/scripts/patrol_events.toml`):**
```toml
# Top-level key must be "ops" or "script"
ops = [
  { op = "if_flag", flag = "patrol_alert",
    then = [
      { op = "message", text = "Boots ring on the cobblestones outside.", tag = "system" },
      { op = "adjust_light", amount = -1 },
    ],
    else = [
      { op = "adjust_light", amount = 1 },
    ]
  },
]
```

Both `script` and `script_file` may be defined on the same process; the inline `script` runs first, then the file. If the file is missing or cannot be parsed, it is silently skipped.

#### 21.2.2 Python Script Files (script_py)

`script_py` points to an actual Python `.py` file. The file must define a top-level `run(ctx)` function. The engine loads and executes it on every process fire, passing in the full game context. This enables integrations that are impossible in TOML ops — HTTP requests, logging, file output, Discord/Slack webhooks, email, or any Python library.

**Context object passed to `run(ctx)`:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `ctx.player` | `Player` | Current player (read/write — changes persist) |
| `ctx.world` | `World` | Zone/room/NPC data |
| `ctx.bus` | `EventBus` | Emit messages to the player's frontend |
| `ctx.quests` | `QuestTracker` | Read quest state |
| `ctx.processes` | `ProcessManager` | Start/stop/pause other processes |

**Emitting output to the player from Python:**
```python
from engine.events import Event
from engine.msg import Msg, Tag

def run(ctx):
    ctx.bus.emit(Event.OUTPUT, Msg(Tag.SYSTEM, "The bell tower tolls the hour."))
```

**Full example — Discord webhook on quest completion:**

Process definition:
```toml
[[process]]
id          = "discord_quest_log"
name        = "Discord Quest Logger"
interval    = 1
autostart   = false          # start via process_start op in a quest's complete script
script_py   = "scripts/discord_quest_log.py"
```

Script file (`data/<world_id>/scripts/discord_quest_log.py`):
```python
import requests  # third-party; install with: pip install requests

WEBHOOK_URL = "https://discord.com/api/webhooks/YOUR_WEBHOOK_URL"

def run(ctx):
    p = ctx.player
    completed = sorted(p.completed_quests)
    if not completed:
        return
    last = completed[-1].replace("_", " ").title()
    try:
        requests.post(WEBHOOK_URL, json={
            "content": f"**{p.name}** completed **{last}** (prestige: {p.prestige})"
        }, timeout=3)
    except Exception:
        pass  # never let network errors crash the game
```

**Rules and safety:**
- The `.py` file is reloaded from disk on **every** fire, so edits take effect immediately without restarting the server.
- All exceptions are caught silently — a broken script never crashes the game or interrupts the player.
- `ctx.player` is the live player object; writes to it (e.g. setting flags, adjusting gold) take effect immediately and are saved on the next player save.
- The script runs synchronously on the game tick, so keep network calls short or fire-and-forget with a timeout. Long-running operations will delay the player's response.
- No sandboxing is applied. Scripts have full Python access. Only use `script_py` with trusted content.

### 21.3 Option B — NPC Route (Caravan / Patrol)

A route process walks an NPC through a list of waypoints. The `ticks` field on each waypoint is how many process-fires the NPC waits at that room before moving.

```toml
[[process]]
id         = "traveling_merchant"
name       = "Traveling Merchant"
interval   = 3           # check route every 3 action ticks
autostart  = false       # started by quest trigger
route_npc  = "merchant_edvard"
route_loop = "cycle"     # loops back to start; use "reverse" for ping-pong
route = [
  { room_id = "millhaven_square", ticks = 4 },
  { room_id = "millhaven_gate",   ticks = 2 },
  { room_id = "millbrook_road",   ticks = 6 },
]
```

With `route_loop = "reverse"`, the NPC walks forward to the last waypoint then back to the first, then forward again.

Both `script` and `route` may appear on the same process — the route advances first, then the script runs.

**Route timing note:** With `interval = 3` and `ticks = 4`, the NPC stays at a waypoint for `3 × 4 = 12` player action commands before moving.

> **Zone eviction:** Route-based NPC movement uses `move_npc` internally and requires the NPC's current zone to be loaded. If a zone is evicted from memory while an NPC is mid-route, the NPC will reappear at its spawn point when the zone reloads. Plan routes within zones or between always-adjacent zones.

### 21.4 Controlling Processes from Scripts

Any script (dialogue, on_enter, item on_get, quest trigger, etc.) can start, stop, or pause a process.

```toml
# Start when player triggers a quest
{ op = "process_start", process_id = "caravan_route" }

# Pause during a cutscene or boss fight
{ op = "process_pause", process_id = "caravan_route" }

# Stop and reset all counters
{ op = "process_stop",  process_id = "caravan_route" }
```

`process_start` also resumes a paused process. `process_stop` resets tick counters so the process starts fresh if restarted.

### 21.5 State Persistence

Process state (active/paused, tick counters, route position) is saved to:

```
data/players/<name>/zone_state/_processes.json
```

It is loaded when `CommandProcessor` initialises and written whenever the player saves. Process state survives world reloads and game restarts.

### 21.6 Full Example — Weather Process

```toml
# data/first_world/millhaven/processes.toml

[[process]]
id        = "millhaven_weather"
name      = "Millhaven Weather"
interval  = 15
autostart = true
admin_comment = "Toggles a rain flag every ~15 commands. Quests check 'millhaven_raining'."
script = [
  { op = "if_flag", flag = "millhaven_raining",
    then = [
      { op = "clear_flag", flag = "millhaven_raining" },
      { op = "message", text = "The rain eases. Pale sunlight breaks through the clouds.", tag = "system" },
    ],
    else = [
      { op = "set_flag", flag = "millhaven_raining" },
      { op = "message", text = "Dark clouds gather. Rain begins to fall.", tag = "system" },
    ]
  }
]
```

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
| `sleep` | **Inn-quality rest** — `sleep`/`rest` here restores full HP (same as a paid inn). Use for campsites the player has made safe, player homes, or free rest areas. |
| `no_camp` | Blocks **all** resting in this room (even camping). Use for indoor spaces, dungeons, or anywhere that should require an inn. Without this flag, the player can always camp anywhere (with reduced healing — see below). |
| `hazard` | Deals `hazard_damage` HP per command (default 2). Use for poison, fire, or caustic areas. Combine with `hazard_damage`, `hazard_message`, `hazard_exempt_flag`. |
| `no_large_companion` | Blocks utility and combat companions. Use for tight caves, cliff faces, or crawl spaces. Narrative companions always enter. |

### Rest & Camping

The `sleep` / `rest` command has three modes:

| Situation | HP restored | Crafting ticks | Status effects |
|-----------|-------------|----------------|----------------|
| Innkeeper NPC present (paid) | Full max HP | 20 | All cleared |
| Room has `sleep` flag (free) | Full max HP | 20 | All cleared |
| **Camping** (any other room, no `no_camp` flag) | Up to +½ max HP | 10 | All cleared |

**Camping** is allowed by default anywhere that doesn't have `no_camp`. It always clears status
effects (a full night's rest resolves them) but only heals up to half your maximum HP — sleeping
rough without a proper bed is restorative but not complete recovery.

Use `on_sleep` and `on_wake` room scripts to add flavor:

```toml
# An inn room
on_sleep = [{ op = "message", text = "The innkeeper dims the lantern as you settle in.", tag = "dialogue" }]
on_wake  = [{ op = "message", text = "Morning light filters through the shutters.", tag = "system" }]

# A wilderness campsite
on_sleep = [{ op = "message", text = "You build a small fire and wrap yourself in your cloak.", tag = "system" }]
on_wake  = [
  { op = "message", text = "Dawn breaks grey over the hills. The fire has long since died.", tag = "system" },
  { op = "if_flag", flag = "storm_warning", then = [
      { op = "message", text = "The storm that was building last night has passed.", tag = "system" }
  ]},
]
```

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

63 ops total. Unknown op names are **silently ignored** — forward-compatible with new engine versions.

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
| `spawn_npc` | `npc_id`, `room_id` | Spawn live NPC in room (default: current) |
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
| `apply_status` | `effect`, `duration` | Apply a world-defined status effect by id (see §11) |
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

**Bank, cutscene & flow control**

| Op | Key attributes | Description |
|----|---------------|-------------|
| `bank_expand` | `tier` | Expand bank slots |
| `pause` | `seconds` | Cutscene timing pause (frontend sleeps N seconds) |
| `fail` | — | Abort script immediately |
| `journal_entry` | `title`, `text` | Add player journal entry |
| `run_script_file` | `path` | Run ops from a world-relative TOML file (see §19) |

**World processes (see §21)**

| Op | Key attributes | Description |
|----|---------------|-------------|
| `process_start` | `process_id` | Activate (or resume) a process |
| `process_stop` | `process_id` | Deactivate and reset tick counters |
| `process_pause` | `process_id` | Suspend without resetting counters |

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

---

## Appendix E — Quest Design Walkthrough

This appendix bridges the gap between **story design** and **technical implementation**.
Rather than listing fields again (see §9), it walks through the thought process of
turning a quest idea into working TOML — with concrete examples from the existing worlds.

---

### E.1 The Core Idea: Quests Are Just Flags + Steps

The engine has no magic quest logic. A quest is nothing more than:

1. A **TOML file** that declares objectives and rewards.
2. **Script ops** scattered throughout your world (`advance_quest`, `complete_quest`) that
   move the player's progress counter forward.
3. **Dialogue conditions** (`quest`, `not_quest`, `quest_complete`) that change what NPCs
   say based on where the player is in the quest.

The player never "clicks accept" on a quest panel. They talk to an NPC, the dialogue runs
a script, and `advance_quest` sets step = 1. That's the entire handshake.

---

### E.2 Design First: The Player's Journey

Before touching any TOML, write out the quest as the player will experience it. Don't
think about files or ops yet — just answer:

> *What does the player do? In what order? What happens when they finish?*

**Example — "The Ashwood Contract":**

```
1. Player talks to Elder Mira. She explains the corruption and offers gold.
2. Player visits Sergeant Vorn to get briefed on the forest.
3. Player travels into the Ashwood.
4. Player finds the Shrine and solves an ancient riddle.
5. Player descends to the Drake's Lair and kills the Drake.
6. Player returns to Mira with proof of the kill.
```

That's your step list. Write it down, then assign each moment an index number. Gaps are
fine — indices don't need to be consecutive. Use round numbers (1, 2, 3…) or leave room
for future steps (1, 3, 5, 10…).

---

### E.3 Create the Quest File

Make one `.toml` file in `data/<world>/<zone>/quests/<quest_id>.toml`. The zone doesn't
have to match the zone where the quest starts — pick the zone that "owns" the quest
thematically (usually where the climax is).

```toml
# data/ashwood/quests/ashwood_contract.toml

id      = "ashwood_contract"
title   = "The Ashwood Contract"
giver   = "elder_mira"
summary = "Investigate the corruption in the Ashwood Forest and slay the Ashwood Drake."

[[step]]
index     = 1
objective = "Speak with Sergeant Vorn in the Millhaven Barracks."
hint      = "Go up from the Town Square to reach the Barracks."

[[step]]
index     = 5
objective = "Descend to the Drake's Lair and slay the Ashwood Drake."
hint      = "Bring the best weapons you can find."

[[step]]
index     = 6
objective = "Return to Elder Mira with the Drake's fang as proof."
hint      = "Go back to town and speak with Elder Mira in the Council Hall."

[[reward]]
type   = "gold"
amount = 150

[[reward]]
type   = "xp"
amount = 400
```

The `giver` field is informational only — the engine doesn't enforce it. It's a note to
yourself and to the WCT quest graph.

---

### E.4 Wire Up the Triggers

Now go to every place in the world where a step should advance, and add an `advance_quest`
or `complete_quest` script op. There are four places this happens:

#### Trigger 1 — NPC Dialogue (most common)

The NPC's dialogue `[[node]]` carries a `script` array that fires when the node is
displayed. This is how quests start and how they end with a hand-in.

```toml
# elder_mira.toml — quest offer node
[[node]]
id     = "offer_quest"
line   = "The contract pays well. Find Sergeant Vorn in the barracks first."
script = [
  { op = "set_flag",      flag = "ashwood_quest_offered" },
  { op = "advance_quest", quest_id = "ashwood_contract", step = 1 },
]
```

```toml
# elder_mira.toml — hand-in node (player presents the fang)
[[node]]
id     = "victory"
line   = "By the old stones. Let me see the fang. You actually did it."
script = [{ op = "complete_quest", quest_id = "ashwood_contract" }]
```

Guard the offer node with a response condition so it only appears when appropriate:

```toml
[[response]]
node      = "root"
condition = { not_flag = "ashwood_quest_offered" }
text      = "Who are you? What's happening in the Ashwood?"
next      = "intro"
```

And guard the hand-in node so it only appears once the kill step is reached:

```toml
[[response]]
node      = "root"
condition = { flag = "ashwood_drake_slain" }
text      = "I have defeated the Ashwood Drake. Here is proof."
next      = "victory"
```

#### Trigger 2 — NPC Kill (`kill_script`)

When a combat NPC dies, its `kill_script` runs automatically. This is the cleanest way
to advance a kill-type objective — no room scripts needed.

```toml
# ashwood/npcs.toml
[[npc]]
id          = "ashwood_drake"
name        = "Ashwood Drake"
kill_script = [
  { op = "set_flag",      flag = "ashwood_drake_slain" },
  { op = "give_item",     item_id = "drakes_fang" },
  { op = "message",       text = "The Drake collapses. You pry a fang from its jaw.", tag = "combat_kill" },
  { op = "advance_quest", quest_id = "ashwood_contract", step = 6 },
]
```

Note that step 6 here is "Return to Elder Mira" — the kill *advances* the quest to the
return step, it doesn't complete it. Completion happens when the player talks to Mira.

#### Trigger 3 — Room Entry (`on_enter`)

Place an `on_enter` script on a room to advance the quest when the player first arrives
at a location. Use `if_not_flag` to make it fire only once.

```toml
# blackfen/rooms.toml
[[room]]
id       = "dragons_clearing"
on_enter = [
  { op = "if_not_flag", flag = "seen_dragons_clearing",
    then = [
      { op = "set_flag",      flag = "seen_dragons_clearing" },
      { op = "advance_quest", quest_id = "grandfathers_emerald", step = 3 },
      { op = "message",       text = "The clearing opens before you. The dead serpent hangs in the canopy above.", tag = "system" },
    ]
  },
]
```

Without the `if_not_flag` guard, the script would fire every time the player enters the
room — advancing the step counter over and over.

#### Trigger 4 — Item Pickup or Use (`on_get` / `on_use`)

For fetch quests, advance the step when the player picks up the key item. Use `on_get`
for floor items (the player `take`s them), or `on_use` for items in inventory.

```toml
# blackfen/items.toml
[[item]]
id     = "lost_ledger"
name   = "Lost Ledger"
on_get = [
  { op = "advance_quest", quest_id = "the_lost_ledger", step = 2 },
  { op = "message",       text = "The pages are water-damaged but readable.", tag = "system" },
]
```

---

### E.5 Gate NPC Dialogue on Quest State

Once the quest is running, NPCs should react differently depending on where the player
is. Use response `condition` fields to branch:

```toml
# Show only when quest is at exactly step 2
[[response]]
condition = { quest = "ashwood_contract", step = 2 }
text      = "Vorn briefed me. I'm heading to the Ashwood now."
next      = "after_vorn"

# Show only after quest is fully complete
[[response]]
condition = { quest_complete = "ashwood_contract" }
text      = "The contract is fulfilled."
next      = "complete"

# Show only if this quest is NOT active at all (offer it)
[[response]]
condition = { not_quest = "ashwood_contract" }
text      = "Tell me about the Ashwood contract."
next      = "intro"
```

The `quest` condition matches *any* step ≥ the given step unless a specific `step =` is
provided. Check §9.5 for all condition forms.

---

### E.6 Optional Paths with Flags

Flags let you track side decisions without needing extra quest steps. Define them in a
comment at the top of your quest file so you remember them:

```toml
# Optional flags:
#   has_grimwick      — hired Grimwick as guide (bonus XP, easier checks)
#   thornbury_friendly — befriended Maren Thornbury (can rest at cottage)
#   emerald_recovered — the emerald is in inventory
```

At completion time, check these flags in dialogue to give bonus rewards:

```toml
[[node]]
id     = "reward_with_bonus"
line   = "Not only that — Grimwick made it out. You kept my old friend alive."
script = [
  { op = "complete_quest", quest_id = "grandfathers_emerald" },
  { op = "if_flag", flag = "saved_grimwick",
    then = [{ op = "give_xp", amount = 200 }]
  },
]
```

---

### E.7 Automatic Step Notifications (`on_advance`)

Each step can carry an `on_advance` array that fires the moment `advance_quest` sets
that step. Use it to deliver items, send messages, or update the world — immediately,
wherever the trigger came from.

```toml
[[step]]
index      = 3
objective  = "Retrieve the hermit's map."
on_advance = [
  { op = "message",   text = "The hermit's directions flash through your memory.", tag = "quest" },
  { op = "give_item", item_id = "hermit_map" },
]
```

This is preferable to putting `give_item` on the trigger side when you want the effect to
happen consistently no matter which trigger (dialogue, room, item) advanced the step.

---

### E.8 Rewards

Rewards are given automatically when `complete_quest` runs. No additional script ops are
needed for gold, XP, and item rewards declared in the `[[reward]]` table.

```toml
[[reward]]
type   = "gold"
amount = 150

[[reward]]
type   = "xp"
amount = 400

[[reward]]
type    = "item"
item_id = "millhaven_commendation"

[[reward]]
type = "message"
text = "Aonn raises his glass to your grandfather. You do the same."
```

Conditional rewards (based on flags) must be handled manually via `if_flag` ops in the
completion script, before or after the `complete_quest` op.

---

### E.9 Checklist: Quest Is Ready When...

Before running `validate.py`, verify:

- [ ] Quest `.toml` file exists with `id`, `title`, `summary`, and at least one `[[step]]`
- [ ] Step 1 is advanced somewhere — a dialogue node, a room `on_enter`, or an item `on_get`
- [ ] Every intermediate step is advanced by exactly one trigger
- [ ] `complete_quest` fires in exactly one place (usually a dialogue hand-in node)
- [ ] Dialogue responses that offer the quest are guarded by `not_quest` (won't re-offer)
- [ ] Dialogue responses that reference quest progress use `quest`, `not_quest`, or `quest_complete`
- [ ] Any "fire once" `on_enter` scripts use an `if_not_flag` + `set_flag` guard
- [ ] Flags used in optional branches are documented in a comment at the top of the quest file
- [ ] `validate.py` reports no errors

---

### E.10 Common Mistakes

**Forgetting the `not_flag` guard on `on_enter`**
Without it, stepping into the room a second time re-fires the script. The quest step
counter will jump past its expected value and dialogue conditions break.

**Putting `complete_quest` on the wrong trigger**
If you put `complete_quest` on the kill NPC script instead of the return-to-NPC dialogue,
the quest completes with no fanfare — the player never hands anything in. Reserve
`complete_quest` for the final hand-in moment.

**Using `step =` conditions that are too strict**
`condition = { quest = "my_quest", step = 3 }` will only match when the player is at
*exactly* step 3. If the quest has advanced to step 4, the response disappears. Often
you want a flag check instead: `condition = { flag = "some_milestone" }`.

**Overlapping `advance_quest` triggers**
If two different triggers both advance the same step, the step counter skips ahead or
jumps out of order. Use flag guards to ensure only one trigger fires per step.

**Rewards that don't get given**
`[[reward]]` entries are only given when `complete_quest` runs via the quest engine. If
you call a custom script to give gold directly (e.g., `give_gold`) instead of using
`complete_quest`, the declared `[[reward]]` entries are skipped entirely. Use one or the
other, not both.

---

## Appendix F — Companion Design Walkthrough

This appendix covers the thought process behind designing a companion — from the story
role they fill to the TOML fields that make it work — with examples from existing worlds.

---

### F.1 The Core Idea: Companions Are Travelling State

A companion is a persistent piece of world state that travels with the player. Unlike
NPCs (which belong to a room), a companion belongs to the player for as long as they are
together. The engine tracks one active companion at a time on `player.companion`.

Mechanically, companions do three things:

1. **Narrative** — say flavourful things and react to the world (all tiers).
2. **Carry** — grant extra inventory capacity (`utility`, `combat`).
3. **Fight** — take a swing at enemies each combat round (`combat` only).

The tier you choose shapes everything else: what stats matter, which rooms block them,
and what the player will feel when they are downed or blocked.

---

### F.2 Design First: What Role Does This Companion Play?

Answer these questions before writing any TOML:

> *Why does this companion exist in the story? What do they add to play?*

| Design goal | Best tier | Example |
|-------------|-----------|---------|
| Story weight — a character the player bonds with | `combat` or `narrative` | Aonn Tesk (veteran, quest companion) |
| Practical help carrying loot | `utility` | Dust the mule |
| Extra muscle in a dangerous zone | `combat` | Drake Hatchling |
| Flavour NPC with restricted movement | `narrative` | A ghost, a spirit guide |

For `combat` companions, also decide: how tough should they be relative to the zone's
enemies? A companion who trivialises every fight is not interesting. A companion who gets
downed in the first encounter is frustrating. Aim for "helpful but mortal."

---

### F.3 Create the Companion File

Place the file at `data/<world>/<zone>/companions/<companion_id>.toml`. The zone folder
is just storage — the engine finds companions by scanning all zones.

**Minimal combat companion:**

```toml
# data/millhaven/companions/aonn_tesk.toml

id           = "aonn_tesk"
name         = "Aonn Tesk"
type         = "combat"
desc_short   = "A lean elf veteran with one arm, hand never far from his sword."
attack       = 8
defense      = 4
hp           = 45
max_hp       = 45
carry_bonus  = 0
style        = "swordplay"
style_prof   = 35.0
restrictions = []

join_message   = "Aonn rises, buckles his sword belt one-handed, and falls into step at your left."
wait_message   = "Aonn studies the opening. 'I won't fit through that. Go. I'll hold here.'"
rejoin_message = "Aonn straightens and rejoins you, giving you a small nod."
downed_message = "Aonn goes down hard. 'Don't stop,' he says. 'I'll be fine. Go.'"
```

**Utility companion (no combat stats needed):**

```toml
# data/millhaven/companions/dust.toml

id           = "dust"
name         = "Dust"
type         = "utility"
desc_short   = "A grey pack mule with a patient expression and an impressive carry frame."
attack       = 0
defense      = 0
hp           = 0
max_hp       = 0
carry_bonus  = 15
style        = "brawling"
style_prof   = 0.0
restrictions = ["no_large_companion"]

join_message   = "Dust ambles alongside you, loaded packs creaking."
wait_message   = "Dust plants her hooves and refuses to squeeze any further."
rejoin_message = "Dust has not moved an inch. She accepts your return with stoic dignity."
downed_message = ""
```

A utility companion with `hp = 0` and `max_hp = 0` cannot be downed. Leave
`downed_message` empty if it never applies.

---

### F.4 Fields Reference

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique identifier (filename without `.toml`) |
| `name` | Yes | Display name |
| `type` | Yes | `narrative`, `utility`, or `combat` |
| `desc_short` | Yes | One-line description shown in `look` and `stats` |
| `attack` | Yes | Base attack stat (0 for non-combat) |
| `defense` | Yes | Base defense stat (0 for non-combat) |
| `hp` | Yes | Current HP (set equal to `max_hp` at creation) |
| `max_hp` | Yes | Maximum HP (0 = cannot be downed) |
| `carry_bonus` | Yes | Extra carry slots granted (0 = none) |
| `style` | Yes | Fighting style ID used in combat |
| `style_prof` | Yes | Style proficiency 0.0–100.0 |
| `restrictions` | Yes | Room flags that block entry (empty list = unrestricted) |
| `join_message` | Yes | Text shown when companion joins |
| `wait_message` | Yes | Text shown when companion is blocked at a room |
| `rejoin_message` | Yes | Text shown when companion re-enters from waiting |
| `downed_message` | Yes | Text shown when companion is downed in combat |

---

### F.5 Giving the Companion to the Player

Companions are delivered via `give_companion` in a dialogue node script. The cleanest
pattern is a two-node sequence: one node where the NPC agrees to come, and a second
where the handshake actually happens.

```toml
# aonn_tesk.toml dialogue

[[node]]
id   = "companion_offer"
line = "Yes. I'll come. Give me a moment to find my belt."
script = [
  { op = "set_flag",      flag = "aonn_offered_to_join" },
  { op = "advance_quest", quest_id = "an_old_debt", step = 2 },
]

[[response]]
node = "companion_offer"
text = "Thank you. I'm glad you're coming."
next = "companion_joins"

[[node]]
id   = "companion_joins"
line = "Let's not make it sentimental. The mire is south."
script = [
  { op = "give_companion", companion_id = "aonn_tesk" },
]

[[response]]
node = "companion_joins"
text = "Ready when you are."
next = ""
```

Guard the offer node with a condition so it only appears once:

```toml
[[response]]
node      = "root"
condition = { not_flag = "aonn_offered_to_join" }
text      = "Would you come with me to the Blackfen?"
next      = "companion_offer"
```

You can also give a companion from a room `on_enter` script or an item `on_use` — any
script context works. Dialogue is just the most common case.

---

### F.6 Room Restrictions — When Does the Companion Wait?

A companion's `restrictions` list is checked against each room's `flags` list. If any
restriction matches a room flag, the companion waits at the entrance instead of following
the player in.

**Companion side (`companions/<id>.toml`):**
```toml
restrictions = ["no_large_companion"]
```

**Room side (`rooms.toml`):**
```toml
[[room]]
id    = "narrow_cave_passage"
name  = "Narrow Passage"
flags = ["no_large_companion"]
```

When blocked, the engine emits `wait_message` and marks the companion as `waiting`.
When the player returns to a room where the companion can follow, it rejoins automatically
and emits `rejoin_message`.

**Which rooms should be restricted?** Tight cave passages, crawl spaces, cliff
traversals, small boats, or any space that would not physically accommodate a large
companion. Narrative companions are never blocked — they exist outside physics.

---

### F.7 The Companion Lifecycle

| Status | What is happening | How it transitions |
|--------|-------------------|--------------------|
| `active` | Travelling, fighting, carrying | Normal state after `give_companion` |
| `waiting` | Blocked by a room restriction | Returns automatically when restriction clears |
| `downed` | HP reached 0 in combat | Restored when player rests at an inn |
| dismissed | Removed from player | `dismiss_companion` script op |

**Downed companions** do not fight and carry nothing until the player rests at an inn.
Plan for this: if a quest requires the companion to survive to the end, the player needs
a route back to an inn or a way to avoid getting the companion downed.

**Only one companion at a time.** Calling `give_companion` when a companion is already
active silently replaces them. If the story allows the player to swap companions, use
`dismiss_companion` first and give the player a clear dialogue moment for it.

---

### F.8 Dismissing a Companion

Use `dismiss_companion` anywhere you want to remove the companion — dialogue, room
script, or item. Always include a `message` to give the moment narrative weight.

```toml
{ op = "dismiss_companion", message = "Aonn raises his glass. 'Go on. You don't need me anymore.'" }
```

If the dismissal is part of quest completion, run it in the same dialogue node as
`complete_quest`:

```toml
[[node]]
id   = "quest_end"
line = "You've done more than I could have asked."
script = [
  { op = "complete_quest",    quest_id = "an_old_debt" },
  { op = "dismiss_companion", message  = "Aonn nods once, and steps back. His chapter here is done." },
]
```

---

### F.9 Companions as Quest Glue

Companions integrate naturally with the quest system. Common patterns:

**Companion join = quest advance:**
```toml
script = [
  { op = "give_companion", companion_id = "aonn_tesk" },
  { op = "advance_quest",  quest_id = "grandfathers_emerald", step = 2 },
]
```

**Bonus reward if the companion survived:**
Track survival with a flag set when the companion successfully completes a key moment,
then check it at the hand-in:

```toml
# Set at a pivotal room or event while the companion is active
{ op = "set_flag", flag = "aonn_survived_blackfen" }

# In the hand-in dialogue node
{ op = "if_flag", flag = "aonn_survived_blackfen",
  then = [{ op = "give_xp", amount = 200 }]
}
```

**Track companion presence via a flag:**
Since scripts cannot directly read `player.companion` state, set a flag when the
companion joins and clear it when they are dismissed:

```toml
# On join
{ op = "give_companion", companion_id = "aonn_tesk" },
{ op = "set_flag",       flag = "has_aonn" },

# On dismiss
{ op = "dismiss_companion", message = "..." },
{ op = "clear_flag",        flag = "has_aonn" },
```

This lets you gate dialogue, `on_enter` scripts, or exits on whether the companion
is present.

---

### F.10 Checklist: Companion Is Ready When...

- [ ] Companion `.toml` exists with all required fields
- [ ] `type` chosen deliberately — fight, carry, or just travel?
- [ ] Stats tuned for the zone (not trivial, not first-encounter lethal)
- [ ] `restrictions` list is correct — empty for narrative companions
- [ ] Any room that should block the companion has the matching flag in `flags`
- [ ] `give_companion` fires from a dialogue node guarded by a `not_flag` condition
- [ ] All four message fields are written as in-character reactions
- [ ] If dismissal is part of a quest, `dismiss_companion` is in the completion node
- [ ] `validate.py` reports no errors

---

### F.11 Common Mistakes

**Giving a companion with no guard condition**
Without a `not_flag` guard, re-talking to the NPC gives the companion again. If the
player already has them, the companion silently resets — losing HP progress and waiting
state. Always gate the offer node with a flag check.

**Forgetting `wait_message` and `rejoin_message` for restricted companions**
These fields fire automatically when the companion waits or rejoins. If they are empty
the player gets no feedback and will not understand why their companion disappeared.
Write them as in-character reactions to being blocked, not mechanical notices.

**Stats too high or too low**
A combat companion with `attack = 25` in a zone where enemies have `hp = 30` will kill
everything before the player acts. A companion with `hp = 10` in a mid-level zone will
be permanently downed after the first fight. Tune `hp` to roughly 1–2 enemy hits in the
target zone.

**Using `narrative` when you mean `combat`**
Narrative companions never fight and are never blocked by room flags. If you want the
companion to participate in combat even a little, use `combat`. The tier is not just
flavour — it changes engine behaviour.

**Assuming the companion will always be active**
Players rest at inns, companions get downed, room restrictions trigger waits. Design
quest steps that involve the companion around the possibility that they might not be
`active` at the critical moment. Use flags set at join time rather than trying to infer
companion state in scripts.

---

## Appendix G — Crafting & Commission Design Walkthrough

This appendix covers the thought process behind designing a crafter NPC and their
commissions — from deciding what they make to wiring quality tiers, material sourcing,
and quest integration.

---

### G.1 The Core Idea: Commissions Are Time-Gated Crafting

A commission is a three-phase player interaction:

1. **Place** — player visits the crafter, picks a commission from the menu, and hands
   over materials (and an optional gold deposit).
2. **Wait** — the crafter works for `turns_required` game moves.
3. **Collect** — player returns, types `collect`, and receives the finished item at a
   randomly rolled quality tier.

The engine handles all of this automatically once the TOML is in place.

**Player commands:**

| Command | What it does |
|---------|-------------|
| `commission` | Open the crafter's menu (or check an in-progress commission's status) |
| `give <item> to <npc>` | Hand over a required material |
| `commissions` | List all active commissions across all crafters |
| `collect` | Pick up a finished item |

---

### G.2 Design First: What Does This Crafter Make?

Before writing TOML, answer:

> *Who is this crafter? What is their craft identity? What materials make sense for their location?*

| Crafter identity | Makes | Materials found nearby |
|-----------------|-------|----------------------|
| Town blacksmith | Weapons, heavy armor | Iron ingots, coal (sold at local market) |
| Leatherworker | Light armor, packs | Tanned hides, leather strips, cloth |
| Deep-zone smith | Exotic alloys, legendary gear | Zone-specific drops and quest materials |
| Specialist (tuner, weaver) | Functional items for specific quests | Quest-gated materials |

Also decide upfront: is this crafter making **gear for combat**, **utility items** (carry
capacity), or **quest-specific outputs** (an item the player must have to progress)?

---

### G.3 Create the Crafting File

Place the file at `data/<world>/<zone>/crafting/<npc_id>.toml`. The filename **must
exactly match** the crafter NPC's `id`.

```toml
[[commission]]
id             = "hunting_knife"
npc_id         = "blacksmith_npc"
label          = "Hunting Knife"
desc           = "A short, curved blade built for fieldwork."
slot           = "weapon"
weapon_tags    = ["blade", "light", "one_handed"]
materials      = ["iron_ingot", "leather_strip"]
turns_required = 20
gold_cost      = 5
xp_reward      = 30
weight         = 1
qualities = [
  { tier = "poor",        weight = 20, attack_bonus = 3, equip_msg = "The edge is a bit rough, but serviceable." },
  { tier = "standard",    weight = 55, attack_bonus = 5, equip_msg = "A clean blade. A steady hand." },
  { tier = "exceptional", weight = 20, attack_bonus = 7, special = "sharp",
    name_prefix = "Fine ", equip_msg = "The edge is razor-keen." },
  { tier = "masterwork",  weight = 5,  attack_bonus = 9, special = "sharp",
    name_prefix = "Masterwork ", equip_msg = "Balanced perfectly. This weapon wants to be used.",
    craft_message = "Not a mark on it. That's as good as iron gets." },
]
```

---

### G.4 Commission Fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique commission identifier |
| `npc_id` | Yes | Must match the crafter NPC's `id` exactly |
| `label` | Yes | Base item name (quality `name_prefix` prepends to this) |
| `desc` | Yes | One-line description shown in the commission menu |
| `slot` | Yes | Equipment slot for the finished item; `""` for non-equippable outputs |
| `weapon_tags` / `armor_tags` | Yes | Tags on finished item (use `[]` if not applicable) |
| `materials` | Yes | List of item IDs the player must provide (repeats allowed) |
| `turns_required` | Yes | Game moves before the commission is ready |
| `gold_cost` | Yes | Upfront gold deposit (0 = free) |
| `xp_reward` | No | XP awarded when the player collects |
| `weight` | No | Item weight of the finished item |
| `on_complete` | No | Script ops run automatically when the item is collected |
| `qualities` | Yes | List of quality tier dicts (see G.5) |

---

### G.5 Quality Tiers

The engine rolls against the `weight` values to pick a tier, then applies that tier's
bonuses to the finished item.

| Field | Description |
|-------|-------------|
| `tier` | `"poor"`, `"standard"`, `"exceptional"`, `"masterwork"` |
| `weight` | Probability weight relative to all tiers. Higher = more common. |
| `attack_bonus` | Attack stat added to finished item |
| `defense_bonus` | Defense stat added to finished item |
| `max_hp_bonus` | Max HP added to finished item |
| `carry_bonus` | Carry capacity added (pack/utility items) |
| `special` | Extra tag appended to `weapon_tags`/`armor_tags` on finished item |
| `name_prefix` | Prepended to `label` — `"Fine "` produces `"Fine Hunting Knife"` |
| `equip_msg` | Message shown when the player equips the item |
| `craft_message` | Optional flavour line spoken by the crafter at collection |

**Typical weight distribution:** 20 / 55 / 20 / 5 makes masterwork rare and poor
uncommon. Adjust per commission — a specialist commission might use 10 / 45 / 35 / 10.

---

### G.6 Material Sourcing: Close the Loop

Trace where every material comes from before finalising your commission list:

- **Shop-sold** — available at a merchant in the same zone. Good for reliable basics.
- **Loot drops** — found on enemy corpses or room floor items. Good for engagement.
- **Quest-gated** — only available after completing a step. Good for earned commissions.
- **Cross-zone** — produced by a commission in another zone. Good for late-game chains.

**Two-step crafting chain example:**

```toml
# Step 1 — produces a material (slot="" = non-equippable output)
[[commission]]
id        = "dp_resonant_ingot"
materials = ["drake_scale", "dp_raw_ore"]
slot      = ""
weapon_tags = []
turns_required = 12
gold_cost = 0
on_complete = [
  { op = "give_item", item_id = "resonant_alloy_ingot" },
]
qualities = [
  { tier = "standard", weight = 100, craft_message = "The ingot cools, humming with resonance." },
]

# Step 2 — uses that material in the final weapon
[[commission]]
id        = "forge_sky_piercer_commission"
materials = ["resonant_alloy_ingot", "drakebone_fragment", "volcanic_glass_shard"]
```

The player forges the ingot first, then uses it as input for the final weapon — a
natural two-step chain that is satisfying without requiring complex scripting.

---

### G.7 Non-Equipment Commissions (Functional Outputs)

For quest items, cross-zone materials, or consumables, set `slot = ""` and use
`on_complete` to give the item directly. The `qualities` list here only affects
`craft_message` flavour — no stat bonuses apply.

```toml
[[commission]]
id             = "mf_tune_echo_compass"
slot           = ""
weapon_tags    = []
materials      = ["echo_compass", "bog_quartz", "pressure_hollowed_stone"]
turns_required = 10
gold_cost      = 0
on_complete = [
  { op = "message",   tag = "crafting", text = "Sera hums softly as the needle settles." },
  { op = "give_item", item_id = "echo_compass_tuned" },
  { op = "set_flag",  flag = "mf_echo_gate_unlocked" },
]
qualities = [
  { tier = "poor",        weight = 25, craft_message = "It will hold — probably." },
  { tier = "standard",    weight = 50, craft_message = "Standard tuning. It will do the job." },
  { tier = "exceptional", weight = 25, craft_message = "Perfect alignment." },
]
```

---

### G.8 Quest-Integrated Commissions

`on_complete` fires when the player types `collect`, regardless of quality tier. Use it
to advance or complete a quest when the commission is the quest's climax:

```toml
on_complete = [
  { op = "message",        tag = "crafting", text = "Orun hands you the Sky-Piercer." },
  { op = "advance_quest",  quest_id = "forge_sky_piercer", step = 3 },
  { op = "complete_quest", quest_id = "forge_sky_piercer" },
  { op = "prestige",       amount = 5, reason = "forged the Sky-Piercer with Orun" },
]
```

---

### G.9 Setting Up the Crafter NPC

The crafter NPC is a standard `[[npc]]` entry in `npcs.toml`. No special fields are
required — the engine detects crafter status by checking whether
`crafting/<npc_id>.toml` exists. A crafter can also run a `shop` alongside commissions.
Combining shop + commissions in one NPC is a good pattern: buy materials and place the
commission in the same visit.

---

### G.10 Checklist: Crafter Is Ready When...

- [ ] `crafting/<npc_id>.toml` filename exactly matches the NPC's `id`
- [ ] Every `[[commission]]` has all required fields
- [ ] `weapon_tags` or `armor_tags` present on every commission (even if `[]`)
- [ ] Quality `weight` values form a sensible distribution
- [ ] Every material is obtainable in-world (shop, loot, or quest)
- [ ] Non-equipment commissions use `slot = ""` and deliver via `on_complete` + `give_item`
- [ ] `craft_message` written for masterwork tier
- [ ] `validate.py` reports no errors

---

### G.11 Common Mistakes

**Filename does not match NPC id**
`crafting/blacksmith.toml` will not be found for an NPC with `id = "blacksmith_npc"`.
The filename must exactly match the NPC's `id`.

**Materials with no acquisition path**
If the player cannot find a material, they cannot complete the commission. Either sell
materials at a nearby shop, drop them as loot, or add NPC dialogue pointing the way.

**Forgetting `slot = ""` on non-equipment commissions**
If `slot` is omitted on a functional-output commission, the engine will try to create
equippable gear. Always set `slot = ""` and use `on_complete` with `give_item`.

**All qualities having the same stats**
If poor and masterwork produce the same item, the quality roll is meaningless. Make the
spread wide enough that quality matters.

**Quest ops inside a quality tier instead of at commission level**
`on_complete` at the commission level always runs. Quest-advancing ops inside a quality
tier entry only fire for that specific tier. Put all quest/flag/prestige ops in the
commission-level `on_complete`.

**Commission material is also a unique quest item**
If the required material only exists once and is needed for a quest too, the player must
choose. This is only intentional if the commission is the quest resolution — otherwise
make the material obtainable in multiples.

---

## Appendix H — Fighting Style Design Walkthrough

This appendix covers the thought process behind designing a fighting style — from its
identity and matchup profile to passive abilities and how players learn it.

---

### H.1 The Core Idea: Styles Are Identity + Matchups + Passives

A fighting style does three things:

1. **Identity** — a combat philosophy the player or NPC embodies.
2. **Matchups** — `strong_vs` and `weak_vs` tag lists determine when the style excels
   or struggles. This is the main balance lever.
3. **Passives** — abilities that unlock at proficiency thresholds and fire during combat
   rounds, adding tactical depth as the player improves.

Design the identity first, then let the mechanics follow from it.

---

### H.2 Design First: What Is This Style's Philosophy?

> *What is this fighter doing that is different? What opponents does that work against — and why?*

| Style identity | Strong against | Weak against |
|---------------|---------------|-------------|
| Street brawling — aggression, no technique | Slow, unprepared humanoids | Armored, fast |
| Classical swordplay — blade angles, leverage | Armored humanoids | Fast animals, undead |
| Evasion — don't be where the strike lands | Fast, accurate strikers | Numbers, brute force |
| Flowing Water — redirect the opponent's force | Fast, aggressive | Slow, patient, armored |
| Sky-Piercer — elevation, plunging strikes | Large, slow beasts | Small, fast, armored |

The identity should make the matchup feel logical. A player should read `strong_vs` and
understand immediately why that makes sense from the style description.

---

### H.3 Create the Style File

All styles live in `data/<world_id>/<zone>/styles/styles.toml`. The engine scans all
zones and merges found styles. Grouping a world's styles in the training or hub zone is
conventional.

```toml
[[style]]
id                = "serpent_strike"
name              = "Serpent Strike"
desc_short        = "Fast precise attacks on weak points. Excels against agile beasts."
desc_long         = "Built on patience and precision — wait for the gap, strike the exact point. Developed to counter fast-moving animals where brute force achieves nothing. Against slow or armored foes the light hits barely register."
strong_vs         = ["fast", "beast", "small"]
weak_vs           = ["armored", "large", "undead"]
strong_multiplier = 1.5
weak_multiplier   = 0.65
attack_bonus      = 1
defense_bonus     = 1
difficulty        = 2.0
preferred_weapon_tags = ["blade", "light", "one_handed"]
preferred_armor_tags  = ["light"]
weapon_bonus      = 0.30
armor_bonus       = 0.10
learned_from      = ""
learned_at        = 5
passives = [
  { ability = "bleed",        threshold = 50, trigger = "attack",
    message = "You find a gap — the wound will bleed!",
    on_activate = [{op = "apply_combat_bleed"}] },
  { ability = "vital_strike", threshold = 75, trigger = "attack",
    message = "You strike a vital point!",
    on_activate = [{op = "multiply_damage", multiplier = 2.0}] },
]
```

---

### H.4 Style Fields

| Field | Description |
|-------|-------------|
| `id` | Unique identifier used in scripts and NPC `style` fields |
| `name` | Display name shown in combat log and skill screen |
| `desc_short` | One-line description shown in menus |
| `desc_long` | Full lore description shown in the skills screen |
| `strong_vs` | NPC tags that this style excels against |
| `weak_vs` | NPC tags that this style struggles against |
| `strong_multiplier` | Damage multiplier when fighting strong targets (e.g. `1.5`) |
| `weak_multiplier` | Damage multiplier when fighting weak targets (e.g. `0.65`) |
| `attack_bonus` | Flat attack bonus applied to the fighter at all times |
| `defense_bonus` | Flat defense bonus applied at all times (can be negative) |
| `difficulty` | Proficiency gain divisor: `1.0` = normal, `2.0` = twice as slow to improve |
| `preferred_weapon_tags` | Weapon tags that gain `weapon_bonus` at full proficiency |
| `preferred_armor_tags` | Armor tags that gain `armor_bonus` at full proficiency |
| `weapon_bonus` | Max attack % bonus from matching weapon at prof 100 (0.25 = 25%) |
| `armor_bonus` | Max defense % bonus from matching armor at prof 100 |
| `learned_from` | NPC `id` who teaches this style, or `""` for self-taught/innate |
| `learned_at` | Minimum player level required to learn |
| `passives` | Array of passive ability definitions (see H.5) |

---

### H.5 Passive Abilities

Each passive unlocks at a proficiency `threshold`, fires on a `trigger`, and runs an
`on_activate` script array when it does.

```toml
passives = [
  # Unlocks at 40 — fires on defend — blocks damage and counters
  { ability = "redirect",  threshold = 40, trigger = "defend",
    message = "You redirect the attack using their force against them!",
    on_activate = [{op = "counter_damage", multiplier = 0.7}, {op = "block_damage"}] },

  # Unlocks at 75 — only fires if "redirect" also fired this round
  { ability = "absorb",    threshold = 75, trigger = "defend", requires = "redirect",
    message = "You absorb the impact, recovering HP!",
    on_activate = [{op = "heal_self", multiplier = 0.33}] },
]
```

**Passive fields:**

| Field | Description |
|-------|-------------|
| `ability` | Any unique name string; used only for chaining via `requires` — no engine registration needed |
| `threshold` | Proficiency 0-100 required to unlock this passive |
| `trigger` | `"attack"` fires on offense; `"defend"` fires on defense; `"always"` = permanent stat effect |
| `chance` | Base probability the passive fires per eligible round (0.0–1.0). Default: `0.15` |
| `chance_scaling` | Additional probability added per 100 proficiency. Default: `0.0` |
| `defense_bonus_base` | Flat defense added when `trigger = "always"`. Default: `0` |
| `defense_bonus_scale` | Defense added per 100 proficiency when `trigger = "always"`. Default: `0` |
| `requires` | Another ability that must have fired this round for this one to trigger |
| `message` | Text emitted when passive fires. Use `{npc}` as a placeholder for NPC name. |
| `on_activate` | Array of combat-only script ops (see section 13.3 for full op list) |

`always` passives do not use `chance`/`chance_scaling` — they apply unconditionally when the threshold is met.
Their bonus is `defense_bonus_base + defense_bonus_scale * (prof / 100)`.

---

### H.6 Ability Names and Probability

The `ability` field is a plain string used only to support passive chaining via `requires`.
Any name is valid — there is no registration required in Python. Choose a descriptive
name unique within the style.

**Probability fields (`attack` and `defend` passives only):**

| Field | Default | Description |
|-------|---------|-------------|
| `chance` | `0.15` | Base probability (0.0–1.0) the passive fires when eligible |
| `chance_scaling` | `0.0` | Additional probability per 100 proficiency |

The final roll is: `random() < chance + chance_scaling * (prof / 100)`

**Examples:**

```toml
# 8% base + up to 12% more at prof 100 — range 8%-20%
{ ability = "stun", threshold = 50, trigger = "defend", chance = 0.08, chance_scaling = 0.12, ... }

# Flat 25% at all proficiency levels
{ ability = "bleed", threshold = 50, trigger = "attack", chance = 0.25, ... }

# 100% — fires every eligible round (e.g. cleave)
{ ability = "cleave", threshold = 50, trigger = "attack", chance = 1.0, ... }
```

**Always-on passives (`trigger = "always"`):**

```toml
# Adds 2 + (prof/100)*4 defense when prof >= 75
{ ability = "iron_skin", threshold = 75, trigger = "always", defense_bonus_base = 2, defense_bonus_scale = 4, message = "" }
```

No Python changes are needed to add new passive abilities — just add them to the TOML.

---

### H.7 Matchup Design: Tags and Multipliers

`strong_vs` and `weak_vs` reference tags on NPCs in `npcs.toml`. The engine checks for
overlap and applies the corresponding multiplier:

```toml
# npcs.toml — wolf has fast + beast + small tags
[[npc]]
id   = "forest_wolf"
tags = ["beast", "fast", "small"]

# serpent_strike has strong_vs = ["fast", "beast", "small"] — match -> strong_multiplier
```

Keep `strong_vs` and `weak_vs` mutually exclusive to avoid ambiguity.

**Multiplier guidelines:**

| Multiplier | Typical value | Feel |
|------------|--------------|------|
| `strong_multiplier` | 1.3 to 1.6 | Clear advantage, not trivial |
| `weak_multiplier` | 0.6 to 0.75 | Meaningful disadvantage |

Avoid values above 1.8 strong or below 0.5 weak unless the style is intentionally
hyper-specialised with a narrow niche.

---

### H.8 Proficiency Gear Bonuses

`weapon_bonus` and `armor_bonus` scale linearly from 0 to max as proficiency rises:

```
weapon_bonus = 0.30  at prof 100  ->  +30% attack from a preferred weapon
weapon_bonus = 0.30  at prof 50   ->  +15% attack from a preferred weapon
```

Use higher values (0.25-0.35) for styles that are strongly equipment-dependent. Use
lower values (0.10-0.20) for styles that rely more on technique than tools.

---

### H.9 Teaching Styles to the Player

Players learn styles via `teach_style` in a dialogue node script:

```toml
[[node]]
id   = "teach_flowing_water"
line = "Watch. Not the strike — the space after it. That is where you move."
script = [
  { op = "teach_style", style_id = "flowing_water" },
  { op = "set_flag",    flag = "learned_flowing_water" },
]
```

Guard the offer with a flag so the NPC does not re-offer:

```toml
[[response]]
node      = "root"
condition = { not_flag = "learned_flowing_water" }
text      = "Would you teach me Flowing Water?"
next      = "teach_flowing_water"
```

If `learned_from` is set to an NPC id, the engine enforces that only that NPC can teach
it. If `learned_from = ""`, any `teach_style` op for that style will work.

---

### H.10 Assigning Styles to NPCs

NPCs use a style via `style` and `style_prof` in `npcs.toml`:

```toml
[[npc]]
id         = "forest_wolf"
style      = "serpent_strike"
style_prof = 30.0
```

`style_prof` controls both passive unlock and damage output. Use 60-90 for bosses and
elites; 10-35 for common enemies. If you want an NPC to have no meaningful style, use
`"brawling"` with low proficiency — it is the universal fallback and every world must
define it.

---

### H.11 Checklist: Style Is Ready When...

- [ ] `id`, `name`, `desc_short`, `desc_long` all written
- [ ] `strong_vs` and `weak_vs` tags match tags present on the target NPCs
- [ ] `strong_multiplier` and `weak_multiplier` are within sensible ranges
- [ ] Every `attack`/`defend` passive has a `chance` field (default 0.15 applies if omitted, but explicit is clearer)
- [ ] `learned_from` set to an NPC id, or `""` if self-taught
- [ ] If `learned_from` is set, that NPC's dialogue has a `teach_style` node
- [ ] All NPCs in the zone that use this style have `style` and `style_prof` set
- [ ] `validate.py` reports no errors

---

### H.12 Common Mistakes

**Missing `chance` on an attack/defend passive**
If `chance` is omitted the passive defaults to 15% — it will still fire, but without
an intentional value. Always set `chance` explicitly so the behaviour is clear.

**Strong and weak tags that overlap on the same NPC**
If an NPC has tags in both `strong_vs` and `weak_vs` for the same style, behaviour is
undefined. Keep the two lists mutually exclusive per style.

**`on_activate` ops on an "always" passive**
`trigger = "always"` passives are permanent stat effects handled directly by combat.py.
The `on_activate` array does not run for them. Use `trigger = "always"` only for flat
bonuses and express those in `attack_bonus`/`defense_bonus` at the style level.

**Forgetting `requires` on chained passives**
Without `requires`, both passives in a chain can fire independently. The `requires`
field ensures the second passive only triggers on rounds where the first also fired.

**NPC `style_prof` too high on common enemies**
A street thug with `style_prof = 90` will parry almost every round. Save high
proficiency for bosses. Common enemies in the 10-35 range keep combat readable.
