"""macOS: baza locală în ~/CIMITIR-MAC/date și sincronizare offline -> online cu mecanismele EXISTENTE (outbox, op_id, versiuni, conflicte).

Scenariul rulează într-un subproces offscreen cu `cimitir_config` simulând macOS (fără CIMITIR_DATA_DIR) și un SIMULATOR de server
(același ca în test_p2_sync_erori_client). Date fictive, fără rețea, fără PROD. Nu înlocuiește testarea pe un Mac real."""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_p2_sync_erori_client import DESKTOP, PRE, POST, SHARED  # noqa: E402

MAC_PATCH = """
import types, cimitir_config
cimitir_config.sys = types.SimpleNamespace(platform="darwin")
import database as db
"""
PRE_MAC = PRE.replace("import database as db\n", MAC_PATCH, 1)

OFFLINE = """
real_call = es._call
mode = {"v": "ok"}
def faulty(method, op, **kw):
    if mode["v"] == "offline" and op in ("push", "pull"):
        raise cs.RetryLater("Fara conexiune la server: offline")
    return real_call(method, op, **kw)
es._call = faulty
use(PATH_A)
"""


def run_mac(tmp_path, body):
    home = tmp_path / "CIMITIR-MAC"
    code = PRE_MAC.format(desktop=str(DESKTOP), shared=str(SHARED)) + body + POST
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_") and k not in ("DATABASE_URL", "SUPABASE_URL")}
    env.update(CIMITIR_MAC_HOME=str(home), CIMITIR_ENV_FILE=str(tmp_path / "nu.env"), CIMITIR_LOG_DIR=str(tmp_path / "logs"),
               CIMITIR_IGNORE_PROJECT_ENV="1", CIMITIR_DB_ENGINE="sqlite", CIMITIR_MODE="local", DATABASE_URL="", QT_QPA_PLATFORM="offscreen")
    done = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(DESKTOP), capture_output=True, text=True, timeout=150)
    assert done.returncode == 0, done.stderr[-4000:]
    return home, json.loads([ln for ln in done.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:])


def test_local_database_is_created_under_cimitir_mac_date_and_nowhere_else(tmp_path):
    home, out = run_mac(tmp_path, "out['db'] = PATH_A\n")
    assert Path(out["db"]).resolve() == (home / "date" / "cimitir-data" / "cimitir.db").resolve()
    assert (home / "date" / "cimitir-data" / "cimitir.db").is_file()
    assert "shared_data" not in out["db"]


def test_offline_changes_to_every_entity_are_kept_then_sent_once_when_online_returns(tmp_path):
    _, out = run_mac(tmp_path, OFFLINE + """
db.save_plot_to_db("M-1", SQ, "#00ff00")
cid = db.save_concesionar({"nume": "Test", "prenume": "Fictiv", "cnp": "1000000000001", "adresa": "Str. Test 1"})
ctr = db.save_contract({"numar_contract": "T-1", "cod_loc": "M-1", "concesionar_id": cid})
inh = db.save_inhumat("M-1", {"nume": "Fictiv", "prenume": "Defunct", "data_inhumare": "2020-01-01"})
pl = db.add_plata("M-1", 100.0, "2026-01-01", chitanta_nr="C-1", contract_id=ctr)
av = db.add_aviz("M-1", "A-1", "2026-01-02", "lucrare test")
out["created"] = [bool(x) for x in (cid, ctr, inh, pl, av)]
mode["v"] = "offline"
ok, _l, _c = es.flush_entities()
out["offline_ok"] = ok
ops = outbox()
out["pending_entities"] = sorted({o["entity"] for o in ops})
out["server_rows_offline"] = len(SRV.rows)
ids_before = sorted(o["op_id"] for o in ops)
mode["v"] = "ok"
ok2, _l, c2 = es.flush_entities()
out["online_ok"] = ok2
out["pending_after"] = len(outbox())
out["server_entities"] = sorted({k[0] for k in SRV.rows})
out["op_ids_unique"] = len(SRV.log) == len(set(SRV.log))
before = len(SRV.log)
es.flush_entities()
out["resend_calls"] = len(SRV.log) - before
out["ids_sent_were_queued_ids"] = sorted(SRV.log) == ids_before
""")
    assert out["created"] == [True] * 5
    assert out["offline_ok"] is False and out["server_rows_offline"] == 0
    assert out["pending_entities"] == ["amenajari_avize", "concesionari", "contracte", "inhumati", "locuri_de_veci", "plati"]
    assert out["online_ok"] is True and out["pending_after"] == 0
    assert out["server_entities"] == out["pending_entities"]
    assert out["op_ids_unique"] is True and out["resend_calls"] == 0 and out["ids_sent_were_queued_ids"] is True


def test_conflict_with_another_computer_is_reported_and_local_data_is_not_overwritten(tmp_path):
    _, out = run_mac(tmp_path, OFFLINE + """
db.save_plot_to_db("M-2", SQ, "#00ff00")
ok, _l, _c = es.flush_entities()
out["first_ok"] = ok
key = next(k for k in SRV.rows if k[0] == "locuri_de_veci")
SRV.rows[key]["data"]["color"] = "#ff0000"; SRV.rows[key]["version"] += 1; SRV.seq += 1; SRV.rows[key]["seq"] = SRV.seq
db.save_plot_to_db("M-2", SQ, "#0000ff")
ok2, _l, c2 = es.flush_entities()
out["second_ok"] = ok2; out["conflicts"] = c2["conflict"]
out["local_color"] = loc_row("M-2")["color"]
out["server_color"] = SRV.rows[key]["data"]["color"]
out["pending_states"] = sorted(o["state"] for o in outbox())
""")
    assert out["first_ok"] is True
    assert out["conflicts"] == 1 and out["second_ok"] is False
    assert out["local_color"] == "#0000ff" and out["server_color"] == "#ff0000"
    assert out["pending_states"] == ["conflict"]


def test_delete_made_offline_is_sent_as_a_tombstone_after_reconnect(tmp_path):
    _, out = run_mac(tmp_path, OFFLINE + """
db.save_plot_to_db("M-3", SQ, "#00ff00")
es.flush_entities()
key = next(k for k in SRV.rows if k[0] == "locuri_de_veci")
c = db.get_connection(); c.execute("DELETE FROM locuri_de_veci WHERE cod_loc='M-3'"); c.commit(); c.close()
mode["v"] = "offline"
ok, _l, _c = es.flush_entities()
out["offline_ok"] = ok; out["pending"] = len(outbox()); out["deleted_before"] = SRV.rows[key]["deleted"]
mode["v"] = "ok"
ok2, _l, _c = es.flush_entities()
out["online_ok"] = ok2; out["pending_after"] = len(outbox()); out["deleted_after"] = SRV.rows[key]["deleted"]
""")
    assert out["offline_ok"] is False and out["pending"] >= 1 and out["deleted_before"] is False
    assert out["online_ok"] is True and out["pending_after"] == 0 and out["deleted_after"] is True
