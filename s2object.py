#!/usr/bin/env python3
"""Builders and parsers for Sims 2 object package resources.

Every format below has both a `parse_*` and a matching builder, and the pair
round-trips byte-for-byte over the donors in sample-packages/ (see _selftest).
That round-trip is the evidence the layouts are right — modding is
read-modify-write, so a reader that loses bytes silently corrupts the object.

Parsers keep the regions they don't name as raw bytes rather than dropping
them, so an edit to one field can't disturb the rest of the resource.

Formats were reverse-engineered from Christianlov's Counterfeit College
Diploma and TwoJeffs' Sim Blender (see sample-packages/):

BHAV 0x8007: 64B name + <HHBBBb (ver, n, type, argc, locals, flags) +
  u32 tree version + n * 23-byte instructions
  (opcode u16, true u16, false u16, 16 operand bytes, 1 tail byte).
  Exit sentinels: 0xFFFD return-true, 0xFFFE return-false, 0xFFFC error.

Expression (opcode 0x0002) operands:
  [flag, lhs_lo, lhs_hi, rhs_lo, rhs_hi, 0, operator, lhs_owner, rhs_owner, 0*7]
  operators: 0x02 '==', 0x03 '+=', 0x05 ':='
  owners: 0x00 my attr, 0x01 stack-obj attr, 0x07 literal, 0x08 temp,
          0x09 param, 0x19 local

Dialog (opcode 0x0024) operands (plain OK message box, text from the
object's STR# 0x12D "Dialog prim string set"):
  [0x01, 0,0,0,0,0, 0x08, 0, 0,0,0,0, 0, 0x01, idx_lo, idx_hi]

STR#/TTAs/CTSS: 64B name + u16 format + u16 count +
  count * (lang u8, value cstring, desc cstring).
  Format 0xFFFD (every donor here) carries the description string; 0xFFFF
  omits it. Read as a u16 the format word is 0xFFFD — earlier notes here
  wrote it byte-reversed as "0xFDFF".

TTAB: 64B name + u32 0xFFFFFFFF + u32 version + u32 0 + u16 count +
  count * fixed-size entries + u32 name-len + name.
  Two versions occur, differing only in entry size — 0x4F has a 28-byte
  motive-advertisement block at +4 that 0x54 drops:
    v0x4F: 74-byte entries, TTAs index u32 @+36   (Diploma, 4 entries)
    v0x54: 54-byte entries, TTAs index u32 @+8    (Sim Blender, 81 entries)
  Both: action tree u16 @+0, guard tree u16 @+2.
  Entry sizes confirmed by solving against resource length, and the indices
  by resolving them back to sensible menu labels in the paired TTAs.

OBJf: 64B filename + u32 0 + u32 0 + "OBJf" signature u32 @+72 +
  u32 count @+76 + count * (guard tree u16, action tree u16).
  Count is the game-version-dependent length of the fixed function-slot
  list (37 in the Diploma, 41 in Sim Blender) and equals (len - 80) / 4.
  Slot 0 = init, slot 1 = main — confirmed: the Diploma's slots hold
  0x1000/0x1001, whose BHAVs are named "Function - Init"/"Function - Main".
  Higher slot meanings are NOT verified here, so they stay numbered.

OBJD: 64B filename + 108 u16 fields + u32 name-len + name.
  Field map per SimsWiki 4F424A44 (word indices, after the 64B filename):
  word 0 version (139/140), word 7 interaction table (TTAB) id,
  word 9 object TYPE (4 = buyable — never change), words 14/15 GUID lo/hi,
  word 18 price, words 39/40 room and function catalog sort flags,
  word 41 catalog strings (CTSS) instance,
  words 52/53 job object GUID (donor mirrors its own GUID here),
  word 58 number of attributes (8 preallocated when 0),
  words 70/71 original (clone source) GUID.
"""

# Annotations stay strings so the modules import under the system
# python3 (3.9), which the app gets when launched from Finder.
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

import s2parser

# ---- SimAntics constants ---------------------------------------------------

RET_TRUE, RET_FALSE, RET_ERROR = 0xFFFD, 0xFFFE, 0xFFFC

OP_EXPRESSION = 0x0002
OP_DIALOG = 0x0024
OP_INVENTORY = 0x0033

# Manage Inventory (0x0033) — layout decoded from Monique's bank + ACR
INV_GLOBAL, INV_NEIGHBOR = 0x00, 0x03
INV_ADD, INV_REMOVE, INV_FIND, INV_BIND, INV_GETPROP, INV_SETPROP, INV_COMMIT = (
    0x00, 0x01, 0x03, 0x06, 0x07, 0x08, 0x09)
INV_COUNT = 0x0A  # get token count -> value var (seen in ffsdebugger gossip code)

EXPR_GT = 0x00  # 'greater than' (ffsdebugger: count > 0 test)

EXPR_EQ, EXPR_ADD, EXPR_SET = 0x02, 0x03, 0x05

OWNER_MY_ATTR = 0x00
OWNER_STACKOBJ_ATTR = 0x01
OWNER_LITERAL = 0x07
OWNER_TEMP = 0x08
OWNER_PARAM = 0x09
OWNER_LOCAL = 0x19

TREE_VERSION = 0xFFFF800A  # matches donor trees; loads on all EP-era games

TYPE_BCON = 0x42434F4E
TYPE_BHAV = 0x42484156
TYPE_CTSS = 0x43545353
TYPE_GLOB = 0x474C4F42
TYPE_NREF = 0x4E524546
TYPE_OBJD = 0x4F424A44
TYPE_OBJF = 0x4F424A66
TYPE_SLOT = 0x534C4F54
TYPE_STR = 0x53545223
TYPE_TTAB = 0x54544142
TYPE_TTAS = 0x54544173


def _name64(name: str) -> bytes:
    raw = name.encode('latin-1')[:63]
    return raw + b'\x00' * (64 - len(raw))


