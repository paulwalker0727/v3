# ruff: noqa: F811
"""E2E real: MAIN (SQLite + desktop/sync_v2.py, proces separat) -> HTTP real (uvicorn) -> Cloud (PostgreSQL local, rol cimitir_cp_app) -> Mobile (HTTP).
Acoperă: loc + înhumat din MAIN apar pe Mobile cu relația; Mobile -> MAIN; întrerupere internet; fără duplicate; conflict (MAIN câștigă).
Rulează doar cu CIMITIR_TEST_PG_ADMIN_URL (altfel SKIP). NU atinge Supabase."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import textwrap
import threading
import time
import uuid
from pathlib import Path

import pytest
import requests
import uvicorn
from fastapi import FastAPI

import cimitir_mobile_http as mih
import cimitir_mobile_registry as mr
import cimitir_sync_v2 as sv
import cimitir_sync_v2_http as svh
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_cleanup_race_pg import adm, pgdb  # noqa: F401
from test_etapa6b_field_storage import sup  # noqa: F401
from test_etapa6c_field_inbox_pg import fw  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")
ROOT = Path(__file__).resolve().parents[2]
RUN = uuid.uuid4().hex[:6]


class Server:
    def __init__(self, app, port):
        self.app, self.port, self.srv, self.th = app, port, None, None

    def start(self):
        self.srv = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error", access_log=False))
        self.th = threading.Thread(target=self.srv.run, daemon=True)
        self.th.start()
        for _ in range(100):
            try:
                requests.get(f"http://127.0.0.1:{self.port}/nope", timeout=0.5)
                return
            except requests.RequestException:
                time.sleep(0.1)
        raise RuntimeError("serverul nu a pornit")

    def stop(self):
        self.srv.should_exit = True
        self.th.join(10)


@pytest.fixture()
def world(fw):
    with adm(fw["pg"]) as c:
        c.execute("TRUNCATE sync_v2_ops, sync_v2_conflicts, sync_v2_permissions, sync_v2_runs, sync_tombstones, inhumati, plati, contracte, concesionari, "
                  "amenajari_avize, locuri_de_veci CASCADE")
    app = FastAPI()
    app.include_router(svh.create_router(sv.SyncV2Service(fw["svc"])))
    app.include_router(mih.create_router(mr.MobileRegistryService(fw["svc"])))
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = Server(app, port); srv.start()
    yield {**fw, "srv": srv, "url": f"http://127.0.0.1:{port}", "data": f"/tmp/syncv2-{RUN}-{uuid.uuid4().hex[:4]}"}
    try:
        srv.stop()
    except Exception:
        pass


def _cfg(w, registry: bool) -> str:
    return ""


def main_run(w, code: str, registry: bool = False) -> dict:
    """Rulează cod în procesul MAIN (propriul SQLite). Ultima linie RESULT=<json>."""
    CLASSIC_CFG = f"cloud_sync.save_config({w['url']!r}, {w['T']['A']['main']!r}, False, False)"
    REG_CFG = (f"import cimitir_secretstore as _ss; _ref='cimitir/cloud/binding/pilot'; _ss.default_store().set(_ref, {w['T']['A']['main']!r}); "
               f"db.set_setting('cloud_url', {w['url']!r}); db.set_setting('cloud_secret_ref', _ref)")
    script = textwrap.dedent(f"""
        import json, sys
        sys.path.insert(0, {str(ROOT / 'desktop')!r})
        import database as db, cloud_sync, entity_sync, sync_v2
        db.init_db()
        {REG_CFG if registry else CLASSIC_CFG}
        out = {{}}
        def q(sql, args=()):
            c = db.get_connection()
            try:
                cur = c.execute(sql, args)
                return [dict(r) for r in cur.fetchall()] if cur.description else cur.lastrowid
            finally:
                c.close()
    """) + textwrap.dedent(code) + "\nprint('RESULT=' + json.dumps(out, default=str))\n"
    env = {**os.environ, "CIMITIR_SECRET_STORE": "test-encrypted", "CIMITIR_ALLOW_TEST_SECRETSTORE": "1",
           "CIMITIR_TEST_SECRET_KEY": "rAR-xhkir67T-0hgHU_d6gxHdQt7zqO9zPP-nl-r7yw=", "CIMITIR_DATA_DIR": w["data"], "CIMITIR_MODE": "local",
           "CIMITIR_DB_ENGINE": "sqlite", "PYTHONPATH": str(ROOT / "shared")}
    env.pop("DATABASE_URL", None)
    p = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env, timeout=170, cwd=ROOT / "desktop")
    assert p.returncode == 0, p.stdout[-1500:] + p.stderr[-2500:]
    return json.loads([ln for ln in p.stdout.splitlines() if ln.startswith("RESULT=")][-1][7:])


def H(k):
    return {"Authorization": f"Binding {k}"}


def mobile_grave(w, uid):
    r = requests.get(f"{w['url']}/api/v1/mobile/graves/{uid}", headers=H(w["T"]["A"]["phone"]), timeout=10)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def pg(w, sql, args=()):
    with adm(w["pg"]) as c:
        return c.execute(sql, args).fetchall()


ADD = """
loc = q("INSERT INTO locuri_de_veci (cod_loc, points, sector) VALUES (?,?,?)", ("E2E-1", json.dumps([[0,0],[10,0],[10,10],[0,10],[0,0]]), "S1"))
q("UPDATE locuri_de_veci SET are_inhumari=1 WHERE id=?", (loc,))
q("INSERT INTO inhumati (nume, prenume, data_deces, id_loc) VALUES (?,?,?,?)", ("Popescu", "Ion", "2026-01-02", loc))
rep = cloud_sync.sync_all()
out["rep"] = {k: rep.get(k) for k in ("ok", "state", "applied", "conflicts", "errors", "pending", "pulled")}
out["loc_uid"] = q("SELECT uid FROM locuri_de_veci WHERE cod_loc='E2E-1'")[0]["uid"]
"""


def test_first_result_main_to_mobile(world):
    w = world
    out = main_run(w, ADD)
    assert out["rep"]["ok"] and out["rep"]["applied"] == 2 and out["rep"]["pending"] == 0, out
    d = mobile_grave(w, out["loc_uid"])
    assert d["grave"]["cod_loc"] == "E2E-1" and len(d["grave"]["points"]) == 5
    assert [(b["nume"], b["prenume"]) for b in d["burials"]] == [("Popescu", "Ion")]
    # a doua apăsare pe „Sincronizează": nimic de trimis, fără duplicate
    again = main_run(w, "rep = cloud_sync.sync_all(); out['rep'] = {k: rep.get(k) for k in ('ok','pushed','applied','pending')}")
    assert again["rep"] == {"ok": True, "pushed": 0, "applied": 0, "pending": 0}
    assert pg(w, "SELECT count(*) FROM locuri_de_veci")[0][0] == 1 and pg(w, "SELECT count(*) FROM inhumati")[0][0] == 1
    # jurnal vizibil în MAIN
    j = main_run(w, "out['runs'] = sync_v2.journal()")["runs"]
    assert any(r["direction"] == "push" and r["applied"] == 2 for r in j)


def test_internet_interruption_then_resume_no_duplicates(world):
    w = world
    out = main_run(w, ADD)
    w["srv"].stop()                                   # întrerupere internet
    off = main_run(w, """
