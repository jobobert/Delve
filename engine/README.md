# Delve Game Engine

## Program flow

### Startup

```
main.py
  └─ CLIFrontend()
       ├─ EventBus()                         subscribe OUTPUT → _on_output
       │                                     subscribe PLAYER_DIED → _on_player_died
       ├─ World(world_path)                  scan world folder — build zone index, load all
       │                                     item and NPC templates into flat dicts
       ├─ _login()                           prompt for name → Player.load() or Player()
       └─ CommandProcessor(world, player, bus)
            └─ QuestTracker(player)
               GameContext(player, world, bus, quests)
               build verb → handler dispatch dict
```

### Command loop (steady state)

```
CLIFrontend.run()
  │
  ├─ [auto-attack check] ──────────────────────────────────────────────────┐
  │    if AUTO_ATTACK and input starts with "attack"/"kill":               │
  │      _resolve_attack_target()  → canonical NPC display name           │
  │      processor.process("attack <full_name>")  (first swing)           │
  │      _run_auto_attack()                                                │
  │        loop: re-resolve live targets → processor.process("attack …")  │
  │              stop if: no targets, HP %, safe room, player dead         │
  │                                                                        │
  └─ processor.process(raw)  ◄──────────────────────────────────────────── ┘
       │
       ├─ strip / split → verb + args
       ├─ expand player alias if present
       ├─ look up verb in dispatch dict → handler(verb, args)
       │
       ├─ _apply_room_effects()        room on_enter scripts (once per visit)
       ├─ _tick_status_effects()       decrement status durations
       └─ _apply_status_damage()       bleed / burn tick damage
```

### Output path

```
Any engine subsystem (commands, combat, dialogue, script, …)
  └─ self._out(Tag.SOMETHING, "text")
  └─ bus.emit(Event.OUTPUT, Msg(tag, text))
  └─ CLIFrontend._on_output(msg)
       └─ _render(msg)  → apply ANSI colour from PALETTE
       └─ print(rendered)
```

### Movement

```
_cmd_direction(verb, args)
  └─ _move(direction)
       ├─ look up exit in current room["exits"]
       ├─ if door dict: check locked → emit Tag.DOOR if blocked
       ├─ world.prepare_room(new_room_id, player)
       │    ├─ load zone if not in memory
       │    ├─ deepcopy NPC templates into room["_npcs"] on first visit
       │    ├─ apply zone_state sidecar (persisted HP / items)
       │    └─ evict zones no longer adjacent
       ├─ player.room_id = new_room_id
       ├─ auto-set bind point if room has "town" flag
       └─ do_look()  → describe new room
```

### Combat

```
_cmd_attack(verb, args)
  └─ _resolve_npc(target)           find live NPC in room["_npcs"]
  └─ CombatSession(player, npc, bus, room, ctx)
       └─ player_attack()           one full round:
            ├─ player hits NPC      style matchup, gear affinity, passives
            │    passives: parry, dodge, riposte, counter, bleed, …
            ├─ NPC retaliates       same passive system
            │    passives: stun, knockback, haymaker, vital_strike, …
            ├─ companion attacks    (if combat-tier companion active)
            ├─ if NPC hp ≤ 0:
            │    run npc["kill_script"] via ScriptRunner
            │    give XP / gold, level-up check
            │    emit COMBAT_KILL
            └─ if player hp ≤ 0:
                 bus.emit(Event.PLAYER_DIED)
                 _on_player_died() → respawn, corpse spawn
```

### Dialogue

```
_cmd_talk(verb, args)
  └─ _resolve_npc(target)
  └─ dialogue.run_inline(npc, player, quests, ctx, bus, input_fn)
       ├─ load data/<world_id>/<zone>/dialogues/<npc_id>.toml  (or npc["dialogue"] string,
       │                                              or auto brush-off line)
       ├─ evaluate node conditions (flags, quests, items, skills, prestige, …)
       ├─ emit Tag.DIALOGUE lines
       ├─ present numbered response options
       └─ on selection: run response script via ScriptRunner → advance to next node
```

### Script execution

