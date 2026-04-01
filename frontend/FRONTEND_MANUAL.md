# Delve — Frontend Programmer's Manual

A guide to connecting a custom frontend to the Delve engine.
Read this before writing a browser client, desktop UI, or any other renderer.

---

## 1. Architecture overview

The Delve engine and its frontends communicate through two abstractions:

```
┌──────────────┐   Msg(tag, text)   ┌──────────────────┐
│   Engine     │ ─── EventBus ────▶ │   Frontend       │
│  (engine/)   │                    │  (your code)     │
│              │ ◀── input_fn() ─── │                  │
└──────────────┘                    └──────────────────┘
```

- **Output**: the engine emits `Msg(tag, text)` objects on an `EventBus`.
  Subscribe to `Event.OUTPUT` to receive them. Every piece of game text
  (room descriptions, combat, dialogue, errors) arrives this way.
- **Input**: the engine calls `input_fn(prompt, choices)` when it needs
  blocking input — primarily during dialogue trees and crafting commissions.
  For normal commands (movement, attack, look), you call
  `CommandProcessor.process(raw_command)` directly.

The engine never calls `print()` or reads `stdin`.

---

## 2. Embedding the engine

### Minimal wiring

```python
from pathlib import Path
from engine.events import EventBus, Event
from engine.world import World
from engine.player import Player
from engine.commands import CommandProcessor
import engine.world_config as wc

# 1. Load world config
world_path = Path("data/first_world")
wc.init(world_path)

# 2. Create event bus and subscribe to output
bus = EventBus()
bus.subscribe(Event.OUTPUT, lambda msg: render(msg))
bus.subscribe(Event.PLAYER_DIED, on_death)

# 3. Create world and player
world  = World(world_path)
player = Player.load("alice") or Player.create_new("alice")
player.world_id = world_path.name
player.room_id  = world.start_room
player.save()
world.attach_player(player)   # switch zone state to player's folder

# 4. Create command processor with optional input_fn
def my_input_fn(prompt: str, choices=None) -> str:
    # Called during dialogue / crafting — return the player's choice
    display(prompt)
    return get_player_input()

processor = CommandProcessor(world, player, bus, input_fn=my_input_fn)

# 5. Show starting room, then enter main loop
processor.do_look()
while not processor.quit_requested:
    raw = get_next_command()
    processor.process(raw)
```

### Thread-safe design (web / async)

When the frontend runs in a different thread (e.g., an HTTP server), keep
the engine in its own dedicated thread and communicate through queues:

```python
import queue, threading

cmd_queue = queue.Queue()   # frontend → engine
out_queue = queue.Queue()   # engine → frontend

bus.subscribe(Event.OUTPUT,
              lambda msg: out_queue.put({"tag": msg.tag, "text": msg.text}))

def input_fn(prompt, choices=None):
    if prompt:
        out_queue.put({"type": "prompt", "text": prompt})
    return cmd_queue.get()   # block until player responds

def engine_thread():
    processor.do_look()
    while not processor.quit_requested:
        try:
            cmd = cmd_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        processor.process(cmd)

threading.Thread(target=engine_thread, daemon=True).start()

# Frontend sends commands:
cmd_queue.put("look")
cmd_queue.put("go north")

# Frontend reads output:
msg = out_queue.get()    # {"tag": "room_name", "text": "..."}
```

`input_fn` feeds the same `cmd_queue` so that both normal commands and
in-dialogue responses use a single channel. The frontend does not need to
know whether the engine is in "dialogue mode" or not.

---

## 3. The Tag system

Every `Msg` has a `tag` field (a plain string constant from `engine/msg.py`).
Tags are the primary hook for styling output.

### Room / navigation

| Tag | Meaning |
|-----|---------|
| `room_name` | Room title (bold, golden) |
| `room_divider` | Separator line under the title (dimmed) |
| `room_desc` | Descriptive prose paragraph |
| `exit` | "Exits: north, east [locked: west]" |
| `move` | "You head north." |

### Entities

