"""slsbPatcher: bring SexLab P+ scene registries in line with your SLAL packs and SLATE action logs.

For every SLAL animation json in the load order:
  * a registry (.slr) that already converts it is PATCHED with the action logs, keeping
    its author's scenes, stage graph, ids and offsets, and only touching tags;
  * a json that no registry covers yet is CONVERTED, with slsb-convert.py's tagging and
    Scene Builder 3 (registry, FNIS lists and behavior files).
Everything is written to one output folder, to install as a mod with the highest priority.
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import __version__, actionlog, console, convert, gamedata, patcher, scenebuilder, slr

log = logging.getLogger("slsbpatcher")
MAX_NAMES = 40


@dataclass
class Registry:
    winner: gamedata.Winner
    data: bytes
    parsed: slr.SlrFile
    project: gamedata.Winner | None = None
    project_data: dict | None = None


@dataclass
class SlalJson:
    winner: gamedata.Winner
    animations: list[tuple[str, set[str]]]  # (name, events lower case)
    covered_by: dict[int, int] = field(default_factory=dict)  # registry index -> animations of this json it holds
    covered: int = 0

    def main_registry(self) -> int | None:
        """The registry holding most of this json's animations (others share a few events)."""
        return max(self.covered_by, key=lambda index: self.covered_by[index]) if self.covered_by else None


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="slsbpatcher",
        description="Patch existing SexLab P+ scene registries with SLATE action logs, and convert SLAL packs "
                    "that have no SLSB conversion yet.")
    parser.add_argument("inputs", nargs="+", type=Path, metavar="INPUT",
                        help="an MO2 profile or instance folder, a Data folder, or a folder of extracted packs. "
                             "Several are layered, the last one winning.")
    parser.add_argument("-o", "--out", type=Path,
                        help="output folder, installed as a mod (default: \"slsbPatcher Output\" in the current folder)")
    parser.add_argument("-l", "--logs", type=Path, action="append", default=[], metavar="PATH",
                        help="extra SLATE action log file or folder (e.g. Hentairim tag logs); repeatable. "
                             "Logs installed under SKSE/Plugins/SLATE are always read.")
    parser.add_argument("--no-installed-logs", action="store_true", help="ignore action logs found in the inputs")
    parser.add_argument("--skip-log", action="append", default=[], metavar="NAME",
                        help="ignore the installed action log with this file name (e.g. \"SLATE_ActionLog - OsmelMC.json\"); "
                             "repeatable")
    parser.add_argument("--official-tags", action="store_true",
                        help="when patching, also apply lines naming one of P+'s 55 official tags (skipped by default, "
                             "as SLATE NG does: P+ reads those per stage)")
    parser.add_argument("--no-convert", action="store_true", help="only patch existing registries")
    parser.add_argument("--no-patch", action="store_true", help="only convert SLAL packs that have no registry")
    parser.add_argument("--reconvert", action="append", default=[], metavar="NAME",
                        help="convert this SLAL json (file name or stem), pack or mod name even though a registry "
                             "covers it, reusing that registry's hash and ids; the new registry replaces it. Repeatable.")
    parser.add_argument("-u", "--update", type=Path, action="append", default=[], metavar="DIR",
                        help="folder of *.slsb.json from an earlier conversion, to keep its pack hash and scene ids")
    parser.add_argument("--author", help="pack author written into new conversions (slsb-convert.py -a)")
    parser.add_argument("--stricter-futa", action="store_true", help="slsb-convert.py -sf")
    parser.add_argument("--scene-builder", metavar="EXE", help="path to scene_builder_cli.exe")
    parser.add_argument("--zip", action="store_true", help="also pack the output folder into a .zip beside it")
    parser.add_argument("-n", "--dry-run", action="store_true", help="report what would change, write nothing")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"slsbPatcher {__version__}")
    return parser.parse_args(argv)