```
ScriptRunner(ctx).run(ops)
  └─ _exec(op) for each op in list
       ├─ output ops:    say, message
       ├─ state ops:     set_flag, give_gold, give_xp, heal, damage, …
       ├─ inventory ops: give_item, take_item, spawn_item
       ├─ world ops:     unlock_exit, lock_exit
       ├─ quest ops:     advance_quest, complete_quest
       ├─ skill ops:     skill_check (d20 + bonus vs DC), skill_grow, if_skill
       ├─ prestige ops:  prestige, add_affinity, if_prestige, if_affinity
       ├─ companion ops: give_companion, dismiss_companion
       ├─ conditional:   if_flag, if_item, if_quest, if_status, …
       └─ flow control:  fail (abort script), require_tag (abort if tag absent)
```

---

## Module reference

| File | Role |
|------|------|
| `commands.py` | Command parser and game-logic dispatcher (~2 500 lines). `CommandProcessor.process(raw)` is the main entry point. Holds `GameContext` and wires all subsystems. |
| `combat.py` | `CombatSession(player, npc, bus, room, ctx)`. Call `player_attack()` once per turn. Runs kill scripts on NPC death. |
| `companion.py` | Companion state — narrative / utility / combat tiers. Combat companions attack in `CombatSession`. |
| `dialogue.py` | `run_inline(npc, player, quests, ctx, bus, input_fn)`. Loads TOML tree, evaluates conditions, presents responses, runs scripts. Falls back through plain string → auto brush-off. |
| `events.py` | Lightweight publish/subscribe `EventBus`. `emit(event, *args)` fires all registered handlers. |
| `log.py` | `log.configure(...)` at startup. `log.debug/info/warn(category, msg, **kv)`. Writes to `delve.log`. |
| `msg.py` | `Msg(tag, text)` dataclass. `Tag` class of string constants. |
| `player.py` | `Player` dataclass. `player.save()` / `Player.load(name)`. Equipped items, bank, skills, prestige, aliases, quest flags. |
| `prestige.py` | Score −999…+999, 10 tiers. `apply_delta`, `tier_name`, `shop_modifier`, `hostile_on_sight`. |
| `quests.py` | `QuestTracker` — load quest TOML files, track step progress, emit journal updates. |
| `room_flags.py` | Room flag constants: `safe_combat`, `no_combat`, `healing`, `town`, `reduced_stats`. |
| `script.py` | `ScriptRunner(ctx).run(ops)`. 45 ops. `fail` aborts cleanly. `require_tag` gates on item tag. |
| `skills.py` | Seven adventuring skills (0–100). `grow(skill, amount)`, `check(skill, dc)` → d20 + bonus vs DC. |
| `styles.py` | 7 fighting styles with matchup tables, gear affinity, and passive abilities unlocking at proficiency thresholds. |
| `toml_io.py` | **Custom TOML parser** — superset of spec. Supports multi-line inline tables and triple-quoted strings. Standard `tomllib` will fail on these files. **Always use `from engine.toml_io import load`.** |
| `world.py` | Zone-streaming manager. `prepare_room(room_id, player)` loads a zone if needed and returns the live room dict. Evicts zones no longer adjacent to the player. |
| `map_builder.py` | Topology-aware map data builder. `apply_auto_layout(rooms)` places all rooms on a grid using exit-direction BFS. `build_map_data(rooms, visited, current)` returns a renderer-agnostic `{(x,y): cell_dict}` grid consumed by the in-game `map` command, `tools/map.py`, and any future HTML/web frontend. |

---

## EventBus events

| Event | Direction | Payload | Description |
|-------|-----------|---------|-------------|
| `Event.OUTPUT` | Engine → Frontend | `Msg(tag, text)` | Any displayable output |
| `Event.PLAYER_DIED` | Engine → Frontend | _(none)_ | Player HP reached 0 |
| `Event.GAME_OVER` | Engine → Frontend | reason `str` | Session should end |
| `Event.COMMAND_IN` | Frontend → Engine | raw input `str` | Available for async/web use; CLI calls `process()` directly |

---

## Msg tags

| Group | Tags |
|-------|------|
| Room / navigation | `ROOM_NAME`, `ROOM_DESC`, `ROOM_DIVIDER`, `EXIT`, `MOVE` |
| Entities | `NPC`, `ITEM`, `ITEM_EQUIP`, `ITEM_HAVE`, `ITEM_BANK`, `ITEM_MISSING` |
| Combat | `COMBAT_HIT`, `COMBAT_RECV`, `COMBAT_KILL`, `COMBAT_DEATH` |
| Rewards | `REWARD_XP`, `REWARD_GOLD` |
| Player | `STATS`, `DIALOGUE` |
| Quests | `QUEST` |
| Economy | `SHOP` |
| Doors | `DOOR` |
| Map | `MAP` |
| Styles | `STYLE` |
| System | `SYSTEM`, `ERROR`, `BLANK`, `AMBIGUOUS` |

