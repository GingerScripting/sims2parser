---
name: decode-resource-type
description: Work out the binary layout of a Sims 2 .package resource type and add a parser/builder pair for it to s2object.py. Use this whenever the task involves reading, writing, decoding, or reverse-engineering any DBPF resource type — named ones like BCON, TPRP, TRCN, SLOT, TTAB, OBJD, STR#, MMAT, GMDC, TXTR, or an unrecognised four-character type id — and also when someone asks "what's in this resource?", "can we read X yet?", "why does SimPE show this but we don't?", or wants to extend the toolkit to a format it does not yet handle. Applies to the sims2parser repo.
---

# Decoding a new resource type

The repo already has six of these (STR#/TTAs/CTSS, TTAB, OBJf, OBJD, BCON,
GLOB). They all got made the same way, and the order of the steps is what
keeps you from shipping a parser that silently corrupts data.

The thing to understand before starting: **a layout that fits one specimen is a
guess.** These formats span eight expansion packs and a decade of third-party
tooling. The corpus is the specification — there is no other one — so the work
is mostly about testing a hypothesis against enough of it to believe.

## 1. See what you are working with

```sh
python3 .claude/skills/decode-resource-type/scripts/survey.py --type 0x42434F4E
```

Counts specimens across `sample-packages/`, the game install, and Downloads.
Run this first because it tells you whether the job is even possible: a type
with four specimens locally cannot be proven, and you should say so rather than
fit a parser to them.

`sample-packages/` alone is not enough. It is gitignored, and for most types it
holds a handful or none — no TRCN at all, one GLOB, one SLOT.

## 2. Read the bytes at labelled offsets

```sh
python3 .claude/skills/decode-resource-type/scripts/survey.py --type 0x42434F4E --dump 3
```

Read the offset table, not a `repr`. A `repr` of a 64-byte name field followed
by nulls is unreadable and invites a miscount of exactly the kind that costs an
afternoon.

What to look for, because most types here share a skeleton:

- **A 64-byte name field** at offset 0, usually NUL-terminated with garbage
  after the terminator. That garbage has to survive a round-trip, which is what
  `_read_name64` / `_emit_name64` and the `_name_raw` field exist for.
- **The type's own id as a signature**, at `+64` or `+72`. The dump marks it.
  At `+72` means an 8-byte header sits between, and `parse_objf` is your model.
  At `+64` means no header, and the next `u32` is usually a version.
- **A version field** whose value moves the layout. `parse_ttab` shows the
  dispatch pattern.

## 3. Test the layout at scale before writing any code

This is the step that matters most, and the one that looks skippable.

```sh
python3 .claude/skills/decode-resource-type/scripts/survey.py --type 0x42434F4E \
    --fit '66 + d[64] * 2'
```

`--fit` takes a Python expression over the resource bytes `d` that returns the
length your layout implies, and compares it against every specimen.

Worked example, from the real thing. BCON is a 64-byte name, a count, then that
many `u16` constants. Reading the count as a `u16`:

```
hypothesis: 66 + struct.unpack_from("<H", d, 64)[0] * 2
  561/912 fit (61.5%), 351 miss
```

61.5% is the dangerous number. It is high enough to look like a near-miss and
low enough to be a completely wrong reading — the count is a *byte*, and the
byte beside it is a flag, so `0x8008` was being read as 32776. As a byte:

```
hypothesis: 66 + d[64] * 2
  912/912 fit (100.0%), 0 miss
```

Aim for 100%. Anything less means you do not understand the format yet.

**Look at the misses rather than rounding them off.** When GLOB fit 166 of 170,
the four failures were carrying trailing `0xA3` filler after the string. That is
not noise to discard — dropping it makes the round-trip lossy. It became a
`_tail` field that preserves the bytes verbatim.

## 4. Write the parse/build pair

Model it on the closest existing pair in `s2object.py`. The conventions that
matter:

- `parse_X(data: bytes) -> X` and `build_X(x: X) -> bytes`, exact inverses.
- A `@dataclass` holding the *meaningful* fields, plus private fields for
  anything that has to survive verbatim (`_name_raw`, `_tail`).
- **Raise `ValueError` when declining** a resource — an unsupported version, a
  length that does not square. The self-test treats a decline as a coverage gap
  and a wrong rebuild as a bug, so this distinction is load-bearing.
- Validate on the way in *and* out. `parse_bcon` checks the count against the
  resource length; `build_bcon` checks the count fits a byte.
- Register in `PARSERS` so `parse_resource` / `build_resource` and the self-test
  pick it up automatically.

Every module here opens with `from __future__ import annotations`, because the
app gets `/usr/bin/python3` (3.9) when launched from Finder. Check with that
interpreter explicitly, never a shell `python3`.

## 5. Prove it round-trips

```sh
python3 .claude/skills/decode-resource-type/scripts/survey.py --type 0x42434F4E --roundtrip
python3 s2object.py                    # sample-packages
python3 s2object.py <wide-corpus-dir>  # the real bar
```

The bar is **byte-identical**, not "it parsed". A parser that silently drops
bytes is worse than no parser, because it turns a missing feature into data
loss. Hundreds of clean round-trips is the evidence; one specimen is not.

## When to stop and say so

Some types are not an afternoon's work, and recognising that early is more
useful than a parser fitted to one specimen.

The signal is a version field with many values where the layout moves with it.
TRCN and SLOT each carry roughly eleven version values, and a plausible
structural hypothesis for TRCN scattered across 57 different trailing
remainders. That is real archaeology — the `s2savediff.py`
snapshot/change-one-thing/diff loop, or documentation from SimsWiki and
Pick'N'Mix Mods — not a parser you can write from the corpus alone.

Being wrong about this in the cheap direction is fine. TPRP was called
archaeology here on the same evidence — a hypothesis that left 12 trailing
bytes on 247 of 275 specimens — and was then decoded anyway once someone found
the field that accounted for them. The point is not that hard types are
impossible; it is that the honest report is "this needs archaeology" rather
than a parser fitted to whichever specimen you opened first.

Say that plainly instead of shipping something that half works. Partial coverage
is fine when it is declared: `parse_ttab` handles 2 of the 11 TTAB versions in
the wild and raises on the rest, which is honest and useful.

## Reporting what you found

Whoever reads this next needs the evidence, not just the code. Record in the
commit message:

- The layout, and the specimen count it was verified against.
- Any hypothesis that looked right and was not — the u16-versus-byte reading is
  the kind of thing that gets re-derived by the next person otherwise.
- What the outliers were and how they are preserved.
- What you deliberately did not do, and why.

That is the same reasoning the memory files and the reverse-engineering notes in
`README.md` capture, and it is what makes the next type faster than this one.
