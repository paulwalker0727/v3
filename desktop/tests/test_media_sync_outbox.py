"""Sincronizare media (cloud_sync_outbox, kind='media'): documente sensibile, stări pending/retry/error/conflict, audit.
Izolat: subproces cu folder de date temporar, date fictive, ORICE conexiune de rețea este blocată (nu există acces la PROD/Cloud)."""
import json
import os
import subprocess
import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]
SHARED = DESKTOP.parent / "shared"

PRE = r'''
import json, os, socket, sys
sys.path.insert(0, {desktop!r}); sys.path.insert(0, {shared!r})
os.chdir({desktop!r})

def _no_network(*a, **k):
    raise AssertionError("acces la rețea interzis în teste")
socket.socket.connect = _no_network
socket.create_connection = _no_network

import database as db
db.init_db()
assert str(db.DB_PATH).startswith({data!r}), db.DB_PATH
import cloud_sync as cs
import media_sync
out = {{}}
TMP = {data!r}

def q(sql, *p):
    c = db.get_connection()
    try:
        return [dict(r) for r in c.execute(sql, p).fetchall()]
    finally:
        c.close()

def mkfile(name, content=b"fictiv"):
    path = os.path.join(TMP, name)
    open(path, "wb").write(content)
    return path

def add_doc(doc_type, name, entity_type="loc", entity_id="X"):
    c = db.get_connection()
    try:
        return c.execute("INSERT INTO documents (entity_type, entity_id, doc_type, file_path) VALUES (?,?,?,?)",
                         (entity_type, entity_id, doc_type, mkfile(name))).lastrowid
    finally:
        c.close()

def calls_recorder(behaviour=None):
    calls = []
    def fake(method, path, **kw):
        calls.append((method, path, (kw.get("json") or {{}}).get("doc_type")))
        if behaviour:
            return behaviour(method, path, kw)
        return object()
    cs._request = fake
    return calls

db.set_setting("cloud_media_sync", "1")
assert media_sync.ensure_tracking(force=True)
'''
POST = "\nprint('RESULT ' + json.dumps(out, default=str))\n"


def run(tmp_path, body):
    data = str(tmp_path / "data")
    code = PRE.format(desktop=str(DESKTOP), shared=str(SHARED), data=data) + body + POST
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_") and k not in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SECRET_KEY")}
    env.update(CIMITIR_DATA_DIR=data, CIMITIR_ENV_FILE=str(tmp_path / "nu.env"), CIMITIR_LOG_DIR=str(tmp_path / "logs"),
               CIMITIR_DB_ENGINE="sqlite", CIMITIR_MODE="local", DATABASE_URL="", QT_QPA_PLATFORM="offscreen")
    p = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(DESKTOP), capture_output=True, text=True, timeout=150)
    assert p.returncode == 0, p.stderr[-4000:]
    return json.loads([ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:])


SEED_OLD_QUEUE = r'''
db.set_setting("_sync_applying", "1")
ci = add_doc("carte_identitate", "ci.pdf"); cd = add_doc("cerere_doc", "cd.pdf"); ok = add_doc("contract_scan", "ok.pdf")
def mid(i): return q("SELECT media_id FROM documents WHERE id=?", i)
c = db.get_connection()
for i, (doc, state) in enumerate([(ci, "pending"), (ci, "retry"), (ci, "error"), (cd, "conflict"),
                                  (ok, "pending"), (ok, "error"), (ok, "conflict")]):
    m = "m%d" % doc
    c.execute("UPDATE documents SET media_id=? WHERE id=?", (m, doc))
    c.execute("INSERT INTO cloud_sync_outbox (kind, entity, entity_uid, action, base_version, state, op_id, created_at, attempts, last_error, "
              "tracking_code, status, note, payload) VALUES ('media','documents',?, 'upload', 0, ?, ?, '1', 3, 'eroare veche', '', '', '', '{}')",
              (m, state, "op-%d" % i))
c.execute("INSERT INTO cloud_sync_outbox (kind, entity, entity_uid, action, base_version, state, op_id, created_at, attempts, last_error, "
          "tracking_code, status, note, payload) VALUES ('entity','concesionari','u1','update',1,'conflict','op-ent','1',1,'', '', '', '', '{}')")
c.close()
db.set_setting("_sync_applying", "")
'''


