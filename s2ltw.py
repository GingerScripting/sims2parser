#!/usr/bin/env python3
"""Sims 2 lifetime wants — which one a sim holds, and how far along they are.

A sim's lifetime want is the *first* record of their SWAF (0xCD95548E, one
resource per sim, instance = sim nid). Layout, verified against Strangetown:

    u32 version (6)      u32 record count      u32 entry version (10)
    u16 sim nid          u32 want GUID         u8  object type
    u32 object GUID      — present only when object type != 0
    u32 target ("$int" in the want's name)     u32 0
    u32 aspiration score u32 influence score

`wants.json` maps the want GUID to its name and check tree; regenerate it with
`make_wants.py` after installing new custom LTW packs.

Progress is *not* stored — the game recomputes it from a per-want "CT - Test -
Lifetime Want - …" BHAV every time it draws the panel — so this module
reimplements the readable ones against the save. Each result carries the
`basis` it was computed from and a `confidence`:

    exact    reimplements what the check tree counts
    approx   a defensible stand-in the game does not compute quite this way
    unknown  no evaluator; `progress` is None (the want and target still read)

Completion is more reliable than progress: fulfilling an LTW leaves a
"Memory - Lifetime - …" token on the sim, so `done` can be true even where
progress is unknown.
"""

# Annotations stay strings so the modules import under the system
# python3 (3.9), which the app gets when launched from Finder.
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

import s2ngbh
from s2parser import open_package, read_resource

TID_SWAF = 0xCD95548E

_SWAF_VERSION = 6
_LTW_ENTRY_VERSION = 10

_here = Path(__file__).resolve().parent
try:
    WANTS = {int(k, 16): v
             for k, v in json.loads((_here / "wants.json").read_text())["lifetime_wants"].items()}
except Exception:
    WANTS = {}

try:
    CAREER_NAMES = {int(k, 16): v["track"]
                    for k, v in json.loads((_here / "careers.json").read_text())["careers"].items()}
except Exception:
    CAREER_NAMES = {}


# ---------------------------------------------------------------------------
# SWAF
# ---------------------------------------------------------------------------

def parse_ltw(d: bytes) -> dict | None:
    """The lifetime want out of one SWAF resource, or None if it holds none.

    Children and toddlers get a SWAF with no records, and a scattering of sims
    carry an older version 1 layout this does not read; both come back None.
    """
    if len(d) < 20:
        return None
    version, count, entry_version = struct.unpack_from("<III", d, 0)
    if version != _SWAF_VERSION or count < 1 or entry_version != _LTW_ENTRY_VERSION:
        return None
    nid, want_guid = struct.unpack_from("<HI", d, 12)
    object_type = d[18]
    pos = 19
    object_guid = None
    if object_type:
        object_guid, = struct.unpack_from("<I", d, pos)
        pos += 4
    if pos + 16 > len(d):
        return None
    target, _, score, influence = struct.unpack_from("<IIII", d, pos)
    want = WANTS.get(want_guid, {})
    return {
        "nid": nid,
        "want_guid": want_guid,
        "name": (want.get("name", "") or f"[want 0x{want_guid:08X}]").replace("$int", str(target)),
        "object_type": want.get("object_type", "Career" if object_type else "None"),
        "object_guid": object_guid,
        "check_tree": want.get("check_tree", ""),
        "target": target,
        "score": score,
        "influence": influence,
    }


def load_ltws(nbr_dir: Path) -> dict[int, dict]:
    """{sim nid: lifetime want} for one neighborhood directory."""
    npkg = nbr_dir / f"{nbr_dir.name}_Neighborhood.package"
    if not npkg.exists():
        return {}
    _, entries = open_package(npkg)
    out: dict[int, dict] = {}
    with open(npkg, "rb") as f:
        for e in entries:
            if e.type_id != TID_SWAF:
                continue
            try:
                ltw = parse_ltw(read_resource(f, e))
            except Exception:
                continue
            if ltw:
                out[ltw["nid"]] = ltw
    return out


# ---------------------------------------------------------------------------
# Memory GUIDs the evaluators count
#
# Read off the OBJD names in the game's objects.package, and cross-checked
# against the Manage Inventory (0x0033) operands in each want's check tree.
# ---------------------------------------------------------------------------

MEM_WOOHOO = 0x2C8CB358          # Memory - Love - WooHoo
MEM_WOOHOO_PUBLIC = 0x8CAB091A   # …- Public
MEM_WOOHOO_NPC = 0xADCA2D1B      # …- NPC
MEM_DREAM_DATE = 0xCF9F6E94
MEM_FIRST_DATE = 0x6F9F6E45

