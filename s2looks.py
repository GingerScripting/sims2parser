#!/usr/bin/env python3
"""s2looks.py — the Looks of an object project: recolours as the game wants them.

A look is a colour option. In the game that is a Material Override (MMAT):
"for object GUID g, model m, subset s, draw with material t", where t is a
material definition (TXMT) naming a texture (TXTR). This module turns what
the user chose — a picture for a subset, or a tint over the original — into
exactly those three kinds of resource, the way the recolour packages in
sample-packages/ and the game's own Catalog/Materials do it:

    TXTR  group 0x1C050000, instance 0xFF000000|crc24(name), hi crc32(name)
    TXMT  same group and hashing; a copy of the game's material for that
          subset with stdMatBaseTextureName pointed at the new texture
    MMAT  group 0xFFFFFFFF, instance 0x6000 and up; name and texture
          references carry the "##0x1C050000!" prefix that tells the game
          which group to look in

Which subsets can be recoloured is the model's decision, not ours: the GMND
lists them in its tsDesignModeEnabled extension and the game ignores an
override on any other subset. Objects with states (a counter's clean and
dirty tops) have one material per state in one MMAT family; a look follows
the same pattern, cloning each state's material.

Everything here is deterministic. Names derive from the project's uuid, the
look's id and the subset — never from the look's display name — so a renamed
look keeps the resources the game has already placed on lots.
"""

# Annotations stay strings so the module imports under the system
# python3 (3.9), which the app gets when launched from Finder.
from __future__ import annotations

import hashlib
import struct
import sys
import uuid as _uuid
from dataclasses import dataclass, field
from pathlib import Path

import s2catalog
import s2object
import s2texture
from s2writer import Resource

TYPE_TXTR = s2texture.TYPE_TXTR
TYPE_TXMT = s2object.TYPE_TXMT
TYPE_MMAT = s2object.TYPE_MMAT
TYPE_CRES = s2catalog.TYPE_CRES
TYPE_SHPE = s2catalog.TYPE_SHPE
TYPE_GMND = s2catalog.TYPE_GMND
TYPE_LIFO = s2texture.TYPE_LIFO

PRIVATE_GROUP = 0xFFFFFFFF
MMAT_FIRST_INSTANCE = 0x6000          # a look's overrides
GAME_FIRST_INSTANCE = 0x7000          # the game's own colours, carried over
PREFIX = s2texture.RECOLOUR_PREFIX
ENCODER_VERSION = 1                   # bump to invalidate every cached texture
PREVIEW_SIDE = 256

# Subsets that are never a look: shadows and the like.
_SKIP_SUBSETS = ("shadow", "_alpha", "reflect", "cube")


# ---------------------------------------------------------------------------
# What the user chose
# ---------------------------------------------------------------------------

@dataclass
class TintSource:
    color: "tuple[int, int, int]"
    strength: float = 1.0
    lightness: float = 0.0

    def to_json(self) -> dict:
        r, g, b = self.color
        return {"kind": "tint", "color": f"#{r:02X}{g:02X}{b:02X}",
                "strength": round(float(self.strength), 4),
                "lightness": round(float(self.lightness), 4)}

    def describe(self) -> str:
        return f"tint {self.to_json()['color']} {self.strength:.4f} {self.lightness:.4f}"


@dataclass
class PictureSource:
    file: str                       # path inside the bundle: looks/<id>/<subset>.png
    sha1: str = ""                  # of the PNG bytes; the cache key
    png: "bytes | None" = field(default=None, repr=False)   # held until saved

    def to_json(self) -> dict:
        return {"kind": "picture", "file": self.file, "sha1": self.sha1}

    def describe(self) -> str:
        return f"picture {self.sha1}"


def source_from_json(d: dict) -> "TintSource | PictureSource":
    kind = str(d.get("kind", ""))
    if kind == "tint":
        return TintSource(parse_color(str(d.get("color", "#808080"))),
                          float(d.get("strength", 1.0)), float(d.get("lightness", 0.0)))
    if kind == "picture":
        return PictureSource(str(d.get("file", "")), str(d.get("sha1", "")))
    raise ValueError(f"unknown look source {kind!r}")


