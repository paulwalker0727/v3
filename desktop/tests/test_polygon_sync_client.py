"""Poligoane (locuri_de_veci.points) în sincronizarea pe entități MAIN <-> Cloud, cu un SIMULATOR local al serverului.

IMPORTANT: serverul de aici este un simulator Python al contractului `push`/`pull` (op_id idempotent, base_version, conflict), NU
backend-ul real și NU PostgreSQL. Testele dovedesc comportamentul clientului MAIN (captare, coadă, identificatori, geometrie, conflicte).
Comportamentul real al serverului/PostgreSQL pe poligoane rămâne de validat în Cloud TEST (vezi RAPORT_AUDIT_SYNC_POLIGOANE.md).
Subproces offscreen, foldere temporare, date fictive; nicio rețea, nicio bază reală."""
import json
import os
import subprocess
import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]
SHARED = DESKTOP.parent / "shared"

PRE = r'''
import json, os, sys, uuid, copy
sys.path.insert(0, {desktop!r}); sys.path.insert(0, {shared!r})
os.chdir({desktop!r})
import database as db
import entity_sync as es
import cloud_sync as cs
db.init_db()
out = {{}}
PATH_A = db.current_db_path()
PATH_B = os.path.join(os.path.dirname(PATH_A), "client_b.db")

class Resp:
    def __init__(self, body): self._b = body
    def json(self): return self._b

class FakeServer:
    """Simulator: o linie per (entity, uid); versiune crescătoare; op_id deja aplicat -> același rezultat, fără modificare."""
    def __init__(self):
        self.rows, self.seq, self.done, self.log = {{}}, 0, {{}}, []
    def push(self, ops):
        res = []
        for op in ops:
            self.log.append(op["op_id"])
            if op["op_id"] in self.done:
                res.append(dict(self.done[op["op_id"]], duplicate=True)); continue
            key = (op["entity"], op["uid"]); cur = self.rows.get(key)
            sv = cur["version"] if cur else 0
            if op["action"] == "upsert":
                if cur and op["base_version"] != sv:
                    r = {{"op_id": op["op_id"], "status": "conflict", "reason": "version_mismatch", "server_version": sv, "server": copy.deepcopy(cur["data"])}}
                else:
                    self.seq += 1
                    self.rows[key] = {{"version": sv + 1, "data": copy.deepcopy(op["data"]), "seq": self.seq, "deleted": False}}
                    r = {{"op_id": op["op_id"], "status": "applied", "version": sv + 1, "updated_at": "2026-01-01T00:00:00"}}
            else:
                self.seq += 1
                if cur: cur.update(version=sv + 1, deleted=True, seq=self.seq)
                r = {{"op_id": op["op_id"], "status": "applied"}}
            self.done[op["op_id"]] = r
            res.append(r)
        return {{"results": res}}
    def pull(self, entity, since):
        ch = [{{"uid": k[1], "version": v["version"], "updated_at": "2026-01-01T00:00:00", "deleted": v["deleted"], "data": copy.deepcopy(v["data"])}}
              for k, v in sorted(self.rows.items(), key=lambda kv: kv[1]["seq"]) if k[0] == entity and v["seq"] > since]
        return {{"changes": ch, "cursor": self.seq, "more": False}}

SRV = FakeServer()
def fake_call(method, op, **kw):
    if op == "push":
        return Resp(SRV.push(kw["json"]["ops"]))
    if op == "pull":
        p = kw["params"]; return Resp(SRV.pull(p["entity"], p["since"]))
    if op == "status":
        return Resp({{}})
    raise AssertionError(op)
es._call = fake_call

def use(path):
    db._active_db_path.set(path)
    if path != PATH_A and not os.path.exists(path):
        db.init_db()
    db.set_setting("cloud_entity_sync", "1")
    es.ensure_tracking(force=True)

def loc_row(cod):
    c = db.get_connection()
    try:
        r = c.execute("SELECT id, uid, version, points, color FROM locuri_de_veci WHERE cod_loc=?", (cod,)).fetchone()
        return dict(r) if r else None
    finally:
        c.close()

def outbox(cod=None):
    return [o for o in es.pending_entity_ops(("pending", "conflict", "error"))]

SQ = [[0, 0], [10, 0], [10, 10], [0, 10]]
'''
POST = "\nprint('RESULT ' + json.dumps(out, default=str))\n"


def run(tmp_path, body):
    data = str(tmp_path / "data")
    code = PRE.format(desktop=str(DESKTOP), shared=str(SHARED)) + body + POST
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_") and k not in ("DATABASE_URL", "SUPABASE_URL")}
    env.update(CIMITIR_DATA_DIR=data, CIMITIR_ENV_FILE=str(tmp_path / "nu.env"), CIMITIR_LOG_DIR=str(tmp_path / "logs"),
               CIMITIR_DB_ENGINE="sqlite", CIMITIR_MODE="local", DATABASE_URL="", QT_QPA_PLATFORM="offscreen")
    p = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(DESKTOP), capture_output=True, text=True, timeout=150)
    assert p.returncode == 0, p.stderr[-4000:]
    return json.loads([ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:])