# "Memory - Lifetime - …": dropped on the sim when the want is fulfilled. Keyed
# by check-tree suffix rather than want GUID so the custom packs that reuse a
# Maxis tree resolve too.
FULFILLED_MEMORY = {
    "Max X Skills": 0x2EB8B7A4,
    "Woohoo": 0x8EB8B8AB,
    "Lots of Grandchildren": 0xEEB8B7B8,
    "Simultaneous Best Friends": 0xCEB8B7D3,
    "Simultaneous Lovers": 0x6EB8B7EC,
    "Marry Off Children": 0xEEB8B88D,
    "Graduate Children from College": 0x8EB8B75D,
    "Lots of Money": 0x2EB8B742,
    "Have X Dream Dates": 0xEFDA8C42,
    "Have X First Dates": 0xAFDA8C56,
    "Own X Max Rank Businesses": 0xB090D562,
    "Eat Grilled Cheese": 0xAFDA8C30,
    "Pet Best Friend": 0xD1B02096,
}

CT_PREFIX = "CT - Test - Lifetime Want - "


def _tree(ltw: dict) -> str:
    ct = ltw.get("check_tree", "")
    return ct[len(CT_PREFIX):] if ct.startswith(CT_PREFIX) else ct


# ---------------------------------------------------------------------------
# Evaluators
#
# Each takes (ltw, sim, ctx) and returns (progress, basis, confidence, detail).
# ctx carries the whole hood: {'sims': {nid: sim}, 'ngbh': parsed NGBH}.
# ---------------------------------------------------------------------------

def _eval_career(ltw, sim, ctx):
    """Every "Become a …" want: the target is a level in one career track."""
    guid = ltw["object_guid"]
    if guid is None:
        return None, "", "unknown", []
    track = CAREER_NAMES.get(guid, f"career 0x{guid:08X}")
    level = 0
    if sim.get("career_guid") == guid:
        level = sim.get("career_level", 0)
    elif sim.get("retired_guid") == guid:
        level = sim.get("retired_level", 0)
    return level, f"level in {track}", "exact", []


def _eval_max_skills(ltw, sim, ctx):
    maxed = [name for name, v in sim.get("skills", {}).items() if v >= 1000]
    return len(maxed), "skills at 10", "exact", maxed


def _eval_lovers(ltw, sim, ctx):
    names = [r["name"] for r in sim.get("relationships", []) if "Love" in r["flags"]]
    return len(names), "relationships flagged Love", "exact", names


def _eval_best_friends(ltw, sim, ctx):
    names = [r["name"] for r in sim.get("relationships", [])
             if "Best Friend" in r["flags"] or r.get("bff")]
    return len(names), "relationships flagged Best Friend", "exact", names


def _eval_grandchildren(ltw, sim, ctx):
    sims = ctx["sims"]
    grandkids = []
    for child in sim.get("children_nids", []):
        for grandchild in sims.get(child, {}).get("children_nids", []):
            if grandchild not in grandkids:
                grandkids.append(grandchild)
    names = [_name(sims, n) for n in grandkids]
    return len(grandkids), "children of this sim's children", "exact", names


def _eval_married_off(ltw, sim, ctx):
    """The game watches for the wedding; the save only keeps the spouse tie, so
    a child widowed or divorced since stops counting here and should not."""
    sims = ctx["sims"]
    names = [_name(sims, c) for c in sim.get("children_nids", [])
             if sims.get(c, {}).get("spouse_nid") is not None]
    return len(names), "children holding a spouse tie", "approx", names


def _memory_counter(guids, basis, *, subjects=False):
    def evaluate(ltw, sim, ctx):
        mems = s2ngbh.sim_memories(ctx["ngbh"], sim["nid"], guids)
        detail = []
        if subjects:
            seen = []
            for m in mems:
                if m["subject"] is not None and m["subject"] not in seen:
                    seen.append(m["subject"])
            detail = [_name(ctx["sims"], n) for n in seen]
        return len(mems), basis, "exact", detail
    return evaluate


def _eval_top_businesses(ltw, sim, ctx):
    """Credits the whole household: the save records who *owns* a business, not
    which member the want is watching. A home business has no rank at all (it
    lives in the lot package), so those never count here."""
    members = ctx.get("family_members", {})
    names = [b["name"] for b in ctx.get("businesses", [])
             if b.get("rank") == 10
             and sim["nid"] in members.get(b.get("owner_family_id"), ())]
    return len(names), "household businesses at rank 10", "approx", names


EVALUATORS = {
    "Max X Skills": _eval_max_skills,
    "Simultaneous Lovers": _eval_lovers,
    "Simultaneous Best Friends": _eval_best_friends,
    "Lots of Grandchildren": _eval_grandchildren,
    "Marry Off Children": _eval_married_off,
    "Own X Max Rank Businesses": _eval_top_businesses,
    # The check tree adds the plain and NPC WooHoo memories into one total and
    # does not fold repeats with the same sim, so a partner who turns up as both
    # counts twice — the detail list is the distinct sims.
    "Woohoo": _memory_counter((MEM_WOOHOO, MEM_WOOHOO_NPC),
                              "WooHoo memories", subjects=True),
    "Have X Dream Dates": _memory_counter((MEM_DREAM_DATE,), "dream date memories"),
    "Have X First Dates": _memory_counter((MEM_FIRST_DATE,), "first date memories"),
}


