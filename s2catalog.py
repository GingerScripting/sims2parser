#!/usr/bin/env python3
"""s2catalog.py — the object catalog Sim Studio's New Object screen browses.

Every buyable object the game knows, from its own objects.package and from
the custom content in Downloads: name, description, price, the Buy Mode
categories it sorts into, and a picture. The picture is a texture swatch —
the object's main texture, not a render of the model — found three ways,
cheapest first:

  1. the 42x42 TGA some of the game's own objects carry in their group
     (type 0x856DDBAC);
  2. for a custom package, the largest texture it ships;
  3. for a game object, the model chain: STR# 0x85 names the model, the
     CRES is found by name hash in Sims3D/Objects*.package, its links name
     the SHPEs, a SHPE names its materials, the TXMT names the base texture,
     and that is a TXTR. Every hop is a hash lookup that hits or misses.

The scenegraph is keyed by `0xFF000000 | crc24(name)` in group 0x1C0532FA
(see s2parser.crc24), so the ten Sims3D packages need only their indexes
read — a second, not a scan of 1.1 GB.

    python3 s2catalog.py                    # list the catalog
    python3 s2catalog.py --swatch GUID OUT  # write one object's swatch as PNG
"""

# Annotations stay strings so the module imports under the system
# python3 (3.9), which the app gets when launched from Finder.
from __future__ import annotations

import argparse
import json
import struct
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path

import s2doctor
import s2object
import s2parser
import s2texture
import s2writer
from s2parser import crc24

# Buy Mode categories: OBJD word 40 (function) and word 39 (room). Verified
# on the game's own objects: a fridge is Kitchen + Appliances, the cheap
# telescope Outside + Hobbies, the plastic high chair Kitchen + Dining + General.
FUNCTION_SORT = [
    (0x001, "Seating"), (0x002, "Surfaces"), (0x004, "Appliances"),
    (0x008, "Electronics"), (0x010, "Plumbing"), (0x020, "Decorative"),
    (0x040, "General"), (0x080, "Lighting"), (0x100, "Hobbies"),
    (0x200, "Aspiration Rewards"), (0x400, "Career Rewards"),
]
ROOM_SORT = [
    (0x001, "Kitchen"), (0x002, "Bedroom"), (0x004, "Bathroom"),
    (0x008, "Living Room"), (0x010, "Outside"), (0x020, "Dining Room"),
    (0x040, "Misc"), (0x080, "Study"), (0x100, "Kids"),
]

TYPE_TGA = 0x856DDBAC           # 42x42 32-bit TGA in some object groups
TYPE_CRES = 0xE519C933
TYPE_SHPE = 0xFC6EB1F7
TYPE_TXMT = 0x49596978
TYPE_TXTR = 0x1C4A276C
TYPE_LIFO = 0xED534136
SCENEGRAPH_GROUP = 0x1C0532FA
OBJ_TYPE_BUYABLE = 4
WORD_OBJ_TYPE, WORD_MASTER_ID, WORD_SUB_INDEX = 9, 10, 11
TILE_MASTER = 0xFFFF
MODEL_STR = 0x85

GAME_INSTALL = Path("/Applications/The Sims 2.app/Contents/Assets/TSData/Res")
OBJECTS_PACKAGE = GAME_INSTALL / "Objects/objects.package"
SIMS3D_DIR = GAME_INSTALL / "Sims3D"
CACHE_DIR = Path.home() / "Library/Application Support/SimStudio"
SWATCH_MAX_SIDE = 256

# Material names that are never the object's look.
_SWATCH_SKIP = ("shadow", "_alpha", "normal", "bump", "reflect", "cube")


def sort_names(flags: int, table: "list[tuple[int, str]]") -> "list[str]":
    return [name for bit, name in table if flags & bit]


@dataclass
class CatalogEntry:
    guid: int
    group: int
    source: str                 # package path; "game" for objects.package
    name: str
    description: str
    price: int
    room_flags: int
    function_flags: int
    tiles: int = 1              # OBJDs in the group (1 = single tile)
    model: str = ""             # STR# 0x85 model name, game objects only
    swatch: str = ""            # "tga" | "package" | "model" | "" (none known)
    filename: str = ""          # OBJD filename, for search

    @property
    def rooms(self) -> "list[str]":
        return sort_names(self.room_flags, ROOM_SORT)

    @property
    def functions(self) -> "list[str]":
        return sort_names(self.function_flags, FUNCTION_SORT)

    def to_json(self) -> dict:
        d = asdict(self)
        d["rooms"] = self.rooms
        d["functions"] = self.functions
        return d


