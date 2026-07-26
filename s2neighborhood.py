#!/usr/bin/env python3
"""Sims 2 neighborhood extractor — sims, families, lots, ties, relationships → JSON.

Reads the same save files SimPE does:
  SDSC 0xAACE2EFB  sim description (career, aspiration, zodiac, personality, …)
  FAMI 0x46414D49  family/household (lot, funds, members)
  LTXT 0x0BF999E7  lot description (address)
  FAMt 0x8C870743  family ties (parents/spouse/siblings/children)
  SREL 0xCC364C2A  pairwise relationships (scores + flags)
  CTSS 0x43545353  neighborhood + per-sim text (names, bios)

Field offsets verified empirically against this save and simswiki.info/wiki.php?title=SDSC.
"""

import argparse
import json
import struct
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from s2parser import open_package, read_resource

TID_SDSC = 0xAACE2EFB
TID_FAMI = 0x46414D49
TID_LTXT = 0x0BF999E7
TID_FAMT = 0x8C870743
TID_SREL = 0xCC364C2A
TID_CTSS = 0x43545353
TID_OBJD = 0x4F424A44

DEFAULT_ROOT = (
    Path.home()
    / "Library/Containers/com.aspyr.sims2.appstore/Data"
      "/Library/Application Support/Aspyr/The Sims 2/Neighborhoods"
)

AGE_STAGES = {1: "Baby", 2: "Toddler", 3: "Child", 16: "Teen", 19: "Adult", 51: "Elder"}
ZODIAC = {1: "Aries", 2: "Taurus", 3: "Gemini", 4: "Cancer", 5: "Leo", 6: "Virgo",
          7: "Libra", 8: "Scorpio", 9: "Sagittarius", 10: "Capricorn", 11: "Aquarius",
          12: "Pisces"}
ASPIRATION_BITS = [(0x01, "Romance"), (0x02, "Family"), (0x04, "Fortune"),
                   (0x10, "Popularity"), (0x20, "Knowledge"), (0x40, "Grow Up"),
                   (0x80, "Pleasure"), (0x100, "Grilled Cheese")]
SREL_FLAG_BITS = [(0x01, "Crush"), (0x02, "Love"), (0x04, "Engaged"), (0x08, "Married"),
                  (0x10, "Friend"), (0x20, "Best Friend"), (0x40, "Steady"), (0x80, "Enemy")]
FAMILY_REL_CODES = {1: "Parent", 2: "Child", 3: "Sibling", 4: "Grandparent",
                    5: "Grandchild", 6: "Aunt/Uncle", 7: "Niece/Nephew",
                    8: "Cousin", 9: "Spouse"}
# FAMt tie types (validated against the Goth family)
TIE_FATHER, TIE_MOTHER, TIE_SPOUSE, TIE_SIBLING, TIE_CHILD = 0, 1, 2, 3, 4

PERSONALITY = [(0x6A, "Neat"), (0x6C, "Nice"), (0x6E, "Active"),
               (0x70, "Outgoing"), (0x72, "Playful")]
SKILLS = [(0x1E, "Cleaning"), (0x20, "Cooking"), (0x22, "Charisma"),
          (0x24, "Mechanical"), (0x2A, "Creativity"), (0x2E, "Body"), (0x30, "Logic")]
INTERESTS = [(0x104, "Politics"), (0x106, "Money"), (0x108, "Environment"),
             (0x10A, "Crime"), (0x10C, "Entertainment"), (0x10E, "Culture"),
             (0x110, "Food"), (0x112, "Health"), (0x114, "Fashion"), (0x116, "Sports"),
             (0x118, "Paranormal"), (0x11A, "Travel"), (0x11C, "Work"),
             (0x11E, "Weather"), (0x120, "Animals"), (0x122, "School"),
             (0x124, "Toys"), (0x126, "Sci-Fi")]

_here = Path(__file__).resolve().parent
try:
    _tables = json.loads((_here / "careers.json").read_text())
    CAREERS = {int(k, 16): v for k, v in _tables["careers"].items()}  # guid → {track, titles}
    MAJORS = {int(k, 16): v for k, v in _tables["majors"].items()}