| Tag | Meaning |
|-----|---------|
| `npc` | NPC name in room / dialogue speaker |
| `item` | Item name on ground or in inventory |
| `item_equip` | Equipped slot annotation in character sheet |
| `item_have` | Commission: player has the required item |
| `item_bank` | Commission: item is in the bank (not on hand) |
| `item_missing` | Commission: item not found anywhere |

### Combat

| Tag | Meaning |
|-----|---------|
| `combat_hit` | Player lands a blow |
| `combat_recv` | Player receives damage |
| `combat_kill` | Enemy defeated |
| `combat_death` | Player is killed |

### Rewards

| Tag | Meaning |
|-----|---------|
| `reward_xp` | XP gain and level-up messages |
| `reward_gold` | Gold gained |

### Player / stats

| Tag | Meaning |
|-----|---------|
| `stats` | Character sheet lines (monospace-aligned columns) |
| `dialogue` | NPC speech and player response option list |

### Quests

| Tag | Meaning |
|-----|---------|
| `quest` | Quest start, update, and completion banners |
| `journal` | Player journal entries written by scripts |

### Economy / shops

| Tag | Meaning |
|-----|---------|
| `shop` | Shop listings and buy/sell confirmations |

### Doors / locks

| Tag | Meaning |
|-----|---------|
| `door` | Lock/unlock feedback and blocked-exit messages |

### Map

| Tag | Meaning |
|-----|---------|
| `map` | One line of the ASCII map grid (render in monospace) |

### Fighting styles

| Tag | Meaning |
|-----|---------|
| `style` | Style info, proficiency, passive-unlock announcements |

### System / feedback

| Tag | Meaning |
|-----|---------|
| `system` | Save confirms, help text, meta information |
| `error` | Error messages and invalid-command feedback |
| `blank` | Empty spacer line |
| `ambiguous` | "Multiple matches — did you mean...?" prompt |

### Rendering tips

- `blank` tags are visual spacers. Render them as an empty line.
- `stats` lines are pre-aligned with spaces — render in a monospace font
  or a fixed-width container.
- `map` lines must be rendered in monospace and must not be word-wrapped.
  Consecutive map lines form a single ASCII art grid; buffer them and flush
  as a single `<pre>` block when a non-map tag arrives.
- `room_divider` is a decorative separator (e.g., `────────────`);
  render verbatim without modification.
- `dialogue` lines include NPC speech and numbered response options:
  ```
  Grimwick: "The emerald belongs to my family."
    1. Ask about the emerald.
    2. Never mind.
  ```
  The player responds by typing the number or the option text.

---

## 4. The HTTP game server API

`frontend/web_server.py` is the game web frontend server (port `7374`).
Start it with `python launch_web.py` (new terminal + browser) or
`python frontend/web_server.py` (direct).

```
http://localhost:7374/     → game.html (web game client)
```

> **WCT is separate.** The World Creation Tool runs on port 7373 via
> `launch_wct.py` / `wct/wct_server.py`.

All game endpoints are root-relative. All responses are JSON unless noted.

### `GET /worlds`

List all available worlds.

**Response:**
```json
{
  "worlds": [
    {"id": "first_world",     "name": "Delve"},
    {"id": "sixfold_realms",  "name": "The Sixfold Realms"}
  ]
}
```

### `GET /players`

List all existing characters (useful for an autocomplete / character select).

**Response:**
```json
{
  "players": [
    {"name": "Alice", "world_id": "first_world", "hp": 85, "max_hp": 100}
  ]
}
```

### `GET /status`

Return the current session state.

**Response (no session):**
```json
{"session": false}
```

**Response (session active):**
```json
{
  "session": true,
  "alive":   true,
  "world":   "first_world",
  "player":  "Alice",
  "hp":      85,
  "max_hp":  100,
  "room":    "millhaven_market_square"
}
```

Poll this endpoint (e.g., every few seconds) to keep a status bar up-to-date.

### `POST /login`

Start a new game session. Fails if a session is already active.

**Request body:**
```json
{"world_id": "first_world", "player_name": "Alice"}
```

**Response (success):**
```json
{"ok": true}
```

**Response (error):**
```json
{"ok": false, "error": "A session is already active — quit it first"}
```

