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


# ── Auto-layout ───────────────────────────────────────────────────────────────

def apply_auto_layout(rooms: dict) -> None:
    """
    Compute a display position for every room and store it in
    ``room["_display_coord"]``.

    - Rooms with an explicit ``coord`` field use that value (authoritative).
    - All other rooms are BFS-placed from their neighbours via exit direction
      deltas; ``_nearest_free()`` resolves grid collisions.
    - Rooms with no path to any already-placed room land near the origin.
    - Sets ``room["_auto_placed"] = True`` for rooms without an explicit coord.

    The function mutates *rooms* in-place and is idempotent — calling it again
    on the same dict is safe (already-placed rooms keep their positions).
    """
    pos_of: dict[str, tuple] = {}
    grid:   dict[tuple, str] = {}

    # Seed grid from rooms that already have explicit coordinates
    for rid, room in rooms.items():
        c = room.get("coord")
        if c and len(c) >= 2:
            p = (int(c[0]), int(c[1]))
            pos_of[rid] = p
            grid[p] = rid

    unplaced: set[str] = {rid for rid in rooms if rid not in pos_of}

    # If every room has an explicit coord we're done
    if not unplaced:
        for rid in rooms:
            rooms[rid]["_display_coord"] = list(pos_of[rid])
        return

    queue:    deque[tuple[str, tuple]] = deque()
    in_queue: set[str] = set()

    def enqueue_neighbors(rid: str, p: tuple) -> None:
        for direction, ev in rooms[rid].get("exits", {}).items():
            dest = exit_dest(ev)
            if dest in unplaced and dest not in in_queue:
                dx, dy = DIR_DELTA.get(direction, (0, 0))
                if dx or dy:
                    queue.append((dest, (p[0] + dx, p[1] + dy)))
                    in_queue.add(dest)

    # Seed BFS from all rooms that already have positions
    for rid, p in pos_of.items():
        enqueue_neighbors(rid, p)

    # If no explicit-coord rooms exist at all, seed from the start/first room
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
        rid, expected = queue.popleft()
        if rid not in unplaced:
            continue
        p = _nearest_free(grid, expected)
        pos_of[rid] = p
        grid[p] = rid
        unplaced.discard(rid)
        enqueue_neighbors(rid, p)

    # Disconnected islands with no cardinal path to anything placed
    for rid in list(unplaced):
        p = _nearest_free(grid, (0, 0))
        pos_of[rid] = p
        grid[p] = rid

    # Write results back into room dicts
    for rid, room in rooms.items():
        p = pos_of.get(rid)
        if p:
            room["_display_coord"] = list(p)
            if not room.get("coord"):
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
        # Visible = rooms the player has been in + the rooms just beyond exits
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