def _read_logs(data: gamedata.GameData, extra: list[Path], installed: bool, skip: list[str],
               official: bool) -> actionlog.ActionLogs:
    files = []
    skip_names = {name.lower() for name in skip}
    if installed:
        for winner in data.files("skse/plugins/slate/", ".json"):
            if winner.path.name.lower() in skip_names or winner.path.stem.lower() in skip_names:
                continue
            log_file = actionlog.read_log_file(winner.path, f"{winner.path.name} ({winner.layer.name})")
            if log_file:
                files.append(log_file)
    for path in extra:
        paths = sorted(p for p in path.iterdir() if p.suffix.lower() == ".json") if path.is_dir() else [path]
        for item in paths:
            log_file = actionlog.read_log_file(item)
            if log_file:
                files.append(log_file)
            elif path.is_file():
                log.warning("%s has no slate.actionlog list", item)
    return actionlog.build_logs(files, official)


def _load_registries(data: gamedata.GameData, problems: list[str]) -> list[Registry]:
    registries = []
    for winner in data.files("skse/sexlab/registry/", ".slr"):
        raw = winner.path.read_bytes()
        try:
            registries.append(Registry(winner, raw, slr.parse_slr(raw)))
        except slr.SlrError as error:
            problems.append(f"{winner.rel} ({winner.layer.name}): unreadable, {error}")
    projects = []
    for winner in data.files("skse/sexlab/registry/", ".slsb.json"):
        try:
            projects.append((winner, json.loads(winner.path.read_text(encoding="utf-8-sig"))))
        except (ValueError, UnicodeDecodeError) as error:
            problems.append(f"{winner.rel} ({winner.layer.name}): unreadable project, {error}")
    # A registry's project is the one whose scene ids it holds: file names differ
    # (FunnyBiznessBound.slsb.json builds FunnyBizBound.slr).
    for registry in registries:
        ids = {scene.id for scene in registry.parsed.scenes}
        for winner, project in projects:
            if ids and ids == patcher.project_scene_ids(project) and project.get("prefix_hash") == registry.parsed.prefix_hash:
                registry.project, registry.project_data = winner, project
                break
    return registries


def _load_slal(data: gamedata.GameData, registries: list[Registry], problems: list[str]) -> list[SlalJson]:
    event_owner: dict[str, set[int]] = {}
    for index, registry in enumerate(registries):
        for event in registry.parsed.events():
            event_owner.setdefault(event, set()).add(index)
    jsons = []
    for winner in data.files("slanims/json/", ".json"):
        try:
            raw = json.loads(convert._read_text(winner.path))
            animations = []
            for animation in raw.get("animations", []):
                events = {str(stage.get("id", "")).lower() for actor in animation.get("actors", [])
                          for stage in actor.get("stages", [])} - {""}
                animations.append((animation.get("name", ""), events))
        except (ValueError, AttributeError, TypeError) as error:
            problems.append(f"{winner.rel} ({winner.layer.name}): unreadable SLAL json, {error}")
            continue
        item = SlalJson(winner, animations)
        for _, events in animations:
            owners = set().union(*(event_owner.get(e, set()) for e in events)) if events else set()
            if owners:
                item.covered += 1
                for owner in owners:
                    item.covered_by[owner] = item.covered_by.get(owner, 0) + 1
        jsons.append(item)
    return jsons


def _matches(item: SlalJson, names: list[str]) -> bool:
    keys = {item.winner.path.name.lower(), item.winner.path.stem.lower(), item.winner.layer.name.lower()}
    return any(name.lower() in keys for name in names)


def _out_is_usable(out: Path) -> bool:
    """An output folder may be replaced only when it is empty or an earlier slsbPatcher output."""
    return not out.exists() or not any(out.iterdir()) or (out / gamedata.OUTPUT_MARKER[0] / gamedata.OUTPUT_MARKER[1]).is_file()


