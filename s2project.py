#!/usr/bin/env python3
"""s2project.py — a Sim Studio object project, and how it becomes a .package.

A project is a folder, `Name.simobject/`, that the daemon owns:

    project.json     what the user chose: the base object, the identity
                     (name, description, price, categories, GUIDs), and the
                     TGIs of resources removed in the Advanced view
    overrides/       resources replaced or added in the Advanced view, one
                     file each, named <TYPE>-<group>-<instance>.bin

Building a project is deterministic: read the base package, copy the object
out (s2workshop.extract_object), renumber and re-identify it, then lay the
overrides over the result and re-assert the identity. The same project
always exports the same package, and a base package that changes under it
(a game patch) still yields a working object.

GUIDs come from a per-project uuid, never from the name: a renamed object
must keep its GUID or every copy already placed on a lot is orphaned.
"""

# Annotations stay strings so the module imports under the system
# python3 (3.9), which the app gets when launched from Finder.
from __future__ import annotations

import json
import shutil
import struct
import time
import uuid as _uuid
from dataclasses import dataclass, field
from pathlib import Path

import s2catalog
import s2clone
import s2package
import s2workshop
import s2writer
from s2workshop import Identity
from s2writer import Resource

EXTENSION = ".simobject"
PROJECT_FILE = "project.json"
OVERRIDES_DIR = "overrides"
VERSION = 1


@dataclass
class Base:
    source: str          # "game" or the absolute path of the donor package
    guid: int
    group: int = 0
    name: str = ""

    def to_json(self) -> dict:
        return {"source": self.source, "guid": self.guid, "group": self.group, "name": self.name}

    @classmethod
    def from_json(cls, d: dict) -> "Base":
        return cls(source=str(d.get("source", "game")), guid=int(d.get("guid", 0)),
                   group=int(d.get("group", 0)), name=str(d.get("name", "")))

    @property
    def path(self) -> Path:
        return s2catalog.OBJECTS_PACKAGE if self.source == "game" else Path(self.source)


@dataclass
class Project:
    dir: Path
    uuid: str
    base: Base
    identity: Identity
    deleted: "list[tuple[int, int, int, int]]" = field(default_factory=list)
    exported: "str | None" = None
    installed: "str | None" = None
    created: float = 0.0
    modified: float = 0.0
    warnings: "list[str]" = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.dir.name[:-len(EXTENSION)] if self.dir.name.endswith(EXTENSION) else self.dir.name

    @property
    def overrides_dir(self) -> Path:
        return self.dir / OVERRIDES_DIR

    def to_json(self) -> dict:
        return {
            "version": VERSION, "uuid": self.uuid,
            "base": self.base.to_json(), "identity": self.identity.to_json(),
            "deleted": [list(t) for t in self.deleted],
            "exported": self.exported, "installed": self.installed,
            "created": self.created, "modified": self.modified,
        }

    def summary(self) -> dict:
        """What the app shows: the JSON plus the paths and name."""
        d = self.to_json()
        d.update({"path": str(self.dir), "name": self.name, "warnings": list(self.warnings)})
        return d


def load(directory: Path) -> Project:
    directory = Path(directory)
    text = (directory / PROJECT_FILE).read_text(encoding="utf-8")
    d = json.loads(text)
    if int(d.get("version", 1)) > VERSION:
        raise ValueError(f"project version {d['version']} is newer than this app understands")
    return Project(
        dir=directory, uuid=str(d.get("uuid") or _uuid.uuid4()),
        base=Base.from_json(d.get("base") or {}),
        identity=Identity.from_json(d.get("identity") or {}),
        deleted=[tuple(int(x) for x in t) for t in d.get("deleted", [])],
        exported=d.get("exported"), installed=d.get("installed"),
        created=float(d.get("created") or 0), modified=float(d.get("modified") or 0),
    )


def new(directory: Path, base: Base, identity: Identity) -> Project:
    """A project that does not exist on disk yet; `build` fills in the GUIDs."""
    return Project(dir=Path(directory), uuid=str(_uuid.uuid4()), base=base,
                   identity=identity, created=time.time(), modified=time.time())


def _override_name(tgi) -> str:
    t, g, i, hi = tgi
    return f"{t:08X}-{g:08X}-{i:08X}-{hi:08X}.bin"


