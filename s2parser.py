#!/usr/bin/env python3
"""Sims 2 .package file parser — header, resource index, and BHAV decoding."""

# Annotations stay strings so the modules import under the system
# python3 (3.9), which the app gets when launched from Finder.
from __future__ import annotations

import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

# Known type IDs (Sims 2)
TYPE_NAMES = {
    0x42484156: "BHAV",
    0x424D505F: "BMP_",
    0x43415354: "CAST",
    0x43504F4C: "CPOL",
    0x43544353: "CTSS",
    0x46414345: "FACE",
    0x46414D49: "FAMI",
    0x46434E53: "FCNS",
    0x46574156: "FWAV",
    0x474C4F42: "GLOB",
    0x484F5553: "HOUS",
    0x4E524546: "NREF",
    0x4E474248: "NGBH",
    0x4F424A44: "OBJD",
    0x4F424A66: "OBJf",
    0x4F574E52: "OWNR",
    0x50414C54: "PALT",
    0x53494D49: "SIMI",
    0x534C4F54: "SLOT",
    0x534F424A: "SOBJ",
    0x53545223: "STR#",
    0x54544142: "TTAB",
    0x54544173: "TTAs",
    0x584D544F: "XMTO",
    0x584F424A: "XOBJ",
    0x6B6A4453: "kjDS",
    0xAACE2EFB: "SDSC",  # Sim Description (neighborhood packages)
    0xAC4F8687: "LIFO",
    0xCC364C2A: "SREL",  # sim-to-sim relationship
    0x0BF999E7: "LTXT",  # lot description
    0x8C870743: "FAMT",  # family ties
    0xCD95548E: "SWAF",  # wants and fears
    0xAC8A7A2E: "IDNO",  # neighborhood ID
    0xEBFEE33F: "PERS",  # person property set (XML)
    0xAC506764: "PGLN",
    0xE86B1EEF: "BINX",
    0xED534136: "COLL",
    0xFC4B284B: "TXTR",
    0x0C560F39: "5SC",
    0xC9C81B9B: "NHTG",
    # graphics / object types common in CC packages
    0x1C4A276C: "TXTR",  # texture resource
    0x49596978: "TXMT",  # material definition
    0x4C697E5A: "LIFO",  # level-of-detail image
    0xCCA8E925: "MMAT",  # material override
}


@dataclass
class Header:
    major_version: int
    minor_version: int
    index_major_version: int
    index_minor_version: int
    index_entry_count: int
    index_offset: int
    index_size: int

    def __str__(self):
        return (
            f"DBPF v{self.major_version}.{self.minor_version}  "
            f"index v{self.index_major_version}.{self.index_minor_version}  "
            f"{self.index_entry_count} entries @ 0x{self.index_offset:08x} ({self.index_size} bytes)"
        )


@dataclass
class ResourceEntry:
    type_id: int
    group_id: int
    instance_id: int
    instance_id2: int  # only used in index v7.2+
    offset: int
    size: int
    # Index version of the package this entry came from; decides which of the
    # two instance fields is the real instance id. See `instance` below.
    index_version: "tuple[int, int]" = (7, 1)

    @property
    def type_name(self) -> str:
        return TYPE_NAMES.get(self.type_id, f"0x{self.type_id:08X}")

    @property
    def instance(self) -> int:
        """The resource's real instance id, for either index version.

        The real instance is always the 3rd uint32 of the index entry, but
        v7.2 entries carry an extra field and `parse_index` lands that 3rd
        uint32 in `instance_id2` for them (`instance_id` then holds the
        resource id, nearly always 0). v7.1 and older have no extra field, so
        the 3rd uint32 stays in `instance_id`. Reading the wrong one turns a
        package's private BHAV 0x1000 into instance 0 and makes unrelated mods
        look like they collide — always prefer this over the raw fields.
        """
        return self.instance_id2 if self.index_version >= (7, 2) else self.instance_id

    @property
    def resource_id(self) -> int:
        """The v7.2 index entry's 4th uint32. Always 0 for older indexes."""
        return self.instance_id if self.index_version >= (7, 2) else 0

    def tgi(self) -> str:
        s = f"{self.type_name}  g={self.group_id:08x}  i={self.instance:08x}"
        if self.resource_id:
            s += f"  r={self.resource_id:08x}"
        return s


HEADER_MAGIC = b"DBPF"
HEADER_SIZE = 96