Override colours for any tag in `frontend/config.py → COLOR_OVERRIDES`.

---

## Systems

### Zone-first architecture (world.py)

Each zone is a folder under `data/`. The engine scans `data/` at startup; no
registry to update. Only the player's current zone and immediate neighbours live
in RAM. Zones load on approach and evict on departure. Live NPC HP and room item
lists persist to `data/players/<name>/zone_state/<zone_id>.json` between sessions
(per-player — isolated from other characters).

All rooms are auto-placed on maps by both the in-game `map` command and
`tools/map.py` using exit topology (BFS from neighbours). An optional
`coord = [x, y]` field (east = +x, north = +y) can pin a room's position
on the admin map permanently; it is not required or validated.

**Zone eviction policy:** Keep current zone + all directly adjacent zones in RAM.
Evict everything else. "Adjacent" = any zone reachable by a single exit from any
room in the current zone.

**NPC spawning:** NPC instances are deepcopied from global templates on first
room visit (not at world load). Startup is instant regardless of world size.

**Item filtering:** Items with `respawn = false` that the player already picked
up are suppressed via `player.looted_items` (a set of `"room_id:item_id"` keys).

### Dialogue system (dialogue.py)

Branching trees in `data/<world_id>/<zone>/dialogues/<npc_id>.toml`. Resolution order:

1. `data/<world_id>/<zone>/dialogues/<npc_id>.toml`
2. `npc["dialogue"]` plain string
3. Auto-generated brush-off line (hostile or friendly pool)

The validator flags NPCs missing any dialogue source.

**Conditions** (valid on both nodes and responses):

| Key | Meaning |
|-----|---------|
| `flag = "name"` | player.flags contains "name" |
| `not_flag = "name"` | player.flags does NOT contain "name" |
| `item = "id"` | player carries this item |
| `quest = "id", step = N` | quest is at step N |
| `quest_complete = "id"` | quest is complete |
| `level_gte = N` | player level ≥ N |
| `skill = "id", min = N` | skill value ≥ N |
| `gold = N` | player gold ≥ N |
| `prestige_min = N` | prestige ≥ N |
| `prestige_max = N` | prestige ≤ N |
| `affinity = "tag"` | player has this prestige affinity |
| `no_companion = true` | player has no active companion |

**Substitution tokens** in text: `{player}`, `{npc}`, `{gold}`, `{hp}`,
`{level}`, `{zone}`.

### Skill system (skills.py)

| Skill | Used for |
|-------|----------|
| `stealth` | Hiding, sneaking, avoiding detection |
| `survival` | Wilderness navigation, hazard avoidance |
| `perception` | Noticing hidden things, reading situations |
| `athletics` | Climbing, swimming, feats of strength |
| `social` | Persuasion, deception, reading people |
| `arcana` | Magic knowledge, identifying enchantments |
| `mining` | Extracting ore from rock seams |

Bonus = `skill ÷ 10` added to a d20 roll vs DC. Tier names:
Untrained → Novice → Practiced → Skilled → Expert → Master → Legendary.

```toml
{ op = "skill_check", skill = "athletics", dc = 10,
  on_pass = [...], on_fail = [...] }
{ op = "if_skill",   skill = "mining", min = 5, then = [...], else = [...] }
{ op = "skill_grow", skill = "mining", amount = 3 }
```

### Prestige system (prestige.py)

A signed integer (−999 to +999) that moves only through story events.

| Tier | Range |
|------|-------|
| Legend | 200+ |
| Champion | 100+ |
| Hero | 50+ |
| Honoured | 20+ |
| Respected | 5+ |
| Neutral | −4 to +4 |
| Suspicious | −5 to −19 |
| Wanted | −20 to −49 |
| Villain | −50 to −99 |
| Outlaw | −100 and below |

Effects: positive prestige → merchant discounts (≥+25: 10% off), NPC warmth;
negative → guards hostile at −50, surcharges below −25, criminal factions open up.

