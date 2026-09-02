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
    return Resource(res.type_id, res.group_id, res.instance_id, bytes(res.data), res.instance_hi)
