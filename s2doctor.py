#!/usr/bin/env python3
"""s2doctor.py — diagnose Sims 2 freezes, glitches, and mod conflicts.

Reads the game's own error logs, scans Downloads for damaged packages and
overlapping overrides, cross-references the two, and ranks what it finds.

Strictly read-only: it never writes, moves, or deletes a single game file.
It only prints what it found and the command you would run to act on it.
"""

# Annotations stay strings so the module imports under the system
# python3 (3.9), the same constraint the rest of the toolkit works under.
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import s2parser

# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

# The App Store / Super Collection build sandboxes the user folder; the older
# retail Aspyr build and the Windows-style layout put it in the open.
ROOT_CANDIDATES = [
    Path.home() / "Library/Containers/com.aspyr.sims2.appstore/Data"
                  "/Library/Application Support/Aspyr/The Sims 2",
    Path.home() / "Library/Application Support/Aspyr/The Sims 2",
    Path.home() / "Documents/EA Games/The Sims 2",
    Path.home() / "Documents/The Sims 2",
]

CRASH_REPORT_DIR = Path.home() / "Library/Logs/DiagnosticReports"

# ---------------------------------------------------------------------------
# Resource types that mods fight over. Two packages supplying the same TGI in
# one of these types means one silently loses — the usual shape of a hack
# conflict. Mesh and texture types are deliberately excluded: duplicate TGIs
# there are common, mostly harmless, and would bury the real findings.
# ---------------------------------------------------------------------------

CONFLICT_TYPES = {
    0x42484156: "BHAV",  # behaviour script
    0x42434F4E: "BCON",  # tuning constants
    0x54544142: "TTAB",  # interaction (pie menu) table
    0x54544173: "TTAs",  # interaction strings
    0x4F424A66: "OBJf",  # function table
    0x4F424A44: "OBJD",  # object definition
    0x474C4F42: "GLOB",  # semi-global reference
    0x53545223: "STR#",  # text
    0x43545353: "CTSS",  # catalogue text
    0x534C4F54: "SLOT",  # slots
}

# Group id shared by the game's global behaviour scripts. A package writing
# here is overriding core game logic for every object at once.
GLOBAL_GROUP = 0x7FD46CD0

# A package's own private resources are indexed under this placeholder group;
# the game resolves it to that package's real group at load time. Two packages
# both holding BHAV 0x1000 in this group are not in conflict — every custom
# object has a BHAV 0x1000. Treating these as clashes buries the real ones.
LOCAL_GROUP = 0xFFFFFFFF

# Extensions the game will not load. Anything else in Downloads is dead weight
# at best, and a half-finished download at worst.
INERT_SUFFIXES = {".rar", ".zip", ".7z", ".sims2pack", ".html", ".htm", ".txt",
                  ".pdf", ".doc", ".docx", ".part", ".crdownload", ".download"}

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}
SEVERITY_LABEL = {"critical": "CRITICAL", "warning": "WARNING ", "info": "INFO    "}


@dataclass
class Finding:
    severity: str
    code: str
    title: str
    detail: "list[str]" = field(default_factory=list)
    fix: "str | None" = None

    def to_dict(self) -> dict:
        return {"severity": self.severity, "code": self.code, "title": self.title,
                "detail": self.detail, "fix": self.fix}


# ---------------------------------------------------------------------------
# Game error logs
# ---------------------------------------------------------------------------

RE_OBJECT_ERROR_NAME = re.compile(r"ObjectError_([A-Za-z0-9]+)_t(\d+)\.txt$")
RE_ERROR_LINE = re.compile(r"^Error:\s*(.+?)\s*$", re.M)
RE_OBJ_NAME = re.compile(r"^name:\s*(.+?)\s*$", re.M)
RE_ITERATIONS = re.compile(r"^Iterations:\s*(\d+)", re.M)
RE_FRAME = re.compile(
    r"^\s*Tree: id (\d+) name '(.*)' version (-?\d+)\s*\n\s*from (.+?)\s*$", re.M)


