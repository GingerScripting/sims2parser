# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## The one hard rule

**Nothing in this project ever writes to a Sims 2 save.** The parsers are
strictly read-only against the game's neighborhood folders. `s2writer.py` and
`s2object.py` do write `.package` files, but only new standalone ones in a
scratch directory or `sample-packages/` — never back into a save.

## Two languages, one direction

```
Sims 2 saves ──▶ Python toolkit ──▶ sims.json ──▶ SwiftUI app
(DBPF/QFS)        (all format work)   (cache)      (SimBrowser/)
```

All binary format knowledge lives in Python. The Swift app only ever reads the
extracted JSON — it cannot open a `.package` and so cannot corrupt a save. Keep
it that way: a new save-data feature means a Python parser plus a JSON field,
then Swift consumes it. Never parse game bytes in Swift.

## Commands

```sh
./SimBrowser/make_app.sh              # build "Sim Browser.app" in the repo root (universal)
ARCHS=native ./SimBrowser/make_app.sh # this machine only — much quicker while iterating
cd SimBrowser && swift run            # run the app from source, no bundle
cd SimBrowser && swift build          # compile check
```

```sh
python3 s2neighborhood.py --hood N002 --out /tmp/sims.json   # extract one hood
python3 s2parser.py --bhav file.package                      # decompile SimAntics
python3 s2doctor.py                                          # freeze/mod-conflict diagnostic
python3 s2savediff.py snap before  # …play the game…  then:
python3 s2savediff.py diff before after                      # "where does the game keep X?"
```

There is no test framework. Verification is two things:

```sh
python3 s2object.py                       # round-trips every parser against donors, byte-for-byte
python3 s2writer.py <donor.package>       # read → write → re-read → compare
python3 s2parser.py --qfs-selftest sample-packages/   # recompress every QFS payload and verify
```

…plus **empirical checks against canonical premades**, which is the real test
suite. If a change to the neighborhood readers can't still tell you that Daniel
Pleasant loves Mary-Sue while she's at −59 and falling, it's wrong. Run the
extractor over Pleasantview/Strangetown and eyeball known families.

Both self-tests read donor packages from `sample-packages/`, which is
**gitignored** — it exists locally but never in a clone. Don't assume CI or a
fresh checkout can run them.

## Python 3.9 is a hard constraint

The app launched from Finder gets `/usr/bin/python3`, which is **3.9.6**. Every
module that the bundle imports must open with:

```python
from __future__ import annotations
```

so that `X | None` and `list[str]` annotations stay strings. This has already
broken the app once in a way that only shows up outside a dev shell — a modern
`python3` on `$PATH` will import the file happily while the shipped app fails.
Sanity-check with the system interpreter explicitly:

```bash
/usr/bin/python3 -c "import s2neighborhood, sim_browser; print('OK')"
```

## Adding a Python module the app needs

`SimBrowser/make_app.sh` copies a **hardcoded list** of files into
`Contents/Resources` — currently `s2neighborhood.py s2parser.py s2ngbh.py
s2luastate.py s2ltw.py careers.json wants.json`. If `s2neighborhood.py` gains a
new import, add it to that list or the app launches fine and then fails at
extraction time with a bare `ImportError`, while `swift run` keeps working.
`careers.json` and `wants.json` are read relative to the module that loads them,
so they have to sit alongside it in the bundle.

## Where the game's data is

Saves default to the Aspyr Super Collection container:

```
~/Library/Containers/com.aspyr.sims2.appstore/Data/Library/Application Support/Aspyr/The Sims 2/Neighborhoods
```

`s2neighborhood.DEFAULT_ROOT` holds this; `s2doctor.ROOT_CANDIDATES` also tries
the non-sandboxed and EA-Games layouts. Extracted JSON and app state cache to
`~/Library/Application Support/SimBrowser/`.

`game-guides/`, `icons/`, and `teen_randomizer.sh` are local reference material,
gitignored on purpose — trait tables and icon art transcribed from the printed
guides. Don't commit them.

## Module ownership

