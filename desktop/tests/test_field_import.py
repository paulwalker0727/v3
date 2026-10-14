# ruff: noqa: F811
"""MAIN: import LOCAL din Field Inbox (ACCEPTED -> SQLite + media -> IMPORTED în cloud). Fără rețea reală: routerele REALE de Field Inbox rulează in-process
(vezi test_field_inbox_main.py); SQLite și media sunt locale (tmp). LIVE (Supabase TEST): nerulat aici. PRODUCTION: blocat."""
from __future__ import annotations

import hashlib
import logging
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from test_field_inbox_main import (  # noqa: F401,I001  (fixture-uri + simulatoare partajate; setează și căile desktop/shared)
    ALL_SECRETS, CEM, JPEG, PDF, PNG, SIGNED_TOKEN, _png_bytes, client, env, make_profile, pump, qapp,
)
import database as db  # noqa: E402
import field_import as fim  # noqa: E402
import field_inbox_client as fic  # noqa: E402

GRAVE_UID = str(uuid.uuid4())


class Crash(BaseException):
    """Simulează o cădere de proces (nu este prinsă de `except Exception`)."""


@pytest.fixture()
def loc(env, tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", str(tmp_path))
    import cimitir_registry
    monkeypatch.setattr(cimitir_registry, "current_binding", lambda: None)
    make_profile(env)
    fim.ensure_tables()
    db.set_setting("cloud_bound_cemetery", CEM)
    conn = db.get_connection()
    conn.execute("INSERT INTO locuri_de_veci (cod_loc, uid) VALUES (?, ?)", ("G-12", GRAVE_UID))
    gid = conn.execute("SELECT id FROM locuri_de_veci WHERE cod_loc='G-12'").fetchone()["id"]
    conn.close()
    return SimpleNamespace(uid=GRAVE_UID, cod="G-12", id=gid, root=tmp_path)


def add(env, loc, t="DECEASED", status="ACCEPTED", payload=None, stage=None, files=(), grave=True, target=None, cemetery=None):
    uid = env.inbox.add_item(t, status=status, payload=payload or {"nume": "Popescu", "prenume": "Ion"}, stage=stage, files=files, grave=loc.uid if grave else None)
    env.inbox.items[uid]["target_uid"] = target
    if cemetery:
        env.inbox.items[uid]["cemetery_uid"] = cemetery
    return uid


def rows(sql, *a):
    conn = db.get_connection()
    try:
        return [dict(r) for r in conn.execute(sql, a).fetchall()]
    finally:
        conn.close()


def media_files():
    return sorted(p for p in Path(db.media_root()).rglob("*") if p.is_file()) if Path(db.media_root()).exists() else []


def imported_calls(env):
    return [c for c in env.http.calls if c["url"].endswith("/imported")]


def download_calls(env):
    return [c for c in env.http.calls if c["url"].startswith("https://storage.example.ro/")]


def add_work(loc, uid=None, cod="G-12"):
    fim.ensure_tables()
    uid = uid or str(uuid.uuid4())
    conn = db.get_connection()
    conn.execute("INSERT INTO amenajari_avize (cod_loc, nr_aviz, uid) VALUES (?,?,?)", (cod, "A-1", uid))
    aid = conn.execute("SELECT id FROM amenajari_avize WHERE uid=?", (uid,)).fetchone()["id"]
    conn.close()
    return uid, aid


def add_deceased_local(loc, uid, nume="Popescu", prenume="Ion"):
    conn = db.get_connection()
    conn.execute("INSERT INTO inhumati (nume, prenume, id_loc, uid) VALUES (?,?,?,?)", (nume, prenume, loc.id, uid))
    iid = conn.execute("SELECT id FROM inhumati WHERE uid=?", (uid,)).fetchone()["id"]
    conn.close()
    return iid


# ------------------------------------------------------------------ import pe tipuri
def test_import_deceased_keeps_uid_and_does_not_invent_dates(env, loc):
    uid = add(env, loc, "DECEASED", payload={"nume": "Popescu", "prenume": "Ion", "observatii": "fără dată"})
    res = fim.import_item(client(env), uid)
    assert res.state == fim.COMPLETE and res.cloud_final and res.created
    r = rows("SELECT * FROM inhumati")
    assert len(r) == 1 and r[0]["uid"] == uid and r[0]["nume"] == "Popescu" and r[0]["prenume"] == "Ion" and r[0]["id_loc"] == loc.id
    assert r[0]["data_nasterii"] == "" and r[0]["data_deces"] == "" and r[0]["data_inhumare"] == "" and r[0]["observatii"] == "fără dată"
    j = fim.journal_get(uid)
    assert j["import_state"] == fim.COMPLETE and j["local_entity_uid"] == uid and j["item_type"] == "DECEASED" and _eq(j["cemetery_uid"], CEM)
    assert env.inbox.items[uid]["status"] == "IMPORTED" and uid in env.inbox.items            # nu se șterge nimic din cloud


def _eq(a, b):
    return str(a) == str(b)


def test_import_deceased_with_dates(env, loc):
    uid = add(env, loc, payload={"nume": "A", "data_nasterii": "01.02.1950", "data_deces": "2026-03-04", "sex": "M"})
    fim.import_item(client(env), uid)
    r = rows("SELECT * FROM inhumati")[0]
    assert (r["data_nasterii"], r["data_deces"], r["sex"], r["prenume"]) == ("01.02.1950", "2026-03-04", "M", "")


def test_import_work_maps_all_supported_fields(env, loc):
    uid = add(env, loc, "WORK", payload={"descriere": "Placare", "data_inceput": "01.05.2026", "data_sfarsit": "09.05.2026", "executant": "SC Marmura", "observatii": "ok"})
    fim.import_item(client(env), uid)
    r = rows("SELECT * FROM amenajari_avize WHERE uid=?", uid)
    assert len(r) == 1
    r = r[0]
    assert (r["cod_loc"], r["lucrare"], r["data_aviz"], r["data_finalizare"], r["stare"], r["nr_aviz"]) == ("G-12", "Placare", "", "09.05.2026", "finalizata", "")
    assert (r["executant"], r["data_inceput"], r["observatii"]) == ("SC Marmura", "01.05.2026", "ok")
    assert env.inbox.items[uid]["status"] == "IMPORTED"


def test_import_work_without_end_date_has_no_invented_state(env, loc):
    uid = add(env, loc, "WORK", payload={"descriere": "Curățare"})
    fim.import_item(client(env), uid)
    r = rows("SELECT * FROM amenajari_avize WHERE uid=?", uid)[0]
    assert r["stare"] == "" and r["data_finalizare"] == "" and r["data_aviz"] == "" and r["executant"] == "" and r["data_inceput"] == ""


def test_import_permit_maps_to_existing_aviz_model(env, loc):
    uid = add(env, loc, "PERMIT", payload={"nr_aviz": "12/2026", "data_aviz": "02.01.2026", "lucrare": "Cavou", "observatii": "x"})
    fim.import_item(client(env), uid)
    r = rows("SELECT * FROM amenajari_avize WHERE uid=?", uid)[0]
    assert (r["nr_aviz"], r["data_aviz"], r["lucrare"], r["observatii"], r["cod_loc"]) == ("12/2026", "02.01.2026", "Cavou", "x", "G-12")
    assert [a["nr"] for a in db.list_avize_for_loc("G-12")] == ["12/2026"]


def test_permit_duplicate_number_on_same_grave_is_conflict(env, loc):
    add_work(loc)                                   # nr_aviz A-1 existent
    uid = add(env, loc, "PERMIT", payload={"nr_aviz": "A-1"})
    with pytest.raises(fim.ImportConflict) as e:
        fim.import_item(client(env), uid)
    assert e.value.fields == ("nr_aviz",) and len(rows("SELECT * FROM amenajari_avize")) == 1 and env.inbox.items[uid]["status"] == "ACCEPTED"


def test_import_document_pdf_to_grave(env, loc):
    uid = add(env, loc, "DOCUMENT", payload={"doc_type": "certificat", "caption": "Cert", "nr_evidenta": "55"}, files=[("application/pdf", PDF)])
    fim.import_item(client(env), uid)
    d = rows("SELECT * FROM documents")
    assert len(d) == 1 and (d[0]["entity_type"], d[0]["entity_id"], d[0]["doc_type"], d[0]["caption"], d[0]["nr_evidenta"]) == ("loc", "G-12", "certificat", "Cert", "55")
    assert d[0]["sha256"] == hashlib.sha256(PDF).hexdigest() and d[0]["size"] == len(PDF)
    assert Path(d[0]["file_path"]).read_bytes() == PDF and len(media_files()) == 1
    assert db.list_documents("loc", "G-12")[0]["id"] == d[0]["id"]


def test_import_document_unknown_type_keeps_original_label(env, loc):
    uid = add(env, loc, "DOCUMENT", payload={"doc_type": "Act Special", "caption": "x"}, files=[("image/png", PNG)])
    fim.import_item(client(env), uid)
    d = rows("SELECT * FROM documents")[0]
    assert d["doc_type"] == "alt_document" and d["caption"] == "[Act Special] x"


def test_import_document_linked_to_deceased_and_work(env, loc):
    did = str(uuid.uuid4())
    iid = add_deceased_local(loc, did)
    wuid, aid = add_work(loc)
    a = add(env, loc, "DOCUMENT", payload={"doc_type": "act"}, files=[("application/pdf", PDF)], target=did)
    b = add(env, loc, "DOCUMENT", payload={"doc_type": "act"}, files=[("image/png", PNG)], target=wuid)
    fim.import_item(client(env), a)
    fim.import_item(client(env), b)
    d = {r["entity_type"]: r for r in rows("SELECT * FROM documents")}
    assert d["inhumat"]["entity_id"] == str(iid) and d["aviz"]["entity_id"] == str(aid)


def test_document_target_of_other_grave_is_blocked(env, loc):
    conn = db.get_connection()
    conn.execute("INSERT INTO locuri_de_veci (cod_loc, uid) VALUES ('G-99', ?)", (str(uuid.uuid4()),))
    conn.close()
    wuid, _ = add_work(loc, cod="G-99")
    uid = add(env, loc, "DOCUMENT", payload={"doc_type": "act"}, files=[("application/pdf", PDF)], target=wuid)
    with pytest.raises(fim.ImportBlocked) as e:
        fim.import_item(client(env), uid)
    assert e.value.code == "TARGET_GRAVE_MISMATCH" and rows("SELECT * FROM documents") == [] and media_files() == []


def test_import_grave_photo_attaches_to_correct_grave(env, loc):
    uid = add(env, loc, "GRAVE_PHOTO", payload={"caption": "față"}, files=[("image/jpeg", JPEG)])
    before = rows("SELECT COUNT(*) AS n FROM locuri_de_veci")[0]["n"]
    fim.import_item(client(env), uid)
    d = rows("SELECT * FROM documents")
    assert len(d) == 1 and (d[0]["entity_type"], d[0]["entity_id"], d[0]["doc_type"], d[0]["caption"]) == ("loc", "G-12", "poza_loc", "față")
    assert rows("SELECT COUNT(*) AS n FROM locuri_de_veci")[0]["n"] == before
    assert fim.journal_get(uid)["local_entity_uid"] == GRAVE_UID


@pytest.mark.parametrize("stage,doc_type", [("BEFORE", "lucrare_inainte"), ("DURING", "lucrare_in_timpul"), ("AFTER", "lucrare_dupa")])
def test_import_work_photo_keeps_stage(env, loc, stage, doc_type):
    wuid, aid = add_work(loc)
    uid = add(env, loc, "WORK_PHOTO", payload={"caption": "c"}, stage=stage, files=[("image/png", PNG)], target=wuid)
    fim.import_item(client(env), uid)
    d = rows("SELECT * FROM documents")
    assert len(d) == 1 and (d[0]["entity_type"], d[0]["entity_id"], d[0]["doc_type"]) == ("aviz", str(aid), doc_type)
    assert [x["id"] for x in db.list_documents("aviz", aid, doc_type)] == [d[0]["id"]]
    from work_details import split_docs
    assert len(split_docs(db.list_documents("aviz", aid))[doc_type]) == 1


def test_same_photo_bytes_on_different_stages_creates_one_document_per_stage(env, loc):
    wuid, aid = add_work(loc)
    for stage in ("BEFORE", "DURING", "AFTER"):
        uid = add(env, loc, "WORK_PHOTO", payload={"caption": stage}, stage=stage, files=[("image/png", PNG)], target=wuid)
        fim.import_item(client(env), uid)
    d = rows("SELECT doc_type, entity_id FROM documents ORDER BY id")
    assert [x["doc_type"] for x in d] == ["lucrare_inainte", "lucrare_in_timpul", "lucrare_dupa"] and {x["entity_id"] for x in d} == {str(aid)} and len(media_files()) == 3


def test_work_photo_without_target_or_stage_is_blocked(env, loc):
    uid = add(env, loc, "WORK_PHOTO", payload={"caption": "c"}, stage="BEFORE", files=[("image/png", PNG)])     # doar grave, fără lucrare
    with pytest.raises(fim.ImportBlocked) as e:
        fim.import_item(client(env), uid)
    assert e.value.code == "MISSING_TARGET"
    wuid, _ = add_work(loc)
    uid2 = add(env, loc, "WORK_PHOTO", payload={"caption": "c"}, stage="AFTER", files=[("image/png", PNG)], target=wuid)
    env.inbox.items[uid2]["work_stage"] = None
    with pytest.raises(fim.ImportBlocked) as e:
        fim.import_item(client(env), uid2)
    assert e.value.code == "BAD_STAGE" and rows("SELECT * FROM documents") == []


def test_work_photo_target_not_yet_imported_is_blocked_then_works_after_work_import(env, loc):
    work = add(env, loc, "WORK", payload={"descriere": "Lucrare"})
    photo = add(env, loc, "WORK_PHOTO", payload={"caption": "c"}, stage="BEFORE", files=[("image/png", PNG)], target=work)
    with pytest.raises(fim.ImportBlocked) as e:
        fim.import_item(client(env), photo)
    assert e.value.code == "MISSING_TARGET"
    fim.import_item(client(env), work)
    fim.import_item(client(env), photo)
    aid = rows("SELECT id FROM amenajari_avize WHERE uid=?", work)[0]["id"]
    assert rows("SELECT entity_id, doc_type FROM documents") == [{"entity_id": str(aid), "doc_type": "lucrare_inainte"}]


def test_photo_must_be_image_and_file_required(env, loc):
    a = add(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[("application/pdf", PDF)])
    b = add(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[])
    for uid, code in ((a, "BAD_MIME"), (b, "NO_FILE")):
        with pytest.raises(fim.ImportBlocked) as e:
            fim.import_item(client(env), uid)
        assert e.value.code == code
    assert rows("SELECT * FROM documents") == [] and media_files() == [] and download_calls(env) == []


# ------------------------------------------------------------------ state machine / accept
def test_pending_cannot_be_imported_directly(env, loc):
    uid = add(env, loc, "DECEASED", status="PENDING")
    with pytest.raises(fim.ImportBlocked) as e:
        fim.import_item(client(env), uid)
    assert e.value.code == "NOT_ACCEPTED" and rows("SELECT * FROM inhumati") == [] and fim.journal_get(uid) is None
    assert imported_calls(env) == [] and download_calls(env) == [] and env.inbox.items[uid]["status"] == "PENDING"


def test_rejected_and_imported_elsewhere_cannot_be_imported(env, loc):
    rej = add(env, loc, status="REJECTED")
    imp = add(env, loc, status="IMPORTED")
    for uid in (rej, imp):
        with pytest.raises(fim.ImportBlocked):
            fim.import_item(client(env), uid)
    assert rows("SELECT * FROM inhumati") == []


def test_accept_and_import_goes_through_state_machine(env, loc):
    uid = add(env, loc, "DECEASED", status="PENDING")
    res = fim.accept_and_import(client(env), uid)
    assert res.state == fim.COMPLETE and env.inbox.items[uid]["status"] == "IMPORTED" and len(rows("SELECT * FROM inhumati")) == 1
    urls = [c["url"].rsplit("/", 1)[-1] for c in env.http.calls if c["method"] == "POST"]
    assert urls == ["accept", "imported"]


def test_accept_and_import_does_not_accept_when_import_is_impossible(env, loc):
    uid = add(env, loc, "DECEASED", status="PENDING", cemetery=str(uuid.uuid4()))
    with pytest.raises(fim.WrongRegistry):
        fim.accept_and_import(client(env), uid)
    assert env.inbox.items[uid]["status"] == "PENDING" and not [c for c in env.http.calls if c["method"] == "POST"]
    uid2 = add(env, loc, "DECEASED", status="PENDING", grave=False)
    env.inbox.items[uid2]["grave_uid"] = str(uuid.uuid4())
    with pytest.raises(fim.ImportBlocked):
        fim.accept_and_import(client(env), uid2)
    assert env.inbox.items[uid2]["status"] == "PENDING"


def test_plain_accept_still_does_not_import(env, loc):
    uid = add(env, loc, "DECEASED", status="PENDING")
    client(env).accept(uid, "PENDING")
    assert env.inbox.items[uid]["status"] == "ACCEPTED" and rows("SELECT * FROM inhumati") == []


# ------------------------------------------------------------------ idempotență
def test_duplicate_click_and_retry_do_not_duplicate(env, loc):
    uid = add(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[("image/jpeg", JPEG)])
    c = client(env)
    r1, r2, r3 = fim.import_item(c, uid), fim.import_item(c, uid), fim.import_item(c, uid)
    assert r1.created and not r2.created and not r3.created and r2.state == fim.COMPLETE
    assert len(rows("SELECT * FROM documents")) == 1 and len(media_files()) == 1 and len(imported_calls(env)) == 1 and len(download_calls(env)) == 1


def test_concurrent_double_click_creates_one_entity(env, loc):
    uid = add(env, loc, "DECEASED")
    out, barrier = [], threading.Barrier(2)

    def run():
        barrier.wait()
        try:
            out.append(fim.import_item(client(env), uid))
        except fim.ImportBlocked as exc:
            out.append(exc)

    ts = [threading.Thread(target=run) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(rows("SELECT * FROM inhumati")) == 1 and fim.journal_get(uid)["import_state"] in (fim.COMPLETE, fim.LOCAL_COMMITTED)
    assert any(isinstance(o, fim.ImportResult) for o in out)
    assert fim.import_item(client(env), uid).state == fim.COMPLETE and len(rows("SELECT * FROM inhumati")) == 1


def test_response_loss_on_mark_imported_then_retry_does_only_finalization(env, loc):
    uid = add(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[("image/png", PNG)])
    real = env.http.request

    def lossy(method, url, **kw):
        r = real(method, url, **kw)
        if url.endswith("/imported"):
            raise requests.ConnectionError("lost " + SIGNED_TOKEN)
        return r
    env.http.request = lossy
    res = fim.import_item(client(env), uid)
    assert res.state == fim.LOCAL_COMMITTED and not res.cloud_final and env.inbox.items[uid]["status"] == "IMPORTED"
    env.http.request = real
    n_down, n_imp = len(download_calls(env)), len(imported_calls(env))
    res2 = fim.import_item(client(env), uid)
    assert res2.state == fim.COMPLETE and res2.cloud_final and not res2.created
    assert len(rows("SELECT * FROM documents")) == 1 and len(media_files()) == 1
    assert len(download_calls(env)) == n_down and len(imported_calls(env)) == n_imp          # nici descărcare, nici al doilea /imported


def test_api_offline_after_local_commit_then_finalize_later(env, loc):
    uid = add(env, loc, "DECEASED")
    real = env.http.request

    def down_on_imported(method, url, **kw):
        if url.endswith("/imported"):
            raise requests.ConnectionError("offline")
        return real(method, url, **kw)
    env.http.request = down_on_imported
    res = fim.import_item(client(env), uid)
    assert res.state == fim.LOCAL_COMMITTED and not res.cloud_final and "reîncearcă" in res.message
    assert len(rows("SELECT * FROM inhumati")) == 1 and env.inbox.items[uid]["status"] == "ACCEPTED"
    env.http.fail = requests.ConnectionError("offline")                  # complet offline: MAIN lucrează local, nimic nu se pierde
    env.http.request = real
    assert fim.finalize_pending(client(env)) == [] and len(rows("SELECT * FROM inhumati")) == 1
    again = fim.import_item(client(env), uid)
    assert again.state == fim.LOCAL_COMMITTED and not again.cloud_final
    env.http.fail = None
    done = fim.finalize_pending(client(env))
    assert [d.state for d in done] == [fim.COMPLETE] and env.inbox.items[uid]["status"] == "IMPORTED"
    assert len(rows("SELECT * FROM inhumati")) == 1 and fim.journal_uncommitted_cloud() == []


# ------------------------------------------------------------------ crash recovery
def _single(env, loc, kind="photo"):
    if kind == "photo":
        return add(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[("image/jpeg", JPEG)])
    return add(env, loc, "DECEASED")


def test_crash_after_download_then_retry(env, loc, monkeypatch):
    uid = _single(env, loc)

    def boom(*a, **k):
        raise Crash()
    real_commit = fim._commit_local
    monkeypatch.setattr(fim, "_commit_local", boom)
    with pytest.raises(Crash):
        fim.import_item(client(env), uid)
    assert fim.journal_get(uid)["import_state"] == fim.PREPARING and Path(fim._staging_dir(uid)).exists()
    assert rows("SELECT * FROM documents") == [] and media_files() == [] and imported_calls(env) == []
    monkeypatch.setattr(fim, "_commit_local", real_commit)
    res = fim.import_item(client(env), uid)
    assert res.state == fim.COMPLETE
    assert len(rows("SELECT * FROM documents")) == 1 and len(media_files()) == 1 and not Path(fim._staging_dir(uid)).exists()
    assert env.inbox.items[uid]["status"] == "IMPORTED"


def test_crash_after_sqlite_commit_then_retry(env, loc, monkeypatch):
    uid = _single(env, loc)
    real_commit = fim._commit_local

    def commit_then_die(*a, **k):
        real_commit(*a, **k)
        raise Crash()
    monkeypatch.setattr(fim, "_commit_local", commit_then_die)
    with pytest.raises(Crash):
        fim.import_item(client(env), uid)
    assert fim.journal_get(uid)["import_state"] == fim.LOCAL_COMMITTED and imported_calls(env) == [] and env.inbox.items[uid]["status"] == "ACCEPTED"
    monkeypatch.setattr(fim, "_commit_local", real_commit)
    assert Path(fim._staging_dir(uid)).exists()
    n_down = len(download_calls(env))
    res = fim.import_item(client(env), uid)
    assert res.state == fim.COMPLETE and not res.created and not Path(fim._staging_dir(uid)).exists()
    assert len(rows("SELECT * FROM documents")) == 1 and len(media_files()) == 1 and len(download_calls(env)) == n_down
    assert env.inbox.items[uid]["status"] == "IMPORTED"


def test_crash_before_cloud_imported_then_restart_recovery(env, loc, monkeypatch):
    uid = _single(env, loc, "deceased")

    def die(*a, **k):
        raise Crash()
    real_fin = fim._finalize_cloud
    monkeypatch.setattr(fim, "_finalize_cloud", die)
    with pytest.raises(Crash):
        fim.import_item(client(env), uid)
    assert len(rows("SELECT * FROM inhumati")) == 1 and imported_calls(env) == []
    monkeypatch.setattr(fim, "_finalize_cloud", real_fin)
    fim._READY.clear()
    fim._LOCKS.clear()                                                # „restart”: stare de proces pierdută
    assert [j["inbox_item_uid"] for j in fim.journal_uncommitted_cloud()] == [uid]
    res = fim.import_item(client(env), uid)
    assert res.state == fim.COMPLETE and len(rows("SELECT * FROM inhumati")) == 1 and env.inbox.items[uid]["status"] == "IMPORTED"


def test_crash_inside_sqlite_transaction_rolls_back_everything(env, loc, monkeypatch):
    uid = add(env, loc, "DOCUMENT", payload={"doc_type": "act"}, files=[("application/pdf", PDF), ("image/png", PNG)])
    real = fim._place_file
    calls = []

    def second_fails(entity_type, f, src):
        calls.append(f["uid"])
        if len(calls) == 2:
            raise sqlite_error()
        return real(entity_type, f, src)
    import sqlite3

    def sqlite_error():
        return sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(fim, "_place_file", second_fails)
    with pytest.raises(sqlite3.OperationalError):
        fim.import_item(client(env), uid)
    assert rows("SELECT * FROM documents") == [] and fim.journal_get(uid) is None and imported_calls(env) == [] and media_files() == []      # fără fișiere orfane
    monkeypatch.setattr(fim, "_place_file", real)
    res = fim.import_item(client(env), uid)
    assert res.state == fim.COMPLETE and len(rows("SELECT * FROM documents")) == 2 and len(media_files()) == 2


def test_crash_after_file_move_before_commit_reuses_orphan_media(env, loc, monkeypatch):
    uid = _single(env, loc)
    real = fim._place_file

    def place_then_die(*a, **k):
        real(*a, **k)
        raise Crash()
    monkeypatch.setattr(fim, "_place_file", place_then_die)
    with pytest.raises(Crash):
        fim.import_item(client(env), uid)
    assert len(media_files()) == 1 and rows("SELECT * FROM documents") == [] and fim.journal_get(uid)["import_state"] == fim.PREPARING
    monkeypatch.setattr(fim, "_place_file", real)
    assert fim.import_item(client(env), uid).state == fim.COMPLETE
    assert len(media_files()) == 1 and len(rows("SELECT * FROM documents")) == 1 and env.inbox.items[uid]["status"] == "IMPORTED"


# ------------------------------------------------------------------ integritate fișiere
def _mk_blocked_assertions(env, uid):
    assert rows("SELECT * FROM documents") == [] and media_files() == [] and fim.journal_get(uid) is None
    assert imported_calls(env) == [] and env.inbox.items[uid]["status"] == "ACCEPTED" and not Path(fim._staging_dir(uid)).exists()


def test_sha_mismatch_blocks_import(env, loc):
    uid = add(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[("image/jpeg", JPEG)])
    fuid = env.inbox.files[uid][0]["uid"]
    env.http.storage_override[fuid] = b"\xff\xd8\xff\xe0" + b"X" * (len(JPEG) - 4)               # aceeași mărime, alt conținut
    with pytest.raises(fim.IntegrityError) as e:
        fim.import_item(client(env), uid)
    assert e.value.code == "SHA_MISMATCH"
    _mk_blocked_assertions(env, uid)


def test_size_mismatch_blocks_import(env, loc):
    uid = add(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[("image/jpeg", JPEG)])
    env.http.storage_override[env.inbox.files[uid][0]["uid"]] = JPEG[:-5]
    with pytest.raises(fim.IntegrityError) as e:
        fim.import_item(client(env), uid)
    assert e.value.code in ("SIZE_MISMATCH", "DOWNLOAD_REJECTED") or "Mărimea" in str(e.value)
    _mk_blocked_assertions(env, uid)


def test_corrupt_file_with_valid_hash_but_wrong_signature_blocks(env, loc):
    bad = b"NOTAJPEG" + b"z" * 40
    uid = add(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[("image/jpeg", bad)])
    with pytest.raises(fim.IntegrityError) as e:
        fim.import_item(client(env), uid)
    assert e.value.code == "CORRUPT_FILE"
    _mk_blocked_assertions(env, uid)


def test_metadata_mismatch_between_item_and_signed_url_blocks(env, loc):
    uid = add(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[("image/jpeg", JPEG)])
    env.inbox.files[uid][0]["sha256"] = "a" * 64                              # lista spune altceva decât obiectul semnat? (simulat: URL are metadata reală)
    real = env.inbox.file_url
    env.inbox.file_url = lambda *a, **k: {**real(*a, **k), "sha256": hashlib.sha256(JPEG).hexdigest()}
    with pytest.raises(fim.IntegrityError) as e:
        fim.import_item(client(env), uid)
    assert e.value.code == "META_MISMATCH"
    _mk_blocked_assertions(env, uid)


def test_invalid_file_metadata_blocks_before_download(env, loc):
    uid = add(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[("image/jpeg", JPEG)])
    env.inbox.files[uid][0]["sha256"] = "zz"
    with pytest.raises(fim.ImportBlocked) as e:
        fim.import_item(client(env), uid)
    assert e.value.code == "BAD_FILE_META" and download_calls(env) == []
    env.inbox.files[uid][0]["sha256"] = hashlib.sha256(JPEG).hexdigest()
    env.inbox.files[uid][0]["status"] = "STAGING"
    with pytest.raises(fim.ImportBlocked) as e:
        fim.import_item(client(env), uid)
    assert e.value.code == "FILE_NOT_READY" and download_calls(env) == []


def test_existing_local_media_with_other_sha_is_never_overwritten(env, loc):
    uid = add(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[("image/jpeg", JPEG)])
    fuid = env.inbox.files[uid][0]["uid"]
    folder = Path(db._media_dir("loc"))
    victim = folder / f"field_{fuid}.jpg"
    victim.write_bytes(b"\xff\xd8\xff-local-different")
    with pytest.raises(fim.IntegrityError) as e:
        fim.import_item(client(env), uid)
    assert e.value.code == "MEDIA_EXISTS_DIFFERENT" and victim.read_bytes() == b"\xff\xd8\xff-local-different"
    assert rows("SELECT * FROM documents") == [] and imported_calls(env) == [] and env.inbox.items[uid]["status"] == "ACCEPTED"


def test_cloud_filename_never_used_in_local_path(env, loc):
    uid = add(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[("image/jpeg", JPEG)])
    env.inbox.files[uid][0]["filename"] = "../../../evil.jpg"
    fim.import_item(client(env), uid)
    p = Path(rows("SELECT file_path FROM documents")[0]["file_path"]).resolve()
    root = Path(db.media_root()).resolve()
    assert root in p.parents and p.name == f"field_{env.inbox.files[uid][0]['uid']}.jpg" and not (loc.root.parent / "evil.jpg").exists()
    assert [f for f in loc.root.rglob("evil*")] == []


# ------------------------------------------------------------------ registru / grave
def test_missing_grave_blocks_and_creates_nothing(env, loc):
    uid = add(env, loc, "DECEASED")
    env.inbox.items[uid]["grave_uid"] = str(uuid.uuid4())
    n = rows("SELECT COUNT(*) AS n FROM locuri_de_veci")[0]["n"]
    with pytest.raises(fim.ImportBlocked) as e:
        fim.import_item(client(env), uid)
    assert e.value.code == "MISSING_GRAVE" and "loc de veci" in str(e.value).lower()
    assert rows("SELECT COUNT(*) AS n FROM locuri_de_veci")[0]["n"] == n and rows("SELECT * FROM inhumati") == [] and imported_calls(env) == []
    uid2 = add(env, loc, "DECEASED", grave=False)
    env.inbox.items[uid2]["target_uid"] = str(uuid.uuid4())
    with pytest.raises(fim.ImportBlocked):
        fim.import_item(client(env), uid2)


def test_wrong_active_cemetery_is_blocked_without_switch(env, loc):
    uid = add(env, loc, "DECEASED", cemetery=str(uuid.uuid4()))
    with pytest.raises(fim.WrongRegistry) as e:
        fim.import_item(client(env), uid)
    assert "Deschide registrul corespunzător" in str(e.value) and rows("SELECT * FROM inhumati") == [] and imported_calls(env) == []
    db.set_setting("cloud_bound_cemetery", str(uuid.uuid4()))
    uid2 = add(env, loc, "DECEASED")
    with pytest.raises(fim.WrongRegistry):
        fim.import_item(client(env), uid2)
    assert rows("SELECT * FROM inhumati") == [] and db.get_setting("cloud_bound_cemetery") != CEM


def test_unknown_active_registry_is_blocked(env, loc):
    db.set_setting("cloud_bound_cemetery", "")
    uid = add(env, loc, "DECEASED")
    with pytest.raises(fim.WrongRegistry) as e:
        fim.import_item(client(env), uid)
    assert e.value.code == "REGISTRY_UNKNOWN" and rows("SELECT * FROM inhumati") == []


def _binding(loc, cem):
    return SimpleNamespace(cemetery_uid=cem, activation_id="act-1", organization_uid=str(uuid.uuid4()), database=Path(db.DB_PATH), media=loc.root / "media", cache=loc.root / "cache")


def test_registry_binding_is_used_for_cemetery_check(env, loc, monkeypatch):
    import cimitir_registry
    monkeypatch.setattr(cimitir_registry, "current_binding", lambda: _binding(loc, CEM))
    monkeypatch.setattr(cimitir_registry, "pinned", lambda: __import__("contextlib").nullcontext())
    assert fim.active_cemetery_uid() == CEM
    uid = add(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[("image/jpeg", JPEG)])
    fim.import_item(client(env), uid)
    assert Path(rows("SELECT file_path FROM documents")[0]["file_path"]).parent.parent == loc.root / "media"
    monkeypatch.setattr(cimitir_registry, "current_binding", lambda: _binding(loc, str(uuid.uuid4())))
    uid2 = add(env, loc, "DECEASED")
    with pytest.raises(fim.WrongRegistry):
        fim.import_item(client(env), uid2)
    assert rows("SELECT * FROM inhumati") == []


# ------------------------------------------------------------------ entitate existentă
def test_existing_identical_entity_is_idempotent(env, loc):
    uid = add(env, loc, "DECEASED", payload={"nume": "Popescu", "prenume": "Ion"})
    iid = add_deceased_local(loc, uid)
    res = fim.import_item(client(env), uid)
    assert res.state == fim.COMPLETE and not res.created
    assert [r["id"] for r in rows("SELECT id FROM inhumati")] == [iid] and env.inbox.items[uid]["status"] == "IMPORTED"


def test_existing_conflicting_entity_is_not_overwritten(env, loc):
    uid = add(env, loc, "DECEASED", payload={"nume": "Popescu", "prenume": "Ion"})
    add_deceased_local(loc, uid, nume="Ionescu", prenume="Vasile")
    with pytest.raises(fim.ImportConflict) as e:
        fim.import_item(client(env), uid)
    assert set(e.value.fields) == {"nume", "prenume"}
    r = rows("SELECT * FROM inhumati")
    assert len(r) == 1 and r[0]["nume"] == "Ionescu" and r[0]["prenume"] == "Vasile"
    assert env.inbox.items[uid]["status"] == "ACCEPTED" and fim.journal_get(uid) is None and imported_calls(env) == []


def test_existing_identical_work_with_files_adds_only_missing_documents(env, loc):
    uid = add(env, loc, "WORK", payload={"descriere": "Placare"}, files=[("application/pdf", PDF)])
    conn = db.get_connection()
    conn.execute("INSERT INTO amenajari_avize (cod_loc, nr_aviz, lucrare, uid, stare, data_aviz, data_finalizare, observatii) VALUES ('G-12','','Placare',?,'','','','')", (uid,))
    conn.close()
    fim.import_item(client(env), uid)
    assert len(rows("SELECT * FROM amenajari_avize")) == 1 and len(rows("SELECT * FROM documents")) == 1
    fim.journal_get(uid)


# ------------------------------------------------------------------ preview / UID
def test_preview_has_required_information(env, loc):
    uid = add(env, loc, "WORK_PHOTO", payload={"caption": "c"}, stage="AFTER", files=[("image/png", PNG)], target=add_work(loc)[0])
    pv = fim.preview(client(env).get_item(uid), "Cimitirul Test")
    text = fim.preview_text(pv)
    assert pv["can_import"] and "Cimitirul Test" in pv["registry"] and pv["grave"] == "G-12" and pv["files"] == 1
    assert "Foto lucrare (DUPĂ)" in text and "Destinație locală: Fotografii lucrare" in text and "Fișiere atașate: 1" in text
    bad = fim.preview({**client(env).get_item(uid), "cemetery_uid": str(uuid.uuid4())})
    assert not bad["can_import"] and "Deschide registrul corespunzător" in fim.preview_text(bad)


def test_journal_is_unique_per_item_and_uid_preserved(env, loc):
    uid = add(env, loc, "DECEASED")
    fim.import_item(client(env), uid)
    conn = db.get_connection()
    with pytest.raises(Exception):
        conn.execute(f"INSERT INTO {fim.JOURNAL} (inbox_item_uid, cemetery_uid, item_type, import_state, created_at, updated_at) VALUES (?,?,?,?,?,?)", (uid, CEM, "DECEASED", "COMPLETE", "x", "x"))
    conn.close()
    assert rows(f"SELECT COUNT(*) AS n FROM {fim.JOURNAL}")[0]["n"] == 1
    assert rows("SELECT uid FROM inhumati")[0]["uid"] == uid


# ------------------------------------------------------------------ securitate
def test_no_secret_or_signed_url_persisted_or_logged(env, loc, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    ok = add(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[("image/jpeg", JPEG)])
    fim.import_item(client(env), ok)
    bad = add(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[("image/jpeg", JPEG)])
    env.http.storage_override[env.inbox.files[bad][0]["uid"]] = b"\xff\xd8\xff\xe0" + b"Q" * (len(JPEG) - 4)
    msgs = []
    with pytest.raises(fim.IntegrityError) as e:
        fim.import_item(client(env), bad)
    msgs.append(str(e.value))
    real = env.http.request
    env.http.request = lambda m, u, **k: (_ for _ in ()).throw(requests.ConnectionError(f"x {SIGNED_TOKEN} {ALL_SECRETS[0]}")) if u.startswith("https://storage") else real(m, u, **k)
    third = add(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[("image/jpeg", JPEG)])
    with pytest.raises(fic.CloudUnavailable) as e2:
        fim.import_item(client(env), third)
    msgs.append(str(e2.value))
    blob = b""
    for p in loc.root.rglob("*"):
        if p.is_file():
            blob += p.read_bytes()
    text = (caplog.text + "\n".join(msgs)).encode()
    for secret in (*ALL_SECRETS, SIGNED_TOKEN):
        assert secret.encode() not in blob and secret.encode() not in text
    assert b"token=" not in blob and b"storage.example.ro" not in blob


def test_import_module_has_no_credential_access():
    src = Path(fim.__file__).read_text(encoding="utf-8")
    for needle in ("SUPABASE_SECRET_KEY", "service_role", "SERVICE_ROLE", "DATABASE_URL", "psycopg", "os.environ", "getenv"):
        assert needle not in src
    for ref in ("txuxkjgftopznvmooexq",):
        assert ref not in src


def test_cloud_status_never_imported_before_local_commit(env, loc, monkeypatch):
    uid = add(env, loc, "DECEASED")
    order = []
    real_commit = fim._commit_local

    def spy(*a, **k):
        order.append(("commit", imported_calls(env)[:]))
        r = real_commit(*a, **k)
        order.append(("committed", rows("SELECT COUNT(*) AS n FROM inhumati")[0]["n"]))
        return r
    monkeypatch.setattr(fim, "_commit_local", spy)
    fim.import_item(client(env), uid)
    assert order[0] == ("commit", []) and order[1] == ("committed", 1) and len(imported_calls(env)) == 1
    assert not imported_calls(env)[0]["json"]


def test_no_cloud_deletion_calls(env, loc):
    uid = add(env, loc, "GRAVE_PHOTO", payload={"caption": "x"}, files=[("image/jpeg", JPEG)])
    fim.import_item(client(env), uid)
    assert all(c["method"] in ("GET", "POST") for c in env.http.calls) and not [c for c in env.http.calls if "delete" in c["url"]]
    assert uid in env.inbox.items and env.inbox.files[uid] and env.inbox.blobs


# ------------------------------------------------------------------ UI
def _yes(monkeypatch, w, seen):
    def question(parent, title, text, *a, **k):
        seen.append((title, text))
        return w.QMessageBox.Yes
    monkeypatch.setattr(w.QMessageBox, "question", staticmethod(question))
    monkeypatch.setattr(w.QMessageBox, "warning", staticmethod(lambda *a, **k: seen.append(("warning", a[2]))))
    monkeypatch.setattr(w.QMessageBox, "information", staticmethod(lambda *a, **k: seen.append(("info", a[2]))))


def test_ui_import_accepted_item_with_confirmation(env, loc, qapp, monkeypatch):
    import field_inbox_window as w
    uid = add(env, loc, "DECEASED", status="ACCEPTED")
    seen = []
    _yes(monkeypatch, w, seen)
    win = w.FieldInboxWindow(client_factory=lambda: client(env), can_review=lambda: True)
    win.cmb_status.setCurrentIndex(fic.STATUSES.index("ACCEPTED"))
    assert pump(qapp, lambda: win.table.rowCount() == 1)
    win.table.selectRow(0)
    assert pump(qapp, lambda: win.btn_import.isEnabled())
    assert not win.btn_accept.isEnabled() and not win.btn_accept_import.isEnabled() and win.btn_import.text() == "Importă în MAIN"
    changed = []
    win.changed.connect(lambda: changed.append(1))
    win.btn_import.click()
    assert pump(qapp, lambda: env.inbox.items[uid]["status"] == "IMPORTED" and win.current_status() == "IMPORTED")
    assert pump(qapp, lambda: win.lbl_state.text() == "1 elemente") and changed
    title, text = seen[0]
    assert title == "Importă în MAIN" and "Registru / cimitir" in text and "Destinație locală: Înhumați — loc G-12" in text and "Fișiere atașate: 0" in text
    assert len(rows("SELECT * FROM inhumati")) == 1
    win.table.selectRow(0)
    assert pump(qapp, lambda: not win.btn_import.isEnabled() and "Importat" in win.txt_detail.toPlainText())
    win.close()


def test_ui_accept_and_import_pending(env, loc, qapp, monkeypatch):
    import field_inbox_window as w
    uid = add(env, loc, "GRAVE_PHOTO", status="PENDING", payload={"caption": "x"}, files=[("image/png", _png_bytes())])
    seen = []
    _yes(monkeypatch, w, seen)
    win = w.FieldInboxWindow(client_factory=lambda: client(env), can_review=lambda: True)
    assert pump(qapp, lambda: win.table.rowCount() == 1)
    win.table.selectRow(0)
    assert pump(qapp, lambda: win.btn_accept_import.isEnabled())
    assert win.btn_accept.isEnabled() and not win.btn_import.isEnabled()
    win.btn_accept_import.click()
    assert pump(qapp, lambda: env.inbox.items[uid]["status"] == "IMPORTED")
    assert len(rows("SELECT * FROM documents")) == 1 and "Fișiere atașate: 1" in seen[0][1]
    win.close()


def test_ui_cancel_confirmation_imports_nothing_and_blocked_shows_reason(env, loc, qapp, monkeypatch):
    import field_inbox_window as w
    uid = add(env, loc, "DECEASED", status="ACCEPTED")
    seen = []
    _yes(monkeypatch, w, seen)
    monkeypatch.setattr(w.QMessageBox, "question", staticmethod(lambda *a, **k: w.QMessageBox.No))
    win = w.FieldInboxWindow(client_factory=lambda: client(env), can_review=lambda: True)
    win.cmb_status.setCurrentIndex(fic.STATUSES.index("ACCEPTED"))
    assert pump(qapp, lambda: win.table.rowCount() == 1)
    win.table.selectRow(0)
    assert pump(qapp, lambda: win.btn_import.isEnabled())
    win.btn_import.click()
    qapp.processEvents()
    assert rows("SELECT * FROM inhumati") == [] and env.inbox.items[uid]["status"] == "ACCEPTED"
    env.inbox.items[uid]["cemetery_uid"] = str(uuid.uuid4())
    win.lbl_state.setText("")
    win.btn_refresh.click()
    assert pump(qapp, lambda: win.lbl_state.text() == "1 elemente")
    win.table.selectRow(0)
    assert pump(qapp, lambda: win.btn_import.isEnabled())
    win.btn_import.click()
    assert any("Deschide registrul corespunzător" in t for k, t in seen if k == "warning")
    assert rows("SELECT * FROM inhumati") == [] and imported_calls(env) == []
    win.close()


def test_ui_without_approve_right_does_nothing(env, loc, qapp, monkeypatch):
    import field_inbox_window as w
    uid = add(env, loc, "DECEASED", status="ACCEPTED")
    win = w.FieldInboxWindow(client_factory=lambda: client(env), can_review=lambda: False)
    win.cmb_status.setCurrentIndex(fic.STATUSES.index("ACCEPTED"))
    assert pump(qapp, lambda: win.table.rowCount() == 1)
    win.table.selectRow(0)
    assert pump(qapp, lambda: win.btn_import.isEnabled())
    win.btn_import.click()
    qapp.processEvents()
    assert rows("SELECT * FROM inhumati") == [] and env.inbox.items[uid]["status"] == "ACCEPTED"
    win.close()