# ---------------------------------------------------------------------------
# Reading packages by index
# ---------------------------------------------------------------------------

class PackageReader:
    """One open package: its index, its DIR, and resource reads by entry."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.header, self.entries = s2parser.open_package(self.path)
        version = (self.header.index_major_version, self.header.index_minor_version)
        with open(self.path, "rb") as f:
            self.directory = s2parser.read_dir(f, self.entries, version) or {}
        self.by_key = {(e.type_id, e.group_id, e.instance): e for e in self.entries}

    def read(self, entry) -> bytes:
        key = (entry.type_id, entry.group_id, entry.instance, entry.resource_id)
        with open(self.path, "rb") as f:
            return s2parser.read_resource(f, entry, compressed=key in self.directory)

    def get(self, type_id: int, group: int, instance: int) -> "bytes | None":
        e = self.by_key.get((type_id, group, instance))
        return self.read(e) if e is not None else None


def _english(table: "s2object.StrResource") -> "list[str]":
    return [e.value for e in table.entries if e.lang == 1]


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def scan_package(path: Path, source: "str | None" = None) -> "list[CatalogEntry]":
    """Every buyable object in one package.

    Buyable = OBJD type 4 with a function-sort flag (the game's own rule for
    what Buy Mode lists). A multi-tile object is listed once, by its master
    (sub index 0xFFFF), with the tile count; a "family" group (several
    objects sharing trees, master id 0) lists each member.
    """
    reader = PackageReader(path)
    source = source or str(path)
    objds: "dict[int, list[tuple]]" = {}
    for e in reader.entries:
        if e.type_id != s2object.TYPE_OBJD:
            continue
        try:
            o = s2object.parse_objd(reader.read(e))
        except (ValueError, struct.error):
            continue
        objds.setdefault(e.group_id, []).append((e, o))

    has_txtr = any(e.type_id == TYPE_TXTR for e in reader.entries)
    out: "list[CatalogEntry]" = []
    for group, members in objds.items():
        tga = (TYPE_TGA, group) in {(e.type_id, e.group_id) for e in reader.entries}
        model = ""
        m = reader.by_key.get((s2object.TYPE_STR, group, MODEL_STR))
        if m is not None:
            try:
                model = next((v for v in _english(s2object.parse_str(reader.read(m))) if v), "")
            except (ValueError, struct.error):
                model = ""
        # Tiles of one object share a non-zero master id (word 10); the master
        # has sub index 0xFFFF and each tile its own index. A group whose
        # members all have master id 0 is a family of separate objects.
        for e, o in members:
            master_id, sub = o.words[WORD_MASTER_ID], o.words[WORD_SUB_INDEX]
            if master_id and sub != TILE_MASTER:
                continue        # a tile; its master represents the object
            tiles = sum(1 for _, m in members if master_id and m.words[WORD_MASTER_ID] == master_id
                        and m.words[WORD_SUB_INDEX] != TILE_MASTER) or 1
            if o.words[WORD_OBJ_TYPE] != OBJ_TYPE_BUYABLE or not o.function_sort_flags:
                continue
            name, description = o.name, ""
            c = reader.by_key.get((s2object.TYPE_CTSS, group, o.ctss_id))
            if c is not None:
                try:
                    strs = _english(s2object.parse_str(reader.read(c)))
                    if strs and strs[0].strip():
                        name = strs[0].strip()
                    if len(strs) > 1:
                        description = strs[1].strip()
                except (ValueError, struct.error):
                    pass
            if source == "game":
                swatch = "tga" if tga else "model" if model else ""
            else:
                swatch = "tga" if tga else "package" if has_txtr else "model" if model else ""
            out.append(CatalogEntry(
                guid=o.guid, group=group, source=source, name=name,
                description=description, price=o.price,
                room_flags=o.room_sort_flags, function_flags=o.function_sort_flags,
                tiles=tiles, model=model, swatch=swatch, filename=o.filename))
    out.sort(key=lambda c: (c.name.lower(), c.guid))
    return out


def _stamp(path: Path) -> "list[int]":
    st = path.stat()
    return [st.st_size, int(st.st_mtime)]


def catalog(game_root: "Path | None" = None, *, refresh: bool = False,
            objects_package: "Path | None" = None,
            downloads: "Path | None" = None,
            progress=None) -> dict:
    """The whole catalog: the game's objects plus every package in Downloads.

    Cached per source file (size and mtime) under CACHE_DIR, so only a
    changed or new package is rescanned. `progress(done, total, note)` is
    called per package when given.
    """
    objects_package = objects_package or OBJECTS_PACKAGE
    if downloads is None and game_root is not None:
        downloads = Path(game_root) / "Downloads"
    sources: "list[tuple[str, Path]]" = []
    if objects_package.is_file():
        sources.append(("game", objects_package))
    if downloads and downloads.is_dir():
        for p in sorted(downloads.rglob("*.package")):
            sources.append((str(p), p))

    cache_file = CACHE_DIR / "catalog.json"
    cache: dict = {}
    if not refresh and cache_file.is_file():
        try:
            cache = json.loads(cache_file.read_text())
        except (OSError, ValueError):
            cache = {}
    fresh: dict = {}
    entries: "list[CatalogEntry]" = []
    scanned = 0
    for n, (source, path) in enumerate(sources):
        if progress:
            progress(n, len(sources), path.name)
        stamp = _stamp(path)
        hit = cache.get(source)
        if hit and hit.get("stamp") == stamp:
            rows = hit["entries"]
        else:
            try:
                rows = [asdict(c) for c in scan_package(path, source)]
            except (OSError, ValueError, struct.error):
                rows = []
            scanned += 1
        fresh[source] = {"stamp": stamp, "entries": rows}
        entries.extend(CatalogEntry(**row) for row in rows)
    if progress:
        progress(len(sources), len(sources), "")
    if scanned or set(fresh) != set(cache):
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(fresh))
        except OSError:
            pass
    return {"sources": [s for s, _ in sources], "entries": entries,
            "scanned": scanned, "cached": len(sources) - scanned}


# ---------------------------------------------------------------------------
# Swatches
# ---------------------------------------------------------------------------

_SIMS3D: "dict | None" = None


def sims3d_index(sims3d_dir: "Path | None" = None) -> "dict":
    """(type, group, instance) -> (PackageReader, entry) across Sims3D/Objects*.
    Index-only; built once per process."""
    global _SIMS3D
    if _SIMS3D is None:
        idx: dict = {}
        d = sims3d_dir or SIMS3D_DIR
        if d.is_dir():
            for p in sorted(d.glob("Objects*.package")):
                try:
                    r = PackageReader(p)
                except (OSError, ValueError, struct.error):
                    continue
                for e in r.entries:
                    idx[(e.type_id, e.group_id, e.instance)] = (r, e)
        _SIMS3D = idx
    return _SIMS3D


def scenegraph_instance(name: str) -> int:
    return 0xFF000000 | crc24(name.lower())


def _sg_read(type_id: int, name: str, group: int = SCENEGRAPH_GROUP) -> "bytes | None":
    hit = sims3d_index().get((type_id, group, scenegraph_instance(name)))
    if hit is None:
        return None
    reader, entry = hit
    return reader.read(entry)


def pascal_strings(data: bytes, *, min_len: int = 3) -> "list[str]":
    """Every length-prefixed printable string in an RCOL payload, in order.
    Good enough to pull subset and material names out of a SHPE and property
    names and values out of a TXMT without a full parser for either."""
    out = []
    pos = 0
    n = len(data)
    while pos < n:
        length = data[pos]
        end = pos + 1 + length
        if min_len <= length <= 128 and end <= n and all(0x20 <= c < 0x7F for c in data[pos + 1:end]):
            out.append(data[pos + 1:end].decode("latin-1"))
            pos = end
        else:
            pos += 1
    return out


def _rcol_links(data: bytes) -> "list[tuple[int, int, int, int]]":
    """The (group, instance, instance_hi, type) links an RCOL header names."""
    magic, = struct.unpack_from("<I", data, 0)
    if magic == s2texture.RCOL_MAGIC:
        count, = struct.unpack_from("<I", data, 4)
        pos = 8
    else:
        count, pos = magic, 4
    if count > 0xFFFF:
        return []
    return [struct.unpack_from("<IIII", data, pos + i * 16) for i in range(count)]


def _texture_png(txtr: bytes, lifo_lookup=None) -> "bytes | None":
    tex = s2texture.parse_image_data(txtr)
    level = next((l for l in tex.levels if l.data is not None
                  and max(l.width, l.height) <= SWATCH_MAX_SIDE), None)
    if level is None and lifo_lookup is not None:
        for l in tex.levels:
            if l.lifo and max(l.width, l.height) <= SWATCH_MAX_SIDE:
                raw = lifo_lookup(l.lifo)
                if raw:
                    try:
                        _, src = s2texture.parse_level_info(raw)
                    except (ValueError, struct.error, IndexError):
                        continue
                    if src.width == l.width:
                        l.data = src.data
                        level = l
                        break
    if level is None:
        return None
    rgba = s2texture.decode(level, tex.format)
    return s2texture.png_bytes(level.width, level.height, rgba)


def tga_to_png(data: bytes) -> "bytes | None":
    """An uncompressed true-colour TGA (the game's 42x42 catalog icons) as PNG."""
    if len(data) < 18:
        return None
    id_len, cmap, kind = data[0], data[1], data[2]
    width, height, bpp, desc = struct.unpack_from("<HHBB", data, 12)
    if kind != 2 or cmap != 0 or bpp not in (24, 32):
        return None
    bytes_pp = bpp // 8
    start = 18 + id_len
    if start + width * height * bytes_pp > len(data):
        return None
    top_down = bool(desc & 0x20)
    rgba = bytearray()
    rows = range(height) if top_down else range(height - 1, -1, -1)
    for y in rows:
        row = data[start + y * width * bytes_pp:start + (y + 1) * width * bytes_pp]
        for x in range(width):
            px = row[x * bytes_pp:(x + 1) * bytes_pp]
            rgba += bytes((px[2], px[1], px[0], px[3] if bytes_pp == 4 else 255))
    return s2texture.png_bytes(width, height, bytes(rgba))


def model_swatch(model: str) -> "bytes | None":
    """The base texture of a game model, through the hash chain."""
    cres = _sg_read(TYPE_CRES, model + "_cres")
    if cres is None:
        return None
    shapes: "list[bytes]" = []
    for group, inst, _hi, tid in _rcol_links(cres):
        if tid != TYPE_SHPE:
            continue
        hit = sims3d_index().get((tid, group, inst))
        if hit:
            shapes.append(hit[0].read(hit[1]))
    candidates: "list[str]" = []
    for shpe in shapes:
        for s in pascal_strings(shpe):
            low = s.lower()
            if low.endswith(("_shpe", "_gmnd", "_cres")) or s.startswith(("c", "##")) and s[1:2].isupper():
                continue
            if any(k in low for k in _SWATCH_SKIP):
                continue
            if s not in candidates:
                candidates.append(s)
    for material in candidates:
        txmt = _sg_read(TYPE_TXMT, material + "_txmt")
        if txmt is None:
            continue
        strs = pascal_strings(txmt)
        try:
            base = strs[strs.index("stdMatBaseTextureName") + 1]
        except (ValueError, IndexError):
            continue
        txtr = _sg_read(TYPE_TXTR, base + "_txtr")
        if txtr is None:
            continue
        png = _texture_png(txtr, lambda n: _sg_read(TYPE_LIFO, n))
        if png:
            return png
    return None


def package_swatch(path: Path, group: int, *, textures: bool = True) -> "bytes | None":
    """A package's own look: the group's TGA if it has one, else (with
    `textures`) the largest texture in the package that is not a shadow or
    normal map. `textures` is off for objects.package, whose textures belong
    to other objects and whose 52,000 resources are not worth inflating."""
    reader = PackageReader(path)
    tga = next((e for e in reader.entries if e.type_id == TYPE_TGA and e.group_id == group), None)
    if tga is not None:
        png = tga_to_png(reader.read(tga))
        if png:
            return png
    if not textures or not any(e.type_id == TYPE_TXTR for e in reader.entries):
        return None
    resources = s2writer.read_all_resources(path)
    best = None
    for r in resources:
        if r.type_id != TYPE_TXTR:
            continue
        try:
            tex = s2texture.parse_image_data(r.data)
        except (ValueError, struct.error, IndexError):
            continue
        low = tex.name.lower()
        if any(k in low for k in _SWATCH_SKIP):
            continue
        if best is None or tex.width * tex.height > best[0]:
            best = (tex.width * tex.height, r)
    if best is None:
        return None
    try:
        tex = s2texture.load_texture(resources, best[1])
    except (ValueError, struct.error, IndexError):
        return None
    level = next((l for l in tex.levels if l.data is not None
                  and max(l.width, l.height) <= SWATCH_MAX_SIDE), None)
    if level is None:
        return None
    return s2texture.png_bytes(level.width, level.height, s2texture.decode(level, tex.format))


def swatch(entry: CatalogEntry, *, objects_package: "Path | None" = None,
           use_cache: bool = True) -> "bytes | None":
    """The PNG swatch for a catalog entry, cached under CACHE_DIR/swatches."""
    cache_dir = CACHE_DIR / "swatches"
    key = f"{Path(entry.source).stem if entry.source != 'game' else 'game'}-{entry.guid:08X}.png"
    cached = cache_dir / key
    if use_cache and cached.is_file():
        try:
            return cached.read_bytes()
        except OSError:
            pass
    png = None
    path = (objects_package or OBJECTS_PACKAGE) if entry.source == "game" else Path(entry.source)
    if path.is_file():
        try:
            png = package_swatch(path, entry.group, textures=entry.source != "game")
        except (OSError, ValueError, struct.error, IndexError):
            png = None
    if png is None and entry.model:
        try:
            png = model_swatch(entry.model)
        except (OSError, ValueError, struct.error, IndexError):
            png = None
    if png and use_cache:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(png)
        except OSError:
            pass
    return png


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, help="Sims 2 user folder (for Downloads)")
    ap.add_argument("--objects", type=Path, help="objects.package to scan instead of the game's")
    ap.add_argument("--downloads", type=Path, help="folder of custom content to scan")
    ap.add_argument("--refresh", action="store_true", help="ignore the cache")
    ap.add_argument("--swatch", nargs=2, metavar=("GUID", "OUT.png"), help="write one object's swatch")
    ap.add_argument("--search", help="only names containing this")
    args = ap.parse_args(argv)
    root = args.root
    if root is None and args.downloads is None:
        root = next((c for c in s2doctor.ROOT_CANDIDATES if (c / "Neighborhoods").is_dir()), None)
    cat = catalog(root, refresh=args.refresh, objects_package=args.objects, downloads=args.downloads)
    entries = cat["entries"]
    if args.swatch:
        guid = int(args.swatch[0], 0)
        entry = next((c for c in entries if c.guid == guid), None)
        if entry is None:
            print(f"no object 0x{guid:08X} in the catalog")
            return 1
        png = swatch(entry, objects_package=args.objects, use_cache=not args.refresh)
        if png is None:
            print(f"no swatch for {entry.name} (swatch hint: {entry.swatch or 'none'})")
            return 1
        Path(args.swatch[1]).write_bytes(png)
        print(f"wrote {args.swatch[1]} for {entry.name}")
        return 0
    q = (args.search or "").lower()
    for c in entries:
        if q and q not in c.name.lower() and q not in c.filename.lower():
            continue
        tiles = f"  [{c.tiles} tiles]" if c.tiles > 1 else ""
        src = "game" if c.source == "game" else Path(c.source).name
        print(f"0x{c.guid:08X}  §{c.price:<6} {c.name:40.40s} {', '.join(c.functions):24.24s} "
              f"{', '.join(c.rooms):30.30s} {c.swatch or '-':8}{tiles}  ({src})")
    print(f"{len(entries)} objects from {len(cat['sources'])} package(s); "
          f"{cat['scanned']} scanned, {cat['cached']} from cache")
    return 0


if __name__ == "__main__":
    sys.exit(main())