def test_create_polygon_is_captured_and_reaches_server_with_same_geometry(tmp_path):
    out = run(tmp_path, r'''
use(PATH_A)
loc_id = db.save_plot_to_db("L-1", SQ, "#00ff00")
db.save_loc_metadata("L-1", {"sector": "A", "parcela": "1"})
ops = outbox()
out["ops"] = [(o["entity"], o["action"]) for o in ops]
ok, lines, counts = es.flush_entities()
srv = SRV.rows[("locuri_de_veci", loc_row("L-1")["uid"])]
out["server_points"] = srv["data"]["points"]; out["server_cod"] = srv["data"]["cod_loc"]; out["server_version"] = srv["version"]
r = loc_row("L-1"); out["local_id_same"] = r["id"] == loc_id; out["local_version"] = r["version"]
out["pending_after"] = len(outbox())
''')
    assert out["ops"] == [["locuri_de_veci", "upsert"]]
    assert out["server_points"] == [[0, 0], [10, 0], [10, 10], [0, 10]] and out["server_cod"] == "L-1"
    assert out["server_version"] == 1 and out["local_version"] == 1 and out["local_id_same"] and out["pending_after"] == 0


def test_editing_coordinates_keeps_id_and_uid_and_replaces_pending_op(tmp_path):
    out = run(tmp_path, r'''
use(PATH_A)
loc_id = db.save_plot_to_db("L-1", SQ, "#00ff00")
es.flush_entities()
before = loc_row("L-1")
NEW = [[0, 0], [12.5, 0], [12.5, 11.25], [0, 11.25]]
db.save_plot_to_db("L-1", NEW, "#00ff00")
db.save_plot_to_db("L-1", NEW, "#00ff00")          # a doua salvare, identică: nu trebuie să dubleze operațiunea
after = loc_row("L-1")
ops = outbox()
out["n_ops"] = len(ops); out["same_id"] = after["id"] == before["id"] == loc_id; out["same_uid"] = after["uid"] == before["uid"]
out["base_version"] = ops[0]["base_version"]
es.flush_entities()
out["server_points"] = SRV.rows[("locuri_de_veci", before["uid"])]["data"]["points"]
out["server_version"] = SRV.rows[("locuri_de_veci", before["uid"])]["version"]
c = db.get_connection(); out["count_locs"] = c.execute("SELECT COUNT(*) FROM locuri_de_veci").fetchone()[0]; c.close()
''')
    assert out["n_ops"] == 1 and out["same_id"] and out["same_uid"] and out["base_version"] == 1
    assert out["server_points"] == [[0, 0], [12.5, 0], [12.5, 11.25], [0, 11.25]] and out["server_version"] == 2 and out["count_locs"] == 1


def test_resending_same_operation_does_not_duplicate_or_bump_version(tmp_path):
    out = run(tmp_path, r'''
use(PATH_A)
db.save_plot_to_db("L-1", SQ, "#00ff00")
op = es.pending_entity_ops()[0]
conn = db.get_connection(); row = es._read_wire(conn, "locuri_de_veci", op["entity_uid"]); conn.close()
wire = {"op_id": op["op_id"], "entity": "locuri_de_veci", "uid": op["entity_uid"], "action": "upsert", "base_version": 0, "data": row["data"]}
r1 = SRV.push([wire]); r2 = SRV.push([wire])      # răspuns pierdut -> aceeași operațiune retrimisă
out["r1"], out["r2"] = r1["results"][0]["status"], r2["results"][0]["status"]
out["server_version"] = SRV.rows[("locuri_de_veci", op["entity_uid"])]["version"]
out["server_rows"] = len(SRV.rows)
''')
    assert out["r1"] == "applied" and out["r2"] == "applied" and out["server_version"] == 1 and out["server_rows"] == 1


def test_second_client_receives_polygon_with_same_uid_and_geometry_without_echo(tmp_path):
    out = run(tmp_path, r'''
use(PATH_A)
db.save_plot_to_db("L-1", [[0.1234567, 1.5], [10, 0], [10, 10.000001]], "#abcdef")
es.flush_entities()
uid = loc_row("L-1")["uid"]
use(PATH_B)
ok, msg, n = es.pull_entities()
b = loc_row("L-1")
out["b_uid_same"] = b["uid"] == uid; out["b_points"] = json.loads(b["points"]); out["b_color"] = b["color"]; out["b_version"] = b["version"]
out["b_outbox"] = len(outbox())          # ce vine de la server nu se retrimite
''')
    assert out["b_uid_same"] and out["b_points"] == [[0.1234567, 1.5], [10, 0], [10, 10.000001]] and out["b_color"] == "#abcdef"
    assert out["b_version"] == 1 and out["b_outbox"] == 0


