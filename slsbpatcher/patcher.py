"""Applies SLATE action logs to scene registries that already exist.

Same rules as SLATE NG (src/core/Patcher.cpp). A scene's tags are the union of its
stages' tags, so "the scene has the tag" means any stage has it. A new tag goes on every
stage, the way the converter writes scene-wide tags; a removed one comes off every stage
that carries it. A stage label goes on the one stage it names, unless the scene has no
such stage. Only tag lists change: scene ids, stage ids and every other byte stay as the
registry's author made them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import slr
from .actionlog import PatchOp, lower, normalize_tag, trim


@dataclass
class Applied:
    added: int = 0
    removed: int = 0
    changes: list[str] = field(default_factory=list)
    matched: set[str] = field(default_factory=set)


def _apply_to_stages(name: str, stages: list[list[str]], ops: list[PatchOp], applied: Applied) -> None:
    """Edit `stages` (one tag list per stage, in place) the way SLATE NG edits a scene."""
    for op in ops:
        if op.remove:
            erased = 0
            for tags in stages:
                before = len(tags)
                tags[:] = [tag for tag in tags if normalize_tag(tag) != op.tag]
                erased += before - len(tags)
            if erased:
                applied.removed += 1
                applied.changes.append(f"- {op.tag}  ({name})")
        elif not any(normalize_tag(tag) == op.tag for tags in stages for tag in tags):
            if 1 <= op.stage <= len(stages):
                stages[op.stage - 1].append(op.tag)
            else:
                for tags in stages:
                    tags.append(op.tag)
            applied.added += 1
            applied.changes.append(f"+ {op.tag}  ({name})")


def patch_slr(data: bytes, ops_by_scene: dict[str, list[PatchOp]]) -> tuple[bytes, Applied]:
    """The patched registry (or `data` itself when nothing changes) and what changed."""
    parsed = slr.parse_slr(data)
    applied = Applied()
    edited = []
    for scene in parsed.scenes:
        stages = [list(stage.tags) for stage in scene.stages]
        key = lower(trim(scene.name))
        if key in ops_by_scene and stages:
            applied.matched.add(key)
            _apply_to_stages(scene.name, stages, ops_by_scene[key], applied)
        edited.append(stages)
    changed = any(stage.tags != tags for scene, scenes in zip(parsed.scenes, edited) for stage, tags in zip(scene.stages, scenes))
    return (slr.build_slr(data, parsed, edited) if changed else data), applied


def patch_project(project: dict, ops_by_scene: dict[str, list[PatchOp]]) -> bool:
    """Mirror the same edits into a Scene Builder project (.slsb.json); True when it changed."""
    changed = False
    for scene in project.get("scenes", {}).values():
        key = lower(trim(scene.get("name", "")))
        stages = scene.get("stages", [])
        if key not in ops_by_scene or not stages:
            continue
        tags = [list(stage.get("tags", [])) for stage in stages]
        before = [list(t) for t in tags]
        _apply_to_stages(scene.get("name", ""), tags, ops_by_scene[key], Applied())
        if tags != before:
            changed = True
            for stage, new in zip(stages, tags):
                stage["tags"] = new
    return changed


def project_scene_ids(project: dict) -> set[str]:
    return {scene.get("id", key) for key, scene in project.get("scenes", {}).items()}


def dump_project(project: dict) -> str:
    """The formatting slsb-convert.py writes on Windows, so a patched project diffs cleanly."""
    return json.dumps(project, indent=2).replace("\n", "\r\n")
