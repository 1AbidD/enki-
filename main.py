#!/usr/bin/env python3
"""Entrypoint utama untuk ENKI CLI."""

import sys
from pathlib import Path

# Pastikan encoding UTF-8 di Windows console
if sys.platform == "win32":
    if sys.stdin and hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if sys.stderr and hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

from enki.cli.console import EnkiConsole

def main():
    app = EnkiConsole()
    app.run()

if __name__ == "__main__":
    main()
