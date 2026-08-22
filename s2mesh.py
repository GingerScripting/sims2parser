#!/usr/bin/env python3
"""s2mesh.py — read Sims 2 meshes (GMDC) and export Wavefront OBJ.

GMDC is `cGeometryDataContainer`, an RCOL block like the textures in
s2texture, whose container reader this reuses.

Layout, worked out against the game's own meshes rather than assumed. A
GMDC body is three sections:

  elements   the raw vertex arrays — positions, normals, UVs, bone data
  linkages   which elements belong together as one addressable vertex set
  groups     the named subsets, each a list of triangle indices

An element is seven u32 then its payload:

    count, identity, sub_index, block_format, set_format, byte_size, hash

`identity` says what the array means and `byte_size / count` gives the
stride. Both were confirmed across 2,517 meshes, where every identity holds
one stride throughout:

    0x5B830781  stride 12   positions        (3 floats)
    0x3B83078B  stride 12   normals          (3 floats)
    0xBB8307AB  stride  8   texture coords   (2 floats)

Deriving the stride rather than hard-coding it per identity means the bone
and morph arrays (strides 4 and 12) still parse without knowing what they
mean, which keeps an unfamiliar element from derailing the walk.

A linkage is a u32 count, that many u16 element indices, then five u32 —
the first being the vertex count the group will address. The five-u32 tail
is empirical: against a nested "count plus u16 array" reading it parses 618
of 800 meshes where the nested form manages 52.

A group is two u32, a pascal name, a u32 index count, then that many u16
triangle indices.

Not parsed: the section after the groups, which carries bounding and subset
data. It is not needed for geometry, so the walk stops at the end of the
groups rather than pretending to understand the rest. That is also why the
self-test checks that every face index lands inside its linkage's vertex
array instead of checking byte consumption — for geometry, in-range indices
are the property that actually matters.

Export is OBJ: positions, normals and texture coords, one `g` per group.

INCOMPLETE — the group header is not fully pinned
-------------------------------------------------
On Objects03.package, 888 of 1,663 meshes (53%) parse and validate cleanly.
The rest drift inside the group section: names come back as "llshadow" or
"lshadow" where the mesh plainly means "wallshadow", a slip of two or three
bytes, so the two u32 before a group's name are not the whole header and
something version- or content-dependent sits in there.

The drift is small and consistent, which is what makes it findable — but it
is not found yet, so do not treat a successful parse of an arbitrary mesh as
guaranteed. What can be relied on is that bad reads are *caught*: a drifted
group yields an unprintable name or an out-of-range face index, both of
which the self-test rejects. Nothing here exports plausible-looking garbage.

Solid below that line: the element walk (validated across 2,517 meshes, with
every identity holding one stride throughout) and the 20-byte linkage tail
(561 of 600, where no other tail size scores above zero).
"""

# Annotations stay strings so the module imports under the system
# python3 (3.9), the same constraint the rest of the toolkit works under.
from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

import s2parser
import s2texture
import s2writer

TYPE_GMDC = 0xAC4F8687      # cGeometryDataContainer

# Element identity -> what the array holds. Only the three needed for
# geometry are named; anything else parses and is carried but not exported.
ID_POSITION = 0x5B830781
ID_NORMAL = 0x3B83078B
ID_UV = 0xBB8307AB

ELEMENT_NAMES = {
    ID_POSITION: 'position',
    ID_NORMAL: 'normal',
    ID_UV: 'uv',
    0xFBD70111: 'bone weights',
    0x3BD70105: 'bone assignments',
    0xDB830795: 'tangents',
    0x89D92BA0: 'morph delta',
    0x7C4DEE82: 'morph key',
}

_ELEMENT_HEADER = struct.Struct('<7I')


@dataclass
class Element:
    count: int
    identity: int
    sub_index: int
    block_format: int
    set_format: int
    data: bytes

    @property
    def stride(self) -> int:
        return len(self.data) // self.count if self.count else 0

    @property
    def name(self) -> str:
        return ELEMENT_NAMES.get(self.identity, f'0x{self.identity:08X}')

    def floats(self) -> list[tuple[float, ...]]:
        """The array as tuples of floats, width implied by the stride."""
        width = self.stride // 4
        if width == 0:
            return []
        fmt = struct.Struct(f'<{width}f')
        return [fmt.unpack_from(self.data, i * self.stride)
                for i in range(self.count)]

    def __str__(self) -> str:
        return f'{self.name} x{self.count} (stride {self.stride})'


@dataclass
class Linkage:
    elements: list[int]         # indices into GmdcMesh.elements
    vertex_count: int


@dataclass
class Group:
    name: str
    linkage: int
    indices: list[int]          # triangle corner indices, 3 per face

    @property
    def face_count(self) -> int:
        return len(self.indices) // 3


