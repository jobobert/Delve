"""
world.py — Zone-streaming world manager for Delve.

Memory model
────────────
The world is divided into zones, one TOML file per zone (data/rooms/*.toml).
Only the player's current zone plus its immediate neighbours are kept in RAM.
Zones load on demand and evict automatically when the player moves away.

Always in memory (tiny):
  _zone_index    {zone_id → ZoneMeta}   file path + set of room IDs
  _room_to_zone  {room_id → zone_id}    fast room→zone lookup
  items          {item_id → template}   global item templates
  npcs           {npc_id  → template}   global NPC templates

Loaded on demand:
  _loaded_zones  {zone_id → {room_id → room_dict}}

Door / exit format
──────────────────
Exits can be a plain room ID string, or a door object:
  { to = "room_id", locked = true, lock_tag = "tag", desc = "..." }

_exit_dest() normalises both forms to a bare room ID wherever exits are
iterated internally (adjacency checks, state persistence, etc.).

Live state persistence
──────────────────────
On zone eviction, live NPC HP and current room item lists are written to
data/players/<name>/zone_state/<zone_id>.json (per-player). On next load
the sidecar is applied over the fresh TOML so the world remembers what
that player did, independently of any other player session.

NPC spawning
────────────
NPC instances are deepcopied from templates lazily on first room visit
(not at world load). This keeps startup instant regardless of world size.

Item filtering
──────────────
Items with respawn = false that the player has already picked up are
suppressed via player.looted_items (a set of "room_id:item_id" keys).
Items with respawn = true always appear.

Zone eviction policy
────────────────────
Keep current zone + all directly adjacent zones loaded. Evict everything
else. "Adjacent" means any zone that has a room reachable by a single exit
from any room in the current zone.
"""

from __future__ import annotations
import json
import random
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from engine.toml_io import load as toml_load


# DATA_DIR and ZONE_STATE_DIR are no longer module-level constants; they are
# instance variables on World (set in __init__) so each World instance can
# point at its own world folder.  See World.__init__.

# NPC fields that are normally scalar. If the TOML supplies a list for any of
# these, _resolve_random_fields() picks one element at random on each spawn.
# Fields that are intrinsically lists (tags, shop, give_accepts, kill_script,
# effects, on_hit) are intentionally excluded.
_RANDOM_FIELDS = frozenset({
    "name", "desc_short", "desc_long",
    "hp", "max_hp",
    "attack", "defense",
    "style", "style_prof",
    "xp_reward", "gold_reward",
})


def _resolve_random_fields(inst: dict) -> None:
    """Replace any list-valued scalar fields with a single random choice.

    Called on a freshly deepcopied NPC instance before it is added to a room.
    If an author writes e.g. ``max_hp = [18, 22, 28]``, each wolf that spawns
    will independently roll one of those values.
    """
    for field in _RANDOM_FIELDS:
        val = inst.get(field)
        if isinstance(val, list) and val:
            inst[field] = random.choice(val)


# ── Zone metadata (always in memory, one line per zone) ───────────────────────

@dataclass
class ZoneMeta:
    zone_id:    str
    folder:     Path              # data/<zone_id>/
    name:       str               = ""    # display name from zone.toml; falls back to zone_id
    room_files: list              = None   # .toml files containing [[room]] blocks
    room_ids:   set               = None
    loaded:     bool              = False

    def __post_init__(self):
        if not self.name:
            self.name = self.zone_id.replace("_", " ").title()
        if self.room_files is None: self.room_files = []
        if self.room_ids   is None: self.room_ids   = set()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _short(obj: dict) -> str:
    return obj.get("desc_short") or obj.get("description", "")

def _long(obj: dict) -> str:
    return obj.get("desc_long") or _short(obj)

def _exit_dest(exit_val) -> str:
    """Extract destination room_id from either a plain string or a door dict."""
    if isinstance(exit_val, dict):
        return exit_val.get("to", "")
    return exit_val or ""


# _zone_dirs() is now an instance method on World — see World._zone_dirs().


def _records_from_folder(zone_folder, key: str) -> list:
    """
    Collect all [[key]] records from every .toml file in zone_folder.
    Only the top-level *.toml files are scanned here; subdirectory files
    (styles/, quests/, dialogues/) are handled by their own loaders.
    Returns (records, room_file_paths) where room_file_paths is only
    populated when key == "room".
    """
    records: list = []
    room_files: list = []
    for path in sorted(zone_folder.glob("*.toml")):
        try:
            data = toml_load(path)
        except Exception:
            continue
        if key in data:
            records.extend(data[key])
            if key == "room":
                room_files.append(path)
    return records, room_files



