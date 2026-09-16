#!/usr/bin/env python3
"""s2workshop.py — copy one object out of a package and make it a new one.

SimPE calls this step the Object Workshop. Sim Studio's New Object screen
runs it for every project: pick a base object, get an independent copy with
its own identity that the game lists beside the original.

Extraction: what is "the object"
--------------------------------
In the game's own objects.package every object keeps its resources in a
group of its own — OBJD, OBJf, NREF, CTSS, the private BHAVs, STR#s, BCON,
TTAB/TTAs, GLOB, SLOT — so the object is exactly "everything in the OBJD's
group". Nothing is pulled from the global group (0x7FD46CD0) or from the
semi-global group the GLOB names: the game resolves those from its own
files at run time, and a copy would turn the clone into a global hack.
Scenegraph resources found in the group (CRES/SHPE/GMND/GMDC/TXMT/TXTR/LIFO)
are found by name hash and must keep their group.

A custom package is taken whole (minus DIR), as s2clone.clone already does:
its object usually sits in group 0xFFFFFFFF, and anything it carries in
other groups (a semi-global override, say) comes along untouched.

Identity: what changes
----------------------
The copied group is renumbered to 0xFFFFFFFF — the per-package private
group SimPE writes and every custom donor uses; the game maps it to
`0x7F000000 | crc24(filename)` at load, so two packages never collide, and
OBJD, CTSS, TTAB and NREF stay together by construction. No resource embeds
its group id (5,478 resources across 200 groups scanned), so that touches
index entries only. Then every OBJD in the copy gets a fresh GUID through
s2clone.reidentify_object, which also rewrites the GUID literals in the
trees (tiles of a multi-tile object create each other by GUID). Everything
else stays byte-identical to the donor — trees are never converted.

    python3 s2workshop.py --selftest sample-packages
"""

# Annotations stay strings so the module imports under the system
# python3 (3.9), which the app gets when launched from Finder.
from __future__ import annotations

import argparse
import struct
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import s2clone
import s2object
import s2parser
import s2writer
from s2writer import Resource

PRIVATE_GROUP = 0xFFFFFFFF
GLOBAL_GROUP = 0x7FD46CD0
SEMIGLOBAL_BASE = 0x7F000000
SCENEGRAPH_TYPES = frozenset({
    0xE519C933, 0xFC6EB1F7, 0x7BA3838C, 0xAC4F8687,   # CRES SHPE GMND GMDC
    0x49596978, 0x1C4A276C, 0xED534136,               # TXMT TXTR LIFO
})
WORD_MASTER_ID, WORD_SUB_INDEX = 10, 11
TILE_MASTER = 0xFFFF


def semiglobal_group(name: str) -> int:
    return SEMIGLOBAL_BASE | s2parser.crc24(name.lower())


@dataclass
class Extraction:
    resources: "list[Resource]"
    donor_group: int
    guid: int                        # the object asked for (the master)
    carried: "list[tuple]" = field(default_factory=list)   # TGIs in other groups
    warnings: "list[str]" = field(default_factory=list)

    @property
    def objects(self) -> "list[s2clone.ObjectInfo]":
        return s2clone.find_objects(self.resources)


def extract_object(resources: "list[Resource]", guid: int, *,
                   whole_package: bool = False) -> Extraction:
    """Plain, inflated copies of the object's resources.

    Copies, not the donor's records: a LazyResource shares its packed bytes
    and renumbering in place would corrupt whatever list the donor came from.
    """
    donor = next((r for r in resources if r.type_id == s2object.TYPE_OBJD
                  and s2object.parse_objd(r.data).guid == guid), None)
    if donor is None:
        raise ValueError(f"no object 0x{guid:08X} in this package")
    group = donor.group_id
    picked = [r for r in resources
              if r.type_id != s2parser.TYPE_DIR and (whole_package or r.group_id == group)]
    copies = [Resource(r.type_id, r.group_id, r.instance_id, bytes(r.data), r.instance_hi)
              for r in picked]
    ext = Extraction(copies, group, guid,
                     carried=[r.tgi() for r in copies if r.group_id != group])
    sg = [r for r in copies if r.group_id == group and r.type_id in SCENEGRAPH_TYPES]
    if sg:
        ext.warnings.append(
            f"{len(sg)} scenegraph resource(s) in the object's group keep it: "
            "they are found by name hash, not by group")
    return ext


