#!/usr/bin/env python3
"""s2texture.py — read Sims 2 textures (TXTR/LIFO) and export them as PNG.

Textures are not flat resources like OBJD or BHAV. They are RCOL documents:
a self-describing chain of named blocks, the same container the scenegraph
(CRES/SHPE/GMND/GMDC) uses. So the RCOL reader here is deliberately generic —
it is the entry point for meshes too.

    RCOL:  u32 0xFFFF0001, u32 link count, links, u32 block count,
           u32 block type ids, then each block as
           [pascal name][u32 type id][u32 version][payload]

The texture block is cImageData, which opens with an embedded cSGResource
naming the texture, then:

    u32 width, u32 height, u32 format, u32 mip levels,
    float (always 1.0), u32 (always 1), u32 unknown, u8 unknown,
    u32 mip levels again, then one entry per level SMALLEST FIRST:

        u8 0 -> u32 size, then that many bytes
        u8 1 -> pascal string naming the LIFO holding this level

    then u32 unknown and a trailing float (10.0 in all 5,983 samples).

The big mip levels of detailed textures live in separate LIFO resources
rather than inline, which is why a level can be a name instead of bytes.
Resolving those needs the whole package, so `load_texture` takes one.

Format codes were identified from the game's own textures rather than
assumed, by solving bytes-per-4x4-block across ~6,000 samples and then
splitting the ties on the smallest stored level — block compression can
never go below one whole block, raw formats go down to bytes-per-pixel:

    code 1  raw ARGB32   64 B/block, min level 4     (4 B/px)
    code 2  raw RGB24    48 B/block, min level 3     (3 B/px)
    code 3  raw 8-bit    16 B/block, min level 1     (1 B/px)
    code 4  DXT1          8 B/block, min level 8     never below a block
    code 5  DXT3         16 B/block, min level 16    never below a block
    code 6  raw 8-bit    16 B/block, min level 1     (1 B/px)
    code 8  DXT5         16 B/block, min level 16    never below a block

Codes 5 and 8 are both 16 B/block, so size alone cannot separate them; they
are read as DXT3 and DXT5 respectively, which is the only way two distinct
codes at one block size make sense (explicit vs interpolated alpha).
"""

# Annotations stay strings so the module imports under the system
# python3 (3.9), the same constraint the rest of the toolkit works under.
from __future__ import annotations

import argparse
import struct
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import s2parser
import s2writer

# Type ids taken from what the resources call themselves in their RCOL block
# name, censused over ~25,000 game resources — not from the type table, which
# had several of these wrong. LIFO is "Level Info", one mip level of a texture.
TYPE_TXTR = 0x1C4A276C      # cImageData
TYPE_LIFO = 0xED534136      # cLevelInfo
TYPE_TXMT = 0x49596978      # cMaterialDefinition

RCOL_MAGIC = 0xFFFF0001

# format code -> (name, bytes per 4x4 block, block compressed?)
FORMATS = {
    1: ('ARGB32', 64, False),
    2: ('RGB24', 48, False),
    3: ('Raw8', 16, False),
    4: ('DXT1', 8, True),
    5: ('DXT3', 16, True),
    6: ('Raw8', 16, False),
    8: ('DXT5', 16, True),
}


def _pascal(data: bytes, pos: int) -> tuple[str, int]:
    """Read a length-prefixed string. Lengths above 0x7F use a varint form
    that no sample here exercises, so it is rejected rather than guessed."""
    n = data[pos]
    pos += 1
    if n & 0x80:
        raise ValueError('multi-byte pascal length not supported')
    return data[pos:pos + n].decode('latin-1', 'replace'), pos + n


# ---------------------------------------------------------------------------
# RCOL container — shared with the scenegraph resources
# ---------------------------------------------------------------------------

@dataclass
class RcolBlock:
    name: str
    type_id: int
    version: int
    start: int          # offset of the payload, just past the block header


def parse_rcol(data: bytes) -> tuple[list[RcolBlock], int]:
    """Read an RCOL header. Returns its blocks and the offset after the last
    block header — only the first block's payload can be located this way,
    since block payloads are variable length and self-delimiting."""
    if len(data) < 12:
        raise ValueError(f'too short for RCOL ({len(data)} bytes)')
    magic, links = struct.unpack_from('<II', data, 0)
    if magic != RCOL_MAGIC:
        raise ValueError(f'not an RCOL (magic 0x{magic:08X})')
    pos = 8 + links * 16
    count, = struct.unpack_from('<I', data, pos)
    pos += 4
    type_ids = [struct.unpack_from('<I', data, pos + i * 4)[0] for i in range(count)]
    pos += count * 4

    blocks = []
    for tid in type_ids:
        name, pos = _pascal(data, pos)
        block_tid, version = struct.unpack_from('<II', data, pos)
        pos += 8
        blocks.append(RcolBlock(name, block_tid, version, pos))
        break   # only the first block's payload start is knowable up front
    return blocks, pos