def _read_name64(data: bytes) -> str:
    return data[:64].split(b'\x00', 1)[0].decode('latin-1', 'replace')


def _emit_name64(name: str, raw: bytes | None) -> bytes:
    """Re-emit a 64-byte name field, preserving the donor's exact padding.

    Maxis resources are not consistent about what follows the terminator, so
    a parsed-then-rebuilt resource reuses the original bytes unless the name
    itself was edited. Without this, round-tripping rewrites junk to zeros.
    """
    if raw is not None and len(raw) == 64 and _read_name64(raw) == name:
        return raw
    return _name64(name)


# ---- BHAV ------------------------------------------------------------------

def instr(opcode: int, true_dest: int, false_dest: int, operands: bytes) -> bytes:
    if len(operands) > 16:
        raise ValueError("operands must be <= 16 bytes")
    ops = operands + b'\x00' * (16 - len(operands))
    return struct.pack('<HHH', opcode, true_dest, false_dest) + ops + b'\x00'


def expr_ops(lhs: int, rhs: int, operator: int, lhs_owner: int, rhs_owner: int) -> bytes:
    return bytes([0, lhs & 0xFF, lhs >> 8, rhs & 0xFF, rhs >> 8, 0,
                  operator, lhs_owner, rhs_owner])


def expr(lhs, rhs, operator, lhs_owner, rhs_owner, t=None, f=RET_ERROR):
    """Expression instruction; t defaults to fall-through (caller patches)."""
    return instr(OP_EXPRESSION, t, f, expr_ops(lhs, rhs, operator, lhs_owner, rhs_owner))


def inv_ops(operation: int, guid: int = 0, *, inv_type: int = INV_GLOBAL,
            owner_scope: int = 0, owner_id: int = 0,
            sel_scope: int = 0, sel_id: int = 0,
            val_scope: int = 0, val_id: int = 0, cat: int = 0) -> bytes:
    """Manage Inventory (0x0033) operands. owner = whose inventory for
    inv_type=NEIGHBOR (scope+id of a variable holding the NID: Monique uses
    param 0x09, ffsdebugger my-person-data 0x12/0x1F, ACR stack-obj person
    data 0x13/0x1F). sel = iterator index var (find/remove); val = value var
    (count dest). NOTE: the GLOBAL inventory (b1=0) is the gossip store —
    find ignores the GUID there; do not use it for mod tokens."""
    return bytes([cat, inv_type, owner_scope, owner_id & 0xFF, owner_id >> 8,
                  operation,
                  guid & 0xFF, (guid >> 8) & 0xFF, (guid >> 16) & 0xFF, guid >> 24,
                  0, sel_scope, sel_id, 0, val_scope, val_id])


def dialog_ops(string_index: int) -> bytes:
    return bytes([0x01, 0, 0, 0, 0, 0, 0x08, 0, 0, 0, 0, 0, 0,
                  0x01, string_index & 0xFF, string_index >> 8])


class Asm:
    """Label-based assembler for BHAV instruction lists: t/f targets may be
    instruction indices, sentinels, or string labels defined via label()."""

    def __init__(self):
        self._items = []   # ('ins', opcode, t, f, ops) | ('label', name)

    def label(self, name: str):
        self._items.append(('label', name))
        return self

    def ins(self, opcode: int, t, f, ops: bytes):
        self._items.append(('ins', opcode, t, f, ops))
        return self

    def assemble(self) -> list[bytes]:
        labels, idx = {}, 0
        for item in self._items:
            if item[0] == 'label':
                if item[1] in labels:
                    raise ValueError(f'duplicate label {item[1]!r}')
                labels[item[1]] = idx
            else:
                idx += 1

        def resolve(dest):
            if isinstance(dest, str):
                return labels[dest]
            return dest

        out = []
        for item in self._items:
            if item[0] == 'ins':
                _, opcode, t, f, ops = item
                out.append(instr(opcode, resolve(t), resolve(f), ops))
        return out


def bhav(name: str, instructions: list[bytes], argc: int = 0, localc: int = 0) -> bytes:
    header = struct.pack('<HHBBBb', 0x8007, len(instructions), 0, argc, localc, 0)
    return _name64(name) + header + struct.pack('<I', TREE_VERSION) + b''.join(instructions)


# ---- STR# / TTAs / CTSS -----------------------------------------------------

def str_resource(name: str, strings: list[str], lang: int = 1) -> bytes:
    out = bytearray(_name64(name))
    out += struct.pack('<HH', 0xFFFD, len(strings))
    for s in strings:
        out += bytes([lang]) + s.encode('latin-1', 'replace') + b'\x00' + b'\x00'
    return bytes(out)


# ---- TTAB --------------------------------------------------------------------

def ttab(entry_template: bytes, entries: list[tuple[int, int, int]],
         name: str = 'Interaction Table') -> bytes:
    """TTAB version 0x54 (fully documented; Sim Blender-proven at 81
    entries). Entry: action u16, guard u16, flags u16 x2, TTAs index u32 @+8,
    attenuation code u32/value f32, autonomy u32, join u32, UI type u16,
    facial u32, memory mult f32, object type u32, model table u32,
    ad-table count u32 (=1), ad-table length u32 (=0) -> 54 bytes fixed.
    entries: (action_tree, guard_tree, ttas_index)."""
    if len(entry_template) != 54:
        raise ValueError(f"TTAB v0x54 entry template must be 54 bytes, got {len(entry_template)}")
    out = bytearray(_name64(name))
    out += struct.pack('<IIIH', 0xFFFFFFFF, 0x54, 0, len(entries))
    for action, guard, sidx in entries:
        e = bytearray(entry_template)
        struct.pack_into('<HH', e, 0, action, guard)
        struct.pack_into('<I', e, 8, sidx)
        out += e
    raw = name.encode('latin-1')
    out += struct.pack('<I', len(raw)) + raw
    return bytes(out)


