#!/usr/bin/env python3
"""Sims 2 .package (DBPF) writer — emits v1.1 / index 7.2 packages, uncompressed.

Companion to s2parser.py. All resources are written uncompressed (the game
accepts this), so no DIR/CLST record is emitted.
"""

# Annotations stay strings so the modules import under the system
# python3 (3.9), which the app gets when launched from Finder.
from __future__ import annotations

import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

import s2parser

HEADER_SIZE = 96
INDEX_ENTRY_SIZE = 24  # index v7.2: type, group, instance, instance_hi, offset, size

# Directory of compressed files: it lists which resources are QFS-compressed
# and how big each one is decompressed. A DIR carried over from a donor is
# always dropped, since write_package decides compression for itself.
TYPE_DIR = 0xE86B1EEF

# The DIR's own identity, constant across all 25 sample packages that have
# one. Entry width follows the index version — 16 bytes at v7.1, 20 at v7.2
# (the extra field is the resource id) — and this writer emits v7.2.
DIR_TGI = (TYPE_DIR, 0xE86B1EEF, 0x286B1F03, 0)


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


def write_package(path: Path | str, resources: list[Resource], *,
                  compress: bool = False,
                  compress_tgis: "set[tuple[int, int, int, int]] | None" = None) -> None:
    """Serialize resources into a DBPF v1.1 / index 7.2 package file.

    With `compress`, each resource is QFS-compressed and kept only if that
    actually made it smaller, and a DIR is emitted listing exactly what ended
    up compressed. Without it everything is stored plain. `compress_tgis`
    picks resources individually instead — the editor uses it to keep a
    donor's own compression choices, or the user's, rather than deciding
    for the whole package at once. A resource is compressed if either says so.

    A resource that still carries its own QFS stream in a `packed` attribute
    (see s2package.LazyResource) is written from that stream rather than
    recompressed, so saving a 50 MB donor with one edit costs one
    compression, not fifty thousand.

    Either way an incoming DIR is dropped and rebuilt rather than carried
    over: it describes compression this call decides afresh, so a donor's
    directory would be describing a file that no longer exists.
    """
    resources = [r for r in resources if r.type_id != TYPE_DIR]
    chosen = compress_tgis or set()

    seen: set[tuple[int, int, int, int]] = set()
    for r in resources:
        if r.tgi() in seen:
            raise ValueError(f"Duplicate TGI: {r.type_name} g={r.group_id:08x} i={r.instance_id:08x}")
        seen.add(r.tgi())

    # On-disk bytes per resource, in package order, and for each one that
    # ends up compressed, its uncompressed length for the DIR.
    payloads: list[bytes] = []
    directory: list[tuple[Resource, int]] = []
    for r in resources:
        want = compress or r.tgi() in chosen
        packed = getattr(r, "packed", None) if want else None
        if packed is not None:
            plain_len = qfs_uncompressed_size(packed)
            if len(packed) < plain_len:
                payloads.append(packed)
                directory.append((r, plain_len))
                continue
        blob = r.data
        if want and len(blob) <= s2parser.QFS_MAX_UNCOMPRESSED:
            packed = s2parser.qfs_compress(blob)
            # Compression that grew the resource is worse than none: the game
            # reads either, and the DIR is what says which this is.
            if len(packed) < len(blob):
                directory.append((r, len(blob)))
                blob = packed
        payloads.append(blob)

    if directory:
        entries = b''.join(
            struct.pack("<IIIII", r.type_id, r.group_id, r.instance_id,
                        r.instance_hi, plain_len)
            for r, plain_len in directory)
        resources = resources + [Resource(*DIR_TGI[:3], entries, DIR_TGI[3])]
        payloads.append(entries)

    body = bytearray()
    offsets = []
    for blob in payloads:
        offsets.append(HEADER_SIZE + len(body))
        body += blob

    index_offset = HEADER_SIZE + len(body)
    index = bytearray()
    for r, blob, off in zip(resources, payloads, offsets):
        index += struct.pack(
            "<IIIIII", r.type_id, r.group_id, r.instance_id, r.instance_hi,
            off, len(blob)
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


def qfs_uncompressed_size(packed: bytes) -> int:
    """The inflated length a QFS stream declares in its 9-byte header."""
    if len(packed) < s2parser.QFS_HEADER_SIZE or packed[4:6] != s2parser.QFS_MAGIC:
        raise ValueError("not a QFS stream")
    return int.from_bytes(packed[6:9], 'big')


def read_all_resources(path: Path | str) -> list[Resource]:
    """Read every resource from a package, decompressed, as writable Resources."""
    out = []
    with open(path, "rb") as f:
        header = s2parser.parse_header(f)
        entries = s2parser.parse_index(f, header)
        version = (header.index_major_version, header.index_minor_version)
        # The DIR says which resources are compressed, and no DIR means none
        # are. Sniffing the payload for the QFS magic instead would misread a
        # stored resource that happens to carry 0x10FB at offset 4. Checked
        # against 4,000 of the game's own packages: the DIR agrees with a
        # sniff on all 3,778 that carry one, and none of the 222 without one
        # hold a compressed resource.
        directory = s2parser.read_dir(f, entries, version) or {}
        for e in entries:
            key = (e.type_id, e.group_id, e.instance, e.resource_id)
            compressed = key in directory
            data = s2parser.read_resource(f, e, compressed=compressed)
            # e.instance/e.resource_id pick the right index fields for the
            # donor's index version; below 7.2 there is no 4th u32 at all.
            out.append(Resource(e.type_id, e.group_id, e.instance, data, e.resource_id))
    return out


def _selftest(donor: str) -> None:
    """Round-trip the donor both ways: stored, and QFS-compressed.

    Compression must be invisible through read_all_resources — same TGIs,
    same bytes — so the two passes assert against the same expectation and
    only the file size differs.
    """
    import tempfile

    original = read_all_resources(donor)
    original = [r for r in original if r.type_id != TYPE_DIR]

    for compress in (False, True):
        with tempfile.NamedTemporaryFile(suffix=".package", delete=False) as tmp:
            tmp_path = tmp.name
        write_package(tmp_path, original, compress=compress)
        reread = read_all_resources(tmp_path)

        # A compressed package carries a DIR the plain one has no need for.
        directory = [r for r in reread if r.type_id == TYPE_DIR]
        payload = [r for r in reread if r.type_id != TYPE_DIR]

        assert len(original) == len(payload), \
            f"count {len(original)} != {len(payload)} (compress={compress})"
        for a, b in zip(original, payload):
            assert a.tgi() == b.tgi(), f"TGI mismatch: {a.tgi()} != {b.tgi()}"
            assert a.data == b.data, \
                (f"data mismatch on {a.type_name} g={a.group_id:08x} "
                 f"i={a.instance_id:08x} (compress={compress})")

        size = Path(tmp_path).stat().st_size
        if not compress:
            assert not directory, "stored package should carry no DIR"
            plain_size = size
            print(f"stored:     {len(payload)} resources, {size} bytes")
        else:
            # Every resource the DIR names must really decompress, to exactly
            # the size the DIR records. Checking that against a magic sniff
            # instead would just be comparing the DIR to the guess it exists
            # to replace — and would agree with it in precisely the case the
            # DIR is there to get right.
            with open(tmp_path, "rb") as f:
                header = s2parser.parse_header(f)
                entries = s2parser.parse_index(f, header)
                version = (header.index_major_version, header.index_minor_version)
                listed = s2parser.read_dir(f, entries, version)
                assert listed, "compressed package should carry a DIR"
                by_key = {(e.type_id, e.group_id, e.instance, e.resource_id): e
                          for e in entries}
                assert s2parser.TYPE_DIR not in {k[0] for k in listed}, \
                    "the DIR must not list itself"
                for key, unc in listed.items():
                    assert key in by_key, f"DIR names a resource not in the index: {key}"
                    e = by_key[key]
                    f.seek(e.offset)
                    raw = f.read(e.size)
                    plain = s2parser.qfs_decompress(raw)
                    assert plain is not raw and len(plain) == unc, (
                        f"DIR claims {unc} decompressed bytes for {key}, "
                        f"got {len(plain)}")
            print(f"compressed: {len(payload)} resources, {size} bytes "
                  f"({size / plain_size:.1%} of stored), "
                  f"DIR lists {len(listed)} -> {tmp_path}")

    _selftest_magic_collision()


def _selftest_magic_collision() -> None:
    """A stored resource that merely looks compressed must survive the trip.

    Sniffing the payload for the QFS magic used to decide this, so a resource
    whose bytes happened to carry 0x10FB at offset 4 was decoded as though it
    were compressed and came back as garbage. It is the DIR's job to say, and
    a package written without compression has no DIR to say it — so both
    paths are exercised here.
    """
    import os
    import tempfile

    # Incompressible, so `compress=True` stores it plain and leaves it out of
    # the DIR, and carrying the magic where the old sniff looked for it.
    payload = os.urandom(4) + s2parser.QFS_MAGIC + os.urandom(4000)
    r = Resource(0x42484156, 0xDEADBEEF, 0x1000, payload, 0)

    for compress in (False, True):
        with tempfile.NamedTemporaryFile(suffix=".package", delete=False) as tmp:
            tmp_path = tmp.name
        write_package(tmp_path, [r], compress=compress)
        back = [x for x in read_all_resources(tmp_path) if x.type_id != TYPE_DIR]
        assert len(back) == 1, f"expected 1 resource, got {len(back)}"
        assert back[0].data == payload, (
            f"a stored resource that looks compressed came back as "
            f"{len(back[0].data)} bytes, not {len(payload)} (compress={compress})")
        os.unlink(tmp_path)
    print(f"magic collision: {len(payload)}-byte stored resource survives both ways")


if __name__ == "__main__":
    _selftest(sys.argv[1] if len(sys.argv) > 1 else "LTW_4UniCareers.package")
