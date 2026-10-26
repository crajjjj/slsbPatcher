"""slsbPatcher.log: what the last run did and any error, beside the exe (or in %TEMP%)."""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import traceback
from pathlib import Path

from . import __version__, gamedata, install

_handler: logging.Handler | None = None


def path() -> Path:
    folder = install.tool_folder()
    if folder and os.access(folder, os.W_OK):
        return folder / "slsbPatcher.log"
    return Path(tempfile.gettempdir()) / "slsbPatcher.log"


def start(mode: str) -> None:
    """Start a fresh log for this run and record where and how the program runs."""
    global _handler
    logger = logging.getLogger("slsbpatcher")
    try:
        _handler = logging.FileHandler(path(), mode="w", encoding="utf-8")
    except OSError:
        return
    _handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    _handler.setLevel(logging.DEBUG)
    logger.addHandler(_handler)
    logger.setLevel(logging.DEBUG)
    instance = install.mo2()
    logger.info("slsbPatcher %s (%s), %s", __version__, mode, " ".join(sys.argv[1:]) or "no arguments")
    logger.info("installed in MO2: %s; started from MO2: %s", instance.folder if instance else "no", gamedata.under_mo2_vfs())
    previous = sys.excepthook

    def hook(kind, value, tb):
        logger.error("".join(traceback.format_exception(kind, value, tb)))
        previous(kind, value, tb)
    sys.excepthook = hook


def write(lines: list[str]) -> None:
    if _handler:
        _handler.stream.write("\n".join(lines) + "\n")
        _handler.flush()
