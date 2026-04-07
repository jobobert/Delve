"""
tools/validate.py — Offline data validator for Delve.

Scans all zone folders (data/*/) for TOML data files and checks:
  - Every room exit target must be a known room id
  - Every room item reference must be a known item id
  - Every room spawn reference must be a known NPC id
  - Every item must have id and name fields
  - Every NPC must have id, name, hp, max_hp, attack, defense fields
  - Duplicate item or NPC ids across zones (warns — first definition wins)
  - NPC shop references to unknown item IDs
  - Styles referenced by NPCs must exist
  - Exactly one room marked start = true
  - Every .toml file is valid TOML (via stdlib tomllib — strict parser)
  - Every dialogue file: nodes reachable, next= refs valid, no [[node.response]] usage

Run from the project root:
    python tools/validate.py                  # validate all worlds
    python tools/validate.py --world first_world  # validate one world only

Exit code 0 = passed (warnings okay), exit code 1 = errors found.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.toml_io import load as toml_load
import engine.world_config as wc
from engine.validate_world import validate_world as _run_core_checks

DATA_DIR = Path(__file__).parent.parent / "data"

# Set by main() before each world's validation pass.
# zone_dirs() reads this to find zones inside the active world folder.
_CURRENT_WORLD: Path | None = None

errors:   list[str] = []
warnings: list[str] = []


def err(msg: str)  -> None: errors.append(f"  [ERROR] {msg}")
def warn(msg: str) -> None: warnings.append(f"  [WARN]  {msg}")


def load_safe(path: Path) -> dict | None:
    try:
        return toml_load(path)
    except Exception as e:
        err(f"{path}: parse error — {e}")
        return None


def world_dirs() -> list[Path]:
    """Return world folder Paths — subfolders of DATA_DIR that contain config.toml or config.py."""
    return sorted(
        p for p in DATA_DIR.iterdir()
        if p.is_dir() and (
            (p / "config.toml").exists() or (p / "config.py").exists()
        )
    )


def zone_dirs() -> list[Path]:
    """Return zone folder Paths inside the current world, skipping runtime dirs."""
    base  = _CURRENT_WORLD if _CURRENT_WORLD else DATA_DIR
    _SKIP = {"zone_state", "players"}
    return sorted(p for p in base.iterdir() if p.is_dir() and p.name not in _SKIP)


# ── Collect all records of a given type, tracking source zone ────────────────

def collect_typed(key: str) -> tuple[dict[str, dict], dict[str, str]]:
    """
    Return (id→record, id→zone_id) by scanning every zone folder's top-level
    .toml files for [[key]] blocks. Warns on duplicate ids; first definition wins.
    """
    records: dict[str, dict]  = {}
    sources: dict[str, str]   = {}
    for zone_folder in zone_dirs():
        for path in sorted(zone_folder.glob("*.toml")):
            data = load_safe(path)
            if not data:
                continue
            for rec in data.get(key, []):
                rid = rec.get("id", "")
                if not rid:
                    err(f"{path.name} [{key}]: record missing 'id'")
                    continue
                if rid in records:
                    # Training zone intentionally mirrors a few starter items from
                    # millhaven so the tutorial area is self-contained.
                    _TRAINING_MIRRORS = {"rusty_sword", "chain_shirt", "health_potion"}
                    if rid in _TRAINING_MIRRORS and "training" in {sources[rid], zone_folder.name}:
                        pass  # known intentional duplicate — suppress
                    else:
                        warn(f"Duplicate {key} id '{rid}': defined in both "
                             f"'{sources[rid]}' and '{zone_folder.name}' "
                             f"(first definition wins)")
                else:
                    records[rid] = rec
                    sources[rid] = zone_folder.name
    return records, sources


def collect_rooms() -> dict[str, dict]:
    """Collect all rooms across all zone folders (any .toml with [[room]])."""
    rooms: dict[str, dict] = {}
    for zone_folder in zone_dirs():
        for path in sorted(zone_folder.glob("*.toml")):
            data = load_safe(path)
            if not data:
                continue
            for room in data.get("room", []):
                rid = room.get("id", "")
                if not rid:
                    err(f"{path.name}: [[room]] missing 'id'")
                    continue
                if rid in rooms:
                    err(f"Duplicate room id '{rid}' in {path}")
                else:
                    rooms[rid] = room
    return rooms


def collect_styles() -> set[str]:
    """Collect all known style ids from zone styles/ subdirectories."""
    style_ids: set[str] = set()
    for zone_folder in zone_dirs():
        styles_dir = zone_folder / "styles"
        if not styles_dir.exists():
            continue
        for path in sorted(styles_dir.glob("*.toml")):
            data = load_safe(path)
            if not data:
                continue
            for style in data.get("style", []):
                sid = style.get("id", "")
                if sid:
                    style_ids.add(sid)
    return style_ids


def collect_quests() -> dict[str, dict]:
    """Collect all quest definitions from zone quests/ subdirectories."""
    quests: dict[str, dict] = {}
    for zone_folder in zone_dirs():
        quests_dir = zone_folder / "quests"
        if not quests_dir.exists():
            continue
        for path in sorted(quests_dir.glob("*.toml")):
            data = load_safe(path)
            if not data:
                continue
            qid = data.get("id", "")
            if qid:
                quests[qid] = data
    return quests


def collect_dialogues() -> set[str]:
    """Return set of npc_ids that have a dialogue tree file."""
    npc_ids: set[str] = set()
    for zone_folder in zone_dirs():
        dialogues_dir = zone_folder / "dialogues"
        if not dialogues_dir.exists():
            continue
        for path in sorted(dialogues_dir.glob("*.toml")):
            npc_ids.add(path.stem)
    return npc_ids


# ── Validation rules ─────────────────────────────────────────────────────────

def validate_rooms(rooms, items, npcs, styles):
    for rid, room in rooms.items():
        for f in ("id", "name", "description"):
            if f not in room:
                err(f"Room '{rid}' missing field '{f}'")
        for direction, exit_val in room.get("exits", {}).items():
            target = exit_val.get("to", "") if isinstance(exit_val, dict) else exit_val
            if target and target not in rooms:
                err(f"Room '{rid}' exit '{direction}' → '{target}' not found")
        for entry in room.get("items", []):
            iid = entry if isinstance(entry, str) else entry.get("id", "")
            if iid and iid not in items:
                warn(f"Room '{rid}' references unknown item '{iid}'")
        hostile_spawn_ids   = set()
        peaceful_spawn_ids  = set()
        for spawn in room.get("spawns", []):
            nid = spawn if isinstance(spawn, str) else spawn.get("id", "")
            if nid and nid not in npcs:
                err(f"Room '{rid}' spawns unknown NPC '{nid}'")
            if not nid or nid not in npcs:
                continue
            # Determine effective hostile flag (per-spawn override beats NPC template)
            if isinstance(spawn, dict) and "hostile" in spawn:
                is_hostile = bool(spawn["hostile"])
            else:
                is_hostile = bool(npcs[nid].get("hostile", False))
            if is_hostile:
                hostile_spawn_ids.add(nid)
            else:
                peaceful_spawn_ids.add(nid)
        # Warn if hostile and non-hostile NPCs share the same room without attacks_npcs
        if hostile_spawn_ids and peaceful_spawn_ids:
            unresolved = [nid for nid in hostile_spawn_ids
                          if not npcs[nid].get("attacks_npcs", False)]
            if unresolved:
                warn(f"Room '{rid}' spawns both hostile and non-hostile NPCs. "
                     f"Set attacks_npcs = true on hostile NPC(s) "
                     f"({', '.join(sorted(unresolved))}) if they should fight, "
                     f"or move them to separate rooms.")


def _check_script_item_refs(script_ops: list, items: dict, context: str) -> None:
    """Recursively check give_item / spawn_item ops reference known item IDs."""
    for op in script_ops:
        if not isinstance(op, dict):
            continue
        name = op.get("op", "")
        if name in ("give_item", "spawn_item"):
            iid = op.get("item_id", "")
            if iid and iid not in items:
                warn(f"{context} script references unknown item '{iid}'")
        # Recurse into branch arrays
        for key in ("then", "else", "on_pass", "on_fail"):
            branch = op.get(key)
            if isinstance(branch, list):
                _check_script_item_refs(branch, items, context)


def validate_items(items):
    for iid, item in items.items():
        for f in ("id", "name"):
            if f not in item:
                err(f"Item '{iid}' missing field '{f}'")
        # Check on_get script item references
        on_get = item.get("on_get", [])
        if on_get:
            _check_script_item_refs(on_get, items, f"Item '{iid}'")
        # Dead-code / contradiction checks
        is_scenery = item.get("scenery", False)
        # scenery + on_get is the valid interactive-scenery pattern (ore nodes, etc.)
        # — engine now runs on_get then stops, so this is intentional; no warning.
        if is_scenery and item.get("slot"):
            warn(f"Item '{iid}' is scenery (unpickable) but has slot='{item['slot']}' — "
                 f"it can never be equipped")
        if item.get("no_drop") and item.get("on_drop"):
            warn(f"Item '{iid}' has no_drop=true but also has on_drop — "
                 f"the script will never run")
        # commands[] sanity
        for cmd in item.get("commands", []):
            if not cmd.get("verb"):
                warn(f"Item '{iid}' has a [[commands]] entry with no verb — "
                     f"it will never be triggered")
        # ── Effect / on_use pattern checks ────────────────────────────────────
        effects = item.get("effects", [])
        has_slot = bool(item.get("slot", ""))
        _VALID_EFFECT_TYPES = {"stat_bonus", "on_use", "on_equip", "on_hit"}
        _VALID_STATS        = {"attack", "defense", "max_hp", "carry_capacity"}
        _KNOWN_SPECIALS     = set()  # only clear_status_<X> is valid; others are bugs
        for eidx, fx in enumerate(effects):
            ftype = fx.get("type", "")
            if ftype not in _VALID_EFFECT_TYPES:
                warn(f"Item '{iid}' effect[{eidx}] has unknown type '{ftype}' — "
                     f"valid types: {sorted(_VALID_EFFECT_TYPES)}")
            if ftype == "stat_bonus":
                stat = fx.get("stat", "")
                if stat not in _VALID_STATS:
                    warn(f"Item '{iid}' stat_bonus has unknown stat '{stat}' — "
                         f"valid: {sorted(_VALID_STATS)}")
            if ftype == "on_equip" and not has_slot:
                warn(f"Item '{iid}' has an on_equip effect but no slot — "
                     f"the item can never be equipped; on_equip will never fire")
            if ftype == "on_use":
                heal    = fx.get("heal", 0)
                special = fx.get("special", "")
                if heal == 0 and not special:
                    warn(f"Item '{iid}' on_use effect has heal=0 and no special — "
                         f"using this item will do nothing")
                if special and not special.startswith("clear_status_"):
                    warn(f"Item '{iid}' on_use has unrecognized special='{special}' — "
                         f"only 'clear_status_<effect>' is handled; this will be silently ignored")
        # on_use script + effects.on_use both present is confusing (script wins)
        if item.get("on_use") and any(fx.get("type") == "on_use" for fx in effects):
            warn(f"Item '{iid}' has both an on_use script array AND an effects on_use — "
                 f"the script array takes priority; effects on_use will be ignored")


_NPC_REQUIRED = ("id", "name", "hp", "max_hp", "attack", "defense",
                  "xp_reward", "gold_reward", "hostile", "tags", "style",
                  "style_prof", "desc_short", "desc_long")

_NPC_KNOWN = set(_NPC_REQUIRED) | {
    "shop", "rest_cost", "dialogue", "dialogue_file", "spawn_chance",
    "respawn", "respawn_time", "kill_script", "gear_affinity",
    "admin_comment", "give_accepts", "round_script",
}


def _collect_dialogue_npc_ids() -> set[str]:
    """Return all npc_ids for which a dialogue TOML file exists."""
    found: set[str] = set()
    for zone_folder in zone_dirs():
        dlg_dir = zone_folder / "dialogues"
        if dlg_dir.exists():
            for path in dlg_dir.glob("*.toml"):
                found.add(path.stem)
    return found


def validate_npcs(npcs, items, styles):
    dialogue_ids = _collect_dialogue_npc_ids()

    for nid, npc in npcs.items():
        # Required fields
        for f in _NPC_REQUIRED:
            if f not in npc:
                err(f"NPC '{nid}' missing required field '{f}'")

        # Unknown / stale fields
        extra = set(npc.keys()) - _NPC_KNOWN
        if extra:
            warn(f"NPC '{nid}' has unrecognised fields: {', '.join(sorted(extra))}")

        # Style reference
        style = npc.get("style", "")
        if style and style not in styles:
            warn(f"NPC '{nid}' references unknown style '{style}'")

        # Shop item IDs
        for shop_entry in npc.get("shop", []):
            iid = shop_entry.get("item_id", "")
            if iid and iid not in items:
                err(f"NPC '{nid}' shop references unknown item '{iid}'")

        # give_accepts item and script references
        for entry in npc.get("give_accepts", []):
            iid = entry.get("item_id", "")
            if iid and iid not in items:
                err(f"NPC '{nid}' give_accepts references unknown item '{iid}'")
            script = entry.get("script", [])
            if script:
                _check_script_item_refs(script, items, f"NPC '{nid}' give_accepts")

        # Dialogue coverage
        is_hostile   = npc.get("hostile", False)
        has_shop     = bool(npc.get("shop"))
        has_dlg_str  = bool(npc.get("dialogue", "").strip())
        dlg_file_field = npc.get("dialogue_file", "")
        if dlg_file_field:
            shared_path = _CURRENT_WORLD / dlg_file_field
            if not shared_path.exists():
                err(f"NPC '{nid}' dialogue_file '{dlg_file_field}' not found")
        has_dlg_file = (nid in dialogue_ids) or bool(dlg_file_field)

        if not has_dlg_file:
            if has_shop:
                warn(f"NPC '{nid}' has a shop but no dialogue tree — "
                     f"players can't access it via 'talk'")
            elif not has_dlg_str:
                if is_hostile:
                    warn(f"NPC '{nid}' (hostile) has no dialogue — "
                         f"engine will generate a brush-off line")
                else:
                    warn(f"NPC '{nid}' has no dialogue tree or fallback string")


def validate_quests(quests, npcs):
    for qid, quest in quests.items():
        giver = quest.get("giver", "")
        if giver and giver not in npcs:
            warn(f"Quest '{qid}' giver NPC '{giver}' not found")


def _collect_all_quest_ops() -> dict[str, set]:
    """
    Scan every script source in the world for advance_quest / complete_quest ops.
    Returns dict: quest_id → set of step numbers that have a trigger (None = complete_quest).
    """
    triggered: dict[str, set] = {}

    def _scan(ops: list) -> None:
        if not isinstance(ops, list):
            return
        for op in ops:
            if not isinstance(op, dict):
                continue
            name = op.get("op", "")
            qid  = op.get("quest_id", "")
            if qid:
                if name == "advance_quest":
                    triggered.setdefault(qid, set()).add(int(op.get("step", 0)))
                elif name == "complete_quest":
                    triggered.setdefault(qid, set()).add(None)
            # Always recurse into branch arrays (do NOT skip on missing qid)
            for key in ("then", "else", "on_pass", "on_fail"):
                branch = op.get(key)
                if isinstance(branch, list):
                    _scan(branch)

    for zone_folder in zone_dirs():
        # NPC kill_scripts
        for path in sorted(zone_folder.glob("*.toml")):
            data = load_safe(path)
            if not data:
                continue
            for npc in data.get("npc", []):
                _scan(npc.get("kill_script", []))
                for entry in npc.get("give_accepts", []):
                    _scan(entry.get("script", []))
            # Item on_get and on_use
            for item in data.get("item", []):
                _scan(item.get("on_get", []))
                _scan(item.get("on_use", []))
            # Room scripts
            for room in data.get("room", []):
                _scan(room.get("on_enter", []))
                _scan(room.get("on_exit",  []))
                _scan(room.get("on_sleep", []))
                _scan(room.get("on_wake",  []))

        # Dialogue scripts
        dlg_dir = zone_folder / "dialogues"
        if dlg_dir.exists():
            for path in sorted(dlg_dir.glob("*.toml")):
                data = load_safe(path)
                if not data:
                    continue
                for node in data.get("node", []):
                    _scan(node.get("script", []))
                for resp in data.get("response", []):
                    _scan(resp.get("script", []))

        # Crafting on_complete
        crafting_dir = zone_folder / "crafting"
        if crafting_dir.exists():
            for path in sorted(crafting_dir.glob("*.toml")):
                data = load_safe(path)
                if not data:
                    continue
                for c in data.get("commission", []):
                    _scan(c.get("on_complete", []))

    return triggered


def validate_orphaned_tables() -> None:
    """
    Detect top-level array-table entries that are syntactically valid TOML but are
    NEVER read by the engine because they appear as free-floating top-level arrays
    instead of inline fields on their parent record.

    Common mistakes and the correct inline form:

      Zone / NPC files:
        [[kill_script]]  →  kill_script  = [{op = "...", ...}]  on the [[npc]]
        [[round_script]] →  round_script = [{...}]              on the [[npc]]
        [[script]]       →  script       = [{...}]              on the [[npc]]
        [[give_accepts]] →  give_accepts = [{item_id = "..."}]  on the [[npc]]

      Zone / Room files:
        [[on_enter]]     →  on_enter = [{op = "...", ...}]      on the [[room]]
        [[on_exit]]      →  on_exit  = [{...}]                  on the [[room]]
        [[on_sleep]]     →  on_sleep = [{...}]                  on the [[room]]
        [[on_wake]]      →  on_wake  = [{...}]                  on the [[room]]

      Item files:
        [[effects]]      →  effects  = [{type = "...", ...}]    on the [[item]]
        [[on_get]]       →  on_get   = [{op = "...", ...}]      on the [[item]]
        [[on_drop]]      →  on_drop  = [{...}]                  on the [[item]]

      Dialogue files:
        [[script]]       →  script = [{...}]                    on the [[node]]

      Crafting files:
        [[quality]]      →  quality = [{...}]                   on the [[commission]]

    The engine loads records by iterating data.get("npc", []) etc., so any
    keys at the top-level dict are completely ignored at runtime.
    """
    for zone_folder in zone_dirs():
        # ── Zone files (npcs, items, rooms) ──────────────────────────────────
        for path in sorted(zone_folder.glob("*.toml")):
            data = load_safe(path)
            if not data:
                continue
            rel = path.relative_to(DATA_DIR.parent)
            # NPC-related orphans (script hooks that belong inline on [[npc]])
            for bad_key in ("kill_script", "round_script", "script", "give_accepts"):
                if bad_key in data and isinstance(data[bad_key], list):
                    err(
                        f"{rel}: top-level [[{bad_key}]] entries found "
                        f"({len(data[bad_key])} entry(s)). These are NEVER used by the engine. "
                        f"Move them inline onto the preceding [[npc]] as "
                        f"{bad_key} = [{{op = \"...\", ...}}]"
                    )
            # Room hook orphans (belong inline on [[room]])
            for bad_key in ("on_enter", "on_exit", "on_sleep", "on_wake"):
                if bad_key in data and isinstance(data[bad_key], list):
                    err(
                        f"{rel}: top-level [[{bad_key}]] entries found "
                        f"({len(data[bad_key])} entry(s)). These are NEVER run by the engine. "
                        f"Move them inline onto the preceding [[room]] as "
                        f"{bad_key} = [{{op = \"...\", ...}}]"
                    )
            # Item hook / effect orphans (belong inline on [[item]])
            for bad_key in ("effects", "on_get", "on_drop"):
                if bad_key in data and isinstance(data[bad_key], list):
                    parent = "[[item]]"
                    hint = (
                        f"effects = [{{type = \"...\", ...}}]" if bad_key == "effects"
                        else f"{bad_key} = [{{op = \"...\", ...}}]"
                    )
                    err(
                        f"{rel}: top-level [[{bad_key}]] entries found "
                        f"({len(data[bad_key])} entry(s)). These are NEVER applied by the engine. "
                        f"Move them inline onto the preceding {parent} as {hint}"
                    )

        # ── Dialogue files ────────────────────────────────────────────────────
        dlg_dir = zone_folder / "dialogues"
        if dlg_dir.exists():
            for path in sorted(dlg_dir.glob("*.toml")):
                data = load_safe(path)
                if not data:
                    continue
                rel = path.relative_to(DATA_DIR.parent)
                if "script" in data and isinstance(data["script"], list):
                    err(
                        f"{rel}: top-level [[script]] entries found "
                        f"({len(data['script'])} entry(s)). These are NEVER executed by the engine. "
                        f"Move them inline onto the [[node]] as "
                        f"script = [{{op = \"...\", ...}}]"
                    )

        # ── Crafting files ────────────────────────────────────────────────────
        crafting_dir = zone_folder / "crafting"
        if crafting_dir.exists():
            for path in sorted(crafting_dir.glob("*.toml")):
                data = load_safe(path)
                if not data:
                    continue
                rel = path.relative_to(DATA_DIR.parent)
                if "quality" in data and isinstance(data["quality"], list):
                    err(
                        f"{rel}: top-level [[quality]] entries found "
                        f"({len(data['quality'])} entry(s)). These are NEVER read by the engine. "
                        f"Move them inline onto the [[commission]] as "
                        f"quality = [{{label = \"...\", ...}}]"
                    )


def validate_duplicate_keys() -> None:
    """
    Detect duplicate keys within the same [[record]] block. The custom TOML
    parser silently overwrites the earlier value (last write wins), so both
    definitions appear syntactically valid but only the second takes effect.

    Example (rooms.toml):
        [[room]]
        id       = "my_room"
        on_enter = [{ op = "set_flag", flag = "visited" }]   ← silently discarded
        on_enter = [{ op = "advance_quest", quest_id = "q" }] ← this one wins

    This scanner works on raw text. Only lines with no leading whitespace are
    considered direct record fields — nested content inside inline arrays or
    tables is always indented, so it is never incorrectly flagged.
    """
    import re

    # Matches the start of an array-of-tables or plain-table header (any indent).
    HDR   = re.compile(r'^\s*\[\[?')
    # Matches a key = value at column 0 (no leading whitespace).
    # Direct fields of [[record]] blocks are always unindented in our files.
    KEYLN = re.compile(r'^(\w[\w_]*)\s*=')

    all_paths: list[Path] = []
    for zone_folder in zone_dirs():
        all_paths += list(zone_folder.glob("*.toml"))
        dlg = zone_folder / "dialogues"
        if dlg.exists():
            all_paths += list(dlg.glob("*.toml"))
        craft = zone_folder / "crafting"
        if craft.exists():
            all_paths += list(craft.glob("*.toml"))
        quests_dir = zone_folder / "quests"
        if quests_dir.exists():
            all_paths += list(quests_dir.glob("*.toml"))

    for path in sorted(all_paths):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        except Exception as e:
            warn(f"{path}: could not read — {e}")
            continue
        rel = path.relative_to(DATA_DIR.parent)

        seen: dict[str, int] = {}   # key → line number of first occurrence

        for lineno, raw in enumerate(lines, 1):
            # Skip blank lines and full-line comments
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue

            # A [[...]] or [...] header starts a new record context
            if HDR.match(raw):
                seen = {}
                continue

            # Only consider lines starting at column 0 (direct record fields).
            # Nested content inside inline arrays/tables is always indented.
            m = KEYLN.match(raw)
            if not m:
                continue
            key = m.group(1)

            if key in seen:
                err(
                    f"{rel}:{lineno}: duplicate key '{key}' in same record block "
                    f"(first at line {seen[key]}). The parser uses the LAST value — "
                    f"the earlier definition is silently discarded."
                )
            else:
                seen[key] = lineno


def validate_quest_triggers(quests: dict) -> None:
    """Error if any quest step or completion has no advance_quest / complete_quest trigger found."""
    triggered = _collect_all_quest_ops()
    for qid, quest in quests.items():
        steps = quest.get("step", [])
        # Check start_quest exists somewhere (informational warn only)
        for s in steps:
            step_num = s.get("index")
            if step_num is None:
                continue
            step_triggers = triggered.get(qid, set())
            if step_num not in step_triggers:
                err(f"Quest '{qid}' step {step_num} has no advance_quest trigger "
                    f"found in any script, kill_script, on_get, or on_enter")
        # Check complete_quest exists
        if steps:
            if None not in triggered.get(qid, set()):
                err(f"Quest '{qid}' has no complete_quest trigger found anywhere")


def validate_companions() -> None:
    """Check companion definition files for required fields and logical consistency."""
    import sys
    sys.path.insert(0, ".")
    from engine.companion import all_companion_ids, load_def
    _ensure_cache_clear()
    required = ("id", "name", "type", "desc_short",
                "attack", "defense", "hp", "max_hp",
                "carry_bonus", "style", "style_prof", "restrictions",
                "join_message", "wait_message", "rejoin_message")
    valid_types = {"narrative", "utility", "combat"}
    ids = all_companion_ids()
    if not ids:
        return
    print(f"Loaded: {len(ids)} companion(s)")
    for cid in ids:
        cdef = load_def(cid)
        if not cdef:
            err(f"Companion '{cid}' failed to load")
            continue
        for f in required:
            if f not in cdef:
                err(f"Companion '{cid}' missing field '{f}'")
        ctype = cdef.get("type", "")
        if ctype not in valid_types:
            err(f"Companion '{cid}' has unknown type '{ctype}'")
        if ctype == "combat" and not int(cdef.get("max_hp", 0)):
            warn(f"Companion '{cid}' is combat type but max_hp is 0")
        if ctype == "utility" and not int(cdef.get("carry_bonus", 0)):
            warn(f"Companion '{cid}' is utility type but carry_bonus is 0")


def _ensure_cache_clear():
    """Clear module caches so validation always reads fresh from disk."""
    import importlib, sys
    for mod_name in list(sys.modules.keys()):
        if "companion" in mod_name or "crafting" in mod_name:
            importlib.reload(sys.modules[mod_name])


def validate_zone_services(zones_with_rooms):
    """Warn if any zone that has rooms is missing a banker NPC or town-flagged room."""
    for zone_folder in zone_dirs():
        zone_id = zone_folder.name
        if zone_id not in zones_with_rooms:
            continue   # no rooms in this folder, skip

        has_banker = False
        has_town   = False

        for path in sorted(zone_folder.glob("*.toml")):
            data = load_safe(path)
            if not data:
                continue
            for npc in data.get("npc", []):
                if "banker" in npc.get("tags", []):
                    has_banker = True
            for room in data.get("room", []):
                if "town" in room.get("flags", []):
                    has_town = True

        if not has_banker:
            warn(f"Zone '{zone_id}' has no banker NPC — players who die here "
                 f"won't have a bank branch")
        if not has_town:
            warn(f"Zone '{zone_id}' has no room with 'town' flag — "
                 f"players cannot auto-bind here")


def collect_commissions() -> list[dict]:
    """Collect all [[commission]] entries from crafting/ subdirectories."""
    commissions = []
    for zone_folder in zone_dirs():
        crafting_dir = zone_folder / "crafting"
        if not crafting_dir.exists():
            continue
        for path in sorted(crafting_dir.glob("*.toml")):
            data = load_safe(path)
            if data:
                for c in data.get("commission", []):
                    commissions.append({**c, "_file": str(path)})
    return commissions


def validate_commissions(commissions: list[dict], items: dict, npcs: dict) -> None:
    """Check commission definitions for referential integrity."""
    seen_ids: set[str] = set()
    for c in commissions:
        cid   = c.get("id", "")
        npcid = c.get("npc_id", "")
        fname = c.get("_file", "?")

        if not cid:
            err(f"Commission in {fname}: missing 'id'")
            continue
        if cid in seen_ids:
            err(f"Commission '{cid}' in {fname}: duplicate id")
        seen_ids.add(cid)

        # NPC must exist
        if npcid and npcid not in npcs:
            err(f"Commission '{cid}': npc_id '{npcid}' not found")

        # All materials must be known item IDs
        for mat in c.get("materials", []):
            if mat not in items:
                err(f"Commission '{cid}': material '{mat}' is not a known item id")

        # Must have at least one quality tier
        if not c.get("qualities") and not c.get("quality"):
            warn(f"Commission '{cid}': no [[quality]] tiers defined")

        # Slot must be valid (uses the active world's EQUIPMENT_SLOTS)
        slot = c.get("slot", "")
        valid_slots = set(wc.EQUIPMENT_SLOTS) | {""}
        if slot not in valid_slots:
            err(f"Commission '{cid}': unknown slot '{slot}' "
                f"(valid: {sorted(wc.EQUIPMENT_SLOTS)})")


# ── TOML syntax validation ────────────────────────────────────────────────────

def validate_toml_syntax() -> None:
    """
    Parse every .toml file with Python stdlib tomllib (strict spec-compliant
    parser) and print a full file inventory: absolute path, byte size,
    modification time, and any parse errors.

    This makes it easy to confirm which files the validator is actually reading
    and whether they match what you expect on disk.
    """
    try:
        import tomllib
    except ModuleNotFoundError:
        try:
            import tomli as tomllib  # pip install tomli
        except ModuleNotFoundError:
            print("  TOML syntax check skipped — requires tomllib (Python 3.11+) or tomli (pip install tomli)")
            return
    import os
    import datetime
    import re as _re

    toml_files: list[Path] = []
    for zone_folder in zone_dirs():
        toml_files.extend(sorted(zone_folder.rglob("*.toml")))
    # Also pick up any .toml files at the world root (not inside zone folders)
    world_root = _CURRENT_WORLD if _CURRENT_WORLD else DATA_DIR
    toml_files.extend(sorted(world_root.glob("*.toml")))
    toml_files = sorted(set(toml_files))

    print()
    print("-- TOML File Inventory " + "-" * 45)
    print(f"  {'FILE':<52}  {'BYTES':>6}  {'MODIFIED':<19}  STATUS")
    print(f"  {'-'*52}  {'-'*6}  {'-'*19}  ------")

    bad = 0
    for path in toml_files:
        stat    = path.stat()
        size    = stat.st_size
        mtime   = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        rel     = str(path.relative_to(DATA_DIR.parent))

        try:
            with open(path, "rb") as f:
                tomllib.load(f)
            status = "ok"
        except tomllib.TOMLDecodeError as e:
            rel_path = path.relative_to(DATA_DIR.parent)
            raw_text = path.read_text(encoding="utf-8", errors="replace")
            # Multi-line inline tables: engine extension, not a real bug
            if _re.search(r"\{[^}]*\n\s+\w", raw_text):
                warn(f"Multi-line inline table in {rel_path} (engine extension — OK)")
                status = "ext"
            else:
                err(f"TOML syntax error in {rel_path}: {e}")
                bad += 1
                status = f"ERR: {e}"
        except Exception as e:
            err(f"Could not read {path.relative_to(DATA_DIR.parent)}: {e}")
            bad += 1
            status = f"ERR: {e}"

        # Truncate long status for table display
        status_display = status if len(status) <= 40 else status[:37] + "..."
        # Truncate long paths
        rel_display = rel if len(rel) <= 52 else "..." + rel[-49:]
        print(f"  {rel_display:<52}  {size:>6}  {mtime}  {status_display}")

    print()
    count = len(toml_files)
    if bad:
        print(f"TOML syntax: {bad} file(s) with errors out of {count}")
    else:
        print(f"TOML syntax: {count} file(s) checked - all valid")


# ── Dialogue integrity validation ─────────────────────────────────────────────

def validate_dialogues(known_npcs: dict) -> None:
    """
    Check every dialogue TOML file for structural correctness and print a
    per-file report showing absolute path, node count, response count, and
    any problems found.
    """
    import re

    print("-- Dialogue File Report " + "-" * 44)
    print(f"  {'FILE':<42}  {'NPC IN WORLD':^12}  {'NODES':>5}  {'RESP':>5}  ISSUES")
    print(f"  {'-'*42}  {'-'*12}  {'-'*5}  {'-'*5}  ------")

    dialogue_count = 0
    for zone_folder in zone_dirs():
        dlg_dir = zone_folder / "dialogues"
        if not dlg_dir.exists():
            continue
        for path in sorted(dlg_dir.glob("*.toml")):
            npc_id = path.stem
            dialogue_count += 1
            issues: list[str] = []

            # ── 1. Check NPC exists in world ───────────────────────────────
            npc_known = npc_id in known_npcs
            if not npc_known:
                issues.append("no matching NPC id in world")
                warn(f"Dialogue '{path.name}' has no matching NPC id '{npc_id}' in any zone")

            # ── 2. Detect [[node.response]] usage in raw text ──────────────
            raw_text = path.read_text(encoding="utf-8", errors="replace")
            nested_count = raw_text.count("[[node.response]]")
            if nested_count:
                issues.append(f"[[node.response]] x{nested_count} — engine ignores these")
                err(f"Dialogue '{path.name}': {nested_count} response(s) use "
                    f"[[node.response]] — engine ignores nested format; "
                    f"convert to [[response]] with node= field")

            # ── 3. Parse via engine loader ──────────────────────────────────
            raw = load_safe(path)
            if raw is None:
                issues.append("parse error (see above)")
                _print_dialogue_row(path, npc_id, npc_known, 0, 0, issues)
                continue

            # ── 4. Build node map ───────────────────────────────────────────
            nodes = {n.get("id",""): n
                     for n in raw.get("node", []) if n.get("id")}
            flat_responses = raw.get("response", [])

            if not nodes:
                issues.append("no [[node]] entries found")
                warn(f"Dialogue '{path.name}': no [[node]] entries found")
                _print_dialogue_row(path, npc_id, npc_known, 0, 0, issues)
                continue

            if "root" not in nodes:
                issues.append("missing root node")
                err(f"Dialogue '{path.name}': missing required 'root' node")

            # ── 5. Attach flat [[response]] entries ─────────────────────────
            for resp in flat_responses:
                parent_id = resp.get("node", "")
                if parent_id in nodes:
                    nodes[parent_id].setdefault("response", []).append(resp)
                elif parent_id:
                    issues.append(f"response→unknown parent '{parent_id}'")
                    err(f"Dialogue '{path.name}': response targets unknown parent "
                        f"node '{parent_id}'")

            total_responses = sum(len(n.get("response", [])) for n in nodes.values())

            # ── 6. Validate all next= references ────────────────────────────
            all_node_ids = set(nodes.keys())
            for node_id, node in nodes.items():
                for resp in node.get("response", []):
                    nxt = resp.get("next", "")
                    if nxt and nxt not in all_node_ids:
                        issues.append(f"next='{nxt}' not found")
                        err(f"Dialogue '{path.name}' node '{node_id}': "
                            f"response next='{nxt}' references unknown node")
                nxt = node.get("next", "")
                if nxt and nxt not in all_node_ids:
                    issues.append(f"node next='{nxt}' not found")
                    err(f"Dialogue '{path.name}' node '{node_id}': "
                        f"next='{nxt}' references unknown node")

            # ── 7. Root has no responses (tree will show no choices) ─────────
            root_responses = nodes.get("root", {}).get("response", [])
            if "root" in nodes and not root_responses and not nodes["root"].get("next"):
                issues.append("root node has 0 responses — talk will show no choices")
                warn(f"Dialogue '{path.name}': root node has no responses and no "
                     f"next= — talk command will display no choices")

            # ── 8. Orphan detection ─────────────────────────────────────────
            reachable: set[str] = set()
            def _walk(node_id: str) -> None:
                if node_id in reachable or node_id not in nodes:
                    return
                reachable.add(node_id)
                node = nodes[node_id]
                for resp in node.get("response", []):
                    nxt = resp.get("next", "")
                    if nxt:
                        _walk(nxt)
                nxt = node.get("next", "")
                if nxt:
                    _walk(nxt)

            _walk("root")
            orphans = all_node_ids - reachable - {"root"}
            for o in sorted(orphans):
                issues.append(f"orphan node '{o}'")
                warn(f"Dialogue '{path.name}': node '{o}' is unreachable from root")

            _print_dialogue_row(path, npc_id, npc_known, len(nodes),
                                total_responses, issues)

    print()
    print(f"  {dialogue_count} dialogue file(s) checked")


def _print_dialogue_row(path: Path, npc_id: str, npc_known: bool,
                        node_count: int, resp_count: int,
                        issues: list[str]) -> None:
    """Print one row of the dialogue report table."""
    rel       = str(path.relative_to(DATA_DIR.parent))
    rel_short = rel if len(rel) <= 42 else "..." + rel[-39:]
    npc_col   = "found" if npc_known else "MISSING"
    issues_str = "; ".join(issues) if issues else "-"
    if len(issues_str) > 60:
        issues_str = issues_str[:57] + "..."
    print(f"  {rel_short:<42}  {npc_col:^12}  {node_count:>5}  {resp_count:>5}  {issues_str}")


def validate_world_config() -> None:
    """Load and validate the active world's config.toml (or legacy config.py)."""
    world_path = _CURRENT_WORLD or DATA_DIR

    # Prefer config.toml; fall back to legacy config.py.
    toml_path = world_path / "config.toml"
    py_path   = world_path / "config.py"

    if toml_path.exists():
        _validate_world_config_toml(toml_path)
    elif py_path.exists():
        _validate_world_config_py(py_path)
    else:
        warn(f"{world_path.name}/config.toml not found — engine will use built-in defaults")


