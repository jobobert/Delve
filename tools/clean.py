#!/usr/bin/env python3
"""
tools/clean.py — Reset Delve to a clean state.

Usage:
  python tools/clean.py              # interactive (asks before deleting players)
  python tools/clean.py --all        # delete everything including players, no prompt
  python tools/clean.py --cache      # __pycache__ only
  python tools/clean.py --state      # zone state only
  python tools/clean.py --players    # player saves only
  python tools/clean.py --help       # this message

What gets cleaned
─────────────────
  __pycache__     Compiled .pyc bytecode in engine/, frontend/, tools/, root
  zone_state      data/zone_state/*.json  (live NPC HP / item state snapshots)
  players         data/players/*.toml     (character saves) — requires --all or
                  --players flag, OR confirmation prompt in interactive mode
"""

import sys
import shutil
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── Target definitions ────────────────────────────────────────────────────────

def _cache_dirs() -> list[Path]:
    return list(ROOT.rglob("__pycache__"))

def _zone_state_files() -> list[Path]:
    return [p for p in (ROOT / "data" / "zone_state").glob("*.json")
            if p.name != ".gitkeep"]

def _player_files() -> list[Path]:
    return [p for p in (ROOT / "data" / "players").glob("*.toml")]


# ── Clean actions ─────────────────────────────────────────────────────────────

def clean_cache(verbose: bool = True) -> int:
    dirs = _cache_dirs()
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)
        if verbose:
            print(f"  removed  {d.relative_to(ROOT)}")
    return len(dirs)

def clean_zone_state(verbose: bool = True) -> int:
    files = _zone_state_files()
    for f in files:
        f.unlink()
        if verbose:
            print(f"  removed  {f.relative_to(ROOT)}")
    return len(files)

def clean_players(verbose: bool = True) -> int:
    files = _player_files()
    for f in files:
        f.unlink()
        if verbose:
            print(f"  removed  {f.relative_to(ROOT)}")
    return len(files)


# ── Summary helpers ───────────────────────────────────────────────────────────

def _summarise() -> None:
    cache  = _cache_dirs()
    state  = _zone_state_files()
    players = _player_files()
    print(f"  __pycache__ dirs : {len(cache)}")
    print(f"  zone state files : {len(state)}")
    print(f"  player saves     : {len(players)}"
          + (f"  ({', '.join(f.stem for f in players)})" if players else ""))


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    # Determine what to clean from flags
    do_cache   = "--cache"   in args or "--all" in args or not args
    do_state   = "--state"   in args or "--all" in args or not args
    do_players = "--players" in args or "--all" in args

    print("Delve Clean Tool")
    print("=" * 40)
    _summarise()
    print()

    if not any([do_cache, do_state, do_players]):
        print("Nothing selected. Use --help for options.")
        return

    # Interactive prompt for players if not explicitly flagged
    if not args or (do_cache and not do_players):
        # Running interactively with no flags — ask about players
        player_files = _player_files()
        if player_files:
            names = ", ".join(f.stem for f in player_files)
            answer = input(f"Also delete player saves ({names})? [y/N] ").strip().lower()
            if answer == "y":
                do_players = True

    total = 0
    if do_cache:
        print("Clearing __pycache__...")
        n = clean_cache()
        print(f"  → {n} director{'y' if n == 1 else 'ies'} removed")
        total += n

    if do_state:
        print("Clearing zone state...")
        n = clean_zone_state()
        print(f"  → {n} file{'s' if n != 1 else ''} removed")
        total += n

    if do_players:
        print("Clearing player saves...")
        n = clean_players()
        print(f"  → {n} save{'s' if n != 1 else ''} removed")
        total += n

    print()
    if total:
        print(f"Done. {total} item{'s' if total != 1 else ''} removed.")
    else:
        print("Already clean — nothing to remove.")


if __name__ == "__main__":
    main()


