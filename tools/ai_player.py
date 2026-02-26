#!/usr/bin/env python3
"""
ai_player.py — Autonomous AI playtester for Delve.

Drives the game engine via the same CommandProcessor used by human players,
using the Anthropic API to decide what commands to issue. Logs all I/O to a
session file and outputs a structured summary on exit.

Usage:
    python tools/ai_player.py [options]

Options:
    --name NAME        Character name (default: AI_Tester)
    --turns N          Max turns before stopping (default: 1000)
    --goal GOAL        High-level goal for the AI (default: "explore and fight")
    --model MODEL      Claude model to use (default: claude-haiku-4-5-20251001)
    --out DIR          Directory for log files (default: tools/ai_sessions/)
    --verbose          Print AI reasoning to stdout in addition to log

The AI receives the game's text output as context and decides what command to
type next. It tracks its own state (XP, gold, style progression, rooms visited)
and reports on them in the log.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ── Minimal Anthropic client (stdlib only, no sdk required) ────────────────

def call_claude(messages: list[dict], system: str, model: str,
                max_retries: int = 5) -> str:
    """
    Call Anthropic /v1/messages and return the text response.
    Retries up to max_retries times on 429 rate-limit responses using
    exponential backoff, honouring the Retry-After header when present.
    """
    import http.client
    import json as _json

    # Priority: environment variable > config.py
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from config import ANTHROPIC_API_KEY as _cfg_key
            api_key = _cfg_key or ""
        except ImportError:
            pass
    if not api_key:
        raise RuntimeError(
            "No Anthropic API key found. Either:\n"
            "  1. Set ANTHROPIC_API_KEY environment variable, or\n"
            "  2. Edit the ANTHROPIC_API_KEY line in config.py (project root)"
        )

    payload = _json.dumps({
        "model":      model,
        "max_tokens": 256,   # commands are short; 256 is plenty
        "system":     system,
        "messages":   messages,
    }).encode()

    headers = {
        "content-type":      "application/json",
        "x-api-key":         api_key,
        "anthropic-version": "2023-06-01",
    }

    backoff = 10  # seconds — start conservatively for a 50k TPM limit
    for attempt in range(max_retries):
        conn = http.client.HTTPSConnection("api.anthropic.com", timeout=30)
        conn.request("POST", "/v1/messages", body=payload, headers=headers)
        resp = conn.getresponse()
        body = _json.loads(resp.read())

        if resp.status == 200:
            return body["content"][0]["text"].strip()

        if resp.status == 429:
            # Honour Retry-After if the server sent one, else use backoff
            retry_after = resp.getheader("Retry-After")
            wait = int(retry_after) if retry_after and retry_after.isdigit() else backoff
            print(f"       [rate limit] waiting {wait}s (attempt {attempt+1}/{max_retries})")
            time.sleep(wait)
            backoff = min(backoff * 2, 120)  # cap at 2 minutes
            continue

        raise RuntimeError(f"API error {resp.status}: {body}")

    raise RuntimeError(f"API call failed after {max_retries} retries (persistent rate limit)")


# ── Output capture ─────────────────────────────────────────────────────────

class OutputCapture:
    """Captures EventBus OUTPUT events as plain text lines."""

    def __init__(self):
        self.lines: list[str] = []
        self._last_flush = 0

    def on_output(self, msg) -> None:
        self.lines.append(msg.text)

    def flush(self) -> str:
        """Return accumulated output and clear."""
        out = "\n".join(self.lines)
        self.lines.clear()
        return out


# ── AI Player ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an AI playtester for a text-based MUD called Delve. Your job is to:
1. Play the game autonomously to find bugs and test systems.
2. Pursue the goal given to you methodically.
3. Try a wide variety of commands to exercise different systems.
4. Note anything that looks like a bug, error, or awkward behaviour.

You receive the most recent game output and your current state. Respond with
EXACTLY ONE command to issue, on its own line, with no explanation.

Valid command examples:
  look
  north / south / east / west / up / down
  talk <npc>
  attack <npc>
  get <item>
  drop <item>
  equip <item>
  unequip <item>
  buy <item>
  sell <item>
  deposit <item>
  withdraw <item>
  balance
  loot corpse
  style
  learn <style>
  use <item>
  map
  save
  status
  quit

ONLY output the command. Nothing else. No quotes. No punctuation after.
If you are stuck or uncertain, issue "look" to get your bearings.
"""


_OUTPUT_LIMIT = 800   # chars — long room descriptions balloon token count