def main(argv=None, explorer: bool = False) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")
    if args.out is None:
        # Started from Explorer, the working folder is wherever Windows put it.
        args.out = (console.exe_folder() if explorer else Path.cwd()) / "slsbPatcher Output"
    if not args.dry_run and not _out_is_usable(args.out):
        print(f"{args.out} is not empty and is not an slsbPatcher output; pick another --out")
        return 2
    started = time.time()
    report: list[str] = []
    problems: list[str] = []

    data = gamedata.load(args.inputs)
    report.append(f"slsbPatcher {__version__}, {datetime.now():%Y-%m-%d %H:%M}")
    report.append(f"Inputs: {', '.join(str(p) for p in args.inputs)} ({len(data.layers)} mods/folders)")
    for layer in data.skipped_outputs:
        report.append(f"  skipped {layer.name}: an earlier slsbPatcher output, so its originals are patched instead")

    logs = _read_logs(data, args.logs, not args.no_installed_logs, args.skip_log, args.official_tags)
    report.append("")
    report.append(f"Action logs: {len(logs.files)}")
    for f in logs.files:
        report.append(f"  {f.label}: {len(f.lines)} lines, {f.adds} AddTag, {f.removes} RemoveTag ({f.stage_labels} stage labels); "
                      f"skipped {f.official_tags} P+ official tags, {f.disables} Disable, {f.comments} comments, {f.invalid} invalid")
    skipped_official = sum(f.official_tags for f in logs.files)

    registries = _load_registries(data, problems)
    slal = _load_slal(data, registries, problems)

    # Decide: patch what a registry covers, convert what nothing covers.
    to_convert: dict[int, convert.Job] = {}
    replaced: dict[int, str] = {}  # registry index -> json it is re-converted from
    partial = []
    for item in slal:
        reconvert = _matches(item, args.reconvert)
        if item.covered and not reconvert:
            if item.covered < len(item.animations):
                partial.append(item)
            continue
        if args.no_convert or not item.animations:
            continue
        layer_index = next(i for i, layer in enumerate(data.layers) if layer is item.winner.layer)
        job = to_convert.setdefault(layer_index, convert.Job(item.winner.layer, []))
        job.jsons.append(item.winner)
        if reconvert and item.covered_by:
            index = item.main_registry()
            if index in replaced:
                problems.append(f"--reconvert {item.winner.path.name}: {registries[index].winner.rel} is already replaced by "
                                f"{replaced[index]}; the new registry is written beside it")
            else:
                project = registries[index].project
                job.replace[item.winner.path.stem.lower()] = (registries[index].winner.rel, project.rel if project else "")
                replaced[index] = item.winner.path.name

    ops = logs.ops_by_scene()
    out_files: dict[str, bytes | Path] = {}
    matched: set[str] = set()

    # ---- patch
    report.append("")
    report.append(f"Existing conversions: {len(registries)} registries")
    up_to_date = 0
    if not args.no_patch:
        for index, registry in enumerate(registries):
            if index in replaced:
                report.append(f"  {registry.winner.rel}: re-converted from {replaced[index]}, not patched")
                continue
            patched, applied = patcher.patch_slr(registry.data, ops)
            matched |= applied.matched
            if patched == registry.data:
                up_to_date += 1
                continue
            out_files[registry.winner.rel] = patched
            report.append(f"  patched {registry.winner.rel} ({registry.winner.layer.name}): "
                          f"+{applied.added} -{applied.removed}")
            report.extend(f"      {change}" for change in applied.changes)
            if registry.project and registry.project_data is not None:
                project = json.loads(json.dumps(registry.project_data))
                if patcher.patch_project(project, ops):
                    out_files[registry.project.rel] = patcher.dump_project(project).encode("utf-8")
                    report.append(f"      and its project {registry.project.rel}")
        report.append(f"  {up_to_date} already up to date")

    # ---- convert
    report.append("")
    covered_jsons = [s for s in slal if s.covered and not _matches(s, args.reconvert)]
    report.append(f"SLAL animation jsons: {len(slal)}, {len(covered_jsons)} already converted, "
                  f"{sum(len(j.jsons) for j in to_convert.values())} to convert")
    all_events = set().union(*(registry.parsed.events() for registry in registries)) if registries else set()
    for item in partial:
        missing = [name for name, events in item.animations if not events & all_events]
        report.append(f"  {item.winner.path.name} ({item.winner.layer.name}): {item.covered} of {len(item.animations)} "
                      f"animations are in an installed registry; not in one: {', '.join(missing[:10])}"
                      f"{' ...' if len(missing) > 10 else ''} (--reconvert {item.winner.path.stem} adds them)")
    result = None
    if to_convert and not args.dry_run:
        previous: dict[str, dict] = {}
        for registry in registries:
            if registry.project_data:
                previous[registry.project_data.get("pack_name")] = registry.project_data
        for folder in args.update:
            for path in sorted(folder.rglob("*.slsb.json")):
                project = json.loads(path.read_text(encoding="utf-8-sig"))
                previous[project.get("pack_name")] = project
        exe = scenebuilder.find(args.scene_builder)
        work = Path(tempfile.mkdtemp(prefix="slsbpatcher-"))
        try:
            result = convert.run(list(to_convert.values()), data, work, exe, previous, logs.converter_entries,
                                 args.author, args.stricter_futa)
            for rel, path in result.files.items():
                if rel.lower() in {k.lower() for k in out_files}:
                    problems.append(f"{rel}: written by both the patch and a conversion; kept the conversion")
                out_files[rel] = path.read_bytes()
        finally:
            shutil.rmtree(work, ignore_errors=True)
        for produced in result.produced:
            report.append(f"  converted {produced.json_name} ({produced.module}) -> {produced.slr_rel}, {len(produced.scenes)} scenes")
            matched |= {actionlog.lower(actionlog.trim(name)) for name in produced.scenes}
        if replaced:
            report.append("  A re-conversion takes its tags from the SLAL json and the action logs read in this run only. "
                          "Pass the logs the earlier conversion was made with (--logs), or its tags from them are gone.")
        problems.extend(result.errors)
    elif to_convert:
        for job in to_convert.values():
            report.extend(f"  would convert {w.path.name} ({job.layer.name})" for w in job.jsons)

    unmatched = sorted(logs.scene_names() - matched)
    known = {actionlog.lower(actionlog.trim(s.name)) for r in registries for s in r.parsed.scenes}
    unmatched = [name for name in unmatched if name not in known]
    if unmatched:
        report.append("")
        report.append(f"{len(unmatched)} scenes named in the action logs are in no registry or conversion:")
        report.extend(f"  {name}" for name in unmatched[:MAX_NAMES])
        if len(unmatched) > MAX_NAMES:
            report.append(f"  ... and {len(unmatched) - MAX_NAMES} more")
    if skipped_official:
        report.append("")
        report.append(f"{skipped_official} action log lines name a P+ official tag and were not patched in "
                      "(pass --official-tags to apply them scene-wide). New conversions read them as slsb-convert.py does.")
    if problems:
        report.append("")
        report.append("Problems:")
        report.extend(f"  {p}" for p in problems)

    report.append("")
    if args.dry_run:
        report.append(f"Dry run: {len(out_files)} files would be written to {args.out}.")
    elif out_files:
        out = args.out.resolve()
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        for rel, content in sorted(out_files.items()):
            target = out / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        manifest = {"tool": "slsbPatcher", "version": __version__, "created": datetime.now().isoformat(timespec="seconds"),
                    "inputs": [str(p) for p in args.inputs], "files": sorted(out_files)}
        marker = out / gamedata.OUTPUT_MARKER[0]
        marker.mkdir(exist_ok=True)
        (marker / gamedata.OUTPUT_MARKER[1]).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        report.append(f"Wrote {len(out_files)} files to {out}")
        report.append("Install that folder as a mod with the highest priority (MO2: bottom of the left pane).")
        if result and result.produced:
            report.append("It holds new conversions: run Pandora (or Nemesis) afterwards. Keep the SLAL packs installed, "
                          "the conversions use their animation files.")
        (marker / "report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
        if args.zip:
            archive = out.with_suffix(".zip")
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
                for path in sorted(out.rglob("*")):
                    if path.is_file():
                        zf.write(path, path.relative_to(out).as_posix())
            report.append(f"Packed {archive}")
    else:
        report.append("Nothing to write: every registry is up to date and every SLAL pack is converted.")
    report.append(f"({time.time() - started:.1f} s)")
    print("\n".join(report))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