def new_guids(objects: "list[s2clone.ObjectInfo]", seed: str) -> "dict[int, int]":
    """A fresh GUID per OBJD, stable for a seed: the master (or lone object)
    from the seed itself, each tile from the seed and its sub index."""
    out: "dict[int, int]" = {}
    taken: "set[int]" = set()
    for o in objects:
        words = None
        try:
            words = s2object.parse_objd(o_data(objects, o)).words if False else None
        except Exception:
            words = None
        key = seed if o.guid == objects[0].guid and len(objects) == 1 else f"{seed}:{o.instance:X}"
        g = s2clone.derive_guid(key)
        while g in taken or g == o.guid:
            g = s2clone.derive_guid(key + "+")
            key += "+"
        taken.add(g)
        out[o.guid] = g
    return out


def o_data(objects, o):   # placeholder kept for symmetry; not used
    return b""


@dataclass
class Identity:
    """What the Name & Catalog page edits, read back from the resources."""
    name: str = ""
    description: str = ""
    price: int = 0
    room_flags: int = 0
    function_flags: int = 0
    guid: int = 0
    guids: "dict[int, int]" = field(default_factory=dict)   # original -> current, every OBJD

    def to_json(self) -> dict:
        return {"name": self.name, "description": self.description, "price": self.price,
                "room_flags": self.room_flags, "function_flags": self.function_flags,
                "guid": self.guid,
                "guids": {str(k): v for k, v in self.guids.items()}}

    @classmethod
    def from_json(cls, d: dict) -> "Identity":
        return cls(name=d.get("name", ""), description=d.get("description", ""),
                   price=int(d.get("price", 0)), room_flags=int(d.get("room_flags", 0)),
                   function_flags=int(d.get("function_flags", 0)), guid=int(d.get("guid", 0)),
                   guids={int(k): int(v) for k, v in (d.get("guids") or {}).items()})


def master_object(objects: "list[s2clone.ObjectInfo]", resources: "list[Resource]"
                  ) -> "s2clone.ObjectInfo":
    """The OBJD that represents the object: the multi-tile master, else the
    first buyable one, else the first."""
    for o in objects:
        words = s2object.parse_objd(resources[o.objd_index].data).words
        if words[WORD_MASTER_ID] and words[WORD_SUB_INDEX] == TILE_MASTER:
            return o
    for o in objects:
        words = s2object.parse_objd(resources[o.objd_index].data).words
        if words[9] == 4 and words[40]:
            return o
    return objects[0]


def read_identity(resources: "list[Resource]") -> Identity:
    objects = s2clone.find_objects(resources)
    if not objects:
        raise ValueError("no OBJD in the object")
    master = master_object(objects, resources)
    objd = s2object.parse_objd(resources[master.objd_index].data)
    name, description = objd.name, ""
    for r in resources:
        if r.type_id == s2object.TYPE_CTSS and r.instance_id == master.ctss_id:
            strs = [e.value for e in s2object.parse_str(r.data).entries if e.lang == 1]
            if strs and strs[0]:
                name = strs[0]
            if len(strs) > 1:
                description = strs[1]
            break
    return Identity(name=name, description=description, price=objd.price,
                    room_flags=objd.room_sort_flags, function_flags=objd.function_sort_flags,
                    guid=master.guid,
                    guids={o.original_guid or o.guid: o.guid for o in objects})


def reidentify(ext: Extraction, *, guids: "dict[int, int]", name: "str | None" = None,
               description: "str | None" = None, price: "int | None" = None,
               room_flags: "int | None" = None, function_flags: "int | None" = None,
               aggressive: bool = False) -> "list[str]":
    """Renumber the group and re-identify every OBJD, in place. Returns
    warnings. `guids` maps each OBJD's current GUID to its new one; name,
    description, price and flags apply to the master only."""
    resources = ext.resources
    for r in resources:
        if r.group_id == ext.donor_group and r.type_id not in SCENEGRAPH_TYPES:
            r.group_id = PRIVATE_GROUP
    objects = s2clone.find_objects(resources)
    master = master_object(objects, resources)
    warnings: "list[str]" = list(ext.warnings)
    for o in objects:
        new = guids.get(o.guid)
        if new is None or new == o.guid:
            continue
        is_master = o.guid == master.guid
        report = s2clone.reidentify_object(
            resources, o, guid=new,
            name=name if is_master else None,
            description=description if is_master else None,
            price=price if is_master else None,
            room_flags=room_flags if is_master else None,
            function_flags=function_flags if is_master else None,
            aggressive=aggressive)
        warnings.extend(report.warnings)
    return warnings