def parse_header(f: BinaryIO) -> Header:
    data = f.read(HEADER_SIZE)
    if len(data) < HEADER_SIZE:
        raise ValueError("File too short to contain a DBPF header")
    if data[:4] != HEADER_MAGIC:
        raise ValueError(f"Not a DBPF file (got {data[:4]!r})")

    # All uint32 LE; offsets match wiki/community docs
    major, minor = struct.unpack_from("<II", data, 4)
    # 0x08–0x17: reserved/unused fields
    index_major = struct.unpack_from("<I", data, 0x20)[0]
    entry_count = struct.unpack_from("<I", data, 0x24)[0]
    index_offset = struct.unpack_from("<I", data, 0x28)[0]
    index_size = struct.unpack_from("<I", data, 0x2C)[0]
    index_minor = struct.unpack_from("<I", data, 0x3C)[0]

    return Header(major, minor, index_major, index_minor, entry_count, index_offset, index_size)


def parse_index(f: BinaryIO, header: Header) -> list[ResourceEntry]:
    f.seek(header.index_offset)
    data = f.read(header.index_size)

    # Entry layout depends on index version:
    #   7.0: type, group, instance, offset, size               → 5 × uint32 = 20 bytes
    #   7.1: type, group, instance, offset, size               → 5 × uint32 = 20 bytes  (same)
    #   7.2: type, group, instance, resource, offset, size     → 6 × uint32 = 24 bytes
    # The instance is the 3rd uint32 either way, but it is stored in
    # ResourceEntry.instance_id2 for 7.2 and instance_id below 7.2 — read it
    # through ResourceEntry.instance rather than picking a field by hand.
    version = (header.index_major_version, header.index_minor_version)
    has_iid2 = version >= (7, 2)
    entry_size = 24 if has_iid2 else 20

    if header.index_entry_count > 0 and len(data) < header.index_entry_count * entry_size:
        raise ValueError(
            f"Index data too short: expected {header.index_entry_count * entry_size} bytes, "
            f"got {len(data)}"
        )

    entries = []
    pos = 0
    for _ in range(header.index_entry_count):
        if has_iid2:
            type_id, group_id, iid2, iid, offset, size = struct.unpack_from("<IIIIII", data, pos)
        else:
            type_id, group_id, iid, offset, size = struct.unpack_from("<IIIII", data, pos)
            iid2 = 0
        entries.append(ResourceEntry(type_id, group_id, iid, iid2, offset, size, version))
        pos += entry_size

    return entries


def open_package(path: Path) -> tuple[Header, list[ResourceEntry]]:
    with open(path, "rb") as f:
        header = parse_header(f)
        entries = parse_index(f, header)
    return header, entries


def read_resource(f: BinaryIO, entry: ResourceEntry) -> bytes:
    """Read raw resource bytes and decompress if QFS-compressed."""
    f.seek(entry.offset)
    data = f.read(entry.size)
    return qfs_decompress(data)


# ---------------------------------------------------------------------------
# QFS / RefPack decompression (Maxis variant used in DBPF packages)
# ---------------------------------------------------------------------------

def qfs_decompress(data: bytes) -> bytes:
    """Decompress QFS/RefPack data. Returns data unchanged if not compressed."""
    if len(data) < 9 or data[4:6] != b'\x10\xfb':
        return data

    uncomp_size = int.from_bytes(data[6:9], 'big')
    out = bytearray(uncomp_size)
    src = 9
    dst = 0
    n = len(data)

    while src < n and dst < uncomp_size:
        b = data[src]; src += 1

        if b < 0x80:
            # 2-byte control: 1 plain copy byte follows
            c = data[src]; src += 1
            num_plain = b & 0x03
            num_copy  = ((b & 0x1C) >> 2) + 3
            offset    = ((b & 0x60) << 3 | c) + 1
        elif b < 0xC0:
            # 3-byte control; high 2 bits of c = plain count, low 6 bits = offset high
            c = data[src]; src += 1
            d = data[src]; src += 1
            num_plain = c >> 6
            num_copy  = (b & 0x3F) + 4
            offset    = ((c & 0x3F) << 8 | d) + 1
        elif b < 0xE0:
            # 4-byte control
            c = data[src]; src += 1
            d = data[src]; src += 1
            e = data[src]; src += 1
            num_plain = b & 0x03
            num_copy  = ((b & 0x0C) << 6 | e) + 5
            offset    = ((b & 0x10) << 12 | c << 8 | d) + 1
        else:
            # 0xE0–0xFB: literal run, min 4 bytes; 0xFC–0xFF: end-of-stream
            num_plain = ((b & 0x1F) + 1) * 4 if b < 0xFC else b & 0x03
            num_copy  = 0
            offset    = 0

        # Copy literal bytes
        out[dst:dst + num_plain] = data[src:src + num_plain]
        dst += num_plain
        src += num_plain

        # Copy from already-decoded output (may overlap → byte-by-byte)
        copy_src = dst - offset
        for i in range(num_copy):
            if dst >= uncomp_size: break
            out[dst] = out[copy_src + i] if copy_src + i >= 0 else 0
            dst += 1

        if b >= 0xFC:
            break

    return bytes(out)  # return full pre-allocated buffer; trailing zeros are intentional


