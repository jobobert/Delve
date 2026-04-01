#!/usr/bin/env python3
"""
launch_cli.py — Start the Delve CLI frontend.

Usage:
    python launch_cli.py
    python launch_cli.py --admin
"""

import sys


def main():
    args = sys.argv[1:]
    admin_mode = "--admin" in args

    from frontend.cli import CLIFrontend
    CLIFrontend(admin_mode=admin_mode).run()


if __name__ == "__main__":
    main()
