#!/usr/bin/env python3
"""Sims 2 per-sim Lua state tables (resource type 0x3053CF74).

The game keeps some sim state on its script side rather than in Simantics
attributes or NGBH tokens, and serializes it into one resource per sim in the
neighborhood package. Two tables turn up in practice:

    "Business Rewards"   Open for Business perks, plus unspent perk points
    "Learned Behaviors"  Pets training progress, keyed by behavior GUID

Layout (verified against every one of the 2787 such resources across five
neighborhoods in the Aspyr Super Collection — all parse to exactly their
resource length):

    u32 1, u32 1, u32 kind      kind is 1 for Business Rewards, 3 for
                                Learned Behaviors
    u32 name-len, name
    u32 entry count, then that many key/value pairs

Keys and values are both tagged with a one-byte type:

    0x00 float32   0x01 int32   0x03 boolean (as u32)   0x04 u32 len + bytes

Business Rewards uses string keys, Learned Behaviors 4-byte numeric ones. A
number is written as whichever of float/int the script side happened to be
holding, so "Reward Points" comes back 1 on one sim and 1.0 on the next —
read it through perk_points(), which normalizes.

The resource's instance_id2 is the sim's neighborhood id, the same key
s2ngbh.sim_badges() returns.

Found by diffing a save before and after buying a single perk in game; see
s2savediff.py, which is the tool for repeating that trick.
"""

# Annotations stay strings so the modules import under the system
# python3 (3.9), which the app gets when launched from Finder.
from __future__ import annotations

import struct

TID_LUA_STATE = 0x3053CF74

TABLE_BUSINESS_REWARDS = "Business Rewards"
TABLE_LEARNED_BEHAVIORS = "Learned Behaviors"

POINTS_KEY = "Reward Points"

# Internal perk keys in track order, tier 1 first — the order the dialog
# fills each column bottom to top. Display names come from the Business
# Rewards object (GUID 0x50728F08, STR# 0x93); the keys the save actually
# uses are terser, and the Cash and Wholesale tracks aren't named at all.
#
# The tier order is corroborated by the data: perks in a track can only be
# bought in sequence, and every sim in every neighborhood owns a prefix of
# each track under this ordering.
BUSINESS_PERKS = {
    "Connections": [
        ("Notable Reputation", "Notable Reputation"),
        ("Sterling Reputation", "Sterling Reputation"),
        ("Talk.../Network", "Network"),
        ("Head For Numbers", "Head for Numbers"),
        ("Talk.../Power Network", "Power Network"),
    ],
    "Perception": [
        ("Assess.../Mood", "Assess Mood"),
        ("Assess.../Desire", "Assess Desire"),
        ("Look For Mark", "Look for Mark"),
        ("Convincing Personality", "Convincing Personality"),
        ("Shameless Manipulation", "Manipulation"),
    ],
    "Cash": [
        ("Cash 1", "LeTourneau Prize"),
        ("Cash 2", "Valued Client Rebate"),
        ("Cash 3", "Chamber of Commerce Prize"),
        ("Cash 4", "Owners Association Award"),
        ("Cash 5", "Will Wright Grant"),
    ],
    "Wholesale": [
        ("Wholesale 1", "Wholesale Discount"),
        ("Wholesale 2", "Supplier Partnership"),
        ("Wholesale 3", "Bargain Hunter"),
        ("Wholesale 4", "Serious Negotiator"),
        ("Wholesale 5", "Shark of Sharks"),
    ],
    "Motivation": [
        ("Simply Influential", "Simply Influential"),
        ("Cheer Up", "Perk Up"),
        ("Motivational Speech", "Motivational Speech"),
        ("Boundless Influence", "Boundless Influence"),
        ("Rally Forth", "Rally Forth!"),
    ],
}

PERK_NAMES = {key: display
              for track in BUSINESS_PERKS.values()
              for key, display in track}


# --- parsing ----------------------------------------------------------------

def _read_value(d: bytes, pos: int):
    tag = d[pos]
    pos += 1
    if tag == 0x00:
        return struct.unpack_from("<f", d, pos)[0], pos + 4
    if tag == 0x01:
        return struct.unpack_from("<i", d, pos)[0], pos + 4
    if tag == 0x03:
        return bool(struct.unpack_from("<I", d, pos)[0]), pos + 4
    if tag == 0x04:
        n, = struct.unpack_from("<I", d, pos)
        pos += 4
        return d[pos:pos + n].decode("latin-1"), pos + n
    raise ValueError(f"unknown type tag 0x{tag:02X} at offset {pos - 1}")


def parse_state_table(d: bytes) -> tuple[str, dict]:
    """(table name, {key: value}) for one 0x3053CF74 resource."""
    pos = 12                                    # 1, 1, kind
    n, = struct.unpack_from("<I", d, pos)
    pos += 4
    name = d[pos:pos + n].decode("latin-1")
    pos += n
    count, = struct.unpack_from("<I", d, pos)
    pos += 4
    table = {}
    for _ in range(count):
        key, pos = _read_value(d, pos)
        value, pos = _read_value(d, pos)
        table[key] = value
    return name, table


# --- business perks ---------------------------------------------------------

def perk_points(table: dict) -> int:
    """Unspent perk points, however the script side happened to store them."""
    return int(table.get(POINTS_KEY, 0) or 0)


def sim_perks(table: dict) -> dict:
    """{'points': int, 'perks': {track: [display name, tier 1 first]}}.

    Entries are only written once bought, so a missing key is an unbought
    perk. Anything unrecognised is passed through under "Other" rather than
    dropped — a stock game shouldn't produce any, but mods might.
    """
    owned = {k for k, v in table.items() if k != POINTS_KEY and v}
    out: dict[str, list[str]] = {}
    for track, perks in BUSINESS_PERKS.items():
        got = [display for key, display in perks if key in owned]
        if got:
            out[track] = got
        owned -= {key for key, _ in perks}
    if owned:
        out["Other"] = sorted(owned)
    return {"points": perk_points(table), "perks": out}


def sim_business_perks(states: dict[int, list[tuple[str, dict]]]) -> dict[int, dict]:
    """{sim nid: sim_perks(...)} for every sim with a Business Rewards table."""
    out = {}
    for nid, tables in states.items():
        for name, table in tables:
            if name == TABLE_BUSINESS_REWARDS:
                out[nid] = sim_perks(table)
    return out