# ---------------------------------------------------------------------------
# BHAV (behavior) parsing — format version 0x8007 (Sims 2)
# ---------------------------------------------------------------------------

# SimAntics primitive opcodes (0x0000–0x00FF). Gaps are opcodes unused in
# Sims 2. Names per Pick'N'Mix Sims 2 primitives reference:
# https://www.picknmixmods.com/Sims2/Notes/Primitives/Primitives.html
PRIMITIVES = {
    0x0000: "Sleep",
    0x0001: "Generic Sims Call",
    0x0002: "Expression",
    0x0003: "Find Best Interaction",
    0x0007: "Refresh",
    0x0008: "Random Number",
    0x000B: "Get Distance To",
    0x000C: "Get Direction To",
    0x000D: "Push Interaction",
    0x000E: "Find Best Object for Function",
    0x000F: "Break Point",
    0x0010: "Find Location For",
    0x0011: "Idle for Input",
    0x0012: "Remove Object Instance",
    0x0013: "Make New Character",
    0x0014: "Run Functional Tree",
    0x0016: "Turn Body Towards",
    0x0017: "Play / Stop Sound Effect",
    0x0019: "Alter Budget",
    0x001A: "Relationship",
    0x001B: "Go To Relative Position",
    0x001C: "Run Tree by Name",
    0x001D: "Set Motive Change",
    0x001E: "Gosub Found Action",
    0x001F: "Set to Next",
    0x0020: "Test Object Type",
    0x0021: "Find 5 Worst Motives",
    0x0022: "UI Effect",
    0x0023: "Camera Control",
    0x0024: "Dialog",
    0x0025: "Test Sim Interacting With",
    0x002A: "Create New Object Instance",
    0x002D: "Go To Routing Slot",
    0x002E: "Snap",
    0x0030: "Stop ALL Sounds",
    0x0031: "Notify the SO out of Idle",
    0x0032: "Add/Change the Action String",
    0x0033: "Manage Inventory",
    0x0069: "Animate Object",
    0x006A: "Animate Sim",
    0x006B: "Animate Overlay",
    0x006C: "Animate Stop",
    0x006D: "Change Material",
    0x006E: "Look At",
    0x006F: "Change Light",
    0x0070: "Effect Stop/Start",
    0x0071: "Snap Into",
    0x0072: "Assign Locomotion Animations",
    0x0073: "Debug",
    0x0074: "Reach/Put",
    0x0075: "Age",
    0x0076: "Array Operation",
    0x0077: "Message",
    0x0078: "RayTrace",
    0x0079: "Change Outfit",
    0x007A: "On Timer",
    0x007B: "Cinematic",
    0x007C: "Want Satisfy",
    0x007D: "Follow Sim",
    0x007E: "LUA",
}

# Destination sentinel values for u16 branch targets (formats 0x8007/0x8009).
# Verified empirically: 0xFFFD = return true, 0xFFFE = return false,
# 0xFFFC = error. No 1-byte forms here — a low value like 0x00FD is a
# legitimate instruction index in a large tree.
_TRUE_SENTINELS  = {0xFFFD}
_FALSE_SENTINELS = {0xFFFE}
_ERROR_SENTINELS = {0xFFFC}

def _dest_str(d: int) -> str:
    if d in _TRUE_SENTINELS:  return "→TRUE"
    if d in _FALSE_SENTINELS: return "→FALSE"
    if d in _ERROR_SENTINELS: return "→ERROR"
    return f"→{d}"


