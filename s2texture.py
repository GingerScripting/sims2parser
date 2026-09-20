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
    float (always 1.0), u32 (always 1), u32 unknown, a pascal string
    (empty in every game texture; a floor recolour puts its own name here),
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


@dataclass
class RcolHeader:
    """The part of an RCOL before its first block: the optional version
    marker, the link table and the block type list. Kept whole so a rebuilt
    resource reproduces the header form it came with."""
    has_magic: bool
    links: list[tuple[int, int, int, int]]     # (group, instance, instance_hi, type)
    block_ids: list[int]


def parse_rcol_header(data: bytes) -> tuple[RcolHeader, int]:
    """Read an RCOL header; returns it and the offset of the first block."""
    if len(data) < 12:
        raise ValueError(f'too short for RCOL ({len(data)} bytes)')
    magic, = struct.unpack_from('<I', data, 0)
    if magic == RCOL_MAGIC:
        count, = struct.unpack_from('<I', data, 4)
        pos, has_magic = 8, True
    else:
        count, pos, has_magic = magic, 4, False
    # Guard the versionless reading: a wild first u32 would otherwise be
    # trusted as a link count and walk the cursor off into the payload.
    if count > 0xFFFF:
        raise ValueError(f'not an RCOL (leading u32 0x{magic:08X})')
    links = [struct.unpack_from('<IIII', data, pos + i * 16) for i in range(count)]
    pos += count * 16
    nblocks, = struct.unpack_from('<I', data, pos)
    pos += 4
    if nblocks > 0xFFFF:
        raise ValueError(f'implausible RCOL block count {nblocks}')
    block_ids = [struct.unpack_from('<I', data, pos + i * 4)[0] for i in range(nblocks)]
    pos += nblocks * 4
    return RcolHeader(has_magic, [tuple(l) for l in links], block_ids), pos


def build_rcol_header(header: RcolHeader) -> bytes:
    out = bytearray()
    if header.has_magic:
        out += struct.pack('<I', RCOL_MAGIC)
    out += struct.pack('<I', len(header.links))
    for link in header.links:
        out += struct.pack('<IIII', *link)
    out += struct.pack('<I', len(header.block_ids))
    for tid in header.block_ids:
        out += struct.pack('<I', tid)
    return bytes(out)


def pascal_bytes(text: str) -> bytes:
    """A length-prefixed string the way RCOL blocks store them. Lengths of
    0x80 and above need the varint form the reader rejects, so they are
    refused here too rather than written unreadably."""
    raw = text.encode('latin-1', 'replace')
    if len(raw) >= 0x80:
        raise ValueError(f'name too long for a pascal string ({len(raw)} bytes): {text[:40]!r}')
    return bytes([len(raw)]) + raw


def parse_rcol(data: bytes) -> tuple[list[RcolBlock], int]:
    """Read an RCOL header. Returns its blocks and the offset after the last
    block header — only the first block's payload can be located this way,
    since block payloads are variable length and self-delimiting.

    Two header forms occur. The common one opens with the version marker
    0xFFFF0001 and then the link count; the other omits the version entirely
    and opens with the link count itself. Both appear in the game's own
    textures (CAS!.package and objects.package use the short form), so the
    marker is treated as optional rather than required.
    """
    header, pos = parse_rcol_header(data)
    blocks = []
    for tid in header.block_ids:
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
    # Everything below is what a byte-exact rebuild needs and a reader does
    # not: the RCOL header, the block and cSGResource versions, and the two
    # words nobody has explained. `tail` differs between textures (96 distinct
    # values in 99 game samples) so it is carried verbatim, never invented.
    header: RcolHeader | None = None
    block_name: str = 'cImageData'
    block_version: int = 9
    sg_type: int = 0
    sg_version: int = 2
    scale: float = 1.0          # 1.0 in nearly every sample, 3.0 in a few
    mip_blocks: int = 1         # >1 is the wall/floor form this reader declines
    unknown_u32: int = 0
    sub_name: str = ''          # a second name; empty in the game's textures
    mips: int | None = None     # the first level count; equals len(levels) everywhere seen
    tail: int = 0
    tail_float: float = 10.0

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
    header, pos = parse_rcol_header(data)
    if len(header.block_ids) != 1:
        raise ValueError(f'cImageData with {len(header.block_ids)} blocks')
    block_name, pos = _pascal(data, pos)
    block_tid, block_version = struct.unpack_from('<II', data, pos)
    pos += 8
    _sg, pos = _pascal(data, pos)
    sg_type, sg_version = struct.unpack_from('<II', data, pos)
    pos += 8                                    # cSGResource type id + version
    name, pos = _pascal(data, pos)

    width, height, fmt, mips = struct.unpack_from('<4I', data, pos)
    pos += 16
    scale, mip_blocks, unknown_u32 = struct.unpack_from('<fII', data, pos)
    pos += 12
    sub_name, pos = _pascal(data, pos)
    if mip_blocks != 1:
        # Walls and floors carry several level lists in one resource; the
        # object textures this module serves never do. Decline rather than
        # misread the second list as pixels.
        raise ValueError(f'texture with {mip_blocks} mip blocks (wall/floor form)')
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
    tail, tail_float = struct.unpack_from('<If', data, pos)
    pos += 8
    if pos != len(data):
        raise ValueError(f'{len(data) - pos} byte(s) left after the texture')

    levels.reverse()                            # hand back largest first
    return Texture(name, width, height, fmt, levels, header=header,
                   block_name=block_name, block_version=block_version,
                   sg_type=sg_type, sg_version=sg_version, scale=scale,
                   mip_blocks=mip_blocks, unknown_u32=unknown_u32,
                   sub_name=sub_name, mips=mips, tail=tail,
                   tail_float=tail_float)