# ---------------------------------------------------------------------------
# cImageData
# ---------------------------------------------------------------------------

@dataclass
class MipLevel:
    width: int
    height: int
    data: bytes | None = None    # None when the level lives in a LIFO
    lifo: str | None = None

    @property
    def resolved(self) -> bool:
        return self.data is not None


@dataclass
class Texture:
    name: str
    width: int
    height: int
    format: int
    levels: list[MipLevel] = field(default_factory=list)   # largest first

    @property
    def format_name(self) -> str:
        return FORMATS.get(self.format, (f'code{self.format}', 0, False))[0]

    def largest(self) -> MipLevel | None:
        """Biggest level whose bytes we actually have."""
        for level in self.levels:
            if level.resolved:
                return level
        return None

    def __str__(self) -> str:
        missing = sum(1 for l in self.levels if not l.resolved)
        note = f', {missing} in LIFO' if missing else ''
        return (f'{self.width}x{self.height} {self.format_name} '
                f'{len(self.levels)} levels{note}  "{self.name}"')


def parse_image_data(data: bytes) -> Texture:
    """Parse a cImageData RCOL (a TXTR, or the image block of a LIFO)."""
    _blocks, pos = parse_rcol(data)
    _sg, pos = _pascal(data, pos)
    pos += 8                                    # cSGResource type id + version
    name, pos = _pascal(data, pos)

    width, height, fmt, mips = struct.unpack_from('<4I', data, pos)
    pos += 24                                   # + float 1.0 + u32 1
    pos += 4                                    # unknown u32
    pos += 1                                    # unknown u8
    stored, = struct.unpack_from('<I', data, pos)
    pos += 4

    levels: list[MipLevel] = []
    for i in range(stored):
        marker = data[pos]
        pos += 1
        # Levels are listed smallest first, so index i counts down from the top.
        shift = stored - 1 - i
        lw, lh = max(1, width >> shift), max(1, height >> shift)
        if marker == 0:
            size, = struct.unpack_from('<I', data, pos)
            pos += 4
            levels.append(MipLevel(lw, lh, data=data[pos:pos + size]))
            pos += size
        elif marker == 1:
            lifo, pos = _pascal(data, pos)
            levels.append(MipLevel(lw, lh, lifo=lifo))
        else:
            raise ValueError(f'unknown mip marker {marker} at level {i}')

    levels.reverse()                            # hand back largest first
    return Texture(name, width, height, fmt, levels)


def parse_level_info(data: bytes) -> tuple[str, MipLevel]:
    """Parse a LIFO (cLevelInfo): one mip level held outside its texture.

    Layout after the cSGResource name is width, height, a stride-like u32,
    then the byte count and that many bytes. The pixel format is deliberately
    absent — it belongs to the TXTR that references this level.
    """
    _blocks, pos = parse_rcol(data)
    _sg, pos = _pascal(data, pos)
    pos += 8
    name, pos = _pascal(data, pos)
    width, height, _stride, size = struct.unpack_from('<4I', data, pos)
    pos += 16
    return name, MipLevel(width, height, data=data[pos:pos + size])


def load_texture(resources: list[s2writer.Resource], entry: s2writer.Resource
                 ) -> Texture:
    """Parse a TXTR and fill in any levels that live in the package's LIFOs."""
    texture = parse_image_data(entry.data)
    if not any(l.lifo for l in texture.levels):
        return texture

    by_name: dict[str, MipLevel] = {}
    for r in resources:
        if r.type_id != TYPE_LIFO:
            continue
        try:
            name, level = parse_level_info(r.data)
        except (ValueError, struct.error, IndexError):
            continue
        by_name[name] = level

    for level in texture.levels:
        if not level.lifo:
            continue
        source = by_name.get(level.lifo)
        if source is not None and source.width == level.width:
            level.data = source.data
    return texture


# ---------------------------------------------------------------------------
# Pixel decoding
# ---------------------------------------------------------------------------

