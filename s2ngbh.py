#!/usr/bin/env python3
"""Sims 2 neighborhood token store (NGBH 0x4E474248).

One resource per neighborhood holding every *persistent* token the game keeps
outside a loaded lot: sim memories, talent badges, and — the reason this module
exists — the Open for Business records for the businesses a household owns.

Layout (verified against the Aspyr Super Collection, NGBH version 0xCB):

    header   'NGBH' u32 version, u32, u32, u32, u32 name-len, name, padding
    section  array of groups; three sections follow each other back to back,
             in order: lots, households, sims. A section ends where the group
             id stops increasing.
    group    u32 owner id, u32 0x000000BE (marker), then two token lists,
             each a u32 count followed by that many tokens
    token    u32 object GUID, 10 unread bytes, u32 count, count * u16 values

The two lists per group are not cleanly separated by meaning — memories and
plain tokens turn up in both — so callers get them merged and should select on
the GUID.

Field offsets for the business token were read off Bluewater Village: the
household that owns the rank-10 lot is the one whose sim carries the
"Built A Top Ranked Business" memory.
"""

# Annotations stay strings so the modules import under the system
# python3 (3.9), which the app gets when launched from Finder.
from __future__ import annotations

import struct

TID_NGBH = 0x4E474248

GROUP_MARKER = 0x000000BE
_TOKEN_HEADER = 18          # GUID (4) + 10 unread + value count (4)
_MAX_TOKEN_VALUES = 5000    # sanity bound for resync

# --- tokens we read ---------------------------------------------------------

# "Token - Remote Business Data": one per business the household owns, kept on
# the household so the neighborhood can show a rank without loading the lot.
TOKEN_REMOTE_BUSINESS = 0x108F47DF

# Open for Business / Seasons talent badges. Value is 0–1000 progress.
BADGE_TOKENS = {
    0xB05FDC1B: "Sales",
    0x105FDC52: "Stocking",
    0x505FDC28: "Cash Register",
    0x505FDBC6: "Robotics",
    0xD05FDB91: "Toy Making",
    0x905FDBB5: "Flower Arranging",
    0x705FDC8F: "Cosmetology",
    0x1215A8C1: "Gardening",
    0x12297157: "Fishing",
}
BADGE_LEVELS = ((1000, "Gold"), (666, "Silver"), (333, "Bronze"))


def badge_level(points: int) -> str:
    for threshold, name in BADGE_LEVELS:
        if points >= threshold:
            return name
    return ""


# --- parsing ----------------------------------------------------------------

def _find_first_group(d: bytes) -> int:
    """Groups start after a variable-length neighborhood name plus padding, so
    locate the first group marker rather than assuming a header size. The name
    is not padded to an even length, so the search runs byte by byte."""
    for off in range(8, min(len(d), 512)):
        if struct.unpack_from("<I", d, off)[0] == GROUP_MARKER:
            return off - 4
    return -1


def _read_tokens(d: bytes, pos: int, count: int):
    """(tokens, new position) or (None, pos) if the run doesn't parse."""
    tokens = []
    for _ in range(count):
        if pos + _TOKEN_HEADER > len(d):
            return None, pos
        guid, = struct.unpack_from("<I", d, pos)
        n, = struct.unpack_from("<I", d, pos + 14)
        end = pos + _TOKEN_HEADER + n * 2
        if n > _MAX_TOKEN_VALUES or end > len(d):
            return None, pos
        tokens.append((guid, struct.unpack_from(f"<{n}H", d, pos + _TOKEN_HEADER)))
        pos = end
    return tokens, pos


def _read_groups(d: bytes) -> list[tuple[int, list]]:
    """[(owner id, [(guid, values), …]), …] in file order."""
    pos = _find_first_group(d)
    if pos < 0:
        return []
    groups = []
    while pos + 12 <= len(d):
        gid, marker, first = struct.unpack_from("<III", d, pos)
        if marker != GROUP_MARKER or first > _MAX_TOKEN_VALUES:
            pos = _resync(d, pos + 4)
            if pos < 0:
                break
            continue
        listed, q = _read_tokens(d, pos + 12, first)
        if listed is None or q + 4 > len(d):
            pos = _resync(d, pos + 4)
            if pos < 0:
                break
            continue
        second, = struct.unpack_from("<I", d, q)
        rest, end = (_read_tokens(d, q + 4, second)
                     if second <= _MAX_TOKEN_VALUES else (None, q))
        if rest is None:
            pos = _resync(d, pos + 4)
            if pos < 0:
                break
            continue
        groups.append((gid, listed + rest))
        pos = end
    return groups


def _resync(d: bytes, pos: int) -> int:
    """Next plausible group header at or after pos, or -1.

    A handful of groups carry a trailing word this layout doesn't account for;
    rather than lose the rest of a 2 MB resource, step forward to the next
    marker. Skipped bytes cost at most one group.
    """
    while pos + 12 <= len(d):
        _, marker, count = struct.unpack_from("<III", d, pos)
        if marker == GROUP_MARKER and count <= _MAX_TOKEN_VALUES:
            return pos
        pos += 4
    return -1


def parse_ngbh(d: bytes) -> dict[str, dict[int, list]]:
    """{'lots': …, 'families': …, 'sims': …}, each {owner id: [(guid, values)]}.

    The three sections always appear in that order; a section boundary is where
    the group id stops increasing.
    """
    groups = _read_groups(d)
    sections: list[list] = []
    for group in groups:
        if not sections or group[0] <= sections[-1][-1][0]:
            sections.append([])
        sections[-1].append(group)
    out: dict[str, dict[int, list]] = {"lots": {}, "families": {}, "sims": {}}
    for name, section in zip(("lots", "families", "sims"), sections):
        for gid, tokens in section:
            out[name].setdefault(gid, []).extend(tokens)
    return out


# --- business records -------------------------------------------------------

def _signed(v: int) -> int:
    return v - 0x10000 if v >= 0x8000 else v


def parse_business_token(values) -> dict | None:
    """Business record from a "Token - Remote Business Data" value array.

    Stale, valueless copies of this token exist on sims; only the household
    copies carry data, so a short array means "no business here".
    """
    if len(values) < 10:
        return None
    return {
        "lot": values[1],
        "rank": values[9],
        "customer_loyalty": _signed(values[5]),
    }


def households_businesses(ngbh: dict) -> dict[int, list[dict]]:
    """{family id: [business record, …]} for every household that owns one."""
    out: dict[int, list[dict]] = {}
    for family_id, tokens in ngbh["families"].items():
        for guid, values in tokens:
            if guid != TOKEN_REMOTE_BUSINESS:
                continue
            record = parse_business_token(values)
            if record:
                out.setdefault(family_id, []).append(record)
    return out


def sim_badges(ngbh: dict) -> dict[int, dict[str, dict]]:
    """{sim nid: {badge name: {'points': 0–1000, 'level': Bronze/Silver/Gold}}}."""
    out: dict[int, dict[str, dict]] = {}
    for nid, tokens in ngbh["sims"].items():
        for guid, values in tokens:
            name = BADGE_TOKENS.get(guid)
            if not name or not values:
                continue
            points = values[0]
            if points:
                out.setdefault(nid, {})[name] = {
                    "points": points,
                    "level": badge_level(points),
                }
    return out


# --- see also ---------------------------------------------------------------

# Business Perks are not in this token store. They live in a per-sim Lua state
# table (resource type 0x3053CF74) alongside the NGBH in the neighborhood
# package — see s2luastate. Two searches here came up empty before that turned
# up: no token in a sim's group encodes the state, and no token GUID is shared
# by a neighborhood's business owners while staying rare elsewhere.
