#!/usr/bin/env python3
"""s2clone.py — clone a Sims 2 object into a new, independent object.

The SimPE "Object Workshop" step: take a donor package, give the object a new
identity, and rewrite every reference that pointed at the old one, so the
clone coexists with its donor instead of fighting it.

What actually has to change, and what does not
----------------------------------------------
Object resources live in group 0xFFFFFFFF, which is a *per-package private
namespace* — verified across the donors in sample-packages/, where every
BHAV, TTAB, OBJD and OBJf sits in that group. Local tree ids (0x1000+),
TTAB/TTAs ids and the CTSS id all resolve inside that namespace, so a clone
in its own package can keep them verbatim. There is no instance renumbering
to do, which is the part of cloning people expect to be hard and isn't.

What must change is the GUID, because that one *is* global. Two objects
sharing a GUID is the catastrophic case.

The reference graph (every edge confirmed against both donors)
-------------------------------------------------------------
  OBJD.guid          (words 14/15) the global identity
  OBJD.job_guid      (words 52/53) donors mirror their own GUID here
  OBJD.original_guid (words 70/71) records the clone source
  OBJD.ttab_id       (word 7)  -> TTAB instance, and the TTAs of the same id
  OBJD.ctss_id       (word 41) -> CTSS instance (2000 in both donors)
  OBJD instance      == OBJf instance == NREF instance (0x41A7 in both)
  OBJf slots         -> BHAV instances (slot 0 init, slot 1 main)
  TTAB entries       -> action/guard BHAV instances, and a TTAs string index
  BHAV operands      -> the object's own GUID, embedded as a literal
  MMAT objectGUID    -> the object a recolour dresses (XML, so exact)

That BHAV edge is the one a naive clone misses: rewriting only the OBJD
leaves those trees driving the *original* object. Both donors do it —
the Diploma in "Interaction - Take Diploma", the Blender in two trees.

Known limits
------------
No donor here is multi-tile (one OBJD each), so the per-tile OBJD chain is
untested. No donor here is an object recolour either — the MMAT objectGUID
rewrite is exact because the resource is XML, but it is unverified against
real data, and the clone report says so when it fires.

GUID literals in BHAV operands
------------------------------
Patched by operand offset, per primitive, for the layouts confirmed here:

  0x001F Set to Next        operand bytes 1-4
  0x0020 Test Object Type   operand bytes 1-4
  0x0033 Manage Inventory   operand bytes 6-9  (matches inv_ops in s2object)

Other primitives take GUIDs too (Create New Object Instance, Find Best
Object for Function...) but their operand layouts are NOT confirmed here, so
they are not in the table. Any GUID-looking hit outside a known layout is
*reported, not patched* — see `--aggressive`. Blind byte-replacement is
genuinely unsafe: a low GUID like the Blender's 0x00006F3B is two plausible
u16 literals (0x6F3B, 0x0000), so an Expression could match by coincidence.
Silently corrupting a tree is worse than making you look at a warning.
"""

# Annotations stay strings so the module imports under the system
# python3 (3.9), the same constraint the rest of the toolkit works under.
from __future__ import annotations

import argparse
import hashlib
import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

import s2object
import s2parser
import s2writer

TYPE_MMAT = 0xCCA8E925

# Instruction layout, shared with s2parser.parse_bhav.
_INSTR_SIZE = 23
_OPERAND_OFFSET = 6
_OPERAND_LEN = 16
_INSTR_START = {0x8007: 76, 0x8009: 77}

# opcode -> operand byte offset at which that primitive stores an object GUID.
# Only layouts confirmed against a donor appear here; see the module docstring.
GUID_OPERANDS = {
    0x001F: 1,   # Set to Next
    0x0020: 1,   # Test Object Type
    0x0033: 6,   # Manage Inventory
}


# ---------------------------------------------------------------------------
# GUIDs
# ---------------------------------------------------------------------------