def make_user_message(game_output: str, state: dict, goal: str,
                      turn: int, max_turns: int, history: list[str]) -> str:
    recent = history[-5:] if len(history) > 5 else history
    # Compact state: skip zero/None/empty values to cut tokens
    s = state
    compact = (
        f"room={s['room']} hp={s['hp']}/{s['max_hp']} lv={s['level']} "
        f"xp={s['xp']}/{s['xp_next']} gold={s['gold']} "
        f"inv=[{', '.join(s['inventory'])}] "
        f"style={s['style']}({s['style_prof'].get(s['style'],0):.0f})"
    )
    if s["xp_debt"]:
        compact += f" debt={s['xp_debt']}"
    if s["quests"]:
        compact += f" quests={s['quests']}"
    # Trim game output to avoid ballooning prompt size
    trimmed = game_output or "(no output)"
    if len(trimmed) > _OUTPUT_LIMIT:
        trimmed = trimmed[-_OUTPUT_LIMIT:]   # keep the most recent text
    return (
        f"GOAL: {goal} | TURN {turn}/{max_turns}\n"
        f"STATE: {compact}\n"
        f"RECENT: {', '.join(recent) if recent else '(none)'}\n\n"
        f"OUTPUT:\n{trimmed}\n\n"
        f"Command?"
    )


def extract_player_state(player) -> dict:
    return {
        "name":        player.name,
        "room":        player.room_id,
        "hp":          player.hp,
        "max_hp":      player.max_hp,
        "level":       player.level,
        "xp":          player.xp,
        "xp_next":     player.xp_next,
        "xp_debt":     player.xp_debt,
        "gold":        player.gold,
        "inventory":   [i.get("name","?") for i in player.inventory],
        "equipped":    {k: (v["name"] if v else None) for k, v in player.equipped.items()},
        "bank":        [i.get("name","?") for i in player.bank],
        "style":       player.active_style,
        "style_prof":  {k: round(v,1) for k, v in player.style_prof.items()},
        "bind_room":   player.bind_room,
        "rooms_seen":  len(player.visited_rooms),
        "quests":      list(player.active_quests.keys()),
        "flags":       list(player.flags),
    }


def sanitise_command(raw: str) -> str:
    """Strip any explanation the model might have accidentally added."""
    # Take only the first line
    line = raw.strip().splitlines()[0].strip()
    # Remove wrapping quotes
    line = line.strip('"\'`')
    # Remove trailing punctuation
    line = line.rstrip('.,:;')
    return line.lower()


DIALOGUE_SYSTEM = """\
You are playing a text MUD. You are in a dialogue with an NPC.
You will be shown the conversation so far and a numbered list of response choices.
Reply with ONLY the number of the choice you want to pick. Nothing else.
"""


def make_dialogue_input_fn(args, capture, log_fn, turn_ref):
    """
    Return an input_fn for dialogue.run_inline:
        input_fn(prompt: str, choices: list[tuple[str, str]]) -> str
    Calls Claude with the dialogue context + choices; falls back to "1" on error.
    """
    def dialogue_input(prompt, choices):
        convo_so_far = capture.flush()
        choices_text = "\n".join(f"  [{k}] {text}" for k, text in choices)
        user_msg = (
            f"Dialogue so far:\n{convo_so_far}\n\n"
            f"Choices:\n{choices_text}\n\n"
            f"Which number do you pick?"
        )
        try:
            raw = call_claude(
                [{"role": "user", "content": user_msg}],
                DIALOGUE_SYSTEM,
                args.model,
            )
            choice = raw.strip().splitlines()[0].strip().rstrip(".,;:")
            valid_keys = [str(k) for k, _ in choices]
            if choice not in valid_keys:
                digits = re.findall(r"\d+", choice)
                choice = digits[0] if digits and digits[0] in valid_keys else valid_keys[0]
        except Exception as e:
            print(f"       [dialogue] API error, defaulting to 1: {e}")
            log_fn("dialogue_api_error", {"turn": turn_ref[0], "error": str(e)})
            choice = "1"
        print(f"  [t{turn_ref[0]}] dialogue → {choice}")
        log_fn("dialogue_choice", {"turn": turn_ref[0], "choice": choice,
                                    "choices": [t for _, t in choices]})
        return choice
    return dialogue_input