def apply_identity(resources: "list[Resource]", identity: Identity) -> int:
    """Write name, description, price and flags onto the master OBJD and its
    CTSS without touching GUIDs. Returns how many resources changed."""
    objects = s2clone.find_objects(resources)
    master = master_object(objects, resources)
    changed = 0
    objd_res = resources[master.objd_index]
    objd = s2object.parse_objd(objd_res.data)
    objd.price = identity.price
    objd.room_sort_flags = identity.room_flags
    objd.function_sort_flags = identity.function_flags
    objd.filename = identity.name
    objd.name = identity.name
    new = s2object.build_objd(objd)
    if new != objd_res.data:
        objd_res.data = new
        changed += 1
    for r in resources:
        if r.type_id == s2object.TYPE_CTSS and r.instance_id == master.ctss_id:
            table = s2object.parse_str(r.data)
            english = [i for i, e in enumerate(table.entries) if e.lang == 1]
            if english:
                table.entries[english[0]].value = identity.name
            if len(english) > 1:
                table.entries[english[1]].value = identity.description
            elif english:
                table.entries.append(s2object.StrEntry(1, identity.description))
            new = s2object.build_str(table)
            if new != r.data:
                r.data = new
                changed += 1
        elif r.type_id == s2object.TYPE_NREF and r.instance_id == master.instance:
            new = identity.name.encode("latin-1", "replace")
            if new != r.data:
                r.data = new
                changed += 1
    return changed


# ---------------------------------------------------------------------------
# Closure check: does the copy reach everything its trees call?
# ---------------------------------------------------------------------------

def tree_references(resources: "list[Resource]") -> "set[int]":
    """Every tree id the object's BHAVs, OBJf and TTAB point at."""
    ids: "set[int]" = set()
    for r in resources:
        try:
            if r.type_id == s2object.TYPE_BHAV:
                for ins in s2parser.parse_bhav(r.data).instructions:
                    if ins.opcode >= 0x100:
                        ids.add(ins.opcode)
            elif r.type_id == s2object.TYPE_OBJF:
                for e in s2object.parse_objf(r.data).entries:
                    ids.update(x for x in (e.guard, e.action) if x)
            elif r.type_id == s2object.TYPE_TTAB:
                for e in s2object.parse_ttab(r.data).entries:
                    ids.update(x for x in (e.action, e.guard) if x)
        except (ValueError, struct.error, IndexError):
            continue
    return ids