@dataclass
class ObjectError:
    path: Path
    hood: str
    tick: int
    mtime: float
    error: str
    object_name: str
    iterations: int
    frames: "list[tuple[int, str, str]]"  # (tree id, tree name, group name)


def parse_object_error(path: Path) -> "ObjectError | None":
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None

    m = RE_OBJECT_ERROR_NAME.search(path.name)
    hood, tick = (m.group(1), int(m.group(2))) if m else ("?", 0)

    err = RE_ERROR_LINE.search(text)
    name = RE_OBJ_NAME.search(text)
    iters = RE_ITERATIONS.search(text)
    frames = [(int(t), n, g) for t, n, _v, g in RE_FRAME.findall(text)]

    return ObjectError(
        path=path,
        hood=hood,
        tick=tick,
        mtime=path.stat().st_mtime,
        error=err.group(1) if err else "(no Error: line)",
        object_name=name.group(1) if name else "(unnamed)",
        iterations=int(iters.group(1)) if iters else 0,
        frames=frames,
    )


def scan_object_errors(logs_dir: Path) -> "list[ObjectError]":
    if not logs_dir.is_dir():
        return []
    out = []
    for p in sorted(logs_dir.glob("ObjectError_*.txt")):
        parsed = parse_object_error(p)
        if parsed:
            out.append(parsed)
    out.sort(key=lambda e: e.mtime, reverse=True)
    return out


def check_object_errors(errors: "list[ObjectError]", recent: int) -> "list[Finding]":
    if not errors:
        return [Finding("info", "no-object-errors",
                        "No ObjectError logs — the simulator has not thrown a "
                        "scripting error it could catch.")]

    findings = []
    by_message = Counter(e.error for e in errors)
    by_object = Counter(e.object_name for e in errors)

    newest = errors[0]
    detail = [
        f"most recent: {newest.path.name}  ({_ts(newest.mtime)})",
        f"  error:  {newest.error}",
        f"  object: {newest.object_name}",
    ]
    if newest.iterations > 1:
        detail.append(f"  iterations: {newest.iterations}  "
                      f"(the same error fired repeatedly — a loop, not a one-off)")
    if newest.frames:
        detail.append("  stack (innermost last):")
        for tree_id, tree_name, group in reversed(newest.frames):
            detail.append(f"    tree {tree_id:<6} {tree_name!r} from {group}")

    findings.append(Finding("warning", "object-errors",
                            f"{len(errors)} ObjectError log(s) — the simulator caught "
                            f"a script fault this many times.", detail))

    if len(by_message) < len(errors):
        lines = [f"{n:>3} x  {msg}" for msg, n in by_message.most_common(recent)]
        findings.append(Finding("warning", "repeat-errors",
                                "Errors that recur — a repeating fault is a mod or a "
                                "damaged object, not bad luck.", lines))

    lines = [f"{n:>3} x  {obj}" for obj, n in by_object.most_common(recent) if n > 1]
    if lines:
        findings.append(Finding("warning", "repeat-offenders",
                                "Objects that errored more than once.", lines,
                                fix="Remove or replace these objects in-game, then "
                                    "see whether the errors stop."))
    return findings


RE_APP_ERROR = re.compile(r"^ERROR\s+(\S+)\s*:\s*(.+?)\s*$", re.M)


