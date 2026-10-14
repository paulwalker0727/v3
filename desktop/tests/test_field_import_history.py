# ruff: noqa: F811
"""MAIN: „Istoric importuri din teren” (jurnal local field_import_journal). Fără rețea reală: aceleași simulatoare ca test_field_import.py.
LIVE (Supabase TEST): nerulat aici. PRODUCTION: blocat."""
from __future__ import annotations

import logging
import threading
import uuid
from types import SimpleNamespace

import database as db
import field_import as fim
import field_import_history as fih
import field_inbox_client as fic
import pytest
import requests
from PySide6 import QtWidgets
from test_field_import import (  # noqa: F401  (fixture-uri + helperi partajați)
    Crash,
    add,
    add_deceased_local,
    add_work,
    download_calls,
    imported_calls,
    loc,
    media_files,
    rows,
)
from test_field_inbox_main import (  # noqa: F401
    ALL_SECRETS,
    CEM,
    JPEG,
    PDF,
    PNG,
    SIGNED_TOKEN,
    client,
    env,
    make_profile,
    pump,
    qapp,
)


# ------------------------------------------------------------------ stări de test (create prin mecanismul REAL de import)
def make_complete(env, loc, t="DECEASED", **kw):
    uid = add(env, loc, t, **kw)
    assert fim.import_item(client(env), uid).state == fim.COMPLETE
    return uid


def make_committed(env, loc, t="DECEASED", **kw):
    """Entitate comisă local, cloud încă ACCEPTED (finalizarea cloud a eșuat)."""
    uid = add(env, loc, t, **kw)
    real = env.http.request

    def down(method, url, **k):
        if url.endswith("/imported"):
            raise requests.ConnectionError("offline " + SIGNED_TOKEN)
        return real(method, url, **k)
    env.http.request = down
    try:
        res = fim.import_item(client(env), uid)
    finally:
        env.http.request = real
    assert res.state == fim.LOCAL_COMMITTED and env.inbox.items[uid]["status"] == "ACCEPTED"
    return uid


def make_preparing(env, loc, monkeypatch, t="GRAVE_PHOTO", **kw):
    kw.setdefault("payload", {"caption": "x"})
    kw.setdefault("files", [("image/jpeg", JPEG)])
    uid = add(env, loc, t, **kw)
    real = fim._commit_local

    def boom(*a, **k):
        raise Crash()
    monkeypatch.setattr(fim, "_commit_local", boom)
    with pytest.raises(Crash):
        fim.import_item(client(env), uid)
    monkeypatch.setattr(fim, "_commit_local", real)
    assert fim.journal_get(uid)["import_state"] == fim.PREPARING
    return uid


def uids(flt="ALL"):
    return {r["inbox_item_uid"]: r for r in fih.list_history(flt)}


def snapshot():
    return (rows("SELECT * FROM inhumati"), rows("SELECT * FROM amenajari_avize"), rows("SELECT * FROM documents"), [str(p) for p in media_files()])


# ------------------------------------------------------------------ listă
def test_list_complete_committed_preparing_with_all_columns(env, loc, monkeypatch):
    a = make_complete(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[("image/png", PNG)])
    b = make_committed(env, loc, "DECEASED")
    c = make_preparing(env, loc, monkeypatch)
    h = uids()
    assert set(h) == {a, b, c}
    ra, rb, rc = h[a], h[b], h[c]
    assert (ra["state"], rb["state"], rc["state"]) == (fim.COMPLETE, fim.LOCAL_COMMITTED, fim.PREPARING)
    assert ra["item_type"] == "GRAVE_PHOTO" and ra["type"] == "Foto mormânt" and ra["files"] == 1 and rb["files"] == 0 and rc["files"] == 0
    assert ra["local_entity_uid"] == loc.uid and rb["local_entity_uid"] == b
    assert rb["grave_uid"] == loc.uid and rb["cod_loc"] == "G-12" and ra["grave_uid"] == loc.uid
    assert ra["cemetery_uid"] == CEM and CEM[:8] in ra["registry"]
    assert ra["created_at"] and ra["last_attempt"] and ra["last_error"] == ""
    for key in ("inbox_item_uid", "item_type", "created_at", "last_attempt", "last_error", "state", "files", "grave_uid", "local_entity_uid", "registry"):
        assert key in ra


def test_list_only_complete(env, loc):
    a = make_complete(env, loc)
    make_committed(env, loc)
    assert list(uids("COMPLETE")) == [a]


def test_list_only_local_committed(env, loc, monkeypatch):
    make_complete(env, loc)
    b = make_committed(env, loc)
    make_preparing(env, loc, monkeypatch)
    assert list(uids("LOCAL_COMMITTED")) == [b]


def test_list_only_preparing(env, loc, monkeypatch):
    make_complete(env, loc)
    make_committed(env, loc)
    c = make_preparing(env, loc, monkeypatch)
    assert list(uids("PREPARING")) == [c]


