"""
engine/log.py — Structured engine logger for Delve.

Writes human-readable, machine-parseable logs to a rotating file so that
combat bugs (especially auto-attack loop issues) can be diagnosed by pasting
the relevant section into a conversation window.

Configuration (all in frontend/config.py):
  LOG_ENABLED     = True        # master switch
  LOG_FILE        = "delve.log" # path relative to the mud/ root
  LOG_LEVEL       = "DEBUG"     # DEBUG | INFO | WARN
  LOG_CATEGORIES  = ["combat", "autoattack", "script", "world", "player"]

Log format
──────────
Each line is:

  2026-02-23 14:05:01.234 [LEVEL] [CATEGORY] message
    key=value  key=value  ...        ← optional structured data, always indented

Sections (→ ENTER / ← EXIT) bracket logical operations so you can see the
full call stack at a glance:

  → ENTER combat.player_attack  player=Aerin npc=Corrupted Wolf hp_before=35
    atk=12 def=3 roll=-1..4  base_dmg=9
    passive=parry  result=blocked
  ← EXIT  combat.player_attack  npc_hp=26 player_hp=48  elapsed_ms=0.4

Paste any contiguous block into the chat window — the → / ← markers and
key=value pairs make it straightforward to replay the logic.

Thread safety: single-writer assumption (MUD is single-threaded). No locks.
"""

from __future__ import annotations

import os
import sys
import time
import datetime
from pathlib import Path
from typing import Any

# ── Runtime state ─────────────────────────────────────────────────────────────

_enabled:    bool       = False
_level:      str        = "DEBUG"
_file_path:  Path | None = None
_file_obj:   Any        = None   # open file handle
_categories: set[str]   = set()

_LEVELS = {"DEBUG": 0, "INFO": 1, "WARN": 2}

# ── Public API ────────────────────────────────────────────────────────────────

def configure(
    enabled:    bool      = False,
    log_file:   str       = "delve.log",
    level:      str       = "DEBUG",
    categories: list[str] | None = None,
) -> None:
    """
    Called once from the CLI at startup (after config.py is loaded).

    enabled    — master on/off switch
    log_file   — path to log file; relative paths are from the mud/ root
    level      — minimum severity: "DEBUG" | "INFO" | "WARN"
    categories — list of active categories; None/empty = log everything
    """
    global _enabled, _level, _file_path, _file_obj, _categories

    _enabled = bool(enabled)
    if not _enabled:
        return

    _level = level.upper() if level.upper() in _LEVELS else "DEBUG"
    _categories = set(c.lower() for c in (categories or []))

    # Resolve path relative to mud/ root (parent of engine/)
    root = Path(__file__).parent.parent
    _file_path = root / log_file if not Path(log_file).is_absolute() else Path(log_file)
    _file_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        _file_obj = open(_file_path, "a", encoding="utf-8", buffering=1)
        _file_obj.write(
            f"\n{'='*72}\n"
            f"  Delve engine log  —  session started "
            f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"  level={_level}  categories={sorted(_categories) or 'ALL'}\n"
            f"{'='*72}\n"
        )
    except OSError as e:
        print(f"[log] WARNING: cannot open log file {_file_path}: {e}", file=sys.stderr)
        _enabled = False


def _active(level: str, category: str) -> bool:
    if not _enabled or _file_obj is None:
        return False
    if _LEVELS.get(level, 0) < _LEVELS.get(_level, 0):
        return False
    if _categories and category.lower() not in _categories:
        return False
    return True


def _ts() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _fmt_kv(kv: dict) -> str:
    """Format a dict as  key=value  key=value  …  (one line, indented)."""
    if not kv:
        return ""
    parts = []
    for k, v in kv.items():
        if isinstance(v, float):
            parts.append(f"{k}={v:.2f}")
        elif isinstance(v, str) and " " in v:
            parts.append(f'{k}="{v}"')
        else:
            parts.append(f"{k}={v}")
    return "    " + "  ".join(parts)


def _write(level: str, category: str, msg: str, data: dict | None = None) -> None:
    line = f"{_ts()} [{level:<5}] [{category:<12}] {msg}\n"
    _file_obj.write(line)
    if data:
        _file_obj.write(_fmt_kv(data) + "\n")


# ── Logging functions (called from engine code) ───────────────────────────────

def debug(category: str, msg: str, **kv) -> None:
    if _active("DEBUG", category):
        _write("DEBUG", category, msg, kv or None)

def info(category: str, msg: str, **kv) -> None:
    if _active("INFO", category):
        _write("INFO", category, msg, kv or None)

def warn(category: str, msg: str, **kv) -> None:
    if _active("WARN", category):
        _write("WARN", category, msg, kv or None)


def enter(category: str, fn: str, **kv) -> float:
    """
    Log entry into a logical operation.  Returns a timestamp for elapsed timing.

      t = log.enter("combat", "player_attack", player_hp=48, npc_hp=35)
      ...
      log.exit("combat", "player_attack", t, npc_hp=26, player_hp=43)
    """
    if _active("DEBUG", category):
        line = f"{_ts()} [ENTER] [{category:<12}] → {fn}\n"
        _file_obj.write(line)
        if kv:
            _file_obj.write(_fmt_kv(kv) + "\n")
    return time.monotonic()


def exit(category: str, fn: str, t0: float = 0.0, **kv) -> None:
    """Log exit from a logical operation, with optional elapsed time."""
    if _active("DEBUG", category):
        elapsed = (time.monotonic() - t0) * 1000 if t0 else 0.0
        kv_out = dict(kv)
        if t0:
            kv_out["elapsed_ms"] = f"{elapsed:.1f}"
        line = f"{_ts()} [EXIT ] [{category:<12}] ← {fn}\n"
        _file_obj.write(line)
        if kv_out:
            _file_obj.write(_fmt_kv(kv_out) + "\n")


def section(title: str) -> None:
    """Write a visible section divider — use between turns or major events."""
    if _enabled and _file_obj:
        _file_obj.write(f"{'─'*72}\n  {title}\n{'─'*72}\n")


def close() -> None:
    """Flush and close the log file. Called at quit."""
    global _file_obj
    if _file_obj:
        _file_obj.write(
            f"{'='*72}\n"
            f"  session ended  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"{'='*72}\n"
        )
        _file_obj.close()
        _file_obj = None