except Exception:
    CAREERS, MAJORS = {}, {}
MAJOR_UNDECLARED = 0x8E97BF1D


def career_info(guid: int, level: int) -> tuple[str, str]:
    """(track name, job title) for a career GUID + level."""
    if not guid:
        return "", ""
    c = CAREERS.get(guid)
    if not c:
        return f"0x{guid:08X}", ""
    titles = c.get("titles") or []
    title = titles[level - 1].strip() if 1 <= level <= len(titles) else ""
    return c["track"], title


def u16(d, off): return struct.unpack_from("<H", d, off)[0] if off + 2 <= len(d) else 0
def i16(d, off): return struct.unpack_from("<h", d, off)[0] if off + 2 <= len(d) else 0
def u32(d, off): return struct.unpack_from("<I", d, off)[0] if off + 4 <= len(d) else 0
def i32(d, off): return struct.unpack_from("<i", d, off)[0] if off + 4 <= len(d) else 0


# ---------------------------------------------------------------------------
# Character files: names, bios, GUIDs
# ---------------------------------------------------------------------------

def parse_ctss_strings(data: bytes) -> list[str]:
    """English strings from a CTSS resource, in order (first, bio, last for sims).

    Format 0xFFFD entries are (language byte, value cstring, description
    cstring). The description must be consumed even when empty, or entries
    with blank descriptions (in-game-born sims) shift the parse off by one
    and lose the last name.
    """
    idx = data.find(b'\xfd\xff')
    if idx < 0:
        return []
    count = struct.unpack_from("<H", data, idx + 2)[0]
    pos = idx + 4

    def cstring(p: int) -> tuple[str, int]:
        end = data.find(b'\x00', p)
        if end < 0:
            return "", len(data)
        raw = data[p:end]
        try:
            return raw.decode('utf-8').strip(), end + 1
        except UnicodeDecodeError:
            return raw.decode('latin-1', errors='replace').strip(), end + 1

    out = []
    for _ in range(min(count, 64)):
        if pos >= len(data):
            break
        lang = data[pos]; pos += 1
        value, pos = cstring(pos)
        _desc, pos = cstring(pos)
        if lang == 1:
            out.append(value)
    return out


def load_characters(chars_dir: Path) -> dict[int, dict]:
    """guid → {first, last, bio, file}."""
    chars: dict[int, dict] = {}
    if not chars_dir.exists():
        return chars
    for pkg in sorted(chars_dir.glob("*.package")):
        try:
            _, entries = open_package(pkg)
        except Exception:
            continue
        first = last = bio = ""
        guid = None
        with open(pkg, "rb") as f:
            for e in entries:
                if e.type_id == TID_CTSS and not first:
                    strs = parse_ctss_strings(read_resource(f, e))
                    first = strs[0] if len(strs) > 0 else ""
                    bio = strs[1] if len(strs) > 1 else ""
                    last = strs[2] if len(strs) > 2 else ""
                elif e.type_id == TID_OBJD and guid is None:
                    d = read_resource(f, e)
                    if len(d) >= 0x60:
                        guid = u32(d, 0x5C)
        if guid is not None and guid not in chars:
            chars[guid] = {"first": first, "last": last, "bio": bio, "file": pkg.name}
    return chars


# ---------------------------------------------------------------------------
# SDSC
# ---------------------------------------------------------------------------