```toml
{ op = "prestige",    amount = 3, reason = "cleared the infestation" }
{ op = "if_prestige", min = 20, then = [...], else = [...] }
{ op = "add_affinity", tag = "verdant_hero" }
{ op = "if_affinity",  tag = "verdant_hero", then = [...] }
```

### Script engine (script.py)

45 ops used in NPC dialogue, kill scripts, round scripts, `give_accepts` handlers,
item `on_get` and `on_drop` arrays, room `on_enter` arrays, and door event arrays. Scripts abort
cleanly when `fail` fires.

```
Output:         say, message
Player state:   set_flag, clear_flag, give_gold, take_gold, give_xp,
                heal, set_hp, damage
Inventory:      give_item, take_item, spawn_item
Quests:         advance_quest, complete_quest
Styles:         teach_style
World:          unlock_exit, lock_exit,
                teleport_player, move_npc, move_item
Skills:         skill_check, if_skill, skill_grow
Status effects: apply_status, clear_status, if_status
Prestige:       prestige, add_affinity, remove_affinity, if_prestige, if_affinity
Companions:     give_companion, dismiss_companion
Bank:           bank_expand
Conditionals:   if_flag, if_not_flag, if_item, if_quest, if_quest_complete,
                if_skill, if_status, if_affinity, if_prestige,
                if_combat_round, if_npc_hp
Flow control:   fail, require_tag, end_combat
```

**`fail`** — aborts the entire script immediately:
```toml
{ op = "if_not_flag", flag = "mine_cleared", then = [
    { op = "message", tag = "error", text = "Clear the mine first." },
    { op = "fail" },
]}
```

**`require_tag`** — gate on an item tag; aborts if player lacks it:
```toml
{ op = "require_tag", tag = "pickaxe",
  fail_message = "You need a pickaxe to mine this." }
```

**`spawn_item`** — drop item into current room (useful in kill scripts):
```toml
{ op = "spawn_item", item_id = "dragon_fang" }
```

**`damage`** — deal direct HP damage to the player:
```toml
{ op = "damage", amount = 5 }
```

**`teleport_player`** — instantly move the player to any room (cross-zone):
```toml
{ op = "teleport_player", room_id = "blackfen_shrine",
  message = "The portal tears open and swallows you whole." }
```
The new room is prepared, the player sees its description, and `on_enter` scripts run.

**`move_npc`** — relocate a live NPC instance to a different room:
```toml
{ op = "move_npc", npc_id = "bandit_captain", to_room = "ambush_clearing" }
```
Only moves the NPC if it is currently in a loaded zone. Silently does nothing if not found.

**`move_item`** — move a ground item from one room to another:
```toml
{ op = "move_item", item_id = "ancient_key", to_room = "vault_antechamber" }
{ op = "move_item", item_id = "crate", from_room = "warehouse", to_room = "dock" }
```
`from_room` defaults to the player's current room if omitted.

**`if_combat_round`** — branch on the current combat round number:
```toml
{ op = "if_combat_round", min = 5, then = [...], else = [...] }
```
Available inside `round_script` (the NPC field that runs each combat round).

**`if_npc_hp`** — branch on the current NPC's HP during combat:
```toml
{ op = "if_npc_hp", max = 10, then = [...], else = [...] }
```
Available inside `round_script`. Passes when NPC HP ≤ `max`.

**`end_combat`** — stop the current fight (NPC yields, combat ends without a kill):
```toml
{ op = "say",       text = "I yield! Here, take this." },
{ op = "give_item", item_id = "vault_key" },
{ op = "end_combat" }
```
The NPC survives at 1 HP with `hostile = false`. No kill rewards are given.

To add a new op: add an `elif name == "my_op":` branch in `ScriptRunner._exec()`
in `engine/script.py`, document it in the module docstring and the op table above.

### Fighting styles (styles.py)

Proficiency (0–100) grows by fighting enemies whose tags match the style's
preferred targets. Gear affinity bonuses apply when wearing matching slot types.

| Style | Strong vs | Notable passives |
|-------|-----------|-----------------|
| Brawling | humanoid, slow | haymaker, vital_strike |
| Swordplay | armored, humanoid | parry, riposte |
| Iron Root | large, slow, armored | iron_skin, knockback |
| Serpent Strike | fast, beast, small | bleed, vital_strike |
| Whirlwind | group, small, humanoid | stun, haymaker |
| Evasion | fast, humanoid, small | dodge, counter |
| Flowing Water | fast, beast, group | stillness, redirect, absorb |

