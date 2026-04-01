#!/usr/bin/env python3
"""
launch_web.py — Start the game web frontend server in a new terminal window
and open the browser automatically.

Usage:
    python launch_web.py
    python launch_web.py --port 8080

The web frontend server (frontend/web_server.py) runs on port 7374 by default.
For the World Creation Tool, use launch_wct.py instead.
"""

import argparse
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).parent


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("localhost", port)) == 0


def main():
    parser = argparse.ArgumentParser(description="Launch the Delve game web frontend")
    parser.add_argument("--port", type=int, default=7374)
    args = parser.parse_args()

    server_script = ROOT / "frontend" / "web_server.py"
    url = f"http://localhost:{args.port}"

    if _port_in_use(args.port):
        print(f"Web frontend server already running on {url} — opening browser.")
    else:
        print(f"Starting web frontend server on {url} ...")
        subprocess.Popen(
            [sys.executable, str(server_script), "--port", str(args.port)],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        time.sleep(1.5)

    webbrowser.open(url)
    print("Done. Browser opened.")


if __name__ == "__main__":
    main()