def ttab_entry_template(donor_ttab: bytes) -> bytes:
    """Most-typical 54-byte entry from a v0x54 donor TTAB: picks the entry
    whose flag words are the most common pair in the table (a plain,
    always-available menu action)."""
    ver, = struct.unpack_from('<I', donor_ttab, 68)
    if ver != 0x54:
        raise ValueError(f"donor TTAB version {ver:#x}, need 0x54")
    cnt, = struct.unpack_from('<H', donor_ttab, 76)
    entries = [donor_ttab[78 + i * 54: 78 + (i + 1) * 54] for i in range(cnt)]
    from collections import Counter
    common = Counter(e[4:8] for e in entries).most_common(1)[0][0]
    return bytes(next(e for e in entries if e[4:8] == common))


# ---- OBJD --------------------------------------------------------------------

def patch_objd(donor: bytes, *, filename: str, guid: int, attr_count: int,
               price: int | None = None, hidden: bool = False) -> bytes:
    words = list(struct.unpack_from('<108H', donor, 64))
    words[14], words[15] = guid & 0xFFFF, guid >> 16
    words[52], words[53] = guid & 0xFFFF, guid >> 16   # job object GUID: donor mirrors own GUID
    words[58] = attr_count
    if price is not None:
        words[18] = price
    if hidden:
        # ACR token style: no interaction table, no catalog sort flags
        words[7] = 0          # interaction table (TTAB) id
        words[39] = 0         # room sort flags
        words[40] = 0         # function sort flags
    raw = filename.encode('latin-1')
    return (_name64(filename) + struct.pack('<108H', *words)
            + struct.pack('<I', len(raw)) + raw)


# ============================================================================
# Parsers — read side, for read-modify-write editing of existing packages
# ============================================================================

# ---- STR# / TTAs / CTSS ------------------------------------------------------

STR_FMT_WITH_DESC = 0xFFFD    # lang, value cstring, desc cstring
STR_FMT_NO_DESC = 0xFFFF      # lang, value cstring


@dataclass
class StrEntry:
    lang: int
    value: str
    desc: str = ''


@dataclass
class StrResource:
    """A parsed STR#/TTAs/CTSS string table."""
    name: str
    format: int
    entries: list[StrEntry]
    trailing: bytes = b''       # bytes past the last counted entry, if any
    _name_raw: bytes | None = field(default=None, repr=False)

    def values(self, lang: int | None = 1) -> list[str]:
        """Just the strings, optionally restricted to one language code."""
        return [e.value for e in self.entries if lang is None or e.lang == lang]

    def __getitem__(self, i: int) -> str:
        return self.entries[i].value

    def __len__(self) -> int:
        return len(self.entries)


def parse_str(data: bytes) -> StrResource:
    """Parse a STR#, TTAs, or CTSS resource (must already be decompressed)."""
    if len(data) < 68:
        raise ValueError(f"STR# data too short ({len(data)} bytes)")
    fmt, count = struct.unpack_from('<HH', data, 64)
    if fmt not in (STR_FMT_WITH_DESC, STR_FMT_NO_DESC):
        raise ValueError(f"Unsupported STR# format 0x{fmt:04X}")

    entries: list[StrEntry] = []
    pos = 68
    for i in range(count):
        if pos >= len(data):
            raise ValueError(f"STR# truncated: {i} of {count} entries read")
        lang = data[pos]
        pos += 1
        value, pos = _read_cstring(data, pos)
        if fmt == STR_FMT_WITH_DESC:
            desc, pos = _read_cstring(data, pos)
        else:
            desc = ''
        entries.append(StrEntry(lang, value, desc))

    return StrResource(_read_name64(data), fmt, entries, data[pos:], data[:64])


def _read_cstring(data: bytes, pos: int) -> tuple[str, int]:
    end = data.find(b'\x00', pos)
    if end < 0:
        raise ValueError("unterminated string in STR# resource")
    return data[pos:end].decode('latin-1', 'replace'), end + 1


def build_str(res: StrResource) -> bytes:
    """Serialize a StrResource. Inverse of parse_str."""
    out = bytearray(_emit_name64(res.name, res._name_raw))
    out += struct.pack('<HH', res.format, len(res.entries))
    for e in res.entries:
        out += bytes([e.lang]) + e.value.encode('latin-1', 'replace') + b'\x00'
        if res.format == STR_FMT_WITH_DESC:
            out += e.desc.encode('latin-1', 'replace') + b'\x00'
    return bytes(out + res.trailing)


# ---- TTAB --------------------------------------------------------------------

# version -> (entry size, offset of the TTAs index within an entry).
# v0x4F carries a 28-byte motive-advertisement block at +4 that v0x54 drops.
TTAB_LAYOUTS = {0x4F: (74, 36), 0x54: (54, 8)}


@dataclass
class TtabEntry:
    """One interaction. Unnamed fields stay in `raw` so edits are surgical."""
    raw: bytearray
    _ttas_offset: int = field(repr=False, default=8)

    @property
    def action(self) -> int:
        return struct.unpack_from('<H', self.raw, 0)[0]

    @action.setter
    def action(self, v: int) -> None:
        struct.pack_into('<H', self.raw, 0, v)

    @property
    def guard(self) -> int:
        return struct.unpack_from('<H', self.raw, 2)[0]

    @guard.setter
    def guard(self, v: int) -> None:
        struct.pack_into('<H', self.raw, 2, v)

    @property
    def ttas_index(self) -> int:
        return struct.unpack_from('<I', self.raw, self._ttas_offset)[0]

    @ttas_index.setter
    def ttas_index(self, v: int) -> None:
        struct.pack_into('<I', self.raw, self._ttas_offset, v)

    def __str__(self) -> str:
        return (f"action=0x{self.action:04X} guard=0x{self.guard:04X} "
                f"ttas={self.ttas_index}")


