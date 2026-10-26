"""Where slsbPatcher is installed, and the defaults that follow from it.

Installed as a mod in an MO2 instance (`<MO2>/mods/slsbPatcher/slsbPatcher.exe`), it reads that
instance's selected profile and writes its result as the mod `<MO2>/mods/slsbPatcher Output`,
which MO2 lists when the program closes. Anywhere else nothing is chosen for you.
"""
from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from . import gamedata

OUTPUT_MOD = "slsbPatcher Output"
SETTINGS = "slsbPatcher.settings.json"


def tool_folder() -> Path | None:
    """The folder of slsbPatcher.exe; SLSBPATCHER_HOME stands in for it when run from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    home = os.environ.get("SLSBPATCHER_HOME")
    return Path(home).resolve() if home else None


@lru_cache(maxsize=1)
def mo2() -> gamedata.Mo2Instance | None:
    folder = tool_folder()
    return gamedata.mo2_instance_of(folder) if folder else None


def default_inputs() -> list[Path]:
    """The MO2 instance itself: it is read with whatever profile MO2 has selected."""
    instance = mo2()
    return [instance.folder] if instance else []


def default_out() -> Path:
    instance = mo2()
    if instance:
        return instance.mods / OUTPUT_MOD
    return (tool_folder() or Path.cwd()) / OUTPUT_MOD


def settings_path() -> Path:
    """Beside the exe when installed (one settings file per install), else in %APPDATA%."""
    folder = tool_folder()
    if folder and os.access(folder, os.W_OK):
        return folder / SETTINGS
    return Path(os.environ.get("APPDATA") or Path.home()) / "slsbPatcher" / "settings.json"


def describe() -> str:
    """One line for the window: where it runs and where the result goes."""
    instance = mo2()
    if not instance:
        return ""
    vfs = " (started from MO2)" if gamedata.under_mo2_vfs() else ""
    return (f"Installed in the MO2 instance {instance.folder}{vfs}: it reads the selected profile "
            f"({instance.selected_profile()}) and writes the mod \"{OUTPUT_MOD}\". After a run, close slsbPatcher; "
            f"MO2 then lists that mod. Enable it and keep it at the bottom.")
