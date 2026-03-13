"""
cli.py — Color CLI frontend for Delve.

Subscribes to EventBus Msg objects and renders them with ANSI colors.
All color/style decisions live here — the engine has zero knowledge of them.

Configuration is in frontend/config.py (word wrap, auto-attack, colors, aliases).
"""

from __future__ import annotations
import re
import textwrap
import sys
import time
from pathlib import Path

from engine.events import EventBus, Event
from engine.world import World
from engine.player import Player
from engine.commands import CommandProcessor
from engine.msg import Msg, Tag
from engine.room_flags import RoomFlags
import engine.world_config as _wc
from engine.world_config import list_worlds, peek_world_name

_DATA_DIR = Path(__file__).parent.parent / "data"


# ── Load config ───────────────────────────────────────────────────────────────

try:
    sys.path.insert(0, str(Path(__file__).parent))
    import config as _cfg
    WRAP_WIDTH               = int(getattr(_cfg, "WRAP_WIDTH", 100))
    AUTO_ATTACK              = bool(getattr(_cfg, "AUTO_ATTACK", True))
    AUTO_ATTACK_STOP_HP_PCT  = int(getattr(_cfg, "AUTO_ATTACK_STOP_HP_PCT", 15))
    COLOR_OVERRIDES          = dict(getattr(_cfg, "COLOR_OVERRIDES", {}))
    STARTUP_ALIASES          = dict(getattr(_cfg, "STARTUP_ALIASES", {}))
    LOG_ENABLED              = bool(getattr(_cfg, "LOG_ENABLED", False))
    LOG_FILE                 = str(getattr(_cfg, "LOG_FILE", "delve.log"))
    LOG_LEVEL                = str(getattr(_cfg, "LOG_LEVEL", "DEBUG"))
    LOG_CATEGORIES           = list(getattr(_cfg, "LOG_CATEGORIES", []))
except ImportError:
    WRAP_WIDTH               = 100
    AUTO_ATTACK              = True
    AUTO_ATTACK_STOP_HP_PCT  = 15
    COLOR_OVERRIDES          = {}
    STARTUP_ALIASES          = {}
    LOG_ENABLED              = False
    LOG_FILE                 = "delve.log"
    LOG_LEVEL                = "DEBUG"
    LOG_CATEGORIES           = []

# Initialise engine logger from config
import engine.log as _elog
_elog.configure(
    enabled    = LOG_ENABLED,
    log_file   = LOG_FILE,
    level      = LOG_LEVEL,
    categories = LOG_CATEGORIES,
)


# ── ANSI helpers ─────────────────────────────────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"


def _fg(r: int, g: int, b: int) -> str:
    return f"\033[38;2;{r};{g};{b}m"

def _bg(r: int, g: int, b: int) -> str:
    return f"\033[48;2;{r};{g};{b}m"


def _parse_color_override(value: str) -> str:
    """Convert a human-readable color spec to an ANSI sequence.

    Accepted formats:
      "B(r,g,b)"  → bold + foreground colour
      "(r,g,b)"   → plain foreground colour
      "D(r,g,b)"  → dim + foreground colour
    Any string that doesn't match is returned unchanged (raw ANSI passthrough).
    """
    import re
    m = re.fullmatch(r'\s*([BD]?)\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)\s*', value)
    if m:
        prefix, r, g, b = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
        weight = BOLD if prefix == "B" else DIM if prefix == "D" else ""
        return weight + _fg(r, g, b)
    return value   # already a raw ANSI string — pass through unchanged


# ── Color palette ─────────────────────────────────────────────────────────────

