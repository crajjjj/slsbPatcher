"""Behaviour for slsbPatcher.exe started from Explorer (double-click or a folder dropped on it)."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def started_from_explorer() -> bool:
    """True when this process owns its console window, i.e. nobody ran it from a prompt."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return False
    try:
        import ctypes
        processes = (ctypes.c_uint32 * 4)()
        return ctypes.windll.kernel32.GetConsoleProcessList(processes, 4) <= 1
    except (AttributeError, OSError):
        return False


def exe_folder() -> Path:
    return Path(sys.executable).resolve().parent


def pause() -> None:
    try:
        input("\nPress Enter to close this window.")
    except EOFError:
        pass


HOW_TO = """slsbPatcher patches SexLab P+ scene registries with SLATE action logs and converts
SLAL animation packs that have no SLSB conversion yet.

Drop a folder onto slsbPatcher.exe:
  * your MO2 profile folder (for example  MO2\\profiles\\Default), or
  * a folder holding extracted SLAL packs.
The result lands in "slsbPatcher Output" next to slsbPatcher.exe.

For every option (extra Hentairim logs, skipping a log, re-converting a pack), run it from
a command prompt:  slsbPatcher.exe --help
"""