loc = q("SELECT id FROM locuri_de_veci WHERE cod_loc='E2E-1'")[0]["id"]
q("INSERT INTO inhumati (nume, prenume, data_deces, id_loc) VALUES (?,?,?,?)", ("Georgescu", "Maria", "2026-02-02", loc))
rep = cloud_sync.sync_all()
out["rep"] = {k: rep.get(k) for k in ("ok", "state", "pending")}
out["queued"] = len(entity_sync.pending_entity_ops())
""")
    assert off["rep"]["ok"] is False and off["rep"]["state"] == "offline" and off["queued"] == 1 and off["rep"]["pending"] == 1
    w["srv"].start()                                  # revine internetul
    on = main_run(w, "rep = cloud_sync.sync_all(); out['rep'] = {k: rep.get(k) for k in ('ok','applied','pending')}")
    assert on["rep"] == {"ok": True, "applied": 1, "pending": 0}
    d = mobile_grave(w, out["loc_uid"])
    assert sorted(b["nume"] for b in d["burials"]) == ["Georgescu", "Popescu"]
    main_run(w, "cloud_sync.sync_all(); cloud_sync.sync_all()")
    assert pg(w, "SELECT count(*) FROM inhumati")[0][0] == 2


def test_mobile_edit_reaches_main_and_conflict_main_wins(world):
    w = world
    out = main_run(w, ADD)
    phone = w["T"]["A"]["phone"]
    # fără permisiune din MAIN: telefonul nu poate scrie
    ch = requests.get(f"{w['url']}/api/v1/sync-v2/pull?entity=inhumati", headers=H(phone), timeout=10).json()["result"]["changes"][0]
    edit = {"op_id": "m-" + uuid.uuid4().hex, "entity": "inhumati", "uid": ch["uid"], "action": "upsert", "base_version": ch["version"],
            "data": {**ch["data"], "observatii": "verificat pe teren"}}
    r = requests.post(f"{w['url']}/api/v1/sync-v2/push", json={"ops": [edit]}, headers=H(phone), timeout=10).json()["result"]["results"][0]
    assert r["status"] == "rejected" and r["reason"] == "not_authorized"
    # MAIN acordă dreptul (din aplicația desktop, prin sync_v2) și telefonul scrie
    perm = main_run(w, """