@dataclass
class Ttab:
    name: str
    version: int
    entries: list[TtabEntry]
    trailing_name: str = 'Interaction Table'
    _name_raw: bytes | None = field(default=None, repr=False)

    def __len__(self) -> int:
        return len(self.entries)


def parse_ttab(data: bytes) -> Ttab:
    """Parse a TTAB (interaction table). Handles versions 0x4F and 0x54."""
    if len(data) < 78:
        raise ValueError(f"TTAB data too short ({len(data)} bytes)")
    _marker, version, _zero, count = struct.unpack_from('<IIIH', data, 64)
    if version not in TTAB_LAYOUTS:
        raise ValueError(
            f"Unsupported TTAB version 0x{version:X} (known: "
            + ", ".join(f"0x{v:X}" for v in TTAB_LAYOUTS) + ")")
    size, ttas_off = TTAB_LAYOUTS[version]

    end = 78 + count * size
    if end + 4 > len(data):
        raise ValueError(
            f"TTAB truncated: {count} x {size}-byte entries need {end} bytes, "
            f"resource is {len(data)}")

    entries = [TtabEntry(bytearray(data[78 + i * size: 78 + (i + 1) * size]), ttas_off)
               for i in range(count)]

    name_len, = struct.unpack_from('<I', data, end)
    trailing = data[end + 4: end + 4 + name_len].decode('latin-1', 'replace')
    return Ttab(_read_name64(data), version, entries, trailing, data[:64])


def build_ttab(t: Ttab) -> bytes:
    """Serialize a Ttab. Inverse of parse_ttab."""
    size, _ = TTAB_LAYOUTS[t.version]
    out = bytearray(_emit_name64(t.name, t._name_raw))
    out += struct.pack('<IIIH', 0xFFFFFFFF, t.version, 0, len(t.entries))
    for e in t.entries:
        if len(e.raw) != size:
            raise ValueError(
                f"entry is {len(e.raw)} bytes, TTAB v0x{t.version:X} needs {size}")
        out += e.raw
    raw = t.trailing_name.encode('latin-1', 'replace')
    return bytes(out + struct.pack('<I', len(raw)) + raw)


# ---- OBJf --------------------------------------------------------------------

# Only the slots confirmed against a donor are named; see the module docstring.
# Unnamed slots print as "function N" rather than risk a wrong label.
OBJF_SLOTS = {0: 'init', 1: 'main'}


@dataclass
class ObjfEntry:
    guard: int
    action: int


@dataclass
class Objf:
    """Object function table: fixed-length list of (guard, action) tree ids."""
    filename: str
    entries: list[ObjfEntry]
    header: bytes = b'\x00' * 8      # the two u32s before the signature
    _name_raw: bytes | None = field(default=None, repr=False)

    def slot_name(self, i: int) -> str:
        return OBJF_SLOTS.get(i, f'function {i}')

    def used(self) -> list[tuple[int, ObjfEntry]]:
        """Slots that actually point at a tree — most of the table is empty."""
        return [(i, e) for i, e in enumerate(self.entries) if e.action or e.guard]


def parse_objf(data: bytes) -> Objf:
    """Parse an OBJf (object function table)."""
    if len(data) < 80:
        raise ValueError(f"OBJf data too short ({len(data)} bytes)")
    sig, count = struct.unpack_from('<II', data, 72)
    if sig != TYPE_OBJF:
        raise ValueError(f"bad OBJf signature 0x{sig:08X} at +72")
    expected = 80 + count * 4
    if expected != len(data):
        raise ValueError(
            f"OBJf count {count} implies {expected} bytes, resource is {len(data)}")

    entries = [ObjfEntry(*struct.unpack_from('<HH', data, 80 + i * 4))
               for i in range(count)]
    return Objf(_read_name64(data), entries, data[64:72], data[:64])


def build_objf(o: Objf) -> bytes:
    """Serialize an Objf. Inverse of parse_objf."""
    out = bytearray(_emit_name64(o.filename, o._name_raw))
    out += o.header
    out += struct.pack('<II', TYPE_OBJF, len(o.entries))
    for e in o.entries:
        out += struct.pack('<HH', e.guard, e.action)
    return bytes(out)


# ---- OBJD --------------------------------------------------------------------

OBJD_WORD_COUNT = 108

# word index -> attribute name, for the fields we have confirmed. GUID-style
# pairs are exposed separately below as combined 32-bit properties.
OBJD_FIELDS = {
    'version': 0,
    'ttab_id': 7,
    'obj_type': 9,
    'price': 18,
    'room_sort_flags': 39,
    'function_sort_flags': 40,
    'ctss_id': 41,
    'attr_count': 58,
}


@dataclass
class Objd:
    """Object definition. `words` is the source of truth; the named
    properties are typed views onto it."""
    filename: str
    words: list[int]
    name: str
    _name_raw: bytes | None = field(default=None, repr=False)

    def __getattr__(self, attr: str) -> int:
        # Only reached for names not found normally, so dataclass fields win.
        if attr in OBJD_FIELDS:
            return self.words[OBJD_FIELDS[attr]]
        raise AttributeError(attr)

    def __setattr__(self, attr: str, value) -> None:
        if attr in OBJD_FIELDS:
            self.words[OBJD_FIELDS[attr]] = value & 0xFFFF
        else:
            object.__setattr__(self, attr, value)

    def _u32(self, lo_word: int) -> int:
        return self.words[lo_word] | (self.words[lo_word + 1] << 16)

    def _set_u32(self, lo_word: int, v: int) -> None:
        self.words[lo_word] = v & 0xFFFF
        self.words[lo_word + 1] = (v >> 16) & 0xFFFF

    @property
    def guid(self) -> int:
        return self._u32(14)

    @guid.setter
    def guid(self, v: int) -> None:
        self._set_u32(14, v)

    @property
    def job_guid(self) -> int:
        return self._u32(52)

    @job_guid.setter
    def job_guid(self, v: int) -> None:
        self._set_u32(52, v)

    @property
    def original_guid(self) -> int:
        return self._u32(70)

    @original_guid.setter
    def original_guid(self, v: int) -> None:
        self._set_u32(70, v)

    def __str__(self) -> str:
        return (f'OBJD "{self.name}"  guid=0x{self.guid:08X}  '
                f'ver={self.version}  type={self.obj_type}  price={self.price}  '
                f'ttab={self.ttab_id}  ctss={self.ctss_id}  '
                f'attrs={self.attr_count}')