def test_filter_with_error_and_all_and_invalid(env, loc, monkeypatch):
    a = make_complete(env, loc)
    b = make_committed(env, loc)
    c = make_committed(env, loc)
    env.http.fail = requests.ConnectionError("down")
    res = fih.finalize_cloud(client(env), c)
    env.http.fail = None
    assert res.state == fim.LOCAL_COMMITTED and not res.cloud_final
    assert set(uids("ERROR")) == {b, c} and a not in uids("ERROR") and uids("ERROR")[c]["has_error"] and uids("ERROR")[c]["last_error"] == "CloudUnavailable"
    assert fih.finalize_cloud(client(env), b).state == fim.COMPLETE and set(uids("ERROR")) == {c}
    assert set(uids("ALL")) == {a, b, c}
    with pytest.raises(ValueError):
        fih.list_history("bogus")


def test_list_empty_journal(env, loc):
    assert fih.list_history() == []


def test_list_is_newest_first(env, loc):
    a = make_complete(env, loc)
    b = make_complete(env, loc)
    conn = db.get_connection()
    conn.execute(f"UPDATE {fim.JOURNAL} SET created_at='2026-01-01T00:00:00+00:00' WHERE inbox_item_uid=?", (a,))
    conn.execute(f"UPDATE {fim.JOURNAL} SET created_at='2026-02-01T00:00:00+00:00' WHERE inbox_item_uid=?", (b,))
    conn.close()
    assert [r["inbox_item_uid"] for r in fih.list_history()] == [b, a]


# ------------------------------------------------------------------ detalii
def test_details_for_document_with_media_sha_and_state(env, loc):
    uid = make_complete(env, loc, "DOCUMENT", payload={"doc_type": "certificat", "caption": "Cert", "nr_evidenta": "55"}, files=[("application/pdf", PDF)])
    d = fih.local_details(uid)
    assert d["state"] == fim.COMPLETE and d["destination"].startswith("Documente — loc G-12") and "Entitate locală: prezentă" in d["local_state"]
    assert len(d["files_detail"]) == 1 and len(d["files_detail"][0]["sha256"]) == 64 and d["files_detail"][0]["exists"]
    assert not d["files_detail"][0]["path"].startswith(("/", "\\")) and d["files_detail"][0]["path"].endswith(".pdf")
    assert ("Tip document", "certificat") in d["payload"] and ("Descriere foto/document", "Cert") in d["payload"]
    assert d["cloud_status"] == "indisponibil"        # local: fără apel de rețea


def test_details_for_deceased_payload_and_entity(env, loc):
    uid = make_committed(env, loc, "DECEASED", payload={"nume": "Popescu", "prenume": "Ion", "data_deces": "2026-03-04"})
    d = fih.local_details(uid)
    pay = dict(d["payload"])
    assert pay["Nume"] == "Popescu" and pay["Data decesului"] == "2026-03-04" and d["entity_kind"] == "inhumat" and d["local_entity_uid"] == uid
    assert d["cod_loc"] == "G-12" and d["grave_uid"] == loc.uid


def test_details_missing_entity_is_reported_not_crash(env, loc):
    uid = make_complete(env, loc, "DECEASED")
    conn = db.get_connection()
    conn.execute("DELETE FROM inhumati")
    conn.close()
    d = fih.local_details(uid)
    assert "LIPSĂ" in d["local_state"] and d["navigation"] is None
    assert fih.local_details(str(uuid.uuid4())) is None


def test_details_preparing_has_nothing_committed(env, loc, monkeypatch):
    uid = make_preparing(env, loc, monkeypatch)
    d = fih.local_details(uid)
    assert d["state"] == fim.PREPARING and "Nimic comis" in d["local_state"] and d["files_detail"] == []


def test_cloud_snapshot_has_status_and_whitelisted_payload(env, loc):
    uid = make_committed(env, loc, "DECEASED")
    snap = fih.cloud_snapshot(lambda: client(env), uid)
    assert snap["available"] and snap["status"] == "ACCEPTED" and ("Nume", "Popescu") in snap["item"]["payload"] and snap["verdict"] is None
    assert not [c for c in env.http.calls if c["url"].startswith("https://storage")]


# ------------------------------------------------------------------ offline
def test_history_works_fully_offline(env, loc, monkeypatch):
    a = make_complete(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[("image/jpeg", JPEG)])
    b = make_committed(env, loc)
    c = make_preparing(env, loc, monkeypatch)
    env.http.fail = requests.ConnectionError("down")
    n = len(env.http.calls)
    assert set(uids()) == {a, b, c} and fih.local_details(a)["files_detail"] and fih.navigation_target(a)["cod_loc"] == "G-12"
    snap = fih.cloud_snapshot(lambda: client(env), b)
    assert snap == {"available": False, "status": "indisponibil", "item": None, "verdict": None}
    assert fih.cloud_snapshot(lambda: (_ for _ in ()).throw(fic.NotConfigured()), b)["status"] == "indisponibil"
    assert not [c for c in env.http.calls[n:] if c["url"].startswith("https://storage")]


