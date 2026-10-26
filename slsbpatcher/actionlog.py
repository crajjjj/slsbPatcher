"""SLATE action logs: the `slate.actionlog` string list in a PapyrusUtil JSON file.

Each line is `Action,Scene name,Tag`. Two readings are produced from the same lines:

* `PatchOp`s with SLATE NG's semantics (src/core/ActionLog.cpp), used to patch scene
  registries that already exist: tags normalized the way Scene Builder compiles them,
  the converter's spelling fixes applied, P+ official tags skipped unless asked for.
* the raw `{action, anim, tag}` entries slsb-convert.py builds in
  `Parsers.parse_slate_actionlogs`, used for fresh conversions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# The label vocabularies slsb-convert.py understands.
STAGE_CODES = frozenset({
    "en", "li", "sb", "fb", "sv", "fv", "sa", "fa", "sr", "dp", "tp", "ba", "bv",  # SexLab Anim Stage Labels
    "na", "ks", "ss", "ms", "fs", "rs", "sc", "mc", "fc",                          # ASL sound effects
})
POSITION_CODES = frozenset({  # Hentairim
    "ldi", "kis", "eno", "eni", "sst", "fst", "bst", "cun", "sbj", "fbj", "svp", "fvp", "sap", "fap", "scg", "fcg",
    "sac", "fac", "sdp", "fdp", "sdv", "fdv", "sda", "fda", "shj", "fhj", "stf", "ftf", "smf", "fmf", "sfj", "ffj",
})

# The spelling fixes slsb-convert.py applies to SLAL tags. A log line using the old
# spelling means the tag a converted registry already carries under the new one.
ALIASES = {
    "laying": "lying",
    "invfurn": "invisfurn",
    "invisibleobj": "invisfurn",
    "cunnilingius": "cunnilingus",
    "agressive": "aggressive",
    "femodm": "femdom",
}

# Registry::TagTable in SexLab P+, lower case.
OFFICIAL_TAGS = frozenset({
    "sixtynine", "anal", "asphyxiation", "blowjob", "boobjob", "breastsucking", "buttjob", "cowgirl",
    "cunnilingus", "deepthroat", "doggy", "dominant", "doublepenetration", "facesitting", "facial", "feet",
    "fingering", "fisting", "footjob", "forced", "grinding", "handjob", "humiliation", "leadin", "lotusposition",
    "masturbation", "missionary", "oral", "penetration", "pronebone", "reversecowgirl", "reversespitroast",
    "rimming", "spanking", "spitroast", "teasing", "toys", "tribadism", "triplepenetration", "vaginal",
    "holding", "spooning", "hugging", "kissing", "sitting", "kneeling", "standing", "loving", "lying", "behind",
    "facing", "magic", "ryona", "gore", "oviposition",
})

_STAGE_LABEL = re.compile(r"^(\d{1,3})(.+)$")


def lower(text: str) -> str:
    """ASCII lower case, the way Papyrus strings compare."""
    return text.translate(_ASCII_LOWER)


_ASCII_LOWER = {c: c + 32 for c in range(ord("A"), ord("Z") + 1)}


def trim(text: str) -> str:
    return text.strip(" \t\r\n")


def normalize_tag(tag: str) -> str:
    """Tags as Scene Builder compiles them: lower case, no spaces ("Leg Up" is stored as "legup")."""
    return lower(tag).replace(" ", "").replace("\t", "")


def canonical(tag: str) -> str:
    return ALIASES.get(tag, tag)


def stage_of_label(tag: str) -> int:
    """The stage a label like `2fv` or `3asvp` names, or 0 when the tag is no stage label."""
    match = _STAGE_LABEL.match(tag)
    if not match:
        return 0
    code = match.group(2)
    if (len(code) == 2 and code in STAGE_CODES) or (len(code) == 4 and code[0] in "abcde" and code[1:] in POSITION_CODES):
        return int(match.group(1))
    return 0


@dataclass
class PatchOp:
    remove: bool
    scene: str  # lower case, trimmed
    tag: str    # normalized, canonical
    stage: int  # 1-based stage a label names, 0 for a scene-wide tag
    source: str = ""


@dataclass
class LogFile:
    path: Path
    label: str
    lines: list[str]
    adds: int = 0
    removes: int = 0
    stage_labels: int = 0
    official_tags: int = 0
    disables: int = 0
    comments: int = 0
    invalid: int = 0
    layer: str = ""  # the mod an installed log comes from; empty for one added by hand


@dataclass
class ActionLogs:
    files: list[LogFile] = field(default_factory=list)
    ops: list[PatchOp] = field(default_factory=list)
    converter_entries: list[dict] = field(default_factory=list)

    def ops_by_scene(self) -> dict[str, list[PatchOp]]:
        by_scene: dict[str, list[PatchOp]] = {}
        for op in self.ops:
            by_scene.setdefault(op.scene, []).append(op)
        return by_scene

    def scene_names(self) -> set[str]:
        return {op.scene for op in self.ops}


class _Scanner:
    """Finds a JSON string array by key without a full parse, skipping // and /* */ comments."""

    def __init__(self, text: str):
        self.text = text
        self.pos = 1 if text.startswith("﻿") else 0

    def at_end(self) -> bool:
        return self.pos >= len(self.text)

    def skip_blank(self) -> None:
        text = self.text
        while self.pos < len(text):
            ch = text[self.pos]
            if ch in " \t\r\n":
                self.pos += 1
            elif text.startswith("/*", self.pos):
                end = text.find("*/", self.pos + 2)
                self.pos = len(text) if end < 0 else end + 2
            elif text.startswith("//", self.pos):
                end = text.find("\n", self.pos + 2)
                self.pos = len(text) if end < 0 else end + 1
            else:
                return

    def string(self) -> str:
        out = []
        text = self.text
        self.pos += 1
        while self.pos < len(text):
            ch = text[self.pos]
            self.pos += 1
            if ch == '"':
                break
            if ch != "\\" or self.pos >= len(text):
                out.append(ch)
                continue
            escaped = text[self.pos]
            self.pos += 1
            if escaped == "u":
                code = int(text[self.pos:self.pos + 4] or "0", 16)
                self.pos += 4
                if 0xD800 <= code <= 0xDBFF and text.startswith("\\u", self.pos):
                    low = int(text[self.pos + 2:self.pos + 6] or "0", 16)
                    self.pos += 6
                    code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                out.append(chr(code))
            else:
                out.append({"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}.get(escaped, escaped))
        return "".join(out)


def extract_action_log(text: str) -> list[str] | None:
    """The lines of the first `slate.actionlog` array, or None when the file has none."""
    scanner = _Scanner(text)
    while True:
        scanner.skip_blank()
        if scanner.at_end():
            return None
        if scanner.text[scanner.pos] != '"':
            scanner.pos += 1
            continue
        if lower(scanner.string()) != "slate.actionlog":
            continue
        scanner.skip_blank()
        if scanner.at_end() or scanner.text[scanner.pos] != ":":
            continue
        scanner.pos += 1
        scanner.skip_blank()
        if scanner.at_end() or scanner.text[scanner.pos] != "[":
            continue
        scanner.pos += 1
        lines = []
        while True:
            scanner.skip_blank()
            if scanner.at_end() or scanner.text[scanner.pos] == "]":
                return lines
            if scanner.text[scanner.pos] == '"':
                lines.append(scanner.string())
            else:
                scanner.pos += 1


def read_log_file(path: Path, label: str = "") -> LogFile | None:
    data = path.read_bytes()
    lines = extract_action_log(data.decode("utf-8", errors="replace"))
    if lines is None:
        return None  # one of SLATE's other files (tag lists, animation sets)
    return LogFile(path, label or path.name, lines)


def build_logs(files: list[LogFile], official_tags: bool = False) -> ActionLogs:
    logs = ActionLogs(files=files)
    for log in files:
        log.adds = log.removes = log.stage_labels = log.official_tags = log.disables = log.comments = log.invalid = 0
        for line in log.lines:
            parts = [trim(part) for part in line.split(",")]
            action = lower(parts[0])
            if len(parts) == 2 and action == "comment":
                log.comments += 1
            elif len(parts) == 2 and action == "disable":
                log.disables += 1
            elif len(parts) == 3 and action in ("addtag", "removetag") and parts[1] and normalize_tag(parts[2]):
                tag = canonical(normalize_tag(parts[2]))
                if tag in OFFICIAL_TAGS and not official_tags:
                    log.official_tags += 1
                else:
                    stage = stage_of_label(tag)
                    log.stage_labels += stage > 0
                    remove = action == "removetag"
                    if remove:
                        log.removes += 1
                    else:
                        log.adds += 1
                    logs.ops.append(PatchOp(remove, lower(parts[1]), tag, stage, log.path.name))
            else:
                log.invalid += 1

            # slsb-convert.py's reading: `action, anim, tag = item.split(',', 2)`. A line
            # without two commas made it crash; here it is left out instead.
            raw = line.split(",", 2)
            if len(raw) == 3:
                logs.converter_entries.append({"action": raw[0].lower(), "anim": raw[1].strip(), "tag": raw[2].strip()})
    return logs