# ── World ─────────────────────────────────────────────────────────────────────

class World:
    def __init__(self, world_path: Path, zone_state_dir: Path | None = None):
        self._world_path     = world_path
        self._zone_state_dir = zone_state_dir or (world_path / "zone_state")
        self._zone_state_dir.mkdir(parents=True, exist_ok=True)

        # Global lightweight catalogues — always in memory
        self.items: dict[str, dict] = {}   # item templates
        self.npcs:  dict[str, dict] = {}   # npc templates

        # Zone index — tiny, always in memory
        self._zone_index:  dict[str, ZoneMeta]        = {}  # zone_id → meta
        self._room_to_zone: dict[str, str]             = {}  # room_id → zone_id

        # Live zone data — only loaded zones
        self._loaded_zones: dict[str, dict[str, dict]] = {}  # zone_id → {room_id → room}

        # Corpse dict: room_id → list of corpse dicts
        # Each corpse: {owner, items, equipped, expires_at}
        self._corpses: dict[str, list[dict]] = {}
        self._build_index()

    def attach_player(self, player) -> None:
        """Switch to this player's personal zone state directory.

        Call after login and before any zones are loaded.  Safe because
        _build_index() only reads TOML files and never accesses _zone_state_dir.
        """
        self._zone_state_dir = player.zone_state_dir
        self._zone_state_dir.mkdir(parents=True, exist_ok=True)

    # ── Zone discovery ────────────────────────────────────────────────────────

    def _zone_dirs(self) -> list:
        """Return sorted zone folder Paths inside the world folder, skipping runtime dirs."""
        _SKIP = {"zone_state", "players"}
        return sorted(
            p for p in self._world_path.iterdir()
            if p.is_dir() and p.name not in _SKIP
        )

    # ── Index construction (startup — fast, no room data loaded) ─────────────

    def _build_index(self) -> None:
        """
        Scan all zone folders; build item/npc templates and zone index.

        Discovery order: zone folders sorted alphabetically (millhaven < training …).
        For items and NPCs, first definition of a given id wins — duplicates are
        silently ignored here (validate.py warns about them).

        Room files within a zone are also discovered by scanning each zone folder
        for .toml files containing [[room]] blocks. Any number of room files per
        zone is supported.
        """
        for zone_folder in self._zone_dirs():
            zone_id = zone_folder.name

            # ── Items and NPCs: first-definition-wins ─────────────────────────
            item_records, _ = _records_from_folder(zone_folder, "item")
            for item in item_records:
                iid = item.get("id", "")
                if iid and iid not in self.items:
                    self.items[iid] = item

            npc_records, _ = _records_from_folder(zone_folder, "npc")
            for npc in npc_records:
                nid = npc.get("id", "")
                if nid and nid not in self.npcs:
                    self.npcs[nid] = npc

            # ── Zone index: collect room IDs and room file paths ──────────────
            room_records, room_files = _records_from_folder(zone_folder, "room")
            if not room_files:
                continue   # folder has no room data (e.g. a dialogues-only zone)

            zone_toml = zone_folder / "zone.toml"
            zone_name = ""
            if zone_toml.exists():
                try:
                    zone_name = toml_load(zone_toml).get("name", "")
                except Exception:
                    pass
            meta = ZoneMeta(zone_id=zone_id, folder=zone_folder,
                            name=zone_name, room_files=room_files)
            for room in room_records:
                rid = room.get("id", "")
                if rid:
                    meta.room_ids.add(rid)
                    self._room_to_zone[rid] = zone_id
            self._zone_index[zone_id] = meta

    # ── Zone loading / unloading ──────────────────────────────────────────────

    def _load_zone(self, zone_id: str) -> None:
        """Load a zone's rooms into memory from all room files, then restore state."""
        meta = self._zone_index.get(zone_id)
        if not meta or meta.loaded:
            return

        # Collect rooms from all room files in this zone folder
        rooms: list[dict] = []
        for room_file in meta.room_files:
            try:
                rooms.extend(toml_load(room_file).get("room", []))
            except Exception:
                pass
        zone_rooms: dict[str, dict] = {}

        for room in rooms:
            room = dict(room)   # shallow copy — we'll mutate
            rid  = room.get("id", "")

            # Resolve item templates (don't deepcopy yet — done in prepare_room)
            templates = []
            for entry in room.get("items", []):
                if isinstance(entry, str):
                    tmpl = self.items.get(entry)
                    if tmpl:
                        templates.append(tmpl)
                else:
                    templates.append(entry)
            room["_item_templates"] = templates

            # NPCs not spawned yet — defer to first visit
            room["_npcs"]       = None    # sentinel: not spawned
            room["items"]       = None    # sentinel: not initialised for player
            room.setdefault("exits", {})
            zone_rooms[rid] = room

        self._loaded_zones[zone_id] = zone_rooms
        meta.loaded = True

        # Reapply any saved live state
        self._restore_zone_state(zone_id, zone_rooms)

    def _spawn_npcs(self, room: dict) -> None:
        """Lazily spawn NPC instances into a room on first entry."""
        if room.get("_npcs") is not None:
            return
        room["_npcs"] = []
        for spawn in room.get("spawns", []):
            npc_id = spawn if isinstance(spawn, str) else spawn.get("id")
            if npc_id and npc_id in self.npcs:
                inst = deepcopy(self.npcs[npc_id])
                _resolve_random_fields(inst)
                inst.setdefault("hp", inst.get("max_hp", 10))
                room["_npcs"].append(inst)

    def _evict_zone(self, zone_id: str) -> None:
        """Save live zone state to disk and free memory."""
        if zone_id not in self._loaded_zones:
            return
        self._save_zone_state(zone_id)
        del self._loaded_zones[zone_id]
        meta = self._zone_index.get(zone_id)
        if meta:
            meta.loaded = False

    def _adjacent_zone_ids(self, zone_id: str) -> set[str]:
        """Find zones that share an exit target with any room in this zone."""
        zone_rooms = self._loaded_zones.get(zone_id, {})
        adjacent   = set()
        for room in zone_rooms.values():
            for exit_val in room.get("exits", {}).values():
                dest_id   = _exit_dest(exit_val)
                dest_zone = self._room_to_zone.get(dest_id)
                if dest_zone and dest_zone != zone_id:
                    adjacent.add(dest_zone)
        return adjacent

    def evict_distant_zones(self, current_zone_id: str) -> None:
        """Keep current + adjacent zones; evict everything else."""
        keep = {current_zone_id} | self._adjacent_zone_ids(current_zone_id)
        for zid in list(self._loaded_zones.keys()):
            if zid not in keep:
                self._evict_zone(zid)

    # ── Zone state persistence ────────────────────────────────────────────────

    def _state_path(self, zone_id: str) -> Path:
        return self._zone_state_dir / f"{zone_id}.json"

    def _save_zone_state(self, zone_id: str) -> None:
        """Persist live NPC HP and room item lists to a JSON sidecar."""
        zone_rooms = self._loaded_zones.get(zone_id, {})
        state: dict = {}
        for rid, room in zone_rooms.items():
            entry: dict = {}
            npcs = room.get("_npcs")
            if npcs is not None:
                entry["npcs"] = [
                    {"id": n.get("id",""), "hp": n.get("hp", n.get("max_hp",0))}
                    for n in npcs
                ]
            items = room.get("items")
            if items is not None:
                entry["item_ids"] = [i.get("id","") for i in items]
            if entry:
                state[rid] = entry
        if state:
            self._state_path(zone_id).write_text(json.dumps(state, indent=2))

    def _restore_zone_state(self, zone_id: str, zone_rooms: dict) -> None:
        """Reapply saved live state (NPC HP, item list) after a zone reload."""
        path = self._state_path(zone_id)
        if not path.exists():
            return
        state = json.loads(path.read_text())
        for rid, entry in state.items():
            room = zone_rooms.get(rid)
            if not room:
                continue
            # Restore NPC HP (spawn if needed, then patch HP)
            if "npcs" in entry:
                self._spawn_npcs(room)
                hp_map = {n["id"]: n["hp"] for n in entry["npcs"]}
                for npc in room["_npcs"]:
                    if npc.get("id") in hp_map:
                        npc["hp"] = hp_map[npc["id"]]
            # Restore item list (which items were still on the ground)
            if "item_ids" in entry:
                saved_ids = entry["item_ids"]
                room["_item_templates"] = [
                    self.items[iid] for iid in saved_ids if iid in self.items
                ]

    def save_all_zone_state(self) -> None:
        """Call on game exit — persist state for all currently-loaded zones."""
        for zone_id in list(self._loaded_zones.keys()):
            self._save_zone_state(zone_id)

    # ── Public API ────────────────────────────────────────────────────────────

    def _ensure_loaded(self, room_id: str) -> None:
        """Make sure the zone containing room_id is in memory."""
        zone_id = self._room_to_zone.get(room_id)
        if zone_id and not self._zone_index[zone_id].loaded:
            self._load_zone(zone_id)

    def _init_room_items(self, room: dict, player) -> None:
        """Build live item list for a room, filtered for this player."""
        live = []
        rid  = room["id"]
        for tmpl in room.get("_item_templates", []):
            item_id  = tmpl.get("id", "")
            respawns = tmpl.get("respawn", False)
            if not respawns and player.has_looted(rid, item_id):
                continue
            live.append(deepcopy(tmpl))
        room["items"] = live

    def prepare_room(self, room_id: str, player) -> dict | None:
        """
        Return a fully-initialised room for this player:
          - Zone is loaded if needed
          - NPCs are spawned if first visit
          - Items are filtered per player's loot log
        """
        self._ensure_loaded(room_id)
        zone_id   = self._room_to_zone.get(room_id)
        if not zone_id:
            return None
        zone_rooms = self._loaded_zones.get(zone_id, {})
        room       = zone_rooms.get(room_id)
        if not room:
            return None
        self._spawn_npcs(room)
        if room["items"] is None:
            self._init_room_items(room, player)
        return room

    def get_room(self, room_id: str) -> dict | None:
        """Return room as-is (zone must already be loaded). Use prepare_room for player context."""
        zone_id = self._room_to_zone.get(room_id)
        if not zone_id:
            return None
        return self._loaded_zones.get(zone_id, {}).get(room_id)

    def zone_for_room(self, room_id: str) -> str | None:
        return self._room_to_zone.get(room_id)

    def spawn_item(self, item_id: str) -> dict | None:
        tmpl = self.items.get(item_id)
        return deepcopy(tmpl) if tmpl else None

    def spawn_npc_in_room(self, npc_id: str, room_id: str) -> bool:
        """Spawn a fresh copy of npc_id into room_id's live NPC list.

        Returns True on success.  The room must be already loaded; if the zone
        isn't loaded (or npc_id is unknown) the call is a no-op.
        """
        room = self.get_room(room_id)
        if room is None:
            return False
        if room.get("_npcs") is None:
            room["_npcs"] = []
        tmpl = self.npcs.get(npc_id)
        if tmpl is None:
            return False
        room["_npcs"].append(deepcopy(tmpl))
        return True

    # ── Corpse system ─────────────────────────────────────────────────────────

    CORPSE_TTL = 600   # seconds before an unclaimed corpse decays

    def drop_corpse(self, room_id: str, owner: str,
                    items: list[dict], equipped: dict) -> None:
        """Spawn a player corpse in room_id containing their dropped items.

        items    — copies of inventory items that were not no_drop
        equipped — copies of equipped items that were not no_drop
        Both lists may be empty (e.g. if everything was banked or no_drop).
        """
        corpse_items = list(items)
        for item in equipped.values():
            if item:
                corpse_items.append(item)
        if not corpse_items:
            return   # nothing to drop — no corpse needed
        corpse = {
            "owner":      owner,
            "items":      corpse_items,
            "expires_at": time.time() + self.CORPSE_TTL,
        }
        self._corpses.setdefault(room_id, []).append(corpse)

    def get_corpses(self, room_id: str, prune: bool = True) -> list[dict]:
        """Return all live corpses in room_id, optionally pruning expired ones."""
        corpses = self._corpses.get(room_id, [])
        if prune:
            now = time.time()
            live = [c for c in corpses if c["expires_at"] > now]
            if len(live) != len(corpses):
                self._corpses[room_id] = live
            return live
        return corpses

    def remove_corpse(self, room_id: str, corpse: dict) -> None:
        """Remove a specific corpse from a room (after it's been looted)."""
        corpses = self._corpses.get(room_id, [])
        if corpse in corpses:
            corpses.remove(corpse)

    @property
    def start_room(self) -> str:
        """Find the room with start=true by scanning all zone room files."""
        for meta in self._zone_index.values():
            for room_file in meta.room_files:
                for room in toml_load(room_file).get("room", []):
                    if room.get("start"):
                        return room["id"]
        return ""

    # ── Memory stats (for debugging / admin) ─────────────────────────────────

    def memory_report(self) -> str:
        import sys
        loaded = list(self._loaded_zones.keys())
        total_rooms = sum(len(z) for z in self._loaded_zones.values())
        total_npcs  = sum(
            len(r.get("_npcs") or [])
            for z in self._loaded_zones.values()
            for r in z.values()
        )
        return (
            f"Zones loaded: {len(loaded)}/{len(self._zone_index)} "
            f"({', '.join(loaded) or 'none'})\n"
            f"Rooms in memory: {total_rooms} | Live NPCs: {total_npcs}"
        )




