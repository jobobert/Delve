"""
events.py — Lightweight publish/subscribe event bus for Delve.

The engine never calls print() or reads stdin directly. All output goes through
EventBus.emit(Event.OUTPUT, Msg(...)), and all frontends listen via subscribe().
This keeps the engine completely frontend-agnostic — the same engine code runs
unchanged under a CLI, a web server, or a test harness.

Typical wiring:
    bus = EventBus()
    bus.subscribe(Event.OUTPUT, lambda msg: render(msg))
    # ... engine emits, frontend renders ...

Event catalogue
───────────────
  Event.OUTPUT      Engine → Frontend: emit a Msg object for display
  Event.PLAYER_DIED Engine → Frontend: player HP reached 0
  Event.GAME_OVER   Engine → Frontend: session should end (reason string)
  Event.COMMAND_IN  Frontend → Engine: raw player input string (async/future use;
                    the CLI currently calls CommandProcessor.process() directly)
"""

from collections import defaultdict
from typing import Any, Callable


class EventBus:
    def __init__(self):
        self._handlers: dict[str, list[Callable]] = defaultdict(list)

    def subscribe(self, event: str, handler: Callable) -> None:
        """Register handler to be called when event is emitted."""
        self._handlers[event].append(handler)

    def unsubscribe(self, event: str, handler: Callable) -> None:
        """Remove a previously registered handler."""
        self._handlers[event].remove(handler)

    def emit(self, event: str, *args: Any, **kwargs: Any) -> None:
        """Fire all handlers registered for event."""
        for handler in self._handlers[event]:
            handler(*args, **kwargs)


class Event:
    """Well-known event name constants. Treat these as an immutable enum."""

    # Engine → Frontend
    OUTPUT       = "output"        # payload: Msg
    PLAYER_DIED  = "player_died"   # payload: (none)
    GAME_OVER    = "game_over"     # payload: reason str

    # Frontend → Engine (available for async/web use; CLI calls engine directly)
    COMMAND_IN   = "command_in"    # payload: raw_input str
