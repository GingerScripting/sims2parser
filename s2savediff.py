#!/usr/bin/env python3
"""Snapshot a neighborhood save and diff two snapshots, resource by resource.

Built to answer "where does the game keep X?" for state that isn't in any
format this toolkit already reads. Its first run found the Open for Business
perks, which aren't in the NGBH token store where they were looked for: one
perk bought between two saves showed up as a 24-byte growth in a resource type
nothing here parsed, keyed by the buying sim's nid (now s2luastate).

The method is a controlled experiment:

    snap before        # save in game, then snapshot
    snap noise         # save again having done nothing, then snapshot
    snap after         # do the one thing you're hunting, save, snapshot
    diff noise after --minus before noise

Two saves with nothing in between still differ in hundreds of places — clock,
motives, autonomy bookkeeping. That's what the `noise` snapshot is for: --minus
drops every change that also showed up in the do-nothing pair, so what's left
is the change you caused.

Diffs run at three levels: which packages changed, which resources inside them
changed, and which bytes inside those resources changed. NGBH resources get a
token-level diff instead, since their contents are variable-length records.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import struct
import sys
from pathlib import Path

import s2parser
import s2ngbh
import s2neighborhood as sn

STORE = Path.home() / "Documents" / "sims2-savediff"

# Everything the game rewrites that could hold sim state. Storytelling and
# Thumbnails are screenshots, .reia is the neighborhood terrain image; none of
# them are worth 200 MB a snapshot.
SUBDIRS = ("", "Characters", "Lots")

# Per-resource byte diffs get noisy fast; these are the ones worth reading.
_MAX_BYTE_DIFFS = 40


# --- snapshots --------------------------------------------------------------

def _in_scope(hood_dir: Path):
    for sub in SUBDIRS:
        d = hood_dir / sub if sub else hood_dir
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.package")):
            yield p, (Path(sub) / p.name if sub else Path(p.name))


def cmd_snap(args) -> int:
    hood_dir = Path(args.root) / args.hood
    if not hood_dir.is_dir():
        print(f"no such neighborhood: {hood_dir}", file=sys.stderr)
        return 1
    dest = STORE / args.hood / args.label
    if dest.exists():
        if not args.force:
            print(f"snapshot {args.label!r} exists; pass --force to replace",
                  file=sys.stderr)
            return 1
        shutil.rmtree(dest)

    total = count = 0
    for src, rel in _in_scope(hood_dir):
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out)
        total += src.stat().st_size
        count += 1
    print(f"snapshot {args.label!r}: {count} packages, {total / 1e6:.0f} MB "
          f"-> {dest}")
    return 0


def cmd_list(args) -> int:
    root = STORE / args.hood
    if not root.is_dir():
        print("no snapshots yet")
        return 0
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        size = sum(p.stat().st_size for p in d.rglob("*.package"))
        newest = max((p.stat().st_mtime for p in d.rglob("*.package")),
                     default=d.stat().st_mtime)
        import datetime
        stamp = datetime.datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M")
        print(f"  {d.name:<16} {size / 1e6:7.0f} MB   saved {stamp}")
    return 0


def cmd_clean(args) -> int:
    root = STORE / args.hood
    if root.is_dir():
        shutil.rmtree(root)
        print(f"removed {root}")
    return 0


# --- reading ----------------------------------------------------------------

def _resources(pkg: Path) -> dict[tuple, bytes]:
    """{(type, group, instance, instance2): decompressed bytes}.

    A resource that won't decompress is keyed to its raw bytes rather than
    dropped — an unparseable resource that *changes* is still a lead.
    """
    try:
        _, entries = s2parser.open_package(pkg)
    except Exception:
        return {}
    out = {}
    with open(pkg, "rb") as f:
        for e in entries:
            key = (e.type_id, e.group_id, e.instance_id, e.instance_id2)
            try:
                out[key] = s2parser.read_resource(f, e)
            except Exception:
                f.seek(e.offset)
                out[key] = f.read(e.size)
    return out


def _tgi(key) -> str:
    t, g, i, i2 = key
    name = s2parser.TYPE_NAMES.get(t, f"0x{t:08X}")
    return f"{name} g={g:08x} i={i2:08x}{i:08x}" if i2 else f"{name} g={g:08x} i={i:08x}"


def _digest(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:12]


# --- change detection -------------------------------------------------------

def _byte_changes(old: bytes, new: bytes) -> list[tuple[int, int, int]]:
    """[(offset, old u16, new u16)] for equal-length resources, else []."""
    if len(old) != len(new):
        return []
    changes = []
    for off in range(0, len(old) - 1, 2):
        a = struct.unpack_from("<H", old, off)[0]
        b = struct.unpack_from("<H", new, off)[0]
        if a != b:
            changes.append((off, a, b))
    return changes


def _token_changes(old: bytes, new: bytes) -> list[tuple]:
    """[(section, owner, sign, guid, values)] for an NGBH resource."""
    from collections import Counter
    a, b = s2ngbh.parse_ngbh(old), s2ngbh.parse_ngbh(new)
    out = []
    for section in ("lots", "families", "sims"):
        for owner in sorted(set(a[section]) | set(b[section])):
            ca = Counter((g, v) for g, v in a[section].get(owner, []))
            cb = Counter((g, v) for g, v in b[section].get(owner, []))
            for (g, v), n in (cb - ca).items():
                out.extend([(section, owner, "+", g, v)] * n)
            for (g, v), n in (ca - cb).items():
                out.extend([(section, owner, "-", g, v)] * n)
    return out


def _collect(snap_a: Path, snap_b: Path) -> dict:
    """{change key: description} for everything that differs between snapshots.

    Keys are stable across runs so a second diff can be subtracted from a
    first: file-level keys name the package, resource-level keys name the
    resource, byte-level keys name the offset within it.
    """
    files_a = {p.relative_to(snap_a): p for p in snap_a.rglob("*.package")}
    files_b = {p.relative_to(snap_b): p for p in snap_b.rglob("*.package")}
    changes: dict = {}

    for rel in sorted(set(files_a) | set(files_b)):
        if rel not in files_b:
            changes[("file-gone", str(rel))] = f"{rel}: package removed"
            continue
        if rel not in files_a:
            changes[("file-new", str(rel))] = f"{rel}: package added"
            continue
        if files_a[rel].read_bytes() == files_b[rel].read_bytes():
            continue

        ra, rb = _resources(files_a[rel]), _resources(files_b[rel])
        for key in sorted(set(ra) | set(rb)):
            if key not in rb:
                changes[("res-gone", str(rel), key)] = f"{rel}  {_tgi(key)}: removed"
                continue
            if key not in ra:
                changes[("res-new", str(rel), key)] = (
                    f"{rel}  {_tgi(key)}: added ({len(rb[key])} bytes)")
                continue
            old, new = ra[key], rb[key]
            if old == new:
                continue

            if key[0] == s2ngbh.TID_NGBH:
                for section, owner, sign, guid, values in _token_changes(old, new):
                    ck = ("token", str(rel), section, owner, sign, guid, values)
                    changes[ck] = (f"{rel}  NGBH {section}[{owner}] {sign}"
                                   f"{guid:08X} {list(values)[:14]}")
                continue

            deltas = _byte_changes(old, new)
            if not deltas:
                changes[("res-size", str(rel), key)] = (
                    f"{rel}  {_tgi(key)}: {len(old)} -> {len(new)} bytes "
                    f"({_digest(old)} -> {_digest(new)})")
                continue
            for off, va, vb in deltas:
                changes[("byte", str(rel), key, off)] = (
                    f"{rel}  {_tgi(key)} @0x{off:04x}: {va} -> {vb}")
    return changes


def cmd_diff(args) -> int:
    root = STORE / args.hood
    a, b = root / args.before, root / args.after
    for p in (a, b):
        if not p.is_dir():
            print(f"no snapshot {p.name!r} (try `list`)", file=sys.stderr)
            return 1

    changes = _collect(a, b)
    noise: dict = {}
    if args.minus:
        na, nb = root / args.minus[0], root / args.minus[1]
        for p in (na, nb):
            if not p.is_dir():
                print(f"no snapshot {p.name!r} (try `list`)", file=sys.stderr)
                return 1
        noise = _collect(na, nb)
        # A byte that moves in the do-nothing pair moves on every save; drop
        # it whatever value it landed on this time.
        changes = {k: v for k, v in changes.items() if k not in noise}
        if not args.keep_noisy_tokens:
            noisy_res = {k[:3] for k in noise if k[0] == "byte"}
            changes = {k: v for k, v in changes.items()
                       if not (k[0] == "byte" and k[:3] in noisy_res)}

    print(f"{args.before} -> {args.after}: {len(changes)} changes"
          + (f" ({len(noise)} subtracted as save noise)" if noise else ""))

    shown = 0
    for kind in ("file-new", "file-gone", "res-new", "res-gone", "res-size",
                 "token", "byte"):
        group = [v for k, v in sorted(changes.items(), key=lambda kv: str(kv[0]))
                 if k[0] == kind]
        if not group:
            continue
        print(f"\n-- {kind} ({len(group)})")
        for line in group[:args.limit]:
            print(f"   {line}")
        if len(group) > args.limit:
            print(f"   … {len(group) - args.limit} more (raise --limit)")
        shown += len(group)
    if not shown:
        print("\nnothing left after subtraction — whatever you did isn't "
              "written to disk, or isn't in the snapshot scope")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hood", default="N002", help="neighborhood folder (default N002)")
    ap.add_argument("--root", default=str(sn.DEFAULT_ROOT), help="Neighborhoods directory")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snap", help="copy the save's packages under a label")
    s.add_argument("label")
    s.add_argument("--force", action="store_true", help="replace an existing snapshot")
    s.set_defaults(func=cmd_snap)

    s = sub.add_parser("list", help="show stored snapshots")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("clean", help="delete stored snapshots for this hood")
    s.set_defaults(func=cmd_clean)

    s = sub.add_parser("diff", help="compare two snapshots")
    s.add_argument("before")
    s.add_argument("after")
    s.add_argument("--minus", nargs=2, metavar=("A", "B"),
                   help="subtract the changes seen between two do-nothing snapshots")
    s.add_argument("--keep-noisy-tokens", action="store_true",
                   help="keep byte changes in resources the noise pair also touched")
    s.add_argument("--limit", type=int, default=60, help="lines per section (default 60)")
    s.set_defaults(func=cmd_diff)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