@dataclass
class BhavInstruction:
    opcode: int
    true_dest: int
    false_dest: int
    operands: bytes  # 16 bytes

    def opcode_name(self, bhav_names: dict[int, str] | None = None) -> str:
        # 0x0000–0x00FF: primitives; 0x0100–0x0FFF: global trees;
        # 0x1000–0x1FFF: local (private) trees; 0x2000+: semiglobal trees.
        # Tree calls use the target BHAV's instance ID as the opcode.
        if self.opcode < 0x0100:
            return PRIMITIVES.get(self.opcode, f"Primitive 0x{self.opcode:04X}")
        if bhav_names and self.opcode in bhav_names:
            return f'CallBHAV → "{bhav_names[self.opcode]}"'
        if self.opcode < 0x1000:
            return f"Global BHAV(0x{self.opcode:04X})"
        if self.opcode < 0x2000:
            return f"Local BHAV(0x{self.opcode:04X})"
        return f"Semiglobal BHAV(0x{self.opcode:04X})"

    def fmt(self, bhav_names: dict[int, str] | None = None) -> str:
        ops = self.operands.hex(' ')
        name = self.opcode_name(bhav_names)
        return (
            f"{name:<50s}  "
            f"T:{_dest_str(self.true_dest):<8s}  "
            f"F:{_dest_str(self.false_dest):<8s}  "
            f"ops=[{ops}]"
        )

    def __str__(self) -> str:
        return self.fmt()


@dataclass
class Bhav:
    name: str
    format_version: int
    bhav_type: int
    argc: int
    localc: int
    instructions: list[BhavInstruction]

    def fmt(self, bhav_names: dict[int, str] | None = None) -> str:
        lines = [
            f"BHAV  \"{self.name}\"",
            f"  format=0x{self.format_version:04X}  type={self.bhav_type}"
            f"  argc={self.argc}  locals={self.localc}"
            f"  instructions={len(self.instructions)}",
        ]
        for i, instr in enumerate(self.instructions):
            lines.append(f"  [{i:3d}]  {instr.fmt(bhav_names)}")
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.fmt()


def parse_bhav(data: bytes) -> Bhav:
    """Parse a BHAV resource (must already be decompressed)."""
    if len(data) < 72:
        raise ValueError(f"BHAV data too short ({len(data)} bytes)")

    name = data[:64].split(b'\x00', 1)[0].decode('latin-1')
    ver, n_instr, bhav_type, argc, localc, _flags = struct.unpack_from("<HHBBBb", data, 64)

    if ver not in (0x8007, 0x8009):
        raise ValueError(f"Unsupported BHAV format version: 0x{ver:04X}")

    # Both formats: opcode(H) true(H) false(H) ops(16B) tail(1B) = 23 bytes/instr.
    # 0x8007 has a 4-byte extra header (instructions at 76); 0x8009 has a
    # 5-byte extra header (instructions at 77) — verified empirically against
    # Sim Blender.package, where offset 76 misaligns every 0x8009 instruction.
    if ver == 0x8007:
        instr_fmt, instr_size, ops_offset, instr_start = "<HHH", 23, 6, 76
    else:
        instr_fmt, instr_size, ops_offset, instr_start = "<HHH", 23, 6, 77

    instrs = []
    pos = instr_start
    for _ in range(n_instr):
        if pos + instr_size > len(data):
            break
        opcode, true_d, false_d = struct.unpack_from(instr_fmt, data, pos)
        operands = data[pos + ops_offset:pos + ops_offset + 16]
        instrs.append(BhavInstruction(opcode, true_d, false_d, operands))
        pos += instr_size

    return Bhav(name, ver, bhav_type, argc, localc, instrs)


# ---------------------------------------------------------------------------
# Tree renderer — converts flat instruction list to indented branch tree
# ---------------------------------------------------------------------------

def _dest_term(d: int, n: int) -> str | None:
    """Terminal label for sentinel/out-of-range destinations, or None if it needs expansion."""
    if d in _TRUE_SENTINELS:  return "→ return TRUE"
    if d in _FALSE_SENTINELS: return "→ return FALSE"
    if d in _ERROR_SENTINELS: return "→ ERROR"
    if d >= n:                return f"→ [{d}] (out of range)"
    return None


