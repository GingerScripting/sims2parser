#!/usr/bin/env python3
"""hoodcheck.py — detect (and repair) a truncated Sims 2 neighborhood token store.

A neighborhood that hangs on load at 100% CPU, with no error log and no crash,
is often carrying an NGBH whose sim section declares more token groups than the
file actually contains. The game reads the declared count, walks past the end of
the buffer, and never terminates.

This is invisible to ordinary integrity checks: every record that exists is
well-formed, there are no dangling references, and nothing is missing that any
reference points at. Only the count disagrees with reality.

It is also invisible to `s2ngbh.parse_ngbh()`, which resyncs past malformed data
so that one bad group doesn't cost a 2 MB resource. That recovery is right for
reading and wrong for auditing, so this module walks the store itself.

Detection is read-only. `--repair` never writes into the save: it emits a
repaired copy next to a chosen output path and leaves the original alone.
"""

# Annotations stay strings so the module imports under the system python3 (3.9).
from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

import s2parser
import s2ngbh
import s2writer

ROOT_CANDIDATES = [
    Path.home() / "Library/Containers/com.aspyr.sims2.appstore/Data"
                  "/Library/Application Support/Aspyr/The Sims 2/Neighborhoods",
    Path.home() / "Library/Application Support/Aspyr/The Sims 2/Neighborhoods",
    Path.home() / "Documents/EA Games/The Sims 2/Neighborhoods",
]

SDSC = 0xAACE2EFB
HOUSEHOLD_SENTINEL = 32767   # last group of the household section
EMPTY_GROUP_SIZE = 16        # gid, marker, 0 tokens, 0 tokens
CHUNK = 8192                 # serialisation buffer granularity
TERMINATOR = struct.pack("<I", 1)


@dataclass
class Report:
    hood: str
    package: Path
    ngbh_entry: object = None
    data: bytes = b""
    groups: "list[tuple[int, int, int]]" = field(default_factory=list)
    sim_start: int = 0          # byte offset where the sim section begins
    declared: int = 0
    actual: int = 0
    last_end: int = 0
    trailing: bytes = b""
    sdsc_count: int = 0
    ngbh_size: int = 0
    missing_nids: "list[int]" = field(default_factory=list)
    error: str = ""

    @property
    def healthy(self) -> bool:
        return not self.error and self.declared == self.actual and len(self.trailing) <= 8


def walk_groups(d: bytes) -> "list[tuple[int, int, int]]":
    """[(group id, start offset, end offset), …].

    Mirrors s2ngbh's recovery so the group list matches what the reader sees,
    but keeps offsets so the tail can be judged separately.
    """
    marker, maxv = s2ngbh.GROUP_MARKER, s2ngbh._MAX_TOKEN_VALUES
    pos = s2ngbh._find_first_group(d)
    out = []
    if pos < 0:
        return out
    while pos + 12 <= len(d):
        gid, mk, first = struct.unpack_from("<III", d, pos)
        if mk != marker or first > maxv:
            pos = s2ngbh._resync(d, pos + 4)
            if pos < 0:
                break
            continue
        listed, q = s2ngbh._read_tokens(d, pos + 12, first)
        if listed is None or q + 4 > len(d):
            pos = s2ngbh._resync(d, pos + 4)
            if pos < 0:
                break
            continue
        second, = struct.unpack_from("<I", d, q)
        rest, end = (s2ngbh._read_tokens(d, q + 4, second)
                     if second <= maxv else (None, q))
        if rest is None:
            pos = s2ngbh._resync(d, pos + 4)
            if pos < 0:
                break
            continue
        out.append((gid, pos, end))
        pos = end
    return out