def parse_color(text: str) -> "tuple[int, int, int]":
    t = text.strip().lstrip("#")
    if len(t) != 6:
        raise ValueError(f"colour {text!r} is not #RRGGBB")
    return int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16)


@dataclass
class Look:
    id: str
    name: str
    default: bool = False
    subsets: "dict[str, TintSource | PictureSource]" = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"id": self.id, "name": self.name, "default": self.default,
                "subsets": {k: v.to_json() for k, v in sorted(self.subsets.items())}}

    @classmethod
    def from_json(cls, d: dict) -> "Look":
        subsets = {}
        for k, v in (d.get("subsets") or {}).items():
            if v:
                subsets[str(k)] = source_from_json(v)
        return cls(id=str(d.get("id") or new_look_id()), name=str(d.get("name", "")),
                   default=bool(d.get("default", False)), subsets=subsets)


@dataclass
class Looks:
    items: "list[Look]" = field(default_factory=list)
    keep_game_options: bool = True

    def to_json(self) -> dict:
        return {"keep_game_options": self.keep_game_options,
                "items": [l.to_json() for l in self.items]}

    @classmethod
    def from_json(cls, d: "dict | None") -> "Looks":
        d = d or {}
        return cls(items=[Look.from_json(x) for x in d.get("items", [])],
                   keep_game_options=bool(d.get("keep_game_options", True)))

    def get(self, look_id: str) -> "Look | None":
        for l in self.items:
            if l.id == look_id:
                return l
        return None


def new_look_id() -> str:
    return _uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# What the model allows: the inventory
# ---------------------------------------------------------------------------

@dataclass
class State:
    """One material state of a subset (clean, dirty, lit...)."""
    material: str                   # the game's material name, no suffix
    txmt: bytes                     # its TXMT, the template a look clones
    default: bool = True            # the state the object shows at rest
    flags: int = 0                  # materialStateFlags of the game's MMAT
    state_index: int = -1           # objectStateIndex of the game's MMAT


@dataclass
class Subset:
    name: str
    material: str
    texture: str = ""               # base texture name, no prefix, no _txtr
    recolourable: bool = False
    width: int = 0
    height: int = 0
    format: int = 0
    states: "list[State]" = field(default_factory=list)
    guids: "list[int]" = field(default_factory=list)   # original GUIDs the game dresses here

    def to_json(self) -> dict:
        return {"name": self.name, "material": self.material, "texture": self.texture,
                "recolourable": self.recolourable, "width": self.width,
                "height": self.height, "format": self.format,
                "states": [s.material for s in self.states], "guids": list(self.guids)}


@dataclass
class Inventory:
    model: str
    subsets: "list[Subset]" = field(default_factory=list)
    game_options: "list[s2object.Mmat]" = field(default_factory=list)
    warnings: "list[str]" = field(default_factory=list)

    def subset(self, name: str) -> "Subset | None":
        low = name.lower()
        for s in self.subsets:
            if s.name.lower() == low:
                return s
        return None

    @property
    def recolourable(self) -> "list[Subset]":
        return [s for s in self.subsets if s.recolourable]

    def to_json(self) -> dict:
        return {"model": self.model,
                "subsets": [s.to_json() for s in self.subsets],
                "game_options": len(self.game_options),
                "warnings": list(self.warnings)}


def model_name(resources: "list[Resource]", group: "int | None" = None) -> str:
    """The model an object draws with: the first English entry of its
    STR# 0x85."""
    for r in resources:
        if r.type_id != s2object.TYPE_STR or r.instance_id != s2catalog.MODEL_STR:
            continue
        if group is not None and r.group_id != group:
            continue
        try:
            table = s2object.parse_str(r.data)
        except (ValueError, struct.error):
            continue
        for e in table.entries:
            if e.lang == 1 and e.value.strip():
                return e.value.strip()
    return ""


def model_guids(model: str) -> "list[int]":
    """Every object the game dresses with this model, from its own
    recolours: a counter and its island twin share one model, and a colour
    option made for one should reach the other, as the game's do."""
    want = model.lower() + "_cres"
    out = []
    for guid, mmats in s2catalog.maxis_mmats().items():
        if any(m.model_name.lower() == want for m in mmats):
            out.append(guid)
    return sorted(out)


