#!/usr/bin/env python3
"""Sim Studio daemon — a JSON-RPC package editor over stdin/stdout.

The Swift app never opens a .package. It launches one of these per window,
and everything about the file — the bytes, the decoding, the undo history,
and the decision whether it may be saved at all — lives here. Swift sees
JSON and nothing else, which is what keeps the CLAUDE.md invariant true:
the app cannot corrupt a save because it never holds one.

Transport
---------
One JSON object per line, each way.

    -> {"id": 1, "method": "open", "params": {"path": "..."}}
    <- {"id": 1, "result": {...}}
    <- {"id": 1, "error": {"code": "readonly", "message": "...", "data": {...}}}

Notifications from the server carry no id: {"event": "progress", ...}.
Resource bytes travel as hex strings; images as base64. stderr is for
diagnostics only and is never parsed.

Read-only policy
----------------
`is_protected` decides, and it is enforced here rather than in the UI. A
neighborhood package (anything under a Neighborhoods folder) and anything
inside the game's own install open read-only: `save` on them fails with
`readonly`, and `save_as` refuses a destination in either place with
`destination_protected`. Save As to a copy elsewhere is fine — that is what
hoodcheck.py --repair does, and it never touches the original.

Undo
----
Swift cannot restore bytes it never had, so the undo stack is here. Each
step records what a resource was before and after, and its position in the
list; `undo`/`redo` reverse a step exactly and tell the client which TGI to
refresh.
"""

# Annotations stay strings so the modules import under the system
# python3 (3.9), which the app gets when launched from Finder.
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import struct
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import base64

import s2clone
import s2doctor
import s2mesh
import s2object
import s2package
import s2parser
import s2texture
import s2tools
import s2writer
from s2writer import Resource

PROTOCOL_VERSION = 1

# Undo history is bounded so a long session on objects.package cannot grow
# without limit — 200 steps, or 64 MB of snapshot payload, whichever first.
UNDO_MAX_STEPS = 200
UNDO_MAX_BYTES = 64 * 1024 * 1024


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class RpcError(Exception):
    def __init__(self, code: str, message: str, data: "dict | None" = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}


# ---------------------------------------------------------------------------
# Read-only policy
# ---------------------------------------------------------------------------

def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def protection_reason(path: "Path | str") -> "str | None":
    """Why this path may not be written, or None if it may.

    Checked on the resolved path and every ancestor, so it holds for a file
    that does not exist yet (a Save As destination) as well as one that does.
    """
    p = Path(path).expanduser()
    try:
        p = p.resolve()
    except OSError:
        p = p.absolute()

    for root in s2doctor.ROOT_CANDIDATES:
        if _under(p, root / "Neighborhoods"):
            return "inside the game's Neighborhoods folder"

    for anc in [p] + list(p.parents):
        if anc.name == "Neighborhoods":
            # A folder called Neighborhoods that holds hoods is a save tree
            # wherever it lives — a backup, a second install. One that holds
            # nothing of the sort is just a folder.
            if any(anc.glob("*/*_Neighborhood.package")):
                return "inside a Neighborhoods folder that holds saves"
        if anc.suffix == ".app" or anc.name == "TSData":
            return "inside the game's own install"
    return None


def is_protected(path: "Path | str") -> bool:
    return protection_reason(path) is not None


# ---------------------------------------------------------------------------
# Decoded <-> JSON
# ---------------------------------------------------------------------------

# Every dataclass s2object defines, by name, so a decoded resource can come
# back from the client as JSON and be rebuilt through build_resource. New
# parser types register themselves here just by being dataclasses.
_CLASSES = {
    name: obj for name, obj in vars(s2object).items()
    if isinstance(obj, type) and dataclasses.is_dataclass(obj)
}


def _settable_properties(cls) -> "list[str]":
    """Names of the dataclass's read/write properties — the typed views a
    parser layers over its raw fields (TtabEntry.action over `raw`,
    Objd.guid over `words`). They travel alongside the fields so the editor
    can show and edit them by name without knowing the byte layout."""
    return [name for name in dir(cls)
            if not name.startswith("_")
            and isinstance(getattr(cls, name, None), property)
            and getattr(cls, name).fset is not None]


