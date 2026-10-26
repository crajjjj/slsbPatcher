"""Reader and tag splicer for SexLab P+ scene registries (.slr, formats 1 to 4).

Ported from SLATE NG's src/core/Slr.cpp. Integers are big-endian and strings are a
64-bit length followed by the bytes, as SexLab P+'s Decode helpers read them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

HASH_SIZE = 4
ID_SIZE = 8
COORDINATE_SIZE = 16  # x, y, z, rotation as 4-byte fixed point
MIN_VERSION = 1
MAX_VERSION = 4


class SlrError(ValueError):
    pass


@dataclass
class SlrStage:
    id: str
    events: list[str]
    tags: list[str]
    tag_block_offset: int = 0
    tag_block_size: int = 0


@dataclass
class SlrScene:
    id: str
    name: str
    stages: list[SlrStage] = field(default_factory=list)


@dataclass
class SlrFile:
    version: int
    name: str
    author: str
    prefix_hash: str
    scenes: list[SlrScene] = field(default_factory=list)

    def events(self) -> set[str]:
        """Every animation event in the registry, lower case."""
        return {event.lower() for scene in self.scenes for stage in scene.stages for event in stage.events}


class _Cursor:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def need(self, count: int) -> None:
        if count > len(self.data) - self.pos:
            raise SlrError("unexpected end of file")

    def skip(self, count: int) -> None:
        self.need(count)
        self.pos += count

    def u8(self) -> int:
        self.need(1)
        value = self.data[self.pos]
        self.pos += 1
        return value

    def u64(self) -> int:
        self.need(8)
        value = int.from_bytes(self.data[self.pos:self.pos + 8], "big")
        self.pos += 8
        return value

    def fixed(self, size: int) -> str:
        self.need(size)
        text = self.data[self.pos:self.pos + size].decode("utf-8", errors="surrogateescape")
        self.pos += size
        return text

    def string(self) -> str:
        return self.fixed(self.u64())

    def skip_string(self) -> None:
        self.skip(self.u64())

    def strings(self) -> list[str]:
        return [self.string() for _ in range(self.u64())]


def _skip_position_info(cursor: _Cursor, version: int) -> None:
    cursor.skip(1 + 1 + 4 + 1)  # race, sex, scale, extra flags
    if 1 < version < 4:
        cursor.strings()  # position annotations


def _read_position_event(cursor: _Cursor, version: int) -> str:
    event = cursor.string()
    cursor.skip(1 + COORDINATE_SIZE + 1)  # climax, offset, strip flags
    if version == 3:
        cursor.skip(1)
    if version >= 4:
        cursor.strings()  # position tags
    return event


def _read_stage(cursor: _Cursor, version: int) -> SlrStage:
    stage_id = cursor.fixed(ID_SIZE)
    events = [_read_position_event(cursor, version) for _ in range(cursor.u64())]
    cursor.skip(4)        # fixed length
    cursor.skip_string()  # navigation text
    offset = cursor.pos
    tags = cursor.strings()
    return SlrStage(stage_id, events, tags, offset, cursor.pos - offset)


def _read_scene(cursor: _Cursor, version: int) -> SlrScene:
    scene = SlrScene(cursor.fixed(ID_SIZE), cursor.string())
    for _ in range(cursor.u64()):
        _skip_position_info(cursor, version)
    if version < 4:
        cursor.skip(ID_SIZE)  # start stage
    scene.stages = [_read_stage(cursor, version) for _ in range(cursor.u64())]
    for _ in range(cursor.u64()):  # graph vertices
        cursor.skip(ID_SIZE)
        cursor.skip(cursor.u64() * ID_SIZE)
    cursor.skip(4 + 1 + COORDINATE_SIZE + 1)  # furniture types, allow bed, furniture offset, private
    return scene


def parse_slr(data: bytes) -> SlrFile:
    cursor = _Cursor(data)
    version = cursor.u8()
    if not MIN_VERSION <= version <= MAX_VERSION:
        raise SlrError(f"unsupported registry version {version}")
    slr = SlrFile(version, cursor.string(), cursor.string(), cursor.fixed(HASH_SIZE))
    slr.scenes = [_read_scene(cursor, version) for _ in range(cursor.u64())]
    if cursor.pos != len(data):
        raise SlrError("unexpected data after the last scene")
    return slr


def _encode_strings(values: list[str]) -> bytes:
    out = bytearray(len(values).to_bytes(8, "big"))
    for value in values:
        raw = value.encode("utf-8", errors="surrogateescape")
        out += len(raw).to_bytes(8, "big") + raw
    return bytes(out)


def build_slr(original: bytes, parsed: SlrFile, edited_tags: list[list[list[str]]]) -> bytes:
    """Copy `original`, replacing only the tag blocks whose tags changed.

    `edited_tags[scene][stage]` is the new tag list of that stage; scene ids, stage ids
    and every other byte stay as they were.
    """
    out = bytearray()
    copied = 0
    for scene, scene_tags in zip(parsed.scenes, edited_tags):
        for stage, tags in zip(scene.stages, scene_tags):
            if stage.tags == tags:
                continue
            out += original[copied:stage.tag_block_offset]
            out += _encode_strings(tags)
            copied = stage.tag_block_offset + stage.tag_block_size
    out += original[copied:]
    return bytes(out)