def check_app_errors(logs_dir: Path) -> "list[Finding]":
    findings = []
    for name, severity in (("AppErrors.log", "warning"), ("AudioErrors.log", "info")):
        path = logs_dir / name
        if not path.is_file() or path.stat().st_size == 0:
            continue
        text = path.read_text(errors="replace")
        # AudioErrors.log wraps each record in braces; AppErrors.log does not.
        msgs = RE_APP_ERROR.findall(text)
        if not msgs:
            msgs = [(m.group(1), m.group(2)) for m in
                    re.finditer(r"ERROR,(\w+),(.+?)\s*\},?$", text, re.M)]
        if not msgs:
            continue
        counts = Counter(f"{sub}: {msg}" for sub, msg in msgs)
        lines = [f"{n:>4} x  {msg}" for msg, n in counts.most_common(8)]
        findings.append(Finding(severity, f"log-{path.stem.lower()}",
                                f"{name}: {len(msgs)} error line(s), "
                                f"{len(counts)} distinct.", lines))
    return findings


def check_crash_reports(limit: int = 5) -> "list[Finding]":
    """macOS crash reports for the game — a hard crash, not a caught error."""
    if not CRASH_REPORT_DIR.is_dir():
        return []
    reports = sorted(CRASH_REPORT_DIR.glob("Sims2*.ips"),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    if not reports:
        return []

    lines = []
    for p in reports[:limit]:
        summary = ""
        try:
            with open(p, "r", errors="replace") as f:
                head = json.loads(f.readline())
                body = json.loads(f.read())
            reason = (body.get("termination", {}) or {}).get("indicator") or ""
            exc = (body.get("exception", {}) or {}).get("type") or ""
            summary = f"  {head.get('app_version', '?')}  {exc} {reason}".rstrip()
        except (ValueError, OSError):
            pass
        lines.append(f"{_ts(p.stat().st_mtime)}  {p.name}{summary}")

    return [Finding("critical", "crash-reports",
                    f"{len(reports)} macOS crash report(s) for the game — these are "
                    f"hard crashes, distinct from in-game errors.", lines,
                    fix="A crash right after a freeze usually means the freeze and "
                        "the crash share a cause; check what the game was loading.")]


# ---------------------------------------------------------------------------
# Downloads folder
# ---------------------------------------------------------------------------

@dataclass
class PackageInfo:
    path: Path
    size: int
    sha1: str = ""
    error: str = ""
    entries: list = field(default_factory=list)
    # (type, group, instance) with the instance resolved for the index version
    resources: "list[tuple[int, int, int]]" = field(default_factory=list)
    guids: "list[tuple[int, str]]" = field(default_factory=list)  # (guid, obj name)


def sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_objd_guids(path: Path, entries: list) -> "list[tuple[int, str]]":
    """GUIDs of every object this package defines.

    OBJD layout is 64 bytes of filename then u16 fields; words 14/15 are the
    GUID low/high halves, so the u32 sits at byte 64 + 28.
    """
    out = []
    objds = [e for e in entries if e.type_id == 0x4F424A44]
    if not objds:
        return out
    try:
        with open(path, "rb") as f:
            for e in objds:
                try:
                    data = s2parser.read_resource(f, e)
                except Exception:
                    continue
                if len(data) < 96:
                    continue
                guid = struct.unpack_from("<I", data, 64 + 28)[0]
                name = data[:64].split(b"\0")[0].decode("latin-1", "replace")
                if guid:
                    out.append((guid, name))
    except OSError:
        pass
    return out


def scan_packages(folder: Path, hash_files: bool = True) -> "list[PackageInfo]":
    infos = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() != ".package":
            continue

        size = path.stat().st_size
        info = PackageInfo(path=path, size=size)
        if size == 0:
            info.error = "zero bytes — an empty or interrupted download"
            infos.append(info)
            continue

        try:
            header, entries = s2parser.open_package(path)
            info.entries = entries
            # An index that points past the end of the file means the download
            # was truncated, which is exactly what hangs the game on load.
            bad = [e for e in entries if e.offset + e.size > size or e.offset < 0]
            if bad:
                info.error = (f"index points past end of file "
                              f"({len(bad)} of {len(entries)} entries) — truncated")
            elif header.index_entry_count == 0:
                info.error = "no resources — the package is empty"
            else:
                # e.instance is the real instance for either index version —
                # reading a raw field turns every package's private BHAV
                # 0x1000 into instance 0 and makes unrelated mods collide.
                info.resources = [(e.type_id, e.group_id, e.instance) for e in entries]
                info.guids = read_objd_guids(path, entries)
        except Exception as exc:  # a damaged file can fail in many ways
            info.error = f"unreadable: {exc}"

        if hash_files and not info.error:
            try:
                info.sha1 = sha1_of(path)
            except OSError:
                pass
        infos.append(info)
    return infos


def check_downloads_integrity(infos: "list[PackageInfo]") -> "list[Finding]":
    broken = [i for i in infos if i.error]
    if not broken:
        return [Finding("info", "packages-ok",
                        f"All {len(infos)} package(s) in Downloads parse cleanly.")]
    lines = [f"{_rel(i.path)}\n        {i.error}" for i in broken]
    return [Finding("critical", "corrupt-packages",
                    f"{len(broken)} damaged package(s) — the top suspect for a "
                    f"freeze at load or a hang on a fixed step.", lines,
                    fix="Move these out of Downloads and start the game. If it "
                        "loads, re-download them one at a time.")]


def check_inert_files(folder: Path) -> "list[Finding]":
    hits = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() in INERT_SUFFIXES:
            hits.append(path)
    if not hits:
        return []
    lines = [f"{_rel(p)}  ({_size(p.stat().st_size)})" for p in hits[:20]]
    if len(hits) > 20:
        lines.append(f"... and {len(hits) - 20} more")
    return [Finding("info", "inert-files",
                    f"{len(hits)} file(s) in Downloads the game cannot load.", lines,
                    fix="Harmless to the game, but an unextracted archive means a "
                        "mod you think is installed is not.")]


def check_duplicates(infos: "list[PackageInfo]") -> "list[Finding]":
    by_hash = defaultdict(list)
    for i in infos:
        if i.sha1:
            by_hash[i.sha1].append(i)
    dupes = {h: v for h, v in by_hash.items() if len(v) > 1}
    if not dupes:
        return []

    wasted = sum(v[0].size * (len(v) - 1) for v in dupes.values())
    lines = []
    for _h, group in sorted(dupes.items(), key=lambda kv: -kv[1][0].size)[:15]:
        lines.append(f"{_size(group[0].size)} x{len(group)}: " +
                     ", ".join(_rel(g.path) for g in group))
    if len(dupes) > 15:
        lines.append(f"... and {len(dupes) - 15} more duplicate sets")

    return [Finding("warning", "duplicate-files",
                    f"{len(dupes)} set(s) of byte-identical packages, "
                    f"{_size(wasted)} wasted — every copy loads, and copies of the "
                    f"same object conflict with each other.", lines,
                    fix="Keep one of each; delete the rest.")]


def check_tgi_conflicts(infos: "list[PackageInfo]", top: int) -> "list[Finding]":
    """Two packages supplying the same overridable resource — one silently wins."""
    by_tgi = defaultdict(set)
    for i in infos:
        if i.error:
            continue
        for type_id, group_id, inst in i.resources:
            if type_id in CONFLICT_TYPES and group_id != LOCAL_GROUP:
                by_tgi[(type_id, group_id, inst)].add(i.path)

    clashes = {k: v for k, v in by_tgi.items() if len(v) > 1}
    if not clashes:
        return [Finding("info", "no-tgi-conflicts",
                        "No two packages override the same behaviour resource.")]

    # Group by the set of packages involved: one pair of fighting mods usually
    # collides on dozens of resources, and that is one finding, not dozens.
    by_pkgset = defaultdict(list)
    for (type_id, group_id, inst), paths in clashes.items():
        by_pkgset[frozenset(paths)].append((type_id, group_id, inst))

    findings = []
    globals_hit = [k for k in clashes if k[1] == GLOBAL_GROUP]

    ranked = sorted(by_pkgset.items(), key=lambda kv: -len(kv[1]))
    lines = []
    for pkgs, res in ranked[:top]:
        types = Counter(CONFLICT_TYPES[t] for t, _g, _i in res)
        type_str = ", ".join(f"{n} {t}" for t, n in types.most_common())
        lines.append(f"{len(res):>4} shared resource(s) [{type_str}]")
        for p in sorted(pkgs):
            lines.append(f"       {_rel(p)}")
    if len(ranked) > top:
        lines.append(f"... and {len(ranked) - top} more conflicting sets")

    findings.append(Finding("warning", "tgi-conflicts",
                            f"{len(clashes)} resource(s) supplied by more than one "
                            f"package, across {len(ranked)} set(s) of mods.", lines,
                            fix="Only one version loads and which one wins is "
                                "load-order luck. Pick one mod per row."))

    if globals_hit:
        pkgs = sorted({p for k in globals_hit for p in clashes[k]})
        findings.append(Finding("critical", "global-overrides",
                                f"{len(globals_hit)} of those clashes are in the "
                                f"global group (0x{GLOBAL_GROUP:08X}) — core game "
                                f"logic, overridden by more than one mod at once.",
                                [_rel(p) for p in pkgs],
                                fix="Global conflicts affect every sim and every "
                                    "object. Test with all but one removed."))
    return findings


def check_guid_conflicts(infos: "list[PackageInfo]", top: int) -> "list[Finding]":
    by_guid = defaultdict(list)
    for i in infos:
        for guid, name in i.guids:
            by_guid[guid].append((i.path, name))

    clashes = {g: v for g, v in by_guid.items()
               if len({p for p, _n in v}) > 1}
    if not clashes:
        return []

    lines = []
    for guid, entries in sorted(clashes.items(), key=lambda kv: -len(kv[1]))[:top]:
        lines.append(f"GUID 0x{guid:08X}  ({entries[0][1]})")
        for path in sorted({p for p, _n in entries}):
            lines.append(f"       {_rel(path)}")
    if len(clashes) > top:
        lines.append(f"... and {len(clashes) - top} more")

    return [Finding("warning", "guid-conflicts",
                    f"{len(clashes)} object GUID(s) claimed by more than one "
                    f"package — two mods defining the same object.", lines,
                    fix="One definition wins; the loser's objects can end up "
                        "invisible, unusable, or stuck mid-interaction.")]


# ---------------------------------------------------------------------------
# Cross-reference: the trees in the crash stack against installed mods
# ---------------------------------------------------------------------------

def check_error_mod_overlap(errors: "list[ObjectError]",
                            infos: "list[PackageInfo]",
                            recent: int) -> "list[Finding]":
    if not errors or not infos:
        return []

    # Only global-group trees can be matched with confidence. The log names the
    # owning group ("global", "ToiletGlobals", "NPC_GrimReaper") but packages
    # carry numeric group ids, and only the global group's id is known without
    # the game's own resources. A frame that says "from global" is a tree in
    # GLOBAL_GROUP; a mod supplying that same instance there is overriding the
    # exact code that failed. Semi-global frames are skipped rather than guessed
    # at — instance ids repeat across groups, so matching on instance alone
    # accuses every custom object of every error.
    global_owners = defaultdict(set)
    for i in infos:
        if i.error:
            continue
        for type_id, group_id, inst in i.resources:
            if type_id == 0x42484156 and group_id == GLOBAL_GROUP:
                global_owners[inst].add(i.path)

    if not global_owners:
        return []

    hits = defaultdict(set)   # tree id -> packages
    context = {}              # tree id -> (name, group)
    for err in errors[:recent]:
        for tree_id, tree_name, group in err.frames:
            if group.strip().lower() != "global":
                continue
            if tree_id in global_owners:
                hits[tree_id] |= global_owners[tree_id]
                context.setdefault(tree_id, (tree_name, group))

    if not hits:
        return [Finding("info", "no-error-mod-overlap",
                        f"No global behaviour tree in the "
                        f"{min(recent, len(errors))} most recent error stack(s) is "
                        f"overridden by a package in Downloads.")]

    lines = []
    for tree_id, paths in sorted(hits.items(), key=lambda kv: -len(kv[1])):
        name, group = context[tree_id]
        lines.append(f"tree {tree_id} {name!r} (from {group})")
        for p in sorted(paths):
            lines.append(f"       {_rel(p)}")

    return [Finding("critical", "error-mod-overlap",
                    f"{len(hits)} global behaviour tree(s) in your recent error "
                    f"stacks are overridden by an installed package.", lines,
                    fix="Strongest lead available: a mod replaced the exact core "
                        "routine that failed. Test with these moved out.")]


# ---------------------------------------------------------------------------
# Caches and neighborhoods
# ---------------------------------------------------------------------------

def check_caches(root: Path) -> "list[Finding]":
    targets = list(root.glob("*.cache"))
    thumbs = root / "Thumbnails"
    if thumbs.is_dir():
        targets += list(thumbs.glob("*.package"))
    targets = [p for p in targets if p.is_file()]
    if not targets:
        return []

    total = sum(p.stat().st_size for p in targets)
    lines = [f"{_size(p.stat().st_size):>10}  {_ts(p.stat().st_mtime)}  {_rel(p, root)}"
             for p in sorted(targets, key=lambda p: -p.stat().st_size)[:10]]
    return [Finding("info", "caches",
                    f"{len(targets)} cache file(s), {_size(total)} total.", lines,
                    fix="Stale caches survive a mod removal and keep pointing at "
                        "content that is gone. Deleting them is safe — the game "
                        "rebuilds them on the next load (first load is slow).")]


def check_neighborhoods(root: Path) -> "list[Finding]":
    hoods_dir = root / "Neighborhoods"
    if not hoods_dir.is_dir():
        return [Finding("warning", "no-neighborhoods",
                        f"No Neighborhoods folder under {root}")]

    findings, broken, summary = [], [], []
    for hood in sorted(p for p in hoods_dir.iterdir() if p.is_dir()):
        packages = sorted(hood.rglob("*.package"))
        bad_here = []
        for p in packages:
            try:
                header, entries = s2parser.open_package(p)
                size = p.stat().st_size
                if any(e.offset + e.size > size for e in entries):
                    bad_here.append((p, "index points past end of file"))
            except Exception as exc:
                bad_here.append((p, str(exc)))
        summary.append(f"{hood.name:<10} {len(packages):>4} package(s)"
                       + (f"   {len(bad_here)} unreadable" if bad_here else ""))
        broken += bad_here

    findings.append(Finding("info", "neighborhoods",
                            f"{len(summary)} neighborhood(s) scanned.", summary))
    if broken:
        findings.append(Finding("critical", "corrupt-save",
                                f"{len(broken)} unreadable file(s) inside your saves.",
                                [f"{_rel(p, root)}\n        {why}" for p, why in broken],
                                fix="Restore these from a backup before playing "
                                    "further. A damaged lot hangs the game when you "
                                    "enter it."))
    return findings


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n} B"