def parse_objd(data: bytes) -> Objd:
    """Parse an OBJD (object definition)."""
    end = 64 + OBJD_WORD_COUNT * 2
    if len(data) < end + 4:
        raise ValueError(f"OBJD data too short ({len(data)} bytes)")
    words = list(struct.unpack_from(f'<{OBJD_WORD_COUNT}H', data, 64))
    name_len, = struct.unpack_from('<I', data, end)
    name = data[end + 4: end + 4 + name_len].decode('latin-1', 'replace')
    return Objd(_read_name64(data), words, name, data[:64])


def build_objd(o: Objd) -> bytes:
    """Serialize an Objd. Inverse of parse_objd."""
    if len(o.words) != OBJD_WORD_COUNT:
        raise ValueError(f"OBJD needs {OBJD_WORD_COUNT} words, got {len(o.words)}")
    raw = o.name.encode('latin-1', 'replace')
    return (_emit_name64(o.filename, o._name_raw)
            + struct.pack(f'<{OBJD_WORD_COUNT}H', *o.words)
            + struct.pack('<I', len(raw)) + raw)


# ---- BCON --------------------------------------------------------------------

# Layout confirmed against all 462 BCONs in the game's own packages plus the
# local Downloads folder: a 64-byte name, a one-byte count, a one-byte flag,
# then that many u16 constants. The count is a *byte* — reading it as a u16
# works for half the corpus and then fails on every resource with the flag
# set, because the flag lands in the high byte (0x8008 reads as 32776).

@dataclass
class Bcon:
    """Behaviour constants — the numeric tuning table a BHAV reads from.

    This is what "tuning a mod" usually means: the values live here, and the
    BHAV indexes into them. TRCN names these entries, but that resource is a
    separate format and is not parsed yet.
    """
    filename: str
    values: list[int]
    flag: int = 0                    # 0 or 0x80 across the corpus; preserved
    _name_raw: bytes | None = field(default=None, repr=False)


def parse_bcon(data: bytes) -> Bcon:
    """Parse a BCON (behaviour constants table)."""
    if len(data) < 66:
        raise ValueError(f"BCON data too short ({len(data)} bytes)")
    count, flag = data[64], data[65]
    expected = 66 + count * 2
    if expected != len(data):
        raise ValueError(
            f"BCON count {count} implies {expected} bytes, resource is {len(data)}")
    values = list(struct.unpack_from(f'<{count}H', data, 66))
    return Bcon(_read_name64(data), values, flag, data[:64])


def build_bcon(b: Bcon) -> bytes:
    """Serialize a Bcon. Inverse of parse_bcon."""
    if len(b.values) > 0xFF:
        raise ValueError(f"BCON holds at most 255 constants, got {len(b.values)}")
    return (_emit_name64(b.filename, b._name_raw)
            + bytes((len(b.values), b.flag))
            + struct.pack(f'<{len(b.values)}H', *b.values))


# ---- GLOB --------------------------------------------------------------------

@dataclass
class Glob:
    """Semi-global reference — which shared tree set the object inherits.

    One length-prefixed name after the 64-byte header, and that is the whole
    resource for 166 of the 170 in the corpus. The other four carry trailing
    filler (0xA3 bytes) after the string, which `_tail` preserves so a
    round-trip stays byte-identical.
    """
    filename: str
    semi_global: str
    _name_raw: bytes | None = field(default=None, repr=False)
    _tail: bytes = b''


def parse_glob(data: bytes) -> Glob:
    """Parse a GLOB (semi-global reference)."""
    if len(data) < 65:
        raise ValueError(f"GLOB data too short ({len(data)} bytes)")
    length = data[64]
    end = 65 + length
    if end > len(data):
        raise ValueError(
            f"GLOB names {length} bytes but only {len(data) - 65} follow")
    return Glob(_read_name64(data), data[65:end].decode('latin-1'),
                data[:64], data[end:])


def build_glob(g: Glob) -> bytes:
    """Serialize a Glob. Inverse of parse_glob."""
    raw = g.semi_global.encode('latin-1', 'replace')
    if len(raw) > 0xFF:
        raise ValueError(f"GLOB name is at most 255 bytes, got {len(raw)}")
    return _emit_name64(g.filename, g._name_raw) + bytes((len(raw),)) + raw + g._tail


# ---- BHAV round trip ---------------------------------------------------------
#
# s2parser.parse_bhav is the decompiler's reader: it keeps what a listing
# needs and drops the rest — the extra header bytes after the counts, the
# flags byte, each instruction's tail byte, and anything past a short read.
# An editor needs the inverse to be exact, so this pair keeps every byte it
# does not interpret and rebuilds the resource identically (see _selftest,
# which holds it to that over the game's own objects.package).

# The per-format layouts live in s2parser, which owns the decompiler; the
# editable model here is the 0x8007 shape for every format, so an edit
# works the same way on a base-game 0x8002 tree as on a modern one.
BHAV_LAYOUTS = s2parser.BHAV_LAYOUTS
BHAV_INSTR_SIZE = BHAV_LAYOUTS[0x8007].instr_size
BHAV_EXTRA_HEADER = {v: l.extra_header for v, l in BHAV_LAYOUTS.items()}
BHAV_SENTINEL_FLOOR = s2parser.BHAV_SENTINEL_FLOOR