def derive_guid(seed: str) -> int:
    """Derive a stable 32-bit GUID from a seed string.

    Deterministic on purpose: a GUID that changed on every rebuild would
    orphan the object in any save that already placed it. Same seed, same
    GUID, forever.

    This is not a registry allocation. Maxis GUIDs are spread across the
    whole 32-bit space (both donors here clone from high ones), so there is
    no "safe range" to hide in — use --check to scan what you actually have
    installed, and register a range properly before distributing anything.
    """
    digest = hashlib.sha256(seed.encode('utf-8')).digest()
    guid = struct.unpack('<I', digest[:4])[0]
    # 0 and 0xFFFFFFFF are sentinels elsewhere in the format.
    if guid in (0x00000000, 0xFFFFFFFF):
        guid = 0x10000001
    return guid


def scan_guids(paths: list[Path]) -> dict[int, list[str]]:
    """Map every object GUID found in the given packages to where it came
    from. Feed it a Downloads folder to check a candidate GUID for collisions."""
    found: dict[int, list[str]] = {}
    for path in paths:
        try:
            resources = s2writer.read_all_resources(path)
        except Exception:
            continue  # unreadable or not a package; scanning is best-effort
        for r in resources:
            if r.type_id != s2object.TYPE_OBJD:
                continue
            try:
                guid = s2object.parse_objd(r.data).guid
            except ValueError:
                continue
            found.setdefault(guid, []).append(path.name)
    return found


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------

@dataclass
class ObjectInfo:
    """One object's identity and the resources it points at."""
    objd_index: int          # position in the resource list
    instance: int            # shared by OBJD, OBJf and NREF
    guid: int
    original_guid: int
    filename: str
    name: str
    price: int
    ttab_id: int
    ctss_id: int

    def __str__(self) -> str:
        return (f'0x{self.guid:08X}  "{self.name}"  '
                f'§{self.price}  instance=0x{self.instance:X}  '
                f'ttab={self.ttab_id} ctss={self.ctss_id}')


def find_objects(resources: list[s2writer.Resource]) -> list[ObjectInfo]:
    """Every object definition in a resource list, in package order."""
    out = []
    for i, r in enumerate(resources):
        if r.type_id != s2object.TYPE_OBJD:
            continue
        o = s2object.parse_objd(r.data)
        out.append(ObjectInfo(i, r.instance_id, o.guid, o.original_guid,
                              o.filename, o.name, o.price, o.ttab_id, o.ctss_id))
    return out


# ---------------------------------------------------------------------------
# GUID reference rewriting
# ---------------------------------------------------------------------------

@dataclass
class GuidPatch:
    """One GUID literal found in a BHAV operand block."""
    instance: int
    bhav_name: str
    instr_index: int
    opcode: int
    operand_offset: int
    known_layout: bool       # True when the primitive's layout is confirmed
    applied: bool = False

    def __str__(self) -> str:
        mark = 'patched' if self.applied else 'REPORTED ONLY'
        how = '' if self.known_layout else '  <- unconfirmed operand layout'
        return (f'{mark}: BHAV 0x{self.instance:04X} "{self.bhav_name}" '
                f'instr[{self.instr_index}] opcode 0x{self.opcode:04X} '
                f'{s2parser.PRIMITIVES.get(self.opcode, "?")} '
                f'operand +{self.operand_offset}{how}')


def _iter_instructions(data: bytes):
    """Yield (index, opcode, absolute operand offset) for a raw BHAV."""
    if len(data) < 72:
        return
    version, count = struct.unpack_from('<HH', data, 64)
    start = _INSTR_START.get(version)
    if start is None:
        return
    for i in range(count):
        pos = start + i * _INSTR_SIZE
        if pos + _INSTR_SIZE > len(data):
            return
        opcode, = struct.unpack_from('<H', data, pos)
        yield i, opcode, pos + _OPERAND_OFFSET


