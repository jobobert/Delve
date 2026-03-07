"""
engine/map_builder.py — Topology-aware map data builder for Delve.

Provides auto-layout (placing rooms without explicit ``coord`` fields using BFS
from exit directions) and a generic ``build_map_data()`` function that returns a
display grid usable by *any* renderer — CLI ASCII, HTML frontend, or future
graphical clients.

Public API
----------
apply_auto_layout(rooms)
    Mutate every room dict with a ``_display_coord`` field.  Rooms with an
    explicit ``coord`` field use that value; all others are BFS-placed via exit
    direction deltas.  Disconnected rooms land near the origin.

build_map_data(rooms, visited=None, current=None) -> dict[(x, y), cell]
    Apply auto-layout then return a renderer-agnostic grid of cell dicts.
    Pass ``visited`` (a set of room IDs) to show only visited rooms + their
    one-exit frontier (fog-of-war); omit for an admin view of every room.

DIR_DELTA
    Public dict: exit direction name → (Δx, Δy).

FACE_OFFSET
    Public dict: direction name → (Δx, Δy) offset from room center to the
    face where that exit visually leaves the room.  Use for edge-line anchors.

REVERSE_DIR
    Public dict: direction name → opposite direction name.

exit_dest(exit_val) -> str
    Extract the destination room ID from an exit value (str or dict).
"""

from __future__ import annotations

from collections import deque


# ── Direction → grid-delta mapping ───────────────────────────────────────────
# "up"/"down" use Δy ±2 to keep them visually separated from north/south.

DIR_DELTA: dict[str, tuple[int, int]] = {
    "north": (0,  1),  "south": (0, -1),
    "east":  (1,  0),  "west":  (-1,  0),
    "northeast": ( 1,  1),  "northwest": (-1,  1),
    "southeast": ( 1, -1),  "southwest": (-1, -1),
    "up":   (0,  2),  "down": (0, -2),
    # Abbreviations accepted by some zones
    "n": (0,  1),  "s": (0, -1),  "e": (1, 0),  "w": (-1, 0),
    "ne": ( 1,  1),  "nw": (-1,  1),
    "se": ( 1, -1),  "sw": (-1, -1),
    "u":  (0,  2),  "d":  (0, -2),
}

# Fractional offset from room CENTER toward the exit face, as fractions of
# (half-width, half-height).  Renderers multiply by (CW/2, CH/2).
# SVG convention: +x = right, +y = down  →  north = (0, -1) in SVG.
FACE_OFFSET: dict[str, tuple[float, float]] = {
    "north":     ( 0.0, -1.0),
    "south":     ( 0.0,  1.0),
    "east":      ( 1.0,  0.0),
    "west":      (-1.0,  0.0),
    "northeast": ( 1.0, -1.0),
    "northwest": (-1.0, -1.0),
    "southeast": ( 1.0,  1.0),
    "southwest": (-1.0,  1.0),
    "up":        ( 0.0, -1.0),   # visually same as north
    "down":      ( 0.0,  1.0),   # visually same as south
    # abbreviations
    "n":  ( 0.0, -1.0), "s":  ( 0.0,  1.0),
    "e":  ( 1.0,  0.0), "w":  (-1.0,  0.0),
    "ne": ( 1.0, -1.0), "nw": (-1.0, -1.0),
    "se": ( 1.0,  1.0), "sw": (-1.0,  1.0),
    "u":  ( 0.0, -1.0), "d":  ( 0.0,  1.0),
}