def inspect(target: Path) -> "Report | None":
    """Check one package, or a hood folder's main neighborhood package."""
    if target.is_dir():
        pkg = target / f"{target.name}_Neighborhood.package"
        if not pkg.is_file():
            return None
    else:
        pkg = target
    rep = Report(hood=pkg.stem.replace("_Neighborhood", ""), package=pkg)
    try:
        header, entries = s2parser.open_package(pkg)
    except Exception as exc:
        rep.error = f"package unreadable: {exc}"
        return rep

    ngbh = [e for e in entries if e.type_id == s2ngbh.TID_NGBH]
    if not ngbh:
        rep.error = "no NGBH resource"
        return rep
    rep.ngbh_entry = ngbh[0]
    rep.sdsc_count = sum(1 for e in entries if e.type_id == SDSC)

    with open(pkg, "rb") as f:
        try:
            rep.data = s2parser.read_resource(f, rep.ngbh_entry)
        except Exception as exc:
            rep.error = f"NGBH unreadable: {exc}"
            return rep

    d = rep.data
    rep.ngbh_size = len(d)
    rep.groups = walk_groups(d)
    if not rep.groups:
        rep.error = "no token groups found"
        return rep

    sentinels = [g for g in rep.groups if g[0] == HOUSEHOLD_SENTINEL]
    if not sentinels:
        rep.error = f"no household sentinel (group {HOUSEHOLD_SENTINEL})"
        return rep

    # The u32 immediately after the household sentinel is the sim-section count.
    _gid, _start, sim_start = sentinels[-1]
    rep.sim_start = sim_start
    if sim_start + 4 > len(d):
        rep.error = "file ends at the sim-section count"
        return rep
    rep.declared = struct.unpack_from("<I", d, sim_start)[0]

    sims = [g for g in rep.groups if g[1] > sim_start]
    rep.actual = len(sims)
    rep.last_end = rep.groups[-1][2]
    rep.trailing = d[rep.last_end:]

    present = {g[0] for g in sims}
    with open(pkg, "rb") as f:
        nids = sorted(e.instance for e in entries if e.type_id == SDSC)
    rep.missing_nids = [n for n in nids if n not in present][: max(0, rep.declared - rep.actual)]
    return rep


def repair(rep: Report, out_path: Path, mode: str = "pad") -> "tuple[int, str]":
    """Write a repaired copy. Returns (groups added or removed, description).

    pad   — keep the declared count and append an empty token group for every
            sim that lost one. The sims still exist; only their memories are
            gone, which is what an empty group means.
    trim  — lower the declared count to the number of groups actually present.
            Loses nothing that is still in the file, but disagrees with the
            SDSC count.
    """
    d = rep.data
    body = d[:rep.last_end]          # drop any partially-written trailing group

    if mode == "pad":
        missing = rep.declared - rep.actual
        nids = list(rep.missing_nids)
        while len(nids) < missing:                     # fall back to fresh ids
            nids.append(max(g[0] for g in rep.groups) + 1 + len(nids))
        added = b"".join(struct.pack("<IIII", nid, s2ngbh.GROUP_MARKER, 0, 0)
                         for nid in nids[:missing])
        new = body + added + TERMINATOR
        note = (f"padded {missing} empty group(s) for nid(s) "
                f"{nids[:missing]}; declared count left at {rep.declared}")
    elif mode == "trim":
        new = bytearray(body)
        struct.pack_into("<I", new, rep.sim_start, rep.actual)
        new = bytes(new) + TERMINATOR
        note = f"declared count lowered {rep.declared} -> {rep.actual}"
    else:
        raise ValueError(f"unknown repair mode {mode!r}")

    resources = s2writer.read_all_resources(rep.package)
    for r in resources:
        if r.type_id == s2ngbh.TID_NGBH:
            r.data = new
            break
    s2writer.write_package(out_path, resources, compress=True)
    return (rep.declared - rep.actual), note


