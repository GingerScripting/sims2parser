#!/usr/bin/env python3
"""Regenerate wants.json — the want GUID → definition table used by s2ltw.py.

Want definitions ship as plain XML `cGZPropertySetString` resources (type
0xED7D7B4D) inside every `TSData/Res/Wants/Wants.package`; custom LTW packs put
the same resource in a package in Downloads. A want is a *lifetime* want when
its checkTree is one of the "CT - Test - Lifetime Want - …" trees, which is how
both Maxis and the custom packs mark them, so that is the filter used here.

    python3 make_wants.py                 # game install + game Downloads
    python3 make_wants.py --no-downloads  # Maxis wants only

Only lifetime wants are kept; the full set is ~1000 wants per EP and none of
the rest is wanted by anything in this repo.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from s2parser import open_package, read_resource

TID_WANT = 0xED7D7B4D

GAME_ASSETS = Path("/Applications/The Sims 2.app/Contents/Assets")
DOWNLOADS = (
    Path.home()
    / "Library/Containers/com.aspyr.sims2.appstore/Data"
      "/Library/Application Support/Aspyr/The Sims 2/Downloads"
)

LTW_TREE_PREFIX = "CT - Test - Lifetime Want"


def _field(xml: str, key: str) -> str:
    m = re.search(r'key="%s"[^>]*>(.*?)</Any' % re.escape(key), xml, re.S)
    return m.group(1).strip() if m else ""


def read_wants(pkg: Path) -> dict[int, dict]:
    """{want guid: definition} for every lifetime want defined in one package."""
    out: dict[int, dict] = {}
    try:
        _, entries = open_package(pkg)
    except Exception:
        return out
    with open(pkg, "rb") as f:
        for e in entries:
            if e.type_id != TID_WANT:
                continue
            try:
                xml = read_resource(f, e).decode("latin-1")
            except Exception:
                continue
            m = re.search(r'key="id"[^>]*>(0x[0-9a-fA-F]+)<', xml)
            if not m:
                continue
            check_tree = _field(xml, "checkTree")
            if not check_tree.startswith(LTW_TREE_PREFIX):
                continue
            out[int(m.group(1), 16)] = {
                "name": _field(xml, "nodeText"),
                "object_type": _field(xml, "objectType") or "None",
                "check_tree": check_tree,
                "folder": _field(xml, "folder"),
                "score": int(_field(xml, "score") or 0),
                "influence": int(_field(xml, "influence") or 0),
            }
    return out


def build(include_downloads: bool = True) -> dict[int, dict]:
    wants: dict[int, dict] = {}
    # Later EPs re-ship the earlier wants; sorting puts the newest copy last so
    # it wins, which matters for wants whose score was retuned by an EP.
    for pkg in sorted(GAME_ASSETS.rglob("TSData/Res/Wants/Wants.package")):
        wants.update(read_wants(pkg))
    if include_downloads and DOWNLOADS.is_dir():
        for pkg in sorted(DOWNLOADS.glob("*.package")):
            wants.update(read_wants(pkg))
    return wants


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-downloads", action="store_true",
                    help="skip custom LTW packs in the game Downloads folder")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "wants.json"))
    args = ap.parse_args()

    wants = build(include_downloads=not args.no_downloads)
    table = {f"0x{guid:08X}": w for guid, w in sorted(wants.items())}
    Path(args.out).write_text(json.dumps({"lifetime_wants": table}, indent=1) + "\n")
    print(f"Wrote {len(table)} lifetime wants → {args.out}")


if __name__ == "__main__":
    main()