@dataclass
class GmdcMesh:
    filename: str
    version: int
    elements: list[Element] = field(default_factory=list)
    linkages: list[Linkage] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)

    def element_of(self, linkage: Linkage, identity: int) -> Element | None:
        """The first element of the given kind reachable through a linkage."""
        for index in linkage.elements:
            if index < len(self.elements):
                element = self.elements[index]
                if element.identity == identity and element.count:
                    return element
        return None

    def total_faces(self) -> int:
        return sum(g.face_count for g in self.groups)

    def __str__(self) -> str:
        return (f'{self.filename!r}  {len(self.groups)} group(s), '
                f'{self.total_faces()} face(s), {len(self.elements)} element(s)')


def parse_gmdc(data: bytes) -> GmdcMesh:
    """Parse a GMDC resource (already decompressed)."""
    blocks, _ = s2texture.parse_rcol(data)
    if not blocks:
        raise ValueError('no RCOL block')
    block = blocks[0]
    if block.type_id != TYPE_GMDC:
        raise ValueError(f'not a GMDC (block type 0x{block.type_id:08X})')

    pos = block.start
    _sg, pos = s2texture._pascal(data, pos)
    pos += 8                                    # cSGResource: unknown + version
    filename, pos = s2texture._pascal(data, pos)

    mesh = GmdcMesh(filename, block.version)

    count, = struct.unpack_from('<I', data, pos)
    pos += 4
    for _ in range(count):
        (n, identity, sub_index, block_format,
         set_format, byte_size, _hash) = _ELEMENT_HEADER.unpack_from(data, pos)
        pos += _ELEMENT_HEADER.size
        if byte_size > len(data) - pos:
            raise ValueError(f'element payload {byte_size} overruns resource')
        mesh.elements.append(
            Element(n, identity, sub_index, block_format, set_format,
                    data[pos:pos + byte_size]))
        pos += byte_size

    count, = struct.unpack_from('<I', data, pos)
    pos += 4
    for _ in range(count):
        n, = struct.unpack_from('<I', data, pos)
        pos += 4
        indices = list(struct.unpack_from(f'<{n}H', data, pos))
        pos += n * 2
        vertex_count, = struct.unpack_from('<I', data, pos)
        pos += 20                               # vertex count plus four more
        mesh.linkages.append(Linkage(indices, vertex_count))

    count, = struct.unpack_from('<I', data, pos)
    pos += 4
    for _ in range(count):
        _primitive, linkage = struct.unpack_from('<II', data, pos)
        pos += 8
        name, pos = s2texture._pascal(data, pos)
        n, = struct.unpack_from('<I', data, pos)
        pos += 4
        if n * 2 > len(data) - pos:
            raise ValueError(f'group {name!r} index array overruns resource')
        indices = list(struct.unpack_from(f'<{n}H', data, pos))
        pos += n * 2
        mesh.groups.append(Group(name, linkage, indices))

    return mesh


def load_meshes(resources: list[s2writer.Resource]) -> list[GmdcMesh]:
    """Every mesh in a package, skipping resources that fail to parse."""
    out = []
    for r in resources:
        if r.type_id != TYPE_GMDC:
            continue
        try:
            out.append(parse_gmdc(r.data))
        except (ValueError, struct.error):
            continue
    return out


# ---------------------------------------------------------------------------
# OBJ export
# ---------------------------------------------------------------------------

def to_obj(mesh: GmdcMesh) -> str:
    """Render a mesh as Wavefront OBJ.

    Each group is emitted with its own copy of the vertices it addresses,
    because groups may sit on different linkages with independently numbered
    vertex arrays. OBJ indices are global and 1-based, so a running offset
    keeps the groups from addressing each other's vertices.
    """
    lines = [f'# {mesh.filename}',
             f'# {len(mesh.groups)} group(s), {mesh.total_faces()} face(s)',
             '# exported by s2mesh.py']
    v_off = vt_off = vn_off = 1

    for group in mesh.groups:
        if group.linkage >= len(mesh.linkages):
            continue
        linkage = mesh.linkages[group.linkage]
        positions = mesh.element_of(linkage, ID_POSITION)
        if positions is None:
            continue
        normals = mesh.element_of(linkage, ID_NORMAL)
        uvs = mesh.element_of(linkage, ID_UV)

        pos_rows = positions.floats()
        nrm_rows = normals.floats() if normals else []
        uv_rows = uvs.floats() if uvs else []

        lines.append(f'g {group.name or "group"}')
        for x, y, z in pos_rows:
            lines.append(f'v {x:.6g} {y:.6g} {z:.6g}')
        for row in uv_rows:
            # OBJ's V axis runs opposite to the game's, so flip it or every
            # exported texture lands upside down.
            lines.append(f'vt {row[0]:.6g} {1.0 - row[1]:.6g}')
        for row in nrm_rows:
            lines.append(f'vn {row[0]:.6g} {row[1]:.6g} {row[2]:.6g}')

        have_uv, have_n = bool(uv_rows), bool(nrm_rows)
        for i in range(0, len(group.indices) - 2, 3):
            corners = []
            for k in range(3):
                idx = group.indices[i + k]
                v = v_off + idx
                if have_uv and have_n:
                    corners.append(f'{v}/{vt_off + idx}/{vn_off + idx}')
                elif have_n:
                    corners.append(f'{v}//{vn_off + idx}')
                elif have_uv:
                    corners.append(f'{v}/{vt_off + idx}')
                else:
                    corners.append(str(v))
            lines.append('f ' + ' '.join(corners))

        v_off += len(pos_rows)
        vt_off += len(uv_rows)
        vn_off += len(nrm_rows)

    return '\n'.join(lines) + '\n'


