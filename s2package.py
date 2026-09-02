#!/usr/bin/env python3
"""In-memory operations on a package's resource list.

The Sim Studio daemon (s2studio.py) holds an open package as the plain
`list[s2writer.Resource]` that `s2writer.read_all_resources` returns, and
edits it through the functions here. Nothing in this module touches a file:
it is the bit of an editor that can be reasoned about without I/O, and the
bit the daemon's undo stack has to be able to reverse exactly.

A TGI is the 4-tuple `(type, group, instance, instance_hi)` that
`Resource.tgi()` returns. Position in the list is preserved across a
delete-then-undo, because the game reads resources by TGI but SimPE users
notice when an index shuffles.
"""

# Annotations stay strings so the modules import under the system
# python3 (3.9), which the app gets when launched from Finder.
from __future__ import annotations

import s2parser
from s2writer import Resource

TGI = "tuple[int, int, int, int]"


class LazyResource(Resource):
    """A Resource that stays QFS-packed until its bytes are first needed.

    The game's objects.package holds 48,000 compressed resources — 128 MB
    once inflated — and decompressing all of it in pure Python takes minutes.
    A user opening it to edit one string should not wait for that, and a
    Save As should not spend minutes recompressing 48,000 resources nobody
    touched. So a compressed resource keeps its on-disk form here, inflates
    on first access to `data`, and forgets the packed form the moment
    `data` is assigned, since it no longer describes those bytes.
    `s2writer.write_package` writes a still-packed resource as-is.
    """

    def __init__(self, type_id: int, group_id: int, instance_id: int,
                 packed: bytes, instance_hi: int = 0):
        # Deliberately not Resource.__init__: that assigns `data`, which is a
        # property here, and the assignment would count as an edit.
        self.type_id = type_id
        self.group_id = group_id
        self.instance_id = instance_id
        self.instance_hi = instance_hi
        self._packed: "bytes | None" = packed
        self._plain: "bytes | None" = None

    @property
    def data(self) -> bytes:
        if self._plain is None:
            self._plain = s2parser.qfs_decompress(self._packed)
        return self._plain

    @data.setter
    def data(self, value: bytes) -> None:
        self._plain = bytes(value)
        self._packed = None

    @property
    def packed(self) -> "bytes | None":
        """The original QFS stream, or None once the bytes have been edited."""
        return self._packed

    @property
    def size(self) -> int:
        """Uncompressed length, without inflating — the QFS header carries it."""
        if self._plain is not None:
            return len(self._plain)
        return int.from_bytes(self._packed[6:9], "big")

    def __repr__(self) -> str:
        state = "packed" if self._packed is not None else "plain"
        return (f"LazyResource({self.type_name}, g={self.group_id:08x}, "
                f"i={self.instance_id:08x}, {state}, {self.size} bytes)")


def size(res: Resource) -> int:
    """Uncompressed length of a resource, inflating nothing."""
    return res.size if isinstance(res, LazyResource) else len(res.data)


def head(res: Resource, n: int) -> bytes:
    """The first `n` uncompressed bytes, stopping the decompressor there
    rather than inflating a whole texture to read its name."""
    if isinstance(res, LazyResource) and res.packed is not None:
        return s2parser.qfs_decompress(res.packed, limit=n)
    return res.data[:n]


# Types whose payload opens with a 64-byte NUL-padded filename. Checked
# against every resource in the game's objects.package: BHAV, BCON and OBJD
# are named without exception; STR#, GLOB, TTAB and CTSS mostly; OBJf and
# TTAs carry the field but usually leave it blank.
NAME64_TYPES = frozenset({
    0x42484156, 0x53545223, 0x42434F4E, 0x4F424A44, 0x4F424A66, 0x474C4F42,
    0x54544142, 0x54544173, 0x43545353, 0x424D505F, 0x44475250, 0x53505232,
    0x54505250,
})
# RCOL documents name themselves in an embedded cSGResource block — right at
# the front for GMDC/SHPE/TXTR/TXMT, ~730 bytes in for a CRES.
SGRES_TYPES = frozenset({
    0xAC4F8687, 0x7BA3838C, 0xFC6EB1F7, 0xE519C933, 0x49596978, 0xFC4B284B,
    0x1C4A276C, 0xED534136, 0xFB00791E,
})
_SGRES_TAG = b"cSGResource"


