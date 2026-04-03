# Delve — World Creation Template

> **For humans:** Fill in the `**Answer:**` fields below. Skip any section that doesn't apply.
> The more you fill in, the closer the output matches your vision. Partial answers are fine —
> Claude will infer reasonable defaults for anything left blank.
>
> **Flow:** Human fills this out → hands the completed file to Claude →
> Claude generates all TOML files → run `python tools/validate.py --world <world_id>` to check.

---

<!--
═══════════════════════════════════════════════════════════════════════════════
  CLAUDE INSTRUCTIONS — Read this section; do not edit it
═══════════════════════════════════════════════════════════════════════════════
-->

## Claude Instructions

When you receive a completed version of this file, do the following:

**Generation order** (dependencies flow top to bottom):
1. `data/<world_id>/config.toml` — world config
2. For each zone: `zone.toml`, `rooms.toml`, `items.toml`, `npcs.toml`
3. For each NPC with a dialogue tree: `dialogues/<npc_id>.toml`
4. For each quest: `quests/<quest_id>.toml`
5. For each crafter NPC: `crafting/<npc_id>.toml`
6. For each companion: `companions/<companion_id>.toml`
7. For each new fighting style: `styles/styles.toml` in the appropriate zone
8. `world.md` — long-form lore (not read by engine; narrative reference only)

**Rules:**

1. **ID naming**: Use `<zone_abbrev>_<noun>` pattern, e.g. `mh_town_guard`, `af_ashwood_wolf`. Zone abbreviations are 2–3 chars. Keep IDs lowercase with underscores.
2. **Exactly one `start = true`** room per world. The validator enforces this.
3. **Quest rewards**: Use only `type = "gold"`, `type = "xp"`, `type = "item"`. Never `type = "prestige"` — award prestige via a `{ op = "prestige", amount = N, reason = "..." }` script op instead.
4. **Flat dialogue format**: `[[response]]` blocks are flat with a `node = "parent_id"` field. They are NOT nested inside `[[node]]` blocks. Every dialogue tree must have a node with `id = "root"`.
5. **Scripts in plain English**: The human describes scripts in plain English in this template. Convert them to the correct TOML op arrays using wct/WORLD_MANUAL.md as reference.
6. **All NPC required fields**: `id`, `name`, `desc_short`, `desc_long`, `tags`, `style`, `style_prof`, `hp`, `max_hp`, `attack`, `defense`, `xp_reward`, `gold_reward`, `hostile`. Never omit any of these.
7. **Style must exist**: The `style` field on every NPC (and `default_style` in config.toml) must match a style defined in a `styles/styles.toml` file in the world. If the human does not define custom styles, default to `brawling` and generate a minimal brawling style.
8. **Exit format**: Use inline dict syntax for exits. Plain room IDs are fine for simple exits. Use dict form `{ to = "...", locked = true, lock_tag = "...", ... }` for locked or scripted exits.
9. **`admin_comment` fields**: Add these to all rooms, NPCs, and items where the human provided design notes or narrative context. The engine ignores them; they are for world authors.
10. **show_if on exits**: Supported ops are `has_flag`, `not_flag`, `min_level`, `has_item`, `min_skill`. Unknown ops default to visible (safe forward-compat).
11. **Companions and crafting**: If the human did not fill out these sections, do not generate those files. Do not invent commissions or companions that weren't requested.
12. **Assumptions**: At the end of your response, list every assumption you made and every field you left at its default value. This helps the human review and correct the output.

**Reference**: `wct/WORLD_MANUAL.md` is authoritative for all field names, script ops, and TOML syntax patterns.

<!--
═══════════════════════════════════════════════════════════════════════════════
  END CLAUDE INSTRUCTIONS
═══════════════════════════════════════════════════════════════════════════════
-->

---

# PART I — WORLD IDENTITY

## 1.1 Concept Pitch

> One to three sentences. What is this world? What is the player doing here?
> Example: "A dying canal empire two weeks before flood season. The player is a debt-collector
> turned reluctant hero navigating a city where everyone owes someone something."