_widen_dest = s2parser.widen_dest


def _narrow_dest(d: int, n: int) -> int:
    if d >= BHAV_SENTINEL_FLOOR:
        return d & 0xFF
    if d > 0xFB:
        raise ValueError(f"instruction {n}: target {d} does not fit a one-byte format "
                         f"(max 251); convert the tree to format 0x8007 first")
    return d


@dataclass
class BhavInstr:
    opcode: int
    true_dest: int
    false_dest: int
    operands: bytes                  # 16 bytes in the model; 8-byte formats pad with zeros
    tail: bytes = b'\x00'            # the per-instruction byte in 0x8005+, meaning unknown


@dataclass
class BhavRes:
    """A BHAV as an editable record: header fields, instructions, and the
    bytes around them that the format does not explain."""
    name: str
    format_version: int
    bhav_type: int
    argc: int
    localc: int
    flags: int                       # signed byte in the header
    extra_header: bytes              # 4 or 5 bytes after the counts
    instructions: list[BhavInstr]
    # The header's instruction count, kept only when the resource is too
    # short to hold that many — so a truncated donor rebuilds identically.
    # Any edit to the instruction list resets it.
    declared_count: int | None = None
    _tail: bytes = b''
    _name_raw: bytes | None = field(default=None, repr=False)


def parse_bhav_rt(data: bytes) -> BhavRes:
    """Parse a BHAV of any known format so that build_bhav reproduces it
    byte for byte. Targets and operands are widened to the 0x8007 model."""
    if len(data) < 72:
        raise ValueError(f"BHAV data too short ({len(data)} bytes)")
    ver, count, bhav_type, argc, localc, flags = struct.unpack_from('<HHBBBb', data, 64)
    layout = BHAV_LAYOUTS.get(ver)
    if layout is None:
        raise ValueError(f"Unsupported BHAV format version 0x{ver:04X}")
    start = 72 + layout.extra_header
    if start > len(data):
        raise ValueError(f"BHAV header truncated ({len(data)} bytes)")
    addr_fmt = '<HBB' if layout.addr_width == 1 else '<HHH'
    ops_at = 2 + 2 * layout.addr_width
    size = layout.instr_size
    instrs: list[BhavInstr] = []
    pos = start
    for _ in range(count):
        if pos + size > len(data):
            break
        opcode, t, f = struct.unpack_from(addr_fmt, data, pos)
        ops = data[pos + ops_at:pos + ops_at + layout.operand_len]
        tail = data[pos + ops_at + layout.operand_len:pos + size]
        if layout.addr_width == 1:
            t, f = _widen_dest(t), _widen_dest(f)
        instrs.append(BhavInstr(opcode, t, f, ops + bytes(16 - len(ops)), tail))
        pos += size
    declared = count if len(instrs) != count else None
    return BhavRes(_read_name64(data), ver, bhav_type, argc, localc, flags,
                   data[72:start], instrs, declared, data[pos:], data[:64])


def build_bhav(b: BhavRes) -> bytes:
    """Serialize a BhavRes. Inverse of parse_bhav_rt."""
    layout = BHAV_LAYOUTS.get(b.format_version)
    if layout is None:
        raise ValueError(f"Unsupported BHAV format version 0x{b.format_version:04X}")
    if len(b.extra_header) != layout.extra_header:
        raise ValueError(f"format 0x{b.format_version:04X} needs {layout.extra_header} extra "
                         f"header bytes, got {len(b.extra_header)}")
    count = b.declared_count if b.declared_count is not None else len(b.instructions)
    if count > 0xFFFF:
        raise ValueError("a BHAV holds at most 65535 instructions")
    out = bytearray(_emit_name64(b.name, b._name_raw))
    out += struct.pack('<HHBBBb', b.format_version, count, b.bhav_type & 0xFF,
                       b.argc & 0xFF, b.localc & 0xFF, b.flags)
    out += b.extra_header
    for n, ins in enumerate(b.instructions):
        if len(ins.operands) != 16:
            raise ValueError(f"instruction {n}: operands must be 16 bytes, got {len(ins.operands)}")
        if len(ins.tail) != layout.tail_len:
            raise ValueError(f"instruction {n}: format 0x{b.format_version:04X} has a "
                             f"{layout.tail_len}-byte tail, got {len(ins.tail)}")
        if layout.operand_len < 16 and any(ins.operands[layout.operand_len:]):
            raise ValueError(f"instruction {n}: format 0x{b.format_version:04X} holds only "
                             f"{layout.operand_len} operand bytes; convert the tree to 0x8007 "
                             f"to use the rest")
        t, f = ins.true_dest & 0xFFFF, ins.false_dest & 0xFFFF
        if layout.addr_width == 1:
            out += struct.pack('<HBB', ins.opcode & 0xFFFF, _narrow_dest(t, n), _narrow_dest(f, n))
        else:
            out += struct.pack('<HHH', ins.opcode & 0xFFFF, t, f)
        out += ins.operands[:layout.operand_len] + ins.tail
    return bytes(out + b._tail)


def bhav_convert(b: BhavRes, version: int = 0x8007) -> None:
    """Rewrite a tree in place to another format version — the way to give a
    cloned base-game object's 12-byte trees room for 16 operand bytes and
    more than 251 instructions. The model already holds the wide form, so
    only the header region and the per-instruction tail need shaping."""
    layout = BHAV_LAYOUTS.get(version)
    if layout is None:
        raise ValueError(f"Unsupported BHAV format version 0x{version:04X}")
    old = BHAV_LAYOUTS[b.format_version]
    extra = b.extra_header[:old.extra_header]
    b.extra_header = (extra + bytes(layout.extra_header))[:layout.extra_header]
    for ins in b.instructions:
        ins.tail = (ins.tail + bytes(layout.tail_len))[:layout.tail_len]
        if old.addr_width == 1 and layout.addr_width == 2:
            # Old trees spell the error exit 0xFF; new ones 0xFFFC.
            if ins.true_dest == 0xFFFF:
                ins.true_dest = 0xFFFC
            if ins.false_dest == 0xFFFF:
                ins.false_dest = 0xFFFC
    b.format_version = version


