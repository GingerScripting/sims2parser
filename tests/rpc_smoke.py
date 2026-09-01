#!/usr/bin/env python3
"""End-to-end check of the Sim Studio daemon, driven the way the app drives it.

Launches `s2studio.py --serve` as a subprocess and walks one editing session
over a scratch copy of a donor: open, index, decode a STR#, change a string,
undo, redo, save as, re-open the copy and confirm the edit stuck. Then the
policy checks: a package under a Neighborhoods tree opens read-only and
refuses `save`; `save_as` into one is refused too.

Donors come from sample-packages/, which is gitignored, so a checkout
without it skips rather than fails.

    python3 tests/rpc_smoke.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import s2parser  # noqa: E402
import s2object  # noqa: E402


class Client:
    def __init__(self, python: str = sys.executable):
        self.proc = subprocess.Popen(
            [python, str(ROOT / "s2studio.py"), "--serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr,
            cwd=str(ROOT))
        self.next_id = 0
        ready = self._read()
        assert ready.get("event") == "ready", ready

    def _read(self) -> dict:
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("daemon closed stdout")
        return json.loads(line)

    def call(self, method: str, **params):
        self.next_id += 1
        req = {"id": self.next_id, "method": method, "params": params}
        self.proc.stdin.write((json.dumps(req) + "\n").encode())
        self.proc.stdin.flush()
        resp = self._read()
        while "event" in resp:            # skip notifications
            resp = self._read()
        assert resp["id"] == self.next_id, resp
        if "error" in resp:
            raise RpcFailure(resp["error"])
        return resp["result"]

    def close(self):
        try:
            self.call("shutdown")
        except Exception:
            pass
        self.proc.wait(timeout=5)


class RpcFailure(Exception):
    def __init__(self, err: dict):
        super().__init__(f"{err['code']}: {err['message']}")
        self.code = err["code"]


def expect_error(code: str, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except RpcFailure as exc:
        assert exc.code == code, f"expected {code}, got {exc.code}: {exc}"
        return
    raise AssertionError(f"expected error {code}, call succeeded")


def pick_donor() -> "Path | None":
    """A sample package with a STR# and, preferably, a BHAV as well."""
    samples = ROOT / "sample-packages"
    if not samples.is_dir():
        return None
    fallback = None
    for p in sorted(samples.rglob("*.package")):
        try:
            _, entries = s2parser.open_package(p)
        except Exception:
            continue
        types = {e.type_id for e in entries}
        if s2object.TYPE_STR in types and s2object.TYPE_BHAV in types:
            return p
        if s2object.TYPE_STR in types and fallback is None:
            fallback = p
    return fallback