def _validate_world_config_toml(config_path: Path) -> None:
    """Validate a config.toml world config file."""
    cfg_label = str(config_path.relative_to(DATA_DIR.parent))
    try:
        cfg = toml_load(config_path)
    except Exception as e:
        err(f"{cfg_label}: failed to parse — {e}")
        return

    if not str(cfg.get("world_name", "")).strip():
        warn(f"{cfg_label}: world_name is not set")

    skills = cfg.get("skills")
    if not skills:
        warn(f"{cfg_label}: [skills] is empty or missing — "
             "engine default (perception) will be available")
    elif not isinstance(skills, dict):
        err(f"{cfg_label}: [skills] must be a table mapping skill_id to display name")
    else:
        for k, v in skills.items():
            if not isinstance(k, str) or not isinstance(v, str):
                err(f"{cfg_label}: skills entry {k!r}: {v!r} — both must be strings")

    hp = cfg.get("new_char_hp")
    if hp is not None and (not isinstance(hp, int) or hp <= 0):
        err(f"{cfg_label}: new_char_hp must be a positive integer (got {hp!r})")

    vt = cfg.get("vision_threshold")
    if vt is not None and (not isinstance(vt, int) or vt < 0):
        err(f"{cfg_label}: vision_threshold must be a non-negative integer (got {vt!r})")

    currency = cfg.get("currency_name")
    if currency is not None and not isinstance(currency, str):
        err(f"{cfg_label}: currency_name must be a string (got {currency!r})")

    style = cfg.get("default_style")
    if style is not None and not isinstance(style, str):
        err(f"{cfg_label}: default_style must be a string (got {style!r})")

    slots = cfg.get("equipment_slots")
    if slots is not None:
        if not isinstance(slots, list) or not slots:
            err(f"{cfg_label}: equipment_slots must be a non-empty list of strings")
        else:
            for s in slots:
                if not isinstance(s, str):
                    err(f"{cfg_label}: equipment_slots entry {s!r} must be a string")

    attrs = cfg.get("player_attrs", [])
    if not isinstance(attrs, list):
        err(f"{cfg_label}: player_attrs must be an array of tables ([[player_attrs]])")
    else:
        for i, a in enumerate(attrs):
            if not isinstance(a, dict):
                err(f"{cfg_label}: player_attrs[{i}] must be a table")
                continue
            if not a.get("id"):
                err(f"{cfg_label}: player_attrs[{i}] missing required field 'id'")
            disp = a.get("display", "number")
            if disp not in ("number", "bar"):
                warn(f"{cfg_label}: player_attrs[{i}] display={disp!r} unknown; "
                     "use 'number' or 'bar'")


