# Delve

A modular, zero-dependency Python 3.10+ MUD engine with TOML-based world data.

---

## Design principles

1. **Everything is data.** Rooms, NPCs, items, quests, dialogue, fighting styles,
   skills, and script logic live in human-readable TOML files. No code changes
   are needed to add or modify content.

2. **Engine and frontend are decoupled.** The engine never calls `print()` or
   reads `stdin`. All output flows through a typed `EventBus` as semantic `Msg`
   objects. The frontend subscribes and renders however it likes.

---

## Quick start

```bash
python main.py
```

Requires Python 3.10+. Zero external dependencies.

Type `help` in-game for a full command list.

---

## Directory structure

```
mud/
├── engine/              Core systems — see engine/README.md for full details
├── frontend/            CLI renderer and configuration
├── data/                All TOML world data — see data/README.md for full details
│   ├── players/         Cross-world player saves
│   └── <world_id>/      World folder (contains config.py + zone subfolders)
├── tools/               Authoring and maintenance utilities
├── main.py              Entry point — selects and launches a frontend
├── config.py            Root-level re-export of frontend/config.py
├── requirements.txt     (empty — zero external dependencies)
├── README.md            This file
├── engine/README.md     Engine architecture and system reference
└── data/README.md       World data organisation and authoring guide
```

---

## Systems

### Combat
Turn-based. Both player and NPC use fighting styles with matchup multipliers,
gear affinity bonuses, and passive abilities (parry, riposte, bleed, dodge,
counter, etc.) that unlock at proficiency thresholds.

### Fighting styles
Each style has matchup multipliers against enemy tags, optional gear affinity
bonuses, and passive abilities that unlock at proficiency thresholds. Proficiency
grows through combat against appropriate targets. New styles are learned from
trainer NPCs.

### Dialogue
Branching TOML trees with conditions (flags, quests, items, skills, prestige),
script execution on response, and substitution tokens. Falls back to a plain
string, then to auto-generated brush-off lines.

### Quests
Multi-step, tracked per-character. Quest flags, script ops, and dialogue
conditions interact to gate content and reward completion.

### Script engine
45 ops embedded in dialogue responses, NPC kill scripts, round scripts,
`give_accepts` handlers, item `on_get`/`on_drop` arrays, room `on_enter`
arrays, and door event arrays. Covers output, player state, inventory, quests,
styles, world state, skills, status effects, prestige, companions, bank,
conditionals, and flow control.

### Skills
Skills are world-configurable — defined per-world in `data/<world_id>/config.py`.
The Sixfold Realms ships with seven: `stealth` · `survival` · `perception` · `athletics` ·
`social` · `arcana` · `mining`. Each 0–100, growing through use. Bonus = `skill ÷ 10`
on a d20 vs DC.

### Prestige
Signed integer (−999…+999) representing reputation. Moves through story events.
Affects merchant prices, NPC hostility, and faction access.

### Death and respawn
On death: timed corpse with dropped items spawns at death room; 25% XP debt
applied; player respawns at bind point with full HP and zero gold. XP debt
blocks new accrual until repaid.

### Crafting commissions
NPCs accept materials and produce quality-tiered items after a turn-based delay.
Four tiers: poor / standard / exceptional / masterwork.

### Bank
Global account with deposit/withdraw/upgrade. Slots expand through gold or
quests. Multiple bankers in the world share one account per character.

### Companions
Three tiers: narrative (story only), utility (exploration abilities), combat
(attacks once per round alongside the player). One active at a time.

### Mining
Ore nodes are scenery items with `on_get` scripts. Requires a `pickaxe`-tagged
item and a mining skill check. Both pass and fail paths grow the skill.

### Spar system
NPCs tagged `spar` yield at 1 HP rather than dying. Useful for quest fights
where the antagonist must be defeated but not permanently removed.

### Doors and keys
Exits can be plain or locked. Items with a matching `key_tag` unlock a door.
Doors can be one-way or two-way. Multiple keys can open the same door.

### Auto-attack
Frontend loop that continues attacking a target family until all die, the player
drops below a configurable HP threshold, or a safe room is entered.

### Alias system
Per-character shorthand commands. Startup aliases (diagonals, named exits)
are defined in `frontend/config.py`. Character aliases take priority on conflict.

### Logging
Structured debug logger writing to `delve.log`. Per-category control:
`combat` · `autoattack` · `dialogue` · `script` · `world` · `player` · `command`.

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
| **Combat** | `attack <npc>` / `kill <npc>` | Attack an NPC |
| | `autoattack` / `aa` / `auto` | Toggle auto-attack loop |
| | `style` | Show active style and proficiencies |
| | `style <n>` | Switch fighting style |
| | `learn <style>` | Learn from a trainer NPC |
| **Doors** | `unlock <dir>` / `lock <dir>` | Unlock / relock a door |
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

## Tools

```bash
# Normal play
python main.py

# Data integrity — run after every data change
python tools/validate.py

# Maps
python tools/map.py                          # ASCII map, all zones
python tools/map.py --zone <zone_id>         # single zone
python tools/map.py --full                   # with NPC/item counts
python tools/map.py --html                   # HTML map → tools/admin_map.html
python tools/map.py --html --output my.html  # HTML map to custom path
python tools/map.py --world <name>           # select world by folder name

# World Creation Tool — browser-based TOML editor with map, dialogue graph
python tools/wct_server.py                   # → http://localhost:7373
python tools/wct_server.py --port 8080
python tools/wct_server.py --browser         # also open browser automatically

# AI playtester (requires ANTHROPIC_API_KEY)
python tools/ai_player.py play
python tools/ai_player.py play --goal "Complete the quest" --verbose
python tools/ai_player.py analyse

# Maintenance
python tools/clean.py                        # interactive reset menu
python tools/clean.py --all                  # full reset
python tools/clean.py --cache / --state / --players
```

---

## Configuration

All tunables live in `frontend/config.py`:

| Setting | Default | Description |
|---------|---------|-------------|
| `WRAP_WIDTH` | 100 | Terminal word-wrap column |
| `AUTO_ATTACK` | `True` | Enable auto-attack loop |
| `AUTO_ATTACK_STOP_HP_PCT` | 15 | Stop auto-attack below this HP% |
| `COLOR_OVERRIDES` | `{}` | Override ANSI colours per tag |
| `STARTUP_ALIASES` | see file | Diagonal and named-exit shorthands |
| `LOG_ENABLED` | `True` | Master logging switch |
| `LOG_FILE` | `"delve.log"` | Log output path |
| `LOG_LEVEL` | `"DEBUG"` | `DEBUG` / `INFO` / `WARN` |
| `LOG_CATEGORIES` | see file | Active log categories |
