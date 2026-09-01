#!/usr/bin/env python3
"""Package-level operations: merge one package's resources into another,
and split a selection out into its own.

Pure functions over `list[s2writer.Resource]`, so the Sim Studio daemon can
record what changed for undo and the CLI below can do the same job on files.
"""

# Annotations stay strings so the modules import under the system
# python3 (3.9), which the app gets when launched from Finder.
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import s2package
import s2writer
from s2writer import Resource


@dataclass
class MergeReport:
    added: "list[tuple[int, int, int, int]]" = field(default_factory=list)
    replaced: "list[tuple[int, int, int, int]]" = field(default_factory=list)
    skipped: "list[tuple[int, int, int, int]]" = field(default_factory=list)

    def __str__(self) -> str:
        return (f"merged: {len(self.added)} added, {len(self.replaced)} replaced, "
                f"{len(self.skipped)} skipped (already present)")


def merge(dest: "list[Resource]", src: "list[Resource]",
          on_conflict: str = "skip") -> MergeReport:
    """Add every resource of `src` to `dest`, in place.

    A TGI already in `dest` is skipped or replaced according to
    `on_conflict`; there is no third choice because a package cannot hold
    two resources with one TGI. Resources are shared, not copied — the
    caller owns both lists and `src` is not used again.
    """
    if on_conflict not in ("skip", "replace"):
        raise ValueError(f"on_conflict must be 'skip' or 'replace', not {on_conflict!r}")
    report = MergeReport()
    for r in src:
        if r.type_id == s2writer.TYPE_DIR:
            continue
        n = s2package.find(dest, r.tgi())
        if n < 0:
            dest.append(r)
            report.added.append(r.tgi())
        elif on_conflict == "replace":
            dest[n] = r
            report.replaced.append(r.tgi())
        else:
            report.skipped.append(r.tgi())
    return report


def split(resources: "list[Resource]", tgis: "list[tuple[int, int, int, int]]",
          remove: bool = False) -> "list[Resource]":
    """The resources with the given TGIs, in package order, for writing to a
    new package. With `remove`, they are also taken out of `resources`."""
    wanted = set(tgis)
    picked = [r for r in resources if r.tgi() in wanted]
    missing = wanted - {r.tgi() for r in picked}
    if missing:
        raise KeyError(sorted(missing)[0])
    if remove:
        resources[:] = [r for r in resources if r.tgi() not in wanted]
    return picked


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="Merge or split Sims 2 packages.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge", help="write DEST + SRC... to OUT")
    m.add_argument("dest", type=Path)
    m.add_argument("src", type=Path, nargs="+")
    m.add_argument("--out", type=Path, required=True)
    m.add_argument("--replace", action="store_true", help="let SRC win on a TGI clash")
    m.add_argument("--compress", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "merge":
        dest = s2writer.read_all_resources(args.dest)
        for src in args.src:
            rep = merge(dest, s2writer.read_all_resources(src), "replace" if args.replace else "skip")
            print(f"{src.name}: {rep}")
        s2writer.write_package(args.out, dest, compress=args.compress)
        print(f"wrote {args.out} ({len(dest)} resources)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
