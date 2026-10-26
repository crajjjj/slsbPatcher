"""Behaviour for slsbPatcher.exe started from Explorer (double-click or a folder dropped on it)."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def started_from_explorer() -> bool:
    """True when no shell shares this console, i.e. nobody ran the exe from a prompt.

    A one-file PyInstaller exe is two processes on the console (the bootloader and the
    program); from a prompt the shell is a third.
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return False
    try:
        import ctypes
        processes = (ctypes.c_uint32 * 8)()
        return ctypes.windll.kernel32.GetConsoleProcessList(processes, 8) <= 2
    except (AttributeError, OSError):
        return False


def exe_folder() -> Path:
    return Path(sys.executable).resolve().parent


def pause() -> None:
    try:
        input("\nPress Enter to close this window.")
    except EOFError:
        pass