def patch_guid_references(resources: list[s2writer.Resource], old_guid: int,
                          new_guid: int, *, aggressive: bool = False
                          ) -> list[GuidPatch]:
    """Rewrite the object's own GUID where it appears inside BHAV operands.

    Only operand bytes are examined, never names, headers or branch targets.
    Hits at a confirmed operand offset are patched; anything else is reported
    unless `aggressive` is set. Mutates `resources` in place.
    """
    needle = struct.pack('<I', old_guid)
    replacement = struct.pack('<I', new_guid)
    patches: list[GuidPatch] = []

    for r in resources:
        if r.type_id != s2object.TYPE_BHAV:
            continue
        data = bytearray(r.data)
        name = bytes(data[:64]).split(b'\x00', 1)[0].decode('latin-1', 'replace')
        touched = False

        for index, opcode, ops_at in _iter_instructions(bytes(data)):
            operands = bytes(data[ops_at:ops_at + _OPERAND_LEN])
            known_at = GUID_OPERANDS.get(opcode)
            search = 0
            while True:
                hit = operands.find(needle, search)
                if hit < 0:
                    break
                search = hit + 1
                known = (known_at is not None and hit == known_at)
                patch = GuidPatch(r.instance_id, name, index, opcode, hit, known)
                if known or aggressive:
                    data[ops_at + hit:ops_at + hit + 4] = replacement
                    patch.applied = True
                    touched = True
                patches.append(patch)

        if touched:
            r.data = bytes(data)

    return patches


# ---------------------------------------------------------------------------
# MMAT (recolour / material override)
# ---------------------------------------------------------------------------

# MMAT is a cGZPropertySetString XML document, so the object reference is a
# named element rather than a byte offset and can be rewritten exactly.
_MMAT_OBJECT_GUID = re.compile(
    br'(<AnyUint32\s+key="objectGUID"[^>]*>)(\d+)(</AnyUint32>)', re.IGNORECASE)


def patch_mmat_references(resources: list[s2writer.Resource], new_guid: int
                          ) -> tuple[int, int]:
    """Point every object recolour at the clone instead of the donor.

    Returns (MMATs seen, objectGUID fields rewritten). Object recolours carry
    an objectGUID key; floor and wall recolours carry a plain `guid` (their
    own identity) and are correctly left alone — which is why "seen but none
    rewritten" is reported rather than treated as success.
    """
    seen = patched = 0
    for r in resources:
        if r.type_id != TYPE_MMAT:
            continue
        seen += 1
        data, count = _MMAT_OBJECT_GUID.subn(
            lambda m: m.group(1) + str(new_guid).encode('ascii') + m.group(3),
            r.data)
        if count:
            r.data = data
            patched += count
    return seen, patched


# ---------------------------------------------------------------------------
# Cloning
# ---------------------------------------------------------------------------

@dataclass
class CloneReport:
    source_guid: int
    new_guid: int
    resource_count: int
    patches: list[GuidPatch] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def unpatched(self) -> list[GuidPatch]:
        return [p for p in self.patches if not p.applied]

    def __str__(self) -> str:
        lines = [f'cloned 0x{self.source_guid:08X} -> 0x{self.new_guid:08X}  '
                 f'({self.resource_count} resources)']
        for p in self.patches:
            lines.append(f'  {p}')
        for w in self.warnings:
            lines.append(f'  warning: {w}')
        return '\n'.join(lines)