def strip_prefix(name: str) -> str:
    return name.split("!", 1)[1] if name.startswith("##") and "!" in name else name


def inventory(model: str, base_guids: "list[int]", local: "list[Resource] | None" = None
              ) -> Inventory:
    """Everything a Looks page needs to know about a model: its subsets,
    which are recolourable, the game's materials for each, and the game's
    own colour options for the base object(s)."""
    inv = Inventory(model)
    if not model:
        inv.warnings.append("the object names no model, so it has no looks")
        return inv
    cres = s2catalog.scene_find(TYPE_CRES, model + "_cres", local)
    if cres is None:
        inv.warnings.append(f"the model {model!r} is not in the game's files")
        return inv
    shapes: "list[bytes]" = []
    for group, inst, _hi, tid in s2catalog._rcol_links(cres):
        if tid != TYPE_SHPE:
            continue
        data = None
        if local:
            for r in local:
                if r.type_id == TYPE_SHPE and r.instance_id == inst:
                    data = r.data
                    break
        if data is None:
            hit = s2catalog.sims3d_index().get((tid, group, inst))
            if hit:
                data = hit[0].read(hit[1])
        if data is not None:
            shapes.append(data)
    if not shapes:
        inv.warnings.append(f"the model {model!r} has no shapes")
        return inv

    design: "set[str]" = set()
    pairs: "list[tuple[str, str]]" = []
    for shpe in shapes:
        for name in s2catalog.pascal_strings(shpe):
            if name.lower().endswith("_gmnd"):
                gmnd = s2catalog.scene_find(TYPE_GMND, name, local)
                if gmnd:
                    design.update(s.lower() for s in s2catalog.gmnd_design_subsets(gmnd))
        for subset, material in s2catalog.shpe_subsets(shpe):
            if any(subset.lower() == p[0].lower() for p in pairs):
                continue
            pairs.append((subset, material))

    options: "list[s2object.Mmat]" = []
    for guid in base_guids:
        options.extend(s2catalog.maxis_mmats().get(guid, []))
    inv.game_options = options

    for subset, material in pairs:
        low = subset.lower()
        if any(k in low for k in _SKIP_SUBSETS):
            continue
        s = Subset(subset, material, recolourable=low in design)
        # The game's colour families for this subset decide the states.
        mine = [m for m in options if m.subset_name.lower() == low]
        family: "list[s2object.Mmat]" = []
        if mine:
            by_family: "dict[str, list[s2object.Mmat]]" = {}
            for m in mine:
                by_family.setdefault(m.family, []).append(m)
            for members in by_family.values():
                if any(strip_prefix(m.name).lower() == material.lower() for m in members):
                    family = members
                    break
            if not family:
                for members in by_family.values():
                    if any(m.default_material for m in members):
                        family = members
                        break
            s.guids = sorted({m.object_guid for m in mine})
        if family:
            family = sorted(family, key=lambda m: (not m.default_material,
                                                   strip_prefix(m.name).lower() != material.lower()))
            for m in family:
                mat = strip_prefix(m.name)
                txmt = s2catalog.scene_find(TYPE_TXMT, mat + "_txmt", local)
                if txmt is None:
                    continue
                s.states.append(State(mat, txmt, default=bool(m.default_material),
                                      flags=m.material_state_flags,
                                      state_index=m.object_state_index))
        if not s.states:
            txmt = s2catalog.scene_find(TYPE_TXMT, material + "_txmt", local)
            if txmt is not None:
                s.states.append(State(material, txmt))
        if not s.guids:
            s.guids = [base_guids[0]] if base_guids else []
        if s.states:
            try:
                t = s2object.parse_txmt(s.states[0].txmt)
                s.texture = strip_prefix(t.base_texture)
            except ValueError:
                pass
            if s.texture:
                txtr = s2catalog.scene_find(TYPE_TXTR, s.texture + "_txtr", local)
                if txtr is not None:
                    try:
                        tex = s2texture.parse_image_data(txtr)
                        s.width, s.height, s.format = tex.width, tex.height, tex.format
                    except ValueError:
                        pass
        if s.recolourable and not (s.states and s.texture and s.width):
            s.recolourable = False
            inv.warnings.append(f"{subset}: the game marks it recolourable but its texture was not found")
        inv.subsets.append(s)
    inv.subsets.sort(key=lambda x: (not x.recolourable, x.name.lower()))
    return inv