def bhav_to_listing(b: BhavRes):
    """The decompiler's view of an editable BHAV, for s2parser.render_bhav_tree."""
    return s2parser.Bhav(b.name, b.format_version, b.bhav_type, b.argc, b.localc,
                         [s2parser.BhavInstruction(i.opcode, i.true_dest, i.false_dest, i.operands)
                          for i in b.instructions])


# Operand layouts the editor can show by name. Only what has been pinned
# against donors is here; every other opcode is edited as 16 raw bytes.
# Each field: name, byte offset in the 16-byte operand block, width, and an
# optional value -> label table. The Expression operator and owner tables
# beyond the values confirmed above (0x02/0x03/0x05; owners 0x00/0x01/0x07/
# 0x08/0x09/0x19) follow SimPE's expression editor.
_EXPR_OPERATORS = {
    0x00: '>', 0x01: '<', 0x02: '==', 0x03: '+=', 0x04: '-=', 0x05: ':=',
    0x06: '*=', 0x07: '/=', 0x0E: '!=', 0x0F: '>=', 0x10: '<=',
}
_DATA_OWNERS = {
    OWNER_MY_ATTR: 'my attribute', OWNER_STACKOBJ_ATTR: 'stack object attribute',
    OWNER_LITERAL: 'literal', OWNER_TEMP: 'temp', OWNER_PARAM: 'param', OWNER_LOCAL: 'local',
}
_INV_OPERATIONS = {
    INV_COUNT: 'count -> value var',
}
BHAV_OPERAND_LAYOUTS = {
    OP_EXPRESSION: [
        {'name': 'flag', 'offset': 0, 'size': 1},
        {'name': 'lhs', 'offset': 1, 'size': 2},
        {'name': 'rhs', 'offset': 3, 'size': 2},
        {'name': 'operator', 'offset': 6, 'size': 1, 'values': _EXPR_OPERATORS},
        {'name': 'lhs_owner', 'offset': 7, 'size': 1, 'values': _DATA_OWNERS},
        {'name': 'rhs_owner', 'offset': 8, 'size': 1, 'values': _DATA_OWNERS},
    ],
    OP_DIALOG: [
        {'name': 'string_index', 'offset': 14, 'size': 2},
    ],
    OP_INVENTORY: [
        {'name': 'category', 'offset': 0, 'size': 1},
        {'name': 'inventory', 'offset': 1, 'size': 1, 'values': {INV_GLOBAL: 'global (gossip store)'}},
        {'name': 'owner_scope', 'offset': 2, 'size': 1},
        {'name': 'owner_id', 'offset': 3, 'size': 2},
        {'name': 'operation', 'offset': 5, 'size': 1, 'values': _INV_OPERATIONS},
        {'name': 'guid', 'offset': 6, 'size': 4},
        {'name': 'sel_scope', 'offset': 11, 'size': 1},
        {'name': 'sel_id', 'offset': 12, 'size': 1},
        {'name': 'val_scope', 'offset': 14, 'size': 1},
        {'name': 'val_id', 'offset': 15, 'size': 1},
    ],
    # GUID literal at +1, the slot s2clone patches (see GUID_OPERANDS there).
    0x001F: [{'name': 'guid', 'offset': 1, 'size': 4}],
    0x0020: [{'name': 'guid', 'offset': 1, 'size': 4}],
}


# ---- dispatch ----------------------------------------------------------------

# type id -> (parser, builder). STR#/TTAs/CTSS share one string-table format.
# ---- TPRP: behaviour function labels ------------------------------------------
#
# The names a BHAV's parameters and locals were given in the editor that
# wrote it. Layout, checked against 5,067 resources across every version
# 0x43–0x4E in the game's lot packages and sample-packages/:
#
#   64  filename            "PRPT"  u32 version  u32 0  u32 nparams  u32 nlocals
#   nparams + nlocals strings, each a 7-bit varint length then Latin-1 bytes
#   u32 0   nparams bytes (one flag per parameter, 0 or 1)   u32 5   u32 0
#
# The trailing words are constant in every sample but their meaning is not
# known, so they are kept as bytes and written back as read.

TYPE_TPRP = 0x54505250


@dataclass
class Tprp:
    name: str
    version: int
    params: list[str]
    locals: list[str]
    param_flags: bytes          # one byte per parameter
    unknown: int = 0            # u32 after the header, always 0 in samples
    unknown2: int = 0           # u32 after the labels, always 0 in samples
    tail: bytes = b'\x05\x00\x00\x00\x00\x00\x00\x00'
    _name_raw: bytes | None = field(default=None, repr=False)


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    n = shift = 0
    while True:
        c = data[pos]
        pos += 1
        n |= (c & 0x7F) << shift
        shift += 7
        if not c & 0x80:
            return n, pos


def _emit_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def parse_tprp(data: bytes) -> Tprp:
    if len(data) < 84 or data[64:68] != b'PRPT':
        raise ValueError('not a TPRP (no PRPT tag at 0x40)')
    version, unknown, np, nl = struct.unpack_from('<4I', data, 68)
    pos = 84
    labels = []
    for _ in range(np + nl):
        n, pos = _read_varint(data, pos)
        if pos + n > len(data):
            raise ValueError('TPRP label runs past the end')
        labels.append(data[pos:pos + n].decode('latin-1'))
        pos += n
    if pos + 4 + np > len(data):
        raise ValueError('TPRP truncated after its labels')
    unknown2, = struct.unpack_from('<I', data, pos)
    pos += 4
    flags = data[pos:pos + np]
    pos += np
    return Tprp(_read_name64(data), version, labels[:np], labels[np:], flags,
                unknown, unknown2, data[pos:], data[:64])


