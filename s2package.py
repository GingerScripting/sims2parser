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

from dataclasses import replace

from s2writer import Resource

TGI = "tuple[int, int, int, int]"


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
    t, g, i, hi = new_tgi
    resources[n] = replace(r, type_id=t, group_id=g, instance_id=i, instance_hi=hi)
    return resources[n]


def copy(res: Resource) -> Resource:
    """A snapshot of a resource, for the undo stack. `bytes` is immutable so
    only the record itself needs copying."""
    return replace(res, data=bytes(res.data))