def test_cleanup_removes_identity_ops_in_all_states_keeps_files_allowed_ops_and_audit_trail(tmp_path):
    out = run(tmp_path, SEED_OLD_QUEUE + r'''
before = q("SELECT COUNT(*) n FROM cloud_sync_outbox")[0]["n"]
assert media_sync.ensure_tracking(force=True)
out["before"] = before
out["identity_left"] = q("SELECT COUNT(*) n FROM cloud_sync_outbox o JOIN documents d ON d.media_id=o.entity_uid "
                         "WHERE d.doc_type IN ('carte_identitate','cerere_doc')")[0]["n"]
out["allowed_states"] = sorted(r["state"] for r in q("SELECT state FROM cloud_sync_outbox WHERE kind='media'"))
out["entity_ops"] = q("SELECT state FROM cloud_sync_outbox WHERE kind='entity'")
out["docs"] = q("SELECT COUNT(*) n FROM documents")[0]["n"]
out["files"] = sorted(f for f in os.listdir(TMP) if f.endswith(".pdf"))
audit = q("SELECT action, target, detail FROM audit_log WHERE action='media_outbox_purge'")
out["audit_n"] = len(audit)
out["audit_ops"] = sorted(o["op_id"] for o in json.loads(audit[0]["detail"])) if audit else []
out["audit_detail"] = audit[0]["detail"] if audit else ""
''')
    assert out["before"] == 8 and out["identity_left"] == 0
    assert out["allowed_states"] == ["conflict", "error", "pending"]
    assert out["entity_ops"] == [{"state": "conflict"}]
    assert out["docs"] == 3 and out["files"] == ["cd.pdf", "ci.pdf", "ok.pdf"]
    assert out["audit_n"] == 1 and out["audit_ops"] == ["op-0", "op-1", "op-2", "op-3"]
    assert ".pdf" not in out["audit_detail"] and "/" not in out["audit_detail"].replace("\\/", "")


def test_second_ensure_tracking_does_not_repeat_purge_or_audit(tmp_path):
    out = run(tmp_path, SEED_OLD_QUEUE + r'''
media_sync.ensure_tracking(force=True); media_sync.ensure_tracking(force=True)
out["audit_n"] = q("SELECT COUNT(*) n FROM audit_log WHERE action='media_outbox_purge'")[0]["n"]
''')
    assert out["audit_n"] == 1


def test_doc_retyped_to_identity_after_queueing_is_never_uploaded(tmp_path):
    out = run(tmp_path, r'''
doc = add_doc("contract_scan", "retype.pdf")
out["queued"] = q("SELECT COUNT(*) n FROM cloud_sync_outbox WHERE kind='media' AND action='upload'")[0]["n"]
c = db.get_connection(); c.execute("UPDATE documents SET doc_type='carte_identitate' WHERE id=?", (doc,)); c.close()
calls = calls_recorder()
ok, lines, counts = media_sync.flush_media()
out["calls"] = calls; out["counts"] = counts
out["left"] = q("SELECT COUNT(*) n FROM cloud_sync_outbox WHERE kind='media'")[0]["n"]
out["doc_still_there"] = q("SELECT COUNT(*) n FROM documents WHERE id=?", doc)[0]["n"]
out["file_still_there"] = os.path.isfile(os.path.join(TMP, "retype.pdf"))
out["audit"] = q("SELECT target FROM audit_log WHERE action='media_outbox_purge'")
''')
    assert out["queued"] == 1
    assert out["calls"] == [], "document de identitate trimis în Cloud"
    assert out["counts"]["dropped"] == 1 and out["left"] == 0
    assert out["doc_still_there"] == 1 and out["file_still_there"]
    assert out["audit"] == [{"target": "blocked_doc_type_at_send"}]


