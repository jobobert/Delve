"""
processes.py — World process scheduler for Delve.

World processes are recurring scripts or NPC-route drivers defined in
``processes.toml`` files within zone folders.  They fire on player-action
ticks (the same tick used by status effects) at a configurable interval.

Process definition (processes.toml in any zone folder)
────────────────────────────────────────────────────────
[[process]]
id            = "caravan_route"
name          = "Merchant Caravan"
interval      = 5          # fire every 5 action ticks (default 1)
autostart     = false      # start automatically on world load (default false)
admin_comment = ""

# Option A — inline script list: run these ops on each fire
script = [
  { op = "message", text = "A caravan rolls past.", tag = "system" },
]

# Option A2 — external script file (world-relative path to a TOML ops file)
# The file must contain an "ops" or "script" array at the top level.
script_file = "scripts/caravan_tick.toml"

# Option B — NPC route: walk an NPC along waypoints
route_npc  = "traveling_merchant"   # NPC id to move
route_loop = "cycle"                # "cycle" (default) or "reverse"
route = [
  { room_id = "millhaven_square", ticks = 3 },
  { room_id = "millhaven_gate",   ticks = 5 },
]

Script ops that control processes (from any script context)
───────────────────────────────────────────────────────────
  { op = "process_start", process_id = "..." }   — activate / resume
  { op = "process_stop",  process_id = "..." }   — deactivate and reset ticks
  { op = "process_pause", process_id = "..." }   — suspend without resetting

State persistence
─────────────────
Per-player process state is stored in
  data/players/<name>/zone_state/_processes.json
alongside the zone state sidecars.  It is loaded at CommandProcessor
initialisation and written whenever the player saves.

Notes
─────
- Processes fire only on action ticks (not read-only commands like look/inventory).
- ``interval = N`` means the script or route step fires every N action ticks.
- Route-based NPC movement uses the existing ``move_npc`` script op internally.
  The NPC must be in a currently-loaded zone; if its zone is evicted and later
  reloaded the NPC will be placed by the normal spawn system, not the route.
- Unknown process IDs in script ops are silently ignored.
- Both ``script`` and ``route`` may be defined on the same process; the route
  advances first, then the script runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from engine.toml_io import load as toml_load

if TYPE_CHECKING:
    from engine.script import GameContext


_SKIP_DIRS = {"zone_state", "players"}


class ProcessManager:
    """Loads process definitions from all zone folders and manages per-player state."""

    def __init__(self, world_path: Path, state_dir: Path) -> None:
        self._world_path = world_path
        self._state_dir  = state_dir
        self._defs:  dict[str, dict] = {}   # process_id → process definition
        self._state: dict[str, dict] = {}   # process_id → runtime state
        self._load_definitions()
        self._load_state()

    # ── Definition loading ────────────────────────────────────────────────────

    def _load_definitions(self) -> None:
        """Scan every zone folder for processes.toml and collect definitions."""
        for zone_folder in sorted(self._world_path.iterdir()):
            if not zone_folder.is_dir():
                continue
            if zone_folder.name in _SKIP_DIRS:
                continue
            proc_file = zone_folder / "processes.toml"
            if not proc_file.exists():
                continue
            try:
                data = toml_load(proc_file)
            except Exception:
                continue
            for proc in data.get("process", []):
                pid = proc.get("id", "")
                if pid and pid not in self._defs:
                    self._defs[pid] = proc

    # ── State loading / saving ────────────────────────────────────────────────

    @staticmethod
    def _default_state(proc: dict) -> dict:
        return {
            "active":              proc.get("autostart", False),
            "paused":              False,
            "ticks":               0,   # accumulated action ticks since last fire
            "route_index":         0,   # current waypoint index
            "route_ticks_at_stop": 0,   # ticks spent at current waypoint
            "route_direction":     1,   # +1 = forward, -1 = reverse (mode "reverse")
        }

    def _load_state(self) -> None:
        path = self._state_dir / "_processes.json"
        saved: dict = {}
        if path.exists():
            try:
                saved = json.loads(path.read_text())
            except Exception:
                pass
        for pid, proc in self._defs.items():
            st = self._default_state(proc)
            if pid in saved:
                st.update(saved[pid])   # overlay persisted values
            self._state[pid] = st

    def save_state(self) -> None:
        """Write process state to _processes.json in the player's zone_state dir."""
        path = self._state_dir / "_processes.json"
        path.write_text(json.dumps(self._state, indent=2))

    # ── Control API (called by process_start / process_stop / process_pause) ──

    def start(self, pid: str) -> None:
        """Activate a process (or resume a paused one)."""
        st = self._state.get(pid)
        if st is not None:
            st["active"] = True
            st["paused"] = False

    def stop(self, pid: str) -> None:
        """Deactivate a process and reset its tick counters."""
        st = self._state.get(pid)
        if st is not None:
            st["active"] = False
            st["ticks"]  = 0

    def pause(self, pid: str) -> None:
        """Suspend a process without resetting its counters."""
        st = self._state.get(pid)
        if st is not None:
            st["paused"] = True

    # ── Tick ─────────────────────────────────────────────────────────────────

    def tick(self, ctx: "GameContext") -> None:
        """Advance all active processes by one action tick."""
        for pid, proc in self._defs.items():
            st = self._state.get(pid)
            if st is None or not st.get("active") or st.get("paused"):
                continue
            interval = max(1, int(proc.get("interval", 1)))
            st["ticks"] = st.get("ticks", 0) + 1
            if st["ticks"] < interval:
                continue
            st["ticks"] = 0
            self._fire(proc, st, ctx)

    def _fire(self, proc: dict, st: dict, ctx: "GameContext") -> None:
        """Execute one process cycle: advance route then run script ops."""
        route  = proc.get("route")
        npc_id = proc.get("route_npc", "")
        if route and npc_id:
            self._advance_route(proc, st, ctx, route, npc_id)

        script = proc.get("script")
        if script:
            from engine.script import ScriptRunner
            ScriptRunner(ctx).run(script)

        script_file = proc.get("script_file", "")
        if script_file:
            script_path = self._world_path / script_file
            if script_path.exists():
                try:
                    data = toml_load(script_path)
                    ops  = data.get("ops", data.get("script", []))
                    from engine.script import ScriptRunner
                    ScriptRunner(ctx).run(ops)
                except Exception:
                    pass

    def _advance_route(self, proc: dict, st: dict, ctx: "GameContext",
                       route: list, npc_id: str) -> None:
        """Move an NPC one step along its waypoint route when its dwell time expires."""
        if not route:
            return
        idx = st.get("route_index", 0)
        if idx >= len(route):
            idx = 0
            st["route_index"] = 0

        required_ticks = max(1, int(route[idx].get("ticks", 1)))
        st["route_ticks_at_stop"] = st.get("route_ticks_at_stop", 0) + 1
        if st["route_ticks_at_stop"] < required_ticks:
            return

        # Dwell time expired — advance to next waypoint
        st["route_ticks_at_stop"] = 0
        if proc.get("route_loop", "cycle") == "reverse":
            direction = st.get("route_direction", 1)
            next_idx  = idx + direction
            if next_idx >= len(route) or next_idx < 0:
                direction = -direction
                st["route_direction"] = direction
                next_idx = idx + direction
            next_idx = max(0, min(len(route) - 1, next_idx))
        else:
            next_idx = (idx + 1) % len(route)

        next_room_id = route[next_idx].get("room_id", "")
        if next_room_id:
            from engine.script import ScriptRunner
            ScriptRunner(ctx).run([
                {"op": "move_npc", "npc_id": npc_id, "to_room": next_room_id}
            ])
        st["route_index"] = next_idx
