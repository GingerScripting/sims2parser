#!/usr/bin/env python3
"""Sims 2 character browser — GUI showing sims with names, bios, and relationships."""

import csv
import io
import struct
import tkinter as tk
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, ttk
from typing import NamedTuple

import s2luastate
from s2neighborhood import TID_SDSC, parse_sdsc
from s2parser import open_package, read_resource

# ---------------------------------------------------------------------------
# Config — change NEIGHBORHOOD to load a different one (N001, N003, etc.)
# ---------------------------------------------------------------------------

NEIGHBORHOODS_ROOT = (
    Path.home()
    / "Library/Containers/com.aspyr.sims2.appstore/Data"
      "/Library/Application Support/Aspyr/The Sims 2/Neighborhoods"
)
NEIGHBORHOOD = "N002"

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class Relationship(NamedTuple):
    name: str
    score: int  # positive = friendly, negative = enemies


@dataclass
class SimInfo:
    char_file: Path
    neighborhood: str
    slot: int = 0
    guid: int = 0
    first_name: str = ""
    last_name: str = ""
    bio: str = ""
    age_stage: str = ""
    species: str = ""
    gender: str = ""
    fitness: int = 0
    relationships: list = field(default_factory=list)  # list[Relationship]
    perk_points: int = 0
    perks: dict = field(default_factory=dict)  # track → [display name, tier 1 first]

    @property
    def perk_count(self) -> int:
        return sum(len(v) for v in self.perks.values())

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def family(self) -> str:
        return self.last_name or "—"

    @property
    def is_playable(self) -> bool:
        return bool(self.last_name)

    @property
    def fitness_label(self) -> str:
        return _FITNESS_MAP.get(self.fitness, "")

    @property
    def best_friend(self) -> str:
        pos = [r for r in self.relationships if r.score >= 70]
        return pos[0].name if pos else ""

    @property
    def enemies(self) -> list[str]:
        return [r.name for r in self.relationships if r.score < -20]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_TID_INT   = 0xEB61E4F7
_TID_STR   = 0x0B8BEA18
_TID_FLOAT = 0xABC78708

_AGE_MAP     = {1: "Baby", 2: "Toddler", 4: "Child", 8: "Teen",
                16: "Adult", 32: "Elder", 64: "Elder"}
_SPECIES_MAP = {1: "Human", 2: "Alien"}
_GENDER_MAP  = {1: "Female", 2: "Male"}
_FITNESS_MAP = {0: "", 1: "Fit", 2: "Fat"}


def _parse_ctss_english(data: bytes) -> tuple[str, str, str]:
    idx = data.find(b'\xfd\xff')
    if idx < 0:
        return "", "", ""
    pos = idx + 4
    english: list[str] = []
    while pos < len(data) and len(english) < 3:
        lang = data[pos]; pos += 1
        end = data.find(b'\x00', pos)
        if end < 0:
            break
        text = data[pos:end].decode('latin-1', errors='replace').strip()
        pos = end + 1
        if lang == 84:
            continue
        if lang == 1:
            english.append(text)
    first = english[0] if len(english) > 0 else ""
    bio   = english[1] if len(english) > 1 else ""
    last  = english[2] if len(english) > 2 else ""
    return first, bio, last


def _parse_char_attrs(data: bytes) -> dict:
    """Parse binary or XML key-value attribute block from 0xAC598EAC."""
    if data[:2] == b'<?':
        # XML format used by older/Maxis-created sims
        try:
            root = ET.fromstring(data.decode('latin-1'))
            result = {}
            for child in root:
                key = child.get('key', '')
                text = child.text or ''
                try:
                    result[key] = int(text)
                except ValueError:
                    try:
                        result[key] = float(text)
                    except ValueError:
                        result[key] = text
            return result
        except Exception:
            return {}

    result: dict = {}
    pos = 10
    while pos < len(data) - 12:
        try:
            tid  = struct.unpack_from('<I', data, pos)[0]
            klen = struct.unpack_from('<I', data, pos + 4)[0]
            if klen == 0 or klen > 64:
                pos += 1; continue
            key  = data[pos + 8: pos + 8 + klen].decode('latin-1', errors='replace')
            vpos = pos + 8 + klen
            if tid == _TID_INT:
                result[key] = struct.unpack_from('<I', data, vpos)[0]
                pos = vpos + 4
            elif tid == _TID_STR:
                slen = struct.unpack_from('<I', data, vpos)[0]
                if slen > 256:
                    pos += 1; continue
                result[key] = data[vpos + 4: vpos + 4 + slen].decode('latin-1', errors='replace')
                pos = vpos + 4 + slen
            elif tid == _TID_FLOAT:
                result[key] = struct.unpack_from('<f', data, vpos)[0]
                pos = vpos + 4
            else:
                pos += 1
        except Exception:
            pos += 1
    return result