def _ts(epoch: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")


_REL_BASE: "Path | None" = None


def _rel(path: Path, base: "Path | None" = None) -> str:
    base = base or _REL_BASE
    try:
        return str(path.relative_to(base)) if base else str(path)
    except ValueError:
        return str(path)


def print_report(findings: "list[Finding]", root: Path, show_info: bool) -> None:
    findings = sorted(findings, key=lambda f: SEVERITY_ORDER[f.severity])
    counts = Counter(f.severity for f in findings)

    print(f"\nThe Sims 2 — diagnostic report")
    print(f"user folder: {root}")
    print("=" * 78)

    shown = 0
    for f in findings:
        if f.severity == "info" and not show_info:
            continue
        shown += 1
        print(f"\n[{SEVERITY_LABEL[f.severity]}] {f.title}")
        for line in f.detail:
            print(f"    {line}")
        if f.fix:
            print(f"  → {f.fix}")

    print("\n" + "=" * 78)
    print(f"{counts.get('critical', 0)} critical   "
          f"{counts.get('warning', 0)} warning   "
          f"{counts.get('info', 0)} info", end="")
    if not show_info and counts.get("info"):
        print(f"   ({counts['info']} info hidden — pass --all to see them)")
    else:
        print()
    if not counts.get("critical") and not counts.get("warning"):
        print("Nothing here explains a freeze. If the game still hangs, note what "
              "you were doing when it happened and re-run after it happens again.")


def main(argv: "list[str] | None" = None) -> int:
    global _REL_BASE

    ap = argparse.ArgumentParser(
        description="Diagnose Sims 2 freezes, glitches, and mod conflicts (read-only).")
    ap.add_argument("--root", type=Path, help="The Sims 2 user folder")
    ap.add_argument("--logs-only", action="store_true",
                    help="Only read the game's error logs; skip the package scan")
    ap.add_argument("--downloads-only", action="store_true",
                    help="Only scan Downloads; skip logs and saves")
    ap.add_argument("--skip-saves", action="store_true",
                    help="Skip the neighborhood scan (the slowest step)")
    ap.add_argument("--no-hash", action="store_true",
                    help="Skip duplicate detection (no file hashing)")
    ap.add_argument("--top", type=int, default=10,
                    help="How many entries to list per finding (default 10)")
    ap.add_argument("--recent", type=int, default=5,
                    help="How many recent error logs to cross-reference (default 5)")
    ap.add_argument("--all", action="store_true", help="Include info-level findings")
    ap.add_argument("--json", action="store_true", help="Emit findings as JSON")
    args = ap.parse_args(argv)

    root = args.root
    if root is None:
        root = next((c for c in ROOT_CANDIDATES if (c / "Neighborhoods").is_dir()), None)
    if root is None or not root.is_dir():
        print("Could not find a Sims 2 user folder. Looked in:", file=sys.stderr)
        for c in ROOT_CANDIDATES:
            print(f"  {c}", file=sys.stderr)
        print("Pass --root PATH to point at it.", file=sys.stderr)
        return 2
    _REL_BASE = root

    findings: "list[Finding]" = []
    errors: "list[ObjectError]" = []
    infos: "list[PackageInfo]" = []

    if not args.downloads_only:
        logs = root / "Logs"
        errors = scan_object_errors(logs)
        findings += check_object_errors(errors, args.top)
        findings += check_app_errors(logs)
        findings += check_crash_reports()

    if not args.logs_only:
        downloads = root / "Downloads"
        if downloads.is_dir():
            infos = scan_packages(downloads, hash_files=not args.no_hash)
            findings += check_downloads_integrity(infos)
            findings += check_inert_files(downloads)
            if not args.no_hash:
                findings += check_duplicates(infos)
            findings += check_tgi_conflicts(infos, args.top)
            findings += check_guid_conflicts(infos, args.top)
        else:
            findings.append(Finding("info", "no-downloads",
                                    "No Downloads folder — no custom content installed."))
        findings += check_caches(root)
        if not args.skip_saves and not args.downloads_only:
            findings += check_neighborhoods(root)

    if errors and infos:
        findings += check_error_mod_overlap(errors, infos, args.recent)

    if args.json:
        json.dump({"root": str(root),
                   "findings": [f.to_dict() for f in
                                sorted(findings, key=lambda f: SEVERITY_ORDER[f.severity])]},
                  sys.stdout, indent=2)
        print()
    else:
        print_report(findings, root, show_info=args.all)

    return 1 if any(f.severity == "critical" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
