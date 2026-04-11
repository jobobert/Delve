"""
engine/validate_world.py — Shared validation core for Delve.

Returns structured issue dicts consumable by both tools/validate.py (CLI)
and the WCT server (/api/validate_issues JSON endpoint).

Issue dict format:
    {
        "sev":     "err" | "warn",
        "msg":     str,       # human-readable message
        "fix":     str,       # suggested fix (may be "")
        "type":    str,       # "room" | "npc" | "item" | "quest" | "dialogue"
        "zone_id": str,
        "id":      str,       # entity id within the zone
    }

Usage:
    from engine.validate_world import validate_world
    issues = validate_world(Path("data/first_world"))
    for issue in issues:
        print(issue["sev"], issue["msg"])
"""

from __future__ import annotations
from pathlib import Path

from engine.toml_io import load as toml_load
import engine.world_config as wc

_SKIP_DIRS = {"zone_state", "players", "__pycache__"}

_NPC_REQUIRED = (
    "id", "name", "hp", "max_hp", "attack", "defense",
    "xp_reward", "gold_reward", "hostile", "tags", "style",
    "style_prof", "desc_short", "desc_long",
)


# ── Issue helpers ─────────────────────────────────────────────────────────────

def _issue(sev: str, msg: str, fix: str, kind: str, zone_id: str, eid: str) -> dict:
    return {"sev": sev, "msg": msg, "fix": fix, "type": kind, "zone_id": zone_id, "id": eid}


def _err(msg: str, fix: str, kind: str, zone_id: str, eid: str) -> dict:
    return _issue("err", msg, fix, kind, zone_id, eid)


def _warn(msg: str, fix: str, kind: str, zone_id: str, eid: str) -> dict:
    return _issue("warn", msg, fix, kind, zone_id, eid)


# ── Script helpers ────────────────────────────────────────────────────────────

def _script_item_issues(
    ops: list, all_item_ids: set[str], context: str, kind: str, zone_id: str, eid: str
) -> list[dict]:
    """Recursively find give_item/spawn_item ops that reference unknown items."""
    out: list[dict] = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        name = op.get("op", "")
        if name in ("give_item", "spawn_item"):
            iid = op.get("item_id", "")
            if iid and iid not in all_item_ids:
                out.append(_warn(
                    f"{context} script references unknown item '{iid}'",
                    "Check the item_id", kind, zone_id, eid,
                ))
        for key in ("then", "else", "on_pass", "on_fail"):
            branch = op.get(key)
            if isinstance(branch, list):
                out.extend(_script_item_issues(branch, all_item_ids, context, kind, zone_id, eid))
    return out


# ── Per-entity checks ─────────────────────────────────────────────────────────

def _check_rooms(
    zone_id: str,
    rooms: list,
    all_room_ids: set[str],
    all_npc_ids: set[str],
    all_item_ids: set[str],
) -> list[dict]:
    issues: list[dict] = []
    for room in rooms:
        rid = room.get("id") or "(no id)"
        if not room.get("id"):
            issues.append(_err("Room missing 'id'", 'Add id = "..."', "room", zone_id, rid))
        if not room.get("name"):
            issues.append(_err(f"Room '{rid}' missing 'name'", 'Add name = "..."', "room", zone_id, rid))
        if not room.get("description"):
            issues.append(_warn(f"Room '{rid}' missing 'description'", 'Add description = "..."', "room", zone_id, rid))
        for direction, ev in (room.get("exits") or {}).items():
            dest = ev.get("to", "") if isinstance(ev, dict) else (ev or "")
            if dest and dest not in all_room_ids:
                issues.append(_err(
                    f"Room '{rid}' exit '{direction}' -> '{dest}' not found",
                    "Check the exit target id", "room", zone_id, rid,
                ))
        for entry in (room.get("items") or []):
            iid = entry if isinstance(entry, str) else (entry.get("id", "") if isinstance(entry, dict) else "")
            if iid and iid not in all_item_ids:
                issues.append(_warn(
                    f"Room '{rid}' references unknown item '{iid}'",
                    "Check the item id", "room", zone_id, rid,
                ))
        for spawn in (room.get("spawns") or []):
            nid = spawn if isinstance(spawn, str) else (spawn.get("id", "") if isinstance(spawn, dict) else "")
            if nid and nid not in all_npc_ids:
                issues.append(_err(
                    f"Room '{rid}' spawns unknown NPC '{nid}'",
                    f"Add NPC '{nid}' or fix the id", "room", zone_id, rid,
                ))
    return issues