def parse_sdsc(d: bytes) -> dict:
    aspiration_val = u16(d, 0x68)
    aspirations = [name for bit, name in ASPIRATION_BITS if aspiration_val & bit]
    career_guid = u32(d, 0xBE)
    career_level = u16(d, 0x7E)
    retired_guid = u32(d, 0x15A)
    retired_level = u16(d, 0x15E)
    major_guid = u32(d, 0x160)
    pref_m, pref_f = i16(d, 0x38), i16(d, 0x3A)
    career_track, career_title = career_info(career_guid, career_level)
    retired_track, retired_title = career_info(retired_guid, retired_level)
    on_campus = u16(d, 0x16A) == 1
    age = AGE_STAGES.get(u16(d, 0x80), f"?{u16(d, 0x80)}")
    # University overlay stage: the age field stays "Adult" while at college;
    # the on-campus flag is what makes a sim a Young Adult.
    if on_campus and age == "Adult":
        age = "Young Adult"
    return {
        "nid": u16(d, 0x1A4),
        "guid": u32(d, 0x1A6),
        "family_id": u16(d, 0x86),
        "age": age,
        "gender": "Female" if u16(d, 0x8E) == 1 else "Male",
        "zodiac": ZODIAC.get(u16(d, 0x98), ""),
        "aspirations": aspirations,
        "aspiration_score": i16(d, 0x14C),
        "career": career_track,
        "career_title": career_title,
        "career_level": career_level,
        "job_performance": i16(d, 0x8A),
        "retired_career": retired_track,
        "retired_title": retired_title,
        "retired_level": retired_level if retired_guid else 0,
        "major": (MAJORS.get(major_guid, "").replace("Major - ", "")
                  if major_guid and major_guid != MAJOR_UNDECLARED else
                  ("Undeclared" if major_guid == MAJOR_UNDECLARED else "")),
        "semester": u16(d, 0x168),
        "on_campus": on_campus,
        "grade": u16(d, 0x7C),
        "pref_male": pref_m,
        "pref_female": pref_f,
        "ghost_flags": u16(d, 0x94),
        "npc_type": u16(d, 0x142),
        "fatness": u16(d, 0xB0),
        "body_flags": u16(d, 0xAE),
        "days_left": i16(d, 0xC2),
        "personality": {name: u16(d, off) for off, name in PERSONALITY},
        "skills": {name: u16(d, off) for off, name in SKILLS},
        "interests": {name: u16(d, off) for off, name in INTERESTS},
    }


def orientation(sim: dict) -> str:
    m, f = sim["pref_male"], sim["pref_female"]
    likes_m, likes_f = m > 0, f > 0
    if not likes_m and not likes_f:
        return ""
    own_female = sim["gender"] == "Female"
    if likes_m and likes_f:
        return "Bi"
    if (own_female and likes_m) or (not own_female and likes_f):
        return "Straight"
    return "Gay"


# ---------------------------------------------------------------------------
# FAMI / LTXT
# ---------------------------------------------------------------------------

def parse_ltxt(d: bytes) -> dict:
    n = u32(d, 0x13)
    name = d[0x17:0x17 + n].decode('latin-1', 'replace') if 0 < n < 200 else ""
    desc = ""
    dpos = 0x17 + n
    dn = u32(d, dpos)
    if 0 < dn < 2000 and dpos + 4 + dn <= len(d):
        desc = d[dpos + 4:dpos + 4 + dn].decode('latin-1', 'replace')
    return {"name": name, "description": desc}


def parse_fami(d: bytes, iid: int, sim_guids: set[int]) -> dict:
    """FAMI v85 (Bon Voyage era). Member list is validated against known sim GUIDs."""
    count = u32(d, 0x28)
    members = []
    if 0 < count <= 40:
        pos = 0x2C
        while pos + 4 <= len(d) and len(members) < count + 4:
            v = u32(d, pos)
            if v in sim_guids:
                members.append(v)
            elif members:
                break  # ran past the member list
            pos += 4
    return {
        "id": iid,
        "lot": u32(d, 0x0C),
        "funds": i32(d, 0x1C),
        "members": members,
    }


# ---------------------------------------------------------------------------
# FAMt / SREL
# ---------------------------------------------------------------------------

def parse_famt(d: bytes) -> dict[int, list[tuple[int, int]]]:
    """nid → [(tie_type, target_nid), …]"""
    if len(d) < 8:
        return {}
    _, count = struct.unpack_from("<II", d, 0)
    pos = 8
    ties: dict[int, list[tuple[int, int]]] = {}
    for _ in range(count):
        if pos + 10 > len(d):
            break
        nid = u16(d, pos); pos += 2
        pos += 4  # sub-record count (always 1 in observed saves)
        tc = u32(d, pos); pos += 4
        lst = []
        for _ in range(min(tc, 200)):
            if pos + 6 > len(d):
                break
            t = u32(d, pos); pos += 4
            tgt = u16(d, pos); pos += 2
            lst.append((t, tgt))
        ties[nid] = lst
    return ties