def _validate_world_config_py(config_path: Path) -> None:
    """Validate a legacy config.py world config file (backward compat)."""
    import importlib.util
    cfg_label = str(config_path.relative_to(DATA_DIR.parent))
    spec = importlib.util.spec_from_file_location("_val_world_cfg", config_path)
    mod  = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        err(f"{cfg_label}: failed to load — {e}")
        return

    warn(f"{cfg_label}: legacy config.py detected — consider migrating to config.toml")

    if not getattr(mod, "WORLD_NAME", "").strip():
        warn(f"{cfg_label}: WORLD_NAME is not set")

    skills = getattr(mod, "SKILLS", None)
    if not skills:
        warn(f"{cfg_label}: SKILLS is empty or missing — "
             "engine default (perception) will be available")
    elif not isinstance(skills, dict):
        err(f"{cfg_label}: SKILLS must be a dict mapping skill_id to display name")
    else:
        for k, v in skills.items():
            if not isinstance(k, str) or not isinstance(v, str):
                err(f"{cfg_label}: SKILLS entry {k!r}: {v!r} — both must be strings")

    hp = getattr(mod, "NEW_CHAR_HP", None)
    if hp is not None and (not isinstance(hp, int) or hp <= 0):
        err(f"{cfg_label}: NEW_CHAR_HP must be a positive integer (got {hp!r})")

    currency = getattr(mod, "CURRENCY_NAME", None)
    if currency is not None and not isinstance(currency, str):
        err(f"{cfg_label}: CURRENCY_NAME must be a string (got {currency!r})")

    style = getattr(mod, "DEFAULT_STYLE", None)
    if style is not None and not isinstance(style, str):
        err(f"{cfg_label}: DEFAULT_STYLE must be a string (got {style!r})")

    slots = getattr(mod, "EQUIPMENT_SLOTS", None)
    if slots is not None:
        if not isinstance(slots, (list, tuple)) or not slots:
            err(f"{cfg_label}: EQUIPMENT_SLOTS must be a non-empty list/tuple of strings")
        else:
            for s in slots:
                if not isinstance(s, str):
                    err(f"{cfg_label}: EQUIPMENT_SLOTS entry {s!r} must be a string")