def to_json(obj):
    """Dataclass -> {"$type": name, fields..., "$props": {...}}; bytes -> {"$hex": ...}."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        out = {"$type": type(obj).__name__}
        for f in dataclasses.fields(obj):
            out[f.name] = to_json(getattr(obj, f.name))
        props = {name: to_json(getattr(obj, name)) for name in _settable_properties(type(obj))}
        if props:
            out["$props"] = props
        return out
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return {"$hex": bytes(obj).hex()}
    if isinstance(obj, (list, tuple)):
        return [to_json(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): to_json(v) for k, v in obj.items()}
    return obj


def from_json(value):
    """Inverse of to_json. Unknown "$type" names are a client error."""
    if isinstance(value, dict):
        if "$hex" in value and len(value) == 1:
            try:
                return bytes.fromhex(value["$hex"])
            except ValueError as exc:
                raise RpcError("bad_params", f"bad hex: {exc}") from None
        if "$type" in value:
            cls = _CLASSES.get(value["$type"])
            if cls is None:
                raise RpcError("bad_params", f"unknown type {value['$type']!r}")
            kwargs = {}
            for f in dataclasses.fields(cls):
                if f.name not in value:
                    continue
                v = from_json(value[f.name])
                # TtabEntry.raw is a bytearray so its property setters can
                # pack into it; the annotation is a string under
                # `from __future__ import annotations`, hence the text test.
                if isinstance(v, bytes) and "bytearray" in str(f.type):
                    v = bytearray(v)
                kwargs[f.name] = v
            try:
                obj = cls(**kwargs)
            except TypeError as exc:
                raise RpcError("bad_params", f"{cls.__name__}: {exc}") from None
            # Properties are applied after the fields, so an edit made by
            # name (action=…) wins over the raw bytes it is a view onto.
            for name, v in (value.get("$props") or {}).items():
                if name not in _settable_properties(cls):
                    raise RpcError("bad_params", f"{cls.__name__} has no property {name!r}")
                try:
                    setattr(obj, name, from_json(v))
                except (TypeError, ValueError, struct.error) as exc:
                    raise RpcError("bad_params", f"{cls.__name__}.{name}: {exc}") from None
            return obj
        return {k: from_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_json(x) for x in value]
    return value


# ---------------------------------------------------------------------------
# Session and undo
# ---------------------------------------------------------------------------

@dataclass
class Change:
    """One resource's before/after inside an undo step.

    `old`/`new` are None for an add / a delete respectively. `index` is where
    the resource sat, so undoing a delete puts it back in the same place.
    """
    tgi: "tuple[int, int, int, int]"
    old: "Resource | None"
    new: "Resource | None"
    index: int
    old_compressed: bool = False
    new_compressed: bool = False

    def size(self) -> int:
        return (len(self.old.data) if self.old else 0) + (len(self.new.data) if self.new else 0)


@dataclass
class Step:
    label: str
    changes: "list[Change]" = field(default_factory=list)

    def size(self) -> int:
        return sum(c.size() for c in self.changes)

    def tgis(self) -> list:
        seen = []
        for c in self.changes:
            for t in (c.tgi, c.new.tgi() if c.new else None, c.old.tgi() if c.old else None):
                if t is not None and t not in seen:
                    seen.append(t)
        return seen


@dataclass
class Session:
    path: "Path | None" = None
    readonly: bool = True
    readonly_reason: str = ""
    header: "s2parser.Header | None" = None
    resources: "list[Resource]" = field(default_factory=list)
    compressed: "set[tuple[int, int, int, int]]" = field(default_factory=set)
    dirty: bool = False
    undo: "list[Step]" = field(default_factory=list)
    redo: "list[Step]" = field(default_factory=list)
    # instance -> name for every BHAV, built on first use. Reading a name
    # means inflating the resource, and objects.package has thousands, so
    # the answer is kept until an edit could have changed it.
    bhav_names: "dict[int, str] | None" = None
    # Per-session lookups that are expensive to build (a hood's characters).
    cache: dict = field(default_factory=dict)

    # -- history ----------------------------------------------------------

    def push(self, step: Step) -> None:
        self.undo.append(step)
        self.redo.clear()
        self.dirty = True
        self.bhav_names = None
        while len(self.undo) > UNDO_MAX_STEPS or (
                len(self.undo) > 1 and sum(s.size() for s in self.undo) > UNDO_MAX_BYTES):
            self.undo.pop(0)

    def _apply(self, change: Change, forward: bool) -> None:
        before, after = (change.old, change.new) if forward else (change.new, change.old)
        comp = change.new_compressed if forward else change.old_compressed
        if before is not None:
            n = s2package.find(self.resources, before.tgi())
            if n >= 0:
                self.resources.pop(n)
            self.compressed.discard(before.tgi())
        if after is not None:
            s2package.add(self.resources, s2package.copy(after), at=change.index)
            if comp:
                self.compressed.add(after.tgi())
            else:
                self.compressed.discard(after.tgi())

    def do_undo(self) -> Step:
        if not self.undo:
            raise RpcError("nothing_to_undo", "nothing to undo")
        step = self.undo.pop()
        for c in reversed(step.changes):
            self._apply(c, forward=False)
        self.redo.append(step)
        self.dirty = True
        self.bhav_names = None
        return step

    def do_redo(self) -> Step:
        if not self.redo:
            raise RpcError("nothing_to_redo", "nothing to redo")
        step = self.redo.pop()
        for c in step.changes:
            self._apply(c, forward=True)
        self.undo.append(step)
        self.dirty = True
        self.bhav_names = None
        return step

    # -- lookups ----------------------------------------------------------

    def require(self, tgi) -> Resource:
        r = s2package.get(self.resources, tgi)
        if r is None:
            raise RpcError("not_found", f"no resource {tgi}", {"tgi": s2package.tgi_json(tgi)})
        return r

    def require_open(self) -> None:
        if self.path is None:
            raise RpcError("no_package", "no package is open")


def load(path: Path) -> Session:
    """Read a package into a fresh session."""
    if not path.is_file():
        raise RpcError("not_found", f"no such file: {path}")
    # Read every payload but inflate none: a compressed resource becomes a
    # LazyResource that decompresses when first looked at. The DIR itself is
    # not kept as a resource — the session tracks compression in
    # `compressed`, and write_package rebuilds the DIR from that.
    resources: "list[Resource]" = []
    compressed: "set[tuple[int, int, int, int]]" = set()
    try:
        with open(path, "rb") as f:
            header = s2parser.parse_header(f)
            entries = s2parser.parse_index(f, header)
            version = (header.index_major_version, header.index_minor_version)
            directory = s2parser.read_dir(f, entries, version) or {}
            for e in entries:
                if e.type_id == s2parser.TYPE_DIR:
                    continue
                key = (e.type_id, e.group_id, e.instance, e.resource_id)
                f.seek(e.offset)
                raw = f.read(e.size)
                if key in directory:
                    resources.append(s2package.LazyResource(
                        e.type_id, e.group_id, e.instance, raw, e.resource_id))
                    compressed.add(key)
                else:
                    resources.append(Resource(e.type_id, e.group_id, e.instance, raw, e.resource_id))
    except (OSError, ValueError, struct.error) as exc:
        raise RpcError("bad_package", f"cannot read {path.name}: {exc}") from None

    reason = protection_reason(path)
    if reason is None and not os.access(path, os.W_OK):
        reason = "file is not writable"
    return Session(
        path=path, readonly=reason is not None, readonly_reason=reason or "",
        header=header, resources=resources, compressed=compressed,
    )


# ---------------------------------------------------------------------------
# Methods
# ---------------------------------------------------------------------------

def _need(params: dict, key: str):
    if key not in params:
        raise RpcError("bad_params", f"missing parameter {key!r}")
    return params[key]


def _tgi(params: dict, key: str = "tgi"):
    try:
        return s2package.parse_tgi(_need(params, key))
    except ValueError as exc:
        raise RpcError("bad_params", str(exc)) from None


def _index_row(session: Session, r: Resource) -> dict:
    return {
        "type": r.type_id, "type_name": r.type_name,
        "group": r.group_id, "instance": r.instance_id, "instance_hi": r.instance_hi,
        "size": s2package.size(r),
        "compressed": r.tgi() in session.compressed,
        "decodable": r.type_id in s2object.PARSERS,
        "bhav": r.type_id == s2object.TYPE_BHAV,
        "flags": _row_flags(session, r),
        "name": s2package.resource_name(r),
    }


def _summary(session: Session) -> dict:
    h = session.header
    return {
        "path": str(session.path),
        "readonly": session.readonly,
        "readonly_reason": session.readonly_reason,
        "version": str(h) if h else "",
        "count": len(session.resources),
        "compressed_count": len(session.compressed),
        "dirty": session.dirty,
        "can_undo": bool(session.undo),
        "can_redo": bool(session.redo),
        "undo_label": session.undo[-1].label if session.undo else None,
        "redo_label": session.redo[-1].label if session.redo else None,
    }


def _bhav_names(session: Session) -> dict:
    """instance -> name for every BHAV in the package, for CallBHAV labels."""
    if session.bhav_names is None:
        names = {}
        for r in session.resources:
            if r.type_id == s2object.TYPE_BHAV and s2package.size(r) >= 64:
                names[r.instance_id] = r.data[:64].split(b"\x00", 1)[0].decode("latin-1")
        session.bhav_names = names
    return session.bhav_names


def _render_bhav(session: Session, r: Resource) -> dict:
    try:
        b = s2object.bhav_to_listing(s2object.parse_bhav_rt(r.data))
    except (ValueError, struct.error) as exc:
        return {"error": str(exc)}
    names = _bhav_names(session)
    return {"flat": b.fmt(names), "tree": s2parser.render_bhav_tree(b, names),
            "name": b.name, "format": b.format_version, "type": b.bhav_type,
            "argc": b.argc, "localc": b.localc, "count": len(b.instructions)}


def _write(session: Session, dest: Path) -> None:
    """Write the session to `dest` atomically: a sibling temp file, then a
    rename, so a crash mid-write cannot leave a half package behind."""
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        s2writer.write_package(tmp, session.resources, compress_tgis=session.compressed)
        os.replace(tmp, dest)
    except (OSError, ValueError) as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise RpcError("write_failed", str(exc)) from None


def m_open(session: Session, params: dict) -> dict:
    new = load(Path(_need(params, "path")).expanduser())
    session.__dict__.update(new.__dict__)
    return _summary(session)


def m_status(session: Session, params: dict) -> dict:
    session.require_open()
    return _summary(session)


INDEX_COLUMNS = ["type", "group", "instance", "instance_hi", "size", "flags"]
FLAG_COMPRESSED, FLAG_DECODABLE, FLAG_BHAV, FLAG_TEXTURE, FLAG_MESH = 1, 2, 4, 8, 16
TEXTURE_TYPES = {s2texture.TYPE_TXTR, 0xFC4B284B}    # both ids s2parser names TXTR


def _row_flags(session: Session, r: Resource) -> int:
    return ((FLAG_COMPRESSED if r.tgi() in session.compressed else 0)
            | (FLAG_DECODABLE if r.type_id in s2object.PARSERS else 0)
            | (FLAG_BHAV if r.type_id == s2object.TYPE_BHAV else 0)
            | (FLAG_TEXTURE if r.type_id in TEXTURE_TYPES else 0)
            | (FLAG_MESH if r.type_id == s2mesh.TYPE_GMDC else 0))


def m_index(session: Session, params: dict) -> dict:
    """The whole index, one short array per resource.

    objects.package has 52,000 entries; as keyed objects that was 8.7 MB and
    took the app seven seconds to decode. Arrays of numbers are a third the
    size and parse in a fraction of that. `columns` names the positions and
    `type_names` maps each type id present to its name.
    """
    session.require_open()
    rows = []
    names = {}
    for r in session.resources:
        rows.append([r.type_id, r.group_id, r.instance_id, r.instance_hi,
                     s2package.size(r), _row_flags(session, r)])
        if r.type_id not in names:
            names[r.type_id] = r.type_name
    return {"columns": INDEX_COLUMNS, "rows": rows,
            "type_names": {str(k): v for k, v in names.items()}}


def m_names(session: Session, params: dict) -> dict:
    """Each resource's own name — the 64-byte filename most object resources
    open with, the cSGResource name of a scenegraph resource — as compact
    `[type, group, instance, instance_hi, name]` rows, only for resources
    that have one. Separate from `index` because it touches every payload
    (0.8 s over objects.package's 52,000 entries against 0.3 s to open),
    so the window can show the index first and fill names in after; and
    because undo and compression changes reload the index without needing
    names read again. `tgis` limits it to those resources.
    """
    session.require_open()
    if "tgis" in params:
        wanted = [s2package.parse_tgi(t) for t in params["tgis"]]
        resources = [r for r in (s2package.get(session.resources, t) for t in wanted) if r is not None]
    else:
        resources = session.resources
    rows = []
    for r in resources:
        name = s2package.resource_name(r)
        if name:
            rows.append([r.type_id, r.group_id, r.instance_id, r.instance_hi, name])
    return {"names": rows}


def m_meta(session: Session, params: dict) -> dict:
    """Static tables the UI needs, served so Swift carries no format knowledge."""
    return {
        "protocol": PROTOCOL_VERSION,
        "type_names": {str(k): v for k, v in s2parser.TYPE_NAMES.items()},
        "type_descriptions": {str(k): v for k, v in s2parser.TYPE_DESCRIPTIONS.items()},
        "decodable_types": sorted(s2object.PARSERS),
        "bhav_type": s2object.TYPE_BHAV,
        "objd_fields": s2object.OBJD_FIELDS,
        # u32 properties and the low word each spans (guid = words 14,15).
        "objd_u32_fields": {"guid": 14, "job_guid": 52, "original_guid": 70},
        "objd_word_count": s2object.OBJD_WORD_COUNT,
        "objf_slots": {str(k): v for k, v in s2object.OBJF_SLOTS.items()},
        "str_formats": {"with_desc": s2object.STR_FMT_WITH_DESC,
                        "no_desc": s2object.STR_FMT_NO_DESC},
        "ttab_layouts": {str(k): {"entry_size": v[0], "ttas_offset": v[1]}
                         for k, v in s2object.TTAB_LAYOUTS.items()},
    }


def m_get_resource(session: Session, params: dict) -> dict:
    session.require_open()
    r = session.require(_tgi(params))
    # The row is nested rather than spread into the reply, so no detail
    # key can ever collide with a row key (the Bool `bhav` flag once did).
    out = {"row": _index_row(session, r), "hex": r.data.hex(),
           "decoded": None, "decode_error": None}
    if r.type_id in s2object.PARSERS:
        try:
            out["decoded"] = to_json(s2object.parse_resource(r.type_id, r.data))
        except (ValueError, struct.error, IndexError) as exc:
            out["decode_error"] = str(exc)
    if r.type_id == s2object.TYPE_BHAV:
        out["bhav_render"] = _render_bhav(session, r)
    return out


def m_render_bhav(session: Session, params: dict) -> dict:
    session.require_open()
    r = session.require(_tgi(params))
    if r.type_id != s2object.TYPE_BHAV:
        raise RpcError("bad_params", f"{r.type_name} is not a BHAV")
    return _render_bhav(session, r)


def _new_data(r: Resource, params: dict) -> bytes:
    """The replacement bytes for a put: raw hex, or a decoded object rebuilt
    through s2object. Exactly one of the two must be given."""
    if ("hex" in params) == ("decoded" in params):
        raise RpcError("bad_params", "give exactly one of 'hex' or 'decoded'")
    if "hex" in params:
        try:
            return bytes.fromhex(params["hex"])
        except ValueError as exc:
            raise RpcError("bad_params", f"bad hex: {exc}") from None
    if r.type_id not in s2object.PARSERS:
        raise RpcError("build_failed", f"{r.type_name} has no builder; send hex")
    obj = from_json(params["decoded"])
    try:
        return s2object.build_resource(r.type_id, obj)
    except (ValueError, struct.error, TypeError, AttributeError) as exc:
        raise RpcError("build_failed", str(exc), {"type_name": r.type_name}) from None


def m_put_resource(session: Session, params: dict) -> dict:
    session.require_open()
    tgi = _tgi(params)
    r = session.require(tgi)
    data = _new_data(r, params)
    if data == r.data:
        return {"size": len(data), "changed": False, "name": s2package.resource_name(r),
                **_summary(session)}
    n = s2package.find(session.resources, tgi)
    old = s2package.copy(r)
    r.data = data
    comp = tgi in session.compressed
    session.push(Step(params.get("label") or f"Edit {r.type_name}",
                      [Change(tgi, old, s2package.copy(r), n, comp, comp)]))
    return {"size": len(data), "changed": True, "name": s2package.resource_name(r),
            **_summary(session)}


def m_add_resource(session: Session, params: dict) -> dict:
    session.require_open()
    t, g, i, hi = _tgi(params)
    try:
        data = bytes.fromhex(params.get("hex", ""))
    except ValueError as exc:
        raise RpcError("bad_params", f"bad hex: {exc}") from None
    res = Resource(t, g, i, data, hi)
    try:
        n = s2package.add(session.resources, res)
    except ValueError as exc:
        raise RpcError("duplicate_tgi", str(exc)) from None
    comp = bool(params.get("compressed", False))
    if comp:
        session.compressed.add(res.tgi())
    session.push(Step(f"Add {res.type_name}",
                      [Change(res.tgi(), None, s2package.copy(res), n, False, comp)]))
    return {**_index_row(session, res), **_summary(session)}


def m_delete_resource(session: Session, params: dict) -> dict:
    session.require_open()
    tgis = [s2package.parse_tgi(x) for x in params["tgis"]] if "tgis" in params else [_tgi(params)]
    changes = []
    for tgi in tgis:
        r = session.require(tgi)
        comp = tgi in session.compressed
        n, r = s2package.remove(session.resources, tgi)
        session.compressed.discard(tgi)
        changes.append(Change(tgi, s2package.copy(r), None, n, comp, False))
    # Undo re-inserts in reverse order, so record from the back to keep
    # positions right when several are deleted at once.
    label = f"Delete {changes[0].old.type_name}" if len(changes) == 1 else f"Delete {len(changes)} resources"
    session.push(Step(label, changes))
    return _summary(session)


def m_rename_resource(session: Session, params: dict) -> dict:
    session.require_open()
    tgi = _tgi(params)
    new_tgi = _tgi(params, "new_tgi")
    r = session.require(tgi)
    if new_tgi == tgi:
        return {**_index_row(session, r), **_summary(session)}
    n = s2package.find(session.resources, tgi)
    old = s2package.copy(r)
    comp = tgi in session.compressed
    try:
        r = s2package.rename(session.resources, tgi, new_tgi)
    except ValueError as exc:
        raise RpcError("duplicate_tgi", str(exc)) from None
    session.compressed.discard(tgi)
    if comp:
        session.compressed.add(new_tgi)
    session.push(Step(f"Rename {r.type_name}",
                      [Change(tgi, old, s2package.copy(r), n, comp, comp)]))
    return {**_index_row(session, r), **_summary(session)}


def m_set_compressed(session: Session, params: dict) -> dict:
    """Mark resources to be QFS-compressed on the next save. Takes effect at
    save time, so it is recorded as an edit but costs nothing now."""
    session.require_open()
    flag = bool(_need(params, "compressed"))
    tgis = ([s2package.parse_tgi(x) for x in params["tgis"]] if "tgis" in params
            else [t.tgi() for t in session.resources] if params.get("all")
            else [_tgi(params)])
    changes = []
    for tgi in tgis:
        r = session.require(tgi)
        was = tgi in session.compressed
        if was == flag:
            continue
        n = s2package.find(session.resources, tgi)
        snap = s2package.copy(r)
        changes.append(Change(tgi, snap, snap, n, was, flag))
        (session.compressed.add if flag else session.compressed.discard)(tgi)
    if changes:
        session.push(Step(("Compress" if flag else "Store") + f" {len(changes)} resource(s)", changes))
    return _summary(session)


def m_undo(session: Session, params: dict) -> dict:
    session.require_open()
    step = session.do_undo()
    return {"label": step.label, "tgis": [s2package.tgi_json(t) for t in step.tgis()], **_summary(session)}


def m_redo(session: Session, params: dict) -> dict:
    session.require_open()
    step = session.do_redo()
    return {"label": step.label, "tgis": [s2package.tgi_json(t) for t in step.tgis()], **_summary(session)}


def m_save(session: Session, params: dict) -> dict:
    session.require_open()
    if session.readonly:
        raise RpcError("readonly", f"{session.path.name} is read-only: {session.readonly_reason}. "
                       "Use Save As to write a copy elsewhere.",
                       {"reason": session.readonly_reason})
    _write(session, session.path)
    session.dirty = False
    return _summary(session)


def m_save_as(session: Session, params: dict) -> dict:
    session.require_open()
    dest = Path(_need(params, "path")).expanduser()
    reason = protection_reason(dest)
    if reason is not None:
        raise RpcError("destination_protected",
                       f"refusing to write {dest.name}: {reason}", {"reason": reason})
    if not dest.parent.is_dir():
        raise RpcError("not_found", f"no such folder: {dest.parent}")
    _write(session, dest)
    session.path = dest
    session.readonly = False
    session.readonly_reason = ""
    session.dirty = False
    return _summary(session)


def m_export_resource(session: Session, params: dict) -> dict:
    """Write one resource's decompressed bytes to a file the user chose."""
    session.require_open()
    r = session.require(_tgi(params))
    dest = Path(_need(params, "path")).expanduser()
    if is_protected(dest):
        raise RpcError("destination_protected", f"refusing to write inside {dest.parent}")
    try:
        dest.write_bytes(r.data)
    except OSError as exc:
        raise RpcError("write_failed", str(exc)) from None
    return {"path": str(dest), "size": len(r.data)}


def m_import_resource(session: Session, params: dict) -> dict:
    """Replace one resource's bytes from a file (the inverse of export)."""
    session.require_open()
    src = Path(_need(params, "path")).expanduser()
    try:
        data = src.read_bytes()
    except OSError as exc:
        raise RpcError("not_found", str(exc)) from None
    return m_put_resource(session, {"tgi": params["tgi"], "hex": data.hex(),
                                    "label": f"Import {src.name}"})


# ---------------------------------------------------------------------------
# Object Workshop and package tools
# ---------------------------------------------------------------------------

# Set by serve(); methods call it to stream progress to the client. A no-op
# when the methods are driven directly, as the tests do.
EMIT = lambda obj: None   # noqa: E731


def _progress(op: str, done: int, total: int, note: str = "") -> None:
    EMIT({"event": "progress", "op": op, "done": done, "total": total, "note": note})


def _game_root(params: dict) -> Path:
    if params.get("root"):
        root = Path(params["root"]).expanduser()
    else:
        root = next((c for c in s2doctor.ROOT_CANDIDATES if (c / "Neighborhoods").is_dir()), None)
    if root is None or not root.is_dir():
        raise RpcError("not_found", "could not find a Sims 2 user folder; pass root")
    return root


def _object_rows(session: Session) -> list:
    out = []
    for n, r in enumerate(session.resources):
        if r.type_id != s2object.TYPE_OBJD:
            continue
        try:
            o = s2object.parse_objd(r.data)
        except (ValueError, struct.error):
            continue
        out.append({"index": n, "instance": r.instance_id, "group": r.group_id,
                    "guid": o.guid, "original_guid": o.original_guid,
                    "filename": o.filename, "name": o.name, "price": o.price,
                    "ttab_id": o.ttab_id, "ctss_id": o.ctss_id})
    return out


def m_objects(session: Session, params: dict) -> dict:
    """Every object definition in the package — what the clone sheet picks from."""
    session.require_open()
    return {"objects": _object_rows(session)}


def m_derive_guid(session: Session, params: dict) -> dict:
    return {"guid": s2clone.derive_guid(_need(params, "seed"))}


def m_clone(session: Session, params: dict) -> dict:
    """Object Workshop: re-identify an object in place, as one undo step.

    Wraps s2clone.clone, which rewrites the OBJD, catalog text, NREF, the
    GUID literals in behaviour trees at confirmed operand slots, and MMAT
    references. What it patched and what it deliberately left alone come
    back in the report. Typical use: open objects.package (read-only),
    clone, Save As into Downloads.
    """
    session.require_open()
    name = params.get("name") or None
    guid = params.get("guid")
    if guid is None:
        guid = s2clone.derive_guid(name or f"{session.path.name}:{len(session.undo)}")
    before = [(r.tgi(), s2package.copy(r)) for r in session.resources]
    try:
        report = s2clone.clone(
            session.resources, guid=int(guid), name=name,
            description=params.get("description") or None,
            price=int(params["price"]) if params.get("price") is not None else None,
            instance=int(params["instance"]) if params.get("instance") is not None else None,
            select_guid=int(params["select_guid"]) if params.get("select_guid") is not None else None,
            aggressive=bool(params.get("aggressive", False)))
    except (ValueError, struct.error) as exc:
        # The list may be half-rewritten; put every record back.
        session.resources[:] = [old for _, old in before]
        raise RpcError("clone_failed", str(exc)) from None

    changes = []
    for n, r in enumerate(session.resources):
        old_tgi, old = before[n]
        if r.tgi() == old_tgi and s2package.same_bytes(old, r):
            continue
        comp = old_tgi in session.compressed
        if r.tgi() != old_tgi:
            session.compressed.discard(old_tgi)
            if comp:
                session.compressed.add(r.tgi())
        changes.append(Change(old_tgi, old, s2package.copy(r), n, comp, comp))
    if changes:
        session.push(Step(f"Clone → 0x{report.new_guid:08X}", changes))

    return {
        "source_guid": report.source_guid, "new_guid": report.new_guid,
        "resource_count": report.resource_count, "changed": len(changes),
        "patches": [{"instance": p.instance, "bhav_name": p.bhav_name,
                     "instr_index": p.instr_index, "opcode": p.opcode,
                     "opcode_name": s2parser.PRIMITIVES.get(p.opcode, ""),
                     "operand_offset": p.operand_offset,
                     "known_layout": p.known_layout, "applied": p.applied}
                    for p in report.patches],
        "warnings": list(report.warnings),
        **_summary(session),
    }


def m_scan_guids(session: Session, params: dict) -> dict:
    """Every object GUID in the Downloads folder (or the folders given), and
    which packages hold it. Reads only the OBJD entries, so a large
    Downloads folder takes seconds rather than minutes."""
    folders = [Path(p).expanduser() for p in params.get("folders", [])]
    if not folders:
        folders = [_game_root(params) / "Downloads"]
    paths = [p for folder in folders if folder.is_dir()
             for p in sorted(folder.rglob("*.package")) if not p.name.startswith(".")]
    found: "dict[int, list[str]]" = {}
    for n, path in enumerate(paths):
        if n % 25 == 0:
            _progress("scan_guids", n, len(paths), path.name)
        try:
            _, entries = s2parser.open_package(path)
            for guid, _name in s2doctor.read_objd_guids(path, entries):
                found.setdefault(guid, []).append(path.name)
        except (OSError, ValueError, struct.error):
            continue
    _progress("scan_guids", len(paths), len(paths))
    wanted = [int(g) for g in params.get("guids", [])]
    return {
        "packages": len(paths),
        "guids": len(found),
        "collisions": {str(g): found[g] for g in wanted if g in found},
        "duplicates": {str(g): v for g, v in found.items() if len(v) > 1},
    }


def m_merge(session: Session, params: dict) -> dict:
    """Bring another package's resources into this one."""
    session.require_open()
    other = load(Path(_need(params, "path")).expanduser())
    on_conflict = params.get("on_conflict", "skip")
    before_count = len(session.resources)
    positions = {r.tgi(): n for n, r in enumerate(session.resources)}
    olds = {r.tgi(): s2package.copy(r) for r in session.resources}
    try:
        report = s2tools.merge(session.resources, other.resources, on_conflict)
    except ValueError as exc:
        raise RpcError("bad_params", str(exc)) from None
    changes = []
    for tgi in report.added:
        n = s2package.find(session.resources, tgi)
        comp = tgi in other.compressed
        if comp:
            session.compressed.add(tgi)
        changes.append(Change(tgi, None, s2package.copy(session.resources[n]), n, False, comp))
    for tgi in report.replaced:
        n = positions[tgi]
        was = tgi in session.compressed
        comp = tgi in other.compressed
        (session.compressed.add if comp else session.compressed.discard)(tgi)
        changes.append(Change(tgi, olds[tgi], s2package.copy(session.resources[n]), n, was, comp))
    if changes:
        session.push(Step(f"Merge {other.path.name}", changes))
    return {"added": len(report.added), "replaced": len(report.replaced),
            "skipped": len(report.skipped), "before": before_count, **_summary(session)}


def m_split(session: Session, params: dict) -> dict:
    """Write the given resources to a new package, optionally removing them."""
    session.require_open()
    dest = Path(_need(params, "path")).expanduser()
    reason = protection_reason(dest)
    if reason is not None:
        raise RpcError("destination_protected", f"refusing to write {dest.name}: {reason}")
    tgis = [s2package.parse_tgi(x) for x in _need(params, "tgis")]
    try:
        picked = s2tools.split(session.resources, tgis, remove=False)
    except KeyError as exc:
        raise RpcError("not_found", f"no resource {exc.args[0]}") from None
    if not picked:
        raise RpcError("bad_params", "nothing selected")
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        s2writer.write_package(tmp, picked, compress_tgis={r.tgi() for r in picked
                                                           if r.tgi() in session.compressed})
        os.replace(tmp, dest)
    except (OSError, ValueError) as exc:
        raise RpcError("write_failed", str(exc)) from None
    if params.get("remove"):
        m_delete_resource(session, {"tgis": [s2package.tgi_json(t) for t in tgis]})
    return {"path": str(dest), "written": len(picked), **_summary(session)}


def m_doctor(session: Session, params: dict) -> dict:
    """Run s2doctor's checks and return its findings — the same ones
    `s2doctor.py --json` prints — with progress along the way."""
    root = _game_root(params)
    downloads_only = bool(params.get("downloads_only", False))
    hash_files = not params.get("no_hash", False)
    top = int(params.get("top", 10))
    findings = []
    errors = []
    infos = []
    steps = 4
    if not downloads_only:
        _progress("doctor", 0, steps, "reading error logs")
        logs = root / "Logs"
        errors = s2doctor.scan_object_errors(logs)
        findings += s2doctor.check_object_errors(errors, top)
        findings += s2doctor.check_app_errors(logs)
        findings += s2doctor.check_crash_reports()
    _progress("doctor", 1, steps, "scanning Downloads")
    downloads = root / "Downloads"
    if downloads.is_dir():
        infos = s2doctor.scan_packages(downloads, hash_files=hash_files)
        findings += s2doctor.check_downloads_integrity(infos)
        findings += s2doctor.check_inert_files(downloads)
        if hash_files:
            findings += s2doctor.check_duplicates(infos)
        findings += s2doctor.check_tgi_conflicts(infos, top)
        findings += s2doctor.check_guid_conflicts(infos, top)
    else:
        findings.append(s2doctor.Finding("info", "no-downloads",
                                         "No Downloads folder — no custom content installed."))
    _progress("doctor", 2, steps, "checking caches")
    findings += s2doctor.check_caches(root)
    if not downloads_only and not params.get("skip_saves", False):
        _progress("doctor", 3, steps, "checking neighborhoods")
        findings += s2doctor.check_neighborhoods(root)
    if errors and infos:
        findings += s2doctor.check_error_mod_overlap(errors, infos, int(params.get("recent", 5)))
    _progress("doctor", steps, steps)
    findings.sort(key=lambda f: s2doctor.SEVERITY_ORDER[f.severity])
    return {"root": str(root), "packages": len(infos),
            "findings": [f.to_dict() for f in findings]}


PREVIEW_MAX_SIDE = 1024   # decode a smaller mip for the pane; export keeps the full one


def m_preview_texture(session: Session, params: dict) -> dict:
    session.require_open()
    r = session.require(_tgi(params))
    if r.type_id not in TEXTURE_TYPES:
        raise RpcError("bad_params", f"{r.type_name} is not a texture")
    try:
        texture = s2texture.load_texture(session.resources, r)
        level = next((l for l in texture.levels
                      if l.resolved and max(l.width, l.height) <= PREVIEW_MAX_SIDE), None)
        if level is None:
            raise ValueError("no decodable mip level is stored in this package (all in LIFOs?)")
        rgba = s2texture.decode(level, texture.format)
        png = s2texture.png_bytes(level.width, level.height, rgba)
    except (ValueError, struct.error, IndexError) as exc:
        raise RpcError("decode_failed", str(exc), {"type_name": r.type_name}) from None
    return {"name": texture.name, "width": texture.width, "height": texture.height,
            "format": texture.format_name, "levels": len(texture.levels),
            "shown_width": level.width, "shown_height": level.height,
            "png_b64": base64.b64encode(png).decode("ascii")}


def m_export_texture(session: Session, params: dict) -> dict:
    session.require_open()
    r = session.require(_tgi(params))
    dest = Path(_need(params, "path")).expanduser()
    if is_protected(dest):
        raise RpcError("destination_protected", f"refusing to write inside {dest.parent}")
    try:
        w, h = s2texture.export_png(s2texture.load_texture(session.resources, r), dest)
    except (ValueError, struct.error, IndexError, OSError) as exc:
        raise RpcError("decode_failed", str(exc)) from None
    return {"path": str(dest), "width": w, "height": h}


def m_preview_mesh(session: Session, params: dict) -> dict:
    """A GMDC as Wavefront OBJ text. The reader is partial — about half the
    game's meshes parse — and says so rather than guessing."""
    session.require_open()
    r = session.require(_tgi(params))
    if r.type_id != s2mesh.TYPE_GMDC:
        raise RpcError("bad_params", f"{r.type_name} is not a mesh")
    try:
        mesh = s2mesh.parse_gmdc(r.data)
        obj = s2mesh.to_obj(mesh)
    except (ValueError, struct.error, IndexError) as exc:
        raise RpcError("decode_failed", f"GMDC reader is partial and could not read this one: {exc}",
                       {"type_name": r.type_name}) from None
    return {"name": mesh.filename, "obj": obj, "faces": mesh.total_faces(),
            "groups": [{"name": g.name, "faces": g.face_count} for g in mesh.groups],
            "partial": True}


# ---------------------------------------------------------------------------
# Neighborhoods: sims, relationships, memories
# ---------------------------------------------------------------------------
#
# A neighborhood package opens read-only (its folder is protected), and every
# edit here goes through the same undo stack as any other resource. Save As
# copies the whole hood folder somewhere else and writes the edited package
# into the copy — the CLAUDE.md rule holds: nothing writes into a save.

import shutil

import hoodcheck
import s2neighborhood
import s2ngbh

_MEMORY_NAMES: "dict[int, str] | None" = None


def _hood_dir(session: Session) -> "Path | None":
    """The hood folder, if the open package is a neighborhood package."""
    if session.path is None or not session.path.name.endswith("_Neighborhood.package"):
        return None
    return session.path.parent


def _characters(session: Session) -> dict:
    """guid -> {first, last, bio, file} from the hood's Characters folder,
    read once per session (700 packages take a couple of seconds)."""
    if "characters" not in session.cache:
        d = _hood_dir(session)
        session.cache["characters"] = (
            s2neighborhood.load_characters(d / "Characters") if d else {})
    return session.cache["characters"]


def _memory_names() -> "dict[int, str]":
    """Memory token GUID -> the memory object's name, from the game's own
    objects.package ("Memory - Love - WooHoo" and friends). Empty when the
    game install is not where s2doctor expects it."""
    global _MEMORY_NAMES
    if _MEMORY_NAMES is None:
        names = {}
        for root in ("/Applications/The Sims 2.app/Contents/Assets/TSData/Res/Objects",):
            pkg = Path(root) / "objects.package"
            if not pkg.is_file():
                continue
            try:
                for r in load(pkg).resources:
                    if r.type_id != s2object.TYPE_OBJD:
                        continue
                    try:
                        o = s2object.parse_objd(r.data)
                    except (ValueError, struct.error):
                        continue
                    if o.filename.startswith("Memory") or o.name.startswith("Memory"):
                        names[o.guid] = o.name or o.filename
            except RpcError:
                continue
        _MEMORY_NAMES = names
    return _MEMORY_NAMES


def _sdsc_resource(session: Session, nid: int) -> Resource:
    for r in session.resources:
        if r.type_id == s2neighborhood.TID_SDSC and r.instance_id == nid:
            return r
    for r in session.resources:
        if (r.type_id == s2neighborhood.TID_SDSC
                and s2package.size(r) >= s2neighborhood.SDSC_MIN_SIZE
                and struct.unpack_from("<H", r.data, 0x1A4)[0] == nid):
            return r
    raise RpcError("not_found", f"no sim {nid}")


def _sim_name(session: Session, sdsc: dict) -> str:
    ch = _characters(session).get(sdsc["guid"], {})
    return " ".join(x for x in (ch.get("first", ""), ch.get("last", "")) if x)


def _ngbh_resource(session: Session) -> "Resource | None":
    return next((r for r in session.resources if r.type_id == s2ngbh.TID_NGBH), None)


def _tokens_json(tokens) -> list:
    names = _memory_names()
    return [{"guid": t.guid, "name": names.get(t.guid, ""), "raw": t.raw.hex(),
             "values": list(t.values)} for t in tokens]


def _hood_check(session: Session) -> "dict | None":
    """hoodcheck's verdict on the token store the session holds.

    The store declares how many sim groups it holds; a failed save keeps
    only whole buffer chunks, and the game then loops forever on load.
    parse_ngbh resyncs past the damage, so this is the only place the
    editor would notice it. Runs on the in-memory bytes — the ones a Copy
    Hood will write — and is cheap enough (a linear walk) to recompute on
    every hood_meta, so it is never stale after an edit or an undo.

    A failure inside the check must not take hood_meta down with it:
    hood_meta is also what tells the app the file is a hood at all.
    """
    if _hood_dir(session) is None:
        return None
    try:
        ngbh = _ngbh_resource(session)
        if ngbh is None:
            rep = hoodcheck.Report(hood="", package=session.path, error="no NGBH resource")
        else:
            nids = [r.instance_id for r in session.resources if r.type_id == s2neighborhood.TID_SDSC]
            rep = hoodcheck.inspect_bytes(ngbh.data, nids, package=session.path)
        return {"healthy": rep.healthy, "summary": rep.verdict(), "error": rep.error,
                "declared": rep.declared, "actual": rep.actual,
                "sdsc_count": rep.sdsc_count, "missing_nids": rep.missing_nids,
                "trailing": len(rep.trailing), "ngbh_size": rep.ngbh_size,
                "chunk_aligned": rep.chunk_aligned}
    except Exception as exc:  # noqa: BLE001 — a diagnostic, not a gate
        return {"healthy": False, "summary": f"Token store could not be checked: {exc}.",
                "error": str(exc), "declared": 0, "actual": 0, "sdsc_count": 0,
                "missing_nids": [], "trailing": 0, "ngbh_size": 0, "chunk_aligned": False}


def m_hood_meta(session: Session, params: dict) -> dict:
    session.require_open()
    d = _hood_dir(session)
    return {
        "is_hood": d is not None,
        "hood_id": d.name if d else None,
        "check": _hood_check(session),
        "sdsc_fields": [{"name": n, "kind": k, "offset": off, "fmt": fmt}
                        for off, fmt, n, k in s2neighborhood.SDSC_FIELDS],
        "sdsc_tables": {k: {str(a): b for a, b in v.items()}
                        for k, v in s2neighborhood.SDSC_TABLES.items()},
        "srel_fields": [{"name": n, "kind": k, "offset": off, "fmt": fmt}
                        for off, fmt, n, k in s2neighborhood.SREL_FIELDS],
        "srel_tables": {k: {str(a): b for a, b in v.items()}
                        for k, v in s2neighborhood.SREL_TABLES.items()},
        "memory_owner_slot": s2ngbh.MEMORY_OWNER,
        "memory_subject_slot": s2ngbh.MEMORY_SUBJECT,
    }


def m_hood_sims(session: Session, params: dict) -> dict:
    """Every sim in the package, with names from the Characters folder."""
    session.require_open()
    sims = []
    for r in session.resources:
        if r.type_id != s2neighborhood.TID_SDSC or s2package.size(r) < s2neighborhood.SDSC_MIN_SIZE:
            continue
        s = s2neighborhood.parse_sdsc(r.data)
        ch = _characters(session).get(s["guid"], {})
        if s["age"] == "?0" and not ch:
            continue
        sims.append({"nid": s["nid"], "guid": s["guid"], "first": ch.get("first", ""),
                     "last": ch.get("last", ""), "age": s["age"], "gender": s["gender"],
                     "family_id": s["family_id"], "career": s["career"],
                     "career_title": s["career_title"], "aspirations": s["aspirations"],
                     "npc_type": s["npc_type"], "char_file": ch.get("file", "")})
    sims.sort(key=lambda s: (s["last"].lower(), s["first"].lower(), s["nid"]))
    return {"sims": sims, "characters": len(_characters(session))}


def m_hood_sim(session: Session, params: dict) -> dict:
    """One sim: raw fields, the extractor's resolved view, relationships,
    and the token group (memories included)."""
    session.require_open()
    nid = int(_need(params, "nid"))
    r = _sdsc_resource(session, nid)
    fields = s2neighborhood.parse_sdsc_fields(r.data)
    resolved = s2neighborhood.parse_sdsc(r.data)
    ch = _characters(session).get(resolved["guid"], {})
    names = {}
    rels = []
    for x in session.resources:
        if x.type_id != s2neighborhood.TID_SREL or (x.instance_id >> 16) & 0xFFFF != nid:
            continue
        target = x.instance_id & 0xFFFF
        if target == nid or s2package.size(x) < 0x0C:
            continue
        if target not in names:
            try:
                names[target] = _sim_name(session, s2neighborhood.parse_sdsc(_sdsc_resource(session, target).data))
            except RpcError:
                names[target] = ""
        rels.append({"target": target, "name": names[target], "size": s2package.size(x),
                     "fields": s2neighborhood.parse_srel_fields(x.data)})
    rels.sort(key=lambda z: (-(z["fields"].get("lifetime", 0)), z["target"]))
    tokens = {"first": [], "second": [], "editable": False, "error": None}
    ngbh = _ngbh_resource(session)
    if ngbh is not None:
        try:
            store = s2ngbh.parse_ngbh_rt(ngbh.data)
            g = store.group("sims", nid)
            tokens["editable"] = True
            if g is not None:
                tokens["first"] = _tokens_json(g.first)
                tokens["second"] = _tokens_json(g.second)
        except ValueError as exc:
            tokens["error"] = str(exc)
    return {"nid": nid, "tgi": s2package.tgi_json(r.tgi()), "fields": fields,
            "resolved": resolved, "first": ch.get("first", ""), "last": ch.get("last", ""),
            "bio": ch.get("bio", ""), "char_file": ch.get("file", ""),
            "relationships": rels, "tokens": tokens}


def m_hood_put_sim(session: Session, params: dict) -> dict:
    session.require_open()
    nid = int(_need(params, "nid"))
    r = _sdsc_resource(session, nid)
    try:
        data = s2neighborhood.build_sdsc(r.data, _need(params, "fields"))
    except (ValueError, TypeError) as exc:
        raise RpcError("build_failed", str(exc)) from None
    name = _sim_name(session, s2neighborhood.parse_sdsc(r.data)) or f"sim {nid}"
    return m_put_resource(session, {"tgi": s2package.tgi_json(r.tgi()), "hex": data.hex(),
                                    "label": f"Edit {name}"})


def m_hood_put_srel(session: Session, params: dict) -> dict:
    session.require_open()
    owner = int(_need(params, "owner"))
    target = int(_need(params, "target"))
    instance = (owner << 16) | target
    r = next((x for x in session.resources
              if x.type_id == s2neighborhood.TID_SREL and x.instance_id == instance), None)
    if r is None:
        raise RpcError("not_found", f"sim {owner} holds no relationship record about {target}")
    try:
        data = s2neighborhood.build_srel(r.data, _need(params, "fields"))
    except (ValueError, TypeError) as exc:
        raise RpcError("build_failed", str(exc)) from None
    return m_put_resource(session, {"tgi": s2package.tgi_json(r.tgi()), "hex": data.hex(),
                                    "label": f"Edit relationship {owner}→{target}"})


def m_hood_put_tokens(session: Session, params: dict) -> dict:
    """Replace a sim's token group — both lists — in the NGBH."""
    session.require_open()
    nid = int(_need(params, "nid"))
    ngbh = _ngbh_resource(session)
    if ngbh is None:
        raise RpcError("not_found", "this package has no NGBH token store")
    try:
        store = s2ngbh.parse_ngbh_rt(ngbh.data)
    except ValueError as exc:
        raise RpcError("unsupported_version", f"token store cannot be rebuilt faithfully: {exc}") from None

    def tokens(items):
        out = []
        for i, t in enumerate(items or []):
            try:
                raw = bytes.fromhex(t.get("raw") or "00" * 10)
                out.append(s2ngbh.NgbhToken(int(t["guid"]), raw, [int(v) for v in t.get("values", [])]))
            except (KeyError, ValueError, TypeError) as exc:
                raise RpcError("bad_params", f"token {i}: {exc}") from None
        return out

    g = store.group("sims", nid)
    if g is None:
        raise RpcError("not_found", f"sim {nid} has no token group; the store cannot grow one here")
    g.first = tokens(params.get("first"))
    g.second = tokens(params.get("second"))
    try:
        data = s2ngbh.build_ngbh_rt(store)
    except ValueError as exc:
        raise RpcError("build_failed", str(exc)) from None
    return m_put_resource(session, {"tgi": s2package.tgi_json(ngbh.tgi()), "hex": data.hex(),
                                    "label": f"Edit tokens of sim {nid}"})


def m_hood_save_as(session: Session, params: dict) -> dict:
    """Copy the whole hood folder to `dir` and write the edited neighborhood
    package into the copy. The original save is not touched."""
    session.require_open()
    src = _hood_dir(session)
    if src is None:
        raise RpcError("bad_params", "the open package is not a neighborhood package")
    dest = Path(_need(params, "dir")).expanduser()
    reason = protection_reason(dest)
    if reason is not None:
        raise RpcError("destination_protected", f"refusing to write into {dest}: {reason}")
    if dest.exists() and any(dest.iterdir()) and not params.get("overwrite"):
        raise RpcError("exists", f"{dest} is not empty")
    _progress("hood_save_as", 0, 2, "copying the hood folder")
    try:
        shutil.copytree(src, dest, dirs_exist_ok=True)
    except (OSError, shutil.Error) as exc:
        raise RpcError("write_failed", f"copy failed: {exc}") from None
    _progress("hood_save_as", 1, 2, "writing the neighborhood package")
    target = dest / session.path.name
    _write(session, target)
    _progress("hood_save_as", 2, 2)
    session.path = target
    session.readonly = False
    session.readonly_reason = ""
    session.dirty = False
    session.cache.pop("characters", None)
    return _summary(session)


def m_bhav_meta(session: Session, params: dict) -> dict:
    """Everything the BHAV editor needs to label things: primitive names,
    the operand layouts s2object has pinned, and the exit sentinels."""
    return {
        "primitives": {str(k): v for k, v in s2parser.PRIMITIVES.items()},
        "layouts": {str(k): v for k, v in s2object.BHAV_OPERAND_LAYOUTS.items()},
        "sentinels": {"true": 0xFFFD, "false": 0xFFFE, "error": 0xFFFC, "floor": s2object.BHAV_SENTINEL_FLOOR},
        "formats": {str(v): {"instr_size": l.instr_size, "operand_len": l.operand_len,
                             "addr_width": l.addr_width}
                    for v, l in s2object.BHAV_LAYOUTS.items()},
    }


def m_bhav_transform(session: Session, params: dict) -> dict:
    """Insert, delete, or move an instruction in a decoded BHAV the client
    holds as a draft. Pure: nothing in the package changes until the client
    applies the result with put_resource. Branch targets are renumbered
    here so the app never has to know what a target is."""
    b = from_json(_need(params, "decoded"))
    if not isinstance(b, s2object.BhavRes):
        raise RpcError("bad_params", "decoded is not a BhavRes")
    op = _need(params, "op")
    try:
        if op == "convert":
            s2object.bhav_convert(b, int(params.get("version", 0x8007)))
            warnings = []
        else:
            index = int(_need(params, "index"))
            if op == "insert":
                warnings = s2package.bhav_insert(b, index)
            elif op == "delete":
                warnings = s2package.bhav_delete(b, index)
            elif op == "move":
                warnings = s2package.bhav_move(b, index, int(_need(params, "to")))
            else:
                raise RpcError("bad_params", f"unknown op {op!r}")
    except ValueError as exc:
        raise RpcError("bad_params", str(exc)) from None
    return {"decoded": to_json(b), "warnings": warnings}


def m_shutdown(session: Session, params: dict) -> dict:
    return {"bye": True}


METHODS = {
    "open": m_open,
    "status": m_status,
    "index": m_index,
    "names": m_names,
    "meta": m_meta,
    "get_resource": m_get_resource,
    "render_bhav": m_render_bhav,
    "put_resource": m_put_resource,
    "add_resource": m_add_resource,
    "delete_resource": m_delete_resource,
    "rename_resource": m_rename_resource,
    "set_compressed": m_set_compressed,
    "undo": m_undo,
    "redo": m_redo,
    "save": m_save,
    "save_as": m_save_as,
    "export_resource": m_export_resource,
    "import_resource": m_import_resource,
    "bhav_meta": m_bhav_meta,
    "bhav_transform": m_bhav_transform,
    "objects": m_objects,
    "derive_guid": m_derive_guid,
    "clone": m_clone,
    "scan_guids": m_scan_guids,
    "merge": m_merge,
    "split": m_split,
    "doctor": m_doctor,
    "preview_texture": m_preview_texture,
    "export_texture": m_export_texture,
    "preview_mesh": m_preview_mesh,
    "hood_meta": m_hood_meta,
    "hood_sims": m_hood_sims,
    "hood_sim": m_hood_sim,
    "hood_put_sim": m_hood_put_sim,
    "hood_put_srel": m_hood_put_srel,
    "hood_put_tokens": m_hood_put_tokens,
    "hood_save_as": m_hood_save_as,
    "shutdown": m_shutdown,
}


# ---------------------------------------------------------------------------
# Server loop
# ---------------------------------------------------------------------------

def dispatch(session: Session, request: dict) -> dict:
    """Run one request and shape the response. Never raises."""
    rid = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}
    try:
        fn = METHODS.get(method)
        if fn is None:
            raise RpcError("unknown_method", f"unknown method {method!r}")
        if not isinstance(params, dict):
            raise RpcError("bad_params", "params must be an object")
        return {"id": rid, "result": fn(session, params)}
    except RpcError as exc:
        return {"id": rid, "error": {"code": exc.code, "message": exc.message, "data": exc.data}}
    except Exception as exc:  # noqa: BLE001 — the loop must survive anything
        return {"id": rid, "error": {"code": "internal", "message": f"{type(exc).__name__}: {exc}",
                                     "data": {"traceback": traceback.format_exc()}}}


def serve(stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    session = Session()

    def send(obj: dict) -> None:
        stdout.write(json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n")
        stdout.flush()

    global EMIT
    EMIT = send
    send({"event": "ready", "protocol": PROTOCOL_VERSION, "python": sys.version.split()[0]})
    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError as exc:
            send({"id": None, "error": {"code": "bad_json", "message": str(exc), "data": {}}})
            continue
        if not isinstance(request, dict):
            send({"id": None, "error": {"code": "bad_json", "message": "request must be an object", "data": {}}})
            continue
        send(dispatch(session, request))
        if request.get("method") == "shutdown":
            return 0
    return 0


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--serve", action="store_true",
                    help="speak JSON-RPC on stdin/stdout until shutdown or EOF")
    ap.add_argument("--check", metavar="PATH",
                    help="report whether PATH would open read-only, and why")
    args = ap.parse_args(argv)
    if args.check:
        reason = protection_reason(args.check)
        print(f"{args.check}: {'read-only — ' + reason if reason else 'writable'}")
        return 0
    if args.serve:
        return serve()
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