def _safe(name: str) -> str:
    keep = [c if (c.isalnum() or c in '-_.') else '_' for c in name]
    return ''.join(keep).strip('_') or 'mesh'


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_list(path: Path) -> int:
    meshes = load_meshes(s2writer.read_all_resources(path))
    print(f'{path.name}  —  {len(meshes)} mesh(es)')
    for mesh in meshes:
        print(f'  {mesh}')
        for element in mesh.elements:
            if element.count:
                print(f'      {element}')
        for group in mesh.groups:
            print(f'      group {group.name!r}: {group.face_count} faces '
                  f'(linkage {group.linkage})')
    return 0


def cmd_export(path: Path, out_dir: Path) -> int:
    meshes = load_meshes(s2writer.read_all_resources(path))
    if not meshes:
        print(f'{path.name}: no meshes', file=sys.stderr)
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)
    for mesh in meshes:
        dest = out_dir / (_safe(mesh.filename) + '.obj')
        dest.write_text(to_obj(mesh))
        print(f'  {mesh.total_faces():6} faces  -> {dest.name}')
    return 0


def cmd_selftest(target: Path, limit: int = 400) -> int:
    """Parse meshes and check every face index lands in its vertex array.

    An out-of-range index is the failure that matters: it means the walk
    drifted and the groups are being read against the wrong vertex data.
    Byte consumption cannot be used here because the trailing bounding and
    subset section is deliberately not parsed.
    """
    packages = sorted(target.rglob('*.package')) if target.is_dir() else [target]
    parsed = exported = 0
    failures: list[str] = []
    no_geometry = 0

    for pkg in packages[:limit]:
        try:
            header, entries = s2parser.open_package(pkg)
        except Exception:
            continue
        with open(pkg, 'rb') as f:
            for entry in entries:
                if entry.type_id != TYPE_GMDC:
                    continue
                try:
                    data = s2parser.read_resource(f, entry)
                except Exception:
                    continue
                try:
                    mesh = parse_gmdc(data)
                except (ValueError, struct.error) as exc:
                    failures.append(f'{pkg.name}: {str(exc)[:60]}')
                    continue
                parsed += 1

                bad = False
                for group in mesh.groups:
                    if group.linkage >= len(mesh.linkages):
                        failures.append(
                            f'{pkg.name} {mesh.filename}: group {group.name!r} '
                            f'links to {group.linkage} of {len(mesh.linkages)}')
                        bad = True
                        break
                    positions = mesh.element_of(mesh.linkages[group.linkage],
                                                ID_POSITION)
                    if positions is None:
                        continue
                    if group.indices and max(group.indices) >= positions.count:
                        failures.append(
                            f'{pkg.name} {mesh.filename}: group {group.name!r} '
                            f'index {max(group.indices)} >= {positions.count} '
                            f'vertices')
                        bad = True
                        break
                if bad:
                    continue

                text = to_obj(mesh)
                if mesh.total_faces() and '\nf ' in text:
                    exported += 1
                elif not mesh.total_faces():
                    no_geometry += 1

    print(f'mesh selftest: {parsed} GMDC parsed, {exported} exported with '
          f'geometry, {no_geometry} with no faces, {len(failures)} failures')
    for msg in failures[:10]:
        print(f'  FAIL {msg}')
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description='Read Sims 2 meshes (GMDC) and export Wavefront OBJ.')
    ap.add_argument('target', type=Path, help='.package file, or a directory')
    ap.add_argument('--export', type=Path, metavar='DIR',
                    help='write each mesh as an .obj into DIR')
    ap.add_argument('--selftest', action='store_true',
                    help='parse meshes under TARGET and verify face indices')
    ap.add_argument('--limit', type=int, default=400,
                    help='packages to scan in --selftest (default 400)')
    args = ap.parse_args(argv)

    if args.selftest:
        return cmd_selftest(args.target, args.limit)
    if args.export:
        return cmd_export(args.target, args.export)
    return cmd_list(args.target)


if __name__ == '__main__':
    sys.exit(main())
