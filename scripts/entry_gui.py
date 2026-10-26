"""PyInstaller entry point for slsbPatcher.exe (the window, no console). Folders dropped on it fill the load order."""
import sys
from pathlib import Path

from slsbpatcher.gui import launch

sys.exit(launch([Path(arg) for arg in sys.argv[1:] if Path(arg).is_dir()]))
