#!/usr/bin/env python3
"""Builders for Sims 2 object package resources (BHAV, STR#, TTAB, OBJD...).

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

STR#/TTAs/CTSS: 64B name + u16 0xFDFF + u16 count +
  count * (lang u8, value cstring, desc cstring)

TTAB: 64B name + u32 0xFFFFFFFF + u32 version(0x4F) + u32 0 + u16 count +
  count * 74-byte entries + u32 name-len + name.
  Entry: action tree u16 @+0, guard tree u16 @+2, TTAs index u32 @+36.

OBJD: 64B filename + 108 u16 fields + u32 name-len + name.
  Field map per SimsWiki 4F424A44 (byte offsets include the 64B filename):
  word 0 version (139/140), word 7 interaction table (TTAB) id,
  word 9 object TYPE (4 = buyable — never change), words 14/15 GUID lo/hi,
  word 18 price, word 41 catalog strings (CTSS) instance,
  words 52/53 job object GUID (donor mirrors its own GUID here),
  word 58 number of attributes (8 preallocated when 0),
  words 70/71 original (clone source) GUID.
"""

import struct

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
            sel_scope: int = 0, sel_id: int = 0,
            val_scope: int = 0, val_id: int = 0, cat: int = 0) -> bytes:
    """Manage Inventory (0x0033) operands. sel = iterator index var (find/bind/
    remove) or property selector (getprop/setprop); val = value var for
    getprop (dest) / setprop (src). Global inventory needs no owner fields.
    cat (b0) = token category: ffsdebugger filters urn tokens with cat=2;
    cat=0 appears to be a wildcard match on find."""
    return bytes([cat, inv_type, 0, 0, 0, operation,
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
    """entries: (action_tree, guard_tree, ttas_index)."""
    if len(entry_template) != 74:
        raise ValueError(f"TTAB entry template must be 74 bytes, got {len(entry_template)}")
    out = bytearray(_name64(name))
    out += struct.pack('<IIIH', 0xFFFFFFFF, 0x4F, 0, len(entries))
    for action, guard, sidx in entries:
        e = bytearray(entry_template)
        struct.pack_into('<HH', e, 0, action, guard)
        struct.pack_into('<I', e, 36, sidx)
        out += e
    raw = name.encode('latin-1')
    out += struct.pack('<I', len(raw)) + raw
    return bytes(out)


def ttab_entry_template(donor_ttab: bytes) -> bytes:
    """First 74-byte entry from a donor TTAB resource."""
    return bytes(donor_ttab[64 + 14: 64 + 14 + 74])


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