def test_concurrent_edit_is_reported_as_conflict_and_local_geometry_is_not_overwritten(tmp_path):
    out = run(tmp_path, r'''
use(PATH_A)
db.save_plot_to_db("L-1", SQ, "#00ff00"); es.flush_entities()
uid = loc_row("L-1")["uid"]
use(PATH_B); es.pull_entities()
A_NEW = [[0, 0], [20, 0], [20, 20], [0, 20]]; B_NEW = [[1, 1], [9, 1], [9, 9], [1, 9]]
use(PATH_A); db.save_plot_to_db("L-1", A_NEW, "#00ff00"); es.flush_entities()
use(PATH_B); db.save_plot_to_db("L-1", B_NEW, "#00ff00")
ok, lines, counts = es.flush_entities()
out["counts"] = counts
out["server_points"] = SRV.rows[("locuri_de_veci", uid)]["data"]["points"]
out["b_local"] = json.loads(loc_row("L-1")["points"])
out["b_ops"] = [(o["state"], o["action"]) for o in es.pending_entity_ops(("pending", "conflict", "error"))]
es.pull_entities()                                   # cu un conflict deschis, pull NU suprascrie geometria locală
out["b_local_after_pull"] = json.loads(loc_row("L-1")["points"])
''')
    assert out["counts"]["conflict"] == 1 and out["counts"]["applied"] == 0
    assert out["server_points"] == [[0, 0], [20, 0], [20, 20], [0, 20]]
    assert out["b_local"] == [[1, 1], [9, 1], [9, 9], [1, 9]] and out["b_local_after_pull"] == out["b_local"]
    assert out["b_ops"] == [["conflict", "upsert"]]


def test_historic_polygons_get_uid_without_changing_id_or_points(tmp_path):
    out = run(tmp_path, r'''
c = db.get_connection()
c.execute("INSERT INTO locuri_de_veci (cod_loc, points, color, extra_data) VALUES ('H-1', '[[0,0],[5.5,0],[5.5,5.5]]', '#111111', '{}')")
c.execute("INSERT INTO locuri_de_veci (cod_loc, points, color, extra_data) VALUES ('H-2', '[]', '#222222', '{}')")      # fără geometrie
c.execute("INSERT INTO locuri_de_veci (cod_loc, points, color, extra_data) VALUES ('H-3', NULL, NULL, '{}')")          # istoric incomplet
c.commit()
before = {r["cod_loc"]: (r["id"], r["points"], r["color"]) for r in c.execute("SELECT id, cod_loc, points, color FROM locuri_de_veci")}
c.close()
use(PATH_A)
c = db.get_connection()
after = {r["cod_loc"]: (r["id"], r["points"], r["color"]) for r in c.execute("SELECT id, cod_loc, points, color FROM locuri_de_veci")}
uids = [r["uid"] for r in c.execute("SELECT uid FROM locuri_de_veci")]
c.close()
out["unchanged"] = before == after
out["uids_ok"] = all(uids) and len(set(uids)) == 3
n = es.enqueue_bootstrap(); out["bootstrap"] = n
ok, lines, counts = es.flush_entities()
out["applied"] = counts["applied"]
srv = {k[1]: v["data"] for k, v in SRV.rows.items()}
out["server_points"] = sorted((d["cod_loc"], d["points"]) for d in srv.values())
''')
    assert out["unchanged"] and out["uids_ok"]
    assert out["applied"] == 3
    assert out["server_points"] == [["H-1", [[0, 0], [5.5, 0], [5.5, 5.5]]], ["H-2", []], ["H-3", []]]


def test_pull_of_existing_server_polygon_never_replaces_pending_local_geometry(tmp_path):
    out = run(tmp_path, r'''
use(PATH_A); db.save_plot_to_db("L-1", SQ, "#00ff00"); es.flush_entities()
use(PATH_B); es.pull_entities()
use(PATH_A); db.save_plot_to_db("L-1", [[0, 0], [30, 0], [30, 30]], "#00ff00"); es.flush_entities()
use(PATH_B); db.save_plot_to_db("L-1", [[2, 2], [4, 2], [4, 4]], "#00ff00")          # modificare locală încă netrimisă
ok, msg, n = es.pull_entities()
out["local"] = json.loads(loc_row("L-1")["points"]); out["pending"] = len(outbox())
''')
    assert out["local"] == [[2, 2], [4, 2], [4, 4]] and out["pending"] == 1


def test_deleting_never_synced_polygon_sends_nothing_but_synced_one_sends_delete(tmp_path):
    out = run(tmp_path, r'''
use(PATH_A)
db.save_plot_to_db("N-1", SQ, "#000000"); db.delete_plot("N-1")           # niciodată trimis: nu există nimic de șters în cloud
out["ops_after_local_only_delete"] = len(outbox())
db.save_plot_to_db("S-1", SQ, "#000000"); es.flush_entities(); uid = loc_row("S-1")["uid"]
db.delete_plot("S-1")
out["ops_delete"] = [(o["action"], o["entity_uid"] == uid) for o in outbox()]
es.flush_entities()
out["server_deleted"] = SRV.rows[("locuri_de_veci", uid)]["deleted"]
''')
    assert out["ops_after_local_only_delete"] == 0 and out["ops_delete"] == [["delete", True]] and out["server_deleted"] is True
