#!/usr/bin/env python3
"""Sims 2 .package (DBPF) writer — emits v1.1 / index 7.2 packages, uncompressed.

Companion to s2parser.py. All resources are written uncompressed (the game
accepts this), so no DIR/CLST record is emitted.
"""

import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

import s2parser

HEADER_SIZE = 96
INDEX_ENTRY_SIZE = 24  # index v7.2: type, group, instance, instance_hi, offset, size


@dataclass
class Resource:
    type_id: int
    group_id: int
    instance_id: int          # main instance (3rd u32 in the index entry)
    data: bytes
    instance_hi: int = 0      # secondary/high id (4th u32), usually 0

    @property
    def type_name(self) -> str:
        return s2parser.TYPE_NAMES.get(self.type_id, f"0x{self.type_id:08X}")

    def tgi(self) -> tuple[int, int, int, int]:
        return (self.type_id, self.group_id, self.instance_id, self.instance_hi)


def write_package(path: Path | str, resources: list[Resource]) -> None:
    """Serialize resources into a DBPF v1.1 / index 7.2 package file."""
    seen: set[tuple[int, int, int, int]] = set()
    for r in resources:
        if r.tgi() in seen:
            raise ValueError(f"Duplicate TGI: {r.type_name} g={r.group_id:08x} i={r.instance_id:08x}")
        seen.add(r.tgi())

    body = bytearray()
    offsets = []
    for r in resources:
        offsets.append(HEADER_SIZE + len(body))
        body += r.data

    index_offset = HEADER_SIZE + len(body)
    index = bytearray()
    for r, off in zip(resources, offsets):
        index += struct.pack(
            "<IIIIII", r.type_id, r.group_id, r.instance_id, r.instance_hi, off, len(r.data)
        )

    header = bytearray(HEADER_SIZE)
    header[0:4] = b"DBPF"
    struct.pack_into("<II", header, 4, 1, 1)               # file version 1.1
    struct.pack_into("<I", header, 0x20, 7)                # index major version
    struct.pack_into("<I", header, 0x24, len(resources))   # entry count
    struct.pack_into("<I", header, 0x28, index_offset)
    struct.pack_into("<I", header, 0x2C, len(index))
    struct.pack_into("<I", header, 0x3C, 2)                # index minor version (7.2)

    with open(path, "wb") as f:
        f.write(header)
        f.write(body)
        f.write(index)


def read_all_resources(path: Path | str) -> list[Resource]:
    """Read every resource from a package, decompressed, as writable Resources."""
    out = []
    with open(path, "rb") as f:
        header = s2parser.parse_header(f)
        for e in s2parser.parse_index(f, header):
            data = s2parser.read_resource(f, e)
            # parse_index stores the index's 3rd u32 (main instance) in
            # instance_id2 and the 4th (high id) in instance_id
            out.append(Resource(e.type_id, e.group_id, e.instance_id2, data, e.instance_id))
    return out


def _selftest(donor: str) -> None:
    """Round-trip: read donor decompressed, rewrite, re-read, compare."""
    import tempfile

    original = read_all_resources(donor)
    # drop the CLST compression directory: rewritten copy is uncompressed
    original = [r for r in original if r.type_id != 0xE86AFEB1]

    with tempfile.NamedTemporaryFile(suffix=".package", delete=False) as tmp:
        tmp_path = tmp.name
    write_package(tmp_path, original)
    reread = read_all_resources(tmp_path)

    assert len(original) == len(reread), f"count {len(original)} != {len(reread)}"
    for a, b in zip(original, reread):
        assert a.tgi() == b.tgi(), f"TGI mismatch: {a.tgi()} != {b.tgi()}"
        assert a.data == b.data, f"data mismatch on {a.type_name} g={a.group_id:08x} i={a.instance_id:08x}"
    print(f"round-trip OK: {len(original)} resources, {Path(tmp_path).stat().st_size} bytes -> {tmp_path}")


if __name__ == "__main__":
    _selftest(sys.argv[1] if len(sys.argv) > 1 else "LTW_4UniCareers.package")
