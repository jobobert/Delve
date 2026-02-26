# Delve Game Engine

## Systems

### Zone-first architecture (world.py)

Each zone is a folder under `data/` — rooms, items, NPCs, quests, and dialogues
all colocated. The engine scans `data/` at startup; no registry to update. Only
the player's current zone and immediate neighbours live in RAM. Zones load on
approach and evict on departure. Live NPC HP and room item lists persist to
`data/zone_state/<zone_id>.json` between sessions.

All rooms should have a `coord = [x, y]` field (east = +x, north = +y).
The validator warns for any room missing coordinates — they won't appear on maps.

### Dialogue system (dialogue.py)

Branching dialogue trees live in `data/<zone>/dialogues/<npc_id>.toml`. If no
tree file exists, the engine falls back to the NPC's plain `dialogue` string. If
that is also absent or empty, a randomly-chosen **brush-off line** is generated:

- Hostile NPCs get flavoured aggressive lines ("bares its teeth and lunges —
  not the talking type.")
- Friendly NPCs get neutral deflections ("gives you a queer look and shrugs you off.")

The validator flags NPCs missing any dialogue source so nothing falls through silently.

**Dialogue node format:**

```toml
[[node]]
id    = "root"
lines = ["Hello!", "Welcome, traveller!"]   # random pick each visit
# OR: line = "Hello!"                       # single fixed line

  [[node.response]]
  text = "What do you know?"
  next = "about_forest"          # "" ends the conversation
  condition = { flag = "quest_started" }
  script = [{ op = "set_flag", flag = "asked_forest" }]
```

Flat format (alternative — useful for many responses):

```toml
[[response]]
node = "root"
text = "What do you know?"
next = "about_forest"
```

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

**Substitution tokens** in dialogue text: `{player}`, `{npc}`, `{gold}`, `{hp}`,
`{level}`, `{zone}`.

### Skill system (skills.py)

Seven adventuring skills, each 0–100, growing through use:

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

37 ops used in NPC dialogue, kill scripts, `give_accepts` handlers, item `on_get`
arrays, and room `on_enter` arrays. Scripts abort cleanly when `fail` fires.

```
Output:         say, message
Player state:   set_flag, clear_flag, give_gold, take_gold, give_xp,
                heal, set_hp, damage
Inventory:      give_item, take_item, spawn_item
Quests:         advance_quest, complete_quest
Styles:         teach_style
World:          unlock_exit, lock_exit
Skills:         skill_check, if_skill, skill_grow
Status effects: apply_status, clear_status, if_status
Prestige:       prestige, add_affinity, remove_affinity, if_prestige, if_affinity
Companions:     give_companion, dismiss_companion
Bank:           bank_expand
Conditionals:   if_flag, if_not_flag, if_item, if_quest, if_quest_complete,
                if_skill, if_status, if_affinity, if_prestige
Flow control:   fail, require_tag
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

**`spawn_item`** — drop item into current room (kill scripts):
```toml
{ op = "spawn_item", item_id = "groundhog_trophy" }
```

**`damage`** — deal direct HP damage to the player:
```toml
{ op = "damage", amount = 5 }
```
### Extending the script engine

Add an `elif name == "my_op":` branch in `ScriptRunner._exec()` in
`engine/script.py`. Document it in the module docstring and the op table above.
Data files can use the new op immediately on next start — no restarts required
for TOML-only changes.

### Fighting styles (styles.py)

Seven styles with matchup multipliers, gear affinity bonuses, and passive abilities
that unlock at proficiency thresholds. Proficiency (0–100) grows by fighting
enemies whose tags match the style's preferred targets.

| Style | Strong vs | Notable passives | Trainer |
|-------|-----------|------------------|---------|
| Brawling | humanoid, slow | haymaker, vital_strike | (innate) |
| Swordplay | armored, humanoid | parry, riposte | Drill Sergeant |
| Iron Root | large, slow, armored | iron_skin, knockback | Drill Sergeant |
| Serpent Strike | fast, beast, small | bleed, vital_strike | (innate) |
| Whirlwind | group, small, humanoid | stun, haymaker | (innate) |
| Evasion | fast, humanoid, small | dodge, counter | (innate) |
| **Flowing Water** | **fast, beast, group** | **stillness, redirect, absorb** | **Brynn (Greenhollow, lv 5+)** |

Flowing Water is a counterattack archetype: `redirect` converts enemy momentum into
damage; `absorb` heals on a successful redirect; `stillness` provides a passive
defence bonus.

### Combat logging (log.py)

Every combat round can be logged to `delve.log` with full stat snapshots, passive
triggers, damage rolls, and HP changes. Configured in `frontend/config.py`:

```python
LOG_ENABLED    = True            # master switch (True by default)
LOG_FILE       = "delve.log"     # relative to mud/ root
LOG_LEVEL      = "DEBUG"         # DEBUG | INFO | WARN
LOG_CATEGORIES = ["combat", "autoattack", "dialogue"]
```

Available categories: `combat`, `autoattack`, `dialogue`, `script`, `world`,
`player`, `command`. Paste any `CMD:` section from the log into chat to debug.

### Mining system

Ore nodes are scenery items with `on_get` scripts. Extracting requires a
`pickaxe`-tagged item (enforced by `require_tag`) and a mining skill check.
Both pass and fail paths grow the mining skill. Higher-tier ore has higher DC.

### Spar system

NPCs tagged `spar` yield at 1 HP rather than dying. Their `kill_script` fires
normally (quest flags, XP) but the NPC stays in the room. Used for ruffian quests
where young antagonists should be fought but not permanently removed.

### Death and respawn

On death: a timed corpse spawns in the death room containing all non-`no_drop`
items; 25% XP debt is applied; player respawns at their bound room at full HP with
zero gold. XP debt blocks new XP accrual until repaid through kills. Bind point
auto-sets on first entry to any `town`-flagged room.

### Bank system

Global account stored in the player save file. Three banker NPCs in Millhaven
share the same account.

```
deposit <item> / deposit gold <N> / deposit all gold
withdraw <item> / withdraw gold <N> / withdraw all gold
bank                         — view balance
upgrade [confirm]            — expand slot capacity
```

| Slots | Cost |
|-------|------|
| 10 | free (base) |
| 20 | 150g (or free via the Lost Ledger quest) |
| 40 | 500g |
| 80 | 2 000g |

### Equipment slots (10)

`weapon` · `head` · `chest` · `legs` · `arms` · `armor` · `pack` · `ring` · `shield` · `cape`

Equipped items do not count toward carry weight. Equipping or unequipping in a
room with hostile NPCs may trigger a free opportunistic attack (speed-weighted
chance: slow→20%, normal→35%, fast→50%).

### Crafting commissions

NPCs with crafting definitions accept materials and produce quality-tiered items
after a turn-based delay. Four tiers: poor / standard / exceptional / masterwork.

```
commission <npc>      — list available commissions
commissions           — active jobs (✓ have / ◈ banked / ✗ missing)
give <item> <npc>     — submit materials
collect <npc>         — pick up finished work
```

Commission state machine: `waiting_materials` → `in_progress` → `ready`.

### Auto-attack

When `AUTO_ATTACK = True` in `frontend/config.py`, `attack <npc>` starts a loop
that continues until all matching enemies are dead or HP drops below
`AUTO_ATTACK_STOP_HP_PCT`. Numbered NPCs (Wolf 1, Wolf 2…) are re-resolved each
swing. Toggle mid-session with `autoattack` / `aa` / `auto`.

### Companion system

Three tiers:

- **Narrative** — story presence only
- **Utility** — special abilities usable in exploration
- **Combat** — attacks once per round alongside the player; NPCs have a 30% chance
  per round to strike the companion instead

Only one companion active at a time. Acquired through quests and dialogue.

### Doors and keys

```toml
exits = { east = { to = "armory", locked = true, lock_tag = "garrison_lock" } }
```

Items with `key_tag = "garrison_lock"` unlock that exit.
Commands: `unlock <dir>` / `lock <dir>`.

### Alias system

```
alias <shorthand> <command>   — define an alias
unalias <shorthand>           — remove one
aliases                       — list all
```

Startup aliases (diagonals, named exits) live in `frontend/config.py →
STARTUP_ALIASES`. Character aliases saved per-player take priority on conflict.

---

## High level program flow
(this needs to be created)