devs = sync_v2.list_devices()["devices"]
out["names"] = [d["name"] for d in devs]
ref = devs[0]["ref"]
out["editable"] = sync_v2.set_device_entities(ref, ["inhumati"])
""")
    assert perm["editable"] == ["inhumati"]
    ref = w["T"]["A"]["phone_b"]
    perm2 = requests.put(f"{w['url']}/api/v1/sync-v2/devices/{ref}/permissions", json={"entities": ["inhumati"]}, headers=H(w["T"]["A"]["main"])).json()
    assert perm2["ok"]
    edit["op_id"] = "m-" + uuid.uuid4().hex
    r = requests.post(f"{w['url']}/api/v1/sync-v2/push", json={"ops": [edit]}, headers=H(phone), timeout=10).json()["result"]["results"][0]
    assert r["status"] == "applied"
    back = main_run(w, """
rep = cloud_sync.sync_all()
out["rep"] = {k: rep.get(k) for k in ("ok", "pulled", "pending")}
out["row"] = q("SELECT observatii, version FROM inhumati WHERE nume='Popescu'")[0]
out["outbox"] = len(entity_sync.pending_entity_ops(("pending","conflict","error")))
""")
    assert back["row"]["observatii"] == "verificat pe teren" and back["row"]["version"] == 2 and back["outbox"] == 0 and back["rep"]["pending"] == 0, back
    # conflict: MAIN editează (bază v2), telefonul editează în paralel; MAIN a trimis întâi
    main_run(w, "q(\"UPDATE inhumati SET observatii='din MAIN' WHERE nume='Popescu'\"); cloud_sync.sync_all()")
    edit2 = {**edit, "op_id": "m-" + uuid.uuid4().hex, "base_version": 2, "data": {**edit["data"], "observatii": "telefon vechi"}}
    r = requests.post(f"{w['url']}/api/v1/sync-v2/push", json={"ops": [edit2]}, headers=H(phone), timeout=10).json()["result"]["results"][0]
    assert r["status"] == "conflict" and r["server"]["data"]["observatii"] == "din MAIN"
    assert pg(w, "SELECT observatii FROM inhumati")[0][0] == "din MAIN"
    conf = main_run(w, "out['c'] = [(c['reason'], c['resolution']) for c in sync_v2.server_conflicts(False)]")["c"]
    assert ["version_mismatch", "open"] in [list(x) for x in conf]


SCENARIO = """
loc = q("INSERT INTO locuri_de_veci (cod_loc, points, sector) VALUES (?,?,?)", ("SC-1", json.dumps([[0,0],[10,0],[10,10],[0,10],[0,0]]), "S1"))
conc = q("INSERT INTO concesionari (nume, prenume, telefon) VALUES (?,?,?)", ("Ionescu", "Maria", "0711000000"))
q("INSERT INTO contracte (numar_contract, data_contract, cod_loc, id_loc, concesionar_id) VALUES (?,?,?,?,?)", ("C-100", "2026-03-01", "SC-1", loc, conc))
q("INSERT INTO inhumati (nume, prenume, data_deces, id_loc) VALUES (?,?,?,?)", ("Popescu", "Ion", "2026-01-02", loc))
q("INSERT INTO inhumati (nume, prenume, data_deces, id_loc) VALUES (?,?,?,?)", ("Popescu", "Ana", "2026-02-02", loc))
rep = cloud_sync.sync_all()
out["rep"] = {k: rep.get(k) for k in ("ok", "applied", "conflicts", "errors", "pending")}
out["loc_uid"] = q("SELECT uid FROM locuri_de_veci WHERE cod_loc='SC-1'")[0]["uid"]
"""


def test_full_scenario_polygon_concessionaire_burials_then_reverse(world):
    """Loc + concesionar + contract + 2 înhumați în MAIN -> Sincronizează -> Mobile vede totul legat; apoi modificări MAIN și Mobile în ambele sensuri."""
    w = world
    out = main_run(w, SCENARIO)
    assert out["rep"] == {"ok": True, "applied": 5, "conflicts": 0, "errors": 0, "pending": 0}, out
    d = mobile_grave(w, out["loc_uid"])
    assert d["grave"]["cod_loc"] == "SC-1" and len(d["grave"]["points"]) == 5
    assert [(c["nume"], c["prenume"], c["numar_contract"]) for c in d["contracts"]] == [("Ionescu", "Maria", "C-100")]
    assert sorted(b["prenume"] for b in d["burials"]) == ["Ana", "Ion"]
    phone = w["T"]["A"]["phone"]
    s = requests.get(f"{w['url']}/api/v1/mobile/search?q=Ionescu", headers=H(phone), timeout=10).json()["result"]
    assert s["concessionaires"][0]["grave_code"] == "SC-1" and s["contracts"][0]["number"] == "C-100"
    mp = requests.get(f"{w['url']}/api/v1/mobile/map", headers=H(phone), timeout=10).json()["result"]["plots"]
    assert [p["code"] for p in mp] == ["SC-1"] and len(mp[0]["points"]) == 5
    # MAIN modifică (sector + telefon concesionar) -> Mobile
    main_run(w, "q(\"UPDATE locuri_de_veci SET sector='S2' WHERE cod_loc='SC-1'\"); q(\"UPDATE concesionari SET telefon='0799999999' WHERE nume='Ionescu'\"); cloud_sync.sync_all()")
    d2 = mobile_grave(w, out["loc_uid"])
    assert d2["grave"]["sector"] == "S2" and d2["contracts"][0]["telefon"] == "0799999999"
    # inversul: MAIN acordă drepturi, telefonul modifică concesionar + înhumat, MAIN primește la „Sincronizează"
    ref = w["T"]["A"]["phone_b"]
    assert requests.put(f"{w['url']}/api/v1/sync-v2/devices/{ref}/permissions", json={"entities": ["concesionari", "inhumati"]}, headers=H(w["T"]["A"]["main"])).json()["ok"]
    conc_uid, conc_ver = d2["contracts"][0]["concessionaire_uid"], d2["contracts"][0]["concessionaire_version"]
    row = requests.get(f"{w['url']}/api/v1/sync-v2/row?entity=concesionari&uid={conc_uid}", headers=H(phone), timeout=10).json()["result"]
    assert row["version"] == conc_ver
    bur = next(b for b in d2["burials"] if b["prenume"] == "Ana")
    brow = requests.get(f"{w['url']}/api/v1/sync-v2/row?entity=inhumati&uid={bur['uid']}", headers=H(phone), timeout=10).json()["result"]
    ops = [{"op_id": "r-" + uuid.uuid4().hex, "entity": "concesionari", "uid": conc_uid, "action": "upsert", "base_version": row["version"],
            "data": {**row["data"], "telefon": "0722123456"}},
           {"op_id": "r-" + uuid.uuid4().hex, "entity": "inhumati", "uid": bur["uid"], "action": "upsert", "base_version": brow["version"],
            "data": {**brow["data"], "observatii": "completat pe teren"}}]
    res = requests.post(f"{w['url']}/api/v1/sync-v2/push", json={"ops": ops}, headers=H(phone), timeout=10).json()["result"]["results"]
    assert [r["status"] for r in res] == ["applied", "applied"]
    again = requests.post(f"{w['url']}/api/v1/sync-v2/push", json={"ops": ops}, headers=H(phone), timeout=10).json()["result"]["results"]
    assert all(r.get("duplicate") for r in again)                                  # retry fără efect dublu
    back = main_run(w, """