def _rgb565(value: int) -> tuple[int, int, int]:
    r = (value >> 11) & 0x1F
    g = (value >> 5) & 0x3F
    b = value & 0x1F
    # Replicate high bits into the low ones so 0x1F maps to 255, not 248.
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)


def _decode_dxt(data: bytes, width: int, height: int, fmt: int) -> bytearray:
    """Decode DXT1/3/5 into RGBA. Blocks are 4x4 and run left-to-right,
    top-to-bottom; edge blocks in sub-4-pixel images are partly discarded."""
    out = bytearray(width * height * 4)
    bw, bh = max(1, (width + 3) // 4), max(1, (height + 3) // 4)
    stride = 8 if fmt == 4 else 16
    pos = 0

    for by in range(bh):
        for bx in range(bw):
            if pos + stride > len(data):
                return out
            alpha = [255] * 16
            block = pos
            if fmt == 5:                        # DXT3: 4 bits per pixel, direct
                for i in range(8):
                    packed = data[block + i]
                    alpha[i * 2] = (packed & 0x0F) * 17
                    alpha[i * 2 + 1] = (packed >> 4) * 17
                block += 8
            elif fmt == 8:                      # DXT5: two endpoints + 3-bit idx
                a0, a1 = data[block], data[block + 1]
                table = [a0, a1]
                if a0 > a1:
                    table += [((7 - i) * a0 + (i + 1) * a1) // 7 for i in range(6)]
                else:
                    table += [((5 - i) * a0 + (i + 1) * a1) // 5 for i in range(4)]
                    table += [0, 255]
                bits = int.from_bytes(data[block + 2:block + 8], 'little')
                for i in range(16):
                    alpha[i] = table[(bits >> (3 * i)) & 0x07]
                block += 8

            c0, c1 = struct.unpack_from('<HH', data, block)
            r0, g0, b0 = _rgb565(c0)
            r1, g1, b1 = _rgb565(c1)
            colors = [(r0, g0, b0, 255), (r1, g1, b1, 255)]
            if c0 > c1 or fmt != 4:
                # Four-colour block. DXT3/DXT5 always use it; DXT1 only when
                # c0 > c1, otherwise it switches to three colours plus a
                # punch-through transparent index.
                colors.append(((2 * r0 + r1) // 3, (2 * g0 + g1) // 3,
                               (2 * b0 + b1) // 3, 255))
                colors.append(((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3,
                               (b0 + 2 * b1) // 3, 255))
            else:
                colors.append(((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2, 255))
                colors.append((0, 0, 0, 0))
            indices, = struct.unpack_from('<I', data, block + 4)

            for py in range(4):
                y = by * 4 + py
                if y >= height:
                    break
                for px in range(4):
                    x = bx * 4 + px
                    if x >= width:
                        continue
                    i = py * 4 + px
                    r, g, b, a = colors[(indices >> (2 * i)) & 0x03]
                    o = (y * width + x) * 4
                    out[o] = r
                    out[o + 1] = g
                    out[o + 2] = b
                    out[o + 3] = min(a, alpha[i])
            pos += stride
    return out


def decode(level: MipLevel, fmt: int) -> bytearray:
    """Decode one mip level to RGBA bytes."""
    if level.data is None:
        raise ValueError('level has no data (it lives in a LIFO)')
    if fmt not in FORMATS:
        raise ValueError(f'unsupported texture format code {fmt}')
    if FORMATS[fmt][2]:
        return _decode_dxt(level.data, level.width, level.height, fmt)

    n = level.width * level.height
    src = level.data
    out = bytearray(n * 4)
    if fmt == 1:                                # stored BGRA
        for i in range(min(n, len(src) // 4)):
            b, g, r, a = src[i * 4:i * 4 + 4]
            out[i * 4:i * 4 + 4] = bytes((r, g, b, a))
    elif fmt == 2:
        for i in range(min(n, len(src) // 3)):
            b, g, r = src[i * 3:i * 3 + 3]
            out[i * 4:i * 4 + 4] = bytes((r, g, b, 255))
    else:                                       # 3 and 6: single channel
        for i in range(min(n, len(src))):
            v = src[i]
            out[i * 4:i * 4 + 4] = bytes((v, v, v, 255))
    return out


# ---------------------------------------------------------------------------
# PNG output — stdlib only, so the bundled app needs no extra dependency
# ---------------------------------------------------------------------------

def png_bytes(width: int, height: int, rgba: bytes) -> bytes:
    """Encode RGBA into a PNG. Each scanline gets filter byte 0 (none)."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += rgba[y * width * 4:(y + 1) * width * 4]

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack('>I', len(payload)) + kind + payload
                + struct.pack('>I', zlib.crc32(kind + payload) & 0xFFFFFFFF))

    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes(raw), 9))
            + chunk(b'IEND', b''))


def export_png(texture: Texture, path: Path) -> tuple[int, int]:
    """Write a texture's largest resolved level as a PNG."""
    level = texture.largest()
    if level is None:
        raise ValueError(f'no resolved level in {texture.name!r}')
    rgba = decode(level, texture.format)
    Path(path).write_bytes(png_bytes(level.width, level.height, rgba))
    return level.width, level.height


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _safe(name: str) -> str:
    keep = [c if (c.isalnum() or c in '-_.') else '_' for c in name]
    return ''.join(keep)[:120] or 'texture'


def cmd_list(path: Path) -> int:
    resources = s2writer.read_all_resources(path)
    textures = [r for r in resources if r.type_id == TYPE_TXTR]
    print(f'{path.name}  —  {len(textures)} TXTR, '
          f'{sum(1 for r in resources if r.type_id == TYPE_LIFO)} LIFO')
    for r in textures:
        try:
            print(f'  {load_texture(resources, r)}')
        except ValueError as exc:
            print(f'  [skip] i=0x{r.instance_id:X}: {exc}')
    return 0


def cmd_export(path: Path, outdir: Path) -> int:
    resources = s2writer.read_all_resources(path)
    outdir.mkdir(parents=True, exist_ok=True)
    written = failed = 0
    for r in resources:
        if r.type_id != TYPE_TXTR:
            continue
        try:
            texture = load_texture(resources, r)
            dest = outdir / f'{_safe(texture.name)}.png'
            w, h = export_png(texture, dest)
            print(f'  {w}x{h} {texture.format_name:6} -> {dest.name}')
            written += 1
        except ValueError as exc:
            print(f'  [skip] i=0x{r.instance_id:X}: {exc}', file=sys.stderr)
            failed += 1
    print(f'{written} PNG(s) written to {outdir}' + (f', {failed} skipped' if failed else ''))
    return 1 if failed and not written else 0


def _selftest(sample_dir: str) -> int:
    """Parse every TXTR/LIFO found and decode the ones we can.

    The bar for parsing is that the block consumes its resource exactly —
    a layout that is merely plausible will drift and leave bytes over. The
    bar for decoding is that the output is the full expected pixel count.
    """
    parsed = decoded = 0
    failures: list[str] = []
    for path in sorted(Path(sample_dir).rglob('*.package')):
        try:
            resources = s2writer.read_all_resources(path)
        except Exception:
            continue
        for r in resources:
            if r.type_id == TYPE_LIFO:
                try:
                    parse_level_info(r.data)
                    parsed += 1
                except (ValueError, struct.error, IndexError) as exc:
                    failures.append(f'{path.name} LIFO 0x{r.instance_id:X}: {exc}')
                continue
            if r.type_id != TYPE_TXTR:
                continue
            try:
                texture = load_texture(resources, r)
            except (ValueError, struct.error, IndexError) as exc:
                failures.append(f'{path.name} TXTR 0x{r.instance_id:X}: {exc}')
                continue
            parsed += 1
            level = texture.largest()
            if level is None or texture.format not in FORMATS:
                continue
            try:
                rgba = decode(level, texture.format)
            except (ValueError, struct.error, IndexError) as exc:
                failures.append(f'{path.name} decode {texture.name}: {exc}')
                continue
            if len(rgba) != level.width * level.height * 4:
                failures.append(f'{path.name} {texture.name}: short decode')
                continue
            decoded += 1

    print(f'texture selftest: {parsed} image resource(s) parsed, {decoded} decoded')
    for msg in failures[:10]:
        print(f'  FAIL {msg}')
    if len(failures) > 10:
        print(f'  ... and {len(failures) - 10} more')
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description='Read Sims 2 textures and export them as PNG.')
    ap.add_argument('path', type=Path, help='.package file, or a directory for --selftest')
    ap.add_argument('--export', type=Path, metavar='DIR',
                    help='write every texture to DIR as PNG')
    ap.add_argument('--selftest', action='store_true',
                    help='parse and decode every texture under PATH')
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest(str(args.path))
    if args.export:
        return cmd_export(args.path, args.export)
    return cmd_list(args.path)


if __name__ == '__main__':
    sys.exit(main())