def _name(sims: dict, nid: int) -> str:
    s = sims.get(nid)
    return f'{s["first"]} {s["last"]}'.strip() if s else f"[{nid}]"


def evaluate(ltw: dict, sim: dict, ctx: dict) -> dict:
    """Progress for one sim's lifetime want. Never raises: an unreadable want
    still reports its name and target with progress None."""
    tree = _tree(ltw)
    evaluator = EVALUATORS.get(tree)
    if evaluator is None and ltw["object_type"] == "Career":
        evaluator = _eval_career

    progress, basis, confidence, detail = None, "", "unknown", []
    if evaluator is not None:
        try:
            progress, basis, confidence, detail = evaluator(ltw, sim, ctx)
        except Exception:
            progress, basis, confidence, detail = None, "", "unknown", []

    fulfilled = FULFILLED_MEMORY.get(tree)
    done = False
    if fulfilled is not None:
        done = bool(s2ngbh.sim_memories(ctx["ngbh"], sim["nid"], (fulfilled,)))
    if not done and progress is not None:
        done = progress >= ltw["target"]

    return {
        "name": ltw["name"],
        "target": ltw["target"],
        "progress": progress,
        "done": done,
        "basis": basis,
        "confidence": confidence,
        "detail": detail,
        "check_tree": ltw["check_tree"],
    }


def annotate(hood: dict, ngbh: dict, ltws: dict[int, dict]) -> None:
    """Attach an 'ltw' key to every sim in an extracted hood, in place."""
    sims = {s["nid"]: s for s in hood["sims"]}
    family_members = {f["id"]: set(f.get("member_nids", [])) for f in hood["families"]}
    ctx = {"sims": sims, "ngbh": ngbh, "businesses": hood.get("businesses", []),
           "family_members": family_members}
    for nid, sim in sims.items():
        ltw = ltws.get(nid)
        sim["ltw"] = evaluate(ltw, sim, ctx) if ltw else None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _ratio(ltw) -> float:
    if ltw["done"]:
        return 1.0
    if ltw["progress"] is None or not ltw["target"]:
        return -1.0
    return min(1.0, ltw["progress"] / ltw["target"])


def _bar(progress, target, width=20):
    if progress is None:
        return "no evaluator".center(width, " ")
    filled = min(width, int(width * progress / target)) if target else 0
    return "█" * filled + "·" * (width - filled)


def main() -> None:
    import s2neighborhood  # imported here: s2neighborhood imports this module

    ap = argparse.ArgumentParser(description="Sims 2 lifetime want progress")
    ap.add_argument("--root", default=str(s2neighborhood.DEFAULT_ROOT))
    ap.add_argument("--hood", default="N001", help="neighborhood folder, e.g. N002")
    ap.add_argument("--sim", help="only sims whose name contains this")
    ap.add_argument("--unfinished", action="store_true", help="hide fulfilled wants")
    ap.add_argument("--json", action="store_true", help="dump the records instead")
    args = ap.parse_args()

    nbr_dir = Path(args.root) / args.hood
    hood = s2neighborhood.extract_hood(nbr_dir)
    if not hood:
        raise SystemExit(f"no neighborhood at {nbr_dir}")

    rows = [s for s in hood["sims"] if s.get("ltw")]
    if args.sim:
        needle = args.sim.lower()
        rows = [s for s in rows
                if needle in f'{s["first"]} {s["last"]}'.lower()]
    if args.unfinished:
        rows = [s for s in rows if not s["ltw"]["done"]]

    if args.json:
        print(json.dumps([{"sim": f'{s["first"]} {s["last"]}', **s["ltw"]}
                          for s in rows], indent=1))
        return

    print(f"{hood['name']} — {len(rows)} sims with a lifetime want\n")
    # Closest to their want first: that is the question this list gets asked.
    for s in sorted(rows, key=lambda s: (-_ratio(s["ltw"]), s["last"], s["first"])):
        ltw = s["ltw"]
        got = "?" if ltw["progress"] is None else ltw["progress"]
        mark = "✓" if ltw["done"] else " "
        name = f'{s["first"]} {s["last"]}'.strip()
        print(f'{mark} {name:26.26s} {_bar(ltw["progress"], ltw["target"])} '
              f'{got:>5} / {ltw["target"]:<6} {ltw["name"]}')
        if args.sim:
            note = f'    {ltw["basis"] or "no evaluator"} ({ltw["confidence"]})'
            print(note)
            if ltw["detail"]:
                print(f'    {len(ltw["detail"])} distinct: {", ".join(map(str, ltw["detail"]))}')


if __name__ == "__main__":
    main()