def build_image_data(t: Texture) -> bytes:
    """Serialize a Texture. Inverse of parse_image_data, byte for byte."""
    header = t.header or RcolHeader(True, [], [TYPE_TXTR])
    out = bytearray(build_rcol_header(header))
    out += pascal_bytes(t.block_name) + struct.pack('<II', TYPE_TXTR, t.block_version)
    out += pascal_bytes('cSGResource') + struct.pack('<II', t.sg_type, t.sg_version)
    out += pascal_bytes(t.name)
    mips = len(t.levels) if t.mips is None else t.mips
    out += struct.pack('<4I', t.width, t.height, t.format, mips)
    out += struct.pack('<fII', t.scale, t.mip_blocks, t.unknown_u32)
    out += pascal_bytes(t.sub_name)
    out += struct.pack('<I', len(t.levels))
    for level in reversed(t.levels):            # smallest first on disk
        if level.data is not None:
            out.append(0)
            out += struct.pack('<I', len(level.data)) + level.data
        elif level.lifo:
            out.append(1)
            out += pascal_bytes(level.lifo)
        else:
            raise ValueError(f'level {level.width}x{level.height} has neither bytes nor a LIFO name')
    out += struct.pack('<If', t.tail, t.tail_float)
    return bytes(out)


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
# PNG input — the other direction, for pictures the user brings
# ---------------------------------------------------------------------------