def clone(resources: list[s2writer.Resource], *, guid: int,
          name: str | None = None, description: str | None = None,
          price: int | None = None, instance: int | None = None,
          select_guid: int | None = None, aggressive: bool = False
          ) -> CloneReport:
    """Turn a donor's resources into a new object, in place.

    `resources` should be the whole donor package (minus DIR, which
    write_package drops anyway): objects share their BHAV trees, so copying
    everything is both simplest and correct.
    """
    objects = find_objects(resources)
    if not objects:
        raise ValueError('no OBJD in this package — nothing to clone')

    if select_guid is not None:
        matches = [o for o in objects if o.guid == select_guid]
        if not matches:
            raise ValueError(f'no object with GUID 0x{select_guid:08X} here')
        target = matches[0]
    elif len(objects) == 1:
        target = objects[0]
    else:
        raise ValueError(
            f'{len(objects)} objects in this package; pick one with '
            '--of-guid: ' + ', '.join(f'0x{o.guid:08X}' for o in objects))

    report = CloneReport(target.guid, guid, len(resources))
    if guid == target.guid:
        raise ValueError('new GUID is identical to the donor\'s — that is the '
                         'collision cloning exists to avoid')

    # --- OBJD: the identity itself ---
    objd_res = resources[target.objd_index]
    objd = s2object.parse_objd(objd_res.data)
    mirrored_job_guid = (objd.job_guid == target.guid)
    objd.guid = guid
    objd.original_guid = target.guid
    if mirrored_job_guid:
        objd.job_guid = guid
    if price is not None:
        objd.price = price
    if name is not None:
        objd.filename = name
        objd.name = name
    objd_res.data = s2object.build_objd(objd)

    # --- catalog text ---
    if name is not None or description is not None:
        report.warnings.extend(
            _retitle_ctss(resources, target.ctss_id, name, description))

    # --- NREF: the object's name token, keyed to the object instance ---
    if name is not None:
        for r in resources:
            if r.type_id == s2object.TYPE_NREF and r.instance_id == target.instance:
                r.data = name.encode('latin-1', 'replace')

    # --- GUID literals inside behaviour trees ---
    report.patches = patch_guid_references(resources, target.guid, guid,
                                           aggressive=aggressive)
    for p in report.patches:
        if not p.applied:
            report.warnings.append(
                f'BHAV 0x{p.instance:04X} "{p.bhav_name}" instr[{p.instr_index}] '
                f'holds the old GUID at an unconfirmed operand offset '
                f'(+{p.operand_offset}, opcode 0x{p.opcode:04X}) and was left '
                f'alone; check it by hand or re-run with --aggressive')

    # --- recolours, which name the object they dress ---
    seen, repointed = patch_mmat_references(resources, guid)
    if repointed:
        report.warnings.append(
            f'repointed {repointed} objectGUID field(s) across {seen} MMAT(s) '
            f'— this path has no donor in sample-packages/ to verify against, '
            f'so check the clone\'s recolours in the catalog')
    elif seen:
        report.warnings.append(
            f'{seen} MMAT(s) present with no objectGUID field; correct for '
            f'floor/wall recolours, but if these are object recolours they '
            f'still point at the donor and need a look')

    # --- object instance id, shared by OBJD/OBJf/NREF ---
    if instance is not None and instance != target.instance:
        moved = 0
        for r in resources:
            if (r.type_id in (s2object.TYPE_OBJD, s2object.TYPE_OBJF,
                              s2object.TYPE_NREF)
                    and r.instance_id == target.instance):
                r.instance_id = instance
                moved += 1
        report.warnings.append(
            f'object instance 0x{target.instance:X} -> 0x{instance:X} '
            f'({moved} resources); only needed when several objects share a package')

    return report


def _retitle_ctss(resources: list[s2writer.Resource], ctss_id: int,
                  name: str | None, description: str | None) -> list[str]:
    """Set the catalog title and description. Entry 0 is the title and entry 1
    the description, per language; only the English (lang 1) pair is touched."""
    warnings: list[str] = []
    for r in resources:
        if r.type_id != s2object.TYPE_CTSS or r.instance_id != ctss_id:
            continue
        table = s2object.parse_str(r.data)
        english = [i for i, e in enumerate(table.entries) if e.lang == 1]
        if name is not None and len(english) > 0:
            table.entries[english[0]].value = name
        if description is not None and len(english) > 1:
            table.entries[english[1]].value = description
        other = {e.lang for e in table.entries if e.lang != 1}
        if other and name is not None:
            warnings.append(
                f'CTSS {ctss_id} also carries language(s) '
                + ', '.join(str(l) for l in sorted(other))
                + ' still showing the donor\'s name')
        r.data = s2object.build_str(table)
        return warnings
    warnings.append(f'no CTSS with instance {ctss_id} — catalog text unchanged')
    return warnings


