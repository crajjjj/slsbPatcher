"""Locates and runs SexLab Scene Builder's headless CLI (scene_builder_cli, Scene Builder 3)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import i18n

_ = i18n._

EXE = "scene_builder_cli.exe" if os.name == "nt" else "scene_builder_cli"


class SceneBuilderError(RuntimeError):
    pass


def _base() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def find(explicit: str | None = None) -> Path:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("SLSB_CLI"):
        candidates.append(Path(os.environ["SLSB_CLI"]))
    base = _base()
    candidates += [
        base / "bin" / EXE,
        base / EXE,
        base / "lib" / "SexLab-Scene-Builder" / "target" / "release" / EXE,
    ]
    on_path = shutil.which("scene_builder_cli")
    if on_path:
        candidates.append(Path(on_path))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise SceneBuilderError(
        _("%(exe)s not found. Put it in %(folder)s, pass --scene-builder, or set SLSB_CLI.")
        % {"exe": EXE, "folder": base / "bin"})


def run(exe: Path, *args: str | Path) -> str:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # no console flashing up over the window
    result = subprocess.run([str(exe), *map(str, args)], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", creationflags=flags)
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        raise SceneBuilderError(_("scene_builder_cli %(command)s failed: %(output)s")
                                % {"command": args[0], "output": output})
    return output