def load_srel(pkg_path: Path, entries) -> dict[int, list[dict]]:
    """owner nid → outgoing relationship records."""
    rels: dict[int, list[dict]] = {}
    with open(pkg_path, "rb") as f:
        for e in entries:
            if e.type_id != TID_SREL:
                continue
            owner = (e.instance_id2 >> 16) & 0xFFFF
            target = e.instance_id2 & 0xFFFF
            if owner == target:
                continue
            d = read_resource(f, e)
            if len(d) < 14:
                continue
            flags = d[12]
            daily = i32(d, 8)
            if abs(daily) > 200:
                continue  # not a v2 SREL layout
            rec = {
                "other": target,
                "daily": daily,
                "lifetime": i32(d, 0x10) if len(d) >= 0x14 else 0,
                "flags": [name for bit, name in SREL_FLAG_BITS if flags & bit],
                "family_rel": FAMILY_REL_CODES.get(u32(d, 0x14), "") if (len(d) >= 0x18 and d[13] & 0x40) else "",
                "bff": bool(u32(d, 0x34)) if len(d) >= 0x38 else False,
            }
            rels.setdefault(owner, []).append(rec)
    return rels


# ---------------------------------------------------------------------------
# Hood extraction
# ---------------------------------------------------------------------------

def extract_hood(nbr_dir: Path) -> dict | None:
    hood_id = nbr_dir.name
    npkg = nbr_dir / f"{hood_id}_Neighborhood.package"
    if not npkg.exists():
        return None

    chars = load_characters(nbr_dir / "Characters")
    _, entries = open_package(npkg)

    hood_name = hood_id
    sims: dict[int, dict] = {}
    families: dict[int, dict] = {}
    lots: dict[int, dict] = {}
    famt: dict[int, list] = {}

    sim_guids = set(chars.keys())

    with open(npkg, "rb") as f:
        for e in entries:
            try:
                if e.type_id == TID_SDSC:
                    d = read_resource(f, e)
                    if len(d) < 0x1AA:
                        continue
                    s = parse_sdsc(d)
                    ch = chars.get(s["guid"], {})
                    s["first"] = ch.get("first", "")
                    s["last"] = ch.get("last", "")
                    s["bio"] = ch.get("bio", "")
                    s["char_file"] = ch.get("file", "")
                    # Skip orphaned placeholder records (no character, no age)
                    if s["age"] == "?0" and not s["char_file"]:
                        continue
                    sims[s["nid"]] = s
                elif e.type_id == TID_LTXT:
                    d = read_resource(f, e)
                    lots[e.instance_id2] = parse_ltxt(d)
                elif e.type_id == TID_FAMT:
                    famt = parse_famt(read_resource(f, e))
                elif e.type_id == TID_CTSS and e.instance_id2 == 1:
                    strs = parse_ctss_strings(read_resource(f, e))
                    if strs and strs[0]:
                        hood_name = strs[0]
            except Exception:
                continue
        # FAMI needs sim_guids, and LTXT/lot linkage
        for e in entries:
            if e.type_id != TID_FAMI:
                continue
            try:
                d = read_resource(f, e)
                families[e.instance_id2] = parse_fami(d, e.instance_id2, sim_guids)
            except Exception:
                continue

    srel = load_srel(npkg, entries)

    guid_to_nid = {s["guid"]: nid for nid, s in sims.items()}

    # Attach household info to sims
    for fid, fam in families.items():
        member_nids = [guid_to_nid[g] for g in fam["members"] if g in guid_to_nid]
        fam["member_nids"] = member_nids
        lastnames = Counter(sims[n]["last"] for n in member_nids if sims[n]["last"])
        fam["name"] = lastnames.most_common(1)[0][0] if lastnames else f"Family {fid}"
        lot = lots.get(fam["lot"])
        fam["address"] = lot["name"] if lot else ""
        for n in member_nids:
            sims[n]["household"] = fam["name"]
            sims[n]["address"] = fam["address"]
            sims[n]["funds"] = fam["funds"]

    name_of = {nid: f'{s["first"]} {s["last"]}'.strip() or f"[{nid}]" for nid, s in sims.items()}

    # Family ties. Tie types 0/1 are the two parents in arbitrary order,
    # so assign mother/father by each parent's own gender.
    #
    # Ties are emitted twice: as display names (`mother`, `siblings`, …) and as
    # the underlying sim ids (`mother_nid`, `sibling_nids`, …). Names are not a
    # usable join key — a hood routinely holds several sims with identical full
    # names (townies, NPCs, repeated premades), so anything that has to walk the
    # graph rather than just print it must go through the ids.
    for nid, s in sims.items():
        ties = famt.get(nid, [])
        parents = [t for ty, t in ties if ty in (TIE_FATHER, TIE_MOTHER)]
        mother = father = ""
        mother_nid = father_nid = None
        for p in parents:
            pname = name_of.get(p, f"[{p}]")
            if sims.get(p, {}).get("gender") == "Female":
                if not mother:
                    mother, mother_nid = pname, p
            else:
                if not father:
                    father, father_nid = pname, p
        spouse_nid = next((t for ty, t in ties if ty == TIE_SPOUSE), None)
        s["father"] = father
        s["mother"] = mother
        s["spouse"] = name_of.get(spouse_nid, f"[{spouse_nid}]") if spouse_nid is not None else ""
        s["siblings"] = [name_of.get(t, f"[{t}]") for ty, t in ties if ty == TIE_SIBLING]
        s["children"] = [name_of.get(t, f"[{t}]") for ty, t in ties if ty == TIE_CHILD]
        s["father_nid"] = father_nid
        s["mother_nid"] = mother_nid
        s["spouse_nid"] = spouse_nid
        s["sibling_nids"] = [t for ty, t in ties if ty == TIE_SIBLING]
        s["children_nids"] = [t for ty, t in ties if ty == TIE_CHILD]

    # Relationships (outgoing, keep meaningful ones)
    for nid, s in sims.items():
        out = []
        for rec in srel.get(nid, []):
            if not (rec["flags"] or rec["bff"] or rec["family_rel"] or abs(rec["daily"]) >= 15):
                continue
            out.append({**rec, "name": name_of.get(rec["other"], f"[{rec['other']}]")})
        out.sort(key=lambda r: r["lifetime"] + r["daily"], reverse=True)
        s["relationships"] = out
        s["orientation"] = orientation(s)
        loves = [r["name"] for r in out if "Love" in r["flags"]]
        s["loves"] = loves
        s["best_friends"] = [r["name"] for r in out if "Best Friend" in r["flags"] or r["bff"]]
        s["enemies"] = [r["name"] for r in out if "Enemy" in r["flags"]]

    for s in sims.values():
        s.setdefault("household", "")
        s.setdefault("address", "")
        s.setdefault("funds", 0)

    return {
        "id": hood_id,
        "name": hood_name,
        "sims": sorted(sims.values(), key=lambda s: (s["last"] or "~", s["first"])),
        "families": list(families.values()),
    }


def extract_all(root: Path) -> dict:
    hoods = []
    for nbr_dir in sorted(root.iterdir()):
        if not nbr_dir.is_dir():
            continue
        hood = extract_hood(nbr_dir)
        if hood and hood["sims"]:
            hoods.append(hood)
    return {"hoods": hoods}


def main():
    ap = argparse.ArgumentParser(description="Extract Sims 2 neighborhood data to JSON")
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--hood", help="only this neighborhood (e.g. N002)")
    ap.add_argument("--out", type=Path, help="output JSON path (default: stdout)")
    args = ap.parse_args()

    if args.hood:
        data = {"hoods": [h for h in [extract_hood(args.root / args.hood)] if h]}
    else:
        data = extract_all(args.root)

    text = json.dumps(data, ensure_ascii=False)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        n = sum(len(h["sims"]) for h in data["hoods"])
        print(f"Wrote {len(data['hoods'])} neighborhoods, {n} sims → {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