def _printable(b: bytes) -> "str | None":
    if b and all(0x20 <= c < 0x7F for c in b):
        return b.decode("ascii")
    return None


def resource_name(res: Resource) -> "str | None":
    """The name a resource gives itself, or None when its type has none or
    the field is blank. This is what SimPE's Name column shows."""
    t = res.type_id
    if t in NAME64_TYPES:
        return _printable(head(res, 64).split(b"\0", 1)[0])
    if t == 0x4E524546:                        # NREF: the payload is the name
        return _printable(res.data.rstrip(b"\0"))
    if t in SGRES_TYPES:
        p = head(res, 2048)
        i = p.find(_SGRES_TAG)
        if i < 0:
            return None
        pos = i + len(_SGRES_TAG) + 8          # block type id + version
        if pos >= len(p) or p[pos] & 0x80:
            return None
        n = p[pos]
        return _printable(p[pos + 1:pos + 1 + n])
    return None


def parse_tgi(value) -> "tuple[int, int, int, int]":
    """Accept a TGI as a 3- or 4-element list, or a dict with named keys.

    The JSON side sends ints — u32 fits a JSON number and Swift decodes it
    into a UInt32 without a hex round-trip. `instance_hi` (the v7.2 resource
    id) is almost always 0 and may be left out.
    """
    if isinstance(value, dict):
        try:
            return (int(value["type"]), int(value["group"]),
                    int(value["instance"]), int(value.get("instance_hi", 0)))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"bad tgi {value!r}: {exc}") from None
    if isinstance(value, (list, tuple)) and len(value) in (3, 4):
        try:
            t, g, i = (int(x) for x in value[:3])
            r = int(value[3]) if len(value) == 4 else 0
        except (TypeError, ValueError) as exc:
            raise ValueError(f"bad tgi {value!r}: {exc}") from None
        return (t, g, i, r)
    raise ValueError(f"bad tgi {value!r}")


def tgi_json(tgi: "tuple[int, int, int, int]") -> dict:
    t, g, i, r = tgi
    return {"type": t, "group": g, "instance": i, "instance_hi": r}


def find(resources: "list[Resource]", tgi: "tuple[int, int, int, int]") -> int:
    """Index of the resource with this TGI, or -1."""
    for n, r in enumerate(resources):
        if r.tgi() == tgi:
            return n
    return -1


def get(resources: "list[Resource]", tgi: "tuple[int, int, int, int]") -> "Resource | None":
    n = find(resources, tgi)
    return resources[n] if n >= 0 else None


def add(resources: "list[Resource]", res: Resource, at: "int | None" = None) -> int:
    """Insert a resource, refusing a duplicate TGI. Returns its index.

    `write_package` would refuse the duplicate at save time anyway; refusing
    here means the user hears about it when they act, not minutes later.
    """
    if find(resources, res.tgi()) >= 0:
        raise ValueError(
            f"duplicate TGI: {res.type_name} g={res.group_id:08x} i={res.instance_id:08x}")
    if at is None or at >= len(resources):
        resources.append(res)
        return len(resources) - 1
    resources.insert(at, res)
    return at


def remove(resources: "list[Resource]", tgi: "tuple[int, int, int, int]") -> "tuple[int, Resource]":
    """Delete a resource. Returns (index it had, the resource)."""
    n = find(resources, tgi)
    if n < 0:
        raise KeyError(tgi)
    return n, resources.pop(n)


def rename(resources: "list[Resource]", tgi: "tuple[int, int, int, int]",
           new_tgi: "tuple[int, int, int, int]") -> Resource:
    """Re-key a resource in place, refusing a collision. Returns it."""
    n = find(resources, tgi)
    if n < 0:
        raise KeyError(tgi)
    if new_tgi != tgi and find(resources, new_tgi) >= 0:
        raise ValueError(f"duplicate TGI: {new_tgi}")
    r = resources[n]
    # Re-keyed in place rather than rebuilt: the caller has already
    # snapshotted it for undo, and a LazyResource has no field constructor.
    r.type_id, r.group_id, r.instance_id, r.instance_hi = new_tgi
    return r