**Answer:**


## 1.2 Genre and Tone

> What genre(s) does this world blend? What is the emotional register?
> Examples: gritty low fantasy / hopepunk / cosmic horror / political intrigue /
> survival horror / mythic epic / dark comedy / historical analog

**Answer:**


## 1.3 Player Role

> Who is the player character in this world? Are they an outsider, a local,
> a mercenary, a chosen one, an amnesiac? Is there a clear opening hook?

**Answer:**


## 1.4 Central Conflict

> What is the primary source of tension? Who or what is the antagonist
> (person, faction, force of nature, existential threat)?

**Answer:**


## 1.5 Factions (optional)

> List any major factions. For each: a one-line description and starting attitude
> toward the player (friendly / neutral / hostile / unknown).
> Claude uses these to flavor NPC dialogue and prestige rewards.

**Answer:**
<!-- Example:
- The Ironclad Guild (merchants): neutral — trade with anyone
- The Ash Wardens (soldiers): hostile — distrust outsiders
- The Rootwalkers (druids): unknown — haven't seen outsiders in decades
-->


## 1.6 World Backstory / Lore

> History the player can discover. Used for lore items, journal entries,
> dialogue exposition, and zone descriptions. Write as much or as little as you want.

**Answer:**


## 1.7 Ending Conditions (optional)

> What does completing the world look like? Final boss, final quest, artifact to destroy,
> escape sequence? Describe the intended ending.

**Answer:**


---

# PART II — WORLD CONFIGURATION

> This section maps to `data/<world_id>/config.toml`.

## 2.1 World Identity

| Field           | Answer | Example                  |
|-----------------|--------|--------------------------|
| World folder ID |        | `sunken_empire`          |
| Display name    |        | `The Sunken Empire`      |
| Currency name   |        | `marks` / `gold`         |
| Currency abbrev |        | `mk` / `g`               |