After a successful login, connect to the SSE stream immediately so you do
not miss the initial `look` output that the engine emits automatically.

### `GET /stream`

Server-Sent Events stream. Keeps the connection open and pushes JSON event
objects as they are produced by the engine.

**Format:** each event is one line:
```
data: <JSON>\n\n
```

**Event types:**

```json
{"type": "msg",         "tag": "room_name", "text": "Millhaven Market Square"}
{"type": "msg",         "tag": "blank",     "text": ""}
{"type": "prompt",      "text": "  > "}
{"type": "player_died"}
{"type": "error",       "text": "Engine error: ..."}
{"type": "quit"}
{"type": "no_session"}
```

- `msg` — a tagged engine message. Apply tag-based styling and append to output.
- `prompt` — the engine is in a blocking-input state (dialogue, crafting).
  Display the prompt text; the player's next POST to `/command` provides
  the response.
- `player_died` — the player's HP reached 0. Show a death notice. The engine
  automatically follows with a `look` of the respawn room.
- `error` — the engine thread threw an unhandled exception.
- `quit` — the session has ended (player typed `quit` or the engine exited).
  Close the SSE connection.
- `no_session` — SSE was opened before `/login` was called.

**Keepalive:** the server sends a comment line (`: ping`) every 15 s to keep
the connection alive through proxies and load balancers. Browser `EventSource`
objects ignore comment lines automatically.

**Reconnection:** if the SSE connection drops, the browser's `EventSource`
will automatically attempt to reconnect. Messages queued in the server while
the connection was down will be delivered when the new connection arrives.

### `POST /command`

Send a command to the active session.

**Request body:**
```json
{"cmd": "go north"}
```

**Response:**
```json
{"ok": true}
```

This endpoint returns immediately — it just enqueues the command. The output
arrives asynchronously over the SSE stream.

During dialogue or crafting, this endpoint is also used to send the player's
choice (e.g., `{"cmd": "1"}`).

### `POST /quit`

Signal the engine to quit gracefully. The session emits a `quit` SSE event
when it finishes.

**Request body:** `{}`

---

## 5. The map

### ASCII map (in-game command)

Typing `map` in the game sends a series of `Tag.MAP` messages to the output
stream. Each message is one line of the ASCII grid. The grid uses box-drawing
characters and these special markers:

```
[ @ ] — current room (where the player is)
[XYZ] — a visited room (abbreviated name)
[ ? ] — an unvisited frontier room (reachable from a visited room)
```

Render consecutive `Tag.MAP` messages in a single `<pre>` element with a
monospace font. Do not word-wrap or strip leading spaces.

The map is **zone-scoped**: it shows only the current zone. Rooms in
adjacent zones appear as `[ ? ]` regardless of their actual visit status.

### JSON map data

For a graphical renderer (canvas, SVG), you can call `build_map_data()`
directly from `engine/map_builder.py` to get a renderer-agnostic grid:

```python
from engine.map_builder import build_map_data

grid = build_map_data(
    rooms,           # dict of room_id → room_dict (from world.py)
    visited=set(),   # set of room IDs the player has been in
    current=None,    # current room ID (marks "here")
)

# grid is a dict[(x, y) → cell]:
# {
#   "id":          "ashwood_gate",
#   "name":        "Gate to Ashwood",
#   "visited":     True,
#   "here":        False,
#   "exits":       {"north": "ashwood_crossroads", "west": "millhaven_east_road"},
#   "auto_placed": True,   # True when position was inferred, not explicit
# }
```

`build_map_data` applies BFS auto-layout: rooms without explicit `coord`
fields in their TOML are placed algorithmically based on exit directions.
Coordinates are (x, y) where east = +x and north = +y.

---

## 6. Dialogue and blocking input

The engine blocks during two kinds of interactive prompts:

1. **Dialogue trees** — when the player talks to an NPC with a dialogue file.
   The engine emits a series of `Tag.DIALOGUE` messages (NPC speech + numbered
   options), then calls `input_fn` to wait for the player's choice.

2. **Crafting commissions** — when the player asks a craftsman to make an
   item. A list of commissions is shown, then `input_fn` is called.