def _validate_world(world_path: Path) -> None:
    """Run all validation checks for one world folder."""
    global _CURRENT_WORLD
    _CURRENT_WORLD = world_path
    wc.init(world_path)   # load EQUIPMENT_SLOTS and other tunables for this world

    print("-- World Config " + "-" * 52)
    validate_world_config()
    print()

    rooms     = collect_rooms()
    items, _  = collect_typed("item")
    npcs,  _  = collect_typed("npc")
    styles    = collect_styles()
    quests    = collect_quests()

    print(f"Loaded: {len(rooms)} rooms, {len(items)} items, {len(npcs)} NPCs, "
          f"{len(styles)} styles, {len(quests)} quests")
    print()

    # Core cross-reference checks run via the shared engine module.
    # Both validate.py (here) and the WCT error panel use the same logic.
    for issue in _run_core_checks(world_path):
        if issue["sev"] == "err":
            err(issue["msg"])
        else:
            warn(issue["msg"])

    validate_orphaned_tables()
    validate_duplicate_keys()
    validate_quest_triggers(quests)

    # Collect zone ids that actually have rooms
    zones_with_rooms = set()
    for zone_folder in zone_dirs():
        for path in sorted(zone_folder.glob("*.toml")):
            data = load_safe(path)
            if data and data.get("room"):
                zones_with_rooms.add(zone_folder.name)
                break
    validate_zone_services(zones_with_rooms)

    commissions = collect_commissions()
    print(f"Loaded: {len(commissions)} commission(s) across all crafters")
    validate_commissions(commissions, items, npcs)

    validate_companions()
    validate_toml_syntax()
    validate_dialogues(npcs)
    print()

    starts = [r for r in rooms.values() if r.get("start")]
    if not starts:
        warn("No room marked 'start = true'")
    elif len(starts) > 1:
        err(f"Multiple rooms marked 'start = true': {[r['id'] for r in starts]}")