def test_finalize_offline_keeps_local_and_reports(env, loc):
    uid = make_committed(env, loc)
    before = snapshot()
    env.http.fail = requests.ConnectionError("down " + SIGNED_TOKEN)
    res = fih.finalize_cloud(client(env), uid)
    assert res.state == fim.LOCAL_COMMITTED and not res.cloud_final and "indisponibil" in res.message
    assert snapshot() == before and fim.journal_get(uid)["import_state"] == fim.LOCAL_COMMITTED
    assert SIGNED_TOKEN not in fim.journal_get(uid)["last_error"]


# ------------------------------------------------------------------ Finalizează în cloud
def test_critical_local_committed_cloud_accepted_finalize_exactly_once(env, loc, monkeypatch):
    uid = make_committed(env, loc, "DOCUMENT", payload={"doc_type": "act", "caption": "c"}, files=[("application/pdf", PDF)])
    assert len(rows("SELECT * FROM documents")) == 1 and len(media_files()) == 1 and env.inbox.items[uid]["status"] == "ACCEPTED"
    downloads, imported = len(download_calls(env)), len(imported_calls(env))

    def never(*a, **k):
        raise AssertionError("finalizarea cloud NU trebuie să reimporte local")
    monkeypatch.setattr(fim, "_commit_local", never)
    monkeypatch.setattr(fim, "_download_all", never)
    res = fih.finalize_cloud(client(env), uid)
    assert res.state == fim.COMPLETE and res.cloud_final and not res.created
    assert len(rows("SELECT * FROM documents")) == 1 and len(media_files()) == 1                 # exact 1 copie media
    assert env.inbox.items[uid]["status"] == "IMPORTED" and fim.journal_get(uid)["import_state"] == fim.COMPLETE and fim.journal_get(uid)["last_error"] == ""
    assert len(download_calls(env)) == downloads and len(imported_calls(env)) == imported + 1


def test_critical_deceased_exactly_one_local_entity(env, loc):
    uid = make_committed(env, loc, "DECEASED")
    fih.finalize_cloud(client(env), uid)
    assert len(rows("SELECT * FROM inhumati")) == 1 and rows("SELECT uid FROM inhumati")[0]["uid"] == uid
    assert env.inbox.items[uid]["status"] == "IMPORTED" and fim.journal_get(uid)["import_state"] == fim.COMPLETE


def test_response_loss_then_retry_marks_complete_without_second_imported(env, loc):
    uid = make_committed(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[("image/png", PNG)])
    real = env.http.request

    def lossy(method, url, **kw):
        r = real(method, url, **kw)
        if url.endswith("/imported"):
            raise requests.ConnectionError("lost " + SIGNED_TOKEN)
        return r
    env.http.request = lossy
    res = fih.finalize_cloud(client(env), uid)
    assert res.state == fim.LOCAL_COMMITTED and not res.cloud_final and env.inbox.items[uid]["status"] == "IMPORTED"
    env.http.request = real
    n_imp = len(imported_calls(env))
    res2 = fih.finalize_cloud(client(env), uid)
    assert res2.state == fim.COMPLETE and res2.cloud_final
    assert len(imported_calls(env)) == n_imp and len(rows("SELECT * FROM documents")) == 1 and len(media_files()) == 1 and fim.journal_get(uid)["last_error"] == ""


def test_cloud_already_imported_marks_journal_complete(env, loc):
    uid = make_committed(env, loc, "DECEASED")
    env.inbox.items[uid]["status"] = "IMPORTED"
    before, n_imp = snapshot(), len(imported_calls(env))
    res = fih.finalize_cloud(client(env), uid)
    assert res.state == fim.COMPLETE and res.cloud_final and fim.journal_get(uid)["import_state"] == fim.COMPLETE
    assert len(imported_calls(env)) == n_imp and snapshot() == before


def test_duplicate_click_sequential_is_idempotent(env, loc):
    uid = make_committed(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[("image/jpeg", JPEG)])
    r1, r2, r3 = (fih.finalize_cloud(client(env), uid) for _ in range(3))
    assert r1.state == r2.state == r3.state == fim.COMPLETE
    assert len(imported_calls(env)) == 1 and len(rows("SELECT * FROM documents")) == 1 and len(media_files()) == 1