PALETTE: dict[str, str] = {
    Tag.ROOM_NAME:    BOLD   + _fg(255, 220, 100),
    Tag.ROOM_DIVIDER: DIM    + _fg(100,  90,  60),
    Tag.ROOM_DESC:             _fg(200, 210, 220),
    Tag.EXIT:         BOLD   + _fg( 80, 200, 120),
    Tag.MOVE:                  _fg(140, 180, 140),

    Tag.NPC:          BOLD   + _fg(230, 130,  60),
    Tag.ITEM:                  _fg(100, 190, 240),
    Tag.ITEM_EQUIP:   BOLD   + _fg(100, 190, 240),
    Tag.ITEM_HAVE:    BOLD   + _fg( 80, 220, 120),   # bright green — in inventory
    Tag.ITEM_BANK:             _fg(120, 160, 140),   # muted teal  — in bank
    Tag.ITEM_MISSING:          _fg(160,  80,  80),   # muted red   — not found
    Tag.DIALOGUE:              _fg(220, 200, 160),

    Tag.COMBAT_HIT:   BOLD   + _fg(240,  80,  80),
    Tag.COMBAT_RECV:           _fg(200,  60,  60),
    Tag.COMBAT_KILL:  BOLD   + _fg(255, 160,  40),
    Tag.COMBAT_DEATH: BOLD   + _fg(180,   0,   0),

    Tag.REWARD_XP:    BOLD   + _fg(160, 120, 255),
    Tag.REWARD_GOLD:           _fg(255, 210,  50),

    Tag.STATS:                 _fg(160, 200, 255),
    Tag.SYSTEM:                _fg(150, 150, 150),
    Tag.ERROR:        BOLD   + _fg(255,  80,  80),
    Tag.BLANK:                 "",
    Tag.AMBIGUOUS:    BOLD   + _fg(255, 180,  40),
    Tag.STYLE:        BOLD   + _fg(180, 120, 255),
    Tag.SHOP:                  _fg(100, 220, 180),
    Tag.DOOR:         BOLD   + _fg(200, 160,  80),
    Tag.MAP:                   _fg( 60, 180,  80),
    Tag.QUEST:        BOLD   + _fg(255, 215,   0),
    Tag.JOURNAL:      BOLD   + _fg(180, 220, 255),   # bright icy blue — journal entries
}

# Apply any overrides from config
for _tag_name, _raw in COLOR_OVERRIDES.items():
    _tag = getattr(Tag, _tag_name, None)
    if _tag is not None:
        PALETTE[_tag] = _parse_color_override(_raw)


def _render(msg: Msg) -> str:
    color = PALETTE.get(msg.tag, "")
    text  = msg.text
    if not text and msg.tag == Tag.BLANK:
        return ""

    # Word-wrap long lines. MAP and ROOM_DIVIDER use exact-width ASCII art;
    # STATS uses aligned columns. Wrapping any of these would break their layout.
    if WRAP_WIDTH > 0 and msg.tag not in (Tag.MAP, Tag.ROOM_DIVIDER, Tag.STATS):
        if len(text) > WRAP_WIDTH:
            indent = len(text) - len(text.lstrip())
            prefix = " " * indent
            wrapped = textwrap.fill(
                text.strip(),
                width=WRAP_WIDTH,
                subsequent_indent=prefix,
            )
            text = prefix + wrapped if indent else wrapped

    return f"{color}{text}{RESET}" if color else text


# ── Banner ────────────────────────────────────────────────────────────────────

BANNER = (
    _fg(255, 200, 80) + BOLD +
    r"""
 ____       _
|  _ \  ___| |_   _____
| | | |/ _ \ \ \ / / _ \
| |_| |  __/ |\ V /  __/
|____/ \___|_| \_/ \___|
""" + RESET +
    DIM + _fg(180, 150, 60) +
    "         a Python MUD engine\n" +
    RESET
)

PROMPT = _fg(80, 160, 255) + BOLD + "\n> " + RESET


# ── Frontend class ────────────────────────────────────────────────────────────

