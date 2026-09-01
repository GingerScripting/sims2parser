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
    """A sample package with at least one decodable STR#."""
    samples = ROOT / "sample-packages"
    if not samples.is_dir():
        return None
    for p in sorted(samples.rglob("*.package")):
        try:
            _, entries = s2parser.open_package(p)
        except Exception:
            continue
        if any(e.type_id == s2object.TYPE_STR for e in entries):
            return p
    return None


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

            expect_error("unknown_method", c.call, "frobnicate")
            expect_error("bad_params", c.call, "get_resource", tgi="nope")
        finally:
            c.close()

    print("rpc smoke: OK")
    return 0


def _roundtrip(decoded: dict):
    import s2studio
    return s2studio.from_json(decoded)


if __name__ == "__main__":
    sys.exit(main())
