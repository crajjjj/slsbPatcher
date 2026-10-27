"""Fresh SLAL to SLSB conversions, driving the vendored slsb-convert.py.

The tag, position and furniture logic is slsb-convert.py's own (vendor/slsb_convert.py,
unchanged). This module replaces its plumbing (`ConvertMain.do_convert_single`):

* a pack is copied into a staging folder first, so the converter's in-place json fixes
  never touch the installed pack;
* Scene Builder 3's CLI does convert / build, and its own behavior generator replaces
  FNIS for Modders, hkxconv and the base game replica;
* two things Scene Builder 3's `convert` does that the Scene Builder 2 exe the script
  was written against did not are undone before the script edits the project, so the
  script sees the input it expects (see `_as_scene_builder_2`);
* scene AND stage ids of an earlier conversion are kept (the script keeps scene ids only).
"""
from __future__ import annotations

import io
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import i18n, scenebuilder
from .gamedata import GameData, Layer, Winner
from .vendor import slsb_convert as sc

_ = i18n._

log = logging.getLogger("slsbpatcher")


@dataclass
class Job:
    layer: Layer                  # the mod (pack) the SLAL jsons come from
    jsons: list[Winner]
    # json stem (lower) -> (registry rel path, its project's rel path or "") the conversion overwrites
    replace: dict[str, tuple[str, str]] = field(default_factory=dict)


@dataclass
class Produced:
    module: str
    json_name: str
    pack_name: str
    slr_rel: str
    scenes: list[str]


@dataclass
class Result:
    files: dict[str, Path] = field(default_factory=dict)  # Data-relative path -> file in the work folder
    produced: list[Produced] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _stage(job: Job, data: GameData, folder: Path) -> list[Path]:
    """Copy what the converter reads of one pack into `folder`, as UTF-8 without BOM."""
    staged = []
    for winner in job.jsons:
        target = folder / winner.rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_read_text(winner.path), encoding="utf-8")
        staged.append(target)
    for winner in data.files("slanims/source/", ".txt", job.layer) + data.files("meshes/actors/", "_list.txt", job.layer):
        target = folder / winner.rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_read_text(winner.path), encoding="utf-8")
    return staged


def _as_scene_builder_2(temp_dir: Path) -> None:
    """Undo what Scene Builder 3's `convert` adds over Scene Builder 2's.

    SB3 marks a strap-on position futa and fills the scene-level tag list; SB2 did
    neither, and slsb-convert.py decides futa (allow_flexible_futa, the futa flags) and
    writes tags itself. SB3 also copies SLAL stage timers and sounds, but it accepts a
    stage number as 1-based or 0-based, so a timer on stage 3 lands on stages 3 and 4;
    SB2 copied neither, and the script sets the timers itself from the SLAL json. Offsets
    are cleared as SB2 left them; the script sets every non-zero one again. The other
    SB3 additions (open_mouth, silent, strap_on, schlong, add_cum) are project-json-only.
    """
    for path in temp_dir.glob("*.slsb.json"):
        project = json.loads(path.read_text(encoding="utf-8"))
        for scene in project.get("scenes", {}).values():
            scene["tags"] = []
            for position in scene.get("positions", []):
                position.setdefault("sex", {})["futa"] = False
            for stage in scene.get("stages", []):
                extra = stage.setdefault("extra", {})
                extra["fixed_len"] = 0.0
                extra.pop("sound", None)
                for position in stage.get("positions", []):
                    position["offset"] = {"x": 0.0, "y": 0.0, "z": 0.0, "r": 0.0}
        path.write_text(json.dumps(project, indent=2), encoding="utf-8")


def _first_event(stage: dict) -> str:
    try:
        return stage["positions"][0]["event"][0]
    except (KeyError, IndexError, TypeError):
        return ""


def _keep_stage_ids(edited_dir: Path, previous: dict[str, dict]) -> int:
    """Reuse an earlier conversion's stage ids for scenes whose scene id was reused.

    P+ keys saved per-stage offsets by stage id, so a new id loses them. A stage keeps
    its old id only when the scene has the same number of stages and every stage starts
    on the same animation event.
    """
    kept = 0
    for path in edited_dir.glob("*.slsb.json"):
        project = json.loads(path.read_text(encoding="utf-8"))
        old_project = previous.get(project.get("pack_name"))
        if not old_project:
            continue
        old_scenes = {scene.get("id", key): scene for key, scene in old_project.get("scenes", {}).items()}
        changed = False
        for scene in project.get("scenes", {}).values():
            old = old_scenes.get(scene.get("id"))
            if not old or len(old.get("stages", [])) != len(scene.get("stages", [])):
                continue
            pairs = list(zip(scene["stages"], old["stages"]))
            if any(_first_event(new) != _first_event(prev) for new, prev in pairs):
                continue
            mapping = {new["id"]: prev["id"] for new, prev in pairs}
            if all(a == b for a, b in mapping.items()):
                continue
            for new, prev in pairs:
                new["id"] = prev["id"]
            scene["root"] = mapping.get(scene.get("root"), scene.get("root"))
            scene["graph"] = {mapping.get(k, k): {**node, "dest": [mapping.get(d, d) for d in node.get("dest", [])]}
                              for k, node in scene.get("graph", {}).items()}
            kept += len(pairs)
            changed = True
        if changed:
            path.write_text(json.dumps(project, indent=2), encoding="utf-8")
    return kept


_OPTIONS = re.compile(r"^(\S+) -(\S+) (.*)$")