rep = cloud_sync.sync_all()
out["rep"] = {k: rep.get(k) for k in ("ok", "pulled", "pending", "conflicts")}
out["tel"] = q("SELECT telefon FROM concesionari WHERE nume='Ionescu'")[0]["telefon"]
out["obs"] = q("SELECT observatii FROM inhumati WHERE prenume='Ana'")[0]["observatii"]
out["links"] = q("SELECT (SELECT count(*) FROM inhumati i JOIN locuri_de_veci l ON l.id=i.id_loc WHERE l.cod_loc='SC-1') AS bur, "
                 "(SELECT count(*) FROM contracte c JOIN concesionari x ON x.id=c.concesionar_id JOIN locuri_de_veci l ON l.id=c.id_loc) AS con")[0]
out["outbox"] = len(entity_sync.pending_entity_ops(("pending", "conflict", "error")))
""")
    assert back["tel"] == "0722123456" and back["obs"] == "completat pe teren" and back["outbox"] == 0 and back["rep"]["ok"], back
    assert back["links"] == {"bur": 2, "con": 1}
    assert pg(w, "SELECT count(*) FROM inhumati")[0][0] == 2 and pg(w, "SELECT count(*) FROM concesionari")[0][0] == 1 and pg(w, "SELECT count(*) FROM contracte")[0][0] == 1


def test_verify_script_reports_real_relations(world):
    """scripts/verify_sync_v2_live.py (read-only) pe același server: relații complete -> cod 0; apoi un părinte lipsă -> cod 1."""
    w = world
    main_run(w, SCENARIO)
    envv = {**os.environ, "CIMITIR_VERIFY_URL": w["url"], "CIMITIR_VERIFY_PHONE_KEY": w["T"]["A"]["phone"], "CIMITIR_VERIFY_MAIN_KEY": w["T"]["A"]["main"]}
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_sync_v2_live.py")], capture_output=True, text=True, env=envv, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "SC-1: poligon 5 puncte · concesionari: Ionescu Maria · înhumați:" in p.stdout and "relații complete" in p.stdout
    assert w["T"]["A"]["phone"] not in p.stdout


def test_main_uses_pilot_binding_key_from_secretstore_without_manual_setup(world):
    """Cheia MAIN Binding de la pairing (SecretStore + cloud_secret_ref din registrul local) este folosită automat: fără profil/legacy key configurate manual."""
    w = world
    out = main_run(w, ADD.replace("rep = cloud_sync.sync_all()", "out['src'] = sync_v2.configured_source(); rep = cloud_sync.sync_all()"), registry=True)
    assert out["src"] == "registry" and out["rep"]["ok"] and out["rep"]["applied"] == 2, out
    assert mobile_grave(w, out["loc_uid"])["burials"][0]["nume"] == "Popescu"
    # cheia nu apare în SQLite (doar referința) și Binding nu pleacă niciodată spre rutele legacy /api/sync
    chk = main_run(w, """