def clone_package(src: Path | str, dest: Path | str, *, guid: int | None = None,
                  name: str | None = None, description: str | None = None,
                  price: int | None = None, instance: int | None = None,
                  select_guid: int | None = None, aggressive: bool = False
                  ) -> CloneReport:
    """Clone a donor package file into a new object package."""
    resources = s2writer.read_all_resources(src)
    if guid is None:
        guid = derive_guid(name or str(Path(dest).stem))
    report = clone(resources, guid=guid, name=name, description=description,
                   price=price, instance=instance, select_guid=select_guid,
                   aggressive=aggressive)
    s2writer.write_package(dest, resources)
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_inspect(path: Path) -> int:
    resources = s2writer.read_all_resources(path)
    objects = find_objects(resources)
    print(f'{path.name}  —  {len(resources)} resources, {len(objects)} object(s)')
    for o in objects:
        print(f'  {o}')
        print(f'    filename   {o.filename!r}')
        print(f'    clone of   0x{o.original_guid:08X}')
        patches = patch_guid_references(list(resources), o.guid, o.guid)
        if patches:
            print(f'    own GUID embedded in {len(patches)} BHAV operand(s):')
            for p in patches:
                flag = '' if p.known_layout else '   (layout unconfirmed)'
                print(f'      BHAV 0x{p.instance:04X} "{p.bhav_name}" '
                      f'instr[{p.instr_index}] '
                      f'{s2parser.PRIMITIVES.get(p.opcode, hex(p.opcode))}{flag}')
    return 0


