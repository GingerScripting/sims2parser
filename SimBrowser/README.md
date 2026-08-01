# Sim Browser

A SimPE-style, read-only browser for The Sims 2 (Aspyr Super Collection) save files.
Native SwiftUI front end; all file parsing happens in Python (`../s2neighborhood.py`).

## Run it

Double-click **Sim Browser.app** in the project folder, or rebuild it:

```sh
./make_app.sh          # builds release + assembles ../「Sim Browser.app」
```

For development: `swift run` inside this directory.

## How it works

1. `s2neighborhood.py` reads every neighborhood under the Aspyr container
   (`~/Library/Containers/com.aspyr.sims2.appstore/…/The Sims 2/Neighborhoods`)
   and writes JSON to `~/Library/Application Support/SimBrowser/sims.json`.
   It parses SDSC (sim description), FAMI (households), LTXT (lot addresses),
   FAMt (family ties), SREL (relationships), and CTSS (names/bios), plus
   career/major names extracted from the game's own `objects.package` files
   (cached in `../careers.json`).
2. The app loads that JSON. **⌘R** (or the toolbar button) re-runs the extractor
   to pick up new game saves. Everything is read-only — nothing ever writes to
   the save files.

Per sim you get: name, age, gender, zodiac, aspiration, orientation, household
+ address + funds, career with real job title and level, retired career,
university major, bio, parents/spouse/siblings/children (clickable),
personality/skills/interests, and flagged relationships (love, married, BFF,
enemy, …) with daily/lifetime scores.

## CSV export

The share button in the toolbar exports to CSV (UTF-8 with BOM, opens cleanly
in Numbers/Excel) with three scopes:

- **Entire Neighborhood** — every sim in the current hood
- **Current List** — exactly what the sidebar shows (search + filters, in order)
- **Selected Sims** — ⌘-click or ⇧-click to multi-select rows first

Columns match the "Sims Reboot" spreadsheet style: First, Last, Household,
Address, Hood, Age, Gender, Sign, Ambition, Major, Career, Job Title, Career
Level, Retired From, Orientation, Mother, Father, Spouse, Siblings, Children,
Best Friend, Loves, Enemies, Funds, Bio.

## Settings

- Extractor location. The app uses the copy bundled in `Contents/Resources`,
  falling back to a source checkout when run via `swift run`. To point it
  somewhere else:
  `defaults write org.macadmins.rebecca.simbrowser extractorPath /path/to/s2neighborhood.py`
