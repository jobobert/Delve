# WCT — World Creation Tool Manual

The WCT (World Creation Tool) is a browser-based editor for Delve MUD worlds. It reads and writes TOML data files directly, with no build step required.

---

## Running the WCT

```bash
python tools/wct_server.py            # starts on http://localhost:7373
python tools/wct_server.py --browser  # starts and opens your default browser
python tools/wct_server.py --port 8080
```

Open `http://localhost:7373` in your browser. Use the world selector in the top bar to choose a world.

---

## Top Bar

| Button | Action |
|--------|--------|
| World selector | Switch between worlds |
| **Config** | Edit `config.toml` (world name, skills, status effects, etc.) |
| **World Notes** | Edit `world.md` — world-level lore and design notes |
| **Reload** | Reload all world data from disk |
| **+ World** | Create a new world |
| **Validate** | Run `validate.py` against the current world |
| **Errors** | Refresh the error panel (bottom of sidebar) |
| **Map** | Open the full-screen world map |
| **+ New** | Create a new room, NPC, item, or zone |
| **Save** | Save the currently selected object |

---

## World Selector & World Creation

The world selector shows all worlds found in `data/`. Selecting a world loads all its zones, rooms, NPCs, items, quests, and dialogues.

### Creating a New World

Click **+ World** in the top bar. A modal asks for:

- **World Name** — display name (e.g. "The Sixfold Realms")
- **World ID** — folder name, auto-generated from the name (alphanumeric + underscores)
- **Currency** — currency name (default: "gold")

On creation, the tool creates:
- `data/<world_id>/config.toml` — minimal world config
- `data/<world_id>/<world_id>_start/` — a starter zone with empty `rooms.toml`, `items.toml`, `npcs.toml`

Edit `config.toml` via the **Config** button to add skills, status effects, equipment slots, and player attributes.

---

## Sidebar — Zone Tree

The left sidebar shows the zone tree. Each zone is collapsible.

### Zone Header Controls

Each zone header has two buttons:

- **Notes** — opens the zone editor (admin comment, name, description)
- **Delete…** — opens the zone deletion/migration modal

### Creating a Zone

Click **+ New** in the top bar, then click the **Zone** tab. Enter a zone name/ID and click **Create**. The new zone gets empty `rooms.toml`, `items.toml`, `npcs.toml`.

### Deleting a Zone

Click **Delete…** on a zone header. A modal lists all objects in the zone (rooms, NPCs, items, quests, dialogues). For each object choose:

- **Delete** — removes the object permanently
- **Move** — moves it to another zone (use the per-row zone dropdown)

Use **Set all to: Delete / Move** at the top for bulk action. Click **Execute** to apply. The zone folder is removed after all objects are processed.

> Note: `zone.toml`, styles, crafting files, and companions are removed with the zone folder and are not listed individually.

---

## Object Editors

Click any object in the sidebar to open its editor in the main panel. Click **Save** (or the keyboard shortcut) to write changes to the TOML file.

### Room Editor

Fields: ID (read-only), Name, Description, Flags, Light level, Start room, Exits, Item spawns, NPC spawns, on_enter script, admin comment.

- **Exits** — each exit has: direction, destination room (autocomplete), optional lock/key, show_if condition, on_exit script
- **on_enter** — script ops executed when a player enters the room

### NPC Editor

Fields: ID, Name, Description, HP, Attack, Defense, Style, Tags, Hostile, Respawn time, Kill script, admin comment.

- **Hostile** — if true, NPC attacks on sight
- **Kill script** — script ops run when the NPC is killed
- **Tags** — used for `give_item` targeting and dialogue conditions

### Item Editor

Fields: ID, Name, Description, Slot, Weight, Value, Tags, Scenery, Light add, on_get script, on_use script, on_drop script, admin comment.

- **Slot** — equipment slot (head, chest, legs, feet, hands, weapon, offhand, neck, ring)
- **Scenery** — if true, item cannot be picked up (use on_get for interaction)
- **Light add** — positive/negative contribution to room light level

### Quest Editor

Fields: ID (read-only), Title, Giver NPC, Summary, Start Message, Complete Message.

- **Start Message** — optional extra line shown in the quest banner when the quest starts
- **Complete Message** — optional line shown on completion (default: "Well done, adventurer.")

**Steps** section — drag to reorder:
- Objective, Hint, Completion Flag, on_advance script

**Rewards** section — types: `gold`, `xp`, `item`

Click **Graph** to see an interactive flow diagram of the quest (requires the Quest graph panel). Click **Export DOT** to download a Graphviz `.dot` file.

### Dialogue Editor

Each dialogue file (`dialogues/<npc_id>.toml`) is a tree of nodes and responses.

- **Nodes** — NPC speech. Each node has: ID, Lines (NPC text), show_if condition, on_enter/on_exit scripts
- **Responses** — player choices. Each response has: text, next node, show_if condition, script