**admin_comment for config.toml** (optional design notes about the world's mechanical identity):

**Answer:**


## 2.2 Starting Character

| Field                   | Answer | Notes                                              |
|-------------------------|--------|----------------------------------------------------|
| Starting HP             |        | Default: 100                                       |
| Default fighting style  |        | Must match a style defined below; default: brawling|
| Vision threshold        |        | 3 = default; lower = better innate dark vision     |

## 2.3 Equipment Slots

> List equipment slots for this world, one per line. Slot names must exactly match
> the `slot` field on items. Delete or add to fit your genre.
>
> Default: `weapon, head, chest, legs, arms, pack, ring, shield, cape`

**Answer:**


## 2.4 Skills

> Each skill needs an ID (lowercase, used in scripts) and a display name.
> You can use the defaults or define your own.
>
> Default: perception, stealth, survival, athletics, social, arcana, mining

**Answer (skill_id = "Display Name" pairs):**


## 2.5 World-Defined Player Attributes (optional)

> Custom numeric stats tracked per-player: corruption, resonance, hunger, madness, etc.
> Scripts set/read them with `set_attr`, `adjust_attr`, `if_attr`.
> Copy this block for each attribute you want.

### Attribute (copy per attribute)

| Field   | Answer | Notes                                    |
|---------|--------|------------------------------------------|
| id      |        | e.g. `corruption`                        |
| min     |        | e.g. `0`                                 |
| max     |        | e.g. `100`                               |
| default |        | Starting value for new characters        |
| display |        | `bar` (shows as [####....]) or `number`  |

**What this attribute tracks and how it changes in the world:**

**Answer:**


## 2.6 Status Effects

> Named conditions that scripts can apply/clear. The engine has five built-in defaults
> (poisoned, blinded, weakened, slowed, protected) — keep, modify, or replace them.
> Copy this block for each status effect.

### Status Effect (copy per effect)

| Field           | Answer | Notes                                |
|-----------------|--------|--------------------------------------|
| id              |        | e.g. `poisoned`                      |
| label           |        | Display name, e.g. `Poisoned`        |
| apply_msg       |        | Text when effect is applied          |
| expiry_msg      |        | Text when effect expires             |
| combat_atk      |        | Attack modifier (0 = none)           |
| combat_def      |        | Defense modifier (0 = none)          |
| damage_per_move |        | HP lost per action tick (0 = none)   |

**admin_comment** (how status effects fit into this world's design):

**Answer:**


---

# PART III — ZONE-BY-ZONE DESIGN

> Copy the entire zone block below once per zone in your world.
> A typical world has 3–8 zones. You need at least one.
> Zones are subfolders under the world folder; the folder name is the zone ID.

---

## ZONE BLOCK — [Zone name here]

### Zone Overview

| Field                     | Answer | Example               |
|---------------------------|--------|-----------------------|
| Zone folder ID            |        | `millhaven`           |
| Display name              |        | `Millhaven Town`      |
| Is this the starting zone?|        | Yes / No              |

**Narrative purpose** (1–3 sentences: role in the world, what draws the player here):

**Answer:**


**admin_comment for zone.toml** (design notes, quest hooks, faction ties):

**Answer:**


---

### Room Roster

> One Room Entry block per room. Copy and fill in as many as you need.

---

#### Room: [short label]

| Field          | Answer | Notes                                             |
|----------------|--------|---------------------------------------------------|
| Room ID        |        | e.g. `mh_town_square`                             |
| Name           |        | Short display name                                |
| Is start room? |        | Yes — mark exactly one room in the entire world   |
| Exits          |        | north → room_id, east → room_id, ...              |
| Spawns         |        | NPC IDs that appear here                          |
| Items          |        | Item IDs on the floor at zone load                |
| Room flags     |        | town, no_combat, safe_combat, hazard, healing, no_camp |
| Light level    |        | 0–10 (5 = daylight default; 2 = dim dungeon)      |

**Description:**

**Answer:**


**desc_long** (optional, shown with `examine room`):

**Answer:**


**on_enter script** (plain English — describe any scripted events on entering this room):
> Example: "First time only: show a message about the smell of smoke, then set flag `entered_harbor_first`."

**Answer:**


**on_sleep / on_wake** (optional — events when player sleeps or wakes here):

**Answer:**


**Hazard details** (only if `hazard` flag is set):

| Field              | Answer |
|--------------------|--------|
| hazard_damage      |        |
| hazard_message     |        |
| hazard_exempt_flag |        |

**Locked exits** (one row per locked door):

| Direction | Destination | lock_tag | Starts locked? | on_unlock flavor text |
|-----------|-------------|----------|----------------|-----------------------|
|           |             |          |                |                       |

**admin_comment:**

**Answer:**


---

#### Room: [short label]
*(Copy the Room Entry block above for each room in this zone)*

---

### Special Exit Behaviors

> List exits with show_if conditions, on_exit/on_enter scripts, or on_look scripts.
> Leave blank if all exits are plain room-to-room connections.

| From Room | Direction | To Room | Type              | Details                                          |
|-----------|-----------|---------|-------------------|--------------------------------------------------|
|           |           |         | show_if / script  | e.g. `has_flag "key_found"` or `min_skill perception 60` |

---

### NPC Roster

> One NPC Entry block per NPC. Copy and fill in as many as you need.

---

#### NPC: [short label]

| Field            | Answer | Notes                                                 |
|------------------|--------|-------------------------------------------------------|
| NPC ID           |        | e.g. `mh_town_guard`                                  |
| Name             |        | Display name (list for random variation: ["Name A","Name B"]) |
| Hostile          |        | true / false                                          |
| Tags             |        | humanoid, beast, armored, large, slow, fast, guard, criminal, coward, spar |
| Fighting style   |        | Style ID (must exist in this world)                   |
| Style prof       |        | 0–100 (20=weak, 50=moderate, 75=tough, 90=elite)      |
| HP               |        | (list for variation, e.g. `[20, 25, 30]`)             |
| Max HP           |        | Match HP, or use same list                            |
| Attack           |        |                                                       |
| Defense          |        |                                                       |
| XP reward        |        |                                                       |
| Gold reward      |        |                                                       |
| Has shop?        |        | Yes / No                                              |
| Rest cost        |        | Gold to rest here (innkeeper only; blank if N/A)      |
| Has dialogue?    |        | Yes / No (if Yes, fill a Dialogue Tree block below)   |

**desc_short** (one line shown in room):

**Answer:**


**desc_long** (shown with `examine <npc>`):

**Answer:**


**Shop items** (if has shop — `item_id (price)` per line):

**Answer:**


**give_accepts** (items this NPC accepts from player):
> For each item: which item_id, what message is shown, what happens (quest advance, gold, flag, etc.)

**Answer:**


**kill_script** (plain English — what happens when this NPC dies):
> Example: "Set flag `bandit_chief_dead`, advance quest `bandit_problem` to step 3, spawn item `bandit_key` in room."

**Answer:**


**round_script** (plain English — any mid-combat events):
> Example: "At round 5, if NPC HP below 20: NPC says 'Enough! I yield!', end combat, set flag `smuggler_yielded`."

**Answer:**


**admin_comment** (personality, secrets, design notes):

**Answer:**


---

#### NPC: [short label]
*(Copy the NPC Entry block above for each NPC in this zone)*

---

### Dialogue Trees

> One Dialogue Tree block per NPC that has a dialogue file.
> Simple NPCs (one-line response, no branching) can use the NPC's fallback `dialogue` field instead.

---

#### Dialogue: [NPC ID]

**Opening line(s) — root node:**
> Single line, OR a list (Claude picks randomly), OR a cycling list (use in order).
> Use `{player}` for player name, `{npc}` for NPC name.

**Answer:**


**Conversation branches:**
> For each topic the player can ask about, describe:
> 1. Response option text (what the player says)
> 2. Condition to show it (flag / quest step / item / skill / prestige — or blank for always)
> 3. NPC reply text
> 4. Script effects (set flag, advance quest, give item, teach style, etc.)
> 5. Where it goes (node name, or blank to end conversation, or sub-branches)

**Answer:**


**Fallback dialogue line** (used if no tree file is generated — for very simple NPCs):

**Answer:**


**admin_comment** (NPC personality, what they know, what they're hiding):

**Answer:**


---

#### Dialogue: [NPC ID]
*(Copy the Dialogue Tree block above for each NPC with a tree)*

---

### Item Roster

> One Item Entry block per item. Covers weapons, armor, consumables, keys, quest items, scenery.

---

#### Item: [short label]

| Field        | Answer | Notes                                                      |
|--------------|--------|------------------------------------------------------------|
| Item ID      |        | e.g. `mh_iron_sword`                                       |
| Name         |        |                                                            |
| Slot         |        | weapon, head, chest, legs, arms, ring, cape, pack, shield — or blank |
| Weight       |        | 0 = weightless                                             |
| Tags         |        | Used for door unlock (`lock_tag` match), `require_tag`, style affinity |
| Weapon tags  |        | e.g. blade, one_handed, light                             |
| Armor tags   |        | e.g. heavy, chain, plate                                  |
| Respawn      |        | true = reappears after pickup; false = one-time            |
| No drop      |        | true = cannot be dropped                                  |
| Scenery      |        | true = visible but not pickable                           |
| Gold value   |        | Sell price to merchants                                    |
| Light add    |        | Light level added when equipped (torches, lanterns)        |

**desc_short:**

**Answer:**


**desc_long** (shown with `examine <item>`):

**Answer:**


**Effects** (plain English — Claude converts to TOML effects array):
> Examples: "+8 attack, on-hit 10% stun 1 turn, equip message: 'The blade hums.'"
> "Heal 30 HP on use, message: 'Warmth floods through you.'"
> "+15 max_hp while equipped."

**Answer:**


**on_get script** (plain English — runs when player picks this up):
> Example: "Require pickaxe tag. Skill check mining DC 10 — on pass: give iron_ore + grow mining 2. On fail: give iron_ore anyway."

**Answer:**


**on_drop script** (plain English — runs when player drops this):

**Answer:**


**Custom commands** (item commands — verbs the player can type):
> For each: verb name, visible (shows as hint) or hidden, what it does.
> Example: "read (visible): show journal entry 'The Inscription'. open (hidden): if flag chest_unlocked: give silver_key + set flag chest_looted; else say 'The chest is locked.'"

**Answer:**


**admin_comment:**

**Answer:**


---

#### Item: [short label]
*(Copy the Item Entry block above for each item)*

---

### Quests in This Zone

> One Quest Design block per quest that starts or significantly progresses here.

---

#### Quest: [short title]

| Field            | Answer | Notes                                        |
|------------------|--------|----------------------------------------------|
| Quest ID         |        | e.g. `mh_the_old_debt`                       |
| Title            |        | Player-facing quest name                     |
| Giver NPC ID     |        | Who starts the quest (informational only)    |
| Summary          |        | 1–2 sentence journal description             |
| Start message    |        | Extra line in the quest-start banner (opt)   |
| Complete message |        | Shown on completion (default: "Well done.")  |

**Steps:**

| Step # | Objective | Hint | on_advance script (plain English) |
|--------|-----------|------|-----------------------------------|
| 1      |           |      |                                   |
| 2      |           |      |                                   |
| 3      |           |      |                                   |

**Rewards** (gold amount, XP amount, item IDs — as many as apply):

**Answer:**


**How is this quest triggered (what fires `advance_quest` at step 1)?**
> Example: "Dialogue response with elder_mira when player asks about the forest."

**Answer:**


**Step trigger chain** (what fires each step advance or completion):
> Example: "Step 1→2: kill_script on `bandit_chief`. Step 2→complete: give_accepts on `elder_mira` when player hands in `bandit_signet`."

**Answer:**


**Flags used** (list flags this quest sets or checks):

**Answer:**


**admin_comment:**

**Answer:**


---

#### Quest: [short title]
*(Copy the Quest Design block above for each quest)*

---

### Crafting Commissions (optional)

> Only fill this out if an NPC in this zone accepts materials and crafts items for the player.

#### Crafter NPC: [NPC ID]

**Commission** (copy per commission this NPC offers):

| Field         | Answer | Notes                                        |
|---------------|--------|----------------------------------------------|
| Commission ID |        | e.g. `mh_iron_helm`                          |
| Label         |        | Base item name (quality tier prefix prepended)|
| Description   |        |                                              |
| Result slot   |        | Equipment slot of the crafted item           |
| Weapon/armor tags |    |                                              |
| Materials     |        | Item IDs required (repeats allowed)          |
| Turns required|        | Player actions until ready                   |
| Gold cost     |        | Upfront deposit                              |
| XP reward     |        |                                              |

**Quality tiers** (describe bonus for each tier: poor / standard / exceptional / masterwork):
> Include: stat bonus (attack or defense), equip message, any special tag, name prefix if any.

**Answer:**


---

### Companions (optional)

> Only fill this out if an NPC in this zone can become a player companion.

#### Companion: [Companion ID]

| Field           | Answer | Notes                                         |
|-----------------|--------|-----------------------------------------------|
| Companion ID    |        | Unique ID (often matches NPC ID)              |
| Name            |        |                                               |
| Type            |        | `narrative` / `utility` / `combat`            |
| Attack          |        | (combat companions only)                      |
| Defense         |        |                                               |
| HP / Max HP     |        |                                               |
| Carry bonus     |        | Extra carry capacity (utility companions)     |
| Fighting style  |        |                                               |
| Style prof      |        | 0–100                                         |
| Restrictions    |        | Room flags that block this companion, e.g. `no_large_companion` |

**join_message:**

**Answer:**


**wait_message** (shown when companion is blocked by a room restriction):

**Answer:**


**rejoin_message:**

**Answer:**


**downed_message** (if companion is defeated in combat):

**Answer:**


**How is this companion acquired?**
> Which NPC / dialogue response fires the `give_companion` op?

**Answer:**


---

### Fighting Styles (optional)

> Only fill this out if this zone introduces a new fighting style (trainer NPC, discovery, etc.).

#### Style: [Style Name]

| Field               | Answer | Notes                                          |
|---------------------|--------|------------------------------------------------|
| Style ID            |        | e.g. `shadow_step`                             |
| Name                |        |                                                |
| Strong against tags |        | e.g. humanoid, armored                        |
| Weak against tags   |        | e.g. beast, large                             |
| Attack bonus        |        | Flat bonus (can be negative)                  |
| Defense bonus       |        | Flat bonus (can be negative)                  |
| Difficulty          |        | 1.0 = normal proficiency gain; 2.0 = slow     |
| Preferred weapon tags |      |                                                |
| Preferred armor tags  |      |                                                |
| Weapon bonus        |        | Max % attack bonus at full prof (e.g. `0.25`) |
| Armor bonus         |        | Max % defense bonus at full prof (e.g. `0.15`)|
| Learned from NPC    |        | NPC ID, or blank if self-taught               |
| Minimum level       |        |                                                |

**desc_short:**

**Answer:**


**desc_long:**

**Answer:**


**Passives** (describe each passive ability this style has):
> For each passive: proficiency threshold to unlock (0–100), trigger (`attack` / `defend` / `always`),
> what it does (block damage, multiply damage, counter damage, skip NPC attack, apply bleed, heal self),
> message shown when it fires, whether it requires another passive to fire first (for chains).

**Answer:**


---

### Zone Puzzle Design (optional)

> Describe any non-combat puzzles in this zone.

#### Puzzle: [short name]

**Concept** (what is the puzzle; what does the player need to figure out):

**Answer:**


**Mechanics** (which TOML features implement it):
> Examples: locked door (lock_tag + key item), skill check gate (show_if min_skill or skill_check in on_enter),
> item command sequence (item.commands verb → flag chain), dialogue discovery (NPC hint → flag → hidden exit),
> scenery item with custom verb (read / pull / press), multi-room flag chain.

**Answer:**


**Solution and flags used:**

**Answer:**


**Reward for solving:**

**Answer:**


---

### Zone Combat Design Notes (optional)

**Intended player level range when entering this zone:**

**Answer:**


**Notable combat encounters** (boss fights, ambushes, wave fights, surrender encounters):

**Answer:**


**NPC round_scripts** (mid-combat scripted events in this zone):
> Example: "At round 3, sorcerer spawns a skeleton. At HP < 10%, sorcerer teleports player to trap room."

**Answer:**


**Hazard rooms** (rooms with the `hazard` flag — damage, message, exempt flag):

**Answer:**


---

## END OF ZONE BLOCK

> Copy the entire Zone Block (from "## ZONE BLOCK" to this line) for each zone in your world.

---

# PART IV — CROSS-WORLD DESIGN

## 4.1 World Map — Inter-Zone Connections

> Describe how zones connect. List every inter-zone exit.

| Source Room | Direction | Destination Room | Notes          |
|-------------|-----------|------------------|----------------|
|             |           |                  |                |

## 4.2 Main Quest Arc

> The primary quest chain that runs through the whole world.

| Beat | Zone | Quest ID | How triggered |
|------|------|----------|---------------|
| 1    |      |          |               |
| 2    |      |          |               |
| 3    |      |          |               |

**Additional narrative details:**

**Answer:**


## 4.3 Cross-Zone Flag Registry

> Flags that are set in one zone and checked in another.
> Consistent naming prevents hard-to-find bugs.

| Flag name | Set by (zone / NPC / event) | Checked by (zone / NPC / condition) | Purpose |
|-----------|----------------------------|-------------------------------------|---------|
|           |                            |                                     |         |

## 4.4 Prestige Design (optional)

**Prestige gains** (event, amount, reason string):

**Answer:**


**Prestige losses** (event, amount, reason string):

**Answer:**


**Prestige-gated content** (dialogue options, exits, or rewards locked behind prestige min/max):

**Answer:**


## 4.5 World Processes (optional)

> Recurring timed events — weather cycles, NPC patrols, caravan routes, periodic flag changes.
> Copy this block per process.

#### Process: [short name]

| Field      | Answer | Notes                                     |
|------------|--------|-------------------------------------------|
| Process ID |        |                                           |
| Zone       |        | Which zone's processes.toml it lives in   |
| Interval   |        | Fire every N action ticks                 |
| Autostart  |        | true / false                              |
| Type       |        | `script` / `route`                        |

**What it does (plain English):**

**Answer:**


**Route waypoints** (if NPC patrol — `room_id (N ticks), room_id (N ticks), ...`):

**Answer:**


## 4.6 Journal Entries and Lore Discoveries

> Journal entries found in the world via script op `journal_entry`.

| Title | Found where (room / item / NPC) | Content summary |
|-------|--------------------------------|-----------------|
|       |                                |                 |

## 4.7 Banking and Economy Notes (optional)

> Bank NPCs, starting bank capacity, any `bank_expand` quest rewards.

**Answer:**


## 4.8 Light and Dark Design (optional)

**Vision threshold** (already set in Part II — any additional notes on how darkness is used):

**Answer:**


**Rooms with non-default light levels:**

| Room ID | Light level | Notes                       |
|---------|-------------|------------------------------|
|         |             |                              |

**Light-providing items** (item ID, light_add value, notes):

**Answer:**


## 4.9 World Notes (`world.md` content)

> Long-form lore, faction histories, NPC backstories, region descriptions, design notes.
> This section becomes `data/<world_id>/world.md`. Not read by the engine.

### World Overview

**Answer:**


### Region and Zone Notes

**Answer:**


### Quest Relationships and Story Flow

**Answer:**


### NPC Backstories

**Answer:**


### Item and Tag Notes

**Answer:**


---

# APPENDIX A — Quick Reference

## A.1 Explicit ID List (optional)

> Only fill this in if you have strong opinions about specific IDs.
> Otherwise Claude generates them from zone abbrev + noun.

| Type  | Human label | Desired ID |
|-------|-------------|------------|
| Zone  |             |            |
| Room  |             |            |
| NPC   |             |            |
| Item  |             |            |
| Quest |             |            |

## A.2 Flag Registry (optional)

> Pre-define flag names for consistency across zones.

| Flag name | Meaning | Where set | Where checked |
|-----------|---------|-----------|---------------|
|           |         |           |               |

## A.3 Prestige Affinity Tags (optional)

> Named affinity tags awarded via `add_affinity` and checked via `if_affinity`.

| Tag name | How earned | Effect on NPC dialogue / world |
|----------|------------|-------------------------------|
|          |            |                               |

## A.4 Generation Preferences

| Preference                             | Answer                      |
|----------------------------------------|-----------------------------|
| Room description length                | brief / moderate / full     |
| NPC desc_long depth                    | one paragraph / two sentences |
| Dialogue verbosity                     | terse / moderate / verbose  |
| Random name variants per NPC           | 1 (fixed) / 2–3 / 4+       |
| Generate admin_comments throughout?    | yes / no                    |

---

# APPENDIX B — Pre-Submission Checklist

> Review before handing this file to Claude. Items marked **(!)** are required for a valid world.

- [ ] **(!)** Part I: World concept written (even briefly)
- [ ] **(!)** At least one zone defined (Zone Block filled out)
- [ ] **(!)** Exactly one room with `Is start room? = Yes` across the entire world
- [ ] **(!)** Starting zone specified
- [ ] **(!)** Starting fighting style specified (or left blank to use `brawling`)
- [ ] All inter-zone connections described in Section 4.1
- [ ] Every NPC with a named quest role has a Dialogue Tree block
- [ ] Every quest has at least one step and one reward
- [ ] Every locked exit has a corresponding key item with matching `lock_tag` / `tags`
- [ ] All cross-zone flags listed in Section 4.3
- [ ] `admin_comment` fields filled for rooms/NPCs/items with specific design intent

---

*Reference: `wct/WORLD_MANUAL.md` — authoritative spec for all TOML field names, script ops, and syntax patterns.*