def main():
    parser = argparse.ArgumentParser(description="Delve data validator.")
    parser.add_argument(
        "--world", metavar="WORLD_ID",
        help="Validate only this world folder (e.g. first_world). "
             "Omit to validate all worlds.",
    )
    args = parser.parse_args()

    print("Delve Data Validator")
    print("=" * 40)

    worlds = world_dirs()
    if not worlds:
        print("[WARN]  No world folders found in data/ "
              "(no subfolders with config.toml or config.py)")
        sys.exit(1)

    # Filter to a single world if --world was given.
    if args.world:
        target = DATA_DIR / args.world
        if target not in worlds:
            print(f"[ERROR] World '{args.world}' not found in data/ "
                  f"(no config.toml or config.py there)")
            sys.exit(1)
        worlds = [target]

    total_errors   = 0
    total_warnings = 0

    for world_path in worlds:
        errors.clear()
        warnings.clear()
        print(f"\n== World: {world_path.name} " + "=" * 48)
        _validate_world(world_path)

        if warnings:
            print("Warnings:")
            for w in warnings: print(w)
            print()
        if errors:
            print("Errors:")
            for e in errors: print(e)
            print()
            print(f"  FAILED: {len(errors)} error(s), {len(warnings)} warning(s).")
        else:
            print(f"  PASSED  ({len(warnings)} warning(s))")

        total_errors   += len(errors)
        total_warnings += len(warnings)

    print()
    print("=" * 40)
    if total_errors:
        print(f"FAILED: {total_errors} error(s), {total_warnings} warning(s) across {len(worlds)} world(s).")
        sys.exit(1)
    else:
        print(f"PASSED  ({total_warnings} warning(s) across {len(worlds)} world(s))")


if __name__ == "__main__":
    main()