# ---------------------------------------------------------------------------
# Neighborhood data (relationships)
# ---------------------------------------------------------------------------

@dataclass
class NeighborhoodData:
    # slot → [(other_slot, score), ...]  (deduplicated, outgoing direction)
    relationships: dict = field(default_factory=dict)
    # slot → name
    slot_names: dict = field(default_factory=dict)
    # Business perks are keyed by sim nid, which is *not* the character file
    # slot this browser keys everything else on — the two never coincide. The
    # join runs through the sim's object GUID, so hold perks by GUID here and
    # let each sim look itself up by the GUID in its own character package.
    perks_by_guid: dict = field(default_factory=dict)


def _load_neighborhood_data(nbr_dir: Path) -> NeighborhoodData:
    nd = NeighborhoodData()
    nbr_pkg = nbr_dir / f"{nbr_dir.name}_Neighborhood.package"
    if not nbr_pkg.exists():
        return nd

    chars_dir = nbr_dir / "Characters"
    prefix = nbr_dir.name + "_User"
    if chars_dir.exists():
        for pkg in chars_dir.glob("*.package"):
            stem = pkg.stem
            if not stem.startswith(prefix):
                continue
            try:
                slot = int(stem[len(prefix):])
            except ValueError:
                continue
            try:
                _, entries = open_package(pkg)
                with open(pkg, "rb") as f:
                    for e in entries:
                        if e.type_id == 0x43545353:
                            first, _, last = _parse_ctss_english(read_resource(f, e))
                            name = f"{first} {last}".strip()
                            if name:
                                nd.slot_names[slot] = name
                            break
            except Exception:
                pass

    try:
        _, entries = open_package(nbr_pkg)
    except Exception:
        return nd

    # raw_rels: (sim_a, sim_b) → best score seen
    raw: dict[tuple[int, int], int] = {}
    perks_by_nid: dict[int, dict] = {}
    nid_to_guid: dict[int, int] = {}
    with open(nbr_pkg, "rb") as f:
        for e in entries:
            if e.type_id == TID_SDSC:
                try:
                    sdsc = parse_sdsc(read_resource(f, e))
                    nid_to_guid[sdsc["nid"]] = sdsc["guid"]
                except Exception:
                    pass
                continue
            if e.type_id == s2luastate.TID_LUA_STATE:
                # Script-side state, one resource per table per sim, with the
                # sim's nid in the high half of the instance id.
                try:
                    name, table = s2luastate.parse_state_table(read_resource(f, e))
                    if name == s2luastate.TABLE_BUSINESS_REWARDS:
                        perks_by_nid[e.instance_id2] = s2luastate.sim_perks(table)
                except Exception:
                    pass
                continue
            if e.type_id != 0xCC364C2A:
                continue
            try:
                data = read_resource(f, e)
                count = struct.unpack_from('<I', data, 4)[0]
                if count < 1 or count > 64:
                    continue
                score = struct.unpack_from('<i', data, 8)[0]
                sim_a = (e.instance_id2 >> 16) & 0xFFFF
                sim_b = e.instance_id2 & 0xFFFF
                key = (min(sim_a, sim_b), max(sim_a, sim_b))
                if key not in raw or abs(score) > abs(raw[key]):
                    raw[key] = score
            except Exception:
                pass

    for (sim_a, sim_b), score in raw.items():
        nd.relationships.setdefault(sim_a, []).append((sim_b, score))
        nd.relationships.setdefault(sim_b, []).append((sim_a, score))

    for nid, perks in perks_by_nid.items():
        guid = nid_to_guid.get(nid)
        if guid:
            nd.perks_by_guid[guid] = perks

    return nd


