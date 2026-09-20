#!/usr/bin/env python3
"""A sim's profile in prose, the way SimPE's Sim Description plugin writes one.

    "Patrick is an outgoing, athletic guy. Patrick prefers men to women.
     He aspires to make as many friends as possible. Patrick has a natural
     talent for science and a keen interest in sci-fi."

`describe` is pure: it takes the resolved dict `s2neighborhood.parse_sdsc`
returns (plus whatever extras the caller has — name, household, memories,
badges, lifetime want) and returns paragraphs. Nothing here reads a file, so
the daemon and the extractor can both call it, and the wording lives in one
place.

    python3 s2profile.py HOOD_DIR      # print every sim's profile, to eyeball
"""

# Annotations stay strings so the module imports under the system
# python3 (3.9), which the app gets when launched from Finder.
from __future__ import annotations

import sys
from pathlib import Path

import s2ltw

HIGH, LOW = 700, 300

# (trait, adjective when high, adjective when low)
PERSONALITY_WORDS = [
    ("Outgoing", "outgoing", "shy"),
    ("Neat", "tidy", "sloppy"),
    ("Active", "athletic", "lazy"),
    ("Playful", "playful", "serious"),
    ("Nice", "kind-hearted", "grouchy"),
]

# What each aspiration wants, in the game's own spirit.
ASPIRATION_PHRASES = {
    "Popularity": "aspires to make as many friends as possible",
    "Fortune": "dreams of getting rich",
    "Family": "wants a big, happy family",
    "Knowledge": "wants to learn everything there is to know",
    "Romance": "is looking for love, again and again",
    "Pleasure": "lives for fun",
    "Grow Up": "just wants to grow up",
    "Grilled Cheese": "thinks about grilled cheese sandwiches a lot",
}

SKILL_TALENTS = {
    "Logic": "science",
    "Creativity": "the arts",
    "Cooking": "cooking",
    "Mechanical": "fixing things",
    "Charisma": "talking people round",
    "Body": "sport",
    "Cleaning": "keeping house",
}

INTEREST_WORDS = {
    "Sci-Fi": "science fiction", "Politics": "politics", "Money": "money",
    "Environment": "the environment", "Crime": "crime", "Entertainment": "entertainment",
    "Culture": "culture", "Food": "food", "Health": "health", "Fashion": "fashion",
    "Sports": "sports", "Paranormal": "the paranormal", "Travel": "travel",
    "Work": "work", "Weather": "the weather", "Animals": "animals", "School": "school",
    "Toys": "toys",
}

WOOHOO_MEMORIES = {s2ltw.MEM_WOOHOO, s2ltw.MEM_WOOHOO_PUBLIC, s2ltw.MEM_WOOHOO_NPC}


def _noun(age: str, gender: str) -> str:
    female = gender == "Female"
    return {
        "Baby": "baby",
        "Toddler": "toddler",
        "Child": "girl" if female else "boy",
        "Teen": "teenage girl" if female else "teenage boy",
        "Young Adult": "young woman" if female else "young man",
        "Adult": "woman" if female else "man",
        "Elder": "old lady" if female else "old man",
    }.get(age, "sim")


def _join(words: list[str]) -> str:
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def _article(phrase: str) -> str:
    return "an " if phrase[:1].lower() in "aeiou" else "a "


def _preference(sim: dict) -> str:
    """"prefers men to women" — from the two signed preference scores. Both
    near zero means the sim has not made up their mind yet, and says so."""
    m, f = sim.get("pref_male", 0), sim.get("pref_female", 0)
    if abs(m) < 100 and abs(f) < 100:
        return ""
    if m > 0 and f > 0:
        return "likes both men and women"
    if m > 0 and f <= 0:
        return "prefers men to women" if f < 0 else "likes men"
    if f > 0 and m <= 0:
        return "prefers women to men" if m < 0 else "likes women"
    return ""