def main() -> int:
    donor = pick_donor()
    if donor is None:
        print("skip: no sample-packages/ with a STR# donor")
        return 0

    with tempfile.TemporaryDirectory(prefix="s2studio-smoke-") as tmp:
        tmp = Path(tmp)
        work = tmp / donor.name
        shutil.copy(donor, work)
        c = Client()
        try:
            info = c.call("open", path=str(work))
            assert not info["readonly"], info
            assert info["count"] > 0
            meta = c.call("meta")
            assert str(s2object.TYPE_STR) in map(str, meta["decodable_types"])

            index = c.call("index")
            assert len(index["rows"]) == info["count"]
            cols = index["columns"]
            rows = [dict(zip(cols, r)) for r in index["rows"]]
            row = next(r for r in rows
                       if r["type"] == s2object.TYPE_STR and r["flags"] & 2)
            tgi = {"type": row["type"], "group": row["group"],
                   "instance": row["instance"], "instance_hi": row["instance_hi"]}
            assert index["type_names"][str(row["type"])] == "STR#"

            got = c.call("get_resource", tgi=tgi)
            assert got["decoded"] and got["decode_error"] is None, got
            assert got["decoded"]["$type"] == "StrResource"
            assert bytes.fromhex(got["hex"]) == \
                s2object.build_resource(row["type"], _roundtrip(got["decoded"]))
            print(f"decoded STR# {got['decoded']['name']!r}: "
                  f"{len(got['decoded']['entries'])} entries")

            # No-op put is not an edit.
            r = c.call("put_resource", tgi=tgi, decoded=got["decoded"])
            assert r["changed"] is False and r["dirty"] is False

            edited = json.loads(json.dumps(got["decoded"]))
            original = edited["entries"][0]["value"]
            edited["entries"][0]["value"] = "Sim Studio was here"
            r = c.call("put_resource", tgi=tgi, decoded=edited)
            assert r["changed"] and r["dirty"] and r["can_undo"]

            back = c.call("get_resource", tgi=tgi)
            assert back["decoded"]["entries"][0]["value"] == "Sim Studio was here"

            r = c.call("undo")
            assert r["tgis"][0]["instance"] == row["instance"]
            assert c.call("get_resource", tgi=tgi)["decoded"]["entries"][0]["value"] == original
            r = c.call("redo")
            assert c.call("get_resource", tgi=tgi)["decoded"]["entries"][0]["value"] == "Sim Studio was here"

            # Add, rename, delete, and undo all three in order.
            new_tgi = {"type": s2object.TYPE_STR, "group": 0xFFFFFFFF, "instance": 0x7FFF}
            c.call("add_resource", tgi=new_tgi, hex=bytes(68).hex())
            expect_error("duplicate_tgi", c.call, "add_resource", tgi=new_tgi, hex="00")
            moved = dict(new_tgi, instance=0x7FFE)
            c.call("rename_resource", tgi=new_tgi, new_tgi=moved)
            expect_error("not_found", c.call, "get_resource", tgi=new_tgi)
            n_before = c.call("status")["count"]
            c.call("delete_resource", tgi=moved)
            assert c.call("status")["count"] == n_before - 1
            c.call("undo")                      # delete
            assert c.call("status")["count"] == n_before
            c.call("undo")                      # rename
            c.call("get_resource", tgi=new_tgi)
            c.call("undo")                      # add
            expect_error("not_found", c.call, "get_resource", tgi=new_tgi)

            # Save As to scratch, then prove the edit survived a real write.
            out = tmp / "edited.package"
            r = c.call("save_as", path=str(out))
            assert r["path"] == str(out) and not r["dirty"] and not r["readonly"]
            assert out.is_file() and not (tmp / "edited.package.tmp").exists()

            c2 = Client()
            try:
                c2.call("open", path=str(out))
                again = c2.call("get_resource", tgi=tgi)
                assert again["decoded"]["entries"][0]["value"] == "Sim Studio was here"
                assert c2.call("status")["compressed_count"] == info["compressed_count"], \
                    "compression choices did not survive the save"
            finally:
                c2.close()
            print(f"edit survived save_as -> {out.name} "
                  f"({out.stat().st_size} bytes, {r['compressed_count']} compressed)")

            # Policy: a hood tree is read-only, in and out.
            hood_dir = tmp / "Neighborhoods" / "N001"
            hood_dir.mkdir(parents=True)
            hood_pkg = hood_dir / "N001_Neighborhood.package"
            shutil.copy(donor, hood_pkg)
            c3 = Client()
            try:
                info = c3.call("open", path=str(hood_pkg))
                assert info["readonly"], info
                expect_error("readonly", c3.call, "save")
                expect_error("destination_protected", c3.call, "save_as",
                             path=str(hood_dir / "copy.package"))
                r = c3.call("save_as", path=str(tmp / "hood-copy.package"))
                assert not r["readonly"]
            finally:
                c3.close()
            print("hood package: opened read-only, save refused, save_as out of tree allowed")

            # Policy: the game's own install is read-only too.
            app_pkg = tmp / "The Sims 2.app" / "Contents" / "objects.package"
            app_pkg.parent.mkdir(parents=True)
            shutil.copy(donor, app_pkg)
            c4 = Client()
            try:
                assert c4.call("open", path=str(app_pkg))["readonly"]
                expect_error("readonly", c4.call, "save")
            finally:
                c4.close()
            print("game install package: opened read-only")

            # BHAV: decode, render, transform a draft, apply, and read back.
            brow = next((r for r in rows if r["flags"] & 4), None)
            if brow is not None:
                btgi = {"type": brow["type"], "group": brow["group"],
                        "instance": brow["instance"], "instance_hi": brow["instance_hi"]}
                got = c.call("get_resource", tgi=btgi)
                assert got["decoded"]["$type"] == "BhavRes" and got["bhav"].get("tree"), got["bhav"]
                bmeta = c.call("bhav_meta")
                assert "2" in bmeta["layouts"] and bmeta["sentinels"]["true"] == 0xFFFD
                n = len(got["decoded"]["instructions"])
                r = c.call("bhav_transform", decoded=got["decoded"], op="insert", index=0)
                assert len(r["decoded"]["instructions"]) == n + 1 and not r["warnings"]
                r = c.call("bhav_transform", decoded=r["decoded"], op="delete", index=0)
                assert len(r["decoded"]["instructions"]) == n
                assert bytes.fromhex(got["hex"]) == s2object.build_resource(
                    brow["type"], _roundtrip(r["decoded"])), "insert+delete is not a no-op"
                edited = r["decoded"]
                edited["name"] = "Sim Studio tree"
                r = c.call("put_resource", tgi=btgi, decoded=edited)
                assert r["changed"]
                back = c.call("get_resource", tgi=btgi)
                assert back["decoded"]["name"] == "Sim Studio tree" and "Sim Studio tree" in back["bhav"]["flat"]
                c.call("undo")
                print(f"BHAV {got['decoded']['name']!r}: {n} instructions, "
                      f"format 0x{got['decoded']['format_version']:04X}; transform/apply/undo OK")

            # Object Workshop and tools, on the same scratch copy.
            objs = c.call("objects")["objects"]
            if objs:
                o = objs[0]
                before_guid = o["guid"]
                r = c.call("clone", name="Smoke Clone", select_guid=o["guid"], price=7)
                assert r["new_guid"] != before_guid and r["changed"] >= 1 and r["can_undo"]
                assert c.call("objects")["objects"][0]["guid"] == r["new_guid"]
                c.call("undo")
                assert c.call("objects")["objects"][0]["guid"] == before_guid, "clone undo"
                c.call("redo")
                scan = c.call("scan_guids", folders=[str(tmp)], guids=[r["new_guid"]])
                assert scan["packages"] >= 1 and str(r["new_guid"]) not in scan["collisions"]
                print(f"clone 0x{before_guid:08X} -> 0x{r['new_guid']:08X}: {r['changed']} changed, "
                      f"{sum(p['applied'] for p in r['patches'])}/{len(r['patches'])} BHAV patches; scan OK")
            count = c.call("status")["count"]
            m = c.call("merge", path=str(donor), on_conflict="skip")
            assert m["added"] == 0 and m["skipped"] == count, m
            part = tmp / "part.package"
            first = c.call("index")["rows"][:2]
            sp = c.call("split", path=str(part), tgis=[dict(zip(cols, x)) for x in first], remove=True)
            assert sp["written"] == 2 and sp["count"] == count - 2 and part.is_file(), sp
            c.call("undo")
            assert c.call("status")["count"] == count
            expect_error("destination_protected", c.call, "split", path=str(hood_dir / "x.package"),
                         tgis=[dict(zip(cols, first[0]))])
            print("merge (all skipped), split 2 + undo, protected split refused")

            expect_error("unknown_method", c.call, "frobnicate")
            expect_error("bad_params", c.call, "get_resource", tgi="nope")
        finally:
            c.close()

    hood_smoke()
    print("rpc smoke: OK")
    return 0