def render_bhav_tree(bhav: "Bhav", bhav_names: dict[int, str] | None = None) -> str:
    instrs = bhav.instructions
    n = len(instrs)
    done: set[int] = set()
    lines: list[str] = []

    def pad(indent: int) -> str:
        return "  " * indent

    def emit(idx: int, indent: int, path: frozenset[int]) -> None:
        term = _dest_term(idx, n)
        if term:
            lines.append(pad(indent) + term)
            return
        if idx in path:
            lines.append(pad(indent) + f"↺ loop → [{idx}]")
            return
        if idx in done:
            lines.append(pad(indent) + f"→ [{idx}]")
            return

        done.add(idx)
        instr = instrs[idx]
        t, f = instr.true_dest, instr.false_dest
        name = instr.opcode_name(bhav_names)

        lines.append(pad(indent) + f"[{idx}] {name}")

        new_path = path | {idx}
        t_term = _dest_term(t, n)
        f_term = _dest_term(f, n)

        if t == f:
            # Both branches go the same place — emit once, no fork shown
            if t_term:
                lines.append(pad(indent + 1) + t_term)
            else:
                emit(t, indent, new_path)
        elif t == idx + 1:
            # True falls through; false diverges
            branch = f_term or f"→ [{f}]"
            if f_term or f in done or f in path:
                lines.append(pad(indent + 1) + f"↳ FALSE: {branch}")
            else:
                lines.append(pad(indent + 1) + "↳ FALSE:")
                emit(f, indent + 2, new_path)
            emit(idx + 1, indent, new_path)
        elif f == idx + 1:
            # False falls through; true diverges
            branch = t_term or f"→ [{t}]"
            if t_term or t in done or t in path:
                lines.append(pad(indent + 1) + f"↳ TRUE: {branch}")
            else:
                lines.append(pad(indent + 1) + "↳ TRUE:")
                emit(t, indent + 2, new_path)
            emit(idx + 1, indent, new_path)
        elif t_term and f_term:
            # Both are terminal — show both inline
            lines.append(pad(indent + 1) + f"↳ TRUE:  {t_term}")
            lines.append(pad(indent + 1) + f"↳ FALSE: {f_term}")
        elif t_term:
            # True is terminal, false is a sub-tree
            lines.append(pad(indent + 1) + f"↳ TRUE: {t_term}")
            emit(f, indent, new_path)
        elif f_term:
            # False is terminal, true is a sub-tree
            lines.append(pad(indent + 1) + f"↳ FALSE: {f_term}")
            emit(t, indent, new_path)
        else:
            # Both branch to non-trivial targets — expand both
            lines.append(pad(indent + 1) + "↳ TRUE:")
            emit(t, indent + 2, new_path)
            lines.append(pad(indent + 1) + "↳ FALSE:")
            emit(f, indent + 2, new_path)

    emit(0, 0, frozenset())

    # Note any unreachable instructions
    unreachable = [i for i in range(n) if i not in done]
    if unreachable:
        lines.append(f"  ··· {len(unreachable)} unreachable instruction(s): {unreachable[:8]}"
                     + (" …" if len(unreachable) > 8 else ""))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_list(path: Path):
    header, entries = open_package(path)
    print(f"{path.name}")
    print(f"  {header}")
    print()
    for i, e in enumerate(entries):
        print(f"  [{i:4d}]  {e.tgi()}  @ 0x{e.offset:08x}  size={e.size}")


def cmd_bhav(path: Path, flat: bool = False):
    """Decode and print all BHAVs in a package."""
    header, entries = open_package(path)
    bhav_entries = [e for e in entries if e.type_id == 0x42484156]
    print(f"{path.name}  —  {len(bhav_entries)} BHAV(s)\n")

    # First pass: build instance → name map for call resolution. A Call
    # opcode is the callee's instance id, so this must key off the real
    # instance — keying off the raw field collapses every BHAV in a v7.2
    # package onto 0.
    bhav_names: dict[int, str] = {}
    with open(path, "rb") as f:
        for e in bhav_entries:
            raw = read_resource(f, e)
            if len(raw) >= 64:
                name = raw[:64].split(b'\x00', 1)[0].decode('latin-1', 'replace')
                bhav_names[e.instance] = name

    # Second pass: parse and print with resolved names
    with open(path, "rb") as f:
        for e in bhav_entries:
            raw = read_resource(f, e)
            try:
                bhav = parse_bhav(raw)
                header_line = (
                    f"BHAV  \"{bhav.name}\"\n"
                    f"  format=0x{bhav.format_version:04X}  type={bhav.bhav_type}"
                    f"  argc={bhav.argc}  locals={bhav.localc}"
                    f"  instructions={len(bhav.instructions)}"
                )
                if flat:
                    print(bhav.fmt(bhav_names))
                else:
                    print(header_line)
                    print(render_bhav_tree(bhav, bhav_names))
            except ValueError as exc:
                print(f"  [skip] {exc}  (iid={e.instance:#010x})")
            print()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Sims 2 .package parser")
    ap.add_argument("files", nargs="+", metavar="FILE")
    ap.add_argument("--bhav", action="store_true", help="Decode BHAV resources")
    ap.add_argument("--flat", action="store_true", help="Flat instruction list instead of tree (use with --bhav)")
    args = ap.parse_args()

    for f in args.files:
        p = Path(f)
        if args.bhav:
            cmd_bhav(p, flat=args.flat)
        else:
            cmd_list(p)
        print()