Drag responses to reorder them. Click **Graph** for the interactive dialogue flow diagram.

### Style Editor

Styles define a fighting style's passive abilities. Each passive has: ability ID, proficiency threshold, trigger (on_hit, on_defend, etc.), and script ops.

### Process Editor

Processes are per-player tick-driven background tasks — either a recurring script or an NPC route that moves an NPC through a sequence of rooms as the player acts.

Fields:

- **ID** — unique process identifier (e.g. `nessa_realm_route`)
- **Name** — display name shown in the editor
- **Admin Comment** — design notes
- **Interval** — number of player action ticks between each process fire (1–999)
- **Autostart** — if checked, the process starts automatically when a player enters the world; if unchecked, it must be started via a `process_start` script op
- **Script** — optional script ops run every time the process fires
- **Route NPC** — NPC ID to move along the waypoints (leave blank for script-only processes)
- **Loop mode** — `cycle` (wrap back to the first waypoint) or `reverse` (ping-pong back and forth)
- **Waypoints** — ordered list of `{room_id, ticks}` pairs. The NPC stays at each room for `ticks` process-fires before moving to the next. Use ↑/↓ to reorder, × to remove, and **+ Waypoint** to add.

**Timing example:** interval=8, ticks=3 → the NPC stays at a waypoint for 8 × 3 = 24 player actions before moving.

Processes are stored in `processes.toml` in the zone folder. They are controlled from scripts using the `process_start`, `process_stop`, and `process_pause` ops (see WORLD_MANUAL.md §21).

### Zone Editor (Notes)

Click the **Notes** button on a zone header to edit:
- Zone Name, Description, and Admin Comment (markdown, rendered in world2html)

Click **Open in New Tab** to edit the admin comment in a dedicated browser tab.

---

## World Notes (world.md)

Click **World Notes** in the top bar to open a fullscreen Markdown editor for `data/<world_id>/world.md`. This file holds world-level lore, design notes, and cross-zone references.

- Click **Save world.md** to write the file
- Click **Open in New Tab** to edit in a dedicated browser tab

`world.md` is rendered in the `world2html` review document between the summary counts and the first zone section.

---

## World Config Editor

Click **Config** in the top bar to open `config.toml` in a structured editor.

Sections:
- **World** — world_name, currency_name, default_style, new_char_hp, vision_threshold
- **Skills** — list of skill IDs
- **Equipment Slots** — list of slot names
- **Player Attributes** — world-specific attributes (id, label, default value)
- **Status Effects** — [[status_effect]] entries (id, label, duration, etc.)

---

## Map View

Click **Map** to open the full-screen world map (all zones stitched together by room coordinates).

- Click a room to select it and highlight its connections
- Connected rooms are highlighted gold
- Start room has a ★ icon; town rooms have a ⌂ icon
- Cross-zone connections are shown as dashed lines

---

## Dialogue Graph

In the dialogue editor, click **Graph** to toggle the interactive SVG flow diagram.

- Pan: click + drag
- Zoom: scroll wheel
- Click a node to highlight it
- Click **< Editor** to return to the editor

---

## Quest Graph

In the quest editor, click **Graph** to toggle the interactive flow diagram showing:
- Quest steps as nodes
- Trigger edges from dialogues, room on_enter, item on_get/on_use, and NPC kill_script

---

## References Panel (right sidebar)

Shows where the selected object is referenced in the world:
- **Used by** — rooms/NPCs/items/scripts that reference this ID
- **Flags** — flags set, cleared, or checked by this object; click a flag to see all world usages

---

## Error Panel (bottom of sidebar)

Lists ERR and WRN issues detected in the loaded world data. Click any row to navigate to the affected object. Click **Errors** in the top bar to refresh.

---

## Validate

Click **Validate** to run `validate.py` against the current world. Output is shown in a modal. Fix any ERR lines before publishing; WRN lines are advisory.

---

## In-Browser Game Terminal

The WCT includes a game terminal tab that lets you play the game inside the browser. This is useful for testing scripts, quests, and dialogue without switching to the CLI.

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| Ctrl+S | Save selected object |
| Escape | Close open modal |

---

## File Layout Reference

```
data/
  <world_id>/
    config.toml           ← world config (edited via Config button)
    world.md              ← world notes (edited via World Notes button)
    <zone_id>/
      zone.toml           ← zone name, description, admin_comment
      rooms.toml          ← [[room]] entries
      items.toml          ← [[item]] entries
      npcs.toml           ← [[npc]] entries
      dialogues/
        <npc_id>.toml     ← [[node]] and [[response]] entries
      quests/
        <quest_id>.toml   ← quest definition
      crafting/
        <npc_id>.toml     ← [[commission]] entries
      companions/
        <companion_id>.toml
      styles/
        styles.toml       ← [[style]] entries
      processes.toml      ← [[process]] entries (optional)
```