_BASE_CACHE: "dict[str, tuple[int, int, bytes]]" = {}


def base_rgba(subset: Subset, local: "list[Resource] | None" = None,
              max_side: "int | None" = None) -> "tuple[int, int, bytearray]":
    """The subset's original texture as RGBA, full size or the largest mip
    level within `max_side`. LIFO-held levels are fetched from the game."""
    key = f"{subset.texture}|{max_side}"
    hit = _BASE_CACHE.get(key)
    if hit:
        return hit[0], hit[1], bytearray(hit[2])
    txtr = s2catalog.scene_find(TYPE_TXTR, subset.texture + "_txtr", local)
    if txtr is None:
        raise ValueError(f"texture {subset.texture!r} not found")
    tex = s2texture.parse_image_data(txtr)
    chosen = None
    for level in tex.levels:
        if max_side is not None and max(level.width, level.height) > max_side:
            continue
        if level.data is None and level.lifo:
            lifo = s2catalog.scene_find(TYPE_LIFO, level.lifo, local)
            if lifo is None:
                continue
            _n, resolved = s2texture.parse_level_info(lifo)
            if resolved.width == level.width:
                level.data = resolved.data
        if level.data is not None:
            chosen = level
            break
    if chosen is None:
        raise ValueError(f"no usable level in {subset.texture!r}")
    rgba = s2texture.decode(chosen, tex.format)
    _BASE_CACHE[key] = (chosen.width, chosen.height, bytes(rgba))
    return chosen.width, chosen.height, rgba


def base_texture(subset: Subset, local: "list[Resource] | None" = None) -> "s2texture.Texture":
    txtr = s2catalog.scene_find(TYPE_TXTR, subset.texture + "_txtr", local)
    if txtr is None:
        raise ValueError(f"texture {subset.texture!r} not found")
    return s2texture.parse_image_data(txtr)


# ---------------------------------------------------------------------------
# Pixels for a source
# ---------------------------------------------------------------------------

def resample(width: int, height: int, rgba: bytes, new_w: int, new_h: int) -> bytearray:
    """Nearest-neighbour resize, for a picture that is not the texture's size."""
    if (width, height) == (new_w, new_h):
        return bytearray(rgba)
    out = bytearray(new_w * new_h * 4)
    for y in range(new_h):
        sy = y * height // new_h
        for x in range(new_w):
            sx = x * width // new_w
            s = (sy * width + sx) * 4
            d = (y * new_w + x) * 4
            out[d:d + 4] = rgba[s:s + 4]
    return out


def source_rgba(subset: Subset, source: "TintSource | PictureSource",
                read_file, local: "list[Resource] | None" = None,
                max_side: "int | None" = None) -> "tuple[int, int, bytearray]":
    """The pixels a source yields for a subset, at full size or shrunk to
    `max_side` for previews. `read_file(path)` returns a picture's bytes."""
    if isinstance(source, TintSource):
        w, h, rgba = base_rgba(subset, local, max_side)
        return w, h, s2texture.tint(rgba, source.color, source.strength, source.lightness)
    png = source.png if source.png is not None else read_file(source.file)
    w, h, rgba = s2texture.read_png(png)
    tw, th = subset.width or w, subset.height or h
    if max_side is not None:
        while max(tw, th) > max_side and tw > 1 and th > 1:
            tw, th = max(1, tw // 2), max(1, th // 2)
    return tw, th, resample(w, h, rgba, tw, th)


def preview_png(subset: Subset, source: "TintSource | PictureSource", read_file,
                local: "list[Resource] | None" = None, side: int = PREVIEW_SIDE) -> bytes:
    w, h, rgba = source_rgba(subset, source, read_file, local, max_side=side)
    return s2texture.png_bytes(w, h, rgba)


def base_png(subset: Subset, local: "list[Resource] | None" = None,
             side: int = PREVIEW_SIDE) -> bytes:
    w, h, rgba = base_rgba(subset, local, side)
    return s2texture.png_bytes(w, h, rgba)


# ---------------------------------------------------------------------------
# Building the resources
# ---------------------------------------------------------------------------

def _stem(model: str, project_uuid: str, look: Look) -> str:
    return f"{model.lower()}-[ss-{project_uuid[:8]}-{look.id[:8]}]"


def family_id(project_uuid: str, look_id: str, subset: str) -> str:
    return str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"simstudio:{project_uuid}/{look_id}/{subset.lower()}"))


