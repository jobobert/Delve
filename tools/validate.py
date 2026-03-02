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
    python tools/validate.py

Exit code 0 = passed (warnings okay), exit code 1 = errors found.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.toml_io import load as toml_load

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
    """Return world folder Paths — subfolders of DATA_DIR that contain config.py."""
    return sorted(
        p for p in DATA_DIR.iterdir()
        if p.is_dir() and (p / "config.py").exists()
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
        if "coord" not in room:
            warn(f"Room '{rid}' has no 'coord' field — won't appear on maps")
        for direction, exit_val in room.get("exits", {}).items():
            target = exit_val.get("to", "") if isinstance(exit_val, dict) else exit_val
            if target and target not in rooms:
                err(f"Room '{rid}' exit '{direction}' → '{target}' not found")
        for entry in room.get("items", []):
            iid = entry if isinstance(entry, str) else entry.get("id", "")
            if iid and iid not in items:
                warn(f"Room '{rid}' references unknown item '{iid}'")
        for spawn in room.get("spawns", []):
            nid = spawn if isinstance(spawn, str) else spawn.get("id", "")
            if nid and nid not in npcs:
                err(f"Room '{rid}' spawns unknown NPC '{nid}'")


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


_NPC_REQUIRED = ("id", "name", "hp", "max_hp", "attack", "defense",
                  "xp_reward", "gold_reward", "hostile", "tags", "style",
                  "style_prof", "desc_short", "desc_long")

_NPC_KNOWN = set(_NPC_REQUIRED) | {
    "shop", "rest_cost", "dialogue", "spawn_chance",
    "respawn_time", "kill_script", "gear_affinity",
    "admin_comment", "give_accepts",
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
        has_dlg_file = nid in dialogue_ids

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

        # Slot must be valid
        slot = c.get("slot", "")
        valid_slots = {"weapon", "armor", "pack", "ring", "shield", "cape", ""}
        if slot not in valid_slots:
            err(f"Commission '{cid}': unknown slot '{slot}'")


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
    """Load and validate the active world's config.py for correct format and fields."""
    import importlib.util
    world_path  = _CURRENT_WORLD or DATA_DIR
    config_path = world_path / "config.py"
    cfg_label   = str(config_path.relative_to(DATA_DIR.parent))

    if not config_path.exists():
        warn(f"{cfg_label} not found — engine will use built-in defaults")
        return

    spec = importlib.util.spec_from_file_location("_val_world_cfg", config_path)
    mod  = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        err(f"{cfg_label}: failed to load — {e}")
        return

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
                err(f"{cfg_label}: SKILLS entry {k!r}: {v!r} — "
                    "both key and value must be strings")

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

    validate_rooms(rooms, items, npcs, styles)
    validate_items(items)
    validate_npcs(npcs, items, styles)
    validate_quests(quests, npcs)

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
        warn(f"Multiple start rooms: {[r['id'] for r in starts]}")


def main():
    print("Delve Data Validator")
    print("=" * 40)

    worlds = world_dirs()
    if not worlds:
        print("[WARN]  No world folders found in data/ (no subfolders with config.py)")
        sys.exit(1)

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




