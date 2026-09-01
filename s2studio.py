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

import s2doctor
import s2object
import s2package
import s2parser
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


def to_json(obj):
    """Dataclass -> {"$type": name, fields...}; bytes -> {"$hex": ...}."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        out = {"$type": type(obj).__name__}
        for f in dataclasses.fields(obj):
            out[f.name] = to_json(getattr(obj, f.name))
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
                return cls(**kwargs)
            except TypeError as exc:
                raise RpcError("bad_params", f"{cls.__name__}: {exc}") from None
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

    # -- history ----------------------------------------------------------

    def push(self, step: Step) -> None:
        self.undo.append(step)
        self.redo.clear()
        self.dirty = True
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
        return step

    def do_redo(self) -> Step:
        if not self.redo:
            raise RpcError("nothing_to_redo", "nothing to redo")
        step = self.redo.pop()
        for c in step.changes:
            self._apply(c, forward=True)
        self.undo.append(step)
        self.dirty = True
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
    try:
        header, entries = s2parser.open_package(path)
        version = (header.index_major_version, header.index_minor_version)
        with open(path, "rb") as f:
            directory = s2parser.read_dir(f, entries, version) or {}
        resources = s2writer.read_all_resources(path)
    except (OSError, ValueError, struct.error) as exc:
        raise RpcError("bad_package", f"cannot read {path.name}: {exc}") from None

    reason = protection_reason(path)
    if reason is None and not os.access(path, os.W_OK):
        reason = "file is not writable"
    return Session(
        path=path, readonly=reason is not None, readonly_reason=reason or "",
        header=header, resources=resources,
        compressed={k for k in directory if s2package.find(resources, k) >= 0},
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
        "size": len(r.data),
        "compressed": r.tgi() in session.compressed,
        "decodable": r.type_id in s2object.PARSERS,
        "bhav": r.type_id == s2object.TYPE_BHAV,
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
    names = {}
    for r in session.resources:
        if r.type_id == s2object.TYPE_BHAV and len(r.data) >= 64:
            names[r.instance_id] = r.data[:64].split(b"\x00", 1)[0].decode("latin-1")
    return names


def _render_bhav(session: Session, r: Resource) -> dict:
    try:
        b = s2parser.parse_bhav(r.data)
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


def m_index(session: Session, params: dict) -> list:
    session.require_open()
    return [_index_row(session, r) for r in session.resources]


def m_meta(session: Session, params: dict) -> dict:
    """Static tables the UI needs, served so Swift carries no format knowledge."""
    return {
        "protocol": PROTOCOL_VERSION,
        "type_names": {str(k): v for k, v in s2parser.TYPE_NAMES.items()},
        "decodable_types": sorted(s2object.PARSERS),
        "bhav_type": s2object.TYPE_BHAV,
        "objd_fields": s2object.OBJD_FIELDS,
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
    out = _index_row(session, r)
    out["hex"] = r.data.hex()
    out["decoded"] = None
    out["decode_error"] = None
    if r.type_id in s2object.PARSERS:
        try:
            out["decoded"] = to_json(s2object.parse_resource(r.type_id, r.data))
        except (ValueError, struct.error, IndexError) as exc:
            out["decode_error"] = str(exc)
    if r.type_id == s2object.TYPE_BHAV:
        out["bhav"] = _render_bhav(session, r)
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
        return {"size": len(data), "changed": False, **_summary(session)}
    n = s2package.find(session.resources, tgi)
    old = s2package.copy(r)
    r.data = data
    comp = tgi in session.compressed
    session.push(Step(params.get("label") or f"Edit {r.type_name}",
                      [Change(tgi, old, s2package.copy(r), n, comp, comp)]))
    return {"size": len(data), "changed": True, **_summary(session)}


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


def m_shutdown(session: Session, params: dict) -> dict:
    return {"bye": True}


METHODS = {
    "open": m_open,
    "status": m_status,
    "index": m_index,
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