def _parse_override_name(name: str) -> "tuple[int, int, int, int] | None":
    stem = name[:-4] if name.endswith(".bin") else name
    parts = stem.split("-")
    if len(parts) != 4:
        return None
    try:
        return tuple(int(x, 16) for x in parts)   # type: ignore[return-value]
    except ValueError:
        return None


def _clean_build(project: Project) -> "tuple[list[Resource], list[str]]":
    """The object as the base and the identity make it, before overrides."""
    path = project.base.path
    if not path.is_file():
        raise FileNotFoundError(f"the base package is missing: {path}")
    whole = project.base.source != "game"
    if whole:
        donor = s2writer.read_all_resources(path)
    else:
        _h, donor, _c = s2package.read_lazy(path)
    ext = s2workshop.extract_object(donor, project.base.guid, whole_package=whole)
    objects = ext.objects
    guids = dict(project.identity.guids)
    if not guids:
        guids = s2workshop.new_guids(objects, project.uuid)
        project.identity.guids = dict(guids)
    ident = project.identity
    warnings = s2workshop.reidentify(
        ext, guids=guids, name=ident.name, description=ident.description,
        price=ident.price, room_flags=ident.room_flags, function_flags=ident.function_flags)
    master = s2workshop.master_object(ext.objects, ext.resources)
    ident.guid = master.guid
    return ext.resources, warnings


def build(project: Project) -> "list[Resource]":
    """The project's package contents: clean build, then overrides and
    deletions, then the identity re-asserted over any raw-edited OBJD."""
    resources, warnings = _clean_build(project)
    project.warnings = warnings
    if project.overrides_dir.is_dir():
        for f in sorted(project.overrides_dir.iterdir()):
            tgi = _parse_override_name(f.name)
            if tgi is None:
                continue
            data = f.read_bytes()
            n = s2package.find(resources, tgi)
            if n >= 0:
                resources[n].data = data
            else:
                resources.append(Resource(tgi[0], tgi[1], tgi[2], data, tgi[3]))
    for tgi in project.deleted:
        n = s2package.find(resources, tgi)
        if n >= 0:
            resources.pop(n)
    try:
        s2workshop.apply_identity(resources, project.identity)
    except ValueError:
        project.warnings.append("the object definition was removed; identity not applied")
    return resources


def save(project: Project, resources: "list[Resource]") -> None:
    """Write the bundle: project.json, and as overrides every resource that
    differs from a clean build (added or changed); removed ones go to
    `deleted`. Rebuilt from the base each time, so no bookkeeping during
    editing and the undo stack covers everything."""
    project.identity = s2workshop.read_identity(resources)
    clean, _warnings = _clean_build(project)
    clean_by = {r.tgi(): r for r in clean}
    now_by = {r.tgi(): r for r in resources}
    project.deleted = sorted(t for t in clean_by if t not in now_by)
    project.modified = time.time()
    project.dir.mkdir(parents=True, exist_ok=True)
    overrides = project.overrides_dir
    if overrides.is_dir():
        shutil.rmtree(overrides)
    for tgi, r in now_by.items():
        c = clean_by.get(tgi)
        if c is not None and c.data == r.data:
            continue
        overrides.mkdir(parents=True, exist_ok=True)
        (overrides / _override_name(tgi)).write_bytes(r.data)
    tmp = project.dir / (PROJECT_FILE + ".tmp")
    tmp.write_text(json.dumps(project.to_json(), indent=2), encoding="utf-8")
    tmp.replace(project.dir / PROJECT_FILE)


def export(project: Project, resources: "list[Resource]", dest: Path) -> Path:
    """Write the package the game will load. Compressed, as custom content
    usually is; written beside its final name and renamed into place."""
    dest = Path(dest)
    tmp = dest.with_name(dest.name + ".tmp")
    s2writer.write_package(tmp, resources, compress=True)
    tmp.replace(dest)
    project.exported = str(dest)
    return dest


def install(project: Project, exported: Path, game_root: Path, *, replace: bool = False) -> Path:
    """Copy an exported package into the game's Downloads folder."""
    downloads = Path(game_root) / "Downloads"
    if not downloads.is_dir():
        raise FileNotFoundError(f"no Downloads folder at {downloads}")
    dest = downloads / Path(exported).name
    if dest.exists() and not replace:
        raise FileExistsError(f"{dest.name} is already installed; replace it?")
    shutil.copyfile(exported, dest)
    project.installed = str(dest)
    return dest
