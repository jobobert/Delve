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
# PART I

## Concept Pitch

A colony ship is on its way to a remote planet, but along the way it is attacked and boarded by a hive-mind insectoid species. The aliens are acting like ants, taking what they can from the ship, protecting the hive, ignoring what is not dangerous, generally doing what they do - but they are an extreme threat. The player has to figure out what is going on, fix ship components to unlock tools, options, areas, etc.

## Genre and Tone

The story is light-horror, set in a spacecraft designed by people with tons of space exploration experience. The aliens are a hive-mind with multiple forms and functions. Most are hostile, but some are special, all are of various sizes from dogs to elephants.

## Player Role

The player, an Engineer, awakes from sleep, has to figure out what is going on, get make-shift supplies, fight off aliens, fix the ship, and either clear the infestation or get off the ship.

## Central Conflict

The aliens are the central conflict, but the ship is also in need of repairs (which the player will do via quests and crafting). Repairing the ship gives options to the player.

## Alien types

Only for NPCs
- Drones - workers, only attack when attacked. Generally ignore the player, but may pursue and attack if the player has something they need
- Soldiers - always patrolling, always hostile, well armed
- Engineers - always tinkering, guarded by soldiers
- Leaders - always guarded by soldiers. Taking them out stops the drones under their control, and the soldiers receive combat stat reductions
- `Queen` - the lead alien. Killing this one ends the infestation

Exact alien details, stats, abilities, etc. vary withing each type (except the `queen`)


## Ending Conditions (optional)


The ending is when the player either fixes and clears the aliens (kills the `queen`) or exits via an escape pod or something

---

# PART II — WORLD CONFIGURATION

> This section maps to `data/<world_id>/config.toml`.

## World Identity

The config file is started

## Starting Character

| Field                   | Answer | Notes                                              |
|-------------------------|--------|----------------------------------------------------|
| Starting HP             |  100      |                                        |
| Default fighting style  |  brawl      | |
| Vision threshold        |  5      |     |

## Equipment Slots


weapon, shield, chest, legs, arms, head, pack, belt

## Skills


perception = "Perception"
stealth = "Stealth"
survival = "Survival"
athletics = "Althetics"
alienbiology = "Alien Biology" (knowing more about the aliens increases combat stats, and better able to counter the alien effects, puzzles, etc.)

## World-Defined Player Attributes (optional)

### Attribute

| Field   | Answer | Notes                                    |
|---------|--------|------------------------------------------|
| id      |    ship_power    |                         |
| min     |    0    |                                 |
| max     |    100    |                       |
| default |    1    |        |
| display |     bar   |   |

## 2.6 Status Effects

As the aliens are defined some will need to be defined. Poison is an obvious one, but other, more interesting ones should be as well.

---

# PART III — ZONE DESIGN
The ship is composed of 3 decks with 2 elevators between the decks, as well as 1 stairway and a few hidden/locked other paths. Each deck is composed of multiple rooms and compartments. Forward areas include the bridge, weapons, shields, and research. Amidships is berthing, cryo, living areas, kitchen, recreation, hydroponics, and the like. Rearward compartments include storage, engines, engineering. Major systems are spread across the ship (both to help narratively, but also for damage control). There is also an alien ship attached to the ship, and that is where the `queen` is found. It is not as large as the ship.

There are multiple paths through the ship, but there are plenty of deadends as the crew should know the ship by heart. Signage between areas exists, but is minimal.

Most likely each deck will be a zone, but it might also make sense to have up to 3 zones per deck.

# QUESTS

Quests are used to drive the storyline. Most quests are to get the ship working again:
- full lifesupport
- engineering
- engines
- recycling
- interior comms
- exterior sensors/navigation
- interior sensors (impacting the map and maybe abilities to see where the aliens are)
- escape pods
- access to weapons
- access to the replicator? (maybe that should always work though...)

Other quests include:
- learning about the aliens
- helping other crew members
- sending a distress call
- and others...

# COMMISSIONS

Commissions are used (by the replicator NPC) to craft the items needed to fix the ship. The parts need to be scavanged around the ship. Maybe alien tech can be used, too? other items, even crafted items can be used by the replicator as well.

# COMPANIONS

A robot companion at some point would be great

# Fighting Styles

Maybe a fighting style is learned from the aliens as the players alien biology skill increases?

# Puzzles

Turning on the systems should be impossibly hard, but each should have some sort of puzzle.

# **Notable combat encounters** (boss fights, ambushes, wave fights, surrender encounters):

The most notable combat encounter will be with the `queen`, but one or more additional encounters should be created as well for other high-power aliens

# PART IV

## World Processes (optional)

Multiple guard patrols need to be set up
Some type of system process should also be occuring. Maybe certain quests can only be completed when, maybe, the engines cycle 

## Journal Entries and Lore Discoveries

Maybe the aliens were a known entity and the player could learn more about that as the story progresses?

## Banking and Economy Notes (optional)

I don't think banking makes sense

## Light and Dark Design (optional)

Lighting should be used extensively. Less power = less light, certain areas would be priortized (bridge, medical bay) and others deprioritized (maintenance corridors)



# NPCs 

- The "Replicator" is a system that does crafting of parts
- At least one npc needs rescuing
- A few other crew members should be working on systems
- The aliens have already been discussed
- The ship's computer (gets "smarter" as repairs are made) - uses a common dialog file for multiple "terminals" across the ship

### Item and Tag Notes

Items are necessary for:
- make shift weapons
- actual weapons (locked away initially)
- materials to provide to the "Replicator" which is the system (NPC) that crafts repairs
- materials provided to various NPCs that are working on systems independently

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
| Room description length                | full     |
| NPC desc_long depth                    | one paragraph |
| Dialogue verbosity                     | verbose  |
| Random name variants per NPC           |  4+       |
| Generate admin_comments throughout?    | yes                    |

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