def _selftest(sample_dir: str = 'sample-packages') -> int:
    """Clone every object donor and assert the clone is a correct one.

    The bar is not "it wrote a file". A clone is correct when the new GUID
    has replaced the old one at every functional reference, the donor GUID
    survives *only* as the original-GUID record, and every resource that
    should not have changed is still byte-identical.
    """
    import tempfile

    failures: list[str] = []
    donors = []
    for path in sorted(Path(sample_dir).glob('*.package')):
        try:
            resources = s2writer.read_all_resources(path)
        except Exception:
            continue
        if find_objects(resources):
            donors.append(path)

    if not donors:
        print(f'no object donors found in {sample_dir}/')
        return 1

    for path in donors:
        original = [r for r in s2writer.read_all_resources(path)
                    if r.type_id != s2writer.TYPE_DIR]
        info = find_objects(original)[0]
        new_guid = derive_guid(f'selftest:{path.name}')

        with tempfile.NamedTemporaryFile(suffix='.package', delete=False) as tmp:
            out = tmp.name
        report = clone_package(path, out, guid=new_guid, name='Selftest Clone',
                               price=42)
        clone_res = s2writer.read_all_resources(out)

        def fail(msg: str) -> None:
            failures.append(f'{path.name}: {msg}')

        # the index must survive untouched — cloning changes contents, not TGIs
        before = {r.tgi() for r in original}
        after = {r.tgi() for r in clone_res}
        if before != after:
            fail(f'TGI set changed ({len(before)} -> {len(after)})')

        # the donor GUID may survive only as OBJD.original_guid
        needle = struct.pack('<I', info.guid)
        for r in clone_res:
            hits = r.data.count(needle)
            if not hits:
                continue
            if r.type_id == s2object.TYPE_OBJD and hits == 1:
                objd = s2object.parse_objd(r.data)
                if objd.original_guid == info.guid:
                    continue
            fail(f'donor GUID survives in {r.type_name} 0x{r.instance_id:X}')

        # the new identity must actually be in place
        objd = s2object.parse_objd(
            next(r for r in clone_res if r.type_id == s2object.TYPE_OBJD).data)
        if objd.guid != new_guid:
            fail(f'OBJD guid is 0x{objd.guid:08X}, expected 0x{new_guid:08X}')
        if objd.original_guid != info.guid:
            fail('OBJD original_guid does not record the donor')
        if objd.price != 42:
            fail(f'price is {objd.price}, expected 42')

        # nothing beyond the object's own identity may have moved
        allowed = {s2object.TYPE_OBJD, s2object.TYPE_CTSS, s2object.TYPE_NREF}
        patched_bhavs = {p.instance for p in report.patches if p.applied}
        old_by_tgi = {r.tgi(): r for r in original}
        for r in clone_res:
            if r.data == old_by_tgi[r.tgi()].data:
                continue
            if r.type_id in allowed:
                continue
            if r.type_id == s2object.TYPE_BHAV and r.instance_id in patched_bhavs:
                continue
            fail(f'unexpected change in {r.type_name} 0x{r.instance_id:X}')

        # a clone must still parse as the thing it claims to be
        for r in clone_res:
            if r.type_id in s2object.PARSERS:
                try:
                    s2object.parse_resource(r.type_id, r.data)
                except ValueError as exc:
                    fail(f'clone broke {r.type_name} 0x{r.instance_id:X}: {exc}')

        Path(out).unlink(missing_ok=True)

    # GUID derivation must be stable, or placed objects orphan on rebuild
    if derive_guid('a thing') != derive_guid('a thing'):
        failures.append('derive_guid is not deterministic')
    if derive_guid('a thing') == derive_guid('another thing'):
        failures.append('derive_guid collides on different seeds')

    print(f'clone selftest: {len(donors)} donor(s) cloned and verified')
    for msg in failures:
        print(f'  FAIL {msg}')
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description='Clone a Sims 2 object into a new, independent object.')
    ap.add_argument('source', type=Path, help='donor .package')
    ap.add_argument('dest', type=Path, nargs='?', help='output .package')
    ap.add_argument('--inspect', action='store_true',
                    help='describe the donor and exit, writing nothing')
    ap.add_argument('--name', help='new catalog and file name')
    ap.add_argument('--description', help='new catalog description')
    ap.add_argument('--price', type=int, help='new catalog price')
    ap.add_argument('--guid', type=lambda s: int(s, 0),
                    help='explicit GUID (default: derived from --name, stable '
                         'across rebuilds)')
    ap.add_argument('--of-guid', type=lambda s: int(s, 0), dest='select_guid',
                    help='which object to clone, if the donor holds several')
    ap.add_argument('--instance', type=lambda s: int(s, 0),
                    help='renumber the object instance (OBJD/OBJf/NREF); only '
                         'needed when packing several objects together')
    ap.add_argument('--aggressive', action='store_true',
                    help='also rewrite GUID hits at unconfirmed operand '
                         'offsets — can corrupt a tree, read the warnings first')
    ap.add_argument('--check', type=Path, metavar='DIR',
                    help='scan DIR for packages and refuse a GUID already in use')
    ap.add_argument('--selftest', action='store_true',
                    help='clone every donor in sample-packages/ and verify it')
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest(str(args.source))

    if args.inspect:
        return _cmd_inspect(args.source)
    if args.dest is None:
        ap.error('an output package is required unless --inspect is given')

    guid = args.guid
    if guid is None:
        guid = derive_guid(args.name or args.dest.stem)

    if args.check:
        packages = sorted(args.check.rglob('*.package'))
        print(f'checking 0x{guid:08X} against {len(packages)} package(s) '
              f'in {args.check}...', flush=True)
        used = scan_guids(packages)
        if guid in used:
            print(f'  GUID 0x{guid:08X} is already used by: '
                  + ', '.join(used[guid]), file=sys.stderr)
            print('  pass a different --guid or --name', file=sys.stderr)
            return 1
        print(f'  clear ({len(used)} distinct GUIDs seen)')

    try:
        report = clone_package(args.source, args.dest, guid=guid,
                               name=args.name, description=args.description,
                               price=args.price, instance=args.instance,
                               select_guid=args.select_guid,
                               aggressive=args.aggressive)
    except ValueError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1

    print(report)
    print(f'wrote {args.dest}')
    return 1 if report.unpatched and not args.aggressive else 0


if __name__ == '__main__':
    sys.exit(main())
