"""PyInstaller entry point for slsbPatcher-cli.exe (console)."""
import sys

from slsbpatcher.__main__ import run

sys.exit(run(window_without_args=False))
