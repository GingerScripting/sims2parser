#!/usr/bin/env python3
"""
s2romance.py — a sim's romantic history, read from their memories.

The relationship panel (SREL) only knows how two sims stand *now*. What
happened between them — the first kiss, the dates, the WooHoo, the proposal
that was turned down, the affair that was caught — survives only as memory
tokens in the NGBH store (see s2ngbh.sim_memories). This module names the
romantic ones and groups them by the other sim.

Memory GUIDs are the OBJD GUIDs of the "Memory - …" objects in the game's
objects.package files (base game through Bon Voyage), read out by name.

Left out on purpose:
  * "Family - Family Engagement / Married / Left at Altar" — a *relative's*
    wedding, remembered by the family. The subject is the relative.
  * "N Simultaneous Loves" — a milestone about the sim themselves.
  * "Wealth - Marry Rich" — always accompanies a Marriage memory.

Read-only. Usage:
    python3 s2romance.py --hood path/to/N002 --sim "Ripp Grunt"
"""
from __future__ import annotations

import argparse
from pathlib import Path

import s2ngbh

# Kinds, in the order a courtship tends to run. The app colours and counts by
# kind; the label is what happened, in the owner's voice.
KINDS = ("date", "kiss", "makeout", "woohoo", "love", "steady", "engaged",
         "married", "affair", "rejected", "breakup")

EVENTS: "dict[int, tuple[str, str]]" = {
    # Nightlife dates
    0x6F9F6E45: ("date", "First date"),
    0xCF9F6E94: ("date", "Dream date"),
    0xCF9F6EA7: ("date", "Great date"),
    0x2F9F6F28: ("date", "Bad date"),
    0x6F9F6F3C: ("date", "Horrible date"),
    # Kissing
    0xCC89C448: ("kiss", "Very first kiss"),
    0x2DB54AE3: ("kiss", "First kiss together"),
    0x4DB54B44: ("makeout", "Made out"),
    # WooHoo. The NPC variant is what the woohoo lifetime want's check tree
    # adds to the plain one (s2ltw), so a partner can carry both.
    0x2C8CB358: ("woohoo", "WooHoo"),
    0xADCA2D1B: ("woohoo", "WooHoo"),
    0x8CAB091A: ("woohoo", "Public WooHoo"),
    0xEEB89C6E: ("woohoo", "Very first WooHoo"),
    0x908FBE48: ("woohoo", "First WooHoo with a robot"),
    # Commitments. The "Fear" variants replace the ordinary memory when the
    # sim had the matching fear rolled: it still happened.
    0x6C8CB22A: ("love", "Fell in love"),
    0x2CD8D374: ("steady", "Went steady"),
    0xADE8A617: ("steady", "Went steady (a fear)"),
    0xAC9BF983: ("engaged", "Got engaged"),
    0x8DD790A1: ("engaged", "Got engaged (a fear)"),
    0x6C9BF9C1: ("married", "Got married"),
    0x2DD790B1: ("married", "Got married (a fear)"),
    0x8EB89EAE: ("married", "Golden anniversary"),
    # Cheating
    0x0CAB0956: ("affair", "Cheated with"),
    0x8CAB0993: ("affair", "Caught cheating by"),
    0x0CCA82D1: ("affair", "Caught cheating"),
    # Turned down
    0x4C89E3FA: ("rejected", "First kiss rejected"),
    0x6DB8BEA5: ("rejected", "Make out rejected"),
    0xCC8CB6D8: ("rejected", "WooHoo rejected"),
    0x4CAB0932: ("rejected", "Public WooHoo rejected"),
    0xADB5508F: ("rejected", "Go steady rejected"),
    0x4C9BF9A7: ("rejected", "Proposal rejected"),
    # Endings
    0x4CD8D38C: ("breakup", "Broke up"),
    0x6DCC9ABD: ("breakup", "Broke up (a want)"),
    0xADC8AC35: ("breakup", "Engagement broken off"),
    0xADCFF200: ("breakup", "Engagement broken off (a want)"),
    0x6C9BFA3A: ("breakup", "Marriage ended"),
    0x4DCA29ED: ("breakup", "Marriage ended (a want)"),
    0x4C9BFA0B: ("breakup", "Left at the altar"),
}


def history(ngbh: dict, nid: int, name_of: "dict[int, str]") -> "list[dict]":
    """One entry per other sim, in order of first event:

        {"nid", "name", "counts": {kind: n}, "events": [{"kind", "label", "seq"}]}

    Order is the token store's. The game appends memories as they happen, so
    that is the true chronology; the date in values[1..3] is the calendar of
    the *lot* it happened on, and every lot starts its own clock at 6/15 —
    sorted by it, Ripp Grunt marries Laci a week before he first kisses her.
    `seq` numbers the sim's romantic events across all partners, so the app
    can interleave them into one timeline.
    """
    partners: "dict[int, dict]" = {}
    seq = 0
    for m in s2ngbh.sim_memories(ngbh, nid, EVENTS):
        other = m["subject"]
        if not other or other == nid:
            continue
        kind, label = EVENTS[m["guid"]]
        p = partners.setdefault(other, {
            "nid": other, "name": name_of.get(other, f"[{other}]"),
            "counts": {}, "events": []})
        p["counts"][kind] = p["counts"].get(kind, 0) + 1
        p["events"].append({"kind": kind, "label": label, "seq": seq})
        seq += 1
    return list(partners.values())


def annotate(sims: "dict[int, dict]", ngbh: dict, name_of: "dict[int, str]") -> None:
    for nid, s in sims.items():
        s["romance"] = history(ngbh, nid, name_of)


def main():
    import s2neighborhood
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--hood", type=Path, required=True,
                    help="neighborhood folder (its name must be the hood id, e.g. N002)")
    ap.add_argument("--sim", help="full name; default prints a per-hood tally")
    args = ap.parse_args()
    hood = s2neighborhood.extract_hood(args.hood)
    if hood is None:
        raise SystemExit(f"no {args.hood.name}_Neighborhood.package in {args.hood}")
    for s in hood["sims"]:
        full = f"{s['first']} {s['last']}".strip()
        if args.sim and full != args.sim:
            continue
        if not s["romance"]:
            continue
        if not args.sim:
            woohoo = sum(1 for p in s["romance"] if p["counts"].get("woohoo"))
            print(f"{full:32} {len(s['romance']):3} partners, {woohoo:3} WooHoo")
            continue
        print(f"{full} (#{s['nid']})")
        for p in s["romance"]:
            tally = ", ".join(f"{k} ×{p['counts'][k]}" for k in KINDS if k in p["counts"])
            print(f"  {p['name']} — {tally}")
            for e in p["events"]:
                print(f"      {e['seq']:3}  {e['label']}")


if __name__ == "__main__":
    main()