REVERSE_DIR: dict[str, str] = {
    "north": "south", "south": "north",
    "east":  "west",  "west":  "east",
    "northeast": "southwest", "southwest": "northeast",
    "northwest": "southeast", "southeast": "northwest",
    "up":   "down",   "down":  "up",
    "n": "s",  "s": "n",  "e": "w",  "w": "e",
    "ne": "sw", "sw": "ne", "nw": "se", "se": "nw",
    "u": "d",  "d": "u",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def exit_dest(exit_val) -> str:
    """Return the destination room ID from an exit value (str or dict)."""
    if isinstance(exit_val, dict):
        return exit_val.get("to", "")
    return exit_val or ""


def _nearest_free(grid: dict, pos: tuple) -> tuple:
    """Spiral outward from *pos* until an unoccupied grid cell is found."""
    if pos not in grid:
        return pos
    for radius in range(1, 40):
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                if abs(dx) == radius or abs(dy) == radius:
                    p = (pos[0] + dx, pos[1] + dy)
                    if p not in grid:
                        return p
    return (pos[0] + 50, pos[1] + 50)


def _nearest_free_along_direction(
    grid: dict, ideal: tuple, delta: tuple
) -> tuple:
    """
    When *ideal* is occupied, prefer positions that continue along *delta*
    before falling back to the generic spiral.

    This keeps displaced rooms on the same directional axis as their parent,
    which dramatically reduces line crossings.
    """
    if ideal not in grid:
        return ideal
    dx, dy = delta
    if dx != 0 or dy != 0:
        sx = (1 if dx > 0 else -1) if dx else 0
        sy = (1 if dy > 0 else -1) if dy else 0
        for dist in range(1, 30):
            p = (ideal[0] + sx * dist, ideal[1] + sy * dist)
            if p not in grid:
                return p
    return _nearest_free(grid, ideal)


def _pull_positions(
    pos_of: dict, grid: dict, rooms: dict, explicit_ids: set
) -> None:
    """
    Iteratively move auto-placed rooms toward their ideal positions.

    After BFS, some rooms may have been displaced from their ideal grid
    position.  This pass repeatedly scans auto-placed rooms and moves any
    whose ideal position (derived from a neighbour's current position) has
    since become free.  Converges quickly (usually 1–3 iterations).
    """
    changed = True
    for _ in range(10):           # cap iterations to avoid infinite loops
        if not changed:
            break
        changed = False
        for rid in list(pos_of):
            if rid in explicit_ids:
                continue
            cur = pos_of[rid]
            for direction, ev in rooms[rid].get("exits", {}).items():
                dest = exit_dest(ev)
                if dest not in pos_of:
                    continue
                dx, dy = DIR_DELTA.get(direction, (0, 0))
                if not dx and not dy:
                    continue
                # ideal = where we *should* be relative to our neighbour
                ideal = (pos_of[dest][0] - dx, pos_of[dest][1] - dy)
                if ideal == cur:
                    break          # already in ideal spot — stop checking
                if ideal not in grid:
                    del grid[cur]
                    pos_of[rid] = ideal
                    grid[ideal] = rid
                    changed = True
                    break


# ── Auto-layout ───────────────────────────────────────────────────────────────

def apply_auto_layout(rooms: dict) -> None:
    """
    Compute a display position for every room and store it in
    ``room["_display_coord"]``.

    Algorithm
    ---------
    1. Place rooms with explicit ``coord`` fields first (authoritative).
    2. BFS outward from all placed rooms using exit-direction deltas.
       When the ideal cell is occupied the room is placed along the *same
       directional axis* (rather than a generic spiral), keeping displaced
       rooms on the correct bearing and minimising line crossings.
    3. After BFS, an iterative "pull" pass moves any auto-placed room whose
       ideal position has since been vacated closer to its correct location.
    4. Disconnected islands land near the origin.

    The function mutates *rooms* in-place and is idempotent.
    """
    pos_of: dict[str, tuple] = {}
    grid:   dict[tuple, str] = {}
    explicit_ids: set[str]   = set()

    # ── Phase 1: seed from explicit coords ───────────────────────────────────
    for rid, room in rooms.items():
        c = room.get("coord")
        if c and len(c) >= 2:
            p = (int(c[0]), int(c[1]))
            pos_of[rid] = p
            grid[p] = rid
            explicit_ids.add(rid)

    unplaced: set[str] = {rid for rid in rooms if rid not in pos_of}
    if not unplaced:
        for rid in rooms:
            rooms[rid]["_display_coord"] = list(pos_of[rid])
        return

    # ── Phase 2: BFS with directional-aware collision resolution ─────────────
    # Queue entries: (room_id, ideal_position, direction_delta)
    queue:    deque[tuple[str, tuple, tuple]] = deque()
    in_queue: set[str] = set()

    def enqueue_neighbors(rid: str, p: tuple) -> None:
        for direction, ev in rooms[rid].get("exits", {}).items():
            dest = exit_dest(ev)
            if dest in unplaced and dest not in in_queue:
                dx, dy = DIR_DELTA.get(direction, (0, 0))
                if dx or dy:
                    queue.append((dest, (p[0] + dx, p[1] + dy), (dx, dy)))
                    in_queue.add(dest)

    for rid, p in pos_of.items():
        enqueue_neighbors(rid, p)

    # If no explicit-coord rooms exist, seed from start room or first room
    if not queue:
        seed = next(
            (rid for rid, r in rooms.items() if r.get("start")),
            next(iter(rooms), None),
        )
        if seed and seed in unplaced:
            p = (0, 0)
            pos_of[seed] = p
            grid[p] = seed
            unplaced.discard(seed)
            in_queue.add(seed)
            enqueue_neighbors(seed, p)

    while queue:
        rid, expected, delta = queue.popleft()
        if rid not in unplaced:
            continue
        p = _nearest_free_along_direction(grid, expected, delta)
        pos_of[rid] = p
        grid[p] = rid
        unplaced.discard(rid)
        enqueue_neighbors(rid, p)

    # ── Phase 3: pull auto-placed rooms toward their ideal positions ──────────
    _pull_positions(pos_of, grid, rooms, explicit_ids)

    # ── Phase 4: disconnected islands ────────────────────────────────────────
    for rid in list(unplaced):
        p = _nearest_free(grid, (0, 0))
        pos_of[rid] = p
        grid[p] = rid

    # ── Write results ─────────────────────────────────────────────────────────
    for rid, room in rooms.items():
        p = pos_of.get(rid)
        if p:
            room["_display_coord"] = list(p)
            if rid not in explicit_ids:
                room["_auto_placed"] = True


# ── Public API ────────────────────────────────────────────────────────────────

def build_map_data(
    rooms: dict,
    visited: "set[str] | None" = None,
    current: "str | None"      = None,
) -> "dict[tuple[int, int], dict]":
    """
    Apply auto-layout then return a renderer-agnostic display grid.

    Parameters
    ----------
    rooms : dict
        Mapping of ``room_id → room_dict`` loaded from TOML.  Dicts are
        mutated in-place with ``_display_coord`` (and ``_auto_placed``).
    visited : set[str] | None
        If provided, only visited rooms + their one-exit frontier are included
        (fog-of-war for the in-game map).  If ``None``, all rooms are shown
        (admin / tools view).
    current : str | None
        Room ID of the player's current location; sets ``cell["here"] = True``.

    Returns
    -------
    dict[(int, int), dict]
        Grid mapping ``(x, y)`` → cell::

            {
                "id":         str,   # room ID
                "name":       str,   # display name
                "visited":    bool,  # True if player has been here
                "here":       bool,  # True if this is the current room
                "exits":      dict,  # raw exits dict from TOML
                "auto_placed": bool, # True when coord was inferred, not explicit
            }

        This structure is renderer-agnostic: the CLI renders it as ASCII, an
        HTML frontend can render it as SVG/canvas, and it serialises cleanly to
        JSON for a future web API.
    """
    apply_auto_layout(rooms)

    if visited is not None:
        visible: set[str] = set(visited)
        for rid in visited:
            for ev in rooms.get(rid, {}).get("exits", {}).values():
                dest = exit_dest(ev)
                if dest:
                    visible.add(dest)
    else:
        visible = set(rooms.keys())

    grid: dict[tuple[int, int], dict] = {}
    for rid in visible:
        room = rooms.get(rid)
        if not room:
            continue
        dc = room.get("_display_coord")
        if not dc:
            continue
        x, y = int(dc[0]), int(dc[1])
        grid[(x, y)] = {
            "id":         rid,
            "name":       room.get("name", "?"),
            "visited":    visited is None or rid in visited,
            "here":       rid == current,
            "exits":      room.get("exits", {}),
            "auto_placed": room.get("_auto_placed", False),
        }

    return grid
