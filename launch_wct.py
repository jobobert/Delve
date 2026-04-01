#!/usr/bin/env python3
"""
launch_wct.py — Start the World Creation Tool in a new terminal window and
open the browser automatically.

Usage:
    python launch_wct.py
    python launch_wct.py --port 8080

The WCT server (wct/wct_server.py) runs on port 7373 by default.
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
    parser = argparse.ArgumentParser(description="Launch the Delve World Creation Tool")
    parser.add_argument("--port", type=int, default=7373)
    args = parser.parse_args()

    server_script = ROOT / "wct" / "wct_server.py"
    url = f"http://localhost:{args.port}"

    if _port_in_use(args.port):
        print(f"WCT server already running on {url} — opening browser.")
    else:
        print(f"Starting WCT server on {url} ...")
        subprocess.Popen(
            [sys.executable, str(server_script), "--port", str(args.port)],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        time.sleep(1.5)

    webbrowser.open(url)
    print("Done. Browser opened.")


if __name__ == "__main__":
    main()