def _drop_md(module_out: Path) -> None:
    """SB3 writes `-md` into every FNIS line; SB2 did not, and the script appends it."""
    for path in module_out.rglob("*"):
        name = path.name.lower()
        if not (path.is_file() and name.startswith("fnis_") and name.endswith("_list.txt")):
            continue
        lines = []
        for line in path.read_text(encoding="utf-8").splitlines():
            match = _OPTIONS.match(line)
            if match:
                options = [o for o in match.group(2).split(",") if o.lower() != "md"]
                line = f"{match.group(1)} -{','.join(options)} {match.group(3)}" if options else f"{match.group(1)} {match.group(3)}"
            lines.append(line)
        path.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))


def _slr_stem(project: dict) -> str:
    """Scene Builder's Package::slr_file_stem (with sanitize_slr_version)."""
    name = (project.get("pack_name") or "").strip() or project.get("prefix_hash", "")
    version = "".join(c if (c.isascii() and c.isalnum()) or c in "_-." else "_"
                      for c in (project.get("pack_version") or "").strip()).strip("_.")
    version = "_".join(part for part in version.split("_") if part)
    return f"{name}_{version}" if version else name


def _configure_converter(exe: Path, temp_dir: Path, author: str | None, stricter_futa: bool,
                         previous: dict[str, dict], entries: list[dict]) -> None:
    sc.Arguments.slsb_path = exe
    sc.Arguments.temp_dir = temp_dir
    sc.Arguments.author = author or "Unknown"
    sc.Arguments.stricter_futa = stricter_futa
    sc.Arguments.debug = staticmethod(lambda *content: log.debug(" ".join(map(str, content))))
    sc.StoredData.slsb_jsons_data.clear()
    for project in previous.values():
        sc.Parsers.parse_slsb_jsons(io.StringIO(json.dumps(project)))
    sc.StoredData.slate_logs_data[:] = entries
    sc.StoredData.cached_variables.clear()
    sc.StoredData.cached_variables["action_logs_found"] = bool(entries)
    sc.StoredData.processed_slal_modules.clear()


def run(jobs: list[Job], data: GameData, work: Path, exe: Path, previous: dict[str, dict], entries: list[dict],
        author: str | None = None, stricter_futa: bool = False) -> Result:
    result = Result()
    temp_dir = work / "tmp_slsb_dir"
    _configure_converter(exe, temp_dir, author, stricter_futa, previous, entries)
    for index, job in enumerate(jobs):
        module = work / "stage" / f"{index:03d}"
        module_out = work / "out" / f"{index:03d}"
        try:
            log.info(_("converting %(mod)s: %(jsons)s") % {
                "mod": job.layer.name, "jsons": ", ".join(w.path.name for w in job.jsons)})
            shutil.rmtree(temp_dir, ignore_errors=True)
            (temp_dir / "edited").mkdir(parents=True)
            staged = _stage(job, data, module)
            sc.StoredData.reset_stored_data()
            sc.ConvertUtils.execute_slal_parsers(module)
            sc.Editors.fix_slal_jsons(module)
            for path in staged:
                scenebuilder.run(exe, "convert", "--in", path, "--out", temp_dir)
            _as_scene_builder_2(temp_dir)
            sc.PackageProcessor.edit_slsb_json()
            kept = _keep_stage_ids(temp_dir / "edited", previous)
            if kept:
                log.info(_("  kept %(n)d stage ids of the earlier conversion") % {"n": kept})

            registry = module_out / "SKSE" / "SexLab" / "Registry"
            module_out.mkdir(parents=True, exist_ok=True)
            for path in sorted((temp_dir / "edited").glob("*.slsb.json")):
                project = json.loads(path.read_text(encoding="utf-8"))
                scenebuilder.run(exe, "build", "--in", path, "--out", module_out)
                json_stem = path.name[:-len(".slsb.json")]
                slr_rel = f"SKSE/SexLab/Registry/{_slr_stem(project)}.slr"
                source_rel = f"SKSE/SexLab/Registry/Source/{path.name}"
                target, target_source = job.replace.get(json_stem.lower(), ("", ""))
                if target:
                    # A re-conversion takes the place of the registry it replaces, so the
                    # old file is overridden instead of loading next to the new one.
                    built = module_out / slr_rel
                    slr_rel = target
                    (module_out / target).parent.mkdir(parents=True, exist_ok=True)
                    built.replace(module_out / target)
                    source_rel = target_source or source_rel
                (module_out / source_rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, module_out / source_rel)
                scenes = sorted(scene.get("name", "") for scene in project.get("scenes", {}).values())
                result.produced.append(Produced(job.layer.name, f"{json_stem}.json", project.get("pack_name", ""), slr_rel, scenes))

            actors = module_out / "meshes" / "actors"
            if actors.is_dir():
                _drop_md(module_out)
                sc.ConvertUtils.iter_fnis_lists(actors, None, sc.Editors.edit_output_fnis)
                scenebuilder.run(exe, "generate-behaviors", "--in", module_out)

            taken = {rel.lower() for rel in result.files}
            for path in sorted(p for p in module_out.rglob("*") if p.is_file()):
                rel = path.relative_to(module_out).as_posix()
                if rel.lower() in taken:
                    result.errors.append(_("%(mod)s: %(rel)s is also produced by another pack; kept the first one")
                                         % {"mod": job.layer.name, "rel": rel})
                    continue
                result.files[rel] = path
        except Exception as error:  # one broken pack must not stop the others
            log.debug("conversion failed", exc_info=True)
            result.errors.append(_("%(mod)s: conversion failed: %(error)s")
                                 % {"mod": job.layer.name, "error": error})
        finally:
            sc.StoredData.reset_stored_data()
    shutil.rmtree(temp_dir, ignore_errors=True)
    return result