| File | Owns |
|------|------|
| `s2parser.py` | DBPF container, QFS/RefPack **de**compression *and* compression, BHAV decompiler. Everything else imports this. |
| `s2neighborhood.py` | The JSON contract with the Swift app. SDSC, FAMI, LTXT, FAMt, SREL, CTSS → `sims.json`. Calls `s2ltw.annotate()` last, because the want evaluators read the relationships, ties, and businesses attached before it. |
| `s2ngbh.py` | NGBH token store — business rank/loyalty, talent badges, `sim_memories()` |
| `s2ltw.py` | Lifetime wants + per-want progress. A sim's LTW is the **first** record of their SWAF (`0xCD95548E`, one resource per sim, instance = sim nid). |
| `s2luastate.py` | Per-sim Lua tables (`0x3053CF74`) — OFB perks, Pets behaviors |
| `s2object.py` | Object resource parsers **and** builders (STR#, TTAB, OBJf, OBJD, BHAV assembler) |
| `s2clone.py` | The SimPE "Object Workshop" step — clone an object to a new identity, rewriting every reference so it coexists with its donor |
| `s2texture.py` | TXTR/LIFO → PNG. Owns the **generic RCOL reader**, which `s2mesh.py` reuses. |
| `s2mesh.py` | GMDC (`cGeometryDataContainer`) → Wavefront OBJ. Partial. |
| `s2writer.py` | DBPF writer (uncompressed or QFS-compressed with a DIR) + `read_all_resources()` |
| `s2savediff.py` | Save snapshot/diff — the discovery tool for unknown formats |
| `s2doctor.py` | Reads the game's own error logs; scans Downloads for conflicts |
| `make_wants.py` | Regenerates `wants.json` (want GUID → definition) from the game's own `Wants.package` |
| `sim_browser.py` | Legacy tkinter prototype, superseded by the app. Don't build on it. |

## Format traps that span files

These have each cost real debugging time and are easy to reintroduce:

- **Read instance ids through `ResourceEntry.instance`**, never the raw field.
  In an index v7.2 package the real instance lives in `instance_id2`; keying off
  the raw field collapses every resource onto 0.
- **Character-file slot numbers are not neighborhood sim ids.** Join character
  packages to SDSC records via the OBJD GUID at `0x5C` ↔ SDSC GUID at `0x1A6`.
- **Sim names are not unique** — every hood has 9–12 duplicate full names.
  Anything walking the family graph must join on tie ids, which is why
  `s2neighborhood.py` emits both `mother`/`siblings`/… and
  `mother_nid`/`sibling_nids`/….
- **FAMt sibling lists include in-laws, recorded one-way.** A tree builder that
  trusts both directions draws people twice.
- **CTSS entries are (language, value, description) triples.** Parse all three
  or in-game-born sims lose their last names.
- **Group `0xFFFFFFFF` is a per-package private namespace; `0x7FD46CD0` is
  global.** Both type and group are needed for real conflict detection.
- **OFB ownership is recorded in two places.** LTXT gives the complete owner
  list (home businesses included) but nothing else; rank and loyalty live in an
  NGBH `Token - Remote Business Data` (GUID `0x108F47DF`) that only exists once
  a *community* lot has been opened. A home business legitimately has an owner
  and no rank — don't "fix" that by blanking the row.

- **Textures and meshes are RCOL documents, not flat resources.** They are a
  self-describing chain of named blocks — the same container the scenegraph
  (CRES/SHPE/GMND/GMDC) uses. The generic reader lives in `s2texture.py`; reuse
  it rather than writing a second one.

When a format is unknown, the workflow that works is `s2savediff.py`: snapshot,
change exactly one thing in-game, snapshot again, diff. That's how the Business
Rewards Lua table was found.

## macOS 26 layout gotcha

A `ScrollView` in a window's root SwiftUI hierarchy can shift sibling views
~16pt off-window (Liquid Glass concentric insets). The app quarantines its
detail pane inside an `NSHostingView` to dodge this. `GeometryProbe.swift` is
the measurement tool that found it — launch with `SIMBROWSER_GEOMETRY_LOG=<path>`.

## Repo conventions

Work happens on a feature branch and lands through a PR against `main` — 25 of
the 31 commits on `main`'s first-parent history are PR merges. The public remote is
`github.com/GingerScripting/sims2parser`; save-game contents and screenshots of
real saves are personal data and don't go there without asking.