def test_allowed_document_still_uploads_and_local_file_is_kept(tmp_path):
    out = run(tmp_path, r'''
import entity_sync
entity_sync.ensure_tracking(force=True)
add_doc("contract_scan", "contract.pdf", entity_type="contract", entity_id="1")
calls = calls_recorder()
ok, lines, counts = media_sync.flush_media()
out["ok"] = ok; out["counts"] = counts; out["calls"] = calls
out["left"] = q("SELECT COUNT(*) n FROM cloud_sync_outbox WHERE kind='media'")[0]["n"]
out["file"] = os.path.isfile(os.path.join(TMP, "contract.pdf"))
''')
    assert out["ok"] and out["counts"]["uploaded"] == 1 and out["left"] == 0 and out["file"]
    methods = [c[0] for c in out["calls"]]
    assert methods == ["PUT", "POST"] and out["calls"][1][2] == "contract_scan"


def test_pending_retry_error_and_conflict_states_are_handled_without_data_loss(tmp_path):
    out = run(tmp_path, r'''
d1 = add_doc("contract_scan", "a.pdf"); d2 = add_doc("contract_scan", "b.pdf")
def states(): return {r["entity_uid"]: (r["state"], r["attempts"], r["last_error"]) for r in q("SELECT * FROM cloud_sync_outbox WHERE kind='media'")}
# 1) offline: rămâne pending, nimic pierdut
def offline(m, p, kw): raise cs.RetryLater("fără internet")
calls_recorder(offline)
ok, lines, counts = media_sync.flush_media()
out["offline_ok"] = ok; out["offline_states"] = sorted(v[0] for v in states().values())
# 2) respins definitiv (403) -> error; erorile NU se retrimit automat
def reject(m, p, kw): raise cs.Rejected(403, "interzis")
calls_recorder(reject)
ok, lines, counts = media_sync.flush_media()
out["reject_counts"] = counts["error"]; out["after_reject"] = sorted(v[0] for v in states().values())
calls = calls_recorder()
media_sync.flush_media()
out["calls_for_error_rows"] = calls
# 3) conflict: nu se retrimite
c = db.get_connection(); c.execute("UPDATE cloud_sync_outbox SET state='conflict'"); c.close()
calls = calls_recorder()
media_sync.flush_media()
out["calls_for_conflict_rows"] = calls
out["docs"] = q("SELECT COUNT(*) n FROM documents")[0]["n"]
out["files"] = sorted(f for f in os.listdir(TMP) if f.endswith(".pdf"))
''')
    assert out["offline_ok"] is False and out["offline_states"] == ["pending", "pending"]
    assert out["reject_counts"] == 1 or out["reject_counts"] == 2
    assert set(out["after_reject"]) <= {"error", "pending"}
    assert out["calls_for_conflict_rows"] == []
    assert out["docs"] == 2 and out["files"] == ["a.pdf", "b.pdf"]


def test_missing_local_file_marks_error_but_keeps_document_row(tmp_path):
    out = run(tmp_path, r'''
doc = add_doc("contract_scan", "gone.pdf")
os.remove(os.path.join(TMP, "gone.pdf"))
calls = calls_recorder()
ok, lines, counts = media_sync.flush_media()
out["counts"] = counts; out["calls"] = calls
out["state"] = q("SELECT state, last_error FROM cloud_sync_outbox WHERE kind='media'")
out["docs"] = q("SELECT COUNT(*) n FROM documents")[0]["n"]
''')
    assert out["calls"] == [] and out["counts"]["error"] == 1 and out["docs"] == 1
    assert out["state"][0]["state"] == "error"


def test_410_gone_in_cloud_drops_stale_operation_keeps_local_document(tmp_path):
    out = run(tmp_path, r'''
add_doc("contract_scan", "stale.pdf")
def gone(m, p, kw): raise cs.Rejected(410, "șters")
calls_recorder(gone)
ok, lines, counts = media_sync.flush_media()
out["counts"] = counts
out["left"] = q("SELECT COUNT(*) n FROM cloud_sync_outbox WHERE kind='media'")[0]["n"]
out["docs"] = q("SELECT COUNT(*) n FROM documents")[0]["n"]
out["file"] = os.path.isfile(os.path.join(TMP, "stale.pdf"))
''')
    assert out["counts"]["dropped"] == 1 and out["left"] == 0 and out["docs"] == 1 and out["file"]


def test_network_is_blocked_inside_isolated_media_tests(tmp_path):
    out = run(tmp_path, r'''
import socket
try:
    socket.create_connection(("127.0.0.1", 9), timeout=1); out["blocked"] = False
except AssertionError:
    out["blocked"] = True
''')
    assert out["blocked"] is True