def _cache_key(texture_name: str, subset: Subset, source) -> str:
    h = hashlib.sha1()
    h.update(f"{ENCODER_VERSION}|{texture_name}|{subset.texture}|{subset.width}x{subset.height}|{source.describe()}".encode())
    return h.hexdigest()


def render(project_uuid: str, model: str, looks: Looks, inv: Inventory,
           guid_map: "dict[int, int]", *, read_file, cache_dir: "Path | None" = None,
           local: "list[Resource] | None" = None, progress=None) -> "list[Resource]":
    """Every resource the looks add to the package, in a fixed order:
    per look, per subset, the texture, then a material per state, then an
    override per (object, state); then the game's own colour options if
    kept. `guid_map` maps the base object's GUIDs to the ones being built
    (identity for a plain recolour)."""
    out: "list[Resource]" = []
    mmat_n = 0
    model_cres = model.lower() + "_cres"
    default_subsets: "set[str]" = set()
    jobs = [(look, name, src) for look in looks.items
            for name, src in sorted(look.subsets.items(), key=lambda kv: kv[0].lower())]
    total = len(jobs)
    for done, (look, name, src) in enumerate(jobs):
        subset = inv.subset(name)
        if subset is None or not subset.recolourable:
            continue
        stem = _stem(model, project_uuid, look)
        texture_name = f"{stem}_{subset.name.lower()}"
        if progress:
            progress(done, total, texture_name)
        key = _cache_key(texture_name, subset, src)
        cached = cache_dir / f"{key}.txtr" if cache_dir else None
        txtr_bytes: "bytes | None" = None
        if cached is not None and cached.is_file():
            txtr_bytes = cached.read_bytes()
        if txtr_bytes is None:
            w, h, rgba = source_rgba(subset, src, read_file, local)
            template = None
            try:
                template = base_texture(subset, local)
            except ValueError:
                pass
            tex = s2texture.make_texture(texture_name + "_txtr", w, h, rgba, template=template)
            txtr_bytes = s2texture.build_image_data(tex)
            if cached is not None:
                cached.parent.mkdir(parents=True, exist_ok=True)
                tmp = cached.with_name(cached.name + ".tmp")
                tmp.write_bytes(txtr_bytes)
                tmp.replace(cached)
        t, g, i, hi = s2texture.scenegraph_tgi(TYPE_TXTR, texture_name + "_txtr")
        out.append(Resource(t, g, i, txtr_bytes, hi))

        family = family_id(project_uuid, look.id, subset.name)
        if look.default:
            default_subsets.add(subset.name.lower())
        for n, state in enumerate(subset.states):
            material = texture_name if n == 0 else f"{texture_name}_{n}"
            txmt = s2object.parse_txmt(state.txmt)
            old_base = txmt.base_texture
            txmt.filename = material + "_txmt"
            txmt.material_name = material
            txmt.base_texture = PREFIX + texture_name
            txmt.textures = [PREFIX + texture_name if strip_prefix(x) == strip_prefix(old_base) else x
                             for x in txmt.textures]
            t, g, i, hi = s2texture.scenegraph_tgi(TYPE_TXMT, material + "_txmt")
            out.append(Resource(t, g, i, s2object.build_txmt(txmt), hi))
            for orig in subset.guids:
                guid = guid_map.get(orig)
                if guid is None:
                    continue
                m = s2object.new_mmat(
                    name=PREFIX + material, model_name=model_cres,
                    subset_name=subset.name, object_guid=guid, family=family,
                    default=bool(look.default and state.default),
                    material_state_flags=state.flags, object_state_index=state.state_index)
                out.append(Resource(TYPE_MMAT, PRIVATE_GROUP, MMAT_FIRST_INSTANCE + mmat_n,
                                    s2object.build_mmat(m)))
                mmat_n += 1

    if looks.keep_game_options:
        n = 0
        for m in inv.game_options:
            guid = guid_map.get(m.object_guid)
            if guid is None:
                continue
            if guid == m.object_guid:
                continue            # a plain recolour: the game already has these
            copy = s2object.Mmat([s2object.MmatProp(e.key, e.code, e.value) for e in m.entries],
                                 m.version, xml=False)
            copy.object_guid = guid
            copy.family = str(_uuid.uuid5(_uuid.NAMESPACE_URL,
                                          f"simstudio:{project_uuid}/game/{m.family}"))
            if copy.default_material and copy.subset_name.lower() in default_subsets:
                copy.default_material = False
            out.append(Resource(TYPE_MMAT, PRIVATE_GROUP, GAME_FIRST_INSTANCE + n,
                                s2object.build_mmat(copy)))
            n += 1
    if progress and total:
        progress(total, total, "")
    return out