def find_hood() -> "Path | None":
    """A neighborhood package readable from here: a save-diff snapshot first
    (the game's own container is off-limits to a plain shell), then the
    game folder if it happens to be readable."""
    import s2doctor
    for p in sorted(Path.home().glob("Documents/sims2-savediff/*/before/*_Neighborhood.package")):
        return p
    for root in s2doctor.ROOT_CANDIDATES:
        try:
            for p in sorted((root / "Neighborhoods").glob("N*/N*_Neighborhood.package")):
                return p
        except OSError:
            continue
    return None


def hood_smoke() -> None:
    """Sims, relationships, tokens, and a hood copy — on a real neighborhood
    package when one is readable, skipped otherwise."""
    import s2neighborhood
    hood = find_hood()
    if hood is None:
        print("skip: no readable neighborhood package for the hood checks")
        return
    with tempfile.TemporaryDirectory(prefix="s2studio-hood-") as tmp:
        tmp = Path(tmp)
        c = Client()
        try:
            info = c.call("open", path=str(hood))
            meta = c.call("hood_meta")
            assert meta["is_hood"] and meta["sdsc_fields"] and meta["sdsc_tables"]["career"]
            sims = c.call("hood_sims")["sims"]
            assert sims, "no sims"
            named = [s for s in sims if s["first"] and s["last"]]
            sim = (named or sims)[0]
            d = c.call("hood_sim", nid=sim["nid"])
            assert d["fields"]["nid"] == sim["nid"] and "skills.Logic" in d["fields"]
            before = d["fields"]["skills.Logic"]
            if d["relationships"]:
                rel = d["relationships"][0]
                r = c.call("hood_put_srel", owner=sim["nid"], target=rel["target"],
                           fields={"daily": (rel["fields"]["daily"] + 1) % 100})
                assert r["changed"]
            if d["tokens"]["editable"]:
                r = c.call("hood_put_tokens", nid=sim["nid"], first=d["tokens"]["first"], second=d["tokens"]["second"])
                assert not r["changed"], "rebuilding an unchanged token group must be a no-op"
            # The sim edit goes last so one undo/redo pair targets exactly it.
            r = c.call("hood_put_sim", nid=sim["nid"], fields={"skills.Logic": 1000 if before != 1000 else 999})
            assert r["changed"] and r["undo_label"].startswith("Edit")
            assert c.call("hood_sim", nid=sim["nid"])["fields"]["skills.Logic"] != before
            expect_error("build_failed", c.call, "hood_put_sim", nid=sim["nid"], fields={"guid": 1})
            c.call("undo")
            assert c.call("hood_sim", nid=sim["nid"])["fields"]["skills.Logic"] == before
            c.call("redo")
            dest = tmp / "copy" / hood.parent.name
            r = c.call("hood_save_as", dir=str(dest))
            assert not r["readonly"] and not r["dirty"] and (dest / hood.name).is_file()
            # A Neighborhoods folder that holds hoods is a save tree wherever
            # it lives; one that holds nothing is just a folder.
            fake = tmp / "Neighborhoods" / "N001"
            fake.mkdir(parents=True)
            shutil.copy(hood, fake / "N001_Neighborhood.package")
            expect_error("destination_protected", c.call, "hood_save_as",
                         dir=str(tmp / "Neighborhoods" / "N009"))
            got = s2neighborhood.extract_hood(dest)
            if got is not None:
                s = next(x for x in got["sims"] if x["nid"] == sim["nid"])
                assert s["skills"]["Logic"] != before
            print(f"hood {hood.parent.name}: {len(sims)} sims; edited "
                  f"{sim['first']} {sim['last']}".rstrip() + f" (nid {sim['nid']}), undo/redo, "
                  f"copied to scratch and re-extracted")
        finally:
            c.close()


def _roundtrip(decoded: dict):
    import s2studio
    return s2studio.from_json(decoded)


if __name__ == "__main__":
    sys.exit(main())