def run_session(args: argparse.Namespace) -> None:
    from engine.world import World
    from engine.player import Player
    from engine.events import EventBus, Event
    from engine.quests import QuestTracker
    from engine.commands import CommandProcessor

    # ── Setup ──────────────────────────────────────────────────────────────
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = out_dir / f"session_{args.name}_{ts}.jsonl"

    def log(event_type: str, data: dict) -> None:
        record = {"t": time.time(), "type": event_type, **data}
        with open(log_path, "a") as f:
            f.write(json.dumps(record) + "\n")

    print(f"[ai_player] Starting session → {log_path}")
    print(f"[ai_player] Goal: {args.goal}")
    print(f"[ai_player] Max turns: {args.turns}")
    print()

    # ── Game setup ────────────────────────────────────────────────────────
    bus     = EventBus()
    capture = OutputCapture()
    bus.subscribe(Event.OUTPUT, capture.on_output)

    world = World()

    char_name = args.name
    if Player.exists(char_name):
        player = Player.load(char_name)
        print(f"[ai_player] Loaded existing character '{char_name}'")
    else:
        player = Player(char_name)
        player.room_id  = world.start_room
        player.bind_room = world.start_room
        print(f"[ai_player] Created new character '{char_name}'")

    quests   = QuestTracker(player)
    turn_ref = [0]  # mutable int so dialogue_input_fn can read current turn
    dlg_input = make_dialogue_input_fn(args, capture, log, turn_ref)
    proc     = CommandProcessor(world, player, bus, input_fn=dlg_input)

    deaths      = 0
    bus.subscribe(Event.PLAYER_DIED, lambda: setattr(session_state, 'died', True))

    class session_state:
        died = False

    log("session_start", {
        "name":      char_name,
        "goal":      args.goal,
        "max_turns": args.turns,
        "model":     args.model,
    })

    # ── Initial look ──────────────────────────────────────────────────────
    proc.process("look")
    initial_output = capture.flush()
    if args.verbose:
        print(initial_output)

    # ── Conversation history for Claude ───────────────────────────────────
    api_messages: list[dict] = []
    command_history: list[str] = []
    bugs_noted: list[str] = []

    # ── Main loop ─────────────────────────────────────────────────────────
    prev_game_out = ""  # output from previous turn, carried forward
    error_backoff = 5   # grows on consecutive API errors, resets on success
    for turn in range(1, args.turns + 1):
        turn_ref[0] = turn
        if proc.quit_requested:
            print(f"[ai_player] Game quit at turn {turn}")
            break

        # Collect output: turn 1 uses the initial look; subsequent turns use
        # whatever the game emitted since the last proc.process() call.
        # Note: dialogue choices flush capture mid-turn (inside proc.process),
        # so we accumulate any remaining output here to avoid losing it.
        if turn == 1:
            pending_output = initial_output
        else:
            extra = capture.flush()
            pending_output = (prev_game_out + ("\n" + extra if extra else "")).strip()

        state = extract_player_state(player)
        user_msg = make_user_message(
            pending_output,
            state, args.goal, turn, args.turns, command_history,
        )

        api_messages.append({"role": "user", "content": user_msg})

        # Keep context window tight — each message pair is ~200-400 tokens
        if len(api_messages) > 8:
            api_messages = api_messages[-8:]

        # ── Call Claude ───────────────────────────────────────────────────
        try:
            raw_response = call_claude(api_messages, SYSTEM_PROMPT, args.model)
            error_backoff = 5  # reset on success
        except Exception as e:
            print(f"[ai_player] API error at turn {turn}: {e}")
            log("api_error", {"turn": turn, "error": str(e)})
            time.sleep(error_backoff)
            error_backoff = min(error_backoff * 2, 60)
            continue

        command = sanitise_command(raw_response)
        api_messages.append({"role": "assistant", "content": raw_response})
        command_history.append(command)

        print(f"[{turn:3}] {player.room_id:<30} > {command}")
        if args.verbose:
            print(f"       (raw: {raw_response!r})")

        log("turn", {
            "turn":    turn,
            "room":    player.room_id,
            "command": command,
            "state":   state,
        })

        # ── Execute command ───────────────────────────────────────────────
        # dialogue_input_fn may flush capture mid-call; we accumulate below
        died_before = session_state.died
        session_state.died = False
        proc.process(command)
        game_out = capture.flush()
        prev_game_out = game_out  # carried into next turn's pending_output

        if args.verbose:
            print(game_out)

        # ── Death tracking ────────────────────────────────────────────────
        if session_state.died:
            deaths += 1
            log("death", {"turn": turn, "room": player.room_id, "deaths_total": deaths})
            print(f"       *** DIED (total deaths: {deaths}) ***")

        # ── Bug detection heuristics ──────────────────────────────────────
        bug_signals = [
            "traceback", "error:", "attributeerror", "keyerror",
            "typeerror", "valueerror", "indexerror", "nonetype",
            "is a bug", "[error: room", "not found. this is a bug",
        ]
        lo = game_out.lower()
        for sig in bug_signals:
            if sig in lo:
                bug_note = f"Turn {turn} | cmd={command!r} | signal={sig!r}"
                if bug_note not in bugs_noted:
                    bugs_noted.append(bug_note)
                    log("bug_detected", {"turn": turn, "command": command,
                                          "signal": sig, "output_snippet": game_out[:300]})
                    print(f"       *** BUG SIGNAL: {sig} ***")

        # ── Quit command ──────────────────────────────────────────────────
        if command in ("quit", "exit"):
            break

        # Small delay to be polite to the API
        time.sleep(0.4)

    # ── Session summary ───────────────────────────────────────────────────
    final_state = extract_player_state(player)
    summary = {
        "turns_played":   min(turn, args.turns),
        "deaths":         deaths,
        "bugs_detected":  len(bugs_noted),
        "rooms_visited":  len(player.visited_rooms),
        "final_level":    player.level,
        "final_xp":       player.xp,
        "final_gold":     player.gold,
        "style_prof":     final_state["style_prof"],
        "quests_active":  list(player.active_quests.keys()),
        "quests_done":    list(player.completed_quests),
        "bugs":           bugs_noted,
    }
    log("session_end", summary)
    player.save()

    print()
    print("─" * 50)
    print("SESSION SUMMARY")
    print("─" * 50)
    print(f"  Turns played:   {summary['turns_played']}")
    print(f"  Deaths:         {summary['deaths']}")
    print(f"  Rooms visited:  {summary['rooms_visited']}")
    print(f"  Level reached:  {summary['final_level']}")
    print(f"  XP earned:      {summary['final_xp']}")
    print(f"  Gold:           {summary['final_gold']}")
    print(f"  Style prof:     {summary['style_prof']}")
    print(f"  Bugs detected:  {summary['bugs_detected']}")
    if bugs_noted:
        print("\n  Bug signals:")
        for b in bugs_noted:
            print(f"    • {b}")
    print(f"\n  Log saved: {log_path}")
    print("─" * 50)