def generated_tgis(resources: "list[Resource]") -> "set[tuple[int, int, int, int]]":
    """The TGIs `render` produced, recognisable by group and type."""
    out = set()
    for r in resources:
        if r.type_id in (TYPE_TXTR, TYPE_TXMT) and r.group_id == s2texture.RECOLOUR_GROUP:
            out.add(r.tgi())
        elif r.type_id == TYPE_MMAT and r.group_id == PRIVATE_GROUP and r.instance_id >= MMAT_FIRST_INSTANCE:
            out.add(r.tgi())
    return out


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _selftest(sample_dir: str) -> int:
    import s2writer
    failures: "list[str]" = []
    notes: "list[str]" = []
    samples = sorted(Path(sample_dir).rglob("*.package"))

    # 1. TXTR builder is byte-exact on every donor texture, and on the game's.
    n = 0
    for p in samples:
        for r in s2writer.read_all_resources(p):
            if r.type_id != TYPE_TXTR:
                continue
            try:
                t = s2texture.parse_image_data(r.data)
            except ValueError as exc:
                notes.append(f"{p.name}: declined TXTR 0x{r.instance_id:08X} ({exc})")
                continue
            if s2texture.build_image_data(t) != r.data:
                failures.append(f"{p.name}: TXTR 0x{r.instance_id:08X} does not rebuild")
            n += 1
    game = 0
    for k, (rdr, e) in list(s2catalog.sims3d_index().items()):
        if k[0] != TYPE_TXTR:
            continue
        game += 1
        if game % 25:
            continue
        d = rdr.read(e)
        try:
            t = s2texture.parse_image_data(d)
        except ValueError:
            continue
        if s2texture.build_image_data(t) != d:
            failures.append(f"game TXTR 0x{k[2]:08X} does not rebuild")
    print(f"1. {n} donor and {game // 25} game textures rebuild byte for byte")

    # 2. PNG reader inverts the writer; 3. DXT round trip; 7. tint.
    w, h = 37, 21
    rgba = bytes((x * 7 + y * 3) & 0xFF for y in range(h) for x in range(w) for _ in range(4))
    rw, rh, back = s2texture.read_png(s2texture.png_bytes(w, h, rgba))
    if (rw, rh, bytes(back)) != (w, h, rgba):
        failures.append("read_png(png_bytes(x)) != x")
    flat = bytes([200, 100, 48, 255]) * 64
    dec = s2texture.decode(s2texture.MipLevel(8, 8, data=s2texture.encode_dxt1(8, 8, flat)), 4)
    if bytes(dec) != bytes([206, 101, 49, 255]) * 64:
        failures.append("DXT1 of a flat block is not exact")
    # A one-axis gradient lies on a line, which is what a DXT block can hold;
    # a two-axis one cannot be represented by any encoder, so it is no test.
    grad = bytes(v for y in range(16) for x in range(16) for v in (x * 16, 200 - x * 8, 128 + (x & 3), 255))
    dec = s2texture.decode(s2texture.MipLevel(16, 16, data=s2texture.encode_dxt1(16, 16, grad)), 4)
    err = sum(abs(dec[i] - grad[i]) for i in range(len(grad)) if i % 4 != 3) / (len(grad) * 3 / 4)
    if err > 6:
        failures.append(f"DXT1 gradient error too high ({err:.1f})")
    alpha = bytes(v for y in range(8) for x in range(8) for v in (x * 30, 90, 200, (x + y) * 16))
    dec = s2texture.decode(s2texture.MipLevel(8, 8, data=s2texture.encode_dxt5(8, 8, alpha)), 8)
    aerr = max(abs(dec[i] - alpha[i]) for i in range(3, len(alpha), 4))
    if aerr > 20:
        failures.append(f"DXT5 alpha error too high ({aerr})")
    if bytes(s2texture.tint(rgba, (10, 20, 30), 0.0)) != rgba:
        failures.append("tint at strength 0 changes pixels")
    if bytes(s2texture.tint(rgba, (10, 20, 30), 0.6, 0.2)) != bytes(s2texture.tint(rgba, (10, 20, 30), 0.6, 0.2)):
        failures.append("tint is not deterministic")
    print(f"2. PNG round trip, DXT1 flat exact, gradient error {err:.2f}, DXT5 alpha error {aerr}, tint ok")

    # 4. Donor TXMT/TXTR instances follow the name hash.
    hits = misses = 0
    for p in samples:
        for r in s2writer.read_all_resources(p):
            if r.type_id == TYPE_TXMT and r.group_id == s2texture.RECOLOUR_GROUP:
                try:
                    name = s2object.parse_txmt(r.data).filename
                except ValueError:
                    continue
            elif r.type_id == TYPE_TXTR and r.group_id == s2texture.RECOLOUR_GROUP:
                try:
                    name = s2texture.parse_image_data(r.data).name
                except ValueError:
                    continue
            else:
                continue
            _t, _g, inst, hi = s2texture.scenegraph_tgi(r.type_id, name)
            if inst == r.instance_id and hi == r.instance_hi:
                hits += 1
            else:
                misses += 1
                notes.append(f"{p.name}: {name} hashes to {inst:08X}/{hi:08X}, "
                             f"stored {r.instance_id:08X}/{r.instance_hi:08X}")
    if misses > hits // 4:
        failures.append(f"hash rule misses {misses} of {hits + misses}")
    print(f"4. name hash matches {hits} donor materials/textures, {misses} differ")

    # 5. Donors rebuilt from parts: every MMAT written in SimPE's key order
    # must come back byte-identical from new_mmat, and the painting's texture
    # must survive decode -> encode within tolerance.
    same = 0
    for p in samples:
        for r in s2writer.read_all_resources(p):
            if r.type_id != TYPE_MMAT:
                continue
            try:
                m = s2object.parse_mmat(r.data)
            except ValueError:
                continue
            if tuple(e.key for e in m.entries) != s2object.MMAT_KEY_ORDER:
                continue
            fresh = s2object.new_mmat(name=m.name, model_name=m.model_name,
                                      subset_name=m.subset_name, object_guid=m.object_guid,
                                      family=m.family, default=m.default_material,
                                      flags=int(m.get("flags", 0)),
                                      material_state_flags=m.material_state_flags,
                                      object_state_index=m.object_state_index,
                                      creator=str(m.get("creator", "")))
            if s2object.build_mmat(fresh) != r.data:
                failures.append(f"{p.name}: MMAT rebuilt from its fields differs")
            same += 1
    donor = next((p for p in samples if p.name == "CS_ParadisePainting01.package"), None)
    if donor is not None:
        res = s2writer.read_all_resources(donor)
        for r in res:
            if r.type_id == TYPE_TXTR:
                t = s2texture.parse_image_data(r.data)
                lvl = t.largest()
                px = s2texture.decode(lvl, t.format)
                mine = s2texture.make_texture(t.name, t.width, t.height, px, template=t)
                if len(mine.levels) != len(t.levels):
                    failures.append(f"donor texture has {len(t.levels)} levels, rebuilt {len(mine.levels)}")
                back = s2texture.decode(mine.levels[0], mine.format)
                e = sum(abs(back[i] - px[i]) for i in range(0, len(px), 4)) / (len(px) / 4)
                if e > 8:
                    failures.append(f"re-encoded donor texture drifts ({e:.1f} per red channel)")
                if s2texture.parse_image_data(s2texture.build_image_data(mine)).width != t.width:
                    failures.append("rebuilt donor texture does not parse")
        print(f"5. {same} donor MMATs rebuilt from their fields byte for byte; painting texture re-encoded within tolerance")
    else:
        notes.append("painting donor absent; step 5 texture check skipped")

    # 6. Inventory on known game objects.
    if s2catalog.OBJECTS_PACKAGE.is_file():
        inv = inventory("counterloft", [0x8C26FB08, 0xCC7CD58D])
        names = {s.name.lower(): s for s in inv.subsets}
        top = names.get("countertop")
        if not top or not top.recolourable or len(top.states) != 2 or set(top.guids) != {0x8C26FB08, 0xCC7CD58D}:
            failures.append(f"counter inventory wrong: {inv.to_json()}")
        else:
            print(f"6. counter: countertop {top.width}x{top.height} states "
                  f"{[s.material for s in top.states]}, {len(inv.game_options)} game colours")
        tele = inventory("telescopeCheap", [0])
        if any(s.recolourable for s in tele.subsets):
            failures.append("the cheap telescope should have no recolourable subset")
        # A render from a tint, twice, must agree byte for byte.
        looks = Looks([Look("abcd1234", "Teal", True, {"countertop": TintSource((20, 120, 140), 0.8, 0.1)})])
        a = render("0123456789abcdef", "counterloft", looks, inv, {0x8C26FB08: 0x11111111, 0xCC7CD58D: 0x22222222},
                   read_file=lambda p: b"")
        b = render("0123456789abcdef", "counterloft", looks, inv, {0x8C26FB08: 0x11111111, 0xCC7CD58D: 0x22222222},
                   read_file=lambda p: b"")
        kinds = [r.type_id for r in a]
        if [d.data for d in a] != [d.data for d in b] or [r.tgi() for r in a] != [r.tgi() for r in b]:
            failures.append("render is not deterministic")
        n_txtr, n_txmt = kinds.count(TYPE_TXTR), kinds.count(TYPE_TXMT)
        n_mmat = kinds.count(TYPE_MMAT)
        expected_mmat = 2 * 2 + len([m for m in inv.game_options])
        if (n_txtr, n_txmt) != (1, 2) or n_mmat != expected_mmat:
            failures.append(f"render made {n_txtr} TXTR {n_txmt} TXMT {n_mmat} MMAT, expected 1/2/{expected_mmat}")
        for r in a:
            if r.type_id == TYPE_MMAT:
                m = s2object.parse_mmat(r.data)
                if m.object_guid not in (0x11111111, 0x22222222):
                    failures.append(f"MMAT points at 0x{m.object_guid:08X}")
            if r.type_id == TYPE_TXMT:
                t = s2object.parse_txmt(r.data)
                if not t.base_texture.startswith(PREFIX):
                    failures.append(f"TXMT base texture lacks the prefix: {t.base_texture}")
        print(f"6. render: {n_txtr} TXTR, {n_txmt} TXMT, {n_mmat} MMAT, deterministic")
    else:
        notes.append("game not installed; inventory checks skipped")

    for msg in notes:
        print(f"  note: {msg}")
    for msg in failures:
        print(f"  FAIL {msg}")
    print(f"looks selftest: {'FAILED' if failures else 'ok'}")
    return 1 if failures else 0


def main(argv: "list[str] | None" = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Looks: recolours for object projects.")
    ap.add_argument("--selftest", metavar="DIR", help="prove the module over the donors in DIR")
    ap.add_argument("--model", help="print the inventory of a game model")
    ap.add_argument("--guid", type=lambda s: int(s, 0), default=0, help="the object's GUID (for the game's colours)")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest(args.selftest)
    if args.model:
        inv = inventory(args.model, [args.guid] if args.guid else [])
        for s in inv.subsets:
            lock = "" if s.recolourable else "  (fixed)"
            print(f"{s.name:20} {s.width}x{s.height} {s.material}{lock}  states={[x.material for x in s.states]}")
        for w in inv.warnings:
            print("warning:", w)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