def describe(rep: Report) -> None:
    label = rep.package.stem
    if rep.error:
        print(f"      --   {label:<28} {rep.error}")
        return
    flag = "OK  " if rep.healthy else "BAD "
    print(f"      {flag} {label:<28} declared={rep.declared:<5} "
          f"actual={rep.actual:<5} sims(SDSC)={rep.sdsc_count:<5} "
          f"tail={len(rep.trailing)}B")
    # A truncated store is always an exact multiple of 8 KB: the game
    # serialises into a buffer that grows in 8 KB chunks, and a failed write
    # keeps only the whole chunks. Healthy stores end at an arbitrary size.
    #
    # It is the *uncompressed* size that aligns. The resource is QFS-compressed
    # in the package and the stored sizes are arbitrary, so the loss happens in
    # memory before compression — which is why the package is always perfectly
    # well-formed and no structural check can see the damage.
    #
    # Three truncations observed at 244, 244 and 245 chunks; three healthy
    # stores unaligned. An earlier reading of this as 32 KB came from two
    # samples that were both 244 x 8192, and 244 divides by 4.
    over = rep.ngbh_size % CHUNK
    if rep.ngbh_size and over == 0:
        print(f"        NGBH is exactly {rep.ngbh_size // CHUNK} x 8 KB — "
              f"the hallmark of a lost final buffer chunk")
    elif rep.healthy and over and over < 1024:
        print(f"        note: NGBH is only {over} bytes past an 8 KB boundary; "
              f"a failed save here would truncate to {rep.ngbh_size - over} "
              f"and lose the tail groups")

    if not rep.healthy:
        gap = rep.declared - rep.actual
        if gap:
            print(f"        {gap} sim token group(s) missing — the loader will walk "
                  f"past the end of the buffer and never terminate")
        if len(rep.trailing) > 8:
            print(f"        tail is a partially written group, no terminator: "
                  f"{rep.trailing[:16].hex(' ')}…")
        if rep.missing_nids:
            print(f"        sims without a token group: {rep.missing_nids}")


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(
        description="Detect a truncated Sims 2 neighborhood token store.")
    ap.add_argument("--root", type=Path, help="Neighborhoods folder")
    ap.add_argument("--hood", help="Check only this hood (e.g. N002)")
    ap.add_argument("--repair", metavar="OUT",
                    help="Write a repaired copy of the package to OUT "
                         "(never modifies the original)")
    ap.add_argument("--mode", choices=("pad", "trim"), default="pad",
                    help="Repair strategy (default: pad)")
    args = ap.parse_args(argv)

    root = args.root or next((c for c in ROOT_CANDIDATES if c.is_dir()), None)
    if not root or not root.is_dir():
        print("Could not find a Neighborhoods folder; pass --root.", file=sys.stderr)
        return 2

    hoods = sorted(p for p in root.iterdir()
                   if p.is_dir() and (not args.hood or p.name == args.hood))
    print(f"\nNeighborhood token store check — {root}\n")
    reports = []
    for h in hoods:
        # Every sub-hood (Downtown, Suburb, University, Vacation) carries its
        # own NGBH and loads as its own neighborhood, so each needs checking.
        pkgs = sorted(h.glob(f"{h.name}_*.package"))
        if not pkgs:
            continue
        print(f"  {h.name}")
        for pkg in pkgs:
            rep = inspect(pkg)
            if rep:
                reports.append(rep)
                describe(rep)

    bad = [r for r in reports if not r.error and not r.healthy]
    errs = [r for r in reports if r.error]
    print(f"\n{len(reports)} hood(s) checked, {len(bad)} with a truncated token store"
          + (f", {len(errs)} not applicable." if errs else "."))

    if args.repair:
        if len(bad) != 1:
            print("--repair needs exactly one damaged hood; use --hood to pick one.",
                  file=sys.stderr)
            return 2
        out = Path(args.repair)
        n, note = repair(bad[0], out, args.mode)
        print(f"\nRepaired copy written to {out}\n  {note}")
        if out.name.endswith("_Neighborhood.package"):
            verified = inspect(out.parent)
            if verified:
                print("\n  Re-checked the repaired copy:")
                describe(verified)
        print("\n  Swap it in yourself — this tool never writes into your save.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