# ---------------------------------------------------------------------------
# Sim loading
# ---------------------------------------------------------------------------

def _slot_from_path(pkg: Path) -> int:
    try:
        return int(pkg.stem.split("User")[1])
    except (IndexError, ValueError):
        return 0


def load_sim(pkg_path: Path, neighborhood: str, nd: NeighborhoodData | None = None) -> SimInfo:
    slot = _slot_from_path(pkg_path)
    sim = SimInfo(char_file=pkg_path, neighborhood=neighborhood, slot=slot)
    try:
        _, entries = open_package(pkg_path)
        with open(pkg_path, "rb") as f:
            for e in entries:
                if e.type_id == 0x43545353:
                    data = read_resource(f, e)
                    sim.first_name, sim.bio, sim.last_name = _parse_ctss_english(data)
                elif e.type_id == 0xAC598EAC:
                    data = read_resource(f, e)
                    attrs = _parse_char_attrs(data)
                    sim.age_stage = _AGE_MAP.get(attrs.get('age', 0), "")
                    sim.species   = _SPECIES_MAP.get(attrs.get('species', 0), "")
                    sim.gender    = _GENDER_MAP.get(attrs.get('gender', 0), "")
                    sim.fitness   = attrs.get('fitness', 0)
                elif e.type_id == 0x4F424A44 and not sim.guid:
                    # OBJD word 14/15 — the sim's object GUID, the only key
                    # that ties a character file to its neighborhood record.
                    data = read_resource(f, e)
                    if len(data) >= 0x60:
                        sim.guid = struct.unpack_from("<I", data, 0x5C)[0]
    except Exception:
        pass

    if nd and sim.guid:
        found = nd.perks_by_guid.get(sim.guid)
        if found:
            sim.perk_points = found["points"]
            sim.perks = found["perks"]

    if nd and slot:
        raw_rels = nd.relationships.get(slot, [])
        rels = [
            Relationship(name=nd.slot_names.get(other, f"[{other}]"), score=score)
            for other, score in raw_rels
        ]
        rels.sort(key=lambda r: r.score, reverse=True)
        sim.relationships = rels

    return sim


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_sims() -> list[SimInfo]:
    nbr_dir = NEIGHBORHOODS_ROOT / NEIGHBORHOOD
    if not nbr_dir.exists():
        print(f"Neighborhood directory not found: {nbr_dir}")
        return []
    chars_dir = nbr_dir / "Characters"
    if not chars_dir.exists():
        print(f"No Characters directory in {nbr_dir}")
        return []
    print(f"Loading {NEIGHBORHOOD}…")
    nd = _load_neighborhood_data(nbr_dir)
    sims = [load_sim(pkg, NEIGHBORHOOD, nd)
            for pkg in sorted(chars_dir.glob("*.package"))]
    return [s for s in sims if s.first_name]


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_csv(sims: list[SimInfo], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([
            "First", "Last", "Neighborhood", "Age", "Species", "Gender", "Fitness",
            "Bio", "Best Friend", "Enemies",
        ])
        for s in sims:
            w.writerow([
                s.first_name, s.last_name, s.neighborhood,
                s.age_stage, s.species, s.gender, s.fitness_label,
                s.bio,
                s.best_friend,
                "; ".join(s.enemies),
            ])


# ---------------------------------------------------------------------------
# GUI helpers
# ---------------------------------------------------------------------------

_REL_COLORS = {
    "best":    "#4fc3f7",
    "friend":  "#81c784",
    "neutral": "#aaaaaa",
    "bad":     "#e57373",
}

def _rel_tag(score: int) -> str:
    if score >= 70:  return "best"
    if score >= 20:  return "friend"
    if score >= -20: return "neutral"
    return "bad"

def _rel_label(score: int) -> str:
    if score >= 70:  return "Best Friends"
    if score >= 20:  return "Friends"
    if score >= -20: return "Acquaintances"
    if score >= -50: return "Disliked"
    return "Enemies"

ALL = "All"


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class SimBrowser(tk.Tk):
    def __init__(self, sims: list[SimInfo]):
        super().__init__()
        self.title(f"Sims 2 Browser — {NEIGHBORHOOD}")
        self.geometry("1150x700")
        self.configure(bg="#1a1a1a")
        self.all_sims = sims
        self._build_ui()
        self._refresh_list()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build_ui(self):
        style = ttk.Style(self)
        style.theme_use("default")
        style.configure("Treeview",
            background="#1e1e1e", foreground="#d4d4d4",
            fieldbackground="#1e1e1e", rowheight=26,
            font=("Helvetica", 12))
        style.configure("Treeview.Heading",
            background="#2a2a2a", foreground="#888888",
            font=("Helvetica", 11, "bold"))
        style.map("Treeview", background=[("selected", "#0d3a5c")])
        style.configure("TScrollbar", troughcolor="#1e1e1e", background="#3c3c3c")

        # ---- Toolbar --------------------------------------------------
        bar = tk.Frame(self, bg="#111111", pady=6)
        bar.pack(fill=tk.X)

        tk.Label(bar, text="Search", bg="#111111", fg="#777777",
                 font=("Helvetica", 11)).pack(side=tk.LEFT, padx=(12, 4))
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._refresh_list())
        tk.Entry(bar, textvariable=self._search_var,
                 bg="#2a2a2a", fg="#dddddd", insertbackground="white",
                 font=("Helvetica", 12), relief=tk.FLAT, bd=4, width=22
                 ).pack(side=tk.LEFT, padx=(0, 16))

        tk.Label(bar, text="Age", bg="#111111", fg="#777777",
                 font=("Helvetica", 11)).pack(side=tk.LEFT, padx=(0, 4))
        ages = [ALL] + [v for v in _AGE_MAP.values() if v not in ("Elder",)] + ["Elder"]
        ages = list(dict.fromkeys(ages))  # deduplicate while preserving order
        self._age_var = tk.StringVar(value=ALL)
        self._age_var.trace_add("write", lambda *_: self._refresh_list())
        ttk.Combobox(bar, textvariable=self._age_var, values=ages,
                     state="readonly", width=10,
                     font=("Helvetica", 11)).pack(side=tk.LEFT, padx=(0, 16))

        tk.Label(bar, text="Gender", bg="#111111", fg="#777777",
                 font=("Helvetica", 11)).pack(side=tk.LEFT, padx=(0, 4))
        self._gender_var = tk.StringVar(value=ALL)
        self._gender_var.trace_add("write", lambda *_: self._refresh_list())
        ttk.Combobox(bar, textvariable=self._gender_var,
                     values=[ALL, "Female", "Male"],
                     state="readonly", width=9,
                     font=("Helvetica", 11)).pack(side=tk.LEFT, padx=(0, 16))

        tk.Label(bar, text="Species", bg="#111111", fg="#777777",
                 font=("Helvetica", 11)).pack(side=tk.LEFT, padx=(0, 4))
        self._species_var = tk.StringVar(value=ALL)
        self._species_var.trace_add("write", lambda *_: self._refresh_list())
        ttk.Combobox(bar, textvariable=self._species_var,
                     values=[ALL, "Human", "Alien"],
                     state="readonly", width=9,
                     font=("Helvetica", 11)).pack(side=tk.LEFT, padx=(0, 16))

        tk.Label(bar, text="Type", bg="#111111", fg="#777777",
                 font=("Helvetica", 11)).pack(side=tk.LEFT, padx=(0, 4))
        self._type_var = tk.StringVar(value="Playable")
        self._type_var.trace_add("write", lambda *_: self._refresh_list())
        ttk.Combobox(bar, textvariable=self._type_var,
                     values=[ALL, "Playable", "Townie"],
                     state="readonly", width=9,
                     font=("Helvetica", 11)).pack(side=tk.LEFT, padx=(0, 24))

        tk.Button(bar, text="Export CSV", command=self._export,
                  bg="#2a4a6a", fg="#aad4f5", relief=tk.FLAT,
                  font=("Helvetica", 11), padx=10, pady=2,
                  activebackground="#1e3a5a", activeforeground="#ffffff",
                  cursor="hand2").pack(side=tk.RIGHT, padx=12)

        # ---- Main pane -----------------------------------------------
        pane = tk.PanedWindow(self, orient=tk.HORIZONTAL, bg="#1a1a1a",
                              sashwidth=3, sashrelief=tk.FLAT)
        pane.pack(fill=tk.BOTH, expand=True)

        # Left: list
        left = tk.Frame(pane, bg="#1e1e1e")
        pane.add(left, width=430)

        cols = ("name", "family", "age", "gender")
        self._tree = ttk.Treeview(left, columns=cols, show="headings",
                                  selectmode="browse")
        self._tree.heading("name",   text="Name")
        self._tree.heading("family", text="Family")
        self._tree.heading("age",    text="Age")
        self._tree.heading("gender", text="Gender")
        self._tree.column("name",   width=170, minwidth=100)
        self._tree.column("family", width=110, minwidth=60)
        self._tree.column("age",    width=80,  minwidth=50)
        self._tree.column("gender", width=70,  minwidth=50)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._tree.bind("<<TreeviewSelect>>", self._on_select)

        vsb = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self._tree.yview)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.configure(yscrollcommand=vsb.set)

        # Right: detail
        right = tk.Frame(pane, bg="#141414")
        pane.add(right, width=710)
        self._build_detail(right)

        # Status bar
        self._status = tk.Label(self, text="", bg="#111111", fg="#555555",
                                font=("Helvetica", 10), anchor="w")
        self._status.pack(fill=tk.X, padx=12, pady=(0, 4))

    def _build_detail(self, parent: tk.Frame):
        # Name + badges row
        header = tk.Frame(parent, bg="#141414")
        header.pack(fill=tk.X, padx=24, pady=(22, 0))

        self._name_label = tk.Label(header, text="", bg="#141414", fg="#4fc3f7",
                                    font=("Helvetica", 22, "bold"), anchor="w")
        self._name_label.pack(side=tk.LEFT)

        # Badge strip (age / species / gender / fitness tags)
        self._badge_frame = tk.Frame(header, bg="#141414")
        self._badge_frame.pack(side=tk.LEFT, padx=(14, 0), pady=(4, 0))

        # Subtitle (family name)
        self._family_label = tk.Label(parent, text="", bg="#141414", fg="#666666",
                                      font=("Helvetica", 12), anchor="w")
        self._family_label.pack(fill=tk.X, padx=24, pady=(0, 8))

        div = tk.Frame(parent, height=1, bg="#2a2a2a")
        div.pack(fill=tk.X, padx=24, pady=(0, 10))

        # Bio
        self._bio_text = tk.Text(parent, bg="#141414", fg="#b0b0b0",
                                 font=("Helvetica", 13), wrap=tk.WORD,
                                 relief=tk.FLAT, bd=0, state=tk.DISABLED,
                                 height=4, padx=0, pady=0)
        self._bio_text.pack(padx=24, fill=tk.X)

        div2 = tk.Frame(parent, height=1, bg="#2a2a2a")
        div2.pack(fill=tk.X, padx=24, pady=10)

        # Business perks — only a handful of sims in a neighborhood own any,
        # so the whole block is packed and unpacked per selection rather than
        # sitting empty. Rows are rebuilt in _show_sim.
        self._perk_frame = tk.Frame(parent, bg="#141414")
        perk_header = tk.Frame(self._perk_frame, bg="#141414")
        perk_header.pack(fill=tk.X, anchor="w")
        tk.Label(perk_header, text="BUSINESS PERKS", bg="#141414", fg="#555555",
                 font=("Helvetica", 9, "bold"), anchor="w").pack(side=tk.LEFT)
        self._perk_points = tk.Label(perk_header, text="", bg="#141414",
                                     fg="#d4a343", font=("Helvetica", 9, "bold"))
        self._perk_points.pack(side=tk.LEFT, padx=(8, 0))
        self._perk_body = tk.Frame(self._perk_frame, bg="#141414")
        self._perk_body.pack(fill=tk.X, anchor="w", pady=(4, 0))

        self._perk_div = tk.Frame(parent, height=1, bg="#2a2a2a")

        # Relationships
        self._rel_header = tk.Label(parent, text="RELATIONSHIPS", bg="#141414",
                                    fg="#555555", font=("Helvetica", 9, "bold"),
                                    anchor="w")
        self._rel_header.pack(padx=24, anchor="w")

        rel_outer = tk.Frame(parent, bg="#141414")
        rel_outer.pack(fill=tk.BOTH, expand=True, padx=24, pady=(4, 0))

        self._rel_text = tk.Text(rel_outer, bg="#141414", fg="#cccccc",
                                 font=("Helvetica", 12), wrap=tk.WORD,
                                 relief=tk.FLAT, bd=0, state=tk.DISABLED,
                                 padx=0, pady=2)
        rel_vsb = ttk.Scrollbar(rel_outer, orient=tk.VERTICAL,
                                command=self._rel_text.yview)
        rel_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._rel_text.configure(yscrollcommand=rel_vsb.set)
        self._rel_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        for tag, color in _REL_COLORS.items():
            self._rel_text.tag_configure(tag, foreground=color)
        self._rel_text.tag_configure("score", font=("Helvetica", 12, "bold"))
        self._rel_text.tag_configure("dim",   foreground="#555555")

        # File path footer
        self._file_label = tk.Label(parent, text="", bg="#141414", fg="#333333",
                                    font=("Helvetica", 9), anchor="w")
        self._file_label.pack(padx=24, pady=(6, 8), anchor="w")

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------

    def _filtered_sims(self) -> list[SimInfo]:
        query   = self._search_var.get().lower()
        age_f   = self._age_var.get()
        gen_f   = self._gender_var.get()
        spec_f  = self._species_var.get()
        type_f  = self._type_var.get()

        result = []
        for s in self.all_sims:
            if age_f  != ALL and s.age_stage  != age_f:              continue
            if gen_f  != ALL and s.gender     != gen_f:              continue
            if spec_f != ALL and s.species    != spec_f:             continue
            if type_f == "Playable" and not s.is_playable:           continue
            if type_f == "Townie"   and s.is_playable:               continue
            if query and not any(query in f.lower() for f in (
                    s.full_name, s.bio, s.family, s.age_stage,
                    s.species, s.gender)):
                continue
            result.append(s)
        return result

    def _refresh_list(self):
        sims = self._filtered_sims()
        self._tree.delete(*self._tree.get_children())
        self._id_map: dict[str, SimInfo] = {}
        for s in sims:
            iid = self._tree.insert("", tk.END, values=(
                s.full_name, s.family, s.age_stage, s.gender))
            self._id_map[iid] = s
        n = len(sims)
        total = len(self.all_sims)
        self._status.config(text=f"Showing {n} of {total} sims")

    # ------------------------------------------------------------------
    # Detail
    # ------------------------------------------------------------------

    def _on_select(self, _event):
        sel = self._tree.selection()
        if not sel:
            return
        sim = self._id_map.get(sel[0])
        if not sim:
            return
        self._show_sim(sim)

    def _show_sim(self, sim: SimInfo):
        self._name_label.config(text=sim.full_name)

        family_str = f"{sim.family} family" if sim.last_name else "No family name"
        self._family_label.config(text=family_str)

        # Rebuild badges
        for w in self._badge_frame.winfo_children():
            w.destroy()

        badge_specs = [
            (sim.age_stage,     "#2a3a4a", "#7ab8e8"),
            (sim.species,       "#2a3a2a", "#80c080") if sim.species != "Human" else ("", "", ""),
            (sim.gender,        "#2a2a3a", "#9090d0"),
            (sim.fitness_label, "#3a2a2a", "#d08080"),
        ]
        for text, bg, fg in badge_specs:
            if text:
                tk.Label(self._badge_frame, text=text, bg=bg, fg=fg,
                         font=("Helvetica", 10), padx=6, pady=2,
                         relief=tk.FLAT).pack(side=tk.LEFT, padx=(0, 4))

        self._bio_text.config(state=tk.NORMAL)
        self._bio_text.delete("1.0", tk.END)
        self._bio_text.insert(tk.END, sim.bio or "(No bio)")
        self._bio_text.config(state=tk.DISABLED)

        self._show_perks(sim)

        self._rel_text.config(state=tk.NORMAL)
        self._rel_text.delete("1.0", tk.END)

        if sim.relationships:
            positives = [r for r in sim.relationships if r.score > 0]
            negatives = [r for r in sim.relationships if r.score <= -20]

            def write_row(r: Relationship):
                tag = _rel_tag(r.score)
                self._rel_text.insert(tk.END, f"  {r.name}", tag)
                self._rel_text.insert(tk.END, f"   {r.score:+d}  ", "score")
                self._rel_text.insert(tk.END, f"{_rel_label(r.score)}\n", "dim")

            if positives:
                for r in positives[:20]:
                    write_row(r)
            if negatives:
                if positives:
                    self._rel_text.insert(tk.END, "\n", "dim")
                for r in negatives[:5]:
                    write_row(r)
            if not positives and not negatives:
                self._rel_text.insert(tk.END, "  No significant relationships", "dim")
        else:
            self._rel_text.insert(tk.END, "  No relationship data", "dim")

        self._rel_text.config(state=tk.DISABLED)
        self._file_label.config(text=str(sim.char_file))

    def _show_perks(self, sim: SimInfo):
        for w in self._perk_body.winfo_children():
            w.destroy()

        # A sim with points but nothing bought yet is still worth showing —
        # that's the state the game is nagging about.
        if not sim.perks and not sim.perk_points:
            self._perk_frame.pack_forget()
            self._perk_div.pack_forget()
            return

        self._perk_frame.pack(fill=tk.X, padx=24, anchor="w",
                              before=self._rel_header)
        self._perk_div.pack(fill=tk.X, padx=24, pady=10,
                            before=self._rel_header)

        pts = sim.perk_points
        self._perk_points.config(
            text=f"{pts} unspent point{'' if pts == 1 else 's'}" if pts else "")

        # Tracks in the order the in-game picker shows its columns.
        for track in s2luastate.BUSINESS_PERKS:
            got = sim.perks.get(track)
            if not got:
                continue
            row = tk.Frame(self._perk_body, bg="#141414")
            row.pack(fill=tk.X, anchor="w")
            tk.Label(row, text=track, bg="#141414", fg="#7ab8e8",
                     font=("Helvetica", 11, "bold"), width=12, anchor="w"
                     ).pack(side=tk.LEFT)
            tk.Label(row, text=" › ".join(got), bg="#141414", fg="#cccccc",
                     font=("Helvetica", 11), anchor="w", justify=tk.LEFT
                     ).pack(side=tk.LEFT)
        for track, got in sim.perks.items():
            if track not in s2luastate.BUSINESS_PERKS:
                tk.Label(self._perk_body, text=f"{track}: {', '.join(got)}",
                         bg="#141414", fg="#888888", font=("Helvetica", 11),
                         anchor="w").pack(fill=tk.X, anchor="w")

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _export(self):
        sims = self.all_sims
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=f"{NEIGHBORHOOD}_sims.csv",
            title="Export sims to CSV",
        )
        if not path:
            return
        export_csv(sims, Path(path))
        self._status.config(text=f"Exported {len(sims)} sims to {Path(path).name}")


def main():
    print("Loading sims…")
    sims = discover_sims()
    print(f"Found {len(sims)} sims.")
    app = SimBrowser(sims)
    app.mainloop()


if __name__ == "__main__":
    main()
