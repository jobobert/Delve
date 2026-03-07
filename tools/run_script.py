"""
tools/run_script.py — Run a standalone script file against a player.

Loads a world, loads (or creates) a player, executes the ops in a TOML script
file, then saves the player. Useful for one-shot world events, admin actions,
and testing script ops without running the full game.

Usage:
    python tools/run_script.py <script_file> --player <name> [--world <id>]

Arguments:
    script_file       Path to a TOML file containing a top-level ops = [...]
                      array. Can be absolute or relative to the world root.
    --player NAME     Character name to load (must already exist unless
                      --create is also passed).
    --world  ID       World folder id (e.g. first_world). Defaults to the
                      player's saved world_id, or prompts if ambiguous.
    --create          Create the player if they don't exist yet.
    --dry-run         Print the ops that would run without saving the player.

Script file format:
    # data/first_world/scripts/drake_returns.toml
    ops = [
      {op = "message", text = "The Drake has returned to Ashwood!"},
      {op = "set_flag", flag = "drake_returned"},
      {op = "spawn_item", item_id = "drake_scale", room_id = "ashwood_clearing"},
    ]

Exit code 0 = success, 1 = error.
"""

from __future__ import annotations
import argparse
import sys
from pathlib import Path

# Allow imports from the project root when running as a standalone script.
sys.path.insert(0, str(Path(__file__).parent.parent))

import engine.world_config as wc
import engine.log as log
from engine.toml_io import load as toml_load
from engine.world import World
from engine.player import Player
from engine.events import EventBus, Event
from engine.msg import Msg, Tag
from engine.quests import QuestTracker
from engine.script import ScriptRunner, GameContext


def _find_worlds(data_dir: Path) -> list[Path]:
    """Return all world directories (contain config.toml or config.py)."""
    worlds = []
    for p in sorted(data_dir.iterdir()):
        if not p.is_dir():
            continue
        if (p / "config.toml").exists() or (p / "config.py").exists():
            worlds.append(p)
    return worlds


def _resolve_world(args_world: str | None, data_dir: Path,
                   player_world_id: str | None) -> Path | None:
    """Pick a world path from the CLI flag, player save, or available worlds."""
    if args_world:
        candidate = data_dir / args_world
        if not candidate.is_dir():
            print(f"[error] World '{args_world}' not found in {data_dir}")
            return None
        return candidate

    if player_world_id:
        candidate = data_dir / player_world_id
        if candidate.is_dir():
            return candidate

    worlds = _find_worlds(data_dir)
    if len(worlds) == 1:
        return worlds[0]

    print("[error] Multiple worlds found. Specify one with --world <id>:")
    for w in worlds:
        print(f"  {w.name}")
    return None


def _load_script(script_arg: str, world_path: Path) -> list[dict] | None:
    """
    Load the ops list from a TOML script file.
    Accepts absolute paths or paths relative to the world root.
    """
    p = Path(script_arg)
    if not p.is_absolute():
        p = world_path / script_arg
    if not p.exists():
        print(f"[error] Script file not found: {p}")
        return None
    try:
        data = toml_load(p)
    except Exception as e:
        print(f"[error] Failed to parse {p}: {e}")
        return None
    ops = data.get("ops", [])
    if not ops:
        print(f"[warn] Script file has no ops array: {p}")
    return ops


def _make_bus(dry_run: bool) -> EventBus:
    """Create an EventBus that prints OUTPUT events to stdout."""
    bus = EventBus()

    def _on_output(msg: Msg) -> None:
        if not dry_run:
            print(f"  [{msg.tag}] {msg.text}")
        else:
            print(f"  [dry-run/{msg.tag}] {msg.text}")

    bus.on(Event.OUTPUT, _on_output)
    return bus


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a standalone TOML script against a player.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("script_file",
                        help="Path to the script TOML file (absolute or "
                             "relative to the world root)")
    parser.add_argument("--player", required=True,
                        help="Character name to run the script on")
    parser.add_argument("--world",
                        help="World folder id (e.g. first_world)")
    parser.add_argument("--create", action="store_true",
                        help="Create the player if they do not exist yet")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what the script would do without saving")
    args = parser.parse_args()

    data_dir     = Path(__file__).parent.parent / "data"
    players_dir  = data_dir / "players"
    player_path  = players_dir / args.player / "player.toml"

    # ── Load or create player ────────────────────────────────────────────────
    player: Player | None = None
    saved_world_id: str | None = None

    if player_path.exists():
        try:
            player = Player.load(player_path)
            saved_world_id = player.world_id
            print(f"Loaded player '{player.name}' (world: {saved_world_id or '?'})")
        except Exception as e:
            print(f"[error] Failed to load player '{args.player}': {e}")
            return 1
    elif args.create:
        print(f"Player '{args.player}' not found — will create after world loads.")
    else:
        print(f"[error] Player '{args.player}' not found. Use --create to make one.")
        return 1

    # ── Resolve world ────────────────────────────────────────────────────────
    world_path = _resolve_world(args.world, data_dir, saved_world_id)
    if world_path is None:
        return 1

    # ── Init world config ────────────────────────────────────────────────────
    try:
        wc.init(world_path)
    except Exception as e:
        print(f"[error] Failed to init world config for '{world_path.name}': {e}")
        return 1

    print(f"World: {wc.WORLD_NAME} ({world_path.name})")

    # ── Create player now if needed ──────────────────────────────────────────
    if player is None:
        player = Player.create_new(args.player)
        player.world_id = world_path.name
        print(f"Created new player '{player.name}'.")

    # ── Load world (needed for spawn_item, move_npc, etc.) ──────────────────
    try:
        zone_state_dir = players_dir / args.player / "zone_state"
        world = World(world_path, zone_state_dir=zone_state_dir)
        world.attach_player(player)
    except Exception as e:
        print(f"[error] Failed to load world '{world_path.name}': {e}")
        return 1

    # ── Load script file ─────────────────────────────────────────────────────
    ops = _load_script(args.script_file, world_path)
    if ops is None:
        return 1

    print(f"Script: {args.script_file}  ({len(ops)} op(s))")
    if args.dry_run:
        print("[dry-run] Ops that would execute:")
        for i, op in enumerate(ops):
            print(f"  {i+1}. {op}")
        print("[dry-run] Player NOT saved.")
        return 0

    # ── Execute ──────────────────────────────────────────────────────────────
    bus    = _make_bus(dry_run=False)
    quests = QuestTracker(player, world, bus)
    ctx    = GameContext(player=player, world=world, bus=bus, quests=quests)

    print("Running script...")
    try:
        ScriptRunner(ctx).run(ops)
    except Exception as e:
        print(f"[error] Script execution failed: {e}")
        log.warn("run_script", f"Script execution error: {e}",
                 script=args.script_file, player=args.player)
        return 1

    # ── Save player ──────────────────────────────────────────────────────────
    player_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        player.save(player_path)
        print(f"Player '{player.name}' saved to {player_path}")
    except Exception as e:
        print(f"[error] Failed to save player: {e}")
        return 1

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