def copy(res: Resource) -> Resource:
    """A snapshot of a resource, for the undo stack.

    `bytes` is immutable, so the snapshot shares payloads with the original
    and only the record is new. A still-packed LazyResource stays packed —
    snapshotting a 3 MB texture must not inflate it.
    """
    if isinstance(res, LazyResource) and res.packed is not None:
        c = LazyResource(res.type_id, res.group_id, res.instance_id, res.packed, res.instance_hi)
        c._plain = res._plain
        return c
    # The same bytes object on purpose: `same_bytes` can then tell an
    # untouched resource from a reassigned one by identity, without a compare.
    return Resource(res.type_id, res.group_id, res.instance_id, res.data, res.instance_hi)


def same_bytes(a: Resource, b: Resource) -> bool:
    """Whether two resources hold the same payload, inflating nothing that
    is still packed. Used to find what an in-place operation like a clone
    actually changed, across a 50,000-resource package."""
    if isinstance(a, LazyResource) and isinstance(b, LazyResource):
        if a.packed is not None and b.packed is not None:
            return a.packed is b.packed or a.packed == b.packed
    if isinstance(a, LazyResource) and a.packed is not None and not isinstance(b, LazyResource):
        return False
    return a.data is b.data or a.data == b.data


# ---- BHAV instruction editing -------------------------------------------------
#
# Branch targets are instruction indices, so inserting, deleting, or moving
# an instruction has to renumber every target that pointed past the change.
# Values from 0xFFFC up are exit sentinels (error / true / false) and are
# never touched. Each function edits the BhavRes in place and returns the
# warnings a user should see — a deleted instruction that something still
# jumped to, say.

SENTINEL_FLOOR = 0xFFFC
RET_ERROR_SENTINEL = 0xFFFC


def _is_sentinel(dest: int) -> bool:
    return dest >= SENTINEL_FLOOR


def _retarget(bhav, mapping) -> "list[str]":
    """Apply `mapping(old_index) -> new_index | None` to every branch target.
    None means the target no longer exists; it becomes the error sentinel."""
    warnings = []
    for n, ins in enumerate(bhav.instructions):
        for attr in ("true_dest", "false_dest"):
            d = getattr(ins, attr)
            if _is_sentinel(d):
                continue
            new = mapping(d)
            if new is None:
                setattr(ins, attr, RET_ERROR_SENTINEL)
                warnings.append(f"[{n}] {attr.split('_')[0]} branch pointed at the deleted "
                                f"instruction; now → ERROR")
            else:
                setattr(ins, attr, new)
    return warnings


def bhav_insert(bhav, index: int, instr=None) -> "list[str]":
    """Insert an instruction at `index` (0..len). Targets at or past `index`
    shift up by one, so the existing flow is unchanged and the new
    instruction is reached only by whatever is rewired to it."""
    from s2object import BHAV_LAYOUTS, BhavInstr
    count = len(bhav.instructions)
    if not 0 <= index <= count:
        raise ValueError(f"index {index} out of range 0..{count}")
    warnings = _retarget(bhav, lambda d: d + 1 if d >= index else d)
    if instr is None:
        # A Sleep for zero ticks that falls through: harmless until edited.
        # The tail matches the tree's format — older ones have none.
        tail = bytes(BHAV_LAYOUTS[bhav.format_version].tail_len)
        instr = BhavInstr(0x0000, min(index + 1, 0xFFFB), RET_ERROR_SENTINEL, bytes(16), tail)
        if index == count:
            instr.true_dest = 0xFFFD
    bhav.instructions.insert(index, instr)
    bhav.declared_count = None
    return warnings


def bhav_delete(bhav, index: int) -> "list[str]":
    count = len(bhav.instructions)
    if not 0 <= index < count:
        raise ValueError(f"index {index} out of range 0..{count - 1}")
    del bhav.instructions[index]
    bhav.declared_count = None
    return _retarget(bhav, lambda d: None if d == index else (d - 1 if d > index else d))


def bhav_move(bhav, index: int, to: int) -> "list[str]":
    """Move the instruction at `index` so it sits at `to`, renumbering every
    target so all branches still reach the same instructions."""
    count = len(bhav.instructions)
    if not (0 <= index < count and 0 <= to < count):
        raise ValueError(f"index out of range 0..{count - 1}")
    if index == to:
        return []
    order = list(range(count))
    order.insert(to, order.pop(index))          # order[new] = old
    new_of = {old: new for new, old in enumerate(order)}
    ins = bhav.instructions
    bhav.instructions = [ins[old] for old in order]
    bhav.declared_count = None
    return _retarget(bhav, lambda d: new_of.get(d))
