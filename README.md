# Sim Browser & sims2parser

A native macOS browser for **The Sims 2** save files — look up any sim and see
their family, address, career, university major, relationships, and life story,
SimPE-style but Mac-native — plus the Python toolkit that parses the game's
DBPF `.package` format underneath it.

Built for the Aspyr **Super Collection** on macOS. Strictly **read-only**: the
app never writes a single byte to your saves.

![Sim Browser main window](docs/browser.png)

## The app

**Sim Browser** reads every neighborhood in your save folder and gives you:

- **Every sim, one click away** — name, bio, age, zodiac, aspiration,
  orientation, household + address + family funds, career with the real
  in-game job title ("State Assemblyperson", not "Politics 6"), retired
  career, and university major with semester.
- **Family & relationships** — parents, spouse, siblings, and children as
  clickable links, plus every meaningful relationship with daily/lifetime
  scores and flags (love, married, best friends, enemies, BFF).
- **Family tree** — an hourglass chart five generations tall: grandparents,
  parents, the sim with their siblings and spouses, children, grandchildren.
  Couples are joined solid pink when the save records a marriage and dashed
  when they only share children (divorced, widowed, or abducted by aliens —
  hello, Bella). Click any box to re-centre the tree on that sim, which walks
  the whole hood one relative at a time.
- **Personality, skills, and interests** as SimPE-style meters.
- **Life-state awareness** — Young Adults at university, unplaced sims in the
  Family Bin, and the dearly departed (deaths are detected from ghost flags)
  each get their own badge and filter.
- **Search and stackable filters** — playable/townie, age stage, gender,
  aspiration, married, in college, employed, retired, has children, in love,
  has enemies, deceased.
- **CSV export** — entire neighborhood, the currently filtered list, or just
  the selected sims, in a spreadsheet-friendly column layout.
- **Randomizer** (the dice in the toolbar) — two rollers. *Event* draws a
  gameplay prompt from your planning spreadsheet, falling back to a built-in
  list. *New Teen* rolls what the game asks for when a child grows up: one of
  the six Aspirations, two Turn Ons and a Turn Off, never repeating a trait
  across the three slots. Each slot has its own re-roll so you can keep the
  parts you like. The 33 traits are transcribed from the printed guides —
  19 from Nightlife ch. 4, 14 more from Bon Voyage ch. 1 — not from memory.

### The journal

A per-neighborhood play journal with one entry per season ("01 Spring",
"01 Summer", … — the next season name is suggested automatically).

![Journal with detected changes](docs/journal.png)

The killer feature: hit **⌘R** after a play session and the app **diffs your
saves against the last read** and drafts the entry for you — marriages,
births, deaths, age-ups, going off to college, promotions, moves, new loves,
new enemies. One click inserts them as bullets you can edit. And any sim
mentioned by name in an entry gets a **Journal** section on their detail page
linking back to every season they appear in: write hood-wide, read per-sim.

## Install & run

```sh
git clone https://github.com/GingerScripting/sims2parser.git
cd sims2parser
./SimBrowser/make_app.sh     # builds "Sim Browser.app" in the repo root
open "Sim Browser.app"
```

Requirements: macOS 13+, Xcode command-line tools (for `swift build`),
Python 3.9+ (system Python is fine), and The Sims 2 Super Collection with at
least one saved neighborhood.

First launch reads your neighborhoods automatically (a few seconds). **⌘R**
re-reads them any time. Data is cached in
`~/Library/Application Support/SimBrowser/`.

## How it works

```
Sims 2 saves ──▶ s2parser.py ──▶ s2neighborhood.py ──▶ sims.json ──▶ SwiftUI app
(DBPF/QFS)       (container)     (SDSC, FAMI, LTXT,     (cache)      (SimBrowser/)
                                  FAMt, SREL, CTSS)
```

All file-format work happens in Python; the Swift app only ever reads the
extracted JSON. That split keeps the save-file logic in one place (and keeps
the app trivially incapable of corrupting a save).

| File | What it does |
|------|--------------|
| `s2parser.py` | DBPF container + QFS/RefPack decompression + BHAV (SimAntics) decompiler. Also a CLI: `python3 s2parser.py --bhav file.package` |
| `s2neighborhood.py` | Turns a neighborhood's packages into JSON: sims, households, lots, family ties, relationships. `python3 s2neighborhood.py --out sims.json` |
| `careers.json` | Career/major GUID → name and per-level job titles, harvested from the game's own `objects.package` files (base + every EP) |
| `s2writer.py` / `s2object.py` | DBPF *writer* and resource builders (BHAV, TTAB, OBJD…) used by companion projects that generate custom objects |
| `SimBrowser/` | The SwiftUI app ([its own README](SimBrowser/README.md)) |
| `sim_browser.py` | Legacy tkinter prototype — superseded by the app |

### Reverse-engineering notes

The neighborhood formats (SDSC sim descriptions, FAMI households, LTXT lot
text, FAMt family ties, SREL relationships) were decoded against
[SimsWiki](https://simswiki.info/wiki.php?title=SDSC) documentation and
verified empirically against canonical premades — if the parser can't tell
you that Daniel Pleasant loves Mary-Sue while she's at −59 and falling, it
isn't parsing Pleasantview correctly. Two hard-won details for fellow
travelers:

- Character-file slot numbers are **not** neighborhood sim ids; join
  character packages to SDSC records via the OBJD GUID at `0x5C` ↔ SDSC GUID
  at `0x1A6`.
- Sim **names are not unique** — every hood here has 9–12 duplicated full
  names (townies, NPCs, repeated premades). Anything that walks the family
  graph rather than just printing it has to join on tie ids, which is why
  `s2neighborhood.py` emits both `mother`/`siblings`/… and
  `mother_nid`/`sibling_nids`/….
- FAMt sibling lists can include **in-laws**, recorded one-way: Annabelle
  Goldstein lists her brother's wife as a sibling, while the wife lists no
  siblings back. A tree builder that trusts both directions draws her twice.
- CTSS text entries are (language, value, description) triples; parse all
  three or in-game-born sims lose their last names.

And one for macOS developers: on macOS 26, a `ScrollView` in a window's root
SwiftUI hierarchy can shift sibling views ~16pt off-window (Liquid Glass
"concentric" insets). This app quarantines its detail pane inside an
`NSHostingView` to avoid it — see `GeometryProbe.swift` for the measurement
tool that found it.

## Credits

Format documentation by the modding community at
[SimsWiki](https://simswiki.info) and
[Pick'N'Mix Mods](https://www.picknmixmods.com). Sample packages by
Christianlov, TwoJeffs, and others, used for format reference. Built with
[Claude Code](https://claude.com/claude-code).