class CLIFrontend:
    def __init__(self, admin_mode: bool = False):
        self._admin_mode = admin_mode
        self.bus       = EventBus()
        self.world:     World | None           = None   # set in run() after world selection
        self.player:    Player | None          = None
        self.processor: CommandProcessor | None = None

        # Auto-attack state
        self._auto_attacking:     bool = False
        self._auto_attack_target: str  = ""
        self._last_ambiguous:     bool = False   # set True when AMBIGUOUS output received
        # Runtime auto-attack toggle (starts from config, can be changed in-session)
        self._auto_attack_on:     bool = AUTO_ATTACK

        self.bus.subscribe(Event.OUTPUT,      self._on_output)
        self.bus.subscribe(Event.PLAYER_DIED, self._on_player_died)

    def _on_output(self, msg: Msg) -> None:
        rendered = _render(msg)
        _elog.debug("dialogue", "CLI._on_output",
                    tag=msg.tag, text_len=len(msg.text),
                    text_preview=msg.text[:60] if msg.text else "(empty)",
                    rendered_len=len(rendered), will_print=bool(rendered or msg.tag == Tag.BLANK))
        if msg.tag == Tag.PAUSE:
            # Cutscene pause — flush stdout then sleep for the requested duration.
            sys.stdout.flush()
            time.sleep(float(msg.text))
            return

        if rendered or msg.tag == Tag.BLANK:
            print(rendered)

        # Track ambiguous-resolution messages so auto-attack can abort
        if msg.tag == Tag.AMBIGUOUS:
            self._last_ambiguous = True

        # Cancel auto-attack if player is dead or combat ended
        if self._auto_attacking:
            if msg.tag == Tag.COMBAT_DEATH or msg.tag == Tag.COMBAT_KILL:
                self._auto_attacking = False

    def _on_player_died(self) -> None:
        self._auto_attacking = False
        debt_msg = (f"  XP debt: {self.player.xp_debt} XP must be earned before "
                    f"progress resumes.") if self.player.xp_debt else ""
        print(
            BOLD + _fg(180, 0, 0) +
            "\n╔══════════════════════════════════════════╗\n"
            "║           YOU HAVE BEEN SLAIN            ║\n"
            "╚══════════════════════════════════════════╝\n" +
            RESET +
            _fg(160, 160, 160) +
            "  Your carried gold is lost.\n"
            "  A corpse containing your items remains where you fell\n"
            "  — it will decay in 10 minutes if not recovered.\n" +
            (debt_msg + "\n" if debt_msg else "") +
            RESET +
            BOLD + _fg(200, 200, 255) +
            f"\n  You wake at {self.player.room_id.replace('_', ' ').title()}.\n" +
            RESET
        )
        self.processor.process("look")

    # ── World selection ───────────────────────────────────────────────────────

    @staticmethod
    def _select_world() -> Path:
        """Find and return the selected world folder path.

        Auto-selects when only one world exists. Shows a numbered menu
        when multiple worlds are found.
        """
        worlds = list_worlds(_DATA_DIR)
        if not worlds:
            raise SystemExit(
                "No worlds found in data/. "
                "Create a world subfolder with a config.py to get started."
            )
        if len(worlds) == 1:
            return worlds[0]
        # Multiple worlds — show selection menu
        label = _fg(200, 200, 200)
        print(label + "\nAvailable worlds:" + RESET)
        for i, w in enumerate(worlds, 1):
            print(label + f"  {i}. {peek_world_name(w)}  ({w.name})" + RESET)
        while True:
            choice = (input(label + "Select a world [1]: " + RESET).strip() or "1")
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(worlds):
                    return worlds[idx]
            except ValueError:
                pass
            print("  Invalid selection — enter a number from the list.")

    # ── Login / Character creation ────────────────────────────────────────────

    def _login(self, world_path: Path) -> Player:
        print(BANNER)
        label = _fg(200, 200, 200)
        bold  = BOLD + _fg(255, 255, 255)
        dim   = _fg(160, 160, 160)
        while True:
            name = input(label + "Enter your character name: " + RESET).strip()
            if not name:
                continue
            if Player.exists(name):
                player = Player.load(name)
                if not player.world_id:
                    player.world_id = world_path.name   # migrate old save
                print(bold + f"\nWelcome back, {player.name}!\n" + RESET)
                return player
            confirm = input(
                label + f"'{name}' is a new character. Create? [y/n]: " + RESET
            ).strip().lower()
            if confirm == "y":
                player = Player.create_new(name)
                player.world_id = world_path.name
                player.room_id  = self.world.start_room
                print(bold + f"\nWelcome to the world, {player.name}!\n" + RESET)
                print(dim + f"  Starting stats rolled:  ATK {player.attack}  DEF {player.defense}  HP {player.hp}" + RESET)
                print(dim + "  (Stats grow through combat and equipment.)\n" + RESET)
                player.save()
                return player

    # ── Auto-attack ───────────────────────────────────────────────────────────

    def _is_safe_room(self) -> bool:
        """Return True if the player is in a no_combat or safe_combat room."""
        if not self.player:
            return True
        room = self.world.get_room(self.player.room_id)
        if not room:
            return True
        return RoomFlags.has(room, RoomFlags.NO_COMBAT) or \
               RoomFlags.has(room, RoomFlags.SAFE_COMBAT)

    def _resolve_attack_target(self, partial: str) -> "str | None":
        """
        Resolve a partial or numbered NPC name to the display name used by the
        engine (e.g. "Corrupted Wolf 1") so the auto-attack loop can send an
        unambiguous command every swing.

        Uses the same _numbered_npcs logic as the engine:
          "wolf"   → "Corrupted Wolf 1"  (first match, no error)
          "wolf 1" → "Corrupted Wolf 1"
          "wolf 2" → "Corrupted Wolf 2"
        Returns None only if no NPC matches at all.
        """
        from engine.commands import CommandProcessor
        room  = self.world.prepare_room(self.player.room_id, self.player)
        alive = [n for n in room.get("_npcs", []) if n.get("hp", 1) > 0] if room else []
        numbered = CommandProcessor._numbered_npcs(alive)
        t = partial.lower().strip()

        # Exact match against display name
        exact = [(npc, dn) for npc, dn in numbered if dn.lower() == t]
        if exact:
            return exact[0][1]

        # Partial match — first hit wins
        partial_matches = [(npc, dn) for npc, dn in numbered if t in dn.lower()]
        if partial_matches:
            return partial_matches[0][1]

        return None

    def _run_auto_attack(self, full_name: str, stop_pct: int) -> None:
        """
        Continue attacking the resolved target until it dies, all targets of that
        base name die, the player falls below stop_pct HP, or a safe room is entered.

        When a numbered target (e.g. "Corrupted Wolf 1") dies, the loop
        automatically re-resolves to the next alive target with the same base name.
        """
        p         = self.player
        from engine.commands import CommandProcessor
        swing = 0
        _elog.debug("autoattack", "_run_auto_attack entered",
                    full_name=full_name, stop_pct=stop_pct,
                    player_hp=p.hp, player_max_hp=p.max_hp)

        while True:
            swing += 1
            # Safety checks before each swing
            if self._is_safe_room():
                _elog.info("autoattack", "loop exit: safe room",
                           swing=swing, room=p.room_id)
                print(_fg(150, 150, 150) + "  [Auto-attack: safe room — stopping.]" + RESET)
                break

            # Re-resolve every iteration: current target may have died,
            # need to move to the next alive NPC with the same base name.
            room = self.world.prepare_room(p.room_id, self.player)
            npcs = room.get("_npcs", []) if room else []

            # Strip trailing number to get the family of targets
            base_name = re.sub(r"\s+\d+$", "", full_name).lower()

            # Get numbered list of live NPCs that match the base name
            alive = [n for n in npcs if n.get("hp", 0) > 0]
            numbered = CommandProcessor._numbered_npcs(alive)
            matching = [(npc, dn) for npc, dn in numbered
                        if base_name in dn.lower()]

            _elog.debug("autoattack", f"swing {swing}: re-resolved targets",
                        base_name=base_name,
                        alive_count=len(alive),
                        matching=[dn for _,dn in matching],
                        player_hp=p.hp)

            if not matching:
                _elog.info("autoattack", "loop exit: no more matching targets",
                           swing=swing, base_name=base_name)
                break   # all targets of this type are dead

            # Use the first remaining live target
            current_name = matching[0][1]
            current_npc  = matching[0][0]
            cmd = f"attack {current_name}"

            # HP threshold
            if stop_pct > 0:
                pct = int(100 * p.hp / max(p.max_hp, 1))
                if pct <= stop_pct:
                    _elog.info("autoattack", "loop exit: HP threshold",
                               swing=swing, hp_pct=pct, stop_pct=stop_pct,
                               player_hp=p.hp)
                    print(_fg(255, 160, 40) + BOLD +
                          f"  [Auto-attack: HP at {pct}% — stopping. Heal up!]" + RESET)
                    break

            _elog.debug("autoattack", f"swing {swing}: attacking",
                        cmd=cmd, target_hp=current_npc.get("hp","?"),
                        target_max_hp=current_npc.get("max_hp","?"),
                        player_hp=p.hp)

            self._last_ambiguous = False
            self._auto_attacking  = True
            self.processor.process(cmd)
            self._auto_attacking  = False

            _elog.debug("autoattack", f"swing {swing}: after attack",
                        player_hp=p.hp, target_hp=current_npc.get("hp","?"),
                        last_ambiguous=self._last_ambiguous)

            if self._last_ambiguous or p.hp <= 0:
                _elog.info("autoattack", "loop exit: ambiguous or player dead",
                           swing=swing, last_ambiguous=self._last_ambiguous,
                           player_hp=p.hp)
                break

    # ── Admin commands ────────────────────────────────────────────────────────

    def _admin_cmd(self, cmd: str) -> bool:
        """Handle admin commands (typed as -<cmd>). Returns True if handled."""
        dim  = _fg(140, 140, 200)
        bold = BOLD + _fg(200, 200, 255)
        err  = _fg(220, 100, 100)

        if cmd == "reload":
            print(dim + "  [admin] Reloading world data..." + RESET)
            try:
                _wc.init(self._world_path)
                self.world = World(self._world_path)
                self.world.attach_player(self.player)
                self.processor = CommandProcessor(self.world, self.player, self.bus)
                print(dim + f"  [admin] World '{_wc.WORLD_NAME}' reloaded." + RESET)
                self.processor.do_look()
            except Exception as exc:
                print(err + f"  [admin] Reload failed: {exc}" + RESET)
            return True

        if cmd in ("flags", "tags"):
            flags = sorted(self.player.flags)
            print(bold + f"\n  Player flags ({len(flags)}):" + RESET)
            if flags:
                for f in flags:
                    print(dim + f"    {f}" + RESET)
            else:
                print(dim + "    (none)" + RESET)
            print()
            return True

        parts = cmd.split(None, 1)
        verb  = parts[0]
        arg   = parts[1].strip() if len(parts) > 1 else ""

        if verb == "addflag":
            if not arg:
                print(err + "  Usage: -addflag <flag_name>" + RESET)
            else:
                self.player.flags.add(arg)
                print(dim + f"  [admin] Flag '{arg}' added." + RESET)
            return True

        if verb == "remflag":
            if not arg:
                print(err + "  Usage: -remflag <flag_name>" + RESET)
            elif arg in self.player.flags:
                self.player.flags.discard(arg)
                print(dim + f"  [admin] Flag '{arg}' removed." + RESET)
            else:
                print(dim + f"  [admin] Flag '{arg}' not set." + RESET)
            return True

        if verb == "teleport":
            if not arg:
                print(err + "  Usage: -teleport <room_id>" + RESET)
            else:
                room = self.world.prepare_room(arg, self.player)
                if room is None:
                    print(err + f"  [admin] Unknown room '{arg}'." + RESET)
                else:
                    self.player.room = arg
                    self.world.evict_distant_zones(self.world.zone_for_room(arg))
                    print(dim + f"  [admin] Teleported to '{arg}'." + RESET)
                    self.processor.do_look()
            return True

        if verb == "give":
            if not arg:
                print(err + "  Usage: -give <item_id>" + RESET)
            else:
                item = self.world.spawn_item(arg)
                if item is None:
                    print(err + f"  [admin] Unknown item '{arg}'." + RESET)
                else:
                    self.player.inventory.append(item)
                    name = item.get("name", arg)
                    print(dim + f"  [admin] Gave '{name}' ({arg}) to player." + RESET)
            return True

        # Unknown admin command — show help rather than silently ignoring
        print(err + f"  Unknown admin command '-{cmd}'. Available: -reload  -flags  -tags  -addflag  -remflag  -teleport  -give" + RESET)
        return True

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        self._world_path = self._select_world()
        world_path       = self._world_path
        _wc.init(world_path)
        self.world     = World(world_path)           # zone_state_dir set after login
        self.player    = self._login(world_path)
        self.world.attach_player(self.player)        # switch to player-specific state dir
        self.processor = CommandProcessor(self.world, self.player, self.bus)

        # Apply startup aliases from config (character aliases take priority)
        for word, expansion in STARTUP_ALIASES.items():
            if word not in self.player.aliases:
                self.player.aliases[word] = expansion

        self.processor.do_look()

        while not self.processor.quit_requested:
            try:
                raw = input(PROMPT).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                self.processor.process("quit")
                _elog.close()
                break
            if not raw:
                continue

            # Admin commands (prefixed with -)
            if raw.startswith("-"):
                if not self._admin_mode:
                    print(_fg(220, 100, 100) + "  Admin mode is disabled. Run with --admin to enable." + RESET)
                    continue
                if self._admin_cmd(raw[1:].strip()):
                    continue

            # Check for auto-attack trigger
            lower = raw.lower()
            is_attack = lower.startswith("attack ") or lower.startswith("kill ")

            # In-session auto-attack toggle
            if lower in ("autoattack", "aa", "auto"):
                self._auto_attack_on = not self._auto_attack_on
                state = "ON" if self._auto_attack_on else "OFF"
                print(_fg(150, 200, 150) + f"  Auto-attack {state}." + RESET)
                continue

            if self._auto_attack_on and is_attack and not self._is_safe_room():
                parts   = raw.split(None, 1)
                partial = parts[1].strip() if len(parts) > 1 else ""
                if partial:
                    # Resolve the full canonical name before the first swing.
                    full_name = self._resolve_attack_target(partial)
                    if full_name:
                        # First swing — user-initiated, use the full resolved name
                        _elog.section(f"AUTO-ATTACK initiated: target={full_name!r}")
                        self._last_ambiguous = False
                        self.processor.process(f"attack {full_name}")
                        if (not self.processor.quit_requested
                                and self.player.hp > 0
                                and not self._last_ambiguous):
                            self._run_auto_attack(full_name, AUTO_ATTACK_STOP_HP_PCT)
                        _elog.section("AUTO-ATTACK loop ended")
                        continue
                    # Not found (or not in a room with hostiles) — fall through
                    # so the engine can emit its own "no such target" error.

            _elog.section(f"CMD: {raw}")
            self.processor.process(raw)

        _elog.close()  # normal quit path




