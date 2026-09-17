#!/usr/bin/env python3
"""s2project.py — a Sim Studio object project, and how it becomes a .package.

A project is a folder, `Name.simobject/`, that the daemon owns:

    project.json     what the user chose: the kind of project, the base
                     object, the identity (name, description, price,
                     categories, GUIDs), the looks (colour options: a picture
                     or a tint per subset), and the TGIs of resources removed
                     in the Advanced view
    looks/<id>/      the pictures a look imports, one PNG per subset
    cache/           encoded textures, keyed by what made them; rebuilt on a miss
    overrides/       resources replaced or added in the Advanced view, one
                     file each, named <TYPE>-<group>-<instance>.bin

Two kinds of project. An "object" is a clone: the base object copied out,
renumbered and re-identified, with its looks added as colour options. A
"recolour" adds a colour option to the base object itself and exports only
the three resources of a classic recolour package (MMAT, TXMT, TXTR).

Building a project is deterministic: read the base package, copy the object
out (s2workshop.extract_object), renumber and re-identify it, render the
looks (s2looks.render), then lay the overrides over the result and
re-assert the identity. The same project always exports the same package,
and a base package that changes under it (a game patch) still yields a
working object.

GUIDs come from a per-project uuid, never from the name: a renamed object
must keep its GUID or every copy already placed on a lot is orphaned.
"""

# Annotations stay strings so the module imports under the system
# python3 (3.9), which the app gets when launched from Finder.
from __future__ import annotations

import hashlib
import json
import shutil
import struct
import time
import uuid as _uuid
from dataclasses import dataclass, field
from pathlib import Path

import s2catalog
import s2clone
import s2looks
import s2package
import s2workshop
import s2writer
from s2looks import Looks
from s2workshop import Identity
from s2writer import Resource

EXTENSION = ".simobject"
PROJECT_FILE = "project.json"
OVERRIDES_DIR = "overrides"
LOOKS_DIR = "looks"
CACHE_DIR = "cache"
VERSION = 2
KIND_OBJECT = "object"
KIND_RECOLOUR = "recolour"


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
    kind: str = KIND_OBJECT
    looks: Looks = field(default_factory=Looks)
    deleted: "list[tuple[int, int, int, int]]" = field(default_factory=list)
    exported: "str | None" = None
    installed: "str | None" = None
    created: float = 0.0
    modified: float = 0.0
    warnings: "list[str]" = field(default_factory=list)
    model: str = ""                    # filled in by the last build

    @property
    def name(self) -> str:
        return self.dir.name[:-len(EXTENSION)] if self.dir.name.endswith(EXTENSION) else self.dir.name

    @property
    def overrides_dir(self) -> Path:
        return self.dir / OVERRIDES_DIR

    @property
    def looks_dir(self) -> Path:
        return self.dir / LOOKS_DIR

    @property
    def cache_dir(self) -> Path:
        return self.dir / CACHE_DIR

    @property
    def is_recolour(self) -> bool:
        return self.kind == KIND_RECOLOUR

    def read_file(self, relative: str) -> bytes:
        """A file inside the bundle, by the path project.json uses."""
        path = (self.dir / relative).resolve()
        if self.dir.resolve() not in path.parents:
            raise ValueError(f"{relative!r} is outside the project")
        return path.read_bytes()

    def to_json(self) -> dict:
        return {
            "version": VERSION, "uuid": self.uuid, "kind": self.kind,
            "base": self.base.to_json(), "identity": self.identity.to_json(),
            "looks": self.looks.to_json(),
            "deleted": [list(t) for t in self.deleted],
            "exported": self.exported, "installed": self.installed,
            "created": self.created, "modified": self.modified,
        }

    def summary(self) -> dict:
        """What the app shows: the JSON plus the paths and name."""
        d = self.to_json()
        d.update({"path": str(self.dir), "name": self.name, "model": self.model,
                  "warnings": list(self.warnings)})
        return d


def load(directory: Path) -> Project:
    directory = Path(directory)
    text = (directory / PROJECT_FILE).read_text(encoding="utf-8")
    d = json.loads(text)
    if int(d.get("version", 1)) > VERSION:
        raise ValueError(f"project version {d['version']} is newer than this app understands")
    kind = str(d.get("kind") or KIND_OBJECT)
    if kind not in (KIND_OBJECT, KIND_RECOLOUR):
        raise ValueError(f"unknown project kind {kind!r}")
    return Project(
        dir=directory, uuid=str(d.get("uuid") or _uuid.uuid4()), kind=kind,
        base=Base.from_json(d.get("base") or {}),
        identity=Identity.from_json(d.get("identity") or {}),
        looks=Looks.from_json(d.get("looks")),
        deleted=[tuple(int(x) for x in t) for t in d.get("deleted", [])],
        exported=d.get("exported"), installed=d.get("installed"),
        created=float(d.get("created") or 0), modified=float(d.get("modified") or 0),
    )


def new(directory: Path, base: Base, identity: Identity, kind: str = KIND_OBJECT) -> Project:
    """A project that does not exist on disk yet; `build` fills in the GUIDs."""
    if kind not in (KIND_OBJECT, KIND_RECOLOUR):
        raise ValueError(f"unknown project kind {kind!r}")
    return Project(dir=Path(directory), uuid=str(_uuid.uuid4()), base=base,
                   identity=identity, kind=kind, created=time.time(), modified=time.time())


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


