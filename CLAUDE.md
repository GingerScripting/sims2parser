# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## The one hard rule

**Nothing in this project ever writes to a Sims 2 save.** The parsers are
strictly read-only against the game's neighborhood folders. `s2writer.py` and
`s2object.py` do write `.package` files, but only new standalone ones in a
scratch directory or `sample-packages/` — never back into a save.

## Two languages, one direction

```
Sims 2 saves ──▶ Python toolkit ──▶ sims.json ──▶ Sim Browser (SwiftUI)
(DBPF/QFS)        (all format work)   (cache)

.package ◀──▶ s2studio.py daemon ◀── JSON-RPC ──▶ Sim Studio (SwiftUI)
              (bytes, decoding, undo, save policy)  (views only)
```

All binary format knowledge lives in Python. The Swift apps only ever see
JSON — Sim Browser reads the extracted `sims.json`; Sim Studio talks to a
`s2studio.py --serve` process over stdin/stdout and never holds a package's
bytes (it gets hex for the hex view and decoded dataclasses as JSON for the
editors, and sends the same shapes back). Neither app can open a `.package`
itself and so neither can corrupt a save. Keep it that way: a new save-data
feature means a Python parser plus a JSON field, then Swift consumes it; a new
editable resource type means a parse/build pair in `s2object.py` registered
in `PARSERS`, and the daemon serves it with no Swift change beyond a form.
Never parse game bytes in Swift.

Sim Studio edits neighborhoods the same way: a `*_Neighborhood.package`
opens read-only, its Sims mode edits SDSC/SREL/NGBH through the ordinary
undo stack, and **Copy Hood** (`hood_save_as`) copies the whole hood folder
somewhere outside `Neighborhoods` and writes the edited package into the
copy. Opening a hood also runs `hoodcheck.inspect` (served in `hood_meta` as
`check`), and the Sims pane shows a red banner when the token store is
truncated. The game's container is not readable from a plain shell (TCC), so
the tests use the `s2savediff.py` snapshots under `~/Documents/sims2-savediff/`.

Sim Studio's read-only rule is enforced in the daemon, not the UI:
`s2studio.protection_reason()` refuses `save` on anything under a
`Neighborhoods` folder or inside the game's own install, and refuses
`save_as` into either. Save As to a copy elsewhere is allowed.

## Commands

```sh
./SimBrowser/make_app.sh              # build "Sim Browser.app" and "Sim Studio.app" in the repo root (universal)
ARCHS=native ./SimBrowser/make_app.sh # this machine only — much quicker while iterating
APPS=SimStudio ARCHS=native ./SimBrowser/make_app.sh   # just one app
cd SimBrowser && swift run SimBrowser # run an app from source, no bundle (or SimStudio)
cd SimBrowser && swift build          # compile check for SimKit + both apps
cd SimBrowser && swift run SimStudioDrive   # drives PackageSession through the daemon on a scratch donor
```