def describe(sim: dict, *, first: str = "", household: str = "",
             memory_guids: "set[int] | frozenset[int]" = frozenset(),
             badges: "dict[str, dict] | None" = None,
             ltw: "dict | None" = None) -> list[str]:
    """Paragraphs of prose for one sim. `sim` is parse_sdsc's dict."""
    name = first or sim.get("first") or "This sim"
    he = "she" if sim.get("gender") == "Female" else "he"
    He = he.capitalize()
    his = "her" if he == "she" else "his"
    age = sim.get("age", "")
    personality = sim.get("personality", {})

    # Who they are.
    adjectives = []
    for trait, high, low in PERSONALITY_WORDS:
        v = personality.get(trait)
        if v is None:
            continue
        if v >= HIGH:
            adjectives.append(high)
        elif v <= LOW:
            adjectives.append(low)
    noun = _noun(age, sim.get("gender", ""))
    phrase = (_join(adjectives) + " " + noun) if adjectives else noun
    who = [f"{name} is {_article(phrase)}{phrase}"]
    if sim.get("zodiac"):
        who[0] += f", born under {sim['zodiac']}"
    who[0] += "."
    if household:
        who.append(f"{He} lives with the {household} household.")
    if age not in ("Baby", "Toddler", "Child"):
        pref = _preference(sim)
        if pref:
            who.append(f"{name} {pref}.")
        if memory_guids and not (WOOHOO_MEMORIES & set(memory_guids)):
            who.append(f"{He} has never WooHooed.")
    paragraphs = [" ".join(who)]

    # What they want.
    wants = []
    for asp in sim.get("aspirations", []):
        p = ASPIRATION_PHRASES.get(asp)
        if p:
            wants.append(p)
    if wants:
        wants_text = f"{He} {_join(wants)}."
        if ltw and ltw.get("name"):
            wants_text += f" {His_want(ltw['name'], his)}"
        paragraphs.append(wants_text)
    elif ltw and ltw.get("name"):
        paragraphs.append(His_want(ltw["name"], his).capitalize())

    # Talents and work.
    work = []
    skills = sim.get("skills", {})
    talents = [SKILL_TALENTS[k] for k, v in sorted(skills.items(), key=lambda kv: -kv[1])
               if v >= HIGH and k in SKILL_TALENTS]
    if talents:
        work.append(f"{name} has a natural talent for {_join(talents[:3])}")
    interests = sim.get("interests", {})
    keen = [INTEREST_WORDS.get(k, k.lower()) for k, v in sorted(interests.items(), key=lambda kv: -kv[1])
            if v >= HIGH]
    if keen:
        lead = f"{name} has" if not work else "and has"
        work.append(f"{lead} a keen interest in {_join(keen[:3])}")
    if work:
        paragraphs.append(" ".join(work) + ".")
    job = []
    if sim.get("on_campus") and sim.get("major"):
        sem = sim.get("semester") or 0
        job.append(f"{He} is studying {sim['major']} at college"
                   + (f", semester {sem}" if sem else "") + ".")
    if sim.get("career"):
        title = sim.get("career_title")
        track = sim["career"].split(" - ", 1)[-1]
        job.append(f"{He} works as {_article(title)}{title} in {track}." if title
                   else f"{He} works in {track}.")
    elif sim.get("retired_career"):
        title = sim.get("retired_title")
        job.append(f"{He} is retired" + (f" — {his} last job was {title}" if title else "") + ".")
    gold = [b for b, info in (badges or {}).items() if info.get("level") == "Gold"]
    if gold:
        job.append(f"{He} holds a gold badge in {_join(sorted(gold))}.")
    if job:
        paragraphs.append(" ".join(job))
    return paragraphs


def His_want(want_name: str, his: str) -> str:
    """"His lifetime want: Marry Off 6 Children." The names are Title Case
    from the game's own Wants.package, so they are shown as they are rather
    than bent into a sentence."""
    return f"{his.capitalize()} lifetime want: {want_name}."


def main() -> None:
    """Print every sim's profile in a hood, for reading against premades."""
    import s2neighborhood
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    hood = s2neighborhood.extract_hood(Path(sys.argv[1]))
    if hood is None:
        print("not a neighborhood folder")
        sys.exit(1)
    for s in sorted(hood["sims"], key=lambda x: (x["last"], x["first"])):
        memories = {m["guid"] for m in s.get("memories", [])} if "memories" in s else frozenset()
        print(f"== {s['first']} {s['last']} (nid {s['nid']})")
        for p in describe(s, first=s["first"], household=s.get("household", ""),
                          memory_guids=memories, badges=s.get("badges"), ltw=s.get("ltw")):
            print("  " + p)
        print()


if __name__ == "__main__":
    main()
