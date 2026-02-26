# Delve Data Folder

## Zone-first architecture (world.py)

Each zone is a folder under `data/` — rooms, items, NPCs, quests, and dialogues
all colocated. The engine scans `data/` at startup; no registry to update. Only
the player's current zone and immediate neighbours live in RAM. Zones load on
approach and evict on departure. Live NPC HP and room item lists persist to
`data/zone_state/<zone_id>.json` between sessions.

All rooms should have a `coord = [x, y]` field (east = +x, north = +y).
The validator warns for any room missing coordinates — they won't appear on maps.

## Adding content

### New zone

Drop a folder in `data/my_zone/` with at least a `rooms.toml` containing
`[[room]]` entries. The engine finds it at next startup — no registry to update.
Add `coord = [x, y]` to every room so it appears on generated maps.

### New item with on_get script (ore node example)

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

### New NPC

```toml
[[npc]]
id          = "marsh_hermit"
name        = "The Marsh Hermit"
desc_short  = "An old man in waders who squints at you."
desc_long   = "He knows the mire by feel."
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
dialogue    = "Go away."      # used if no dialogues/<npc_id>.toml exists
```

Hostile NPCs with no `dialogue` field and no dialogue file get an auto-generated
brush-off line. The validator warns about both cases.

For branching dialogue → create `data/<zone>/dialogues/<npc_id>.toml`.
For quest item acceptance → add `give_accepts = [...]`.
For kill rewards → add `kill_script = [...]`.

### New quest

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


### Validator

Run after any data change:

```bash
python tools/validate.py
```

Checks: required fields, exit targets, item refs in shops/scripts/rooms, NPC
styles, dialogue integrity (orphan nodes, dangling `next=` refs, NPC existence),
TOML syntax, `give_item`/`spawn_item` script refs, missing `coord` fields,
dialogue coverage (warns for NPCs without any dialogue source).

Exit code 0 = passed (warnings OK). Exit code 1 = errors found.

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

## Directory structure

```
mud/
│
├── data/
│   ├── millhaven/          Starter hub — town + southern forest, 14 rooms
│   │   ├── town.toml / forest.toml
│   │   ├── items.toml / npcs.toml
│   │   ├── companions/     aonn_tesk.toml, dust.toml
│   │   ├── crafting/       blacksmith_npc.toml, leatherworker_npc.toml
│   │   ├── quests/         a_good_mule, an_old_debt, the_lost_ledger
│   │   └── dialogues/      elder_mira, aonn_tesk, innkeeper, merchant,
│   │                       blacksmith_npc, leatherworker_npc, millhaven_banker
│   │
│   ├── training/           Barracks + yard — 3 rooms, spar system, style trainers
│   │   ├── rooms / items / npcs
│   │   ├── styles/         styles.toml  (all 7 fighting style definitions)
│   │   └── dialogues/      drill_sergeant, barracks_quartermaster
│   │
│   ├── ashwood/            Forest quest zone — 10 rooms
│   │   ├── rooms / items / npcs
│   │   ├── quests/         ashwood_contract (6 steps)
│   │   └── dialogues/      ashwood_warden, garrison_ghost, ranger_wren,
│   │                       riddle_spirit, shrine_keeper
│   │
│   ├── blackfen/           Swamp adventure — 18 rooms, skill checks throughout
│   │   ├── zone.toml / rooms / items / npcs
│   │   ├── quests/         grandfathers_emerald, the_emerald_in_the_dark
│   │   └── dialogues/      grimwick_guide, grimwick_fenborn,
│   │                       grimwick_fenborn_isle, fenwatch_warden,
│   │                       maren_thornbury, anurus_the_returned
│   │
│   ├── millbrook/          Farming village — 16 rooms, mining + ruffian quests
│   │   ├── zone.toml / rooms / items / npcs
│   │   ├── companions/     ruffian_trio.toml
│   │   ├── quests/         thorngrub_queen, village_ruffians, lost_amulet
│   │   └── dialogues/      elder_tomlin, aldric_miller, marta_villager,
│   │                       ruffian_bram, rilla_farmer, tobias_brewer
│   │
│   ├── greenhollow/        Valley — 12 rooms, ore nodes, Flowing Water trainer
│   │   ├── zone.toml / rooms / items / npcs
│   │   ├── quests/         the_groundhog_king, finns_reckoning, osrics_pick
│   │   └── dialogues/      elder_torva, farrier_brynn, miller_owain,
│   │                       mine_foreman_osric, ruffian_finn,
│   │                       herb_wife_sable, farm_hand_npc
│   │
│   ├── players/            Per-character save files (auto-created)
│   └── zone_state/         Live zone snapshots — NPC HP, room items (auto-created)
│
├── tools/
│   ├── validate.py         Data integrity checker — TOML syntax, refs, dialogue coverage
│   ├── map.py              ASCII map generator (all zones or single zone)
│   ├── gen_map.py          Interactive HTML map generator → tools/admin_map.html
│   ├── wct_server.py       World Creation Tool — browser-based TOML editor
│   └── clean.py            Reset helper: player saves, zone state, caches

```

---