Sim Studio can be driven headless when its window can't be seen:
`SIMSTUDIO_TRACE=1` logs every RPC and detail load to stderr and
`SIMSTUDIO_OPEN=<file>` opens that package at launch. The editing flow
itself is covered by `cd SimBrowser && swift run SimStudioDrive`, a separate
executable that drives `PackageSession` — the layer every editor button
calls — against a scratch copy of a donor through the real daemon (select a
STR#, edit, apply, undo/redo, add/rename/compress/delete, save, re-open, BHAV
insert/apply/undo) and exits 0 or 1. It is not a test target because XCTest
and Swift Testing both need Xcode and the README asks only for the Command
Line Tools. It found the detail-pane decode bug and a stale-refresh race that
the Python smoke test could not see, but it is not a substitute for clicking
through the views. It skips when `sample-packages/` is absent.

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
python3 tests/rpc_smoke.py                # drives the Sim Studio daemon end to end + the read-only policy
```

…plus **empirical checks against canonical premades**, which is the real test
suite. If a change to the neighborhood readers can't still tell you that Daniel
Pleasant loves Mary-Sue while she's at −59 and falling, it's wrong. Run the
extractor over Pleasantview/Strangetown and eyeball known families.

`s2object.py` takes a directory and recurses, so the game's own install or a
Downloads folder can serve as a wide corpus — several thousand resources
rather than the few dozen in `sample-packages/`, which holds too few of most
types to prove a parser on (one GLOB, one SLOT, no TRCN). Use it whenever you
touch a parser:

```sh
python3 s2object.py "$HOME/Library/Containers/com.aspyr.sims2.appstore/Data/Library/Application Support/Aspyr/The Sims 2/Downloads"
```

It separates a parser **declining** a resource from a parser **corrupting**
one, and only the second fails the run. That distinction is load-bearing, so a
new parser must `raise ValueError` for a version or length it cannot handle
rather than guessing — declines are summarised by reason, while a resource that
parses and then rebuilds differently is a bug and gets named. A run that
verifies nothing also fails, so an empty result can't read as a pass.

**The wide corpus is not green today, and that is not your change.** Two known
gaps: `parse_ttab` handles 2 of the 11 TTAB versions in the wild (`TTAB_LAYOUTS`
has 0x4F and 0x54), and 41 TTAB/OBJD resources parse but do not rebuild
byte-identically — `CarOwnable_FordEdge.package` loses about 90% of a TTAB, and
around 21 OBJDs declare a name one byte longer than the resource holds, so the
parser silently truncates and the builder writes the shorter length back. The
default `python3 s2object.py` against `sample-packages/` is green.

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

`SimBrowser/make_app.sh` copies a **hardcoded list** of files into each
bundle's `Contents/Resources` — `BROWSER_FILES` for Sim Browser (the
extractor's import closure) and `STUDIO_FILES` for Sim Studio (the daemon's,
which is nearly the whole toolkit). If `s2neighborhood.py` or `s2studio.py`
gains a new import, add it to the right list or the app launches fine and
then fails at extraction time with a bare `ImportError`, while `swift run`
keeps working.
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

## Sim Studio layout

`SimBrowser/Package.swift` builds five targets: `SimKit` (shared:
`IsolatedPane`, `FlowLayout`, `SectionCard`, `Banner`, `PythonLocator`),
`SimBrowser`, `SimStudioCore` (the RPC client, `JSONValue`, the Codable
mirrors of the daemon's replies, and `PackageSession` — one open package,
`@MainActor` — all `public`), `SimStudio` (the views only: three-pane window,
type tree, resource `Table`, detail pane with Decoded/Tree/Preview/Hex tabs,
one editor per decodable type under `Views/Editors/`), and `SimStudioDrive`
(the headless editing-flow check). It is
deliberately **not** a document-based app: `FileDocument` would hand Swift
the bytes. Each package is a `WindowGroup(for: URL.self)` window owning one
daemon process. Calling `openWindow` inside the first `onAppear` or the
launch-time open-URL event races window creation and produces two windows
for one URL, so every open is deferred by a beat.

## Module ownership

| File | Owns |
|------|------|
| `s2parser.py` | DBPF container, QFS/RefPack **de**compression *and* compression, BHAV decompiler. Owns `BHAV_LAYOUTS`, the per-format instruction layout table (0x8000–0x8009), and reads every format into one widened shape. Everything else imports this. |
| `s2neighborhood.py` | The JSON contract with the Swift app. SDSC, FAMI, LTXT, FAMt, SREL, CTSS → `sims.json`. Calls `s2ltw.annotate()` last, because the want evaluators read the relationships, ties, and businesses attached before it. Also the editor's view of SDSC and SREL: `SDSC_FIELDS`/`SREL_FIELDS` name the offsets the reader uses, and `build_sdsc`/`build_srel` write only those over a copy of the original record (`--selftest HOOD_DIR` proves the pair on every record). |
| `s2ngbh.py` | NGBH token store — business rank/loyalty, talent badges, `sim_memories()`. `parse_ngbh_rt`/`build_ngbh_rt` are the byte-exact pair the editor uses (both token lists per group, the unread header bytes, the rare trailing word); a store the reader has to resync past is refused rather than rebuilt with a hole. |
| `s2ltw.py` | Lifetime wants + per-want progress. A sim's LTW is the **first** record of their SWAF (`0xCD95548E`, one resource per sim, instance = sim nid). |
| `s2luastate.py` | Per-sim Lua tables (`0x3053CF74`) — OFB perks, Pets behaviors |
| `s2object.py` | Object resource parsers **and** builders (STR#, TTAB, OBJf, OBJD, BCON, GLOB, and the byte-exact BHAV pair `parse_bhav_rt`/`build_bhav` plus `bhav_convert`), the from-scratch BHAV assembler, and `BHAV_OPERAND_LAYOUTS` for the editor |
| `s2clone.py` | The SimPE "Object Workshop" step — clone an object to a new identity, rewriting every reference so it coexists with its donor. Sim Studio's Tools ▸ Clone Object runs it in place as one undo step. |
| `s2texture.py` | TXTR/LIFO → PNG. Owns the **generic RCOL reader**, which `s2mesh.py` reuses. |
| `s2mesh.py` | GMDC (`cGeometryDataContainer`) → Wavefront OBJ. Partial. |
| `s2writer.py` | DBPF writer (uncompressed or QFS-compressed with a DIR, whole-package or per-TGI via `compress_tgis`) + `read_all_resources()`. Writes a still-packed `LazyResource` as-is. |
| `s2studio.py` | The Sim Studio daemon: JSON-RPC over stdio, one session per open package, undo stack, the read-only policy (`protection_reason`), decoded↔JSON conversion (`to_json`/`from_json`, with `$type`, `$hex`, `$props`). |
| `s2tools.py` | Merge one package's resources into another and split a selection out — pure list operations plus a small CLI. |
| `s2package.py` | Pure in-memory package operations the daemon and its undo stack share, and `LazyResource` — a compressed resource that inflates on first access so objects.package opens in a quarter second. |
| `hoodcheck.py` | Detects a truncated NGBH token store (declared vs actual sim groups, 8 KB chunk alignment) and writes a padded or trimmed **copy**. `inspect_bytes` is the in-memory form the daemon runs on every hood open and edit; `Report.verdict()` is the one wording both the CLI and the app show. |
| `s2savediff.py` | Save snapshot/diff — the discovery tool for unknown formats |
| `s2doctor.py` | Reads the game's own error logs; scans Downloads for conflicts |
| `make_wants.py` | Regenerates `wants.json` (want GUID → definition) from the game's own `Wants.package` |
| `sim_browser.py` | Legacy tkinter prototype, superseded by the app. Don't build on it. |

## Adding a resource type

`.claude/skills/decode-resource-type/` is a skill in this repo covering the
workflow every parser here went through, with `scripts/survey.py` bundling the
corpus work — count specimens, dump offset-labelled bytes, test a length
hypothesis against every specimen, verify a registered parser round-trips.

The one step worth not skipping is testing the layout corpus-wide *before*
writing code. Reading BCON's count as a `u16` rather than a byte fits 561 of
912 specimens: 61.5% is high enough to look like a near-miss and low enough to
be a completely wrong reading.

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
- **BHAV formats differ in width, not just header.** 0x8000–0x8006 have
  one-byte branch targets (sentinels 0xFD/0xFE/0xFF) and 0x8000–0x8002 only 8
  operand bytes; the game's own objects.package is mostly these. The editable
  model widens everything to the 0x8007 shape, so `s2package`'s instruction
  ops and the app treat every tree the same; `build_bhav` refuses an edit the
  original format cannot hold and `bhav_convert` lifts the tree to 0x8007.
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
- **Ask the DIR whether a resource is compressed; never sniff for the magic.**
  A package's DIR lists exactly what is QFS-compressed, and no DIR means
  nothing is (checked against 4,000 of the game's own packages: the DIR agrees
  with a sniff on all 3,778 that carry one, and none of the 222 without one
  hold a compressed resource). Testing bytes 4:6 for `0x10FB` instead is a
  guess that fails on any stored resource whose payload happens to start with
  four bytes and then those two — a 4,006-byte resource came back as 9,109,547
  bytes of garbage. `s2parser.read_dir()` gives the answer; `read_resource`
  takes it as `compressed=`, and only falls back to sniffing when nothing
  passes one.

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