def _check_items(zone_id: str, items: list, all_item_ids: set[str]) -> list[dict]:
    issues: list[dict] = []
    valid_slots = set(wc.EQUIPMENT_SLOTS) | {""}
    for item in items:
        iid = item.get("id") or "(no id)"
        if not item.get("id"):
            issues.append(_err("Item missing 'id'", 'Add id = "..."', "item", zone_id, iid))
        if not item.get("name"):
            issues.append(_err(f"Item '{iid}' missing 'name'", 'Add name = "..."', "item", zone_id, iid))
        slot = item.get("slot", "")
        if slot not in valid_slots:
            issues.append(_err(
                f"Item '{iid}': unknown slot '{slot}'",
                f"Valid slots: {sorted(wc.EQUIPMENT_SLOTS)} (or \"\" for non-equippable)",
                "item", zone_id, iid,
            ))
        if item.get("scenery") and item.get("slot"):
            issues.append(_warn(
                f"Item '{iid}': scenery + slot='{item['slot']}' — item can never be equipped",
                "Remove the slot field or remove the scenery flag", "item", zone_id, iid,
            ))
        if item.get("no_drop") and item.get("on_drop"):
            issues.append(_warn(
                f"Item '{iid}': no_drop + on_drop — script will never run",
                "Remove on_drop or remove no_drop", "item", zone_id, iid,
            ))
        for cmd in (item.get("commands") or []):
            if not cmd.get("verb"):
                issues.append(_warn(
                    f"Item '{iid}': [[commands]] entry has no verb — will never trigger",
                    'Add verb = "..." to the command', "item", zone_id, iid,
                ))
        issues.extend(_script_item_issues(
            item.get("on_get") or [], all_item_ids, f"Item '{iid}'", "item", zone_id, iid,
        ))
    return issues


def _check_npcs(
    zone_id: str,
    npcs: list,
    all_item_ids: set[str],
    all_styles: set[str],
    dialogue_npc_ids: set[str],
) -> list[dict]:
    issues: list[dict] = []
    for npc in npcs:
        nid = npc.get("id") or "(no id)"
        for f in _NPC_REQUIRED:
            if f not in npc:
                issues.append(_err(
                    f"NPC '{nid}' missing required field '{f}'",
                    f"Add {f} = ... to the NPC", "npc", zone_id, nid,
                ))
        style = npc.get("style", "")
        if style and all_styles and style not in all_styles:
            issues.append(_warn(
                f"NPC '{nid}' references unknown style '{style}'",
                "Add style or fix the style id", "npc", zone_id, nid,
            ))
        for entry in (npc.get("shop") or []):
            iid = entry.get("item_id", "")
            if iid and iid not in all_item_ids:
                issues.append(_err(
                    f"NPC '{nid}' shop references unknown item '{iid}'",
                    "Check the item id", "npc", zone_id, nid,
                ))
        for entry in (npc.get("give_accepts") or []):
            iid = entry.get("item_id", "")
            if iid and iid not in all_item_ids:
                issues.append(_err(
                    f"NPC '{nid}' give_accepts references unknown item '{iid}'",
                    "Check the item id", "npc", zone_id, nid,
                ))
            issues.extend(_script_item_issues(
                entry.get("script") or [], all_item_ids,
                f"NPC '{nid}' give_accepts", "npc", zone_id, nid,
            ))
        has_dlg_file = nid in dialogue_npc_ids
        has_dlg_str  = bool((npc.get("dialogue") or "").strip())
        is_hostile   = npc.get("hostile", False)
        has_shop     = bool(npc.get("shop"))
        if not has_dlg_file:
            if has_shop:
                issues.append(_warn(
                    f"NPC '{nid}' has a shop but no dialogue tree — players can't access it via 'talk'",
                    "Add a dialogue file", "npc", zone_id, nid,
                ))
            elif not has_dlg_str:
                if is_hostile:
                    issues.append(_warn(
                        f"NPC '{nid}' (hostile) has no dialogue — engine will generate a brush-off line",
                        'Add dialogue = "..." for a custom message', "npc", zone_id, nid,
                    ))
                else:
                    issues.append(_warn(
                        f"NPC '{nid}' has no dialogue tree or fallback string",
                        'Add a dialogue file or dialogue = "..."', "npc", zone_id, nid,
                    ))
    return issues


