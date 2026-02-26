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
    mode = "--cli"
    if len(sys.argv) > 1:
        mode = sys.argv[1]

    if mode == "--cli":
        from frontend.cli import CLIFrontend
        CLIFrontend().run()
    else:
        print(f"Unknown frontend '{mode}'. Available: --cli")
        sys.exit(1)


if __name__ == "__main__":
    main()