def _donor(project: Project) -> "tuple[s2workshop.Extraction, bool]":
    """The base object copied out of its package, untouched."""
    path = project.base.path
    if not path.is_file():
        raise FileNotFoundError(f"the base package is missing: {path}")
    whole = project.base.source != "game"
    if whole:
        donor = s2writer.read_all_resources(path)
    else:
        _h, donor, _c = s2package.read_lazy(path)
    return s2workshop.extract_object(donor, project.base.guid, whole_package=whole), whole


def inventory(project: Project, resources: "list[Resource] | None" = None) -> s2looks.Inventory:
    """What the base model allows a look to change. `resources` is the
    built package when there is one (a custom base carries its own scene
    resources); otherwise the base is copied out to find the model."""
    if resources is None or not project.model:
        ext, _whole = _donor(project)
        local = ext.resources
        project.model = s2looks.model_name(local)
    else:
        local = resources
    originals = [int(g) for g in project.identity.guids] or [project.base.guid]
    if project.base.guid not in originals:
        originals.insert(0, project.base.guid)
    if project.is_recolour:
        originals += [g for g in s2looks.model_guids(project.model) if g not in originals]
    return s2looks.inventory(project.model, originals, local if project.base.source != "game" else None)


def render_looks(project: Project, resources: "list[Resource]", progress=None
                 ) -> "tuple[list[Resource], list[str]]":
    """The resources the project's looks add to `resources` (a clean build
    without them, or the session's package with the old ones removed)."""
    if not project.looks.items and (project.is_recolour or not project.looks.keep_game_options):
        return [], []
    inv = inventory(project, resources)
    warnings = list(inv.warnings)
    if project.is_recolour:
        # A colour option for the base object, and for every object the
        # game dresses with the same subsets (a counter and its island twin).
        originals = {project.base.guid}
        for s in inv.subsets:
            originals.update(s.guids)
        guid_map = {g: g for g in sorted(originals)}
    else:
        guid_map = {int(k): int(v) for k, v in project.identity.guids.items()}
    if not guid_map:
        return [], warnings
    local = resources if project.base.source != "game" else None
    return s2looks.render(
        project.uuid, project.model, project.looks, inv, guid_map,
        read_file=project.read_file, cache_dir=project.cache_dir, local=local,
        progress=progress), warnings


def _render_looks(project: Project, resources: "list[Resource]", warnings: "list[str]",
                  progress=None) -> None:
    added, notes = render_looks(project, resources, progress)
    resources.extend(added)
    warnings.extend(notes)


def _clean_build(project: Project, progress=None) -> "tuple[list[Resource], list[str]]":
    """The object as the base, the identity and the looks make it, before
    overrides. A recolour project is only its looks."""
    ext, _whole = _donor(project)
    project.model = s2looks.model_name(ext.resources)
    warnings: "list[str]" = []
    if project.is_recolour:
        resources: "list[Resource]" = []
        _render_looks(project, resources, warnings, progress)
        return resources, warnings
    objects = ext.objects
    guids = dict(project.identity.guids)
    if not guids:
        guids = s2workshop.new_guids(objects, project.uuid)
        project.identity.guids = dict(guids)
    ident = project.identity
    # What the identity leaves unsaid, the base object supplies. Blank
    # category flags in particular would drop the object out of the catalog
    # and change which OBJD counts as the master.
    try:
        donor = s2workshop.read_identity(ext.resources)
        if not ident.name.strip():
            ident.name = donor.name
        if not ident.description:
            ident.description = donor.description
        if not ident.function_flags:
            ident.function_flags = donor.function_flags
        if not ident.room_flags:
            ident.room_flags = donor.room_flags
    except (ValueError, struct.error):
        pass
    warnings = s2workshop.reidentify(
        ext, guids=guids, name=ident.name, description=ident.description,
        price=ident.price, room_flags=ident.room_flags, function_flags=ident.function_flags)
    master = s2workshop.master_object(ext.objects, ext.resources)
    ident.guid = master.guid
    # Normalise the identity to what the resources now say, so a partial
    # identity (no description, say) reads back the same after a save and
    # the clean build the save diffs against does not differ from the session.
    try:
        current = s2workshop.read_identity(ext.resources)
        ident.name, ident.description = current.name, current.description
        ident.price, ident.room_flags, ident.function_flags = current.price, current.room_flags, current.function_flags
    except (ValueError, struct.error):
        pass
    _render_looks(project, ext.resources, warnings, progress)
    return ext.resources, warnings


def build(project: Project, progress=None) -> "list[Resource]":
    """The project's package contents: clean build, then overrides and
    deletions, then the identity re-asserted over any raw-edited OBJD."""
    resources, warnings = _clean_build(project, progress)
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
    if not project.is_recolour:
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
    if not project.is_recolour:
        project.identity = s2workshop.read_identity(resources)
    _write_pictures(project)
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


def _write_pictures(project: Project) -> None:
    """Put every look's pictures on disk under looks/<id>/ and drop the
    folders of looks that no longer exist."""
    keep: "set[str]" = set()
    for look in project.looks.items:
        keep.add(look.id)
        for subset, src in look.subsets.items():
            if not isinstance(src, s2looks.PictureSource):
                continue
            rel = f"{LOOKS_DIR}/{look.id}/{_safe_name(subset)}.png"
            if src.png is not None:
                dest = project.dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_name(dest.name + ".tmp")
                tmp.write_bytes(src.png)
                tmp.replace(dest)
                src.file = rel
                src.sha1 = hashlib.sha1(src.png).hexdigest()
    if project.looks_dir.is_dir():
        for child in project.looks_dir.iterdir():
            if child.is_dir() and child.name not in keep:
                shutil.rmtree(child, ignore_errors=True)


def _safe_name(text: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in text) or "subset"


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
