"""P2 §5: client de sincronizare MAIN <-> Cloud TEST (SIMULATOR local de server) la erori de rețea/răspuns.

Scenarii: timeout, conexiune întreruptă DUPĂ ce serverul a aplicat operația, JSON trunchiat, răspuns fără rezultate.
Regula: operațiunea rămâne în coadă sub același op_id (retrimitere idempotentă) și rezultatul este raportat ca NEREUȘIT; fără pierderi
silențioase, fără excepții necontrolate. Simulatorul NU este serverul real/PostgreSQL. Date fictive, subproces offscreen, fără rețea."""
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


FAULT = """
real_call = es._call
mode = {"v": "ok"}
class Bad:
    def json(self): raise ValueError("Expecting value: truncated body")
def faulty(method, op, **kw):
    m = mode["v"]
    if op == "push":
        if m == "timeout":
            raise cs.RetryLater("Fara conexiune la server: timeout")
        if m == "lost_response":
            real_call(method, op, **kw)
            raise cs.RetryLater("Fara conexiune la server: connection reset")
        if m == "truncated":
            return Bad()
        if m == "empty":
            return Resp({})
    if op == "pull" and m == "pull_truncated":
        return Bad()
    return real_call(method, op, **kw)
es._call = faulty
use(PATH_A)
db.save_plot_to_db("L-1", SQ, "#00ff00")
"""


def test_timeout_lasa_operatia_in_coada_si_retrimiterea_o_confirma_fara_duplicat(tmp_path):
    out = run(tmp_path, FAULT + """
mode["v"] = "timeout"
ok, lines, counts = es.flush_entities()
out["ok_timeout"] = ok; out["pending_after_timeout"] = len(outbox()); out["server_rows_after_timeout"] = len(SRV.rows)
mode["v"] = "ok"
ok2, _l, c2 = es.flush_entities()
out["ok_retry"] = ok2; out["pending_after_retry"] = len(outbox()); out["server_rows"] = len(SRV.rows)
""")
    assert out["ok_timeout"] is False and out["pending_after_timeout"] >= 1 and out["server_rows_after_timeout"] == 0
    assert out["ok_retry"] is True and out["pending_after_retry"] == 0 and out["server_rows"] == 1


def test_raspuns_pierdut_dupa_aplicarea_pe_server_nu_creeaza_duplicat_la_retrimitere(tmp_path):
    out = run(tmp_path, FAULT + """
mode["v"] = "lost_response"
ok, _l, _c = es.flush_entities()
out["ok_lost"] = ok; out["pending_lost"] = len(outbox()); out["server_rows_lost"] = len(SRV.rows)
mode["v"] = "ok"
ok2, _l, _c = es.flush_entities()
r = loc_row("L-1")
out["ok_retry"] = ok2; out["pending_retry"] = len(outbox()); out["server_rows"] = len(SRV.rows)
out["server_version"] = list(SRV.rows.values())[0]["version"]; out["local_version"] = r["version"]
out["push_calls_same_op"] = len(SRV.log) - len(set(SRV.log))
""")
    assert out["ok_lost"] is False and out["pending_lost"] >= 1 and out["server_rows_lost"] == 1
    assert out["ok_retry"] is True and out["pending_retry"] == 0 and out["server_rows"] == 1
    assert out["server_version"] == 1 and out["local_version"] == 1 and out["push_calls_same_op"] >= 1


def test_json_trunchiat_la_push_nu_ridica_exceptie_necontrolata_si_pastreaza_coada(tmp_path):
    out = run(tmp_path, FAULT + """
mode["v"] = "truncated"
try:
    ok, lines, counts = es.flush_entities()
    out["raised"] = False; out["ok"] = ok; out["lines"] = lines
except Exception as exc:
    out["raised"] = type(exc).__name__
out["pending"] = len(outbox())
mode["v"] = "ok"
ok2, _l, _c = es.flush_entities()
out["ok_retry"] = ok2; out["server_rows"] = len(SRV.rows); out["pending_retry"] = len(outbox())
""")
    assert out["raised"] is False, f"exceptie necontrolata: {out['raised']}"
    assert out["ok"] is False and out["pending"] >= 1
    assert out["ok_retry"] is True and out["server_rows"] == 1 and out["pending_retry"] == 0


def test_raspuns_fara_rezultate_nu_este_raportat_ca_succes(tmp_path):
    out = run(tmp_path, FAULT + """
mode["v"] = "empty"
ok, lines, counts = es.flush_entities()
out["ok"] = ok; out["pending"] = len(outbox()); out["lines"] = lines
mode["v"] = "ok"
ok2, _l, _c = es.flush_entities()
out["ok_retry"] = ok2; out["pending_retry"] = len(outbox())
""")
    assert out["ok"] is False, "operatiuni neconfirmate raportate ca reusite"
    assert out["pending"] >= 1 and out["ok_retry"] is True and out["pending_retry"] == 0


def test_json_trunchiat_la_pull_nu_avanseaza_cursorul_si_nu_strica_datele_locale(tmp_path):
    out = run(tmp_path, FAULT + """
mode["v"] = "ok"
es.flush_entities()
before_cursor = db.get_setting(es.CURSOR_PREFIX + "locuri_de_veci")
mode["v"] = "pull_truncated"
try:
    ok, msg, n = es.pull_entities(); out["raised"] = False; out["ok"] = ok
except Exception as exc:
    out["raised"] = type(exc).__name__
out["cursor_unchanged"] = db.get_setting(es.CURSOR_PREFIX + "locuri_de_veci") == before_cursor
out["local"] = loc_row("L-1") is not None
""")
    assert out["raised"] is False, f"exceptie necontrolata: {out['raised']}"
    assert out["ok"] is False and out["cursor_unchanged"] and out["local"]
