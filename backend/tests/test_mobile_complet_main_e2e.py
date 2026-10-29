# ruff: noqa: F811
"""E2E: MAIN real (SQLite + desktop/sync_v2.py, proces separat) -> HTTP real -> Cloud (PostgreSQL local, rol cimitir_cp_app) -> Mobile (HTTP).
Permisiuni Mobile configurate din MAIN prin modulul desktop `sync_v2`, aplicate pe server; circuit MAIN->Cloud->Mobile și Mobile->Cloud->MAIN; fundalul hărții.
Rulează doar cu CIMITIR_TEST_PG_ADMIN_URL. NU atinge Supabase și NU rulează pe Windows (MAIN = Python/SQLite în proces separat pe Linux)."""
from __future__ import annotations

import hashlib
import socket
import uuid

import pytest
import requests
from fastapi import FastAPI

import cimitir_mobile_http as mih
import cimitir_mobile_issue as mi
import cimitir_mobile_registry as mr
import cimitir_sync_v2 as sv
import cimitir_sync_v2_http as svh
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_cleanup_race_pg import adm, pgdb  # noqa: F401
from test_etapa6b_field_storage import sup  # noqa: F401
from test_etapa6c_field_inbox_pg import fw  # noqa: F401
from test_sync_v2_main_e2e import ADD, H, Server, main_run, pg

import os
pytestmark = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")


@pytest.fixture()
def world(fw):
    with adm(fw["pg"]) as c:
        c.execute("TRUNCATE sync_v2_ops, sync_v2_conflicts, sync_v2_permissions, sync_v2_runs, sync_v2_audit, sync_v2_issued, sync_v2_counters, sync_v2_map_background, "
                  "sync_tombstones, inhumati, plati, contracte, concesionari, amenajari_avize, locuri_de_veci CASCADE")
    sync = sv.SyncV2Service(fw["svc"])
    app = FastAPI()
    app.include_router(svh.create_router(sync, None, mi.IssueService(sync)))
    app.include_router(mih.create_router(mr.MobileRegistryService(fw["svc"])))
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = Server(app, port); srv.start()
    yield {**fw, "srv": srv, "url": f"http://127.0.0.1:{port}", "data": f"/tmp/syncv2-mc-{uuid.uuid4().hex[:6]}"}
    srv.stop()


def test_main_configures_permissions_and_both_directions_sync_with_map_image(world):
    w = world
    phone, ref = w["T"]["A"]["phone"], w["T"]["A"]["phone_b"]
    out = main_run(w, ADD + """
from PIL import Image
import os
p = os.path.join(os.environ["CIMITIR_DATA_DIR"], "harta.png"); os.makedirs(os.path.dirname(p), exist_ok=True)
Image.new("RGB", (64, 48), (40, 90, 60)).save(p)
db.set_setting("map_path", p)
rep2 = cloud_sync.sync_all()
out["rep2"] = {k: rep2.get(k) for k in ("ok", "state")}
out["png_sha"] = __import__("hashlib").sha256(open(p, "rb").read()).hexdigest()
out["profiles"] = sorted(sync_v2.permission_profiles()["profiles"])
devs = sync_v2.list_devices()["devices"]
out["mref"] = "__REF__" if any(d["ref"] == "__REF__" for d in devs) else None
out["set"] = sync_v2.set_device_matrix("__REF__", profile="personal_cimitir")["profile"]
""".replace("__REF__", ref))
    assert out["rep"]["ok"] and {"administrator", "secretar", "personal_cimitir", "doar_consultare"} <= set(out["profiles"])
    assert out["set"] == "personal_cimitir" and out["mref"] == ref
    # fundalul hărții din MAIN a ajuns în cloud și telefonul (cu „Vede hartă") îl primește intact
    img = requests.get(f"{w['url']}/api/v1/sync-v2/map/background", headers=H(phone), timeout=10)
    assert img.status_code == 200 and hashlib.sha256(img.content).hexdigest() == out["png_sha"] and img.headers["x-map-width"] == "64"
    st = requests.get(f"{w['url']}/api/v1/sync-v2/status", headers=H(phone), timeout=10).json()["result"]
    assert st["permissions"]["profile"] == "personal_cimitir" and st["permissions"]["matrix"]["plati"]["view"] is False
    # Mobile -> Cloud -> MAIN: câmp permis (observații) trece, număr/cod blocat este refuzat de server
    row = requests.get(f"{w['url']}/api/v1/sync-v2/row?entity=locuri_de_veci&uid={out['loc_uid']}", headers=H(phone), timeout=10).json()["result"]
    base = {"op_id": "p-" + uuid.uuid4().hex, "entity": "locuri_de_veci", "uid": out["loc_uid"], "action": "upsert", "base_version": row["version"]}
    ok = requests.post(f"{w['url']}/api/v1/sync-v2/push", json={"ops": [{**base, "data": {**row["data"], "observatii": "din teren"}}]}, headers=H(phone), timeout=10).json()["result"]["results"][0]
    assert ok["status"] == "applied"
    bad = requests.post(f"{w['url']}/api/v1/sync-v2/push", json={"ops": [{**base, "op_id": "p-" + uuid.uuid4().hex, "base_version": ok["version"], "data": {**row["data"], "observatii": "din teren", "cod_loc": "HACK"}}]},
                        headers=H(phone), timeout=10).json()["result"]["results"][0]
    assert bad["status"] == "rejected" and bad["reason"] == "field_not_allowed"
    # MAIN primește modificarea; apoi MAIN modifică același loc și telefonul primește actualizarea (Mobile <- Cloud <- MAIN)
    back = main_run(w, """
rep = cloud_sync.sync_all()
out["obs"] = q("SELECT observatii, cod_loc FROM locuri_de_veci WHERE cod_loc IN ('E2E-1','HACK')")
q("UPDATE locuri_de_veci SET sector='S-NOU' WHERE cod_loc='E2E-1'")
rep = cloud_sync.sync_all()
out["audit"] = sorted({e["kind"] for e in sync_v2.audit_log(50)})
out["ok"] = rep.get("ok")
""")
    assert back["obs"] == [{"observatii": "din teren", "cod_loc": "E2E-1"}] and back["ok"]
    assert {"permissions_changed", "denied"} <= set(back["audit"])
    new = requests.get(f"{w['url']}/api/v1/sync-v2/row?entity=locuri_de_veci&uid={out['loc_uid']}", headers=H(phone), timeout=10).json()["result"]
    assert new["data"]["sector"] == "S-NOU" and new["data"]["observatii"] == "din teren" and new["version"] > ok["version"]
    # revocare din MAIN: telefonul nu mai poate scrie nimic (nici retrimiterea operației)
    main_run(w, f"sync_v2.set_device_matrix('{ref}', profile='doar_consultare')\n")
    again = requests.post(f"{w['url']}/api/v1/sync-v2/push", json={"ops": [{**base, "op_id": "p-" + uuid.uuid4().hex, "base_version": new["version"], "data": {**new["data"], "observatii": "după revocare"}}]},
                          headers=H(phone), timeout=10).json()["result"]["results"][0]
    assert again["status"] == "rejected" and again["reason"] == "not_authorized"
    assert pg(w, "SELECT observatii FROM locuri_de_veci")[0][0] == "din teren"