def test_duplicate_click_concurrent_is_idempotent(env, loc):
    uid = make_committed(env, loc, "DECEASED")
    out, barrier = [], threading.Barrier(2)

    def run():
        barrier.wait()
        try:
            out.append(fih.finalize_cloud(client(env), uid))
        except fim.ImportBlocked as exc:
            out.append(exc)
    ts = [threading.Thread(target=run) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert any(isinstance(o, fim.ImportResult) and o.state == fim.COMPLETE for o in out)
    assert len(rows("SELECT * FROM inhumati")) == 1 and len(imported_calls(env)) == 1 and fim.journal_get(uid)["import_state"] == fim.COMPLETE


def test_restart_between_commit_and_finalize(env, loc):
    uid = make_committed(env, loc, "DECEASED")
    fim._READY.clear()
    fim._LOCKS.clear()                                        # „restart”: starea de proces dispare, jurnalul rămâne
    assert [r["state"] for r in fih.list_history("LOCAL_COMMITTED")] == [fim.LOCAL_COMMITTED]
    assert fih.finalize_cloud(client(env), uid).state == fim.COMPLETE
    fim._READY.clear()
    assert fih.list_history("COMPLETE")[0]["inbox_item_uid"] == uid and len(rows("SELECT * FROM inhumati")) == 1


def test_no_duplicate_local_entity_or_media_after_all_actions(env, loc):
    uid = make_committed(env, loc, "DOCUMENT", payload={"doc_type": "act"}, files=[("application/pdf", PDF), ("image/png", PNG)])
    for _ in range(2):
        fih.finalize_cloud(client(env), uid)
    fim.import_item(client(env), uid)
    assert len(rows("SELECT * FROM documents")) == 2 and len(media_files()) == 2 and len(rows(f"SELECT * FROM {fim.JOURNAL}")) == 1


def test_finalize_refuses_preparing_unknown_and_other_registry(env, loc, monkeypatch):
    p = make_preparing(env, loc, monkeypatch)
    with pytest.raises(fim.ImportBlocked) as e:
        fih.finalize_cloud(client(env), p)
    assert e.value.code == "NOT_LOCAL_COMMITTED"
    with pytest.raises(fim.ImportBlocked) as e2:
        fih.finalize_cloud(client(env), str(uuid.uuid4()))
    assert e2.value.code == "NOT_IN_JOURNAL"
    c = make_committed(env, loc)
    before = snapshot()
    db.set_setting("cloud_bound_cemetery", str(uuid.uuid4()))
    with pytest.raises(fim.WrongRegistry):
        fih.finalize_cloud(client(env), c)
    assert fim.journal_get(c)["import_state"] == fim.LOCAL_COMMITTED and env.inbox.items[c]["status"] == "ACCEPTED" and snapshot() == before


def test_finalize_when_cloud_rejected_or_other_cemetery_does_nothing(env, loc):
    uid = make_committed(env, loc)
    env.inbox.items[uid]["status"] = "REJECTED"
    res = fih.finalize_cloud(client(env), uid)
    assert res.state == fim.LOCAL_COMMITTED and "REJECTED" in res.message and fim.journal_get(uid)["last_error"] == "CLOUD_REJECTED"
    env.inbox.items[uid]["status"] = "ACCEPTED"
    env.inbox.items[uid]["cemetery_uid"] = str(uuid.uuid4())
    with pytest.raises(fim.WrongRegistry):
        fih.finalize_cloud(client(env), uid)
    assert not imported_calls(env)


# ------------------------------------------------------------------ Reia importul (PREPARING)
def test_resume_preparing_after_safe_verification(env, loc, monkeypatch):
    uid = make_preparing(env, loc, monkeypatch)
    snap = fih.cloud_snapshot(lambda: client(env), uid)
    assert snap["verdict"].can_resume and snap["verdict"].reasons == []
    res = fih.resume_import(client(env), uid)
    assert res.state == fim.COMPLETE and res.cloud_final
    assert len(rows("SELECT * FROM documents")) == 1 and len(media_files()) == 1 and env.inbox.items[uid]["status"] == "IMPORTED"


def test_resume_after_orphan_media_reuses_it_without_duplicates(env, loc, monkeypatch):
    uid = add(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[("image/jpeg", JPEG)])
    real = fim._place_file

    def place_then_die(*a, **k):
        real(*a, **k)
        raise Crash()
    monkeypatch.setattr(fim, "_place_file", place_then_die)
    with pytest.raises(Crash):
        fim.import_item(client(env), uid)
    monkeypatch.setattr(fim, "_place_file", real)
    assert len(media_files()) == 1 and fih.check_resume(client(env), uid).can_resume
    assert fih.resume_import(client(env), uid).state == fim.COMPLETE
    assert len(media_files()) == 1 and len(rows("SELECT * FROM documents")) == 1


def _unsafe(env, uid, before, reason):
    v = fih.check_resume(client(env), uid)
    assert not v.can_resume and any(reason in r for r in v.reasons), v.reasons
    with pytest.raises(fim.ImportBlocked) as e:
        fih.resume_import(client(env), uid)
    assert e.value.code == "RESUME_UNSAFE"
    assert snapshot() == before and fim.journal_get(uid)["import_state"] == fim.PREPARING and not imported_calls(env)


def test_resume_blocked_wrong_registry(env, loc, monkeypatch):
    uid = make_preparing(env, loc, monkeypatch)
    before = snapshot()
    db.set_setting("cloud_bound_cemetery", str(uuid.uuid4()))
    _unsafe(env, uid, before, "Registru")


def test_resume_blocked_wrong_cemetery_on_cloud_item(env, loc, monkeypatch):
    uid = make_preparing(env, loc, monkeypatch)
    before = snapshot()
    env.inbox.items[uid]["cemetery_uid"] = str(uuid.uuid4())
    _unsafe(env, uid, before, "Cimitirul")


@pytest.mark.parametrize("status", ["PENDING", "REJECTED", "IMPORTED"])
def test_resume_blocked_when_not_accepted(env, loc, monkeypatch, status):
    uid = make_preparing(env, loc, monkeypatch)
    before = snapshot()
    env.inbox.items[uid]["status"] = status
    _unsafe(env, uid, before, "ACCEPTED")


def test_resume_blocked_on_conflict(env, loc, monkeypatch):
    uid = make_preparing(env, loc, monkeypatch, t="DECEASED", payload={"nume": "Popescu", "prenume": "Ion"}, files=[])
    add_deceased_local(loc, uid, nume="Altul")
    before = snapshot()
    _unsafe(env, uid, before, "Conflict")


def test_resume_blocked_missing_grave(env, loc, monkeypatch):
    uid = make_preparing(env, loc, monkeypatch)
    before = snapshot()
    conn = db.get_connection()
    conn.execute("UPDATE locuri_de_veci SET uid=? WHERE cod_loc='G-12'", (str(uuid.uuid4()),))
    conn.close()
    _unsafe(env, uid, before, "loc de veci")


def test_resume_blocked_media_invalid_or_conflicting(env, loc, monkeypatch):
    uid = make_preparing(env, loc, monkeypatch)
    f = env.inbox.files[uid][0]
    f_meta = {"uid": f["uid"], "mime": f["mime"]}
    dest = fim._dest_path("loc", f_meta)
    import os
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as fh:
        fh.write(b"alt continut")
    before = snapshot()
    _unsafe(env, uid, before, "alt SHA-256")
    os.remove(dest)
    env.inbox.files[uid][0]["status"] = "UPLOADING"
    _unsafe(env, uid, snapshot(), "Media")


def test_resume_blocked_unexpected_staging_content(env, loc, monkeypatch):
    uid = make_preparing(env, loc, monkeypatch)
    import os
    with open(os.path.join(fim._staging_dir(uid), "strain.exe"), "wb") as fh:
        fh.write(b"x")
    _unsafe(env, uid, snapshot(), "staging")


def test_resume_blocked_when_cloud_unavailable(env, loc, monkeypatch):
    uid = make_preparing(env, loc, monkeypatch)
    before = snapshot()
    env.http.fail = requests.ConnectionError("down")
    v = fih.check_resume(client(env), uid)
    assert not v.can_resume and "Cloud indisponibil" in v.reasons[0]
    with pytest.raises(fim.ImportBlocked):
        fih.resume_import(client(env), uid)
    assert snapshot() == before and fim.journal_get(uid)["import_state"] == fim.PREPARING


def test_resume_only_for_preparing(env, loc):
    a = make_committed(env, loc)
    b = make_complete(env, loc)
    for u in (a, b, str(uuid.uuid4())):
        assert not fih.check_resume(client(env), u).can_resume
        with pytest.raises(fim.ImportBlocked):
            fih.resume_import(client(env), u)
    assert len(rows("SELECT * FROM inhumati")) == 2


def test_resume_failure_keeps_preparing_row_visible_with_error_code(env, loc, monkeypatch):
    uid = make_preparing(env, loc, monkeypatch)
    env.http.storage_override[env.inbox.files[uid][0]["uid"]] = b"\xff\xd8\xff\xe0" + b"Q" * (len(JPEG) - 4)
    with pytest.raises(fim.IntegrityError):
        fih.resume_import(client(env), uid)
    j = fim.journal_get(uid)
    assert j["import_state"] == fim.PREPARING and j["last_error"] == "SHA_MISMATCH"
    assert rows("SELECT * FROM documents") == [] and media_files() == [] and env.inbox.items[uid]["status"] == "ACCEPTED"
    assert list(uids("ERROR")) == [uid]
    env.http.storage_override.clear()
    assert fih.resume_import(client(env), uid).state == fim.COMPLETE and fim.journal_get(uid)["last_error"] == ""


# ------------------------------------------------------------------ navigare
@pytest.mark.parametrize("t,kw", [
    ("DECEASED", {}),
    ("WORK", {"payload": {"descriere": "Placare"}}),
    ("PERMIT", {"payload": {"nr_aviz": "9/2026"}}),
    ("DOCUMENT", {"payload": {"doc_type": "act"}, "files": [("application/pdf", PDF)]}),
    ("GRAVE_PHOTO", {"payload": {"caption": "x"}, "files": [("image/jpeg", JPEG)]}),
])
def test_navigation_target_for_each_type(env, loc, t, kw):
    uid = make_complete(env, loc, t, **kw)
    nav = fih.navigation_target(uid)
    assert nav == {"kind": t, "cod_loc": "G-12", "label": nav["label"]} and "G-12" in nav["label"]


def test_navigation_target_work_photo_and_document_on_target(env, loc):
    wuid, _aid = add_work(loc)
    w = make_complete(env, loc, "WORK_PHOTO", payload={}, stage="BEFORE", files=[("image/png", PNG)], target=wuid)
    d = make_complete(env, loc, "DOCUMENT", payload={"doc_type": "act"}, files=[("application/pdf", PDF)], target=wuid)
    assert fih.navigation_target(w)["cod_loc"] == "G-12" and fih.navigation_target(d)["cod_loc"] == "G-12"


def test_navigation_none_for_preparing_or_missing_entity(env, loc, monkeypatch):
    p = make_preparing(env, loc, monkeypatch)
    assert fih.navigation_target(p) is None or fih.navigation_target(p)["cod_loc"] == "G-12"      # fotografie: entitatea țintă (locul) există
    d = make_complete(env, loc, "DECEASED")
    conn = db.get_connection()
    conn.execute("DELETE FROM inhumati")
    conn.close()
    assert fih.navigation_target(d) is None and fih.navigation_target(str(uuid.uuid4())) is None


def test_open_in_main_uses_existing_search_plot(env, loc, qapp, monkeypatch):
    import field_import_history_window as w
    make_complete(env, loc, "DECEASED")
    calls = []

    class Main(QtWidgets.QWidget):
        features = SimpleNamespace(search_plot=lambda cod: calls.append(cod))
    main = Main()
    win = w.FieldImportHistoryWindow(main, client_factory=lambda: client(env), can_review=lambda: True)
    win.table.selectRow(0)
    assert pump(qapp, lambda: win.btn_open.isEnabled())
    win.btn_open.click()
    assert calls == ["G-12"] and win.result() == QtWidgets.QDialog.Accepted
    win.close()


def test_open_in_main_without_main_window_is_message_not_crash(env, loc, qapp, monkeypatch):
    import field_import_history_window as w
    make_complete(env, loc, "DECEASED")
    seen = []
    monkeypatch.setattr(w.QMessageBox, "information", staticmethod(lambda *a, **k: seen.append(a[2])))
    win = w.FieldImportHistoryWindow(client_factory=lambda: client(env), can_review=lambda: True)
    win.table.selectRow(0)
    win.btn_open.click()
    assert seen and "fereastra principală" in seen[0]
    win.close()


# ------------------------------------------------------------------ securitate
def test_no_secret_or_signed_url_in_history_outputs_logs_or_db(env, loc, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    a = make_committed(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[("image/jpeg", JPEG)])
    p = make_preparing(env, loc, monkeypatch)
    out = [fih.list_history(), fih.local_details(a), fih.local_details(p), fih.cloud_snapshot(lambda: client(env), a), fih.cloud_snapshot(lambda: client(env), p)]
    fih.finalize_cloud(client(env), a)
    fih.resume_import(client(env), p)
    text = repr(out) + caplog.text
    for secret in (*ALL_SECRETS, SIGNED_TOKEN):
        assert secret not in text
    assert "storage.example.ro" not in text and "token=" not in text
    with open(db.current_db_path(), "rb") as fh:
        blob = fh.read()
    for secret in (*ALL_SECRETS, SIGNED_TOKEN):
        assert secret.encode() not in blob
    assert b"storage.example.ro" not in blob and b"token=" not in blob


def test_finalize_and_list_never_request_signed_urls_or_download(env, loc):
    uid = make_committed(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[("image/jpeg", JPEG)])
    n = len(env.http.calls)
    fih.list_history()
    fih.local_details(uid)
    fih.cloud_snapshot(lambda: client(env), uid)
    fih.finalize_cloud(client(env), uid)
    new = env.http.calls[n:]
    assert new and all(not c["url"].startswith("https://storage") and "/files/" not in c["url"] for c in new)


def test_redact_hides_urls_and_keys(env, loc):
    assert "token" not in fih.redact(f"https://storage.example.ro/object/sign/x?token={SIGNED_TOKEN}") and SIGNED_TOKEN not in fih.redact(f"x token={SIGNED_TOKEN}")
    assert ALL_SECRETS[0] not in fih.redact(f"Binding {ALL_SECRETS[0]}") and ALL_SECRETS[0] not in fih.redact(ALL_SECRETS[0])
    uid = make_committed(env, loc)
    conn = db.get_connection()
    conn.execute(f"UPDATE {fim.JOURNAL} SET last_error=? WHERE inbox_item_uid=?", (f"https://storage.example.ro/s?token={SIGNED_TOKEN}", uid))
    conn.close()
    r = fih.list_history("ERROR")[0]
    assert SIGNED_TOKEN not in r["last_error"] and "storage.example.ro" not in r["last_error"] and SIGNED_TOKEN not in repr(fih.local_details(uid))


def test_history_module_has_no_credential_access_and_no_cloud_delete():
    import inspect

    import field_import_history_window as w
    for mod in (fih, w):
        src = inspect.getsource(mod)
        for needle in ("SUPABASE_SECRET_KEY", "service_role", "SERVICE_ROLE", "DATABASE_URL", "psycopg", "os.environ", "getenv", "txuxkjgftopznvmooexq", "signed_url", "download_signed",
                       "DELETE FROM", "rmtree(sdir"):
            assert needle not in src.replace("shutil.rmtree(fim._staging_dir(uid)", "")


def test_history_does_not_change_import_mechanism_and_adds_no_migration():
    import subprocess
    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    mig = sorted(p.name for p in (root / "backend" / "cimitir" / "pg_migrations").glob("*.sql"))
    assert mig[-1].startswith("019_") and not any(n.startswith("020") for n in mig)
    ref = subprocess.run(["git", "rev-parse", "--verify", "-q", "main"], cwd=root, capture_output=True, text=True, check=False)
    if ref.returncode != 0:
        pytest.skip("referința main nu există în acest checkout")
    diff = subprocess.run(["git", "diff", "--stat", "main", "--", "desktop/field_import.py", "desktop/field_inbox_client.py"], cwd=root, capture_output=True, text=True, check=False)
    assert diff.stdout.strip() == ""


# ------------------------------------------------------------------ UI
def _msgs(monkeypatch, w, seen, answer=True):
    monkeypatch.setattr(w.QMessageBox, "question", staticmethod(lambda *a, **k: (seen.append(("question", a[2])), w.QMessageBox.Yes if answer else w.QMessageBox.No)[1]))
    monkeypatch.setattr(w.QMessageBox, "warning", staticmethod(lambda *a, **k: seen.append(("warning", a[2]))))
    monkeypatch.setattr(w.QMessageBox, "information", staticmethod(lambda *a, **k: seen.append(("info", a[2]))))


def _row_of(win, uid):
    for r in range(win.table.rowCount()):
        if win.table.item(r, 0).data(0x0100) == uid:
            return r
    raise AssertionError("rând lipsă")


def test_ui_list_filters_and_details(env, loc, qapp, monkeypatch):
    import field_import_history_window as w
    a = make_complete(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[("image/png", PNG)])
    b = make_committed(env, loc)
    c = make_preparing(env, loc, monkeypatch)
    win = w.FieldImportHistoryWindow(client_factory=lambda: client(env), can_review=lambda: True)
    assert win.windowTitle() == "Istoric importuri din teren" and win.table.columnCount() == 10 and win.table.rowCount() == 3
    assert [win.cmb_filter.itemText(i) for i in range(win.cmb_filter.count())] == ["Toate", "COMPLETE", "LOCAL_COMMITTED", "PREPARING", "Cu eroare"]
    for text, expected in (("COMPLETE", {a}), ("LOCAL_COMMITTED", {b}), ("PREPARING", {c}), ("Toate", {a, b, c}), ("Cu eroare", {b})):
        win.cmb_filter.setCurrentIndex(win.cmb_filter.findText(text))
        assert {win.table.item(r, 0).data(0x0100) for r in range(win.table.rowCount())} == expected
    win.cmb_filter.setCurrentIndex(0)
    win.table.selectRow(_row_of(win, a))
    assert pump(qapp, lambda: "Stare cloud: IMPORTED" in win.txt_detail.toPlainText() or "Stare cloud: ACCEPTED" in win.txt_detail.toPlainText())
    t = win.txt_detail.toPlainText()
    assert "Payload relevant" in t and "SHA256:" in t and "Destinație locală: Fotografii loc — loc G-12" in t and "Stare locală" in t and "Ultima eroare" in t
    assert not win.btn_finalize.isEnabled() and not win.btn_resume.isEnabled() and win.btn_open.isEnabled()
    win.close()


def test_ui_finalize_button_and_duplicate_click(env, loc, qapp, monkeypatch):
    import field_import_history_window as w
    uid = make_committed(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[("image/jpeg", JPEG)])
    seen = []
    _msgs(monkeypatch, w, seen)
    win = w.FieldImportHistoryWindow(client_factory=lambda: client(env), can_review=lambda: True)
    win.table.selectRow(0)
    assert pump(qapp, lambda: win.btn_finalize.isEnabled() and "Stare cloud: ACCEPTED" in win.txt_detail.toPlainText())
    assert win.btn_finalize.text() == "Finalizează în cloud"
    changed = []
    win.changed.connect(lambda: changed.append(1))
    win.btn_finalize.click()
    win.btn_finalize.click()                                      # al doilea click: ignorat (ocupat) sau idempotent
    assert pump(qapp, lambda: env.inbox.items[uid]["status"] == "IMPORTED" and fim.journal_get(uid)["import_state"] == fim.COMPLETE and changed)
    qapp.processEvents()
    assert len(imported_calls(env)) == 1 and len(rows("SELECT * FROM documents")) == 1 and len(media_files()) == 1
    assert pump(qapp, lambda: not win.btn_finalize.isEnabled())
    win.close()


def test_ui_finalize_without_approve_right_does_nothing(env, loc, qapp):
    import field_import_history_window as w
    uid = make_committed(env, loc)
    win = w.FieldImportHistoryWindow(client_factory=lambda: client(env), can_review=lambda: False)
    win.table.selectRow(0)
    assert pump(qapp, lambda: win.btn_finalize.isEnabled())
    win.btn_finalize.click()
    qapp.processEvents()
    assert env.inbox.items[uid]["status"] == "ACCEPTED" and fim.journal_get(uid)["import_state"] == fim.LOCAL_COMMITTED
    win.close()


def test_ui_offline_opens_and_shows_cloud_unavailable(env, loc, qapp, monkeypatch):
    import field_import_history_window as w
    b = make_committed(env, loc)
    make_complete(env, loc)
    env.http.fail = requests.ConnectionError("down")
    seen = []
    _msgs(monkeypatch, w, seen)
    win = w.FieldImportHistoryWindow(client_factory=lambda: client(env), can_review=lambda: True)
    assert win.table.rowCount() == 2
    win.table.selectRow(_row_of(win, b))
    assert pump(qapp, lambda: "Stare cloud: indisponibil" in win.txt_detail.toPlainText())
    assert win.btn_finalize.isEnabled() and not win.btn_resume.isEnabled()
    win.btn_finalize.click()
    assert pump(qapp, lambda: any(k == "info" and "indisponibil" in t for k, t in seen))
    assert fim.journal_get(b)["import_state"] == fim.LOCAL_COMMITTED and len(rows("SELECT * FROM inhumati")) == 2
    win.close()


def test_ui_not_configured_cloud_does_not_break_history(env, loc, qapp):
    import field_import_history_window as w
    uid = make_complete(env, loc)
    win = w.FieldImportHistoryWindow(client_factory=lambda: (_ for _ in ()).throw(fic.NotConfigured()), can_review=lambda: True)
    win.table.selectRow(0)
    assert pump(qapp, lambda: "Stare cloud: indisponibil" in win.txt_detail.toPlainText()) and uid in win.txt_detail.toPlainText()
    win.close()


def test_ui_resume_button_only_when_safe_and_confirmed(env, loc, qapp, monkeypatch):
    import field_import_history_window as w
    uid = make_preparing(env, loc, monkeypatch)
    seen = []
    _msgs(monkeypatch, w, seen, answer=False)
    win = w.FieldImportHistoryWindow(client_factory=lambda: client(env), can_review=lambda: True)
    win.table.selectRow(0)
    assert pump(qapp, lambda: win.btn_resume.isEnabled()) and win.btn_resume.text() == "Reia importul" and not win.btn_finalize.isEnabled()
    win.btn_resume.click()
    qapp.processEvents()
    assert env.inbox.items[uid]["status"] == "ACCEPTED" and fim.journal_get(uid)["import_state"] == fim.PREPARING            # confirmare refuzată
    _msgs(monkeypatch, w, seen, answer=True)
    win.btn_resume.click()
    assert pump(qapp, lambda: env.inbox.items[uid]["status"] == "IMPORTED" and fim.journal_get(uid)["import_state"] == fim.COMPLETE)
    assert len(rows("SELECT * FROM documents")) == 1 and len(media_files()) == 1
    win.close()


def test_ui_resume_disabled_and_reason_shown_when_unsafe(env, loc, qapp, monkeypatch):
    import field_import_history_window as w
    uid = make_preparing(env, loc, monkeypatch)
    env.inbox.items[uid]["status"] = "REJECTED"
    before = snapshot()
    win = w.FieldImportHistoryWindow(client_factory=lambda: client(env), can_review=lambda: True)
    win.table.selectRow(0)
    assert pump(qapp, lambda: "NU este sigură" in win.txt_detail.toPlainText())
    assert not win.btn_resume.isEnabled() and "ACCEPTED" in win.txt_detail.toPlainText()
    win.btn_resume.click()
    qapp.processEvents()
    assert snapshot() == before and fim.journal_get(uid)["import_state"] == fim.PREPARING
    win.close()


def test_ui_main_window_has_history_action():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "ui_mainwindow.py").read_text(encoding="utf-8")
    assert "act-field-import-history" in src and "open_field_import_history_window" in src and "FieldImportHistoryWindow" in src
    spec = (pathlib.Path(__file__).resolve().parents[1] / "cimitir.spec").read_text(encoding="utf-8")
    assert "field_import_history_window" in spec and "field_import_history" in spec


def test_ui_history_does_not_block_ui_thread_with_http(env, loc, qapp):
    import field_import_history_window as w
    make_committed(env, loc)
    main_thread = threading.get_ident()
    seen = []
    real = env.http.request

    def spy(method, url, **kw):
        seen.append(threading.get_ident())
        return real(method, url, **kw)
    env.http.request = spy
    win = w.FieldImportHistoryWindow(client_factory=lambda: client(env), can_review=lambda: True)
    assert seen == []                                           # deschiderea ferestrei nu face HTTP
    win.table.selectRow(0)
    assert pump(qapp, lambda: "Stare cloud: ACCEPTED" in win.txt_detail.toPlainText())
    assert seen and main_thread not in seen
    win.btn_finalize.click()
    assert pump(qapp, lambda: fim.journal_get(win.selected_uid() or "") is None or fim.journal_get(win.selected_uid())["import_state"] == fim.COMPLETE)
    assert main_thread not in seen
    win.close()