# ── Session log analyser ───────────────────────────────────────────────────

def analyse_sessions(session_dir: Path) -> None:
    """Read all .jsonl session files and print aggregated statistics."""
    files = sorted(session_dir.glob("*.jsonl"))
    if not files:
        print(f"No session files in {session_dir}")
        return

    totals = {
        "sessions": 0, "turns": 0, "deaths": 0, "bugs": 0,
        "rooms": set(), "xp_earned": [], "style_data": {},
    }

    for path in files:
        events = []
        with open(path) as f:
            for line in f:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

        end_events = [e for e in events if e["type"] == "session_end"]
        if not end_events:
            continue

        totals["sessions"] += 1
        end = end_events[-1]
        totals["turns"]  += end.get("turns_played", 0)
        totals["deaths"] += end.get("deaths", 0)
        totals["bugs"]   += end.get("bugs_detected", 0)
        totals["xp_earned"].append(end.get("final_xp", 0))

        for turn_ev in [e for e in events if e["type"] == "turn"]:
            totals["rooms"].add(turn_ev.get("room",""))
            for sname, sval in turn_ev.get("state",{}).get("style_prof",{}).items():
                totals["style_data"].setdefault(sname,[]).append(sval)

    print(f"\nAGGREGATE STATS ({totals['sessions']} sessions, {len(files)} files)")
    print(f"  Total turns:    {totals['turns']}")
    print(f"  Total deaths:   {totals['deaths']}")
    print(f"  Total bugs:     {totals['bugs']}")
    print(f"  Unique rooms:   {len(totals['rooms'])}")
    if totals["xp_earned"]:
        xps = totals["xp_earned"]
        print(f"  XP range:       {min(xps)}–{max(xps)} (avg {sum(xps)//len(xps)})")
    if totals["style_data"]:
        print("  Style progression (peak values):")
        for sname, vals in sorted(totals["style_data"].items()):
            print(f"    {sname:<20} peak={max(vals):.1f}  avg={sum(vals)/len(vals):.1f}")


# ── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command")

    # play subcommand
    play = sub.add_parser("play", help="Run an AI playtest session")
    play.add_argument("--name",    default="AI_Tester", help="Character name")
    play.add_argument("--turns",   type=int, default=1000, help="Max turns")
    play.add_argument("--goal",    default="Explore the world, fight enemies, and try the bank system",
                      help="High-level goal for the AI")
    play.add_argument("--model",   default="claude-haiku-4-5-20251001", help="Claude model")
    play.add_argument("--out",     default=str(ROOT / "tools" / "ai_sessions"),
                      help="Output directory for session logs")
    play.add_argument("--verbose", action="store_true", help="Print AI reasoning to stdout")

    # analyse subcommand
    analyse = sub.add_parser("analyse", help="Aggregate stats from past sessions")
    analyse.add_argument("--dir", default=str(ROOT / "tools" / "ai_sessions"),
                         help="Directory containing .jsonl session files")

    args = p.parse_args()

    if args.command == "play":
        run_session(args)
    elif args.command == "analyse":
        analyse_sessions(Path(args.dir))
    else:
        p.print_help()