def _check_quests(zone_id: str, quests: list, all_npc_ids: set[str]) -> list[dict]:
    issues: list[dict] = []
    for quest in quests:
        qid   = quest.get("_id") or quest.get("id") or "?"
        giver = quest.get("giver", "")
        if giver and not giver.startswith("_") and giver not in all_npc_ids:
            issues.append(_warn(
                f"Quest '{qid}': giver '{giver}' not found",
                f"Add NPC '{giver}' or fix the giver id", "quest", zone_id, qid,
            ))
    return issues


def _check_dialogues(zone_id: str, dialogues: list) -> list[dict]:
    issues: list[dict] = []
    for dlg in dialogues:
        did   = dlg.get("_npc_id") or "?"
        nodes = {n["id"]: n for n in (dlg.get("node") or []) if n.get("id")}
        if "root" not in nodes:
            issues.append(_err(
                f"Dialogue '{did}': missing 'root' node",
                'Add [[node]] with id = "root"', "dialogue", zone_id, did,
            ))
        for resp in (dlg.get("response") or []):
            nxt = resp.get("next")
            if nxt and nxt not in nodes:
                issues.append(_err(
                    f"Dialogue '{did}': response next='{nxt}' not found",
                    f'Add a [[node]] with id = "{nxt}" or fix the next= value',
                    "dialogue", zone_id, did,
                ))
    return issues


# ── Public API ────────────────────────────────────────────────────────────────

def validate_world(world_path: Path) -> list[dict]:
    """
    Run core validation checks for a world. Returns a flat list of issue dicts.
    Designed to be importable and callable from both validate.py and wct_server.py.
    """
    wc.init(world_path)

    # ── Pass 1: collect ───────────────────────────────────────────────────────
    all_room_ids:     set[str] = set()
    all_npc_ids:      set[str] = set()
    all_item_ids:     set[str] = set()
    all_styles:       set[str] = set()
    dialogue_npc_ids: set[str] = set()

    zone_rooms:     dict[str, list] = {}
    zone_items:     dict[str, list] = {}
    zone_npcs:      dict[str, list] = {}
    zone_quests:    dict[str, list] = {}
    zone_dialogues: dict[str, list] = {}

    for zone_dir in sorted(world_path.iterdir()):
        if not zone_dir.is_dir() or zone_dir.name in _SKIP_DIRS:
            continue
        zid = zone_dir.name
        zone_rooms[zid]     = []
        zone_items[zid]     = []
        zone_npcs[zid]      = []
        zone_quests[zid]    = []
        zone_dialogues[zid] = []

        for path in sorted(zone_dir.glob("*.toml")):
            if path.name == "zone.toml":
                continue
            try:
                data = toml_load(path)
            except Exception:
                continue
            for room in data.get("room", []):
                zone_rooms[zid].append(room)
                if room.get("id"):
                    all_room_ids.add(room["id"])
            for item in data.get("item", []):
                zone_items[zid].append(item)
                if item.get("id"):
                    all_item_ids.add(item["id"])
            for npc in data.get("npc", []):
                zone_npcs[zid].append(npc)
                if npc.get("id"):
                    all_npc_ids.add(npc["id"])

        quest_dir = zone_dir / "quests"
        if quest_dir.exists():
            for path in sorted(quest_dir.glob("*.toml")):
                try:
                    data = toml_load(path)
                    data["_id"] = path.stem
                    zone_quests[zid].append(data)
                except Exception:
                    pass

        dlg_dir = zone_dir / "dialogues"
        if dlg_dir.exists():
            for path in sorted(dlg_dir.glob("*.toml")):
                dialogue_npc_ids.add(path.stem)
                try:
                    data = toml_load(path)
                    data["_npc_id"] = path.stem
                    zone_dialogues[zid].append(data)
                except Exception:
                    pass

        styles_dir = zone_dir / "styles"
        if styles_dir.exists():
            for path in sorted(styles_dir.glob("*.toml")):
                try:
                    data = toml_load(path)
                    for style in data.get("style", []):
                        if style.get("id"):
                            all_styles.add(style["id"])
                except Exception:
                    pass

    # ── Pass 2: validate ──────────────────────────────────────────────────────
    issues: list[dict] = []
    for zid in zone_rooms:
        issues.extend(_check_rooms(zid, zone_rooms[zid], all_room_ids, all_npc_ids, all_item_ids))
        issues.extend(_check_items(zid, zone_items[zid], all_item_ids))
        issues.extend(_check_npcs(zid, zone_npcs[zid], all_item_ids, all_styles, dialogue_npc_ids))
        issues.extend(_check_quests(zid, zone_quests[zid], all_npc_ids))
        issues.extend(_check_dialogues(zid, zone_dialogues[zid]))

    return issues