def build_tprp(t: Tprp) -> bytes:
    if len(t.param_flags) != len(t.params):
        raise ValueError(f'{len(t.params)} parameters but {len(t.param_flags)} parameter flags')
    out = bytearray(_emit_name64(t.name, t._name_raw))
    out += b'PRPT' + struct.pack('<4I', t.version, t.unknown, len(t.params), len(t.locals))
    for label in t.params + t.locals:
        raw = label.encode('latin-1', 'replace')
        out += _emit_varint(len(raw)) + raw
    out += struct.pack('<I', t.unknown2) + bytes(t.param_flags) + t.tail
    return bytes(out)


PARSERS = {
    TYPE_BHAV: (parse_bhav_rt, build_bhav),
    TYPE_TPRP: (parse_tprp, build_tprp),
    TYPE_BCON: (parse_bcon, build_bcon),
    TYPE_GLOB: (parse_glob, build_glob),
    TYPE_STR: (parse_str, build_str),
    TYPE_TTAS: (parse_str, build_str),
    TYPE_CTSS: (parse_str, build_str),
    TYPE_TTAB: (parse_ttab, build_ttab),
    TYPE_OBJF: (parse_objf, build_objf),
    TYPE_OBJD: (parse_objd, build_objd),
}


def parse_resource(type_id: int, data: bytes):
    """Parse any resource this module understands, or raise KeyError."""
    return PARSERS[type_id][0](data)


def build_resource(type_id: int, obj) -> bytes:
    return PARSERS[type_id][1](obj)


# ---- self-test ---------------------------------------------------------------

def _selftest(sample_dir: str = 'sample-packages') -> int:
    """Round-trip every parseable resource in every sample package.

    A parser that silently drops bytes is worse than no parser, so the bar
    here is byte-identical output, not "it parsed without raising".
    """
    from collections import Counter, defaultdict
    from pathlib import Path
    import s2parser

    ok = skipped = 0
    failures: list[str] = []
    # masked message -> {concrete message: count}. Grouping on the raw text
    # does not work: only some of this module's messages put their numbers in
    # a trailing parenthetical, so the rest would give one summary line per
    # resource and the summary would stop summarising.
    gaps: "dict[str, Counter[str]]" = defaultdict(Counter)
    # Recurse, so the game's own install and a Downloads folder can be used as
    # a wide corpus. sample-packages/ holds only a handful of some types —
    # BCON 6, GLOB 1, TPRP 1, SLOT 1 and no TRCN at all — which is too thin to
    # prove a parser on its own.
    packages = sorted(Path(sample_dir).rglob('*.package'))
    if not packages:
        print(f"no packages found in {sample_dir}/")
        return 1

    for path in packages:
        try:
            header, entries = s2parser.open_package(path)
        except Exception as exc:
            failures.append(f"{path.name}: cannot open ({exc})")
            continue
        with open(path, 'rb') as f:
            for e in entries:
                if e.type_id not in PARSERS:
                    continue
                data = s2parser.read_resource(f, e)
                label = f"{path.name} {e.type_name} i={e.instance:08x}"
                try:
                    obj = parse_resource(e.type_id, data)
                except ValueError as exc:
                    # A parser that *declines* a resource — an unsupported
                    # version, a length it can't square — is a coverage gap,
                    # not a round-trip failure. Only a parser that accepts a
                    # resource and then can't reproduce it is a bug. Keeping
                    # the two apart is what makes a wide corpus usable: over
                    # the game's Downloads folder the known TTAB version gap
                    # alone raises 165 of these, and a real regression would
                    # be invisible among them.
                    skipped += 1
                    gaps[_gap_template(str(exc))][str(exc)] += 1
                    continue
                rebuilt = build_resource(e.type_id, obj)
                if rebuilt == data:
                    ok += 1
                else:
                    failures.append(
                        f"{label}: round-trip differs "
                        f"({len(data)} -> {len(rebuilt)} bytes, "
                        f"first diff at {_first_diff(data, rebuilt)})")

    print(f"round-trip: {ok} resource(s) byte-identical across "
          f"{len(packages)} package(s)")
    if skipped:
        print(f"declined by a parser: {skipped} resource(s)")
        for template, variants in sorted(gaps.items(),
                                         key=lambda kv: -sum(kv[1].values())):
            print(f"  {sum(variants.values()):5} x {template}")
            # The template masks the numbers, so show a few concrete messages
            # to keep the detail that actually identifies a gap — which TTAB
            # versions are missing, say, rather than just "some version".
            for msg, n in variants.most_common(_GAP_VARIANTS):
                print(f"          {n:5} {msg}")
            if len(variants) > _GAP_VARIANTS:
                print(f"          ... and {len(variants) - _GAP_VARIANTS} more")
    for msg in failures:
        print(f"  FAIL {msg}")
    if failures:
        return 1
    if ok == 0:
        # Every parseable resource was declined. Nothing was verified, so
        # this is not a pass — a harness that reports success having checked
        # nothing is worse than one that fails.
        print("nothing verified: every parseable resource was declined")
        return 1
    return 0


# How many concrete messages to show under each masked template.
_GAP_VARIANTS = 4

_GAP_NUMBER = re.compile(r'0x[0-9A-Fa-f]+|\d+')


def _gap_template(message: str) -> str:
    """Mask the numbers out of a parser's message, for grouping declines.

    'BCON count 12 implies 90 bytes, resource is 88' and the same message
    with different sizes are one gap, not two. The concrete messages are
    still shown underneath, so nothing is lost.
    """
    return _GAP_NUMBER.sub('#', message)


def _first_diff(a: bytes, b: bytes) -> int | str:
    for i in range(min(len(a), len(b))):
        if a[i] != b[i]:
            return i
    return 'length only'


if __name__ == '__main__':
    import sys
    sys.exit(_selftest(*sys.argv[1:]))
