"""What the game would see: the winning copy of every file slsbPatcher reads.

Inputs are layers, lowest priority first. A layer is a folder laid out like Skyrim's
Data folder. Three kinds of input become layers:

* an MO2 profile (or instance) folder: its enabled mods in priority order, the game's
  own Data folder below them and the overwrite folder above;
* a folder laid out like Data (it has SLAnims, SKSE or meshes in it);
* any other folder: every folder below it that holds SLAnims or SKSE becomes its own
  layer, the way slsb-convert.py treats its SLAL_Packs folder.

Only loose files are seen; animation packs packed into a BSA are not.
"""
from __future__ import annotations

import configparser
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

OUTPUT_MARKER = ("slsbPatcher", "manifest.json")  # inside our own output; that layer is skipped


@dataclass(eq=False)
class Layer:
    name: str
    root: Path
    keys: set[str] = field(default_factory=set)  # lower-case Data-relative paths this layer has


@dataclass
class Winner:
    layer: Layer
    path: Path
    rel: str  # Data-relative, forward slashes, original case


@dataclass
class GameData:
    layers: list[Layer] = field(default_factory=list)
    skipped_outputs: list[Layer] = field(default_factory=list)
    _files: dict[str, Winner] = field(default_factory=dict)

    def add(self, layer: Layer) -> None:
        if _child(layer.root, *OUTPUT_MARKER):
            self.skipped_outputs.append(layer)
            return
        self.layers.append(layer)
        for path in _interesting_files(layer.root):
            rel = path.relative_to(layer.root).as_posix()
            layer.keys.add(rel.lower())
            self._files[rel.lower()] = Winner(layer, path, rel)

    def files(self, prefix: str, suffix: str = "", layer: Layer | None = None) -> list[Winner]:
        """Winning files under `prefix`; with `layer`, only paths that layer also has."""
        prefix, suffix = prefix.lower(), suffix.lower()
        keys = layer.keys if layer else self._files.keys()
        return sorted((self._files[key] for key in keys if key.startswith(prefix) and key.endswith(suffix)),
                      key=lambda w: w.rel.lower())


def _child(folder: Path, *names: str) -> Path | None:
    """`folder/names...` with every part matched case-insensitively, or None."""
    current = folder
    for name in names:
        try:
            entries = os.scandir(current)
        except OSError:
            return None
        with entries:
            match = next((Path(e.path) for e in entries if e.name.lower() == name.lower()), None)
        if match is None:
            return None
        current = match
    return current


def _walk(folder: Path):
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames.sort()
        for name in sorted(filenames):
            yield Path(dirpath, name)


def _interesting_files(root: Path):
    slanims = _child(root, "SLAnims")
    if slanims:
        for sub, suffix in (("json", ".json"), ("source", ".txt")):
            folder = _child(slanims, sub)
            if folder:
                for path in sorted(folder.iterdir()):
                    if path.is_file() and path.suffix.lower() == suffix:
                        yield path
        # FNIS lists of a SLAL pack, meshes/actors/<race>/animations/<pack>/FNIS_*_List.txt.
        # Only packs carry them, so other mods' (large) meshes folders are not walked.
        actors = _child(root, "meshes", "actors")
        if actors:
            for path in _walk(actors):
                name = path.name.lower()
                if name.startswith("fnis_") and name.endswith("_list.txt") and path.parent.parent.name.lower() == "animations":
                    yield path
    registry = _child(root, "SKSE", "SexLab", "Registry")
    if registry:
        for path in _walk(registry):
            name = path.name.lower()
            if name.endswith(".slr") or name.endswith(".slsb.json"):
                yield path
    slate = _child(root, "SKSE", "Plugins", "SLATE")
    if slate:
        for path in sorted(slate.iterdir()):
            if path.is_file() and path.suffix.lower() == ".json":
                yield path


def _looks_like_data(folder: Path) -> bool:
    return any(_child(folder, name) for name in ("SLAnims", "SKSE", "meshes"))


# ---------------------------------------------------------------------------------- MO2

def _ini_value(raw: str) -> str:
    raw = raw.strip()
    match = re.fullmatch(r"@ByteArray\((.*)\)", raw)
    if match:
        raw = match.group(1)
    return raw.replace("\\\\", "\\").strip('"')


def _read_mo2_ini(instance: Path) -> dict[str, dict[str, str]]:
    ini = _child(instance, "ModOrganizer.ini")
    if not ini:
        return {}
    parser = configparser.RawConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    try:
        parser.read(ini, encoding="utf-8-sig")
    except (configparser.Error, UnicodeDecodeError):
        return {}
    return {section: {k: _ini_value(v) for k, v in parser.items(section)} for section in parser.sections()}


def _mo2_dir(settings: dict[str, str], key: str, base: Path, default: str) -> Path:
    value = settings.get(key, "")
    if not value:
        return base / default
    value = value.replace("%BASE_DIR%", str(base))
    path = Path(value)
    return path if path.is_absolute() else base / path


def mo2_layers(folder: Path) -> list[Layer] | None:
    """The layers of an MO2 profile or instance folder, lowest priority first, or None."""
    profile = folder if _child(folder, "modlist.txt") else None
    instance = folder.parent.parent if profile else folder
    ini = _read_mo2_ini(instance)
    if profile is None:
        if not ini:
            return None
        settings = ini.get("Settings", {})
        base = _mo2_dir(settings, "base_directory", instance, ".")
        name = ini.get("General", {}).get("selected_profile", "Default")
        profile = _child(_mo2_dir(settings, "profiles_directory", base, "profiles"), name)
        if not profile or not _child(profile, "modlist.txt"):
            return None
    settings = ini.get("Settings", {})
    base = _mo2_dir(settings, "base_directory", instance, ".")
    mods_dir = _mo2_dir(settings, "mod_directory", base, "mods")
    overwrite = _mo2_dir(settings, "overwrite_directory", base, "overwrite")

    enabled = []
    for line in _child(profile, "modlist.txt").read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if line.startswith("+") and not line.endswith("_separator"):
            enabled.append(line[1:])

    layers = []
    game = ini.get("General", {}).get("gamePath", "")
    if game:
        data = _child(Path(game), "Data")
        if data:
            layers.append(Layer("(game Data)", data))
    for name in reversed(enabled):  # modlist.txt lists the highest priority first
        root = mods_dir / name
        if root.is_dir():
            layers.append(Layer(name, root))
    if overwrite.is_dir():
        layers.append(Layer("(overwrite)", overwrite))
    return layers


def layers_for_input(folder: Path) -> list[Layer]:
    folder = folder.resolve()
    if not folder.is_dir():
        raise FileNotFoundError(f"not a folder: {folder}")
    mo2 = mo2_layers(folder)
    if mo2 is not None:
        return mo2
    if _looks_like_data(folder):
        return [Layer(folder.name, folder)]
    layers = []
    for dirpath, dirnames, _ in os.walk(folder):
        dirnames.sort()
        here = Path(dirpath)
        if here != folder and (_child(here, "SLAnims") or _child(here, "SKSE")):
            dirnames.clear()  # a pack's own subfolders are part of it
            parts = here.relative_to(folder).parts
            if not any(part.startswith("!") for part in parts):  # slsb-convert.py's way to park a pack
                layers.append(Layer("/".join(parts), here))
        elif len(here.relative_to(folder).parts) >= 4:
            dirnames.clear()
    return layers


def load(inputs: list[Path]) -> GameData:
    data = GameData()
    for folder in inputs:
        for layer in layers_for_input(folder):
            data.add(layer)
    return data
