#!/usr/bin/env python3
"""Corpus tools for decoding a Sims 2 resource type.

Three things you need repeatedly when working out an unknown format, and
which are tedious to rewrite each time:

    survey.py --type 0x42434F4E                 how many exist, and where
    survey.py --type 0x42434F4E --dump 3        offset-labelled bytes
    survey.py --type 0x42434F4E --roundtrip     byte-identical check, wide

The corpus is deliberately wider than sample-packages/, which holds only a
handful of most types and none at all of some. A layout fitted to one
specimen is a guess; a layout that survives several hundred is a finding.
"""

from __future__ import annotations

import argparse
import collections
import itertools
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import s2parser  # noqa: E402


def corpus_roots() -> "list[Path]":
    """Everywhere a package might live, widest-useful first.

    s2doctor already knows where the game keeps things across the sandboxed,
    non-sandboxed and EA-Games layouts, so reuse it rather than hardcoding a
    path that goes stale.
    """
    roots = [Path("sample-packages")]
    try:
        import s2doctor
        for c in s2doctor.ROOT_CANDIDATES:
            roots.extend([c / "Downloads", c])
    except Exception:
        pass
    return [r for r in roots if r.exists()]


def iter_resources(type_id: int, limit: int = 2000):
    """Yield (package_path, entry, decompressed bytes) for one type."""
    seen = set()
    for root in corpus_roots():
        for p in itertools.islice(root.rglob("*.package"), limit):
            if p in seen:
                continue
            seen.add(p)
            try:
                with open(p, "rb") as f:
                    header = s2parser.parse_header(f)
                    for e in s2parser.parse_index(f, header):
                        if e.type_id == type_id:
                            yield p, e, s2parser.read_resource(f, e)
            except Exception:
                continue          # unreadable or not a package; best-effort


def cmd_count(type_id: int) -> int:
    per_pkg = collections.Counter()
    sizes = []
    for p, _e, d in iter_resources(type_id):
        per_pkg[p.name] += 1
        sizes.append(len(d))
    if not sizes:
        print(f"no resources of type 0x{type_id:08X} found in the corpus")
        return 1
    sizes.sort()
    print(f"type 0x{type_id:08X}: {len(sizes)} specimens "
          f"in {len(per_pkg)} packages")
    print(f"  size  min={sizes[0]}  median={sizes[len(sizes)//2]}  max={sizes[-1]}")
    print(f"  distinct sizes: {len(set(sizes))}")
    print("  most specimens:")
    for name, n in per_pkg.most_common(5):
        print(f"    {n:4}  {name[:56]}")
    return 0


def cmd_dump(type_id: int, count: int) -> int:
    """Offset-labelled bytes. Read these, do not eyeball a repr.

    Most resources here open with a 64-byte name field; several then carry
    their own type id as a signature, at +64 or at +72. Which one tells you
    a lot about which existing parser to model the new one on.
    """
    shown = 0
    for p, _e, d in iter_resources(type_id):
        print(f"=== {len(d)} bytes  ({p.name[:44]})")
        name = d[:64].split(b"\x00", 1)[0]
        print(f"    name[0:64] = {name!r}")
        for off in range(64, min(len(d), 96), 4):
            u32 = struct.unpack_from("<I", d, off)[0] if off + 4 <= len(d) else None
            if u32 is None:
                break
            note = "   <- this type's id (signature)" if u32 == type_id else ""
            print(f"    +{off:<3} u32=0x{u32:08X} ({u32:<11}) "
                  f"u16={struct.unpack_from('<H', d, off)[0]:<6} "
                  f"bytes={d[off:off+4]!r}{note}")
        print(f"    tail: {d[-24:]!r}\n")
        shown += 1
        if shown >= count:
            break
    return 0 if shown else 1


def cmd_fit(type_id: int, expr: str) -> int:
    """Test a length hypothesis against every specimen.

    `expr` is Python over `d` (the resource bytes) returning the length the
    layout implies. It is compared against len(d). This is the step worth
    not skipping: a hypothesis that fits the first specimen and half the
    corpus looks right and is not. Reading BCON's count as a u16 rather
    than a byte fits 259 of 462 — the failures are exactly the resources
    with the flag byte set, and they only show up at scale.
    """
    fit = miss = 0
    examples = []
    for p, _e, d in iter_resources(type_id):
        try:
            implied = eval(expr, {"struct": struct, "len": len}, {"d": d})  # noqa: S307
        except Exception as exc:
            miss += 1
            if len(examples) < 4:
                examples.append(f"{p.name[:34]}: {type(exc).__name__}: {exc}")
            continue
        if implied == len(d):
            fit += 1
        else:
            miss += 1
            if len(examples) < 4:
                examples.append(f"{p.name[:34]}: len={len(d)} implied={implied}")
    total = fit + miss
    if not total:
        print("no specimens")
        return 1
    print(f"hypothesis: {expr}")
    print(f"  {fit}/{total} fit ({100*fit/total:.1f}%), {miss} miss")
    for e in examples:
        print(f"    {e}")
    if miss:
        print("\n  Look at the misses before adjusting. A group of them that "
              "\n  differ by a constant usually means a trailing field you "
              "\n  have not accounted for — preserve it rather than ignore it.")
    return 0 if miss == 0 else 1


def cmd_roundtrip(type_id: int) -> int:
    """Byte-identical check across the wide corpus, once registered."""
    import s2object
    if type_id not in s2object.PARSERS:
        print(f"0x{type_id:08X} is not in s2object.PARSERS yet")
        return 1
    ok = 0
    failures = []
    for p, _e, d in iter_resources(type_id):
        try:
            rebuilt = s2object.build_resource(
                type_id, s2object.parse_resource(type_id, d))
        except ValueError as exc:
            failures.append(f"{p.name[:34]}: declined: {exc}")
            continue
        if rebuilt == d:
            ok += 1
        else:
            first = next((i for i in range(min(len(d), len(rebuilt)))
                          if d[i] != rebuilt[i]), "length only")
            failures.append(f"{p.name[:34]}: {len(d)} -> {len(rebuilt)} bytes, "
                            f"first diff at {first}")
    print(f"round-trip 0x{type_id:08X}: {ok} byte-identical, {len(failures)} not")
    for f in failures[:10]:
        print(f"  FAIL {f}")
    if len(failures) > 10:
        print(f"  ... and {len(failures) - 10} more")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--type", required=True, type=lambda s: int(s, 0),
                    metavar="ID", help="resource type id, e.g. 0x42434F4E")
    ap.add_argument("--dump", type=int, metavar="N",
                    help="print offset-labelled bytes for N specimens")
    ap.add_argument("--fit", metavar="EXPR",
                    help="test a length hypothesis, Python over `d`, e.g. "
                         "'66 + d[64] * 2'")
    ap.add_argument("--roundtrip", action="store_true",
                    help="verify parse->build is byte-identical corpus-wide")
    args = ap.parse_args()

    if args.dump:
        return cmd_dump(args.type, args.dump)
    if args.fit:
        return cmd_fit(args.type, args.fit)
    if args.roundtrip:
        return cmd_roundtrip(args.type)
    return cmd_count(args.type)


if __name__ == "__main__":
    sys.exit(main())