Flowing Water is a counterattack archetype: `redirect` converts enemy momentum
into damage; `absorb` heals on a successful redirect; `stillness` provides a
passive defence bonus.

### Combat system (combat.py)

Each fight is a `CombatSession`. One call to `player_attack()` = one full round:

1. Player attacks NPC — style matchup multiplier, gear affinity, passive checks
2. NPC retaliates — same passive system (NPCs can parry, dodge, riposte)
3. Combat companion attacks (if active)
4. Bleed ticks (if active on either side)
5. Check win/loss conditions

Room flags affect combat:
- `safe_combat` — player takes zero damage (training rooms)
- `no_combat` — attack command blocked before this code is reached
- `reduced_stats` — both sides use half attack/defense

### Mining system

Ore nodes are scenery items with `on_get` scripts. Extracting requires a
`pickaxe`-tagged item (enforced by `require_tag`) and a mining skill check.
Both pass and fail paths grow the mining skill. Higher-tier ore has a higher DC.

### Spar system

NPCs tagged `spar` yield at 1 HP rather than dying. Their `kill_script` fires
normally (quest flags, XP) but the NPC stays in the room.

### Death and respawn

On death: a timed corpse spawns in the death room containing all non-`no_drop`
items; 25% XP debt is applied; player respawns at their bind room at full HP
with zero gold. XP debt blocks new XP accrual until repaid through kills. Bind
point auto-sets on first entry to any `town`-flagged room.

### Bank system

Global account stored in the player save file. Multiple banker NPCs share the
same account per character.

| Slots | Cost |
|-------|------|
| 10 | free (base) |
| 20 | 150g |
| 40 | 500g |
| 80 | 2 000g |

Slot costs are defined in world data and can be changed or made questable without
engine changes.

### Equipment slots (10)

`weapon` · `head` · `chest` · `legs` · `arms` · `armor` · `pack` · `ring` · `shield` · `cape`

Equipped items do not count toward carry weight. Equipping or unequipping in a
room with hostile NPCs may trigger a free opportunistic attack (speed-weighted
chance: slow→20%, normal→35%, fast→50%).

### Crafting commissions

NPCs with crafting definitions accept materials and produce quality-tiered items
after a turn-based delay. Four tiers: poor / standard / exceptional / masterwork.

Commission state machine: `waiting_materials` → `in_progress` → `ready`.

### Companion system

Three tiers:

- **Narrative** — story presence only
- **Utility** — special abilities usable in exploration
- **Combat** — attacks once per round alongside the player; NPCs have a 30%
  chance per round to strike the companion instead

Only one companion active at a time. Acquired through quests and dialogue.

### Doors and keys

```toml
exits = { east = { to = "armory", locked = true, lock_tag = "garrison_lock" } }
```

Items with `key_tag = "garrison_lock"` unlock that exit.
Commands: `unlock <dir>` / `lock <dir>`.

### Alias system

Startup aliases live in `frontend/config.py → STARTUP_ALIASES`.
Character aliases saved per-player take priority on conflict.

### Logging (log.py)

`log.configure(...)` is called at startup from `frontend/cli.py` using values
from `frontend/config.py`. Structured `key=value` lines are written to
`delve.log`.

```python
import engine.log as log
log.debug("category", "description", key=value, ...)
log.info(...)
log.warn(...)
log.section("CMD: look")   # section dividers visible in the log file
```

Available categories: `combat`, `autoattack`, `dialogue`, `script`, `world`,
`player`, `command`. To debug a report: find the `CMD: <command>` section in
`delve.log` and paste it into chat.

---

## Adding a new command

1. Add `"verb": self._cmd_foo` in `CommandProcessor.__init__` dispatch dict
2. Write `_cmd_foo(self, verb, args)`
3. Add a line to `_cmd_help`'s lines list
4. Register aliases if needed

## Adding a new frontend

1. Instantiate `EventBus`, `World`, `Player`, `CommandProcessor`
2. Subscribe to `Event.OUTPUT` and `Event.PLAYER_DIED`
3. Feed input to `processor.process(raw)` — no other engine changes needed