import sqlite3
rows = q("SELECT key, value FROM settings")
out["leak"] = [r["key"] for r in rows if "cimitir." in str(r["value"]) and r["key"] != "cloud_secret_ref" and len(str(r["value"]).split(".")) == 3]
try:
    cloud_sync._request("GET", "/ping")
    out["legacy"] = "sent"
except cloud_sync.Rejected as e:
    out["legacy"] = "blocked"
""", registry=True)
    assert chk["leak"] == []
    assert chk["legacy"] == "blocked"


def test_payments_permits_delete_sync_and_phone_repair_lose_nothing(world):
    from test_etapa6c_field_inbox_pg import seed_binding
    w = world
    out = main_run(w, SCENARIO + """
cid = q("SELECT id FROM contracte WHERE numar_contract='C-100'")[0]["id"]
q("INSERT INTO plati (cod_loc, suma, data_plata, tip, chitanta_nr, contract_id) VALUES (?,?,?,?,?,?)", ("SC-1", 250.0, "2026-03-02", "taxă concesiune", "R-77", cid))
q("INSERT INTO amenajari_avize (cod_loc, nr_aviz, data_aviz, lucrare) VALUES (?,?,?,?)", ("SC-1", "AV-5", "2026-03-03", "bordură"))
rep = cloud_sync.sync_all()
out["rep2"] = {k: rep.get(k) for k in ("ok", "applied", "pending")}
""")
    assert out["rep2"] == {"ok": True, "applied": 2, "pending": 0}, out
    d = mobile_grave(w, out["loc_uid"])
    assert [(p["suma"], p["chitanta_nr"]) for p in d["payments"]] == [(250.0, "R-77")] and [x["nr_aviz"] for x in d["permits"]] == ["AV-5"]
    # MAIN șterge un înhumat (anulare) și plata -> ștergerile ajung în cloud ca tombstone; restul datelor rămân intacte
    dele = main_run(w, """
