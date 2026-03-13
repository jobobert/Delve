#!/usr/bin/env python3
"""
main.py — Delve entry point.

Selects and launches a frontend. The engine itself has no knowledge of which
frontend is running; it communicates entirely through EventBus messages.

Usage:
    python main.py           # start the CLI frontend (default)
    python main.py --cli     # explicitly select CLI

Future frontends (not yet implemented):
    python main.py --web     # browser-based frontend via FastAPI + WebSockets
"""

import sys


def main():
    args = sys.argv[1:]
    admin_mode = "--admin" in args
    args = [a for a in args if a != "--admin"]

    mode = args[0] if args else "--cli"

    if mode == "--cli":
        from frontend.cli import CLIFrontend
        CLIFrontend(admin_mode=admin_mode).run()
    else:
        print(f"Unknown frontend '{mode}'. Available: --cli")
        sys.exit(1)


if __name__ == "__main__":
    main()