def unresolved_trees(resources: "list[Resource]", game: "set | None" = None) -> "list[str]":
    """Tree ids the object's trees, OBJf and TTAB point at that resolve
    nowhere the game would look: the copy itself, then the GLOB's semi-global
    group, then the global group (`game` = {(type, group, instance)} of
    objects.package). The game's own objects carry a few of these — a tile's
    OBJf naming trees that never existed — and skip them at run time, so this
    is information about the donor, not a fault in the copy."""
    have = {r.instance_id for r in resources if r.type_id == s2object.TYPE_BHAV}
    globs = []
    for r in resources:
        if r.type_id == s2object.TYPE_GLOB:
            try:
                globs.append(s2object.parse_glob(r.data).semi_global)
            except (ValueError, struct.error):
                pass
    out = []
    for tid in sorted(tree_references(resources)):
        if tid < 0x100 or tid in have:
            continue
        if game is None:
            if tid < 0x2000 and 0x1000 <= tid:
                out.append(f"private tree 0x{tid:04X} is not in the copy")
            continue
        if any((s2object.TYPE_BHAV, semiglobal_group(g), tid) in game for g in globs):
            continue
        if (s2object.TYPE_BHAV, GLOBAL_GROUP, tid) in game:
            continue
        kind = "private" if 0x1000 <= tid < 0x2000 else "semi-global" if tid >= 0x2000 else "global"
        out.append(f"{kind} tree 0x{tid:04X} resolves nowhere (dangling in the donor too)")
    return out


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _check_clone(donor: "list[Resource]", guid: int, whole: bool, game_keys, label: str) -> "list[str]":
    ext = extract_object(donor, guid, whole_package=whole)
    objects = ext.objects
    before = {r.tgi(): bytes(r.data) for r in ext.resources}
    guids = new_guids(objects, "selftest:" + label)
    warnings = reidentify(ext, guids=guids, name="Selftest Clone", description="made by the selftest",
                          price=123, room_flags=0x2, function_flags=0x20)
    with tempfile.TemporaryDirectory(prefix="s2workshop-") as tmp:
        out = Path(tmp) / "clone.package"
        s2writer.write_package(out, ext.resources, compress=True)
        back = [r for r in s2writer.read_all_resources(out) if r.type_id != s2parser.TYPE_DIR]
    problems = []
    def parses(r):
        try:
            s2object.PARSERS[r.type_id][0](r.data)
            return True
        except (ValueError, struct.error, IndexError):
            return False
    parsed_before = {r.tgi() for r in ext.resources if r.type_id in s2object.PARSERS and parses(r)}
    if len(back) != len(ext.resources):
        problems.append(f"{len(back)} resources back, {len(ext.resources)} written")
    donor_guids = set(guids)
    for r in back:
        if r.group_id == ext.donor_group and ext.donor_group != PRIVATE_GROUP and r.type_id not in SCENEGRAPH_TYPES:
            problems.append(f"{r.type_name} 0x{r.instance_id:X} kept the donor group")
        if r.tgi() in parsed_before and not parses(r):
            problems.append(f"{r.type_name} 0x{r.instance_id:X} no longer parses")
    objs = s2clone.find_objects(back)
    for o in objs:
        if o.guid in donor_guids:
            problems.append(f"OBJD 0x{o.instance:X} still carries donor GUID 0x{o.guid:08X}")
        if o.original_guid not in donor_guids:
            problems.append(f"OBJD 0x{o.instance:X} original_guid 0x{o.original_guid:08X} is not a donor GUID")
    ident = read_identity(back)
    if (ident.name, ident.description, ident.price, ident.room_flags, ident.function_flags) != (
            "Selftest Clone", "made by the selftest", 123, 0x2, 0x20):
        problems.append(f"identity read back as {ident}")
    # Byte-identical outside the identity edits: only OBJD, CTSS, NREF and
    # the BHAVs reported patched may differ.
    for r in back:
        old = before.get((r.type_id, ext.donor_group if r.group_id == PRIVATE_GROUP and ext.donor_group != PRIVATE_GROUP else r.group_id, r.instance_id, r.instance_hi))
        if old is None:
            continue
        if r.type_id in (s2object.TYPE_OBJD, s2object.TYPE_CTSS, s2object.TYPE_NREF, s2object.TYPE_BHAV):
            continue
        if old != r.data:
            problems.append(f"{r.type_name} 0x{r.instance_id:X} changed bytes")
    # Every tree the donor group holds must be in the copy; what the donor
    # itself leaves dangling is reported but is not a failure.
    donor_trees = {r.instance_id for r in donor if r.type_id == s2object.TYPE_BHAV
                   and r.group_id == ext.donor_group}
    copy_trees = {r.instance_id for r in back if r.type_id == s2object.TYPE_BHAV}
    for tid in sorted(donor_trees - copy_trees):
        problems.append(f"tree 0x{tid:04X} in the donor group is missing from the copy")
    notes = unresolved_trees(back, game_keys)
    if notes and _VERBOSE:
        print(f"note {label}: " + "; ".join(notes[:4]))
    return problems


_VERBOSE = False


def _selftest(sample_dir: str) -> int:
    import s2catalog
    donors = []
    for p in sorted(Path(sample_dir).glob("*.package")):
        try:
            res = s2writer.read_all_resources(p)
        except (OSError, ValueError, struct.error):
            continue
        for o in s2clone.find_objects(res):
            donors.append((p.name, res, o.guid, True))
            break
    game_keys = None
    if s2catalog.OBJECTS_PACKAGE.is_file():
        reader = s2catalog.PackageReader(s2catalog.OBJECTS_PACKAGE)
        game_keys = set(reader.by_key)
        import s2studio
        game_res = s2studio.load(s2catalog.OBJECTS_PACKAGE).resources
        entries = [c for c in s2catalog.scan_package(s2catalog.OBJECTS_PACKAGE, "game")]
        picked = entries[::20] + [c for c in entries if c.tiles > 1][::10]
        seen = set()
        for c in picked:
            if c.guid in seen:
                continue
            seen.add(c.guid)
            donors.append((f"game:{c.name}", game_res, c.guid, False))
    ok = bad = 0
    for label, res, guid, whole in donors:
        try:
            problems = _check_clone(res, guid, whole, game_keys, label)
        except Exception as exc:   # noqa: BLE001 — a selftest reports, not raises
            problems = [f"raised {type(exc).__name__}: {exc}"]
        if problems:
            bad += 1
            print(f"FAIL {label}")
            for p in problems[:8]:
                print(f"     {p}")
        else:
            ok += 1
    print(f"workshop selftest: {ok} object(s) cloned and verified, {bad} failed")
    return 1 if bad or not ok else 0


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", metavar="SAMPLE_DIR")
    ap.add_argument("--verbose", action="store_true", help="also print what the donor leaves dangling")
    args = ap.parse_args(argv)
    global _VERBOSE
    _VERBOSE = args.verbose
    if args.selftest:
        return _selftest(args.selftest)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