q("DELETE FROM inhumati WHERE prenume='Ana'")
q("DELETE FROM plati WHERE chitanta_nr='R-77'")
rep = cloud_sync.sync_all()
out["rep"] = {k: rep.get(k) for k in ("ok", "applied", "pending", "errors")}
""")
    assert dele["rep"]["ok"] and dele["rep"]["pending"] == 0 and dele["rep"]["errors"] == 0, dele
    assert pg(w, "SELECT count(*) FROM inhumati")[0][0] == 1 and pg(w, "SELECT count(*) FROM plati")[0][0] == 0
    assert pg(w, "SELECT count(*) FROM sync_tombstones WHERE entity IN ('inhumati','plati')")[0][0] == 2
    assert pg(w, "SELECT count(*) FROM locuri_de_veci")[0][0] == 1 and pg(w, "SELECT count(*) FROM contracte")[0][0] == 1 and pg(w, "SELECT count(*) FROM concesionari")[0][0] == 1
    # telefon nou/reîmperecheat (alt binding, același cimitir): descarcă totul de la zero, fără pierderi
    t = w["T"]["A"]
    new_key, _b = seed_binding(w["pg"], t["org"], t["cem"], "MOBILE")
    for ent, expect in (("locuri_de_veci", 1), ("concesionari", 1), ("contracte", 1), ("inhumati", 1), ("amenajari_avize", 1)):
        ch = requests.get(f"{w['url']}/api/v1/sync-v2/pull?entity={ent}&since=0", headers=H(new_key), timeout=10).json()["result"]["changes"]
        assert len([c for c in ch if not c["deleted"]]) == expect, ent
    ch = requests.get(f"{w['url']}/api/v1/sync-v2/pull?entity=inhumati&since=0", headers=H(new_key), timeout=10).json()["result"]["changes"]
    assert any(c["deleted"] for c in ch)        # ștergerea ajunge și la un dispozitiv nou/vechi, controlat prin tombstone
    # MAIN se reface din cloud (SQLite nou, aceeași legătură): primește tot, nimic nu se pierde și nu se dublează
    again = main_run(w, "rep = cloud_sync.sync_all(); out['rep'] = {k: rep.get(k) for k in ('ok','applied','pending')}; out['n'] = q('SELECT (SELECT count(*) FROM inhumati) AS i, (SELECT count(*) FROM locuri_de_veci) AS l')[0]")
    assert again["rep"]["ok"] and again["n"] == {"i": 1, "l": 1}
