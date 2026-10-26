"""What slsbPatcher does, shared by the command line and the window.

For every SLAL animation json in the load order:
  * a registry (.slr) that already converts it is PATCHED with the action logs, keeping
    its author's scenes, stage graph, ids and offsets, and only touching tags;
  * a json that no registry covers yet is CONVERTED, with slsb-convert.py's tagging and
    Scene Builder 3 (registry, FNIS lists and behavior files).
Everything is written to one output folder, to install as a mod with the highest priority.

`scan()` reads the load order once; `execute()` decides and writes from a scan and options.
"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import __version__, actionlog, convert, gamedata, patcher, scenebuilder, slr

log = logging.getLogger("slsbpatcher")
MAX_NAMES = 40


@dataclass
class Options:
    inputs: list[Path]
    out: Path
    logs: list[Path] = field(default_factory=list)       # extra log files or folders
    installed_logs: bool = True
    skip_logs: list[str] = field(default_factory=list)   # installed log file names to leave out
    official_tags: bool = False
    patch: bool = True
    convert: bool = True
    reconvert: list[str] = field(default_factory=list)   # json name or stem, or mod name
    update: list[Path] = field(default_factory=list)     # folders of earlier *.slsb.json
    author: str | None = None
    stricter_futa: bool = False
    scene_builder: str | None = None
    zip: bool = False
    dry_run: bool = False
    skip_packs: list[str] = field(default_factory=list)       # SLAL json names (or stems) to leave alone
    skip_registries: list[str] = field(default_factory=list)  # registry paths or file names to leave alone


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

    def matches(self, names: list[str]) -> bool:
        keys = {self.winner.path.name.lower(), self.winner.path.stem.lower(), self.winner.layer.name.lower()}
        return any(name.lower() in keys for name in names)


@dataclass
class Scan:
    inputs: list[Path]
    data: gamedata.GameData
    installed_logs: list[actionlog.LogFile]
    extra_logs: list[actionlog.LogFile]
    registries: list[Registry]
    slal: list[SlalJson]
    problems: list[str]
    seconds: float = 0.0

    def registry_sources(self) -> dict[int, list[int]]:
        """For each registry (by index), the SLAL jsons (by index) it is the main registry of."""
        sources: dict[int, list[int]] = {}
        for index, item in enumerate(self.slal):
            main = item.main_registry()
            if main is not None:
                sources.setdefault(main, []).append(index)
        return sources


@dataclass
class PatchedRegistry:
    rel: str
    mod: str
    changes: list[patcher.TagChange]
    project_rel: str = ""

    @property
    def added(self) -> int:
        return sum(change.added for change in self.changes)

    @property
    def removed(self) -> int:
        return sum(not change.added for change in self.changes)

    @property
    def scenes(self) -> set[str]:
        return {change.scene for change in self.changes}


@dataclass
class Partial:
    json_name: str
    mod: str
    covered: int
    total: int
    missing: list[str]


@dataclass
class Outcome:
    options: Options
    logs: list[actionlog.LogFile] = field(default_factory=list)
    registries: int = 0
    patched: list[PatchedRegistry] = field(default_factory=list)
    up_to_date: int = 0
    produced: list[convert.Produced] = field(default_factory=list)
    would_convert: list[tuple[str, str]] = field(default_factory=list)  # (json, mod) in a preview
    partial: list[Partial] = field(default_factory=list)
    left_alone_packs: list[str] = field(default_factory=list)       # unticked SLAL jsons
    left_alone_registries: list[str] = field(default_factory=list)  # unticked registries (rel paths)
    unmatched: list[str] = field(default_factory=list)
    skipped_official: int = 0
    problems: list[str] = field(default_factory=list)
    written: int = 0
    out: Path | None = None
    summary: list[str] = field(default_factory=list)
    report: list[str] = field(default_factory=list)

    @property
    def converted(self) -> bool:
        return bool(self.produced)

    @property
    def code(self) -> int:
        return 1 if self.problems else 0


def read_extra_logs(paths: list[Path]) -> tuple[list[actionlog.LogFile], list[str]]:
    files, problems = [], []
    for path in paths:
        try:
            items = sorted(p for p in path.iterdir() if p.suffix.lower() == ".json") if path.is_dir() else [path]
        except OSError as error:
            problems.append(f"{path}: {error}")
            continue
        for item in items:
            try:
                log_file = actionlog.read_log_file(item)
            except OSError as error:
                problems.append(f"{item}: {error}")
                continue
            if log_file:
                files.append(log_file)
            elif path.is_file():
                problems.append(f"{item} has no slate.actionlog list")
    return files, problems


def _load_registries(data: gamedata.GameData, problems: list[str]) -> list[Registry]:
    registries = []
    for winner in data.files("skse/sexlab/registry/", ".slr"):
        try:
            raw = winner.path.read_bytes()
            registries.append(Registry(winner, raw, slr.parse_slr(raw)))
        except (OSError, slr.SlrError) as error:
            problems.append(f"{winner.rel} ({winner.layer.name}): unreadable, {error}")
    projects = []
    for winner in data.files("skse/sexlab/registry/", ".slsb.json"):
        try:
            projects.append((winner, json.loads(winner.path.read_text(encoding="utf-8-sig"))))
        except (OSError, ValueError, UnicodeDecodeError) as error:
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
        except (OSError, ValueError, AttributeError, TypeError) as error:
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


def scan(inputs: list[Path], extra_logs: list[Path]) -> Scan:
    started = time.time()
    problems: list[str] = []
    data = gamedata.load(inputs)
    installed = []
    for winner in data.files("skse/plugins/slate/", ".json"):
        try:
            log_file = actionlog.read_log_file(winner.path, f"{winner.path.name} ({winner.layer.name})")
        except OSError as error:
            problems.append(f"{winner.rel} ({winner.layer.name}): unreadable, {error}")
            continue
        if log_file:
            log_file.layer = winner.layer.name
            installed.append(log_file)
    extra, extra_problems = read_extra_logs(extra_logs)
    problems += extra_problems
    registries = _load_registries(data, problems)
    slal = _load_slal(data, registries, problems)
    return Scan(list(inputs), data, installed, extra, registries, slal, problems, time.time() - started)


def is_skipped(log_file: actionlog.LogFile, skip_logs: list[str]) -> bool:
    skip = {name.lower() for name in skip_logs}
    return log_file.path.name.lower() in skip or log_file.path.stem.lower() in skip


def selected_logs(found: Scan, options: Options) -> actionlog.ActionLogs:
    files = [f for f in found.installed_logs if options.installed_logs and not is_skipped(f, options.skip_logs)]
    files += [f for f in found.extra_logs if not is_skipped(f, options.skip_logs)]
    return actionlog.build_logs(files, options.official_tags)


def out_is_usable(out: Path) -> bool:
    """An output folder may be replaced only when it is empty or an earlier slsbPatcher output."""
    return not out.exists() or not any(out.iterdir()) or (out / gamedata.OUTPUT_MARKER[0] / gamedata.OUTPUT_MARKER[1]).is_file()


def _plural(count: int, one: str, many: str | None = None) -> str:
    return f"{count} {one if count == 1 else (many or one + 's')}"


def _write_output(options: Options, outcome: Outcome, out_files: dict[str, bytes]) -> None:
    out = options.out.resolve()
    if not out_is_usable(out):
        raise RuntimeError(f"{out} is not empty and is not an slsbPatcher output; pick another output folder")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for rel, content in sorted(out_files.items()):
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    manifest = {"tool": "slsbPatcher", "version": __version__, "created": datetime.now().isoformat(timespec="seconds"),
                "inputs": [str(p) for p in options.inputs], "files": sorted(out_files)}
    marker = out / gamedata.OUTPUT_MARKER[0]
    marker.mkdir(exist_ok=True)
    (marker / gamedata.OUTPUT_MARKER[1]).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    outcome.written, outcome.out = len(out_files), out


def execute(options: Options, found: Scan | None = None) -> Outcome:
    started = time.time()
    found = found or scan(options.inputs, options.logs)
    data, registries, slal = found.data, found.registries, found.slal
    outcome = Outcome(options, registries=len(registries), problems=list(found.problems))
    logs = selected_logs(found, options)
    outcome.logs = logs.files
    outcome.skipped_official = sum(f.official_tags for f in logs.files)

    # Decide: patch what a registry covers, convert what nothing covers, leave the unticked alone.
    to_convert: dict[int, convert.Job] = {}
    replaced: dict[int, str] = {}  # registry index -> json it is re-converted from
    all_events = set().union(*(registry.parsed.events() for registry in registries)) if registries else set()
    sources = found.registry_sources()
    skipped_packs = {name.lower() for name in options.skip_packs}
    skipped_registries = {name.lower() for name in options.skip_registries}

    def pack_skipped(item: SlalJson) -> bool:
        return item.winner.path.name.lower() in skipped_packs or item.winner.path.stem.lower() in skipped_packs

    def registry_skipped(index: int) -> bool:
        """Named outright, or the registry of SLAL jsons that are all left alone."""
        registry = registries[index]
        if registry.winner.rel.lower() in skipped_registries or registry.winner.path.name.lower() in skipped_registries:
            return True
        owners = sources.get(index, [])
        return bool(owners) and all(pack_skipped(slal[i]) for i in owners)

    for item in slal:
        if pack_skipped(item):
            outcome.left_alone_packs.append(item.winner.path.name)
            continue
        reconvert = item.matches(options.reconvert)
        if item.covered and not reconvert:
            if item.covered < len(item.animations):
                missing = [name for name, events in item.animations if not events & all_events]
                outcome.partial.append(Partial(item.winner.path.name, item.winner.layer.name, item.covered,
                                               len(item.animations), missing))
            continue
        if not options.convert or not item.animations:
            continue
        layer_index = next(i for i, layer in enumerate(data.layers) if layer is item.winner.layer)
        job = to_convert.setdefault(layer_index, convert.Job(item.winner.layer, []))
        job.jsons.append(item.winner)
        if reconvert and item.covered_by:
            index = item.main_registry()
            if index in replaced:
                outcome.problems.append(f"re-convert {item.winner.path.name}: {registries[index].winner.rel} is already "
                                        f"replaced by {replaced[index]}; the new registry is written beside it")
            else:
                project = registries[index].project
                job.replace[item.winner.path.stem.lower()] = (registries[index].winner.rel, project.rel if project else "")
                replaced[index] = item.winner.path.name

    ops = logs.ops_by_scene()
    out_files: dict[str, bytes] = {}
    matched: set[str] = set()

    # ---- patch
    if options.patch:
        for index, registry in enumerate(registries):
            if index in replaced:
                continue
            if registry_skipped(index):
                outcome.left_alone_registries.append(registry.winner.rel)
                continue
            patched, applied = patcher.patch_slr(registry.data, ops)
            matched |= applied.matched
            if patched == registry.data:
                outcome.up_to_date += 1
                continue
            out_files[registry.winner.rel] = patched
            entry = PatchedRegistry(registry.winner.rel, registry.winner.layer.name, applied.changes)
            if registry.project and registry.project_data is not None:
                project = json.loads(json.dumps(registry.project_data))
                if patcher.patch_project(project, ops):
                    out_files[registry.project.rel] = patcher.dump_project(project).encode("utf-8")
                    entry.project_rel = registry.project.rel
            outcome.patched.append(entry)

    # ---- convert
    if to_convert and options.dry_run:
        outcome.would_convert = [(w.path.name, job.layer.name) for job in to_convert.values() for w in job.jsons]
    elif to_convert:
        previous: dict[str, dict] = {}
        for registry in registries:
            if registry.project_data:
                previous[registry.project_data.get("pack_name")] = registry.project_data
        for folder in options.update:
            for path in sorted(folder.rglob("*.slsb.json")):
                try:
                    project = json.loads(path.read_text(encoding="utf-8-sig"))
                    previous[project.get("pack_name")] = project
                except (OSError, ValueError, UnicodeDecodeError) as error:
                    outcome.problems.append(f"{path}: not used as an earlier conversion, {error}")
        exe = scenebuilder.find(options.scene_builder)
        work = Path(tempfile.mkdtemp(prefix="slsbpatcher-"))
        try:
            result = convert.run(list(to_convert.values()), data, work, exe, previous, logs.converter_entries,
                                 options.author, options.stricter_futa)
            taken = {rel.lower() for rel in out_files}
            for rel, path in result.files.items():
                if rel.lower() in taken:
                    outcome.problems.append(f"{rel}: written by both the patch and a conversion; kept the conversion")
                out_files[rel] = path.read_bytes()
        finally:
            shutil.rmtree(work, ignore_errors=True)
        outcome.produced = result.produced
        outcome.problems.extend(result.errors)
        for produced in result.produced:
            matched |= {actionlog.lower(actionlog.trim(name)) for name in produced.scenes}

    known = {actionlog.lower(actionlog.trim(s.name)) for r in registries for s in r.parsed.scenes}
    outcome.unmatched = sorted(name for name in logs.scene_names() - matched if name not in known)

    if not options.dry_run and out_files:
        _write_output(options, outcome, out_files)
    outcome.summary = summarize(outcome, len(data.layers), bool(replaced), found.seconds + time.time() - started)
    outcome.report = outcome.summary + [""] + _details(outcome, data, replaced)
    if outcome.out:
        marker = outcome.out / gamedata.OUTPUT_MARKER[0]
        (marker / "report.txt").write_text("\n".join(outcome.report) + "\n", encoding="utf-8")
        if options.zip:
            archive = outcome.out.with_suffix(".zip")
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
                for path in sorted(outcome.out.rglob("*")):
                    if path.is_file():
                        zf.write(path, path.relative_to(outcome.out).as_posix())
    return outcome


def by_mod(patched: list[PatchedRegistry]) -> list[tuple[str, list[PatchedRegistry]]]:
    """Patched registries grouped by mod, the most changed mod first."""
    groups: dict[str, list[PatchedRegistry]] = {}
    for p in patched:
        groups.setdefault(p.mod, []).append(p)
    return sorted(groups.items(), key=lambda kv: -sum(len(p.changes) for p in kv[1]))


def tag_counts(patched: list[PatchedRegistry]) -> list[tuple[str, int]]:
    """`+tag` / `-tag` with how many scenes got that change, the most common first."""
    counts: dict[str, int] = {}
    for c in (c for p in patched for c in p.changes):
        key = f"{'+' if c.added else '-'}{c.tag}"
        counts[key] = counts.get(key, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def summarize(o: Outcome, layers: int, reconverted: bool, seconds: float) -> list[str]:
    """The high-level report: what was patched and converted, by mod, by log and by tag."""
    lines = [f"slsbPatcher {__version__}, {datetime.now():%Y-%m-%d %H:%M}", "", "SUMMARY"]
    log_lines = sum(len(f.lines) for f in o.logs)
    lines.append(f"  Read {_plural(len(o.logs), 'action log')} ({_plural(log_lines, 'line')}) and "
                 f"{_plural(o.registries, 'registry', 'registries')} from {_plural(layers, 'mod')}.")

    if not o.options.patch:
        lines.append("  Patching is off.")
    elif o.patched:
        changes = [c for p in o.patched for c in p.changes]
        added = sum(c.added for c in changes)
        scenes = sum(len(p.scenes) for p in o.patched)
        lines.append(f"  Patched {len(o.patched)} of {_plural(o.registries, 'registry', 'registries')}: "
                     f"{_plural(added, 'tag')} added, {len(changes) - added} removed, in {_plural(scenes, 'scene')}.")
        lines.append("    By mod:")
        for mod, items in by_mod(o.patched):
            lines.append(f"      {mod}: {_plural(len(items), 'registry', 'registries')}, "
                         f"+{sum(p.added for p in items)} -{sum(p.removed for p in items)}")
        lines.append("    By action log:")
        for f in o.logs:
            items = [c for c in changes if c.log == f.path.name]
            text = (f"+{sum(c.added for c in items)} -{sum(not c.added for c in items)}" if items
                    else "no changes (already in the registries, or for scenes not installed)")
            lines.append(f"      {f.path.name}: {text}")
        top = tag_counts(o.patched)
        shown = ", ".join(f"{tag} x{count}" for tag, count in top[:12])
        lines.append(f"    Tags: {shown}{f', and {len(top) - 12} more' if len(top) > 12 else ''}")
    elif not o.logs:
        lines.append("  Nothing to patch: no action logs were read.")
    elif not o.registries:
        lines.append("  Nothing to patch: no registries in the load order.")
    else:
        lines.append(f"  Nothing to patch: all {_plural(o.registries, 'registry', 'registries')} already match the logs.")

    if o.left_alone_packs or o.left_alone_registries:
        lines.append(f"  Left alone (unticked): {_plural(len(o.left_alone_packs), 'SLAL pack')}, "
                     f"{_plural(len(o.left_alone_registries), 'registry', 'registries')}.")

    if o.produced:
        scenes = sum(len(p.scenes) for p in o.produced)
        lines.append(f"  Converted {_plural(len(o.produced), 'SLAL json')} into new registries ({_plural(scenes, 'scene')}):")
        groups: dict[str, list[convert.Produced]] = {}
        for p in o.produced:
            groups.setdefault(p.module, []).append(p)
        for mod, items in groups.items():
            lines.append(f"      {mod}: {', '.join(p.slr_rel.rsplit('/', 1)[-1] for p in items)}")
        if reconverted:
            lines.append("    A re-conversion takes its tags only from the logs read in this run.")
    elif o.would_convert:
        names = ", ".join(name for name, _ in o.would_convert[:8])
        lines.append(f"  Would convert {_plural(len(o.would_convert), 'SLAL json')}: {names}"
                     f"{' ...' if len(o.would_convert) > 8 else ''}")
    elif not o.options.convert:
        lines.append("  Converting is off.")
    elif not o.partial:
        lines.append("  Every SLAL pack already has a conversion.")

    if o.partial:
        missing = sum(len(p.missing) for p in o.partial)
        names = ", ".join(f"{p.json_name} ({len(p.missing)})" for p in o.partial)
        lines.append(f"  {_plural(len(o.partial), 'SLAL json is', 'SLAL jsons are')} only partly converted "
                     f"({_plural(missing, 'animation')} in no registry; re-convert to add them): {names}")
    if o.unmatched:
        lines.append(f"  {_plural(len(o.unmatched), 'scene')} named in the logs are not installed (listed in the details).")
    if o.skipped_official:
        lines.append(f"  {_plural(o.skipped_official, 'log line')} naming P+ official tags were left out of the patch.")
    if o.problems:
        lines.append(f"  {_plural(len(o.problems), 'problem')}, listed at the end.")

    if o.options.dry_run:
        lines.append("  Preview only: nothing was written.")
    elif o.out:
        lines.append(f"  Wrote {_plural(o.written, 'file')} to {o.out}")
        lines.append("  Install that folder as a mod with the highest priority (MO2: bottom of the left pane).")
        lines.append("  Keep the mods it was made from enabled: the output holds only the files that changed.")
        if o.converted:
            lines.append("  It holds new conversions: run Pandora (or Nemesis) afterwards, and keep the SLAL packs installed.")
        if o.options.zip:
            lines.append(f"  Packed {o.out.with_suffix('.zip')}")
    else:
        lines.append("  Nothing to write.")
    lines.append(f"  ({seconds:.1f} s)")
    return lines


def _details(o: Outcome, data: gamedata.GameData, replaced: dict[int, str]) -> list[str]:
    lines = ["DETAILS", "", f"Inputs: {', '.join(str(p) for p in o.options.inputs)} ({len(data.layers)} mods/folders)"]
    if gamedata.under_mo2_vfs():
        lines.append("  started from MO2: its virtual file system is active")
    for layer in data.skipped_outputs:
        lines.append(f"  skipped {layer.name}: an earlier slsbPatcher output, so its originals are patched instead")
    lines += ["", f"Action logs: {len(o.logs)}"]
    for f in o.logs:
        lines.append(f"  {f.label}: {len(f.lines)} lines, {f.adds} AddTag, {f.removes} RemoveTag ({f.stage_labels} stage labels); "
                     f"skipped {f.official_tags} P+ official tags, {f.disables} Disable, {f.comments} comments, {f.invalid} invalid")
    lines += ["", f"Existing conversions: {o.registries} registries"]
    for name in replaced.values():
        lines.append(f"  re-converted from {name}, not patched")
    for p in o.patched:
        lines.append(f"  patched {p.rel} ({p.mod}): +{p.added} -{p.removed}")
        lines.extend(f"      {change}  [{change.log}]" for change in p.changes)
        if p.project_rel:
            lines.append(f"      and its project {p.project_rel}")
    if o.options.patch:
        lines.append(f"  {o.up_to_date} already up to date")
    for rel in o.left_alone_registries:
        lines.append(f"  {rel}: left alone (unticked)")
    lines += ["", "SLAL animation jsons:"]
    for name in o.left_alone_packs:
        lines.append(f"  {name}: left alone (unticked)")
    for p in o.produced:
        lines.append(f"  converted {p.json_name} ({p.module}) -> {p.slr_rel}, {len(p.scenes)} scenes")
    for name, mod in o.would_convert:
        lines.append(f"  would convert {name} ({mod})")
    for p in o.partial:
        lines.append(f"  {p.json_name} ({p.mod}): {p.covered} of {p.total} animations are in an installed registry; "
                     f"not in one: {', '.join(p.missing[:10])}{' ...' if len(p.missing) > 10 else ''}")
    if o.unmatched:
        lines += ["", f"{len(o.unmatched)} scenes named in the action logs are in no registry or conversion:"]
        lines.extend(f"  {name}" for name in o.unmatched[:MAX_NAMES])
        if len(o.unmatched) > MAX_NAMES:
            lines.append(f"  ... and {len(o.unmatched) - MAX_NAMES} more")
    if o.skipped_official:
        lines += ["", f"{o.skipped_official} action log lines name a P+ official tag and were not patched in "
                      "(turn on official tags to apply them scene-wide). New conversions read them as slsb-convert.py does."]
    if o.problems:
        lines += ["", "Problems:"] + [f"  {p}" for p in o.problems]
    return lines