In both cases:
- The prompt text arrives as a `{"type": "prompt", "text": "  > "}` SSE event.
- The player's response is sent via `POST /game/command`.
- The frontend does **not** need to know whether the engine is in dialogue mode;
  just display the prompt and forward whatever the player types.

Responses are typically a number (`"1"`, `"2"`) or `"0"` / `"quit"` to exit.

---

## 7. Events you may want to handle specially

| SSE event | Suggested frontend action |
|-----------|--------------------------|
| `player_died` | Show a death banner; update HP to 0 in status bar |
| `quit` | Close SSE connection; show "session ended" message |
| `error` | Show full error text; allow page refresh to start fresh |
| First `room_name` after `player_died` | Clear death state; player has respawned |

---

## 8. CLI palette reference

The `frontend/cli.py` color palette maps each tag to an RGB value. Use these
as a starting point for your own theme:

| Tag | RGB | Weight |
|-----|-----|--------|
| `room_name` | (255, 220, 100) | bold |
| `room_divider` | (100, 90, 60) | dim |
| `room_desc` | (200, 210, 220) | — |
| `exit` | (80, 200, 120) | bold |
| `move` | (140, 180, 140) | — |
| `npc` | (230, 130, 60) | bold |
| `item` | (100, 190, 240) | — |
| `item_equip` | (100, 190, 240) | bold |
| `item_have` | (80, 220, 120) | bold |
| `item_bank` | (120, 160, 140) | — |
| `item_missing` | (160, 80, 80) | — |
| `dialogue` | (220, 200, 160) | — |
| `combat_hit` | (240, 80, 80) | bold |
| `combat_recv` | (200, 60, 60) | — |
| `combat_kill` | (255, 160, 40) | bold |
| `combat_death` | (180, 0, 0) | bold |
| `reward_xp` | (160, 120, 255) | bold |
| `reward_gold` | (255, 210, 50) | — |
| `stats` | (160, 200, 255) | — |
| `system` | (150, 150, 150) | — |
| `error` | (255, 80, 80) | bold |
| `blank` | transparent | — |
| `ambiguous` | (255, 180, 40) | bold |
| `style` | (180, 120, 255) | bold |
| `shop` | (100, 220, 180) | — |
| `door` | (200, 160, 80) | bold |
| `map` | (60, 180, 80) | — |
| `quest` | (255, 215, 0) | bold |
| `journal` | (180, 220, 255) | bold |

---

## 9. Step-by-step: build your own frontend

1. **Serve your HTML** from any HTTP server (or use the bundled wct_server.py).

2. **Discover worlds** — call `GET /game/worlds` and show a selection UI.

3. **Login** — POST `{"world_id": ..., "player_name": ...}` to `/game/login`.

4. **Open SSE** — connect to `GET /game/stream` immediately after login.
   The engine emits the starting room output within milliseconds.

5. **Render output** — for each `msg` event, look up the tag in your palette
   and append a styled line. Buffer `map` lines and flush as a `<pre>`.

6. **Send commands** — when the player submits input, POST `{"cmd": ...}` to
   `/game/command`. Echo the command to the output area for feedback.

7. **Update status** — poll `GET /game/status` every few seconds to keep HP
   and room information current in your status bar.

8. **Handle quit** — on a `quit` SSE event, close the connection and prompt
   the player to log in again.

### Minimal JavaScript snippet

```javascript
// Open SSE stream
const sse = new EventSource('/game/stream');
sse.onmessage = (e) => {
  const ev = JSON.parse(e.data);
  if (ev.type === 'msg') {
    appendLine(ev.tag, ev.text);   // your styling function
  } else if (ev.type === 'quit') {
    sse.close();
  }
};

// Send a command
async function send(cmd) {
  await fetch('/game/command', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cmd }),
  });
}
```

---

## 10. Security note

`wct_server.py` is a local development tool — it binds to `localhost` only
and has no authentication. Do not expose it on a public network.

The WCT endpoints (`/api/*`) can read and write TOML files on disk. The game
endpoints (`/game/*`) run engine code in a background thread. Both are
intended for single-developer local use only.