def read_png(data: bytes) -> tuple[int, int, bytearray]:
    """Decode a PNG into (width, height, RGBA bytes).

    Handles the files this app writes and the ones people save from ordinary
    editors: 8-bit, non-interlaced, greyscale, RGB, palette (with tRNS),
    greyscale+alpha and RGBA. 16-bit and interlaced files are refused with a
    message that says what to change; the app normalises pictures through
    CoreGraphics before they get here, so this mostly serves the command line.
    """
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('not a PNG file')
    pos = 8
    width = height = 0
    depth = colour = interlace = 0
    palette = b''
    trns = b''
    idat = bytearray()
    while pos + 8 <= len(data):
        length, = struct.unpack_from('>I', data, pos)
        kind = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b'IHDR':
            width, height, depth, colour, _comp, _filt, interlace = struct.unpack('>IIBBBBB', payload)
        elif kind == b'PLTE':
            palette = payload
        elif kind == b'tRNS':
            trns = payload
        elif kind == b'IDAT':
            idat += payload
        elif kind == b'IEND':
            break
    if not width or not height:
        raise ValueError('PNG has no IHDR')
    if depth != 8:
        raise ValueError(f'{depth}-bit PNG; save it as 8 bits per channel')
    if interlace:
        raise ValueError('interlaced PNG; save it without interlacing')
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(colour)
    if channels is None:
        raise ValueError(f'unsupported PNG colour type {colour}')

    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    if len(raw) < height * (stride + 1):
        raise ValueError('PNG image data is truncated')
    prev = bytearray(stride)
    rows: list[bytearray] = []
    src_pos = 0
    for _y in range(height):
        ftype = raw[src_pos]
        row = bytearray(raw[src_pos + 1:src_pos + 1 + stride])
        src_pos += 1 + stride
        if ftype == 1:
            for i in range(channels, stride):
                row[i] = (row[i] + row[i - channels]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                row[i] = (row[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = row[i - channels] if i >= channels else 0
                row[i] = (row[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                a = row[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                row[i] = (row[i] + pred) & 0xFF
        elif ftype != 0:
            raise ValueError(f'unknown PNG filter type {ftype}')
        rows.append(row)
        prev = row

    out = bytearray(width * height * 4)
    o = 0
    if colour == 6:
        for row in rows:
            out[o:o + stride] = row
            o += stride
    elif colour == 2:
        for row in rows:
            for x in range(width):
                out[o:o + 4] = bytes((row[x * 3], row[x * 3 + 1], row[x * 3 + 2], 255))
                o += 4
    elif colour == 0:
        for row in rows:
            for x in range(width):
                v = row[x]
                out[o:o + 4] = bytes((v, v, v, 255))
                o += 4
    elif colour == 4:
        for row in rows:
            for x in range(width):
                v = row[x * 2]
                out[o:o + 4] = bytes((v, v, v, row[x * 2 + 1]))
                o += 4
    else:                                       # 3: palette
        for row in rows:
            for x in range(width):
                i = row[x]
                out[o:o + 4] = bytes((palette[i * 3], palette[i * 3 + 1], palette[i * 3 + 2],
                                      trns[i] if i < len(trns) else 255))
                o += 4
    return width, height, out


# ---------------------------------------------------------------------------
# Pixel work: mip chains, tinting, DXT encoding
# ---------------------------------------------------------------------------

def has_alpha(rgba: bytes) -> bool:
    return any(rgba[i] != 255 for i in range(3, len(rgba), 4))


def mip_chain(width: int, height: int, rgba: bytes) -> list[tuple[int, int, bytes]]:
    """Every level from the full image down to 1x1, largest first, by 2x2
    box filter. Odd edges fold the last column or row in on itself."""
    levels = [(width, height, bytes(rgba))]
    w, h, src = width, height, rgba
    while w > 1 or h > 1:
        nw, nh = max(1, w // 2), max(1, h // 2)
        dst = bytearray(nw * nh * 4)
        for y in range(nh):
            y0 = min(2 * y, h - 1)
            y1 = min(2 * y + 1, h - 1)
            for x in range(nw):
                x0 = min(2 * x, w - 1)
                x1 = min(2 * x + 1, w - 1)
                a = (y0 * w + x0) * 4
                b = (y0 * w + x1) * 4
                c = (y1 * w + x0) * 4
                d = (y1 * w + x1) * 4
                o = (y * nw + x) * 4
                for k in range(4):
                    dst[o + k] = (src[a + k] + src[b + k] + src[c + k] + src[d + k] + 2) >> 2
        levels.append((nw, nh, bytes(dst)))
        w, h, src = nw, nh, dst
    return levels


def tint(rgba: bytes, color: tuple[int, int, int], strength: float,
         lightness: float = 0.0) -> bytearray:
    """Recolour a texture the way a paint tint does: the pixel's luminance
    carries `color`, blended over the original by `strength` (0 = untouched,
    1 = fully recoloured), then the whole thing is brightened or darkened by
    `lightness` (-1..1). Integer arithmetic throughout, so two builds of the
    same look agree byte for byte."""
    s = max(0, min(1024, int(round(strength * 1024))))
    gain = max(0, int(round((1.0 + max(-1.0, min(1.0, lightness))) * 1024)))
    cr, cg, cb = (max(0, min(255, int(c))) for c in color)
    out = bytearray(rgba)
    for i in range(0, len(out), 4):
        r, g, b = out[i], out[i + 1], out[i + 2]
        lum = (299 * r + 587 * g + 114 * b) // 1000
        tr, tg, tb = cr * lum // 255, cg * lum // 255, cb * lum // 255
        r = (r * (1024 - s) + tr * s) >> 10
        g = (g * (1024 - s) + tg * s) >> 10
        b = (b * (1024 - s) + tb * s) >> 10
        out[i] = min(255, (r * gain) >> 10)
        out[i + 1] = min(255, (g * gain) >> 10)
        out[i + 2] = min(255, (b * gain) >> 10)
    return out


def _pack565(r: int, g: int, b: int) -> int:
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def _block_pixels(rgba: bytes, width: int, height: int, bx: int, by: int
                  ) -> list[tuple[int, int, int, int]]:
    """The 16 pixels of a 4x4 block; edge blocks repeat their last row and
    column so every block is full and the average is not skewed by zeros."""
    px = []
    for py in range(4):
        y = min(by * 4 + py, height - 1)
        for pxx in range(4):
            x = min(bx * 4 + pxx, width - 1)
            o = (y * width + x) * 4
            px.append((rgba[o], rgba[o + 1], rgba[o + 2], rgba[o + 3]))
    return px


def _fit_colours(px: list[tuple[int, int, int, int]]) -> tuple[int, int, int]:
    """Choose two RGB565 endpoints for a block and index every pixel to the
    nearest of the four palette colours. Bounding-box endpoints along the
    block's brightest-to-darkest diagonal, then one least-squares refinement,
    which is what keeps flat blocks exact and gradients smooth enough."""
    rs = [p[0] for p in px]
    gs = [p[1] for p in px]
    bs = [p[2] for p in px]
    lo = (min(rs), min(gs), min(bs))
    hi = (max(rs), max(gs), max(bs))
    if lo == hi:
        c = _pack565(*hi)
        return c, c, 0
    # Orient the endpoints so pixels near the box's dark corner get index 1
    # and near the bright corner index 0, whichever axis dominates.
    def quant(c: tuple[int, int, int]) -> int:
        return _pack565(*c)

    def palette(c0: int, c1: int) -> list[tuple[int, int, int]]:
        r0, g0, b0 = _rgb565(c0)
        r1, g1, b1 = _rgb565(c1)
        return [(r0, g0, b0), (r1, g1, b1),
                ((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3),
                ((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3)]

    def assign(pal: list[tuple[int, int, int]]) -> tuple[list[int], int]:
        idx = []
        err = 0
        for r, g, b, _a in px:
            best = 0
            best_d = 1 << 30
            for k, (pr, pg, pb) in enumerate(pal):
                d = (r - pr) * (r - pr) + (g - pg) * (g - pg) + (b - pb) * (b - pb)
                if d < best_d:
                    best_d, best = d, k
            idx.append(best)
            err += best_d
        return idx, err

    c0, c1 = quant(hi), quant(lo)
    if c0 == c1:
        return c0, c0, 0
    if c0 < c1:
        c0, c1 = c1, c0
    idx, err = assign(palette(c0, c1))

    # One least-squares pass: given the assignment, solve for the endpoints
    # that minimise the error, requantise, and keep the result if it helped.
    w0 = w1 = 0.0
    n_a = n_b = n_ab = 0.0
    for (r, g, b, _a), k in zip(px, idx):
        fa = (1.0, 0.0, 2 / 3, 1 / 3)[k]
        fb = 1.0 - fa
        n_a += fa * fa
        n_b += fb * fb
        n_ab += fa * fb
    det = n_a * n_b - n_ab * n_ab
    if det > 1e-9:
        sums_a = [0.0, 0.0, 0.0]
        sums_b = [0.0, 0.0, 0.0]
        for (r, g, b, _a), k in zip(px, idx):
            fa = (1.0, 0.0, 2 / 3, 1 / 3)[k]
            fb = 1.0 - fa
            for ch, v in enumerate((r, g, b)):
                sums_a[ch] += fa * v
                sums_b[ch] += fb * v
        end0 = []
        end1 = []
        for ch in range(3):
            e0 = (sums_a[ch] * n_b - sums_b[ch] * n_ab) / det
            e1 = (sums_b[ch] * n_a - sums_a[ch] * n_ab) / det
            end0.append(max(0, min(255, int(round(e0)))))
            end1.append(max(0, min(255, int(round(e1)))))
        d0, d1 = quant(tuple(end0)), quant(tuple(end1))
        if d0 < d1:
            d0, d1 = d1, d0
        if d0 != d1:
            idx2, err2 = assign(palette(d0, d1))
            if err2 < err:
                c0, c1, idx = d0, d1, idx2
    bits = 0
    for i, k in enumerate(idx):
        bits |= k << (2 * i)
    return c0, c1, bits


def encode_dxt1(width: int, height: int, rgba: bytes) -> bytes:
    """Encode opaque RGBA as DXT1 (format code 4). Always writes c0 >= c1,
    so the four-colour palette applies and nothing turns transparent."""
    out = bytearray()
    bw, bh = max(1, (width + 3) // 4), max(1, (height + 3) // 4)
    for by in range(bh):
        for bx in range(bw):
            px = _block_pixels(rgba, width, height, bx, by)
            c0, c1, bits = _fit_colours(px)
            out += struct.pack('<HHI', c0, c1, bits)
    return bytes(out)


def encode_dxt5(width: int, height: int, rgba: bytes) -> bytes:
    """Encode RGBA with alpha as DXT5 (format code 8): eight interpolated
    alpha values per block plus the DXT1 colour block."""
    out = bytearray()
    bw, bh = max(1, (width + 3) // 4), max(1, (height + 3) // 4)
    for by in range(bh):
        for bx in range(bw):
            px = _block_pixels(rgba, width, height, bx, by)
            alphas = [p[3] for p in px]
            a0, a1 = max(alphas), min(alphas)
            if a0 == a1:
                table = [a0] * 8
                a1 = a0
            else:
                table = [a0, a1] + [((7 - i) * a0 + (i + 1) * a1) // 7 for i in range(6)]
            bits = 0
            for i, a in enumerate(alphas):
                best = min(range(8), key=lambda k: abs(table[k] - a))
                bits |= best << (3 * i)
            out += bytes((a0, a1)) + bits.to_bytes(6, 'little')
            c0, c1, cbits = _fit_colours(px)
            out += struct.pack('<HHI', c0, c1, cbits)
    return bytes(out)


def make_texture(name: str, width: int, height: int, rgba: bytes, *,
                 template: Texture | None = None) -> Texture:
    """A complete TXTR from pixels: DXT1 when the picture is opaque, DXT5
    otherwise, every mip level inline. `template` lends the header words that
    are copied rather than understood (`tail`, `scale`); without one the
    values every game texture shares are used."""
    if len(rgba) != width * height * 4:
        raise ValueError(f'expected {width * height * 4} RGBA bytes, got {len(rgba)}')
    fmt = 8 if has_alpha(rgba) else 4
    encode = encode_dxt5 if fmt == 8 else encode_dxt1
    levels = [MipLevel(w, h, data=encode(w, h, px)) for w, h, px in mip_chain(width, height, rgba)]
    t = Texture(name, width, height, fmt, levels,
                header=RcolHeader(True, [], [TYPE_TXTR]))
    if template is not None:
        t.scale = template.scale
        t.tail = template.tail
        t.tail_float = template.tail_float
        t.unknown_u32 = template.unknown_u32
    return t


# ---------------------------------------------------------------------------
# Names and hashes for custom scenegraph resources
# ---------------------------------------------------------------------------

RECOLOUR_GROUP = 0x1C050000      # where custom TXMT/TXTR live; names carry "##0x1C050000!"
RECOLOUR_PREFIX = '##0x1C050000!'

_CRC32_TABLE: list[int] | None = None


def crc32_maxis(text: str) -> int:
    """The 32-bit hash SimPE-made recolours put in a resource's high
    instance: CRC-32 with polynomial 0x04C11DB7, non-reflected, initial
    0xFFFFFFFF, no final XOR, over the lower-cased name. 42 of 42 donor
    materials and 36 of 40 donor textures follow it."""
    global _CRC32_TABLE
    if _CRC32_TABLE is None:
        table = []
        for n in range(256):
            c = n << 24
            for _ in range(8):
                c = ((c << 1) ^ 0x04C11DB7) if c & 0x80000000 else (c << 1)
            table.append(c & 0xFFFFFFFF)
        _CRC32_TABLE = table
    crc = 0xFFFFFFFF
    for b in text.encode('latin-1', 'replace'):
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC32_TABLE[((crc >> 24) ^ b) & 0xFF]
    return crc


def scenegraph_tgi(type_id: int, name: str, group: int = RECOLOUR_GROUP
                   ) -> tuple[int, int, int, int]:
    """(type, group, instance, instance_hi) for a custom scenegraph resource
    named `name` (with its `_txmt`/`_txtr` suffix), the way the game finds it."""
    key = name.lower()
    return type_id, group, 0xFF000000 | s2parser.crc24(key), crc32_maxis(key)


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
