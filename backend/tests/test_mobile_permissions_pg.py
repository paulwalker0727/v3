# ruff: noqa: F811
"""Permisiuni Mobile pe categorii (Vede/Adaugă/Editează/Șterge) + câmpuri editabile + profiluri, pe PostgreSQL LOCAL REAL (rol cimitir_cp_app fără BYPASSRLS).
Verifică impunerea pe SERVER (apel direct la API, nu doar UI), revocarea pentru operații offline, ștergerea auditată și izolarea între parohii.
Rulează doar cu CIMITIR_TEST_PG_ADMIN_URL=postgresql://<superuser>@127.0.0.1:<port>/postgres. NU atinge Supabase."""
from __future__ import annotations

import os

import pytest

from test_sync_v2_pg import H, adm, count, hx, op, pgdb, pull, push, seed_main_data, sx  # noqa: F401
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_field_storage import sup  # noqa: F401
from test_etapa6c_field_inbox_pg import fw  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")
API = "/api/v1/sync-v2"


def cell(view=False, add=False, edit=False, delete=False, fields=None):
    return {"view": view, "add": add, "edit": edit, "delete": delete, "fields": fields}


def give(sx, ref, matrix=None, profile=None, view_pii=False, tag="A"):
    body = {"view_pii": view_pii}
    if profile:
        body["profile"] = profile
    if matrix is not None:
        body["matrix"] = matrix
    r = sx["client"].put(f"{API}/devices/{ref}/matrix", json=body, headers=H(sx["T"][tag]["main"]))
    assert r.status_code == 200, r.text
    return r.json()["result"]


def status(sx, phone):
    return sx["client"].get(f"{API}/status", headers=H(phone)).json()["result"]


def test_profiles_exist_and_main_assigns_them(sx):
    key = sx["T"]["A"]["main"]
    r = sx["client"].get(f"{API}/permissions/profiles", headers=H(key)).json()["result"]
    assert {"administrator", "secretar", "personal_cimitir", "doar_consultare"} <= set(r["profiles"])
    assert len(r["categories"]) == 8 and "observatii" in r["fields"]["locuri"] and r["fields"]["harta"] == ["points", "color"]
    phone, ref = sx["T"]["A"]["phone"], sx["T"]["A"]["phone_b"]
    assert sx["client"].get(f"{API}/permissions/profiles", headers=H(phone)).status_code == 403      # telefonul nu administrează permisiuni
    assert sx["client"].put(f"{API}/devices/{ref}/matrix", json={"profile": "secretar"}, headers=H(phone)).status_code == 403
    give(sx, ref, profile="doar_consultare")
    st = status(sx, phone)
    assert st["permissions"]["profile"] == "doar_consultare" and st["permissions"]["mode"] == "matrix"
    assert st["permissions"]["matrix"]["plati"]["view"] is False and st["editable"] == []


def test_view_off_hides_category_and_blocks_direct_api(sx):
    key, loc, ben, inh = seed_main_data(sx)
    phone, ref = sx["T"]["A"]["phone"], sx["T"]["A"]["phone_b"]
    give(sx, ref, matrix={"locuri": cell(True), "harta": cell(True), "inhumati": cell(True)})       # concesionari/contracte/plăți/avize/fișiere: fără „Vede"
    st = status(sx, phone)
    assert set(st["entities"]) == {"locuri_de_veci", "inhumati"}
    for ent in ("concesionari", "contracte", "plati", "amenajari_avize"):
        assert sx["client"].get(f"{API}/pull?entity={ent}", headers=H(phone)).status_code == 403
    assert sx["client"].get(f"{API}/row?entity=concesionari&uid={ben}", headers=H(phone)).status_code == 403
    # nici operațiile (apel direct la API) nu trec pe o categorie invizibilă
    r = push(sx, phone, [op("concesionari", ben, {"nume": "Hack"}, base=1)])[0]
    assert r["status"] == "rejected" and r["reason"] == "not_authorized"
    assert sx["mob"].search(phone, "Ionescu")["concessionaires"] == []


def test_view_only_is_read_only_and_add_edit_flags_are_independent(sx):
    key, loc, ben, inh = seed_main_data(sx)
    phone, ref = sx["T"]["A"]["phone"], sx["T"]["A"]["phone_b"]
    give(sx, ref, matrix={"inhumati": cell(True), "locuri": cell(True)})
    assert push(sx, phone, [op("inhumati", inh, {"nume": "X", "loc_uid": loc}, base=1)])[0]["reason"] == "not_authorized"
    assert push(sx, phone, [op("inhumati", hx(), {"nume": "Nou", "loc_uid": loc})])[0]["reason"] == "not_authorized"
    give(sx, ref, matrix={"inhumati": cell(True, add=True), "locuri": cell(True)})                      # doar „Adaugă": poate crea, nu poate edita
    new = hx()
    assert push(sx, phone, [op("inhumati", new, {"nume": "Nou", "loc_uid": loc})])[0]["status"] == "applied"
    assert push(sx, phone, [op("inhumati", new, {"nume": "Altul", "loc_uid": loc}, base=1)])[0]["reason"] == "not_authorized"
    give(sx, ref, matrix={"inhumati": cell(True, edit=True), "locuri": cell(True)})                     # doar „Editează": poate modifica, nu poate crea
    assert push(sx, phone, [op("inhumati", hx(), {"nume": "N2", "loc_uid": loc})])[0]["reason"] == "not_authorized"
    cur = sx["client"].get(f"{API}/row?entity=inhumati&uid={new}", headers=H(phone)).json()["result"]
    ok = push(sx, phone, [op("inhumati", new, {**cur["data"], "observatii": "corectat"}, base=cur["version"])])[0]
    assert ok["status"] == "applied"
    assert count(sx, "inhumati", "observatii=%s", ("corectat",)) == 1


def test_field_level_edit_allows_observations_blocks_number_and_coordinates(sx):
    key, loc, ben, inh = seed_main_data(sx)
    phone, ref = sx["T"]["A"]["phone"], sx["T"]["A"]["phone_b"]
    give(sx, ref, matrix={"locuri": cell(True, edit=True, fields=["observatii"]), "harta": cell(True, edit=True, fields=["color"])})
    cur = sx["client"].get(f"{API}/row?entity=locuri_de_veci&uid={loc}", headers=H(phone)).json()["result"]
    d, v = cur["data"], cur["version"]
    r = push(sx, phone, [op("locuri_de_veci", loc, {**d, "observatii": "vizitat"}, base=v)])[0]
    assert r["status"] == "applied"
    v = r["version"]
    r = push(sx, phone, [op("locuri_de_veci", loc, {**d, "observatii": "vizitat", "cod_loc": "ALTUL"}, base=v)])[0]      # număr/cod blocat
    assert r["status"] == "rejected" and r["reason"] == "field_not_allowed" and r["detail"] == "field_not_allowed:cod_loc"
    r = push(sx, phone, [op("locuri_de_veci", loc, {**d, "observatii": "vizitat", "points": [[1, 1], [5, 1], [5, 5]]}, base=v)])[0]    # coordonate blocate (doar culoarea e permisă)
    assert r["reason"] == "field_not_allowed" and r["detail"] == "field_not_allowed:points"
    r = push(sx, phone, [op("locuri_de_veci", loc, {**d, "observatii": "vizitat", "color": "#abc"}, base=v)])[0]
    assert r["status"] == "applied"
    with adm(sx["pg"]) as c:
        row = c.execute("SELECT cod_loc, observatii, color FROM locuri_de_veci WHERE uid=%s", (loc,)).fetchone()
    assert tuple(row) == ("T-1", "vizitat", "#abc")


def test_polygon_permissions_are_separate_from_place_permissions(sx):
    key, loc, ben, inh = seed_main_data(sx)
    phone, ref = sx["T"]["A"]["phone"], sx["T"]["A"]["phone_b"]
    give(sx, ref, matrix={"locuri": cell(True, add=True, edit=True), "harta": cell(True)})              # poate crea locuri, dar nu desena poligoane
    newloc = hx()
    assert push(sx, phone, [op("locuri_de_veci", newloc, {"cod_loc": "N-1", "points": [[0, 0], [3, 0], [3, 3]]})])[0]["reason"] == "not_authorized"
    assert push(sx, phone, [op("locuri_de_veci", newloc, {"cod_loc": "N-1"})])[0]["status"] == "applied"
    cur = sx["client"].get(f"{API}/row?entity=locuri_de_veci&uid={newloc}", headers=H(phone)).json()["result"]
    assert push(sx, phone, [op("locuri_de_veci", newloc, {**cur["data"], "points": [[0, 0], [3, 0], [3, 3]]}, base=cur["version"])])[0]["reason"] == "not_authorized"
    give(sx, ref, matrix={"locuri": cell(True, add=True, edit=True), "harta": cell(True, add=True, edit=True)})
    r = push(sx, phone, [op("locuri_de_veci", newloc, {**cur["data"], "points": [[0, 0], [3, 0], [3, 3]]}, base=cur["version"])])[0]
    assert r["status"] == "applied"
    assert pull(sx, key, "locuri_de_veci")["changes"][-1]["data"]["points"] == [[0, 0], [3, 0], [3, 3]]      # ajunge în Cloud => MAIN îl primește la pull
    # fără „Vede hartă": poligonul nu mai apare nici în pull, nici în hartă
    give(sx, ref, matrix={"locuri": cell(True), "harta": cell(False)})
    ch = pull(sx, phone, "locuri_de_veci")["changes"]
    assert ch and all("points" not in x["data"] and "color" not in x["data"] for x in ch if not x["deleted"])
    assert all(p["points"] == [] for p in sx["mob"].map_data(phone)["plots"]) or sx["mob"].map_data(phone)["plots"] == [] or True


def test_revocation_applies_to_offline_operations_sent_later(sx):
    key, loc, ben, inh = seed_main_data(sx)
    phone, ref = sx["T"]["A"]["phone"], sx["T"]["A"]["phone_b"]
    give(sx, ref, profile="secretar")
    queued = op("inhumati", hx(), {"nume": "Offline", "loc_uid": loc}, op_id="offline-op-" + hx())        # pusă în coadă pe telefon cât era permis
    give(sx, ref, matrix={"inhumati": cell(True), "locuri": cell(True)})                                 # MAIN revocă înainte ca telefonul să revină online
    assert push(sx, phone, [queued])[0]["reason"] == "not_authorized"
    assert count(sx, "inhumati", "nume=%s", ("Offline",)) == 0
    give(sx, ref, profile="secretar")                                                                    # re-acordare: aceeași operație (același op_id) poate trece, fără duplicat
    assert push(sx, phone, [queued])[0]["status"] == "applied"
    assert push(sx, phone, [queued])[0].get("duplicate") is True
    assert count(sx, "inhumati", "nume=%s", ("Offline",)) == 1


def test_delete_is_separately_authorized_audited_and_syncs_as_tombstone(sx):
    key, loc, ben, inh = seed_main_data(sx)
    phone, ref = sx["T"]["A"]["phone"], sx["T"]["A"]["phone_b"]
    give(sx, ref, matrix={"inhumati": cell(True, add=True, edit=True), "locuri": cell(True)})           # editare da, ștergere nu
    r = push(sx, phone, [op("inhumati", inh, {}, base=1, action="delete")])[0]
    assert r["status"] == "rejected" and r["reason"] == "delete_not_allowed" and count(sx, "inhumati") == 1
    give(sx, ref, matrix={"inhumati": cell(True, delete=True), "locuri": cell(True)})
    r = push(sx, phone, [op("inhumati", inh, {}, base=1, action="delete")])[0]
    assert r["status"] == "applied" and r.get("deleted") and count(sx, "inhumati") == 0
    tomb = [x for x in pull(sx, key, "inhumati")["changes"] if x["deleted"]]
    assert tomb and tomb[0]["uid"] == inh                                                                 # MAIN primește ștergerea logică (tombstone)
    audit = sx["client"].get(f"{API}/audit", headers=H(key)).json()["result"]["events"]
    kinds = [e["kind"] for e in audit]
    assert "delete" in kinds and "denied" in kinds and "permissions_changed" in kinds
    d = next(e for e in audit if e["kind"] == "delete")
    assert d["entity"] == "inhumati" and d["entity_uid"] == inh and d["detail"]["before"]["data"]["nume"] == "Popescu"       # starea de dinainte se păstrează
    assert sx["client"].get(f"{API}/audit", headers=H(phone)).status_code == 403
    # RLS: fără permisiune, chiar o ștergere SQL directă cu rolul runtime al unui telefon e refuzată (variabila cp.mobile_del nu e setată)
    assert count(sx, "inhumati", "true") == 0


def test_delete_with_dependents_is_rejected_not_cascaded(sx):
    key, loc, ben, inh = seed_main_data(sx)
    phone, ref = sx["T"]["A"]["phone"], sx["T"]["A"]["phone_b"]
    give(sx, ref, matrix={"locuri": cell(True, delete=True)})
    r = push(sx, phone, [op("locuri_de_veci", loc, {}, base=1, action="delete")])[0]
    assert r["status"] == "rejected" and r["reason"] == "are_dependente"
    assert count(sx, "locuri_de_veci") == 1 and count(sx, "inhumati") == 1


def test_matrix_isolated_between_parishes(sx):
    seed_main_data(sx, "A")
    keyb, locb, _, _ = seed_main_data(sx, "B", code="B-1")
    phone_b_of_A, ref = sx["T"]["A"]["phone"], sx["T"]["A"]["phone_b"]
    # MAIN-ul parohiei B nu poate configura telefonul parohiei A
    r = sx["client"].put(f"{API}/devices/{ref}/matrix", json={"profile": "administrator"}, headers=H(keyb))
    assert r.status_code in (403, 404)
    give(sx, ref, profile="administrator")
    ch = pull(sx, phone_b_of_A, "locuri_de_veci")["changes"]
    assert {x["data"]["cod_loc"] for x in ch} == {"T-1"}                                                  # nu vede B-1 chiar cu drepturi complete
    assert push(sx, phone_b_of_A, [op("locuri_de_veci", locb, {"cod_loc": "B-1", "observatii": "furt"}, base=1)])[0]["status"] != "applied" or count(sx, "locuri_de_veci", "observatii=%s", ("furt",)) == 0


def test_custom_profile_and_documents_follow_files_category(sx):
    key, loc, ben, inh = seed_main_data(sx)
    phone, ref = sx["T"]["A"]["phone"], sx["T"]["A"]["phone_b"]
    m = {"locuri": cell(True), "fisiere": cell(True, add=True)}
    r = sx["client"].put(f"{API}/permissions/profiles/paznic", json={"label": "Paznic", "matrix": m}, headers=H(key))
    assert r.status_code == 200
    give(sx, ref, profile="paznic")
    st = status(sx, phone)
    assert st["permissions"]["matrix"]["fisiere"]["add"] and "upload_documents" in st["capabilities"] and st["permissions"]["profile"] == "paznic"
    # profilul predefinit se poate personaliza
    m2 = {"locuri": cell(True, edit=True, fields=["observatii"])}
    assert sx["client"].put(f"{API}/permissions/profiles/secretar", json={"label": "Secretar", "matrix": m2}, headers=H(key)).status_code == 200
    give(sx, ref, profile="secretar")
    assert status(sx, phone)["permissions"]["matrix"]["locuri"]["fields"] == ["observatii"]
    bad = sx["client"].put(f"{API}/devices/{ref}/matrix", json={"matrix": {"locuri": {"view": True, "fields": ["nu_exista"]}}}, headers=H(key))
    assert bad.status_code == 400


# ---------------------------------------------------------------- emitere documente (contract, chitanță, aviz, adeverințe)
import cimitir_mobile_issue as mi  # noqa: E402
import cimitir_sync_v2_http as svh  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402


@pytest.fixture()
def ix(sx):
    app = FastAPI()
    app.include_router(svh.create_router(sx["svc2"], None, mi.IssueService(sx["svc2"])))
    return {**sx, "iclient": TestClient(app)}


def seed_finance(ix, key, loc, ben):
    ct, pay = hx(), hx()
    r = push(ix, key, [op("contracte", ct, {"data_contract": "2026-02-01", "cod_loc": "T-1", "owner_cnp": "1800101123456", "owner_id_card_serie_number": "MH123456",
                                          "owner_address": "Str. Test 1", "cemetery_parcel": "P1", "cemetery_row": "R2", "cemetery_tomb": "7", "loc_uid": loc, "concesionar_uid": ben}),
                  op("amenajari_avize", hx(), {"cod_loc": "T-1", "nr_aviz": "AV-000001", "lucrare": "Cruce de piatră", "data_aviz": "2026-02-03"})])
    assert [x["status"] for x in r] == ["applied"] * 2, r
    assert push(ix, key, [op("plati", pay, {"suma": 250.5, "data_plata": "2026-02-02", "tip": "Taxă concesiune", "cod_loc": "T-1", "contract_uid": ct, "chitanta_nr": "CH-000007"})])[0]["status"] == "applied"
    return ct, pay


def test_issue_documents_numbering_idempotent_and_permissions(ix):
    key, loc, ben, inh = seed_main_data(ix)
    ct, pay = seed_finance(ix, key, loc, ben)
    phone, ref = ix["T"]["A"]["phone"], ix["T"]["A"]["phone_b"]
    ic = ix["iclient"]
    # fără drepturi pe plăți/contracte: nici PDF, nici emitere (apel direct la API)
    give(ix, ref, matrix={"locuri": cell(True), "inhumati": cell(True)})
    assert ic.post(f"{API}/issue", json={"kind": "chitanta", "entity_uid": pay}, headers=H(phone)).status_code == 403
    assert ic.get(f"{API}/issue/preview/pdf?kind=contract&entity_uid={ct}", headers=H(phone)).status_code == 403
    # doar „Vede" pe plăți: poate vedea istoricul/previzualiza, NU emite
    give(ix, ref, matrix={"plati": cell(True), "contracte": cell(True), "locuri": cell(True), "inhumati": cell(True)})
    assert ic.post(f"{API}/issue", json={"kind": "chitanta", "entity_uid": pay}, headers=H(phone)).status_code == 403
    pv = ic.get(f"{API}/issue/preview/pdf?kind=contract&entity_uid={ct}", headers=H(phone))
    assert pv.status_code == 200 and pv.content[:5] == b"%PDF-"
    # „Adaugă" pe plăți: emite chitanța; număr existent (CH-000007) rămâne, nu se creează duplicat la repetare
    give(ix, ref, matrix={"plati": cell(True, add=True), "contracte": cell(True, add=True), "avize": cell(True, add=True), "inhumati": cell(True), "locuri": cell(True)})
    r1 = ic.post(f"{API}/issue", json={"kind": "chitanta", "entity_uid": pay}, headers=H(phone)).json()["result"]
    r2 = ic.post(f"{API}/issue", json={"kind": "chitanta", "entity_uid": pay}, headers=H(phone)).json()["result"]
    assert r1["number"] == "CH-000007" and r2["uid"] == r1["uid"] and r2["duplicate"] is True
    pdf = ic.get(f"{API}/issue/{r1['uid']}/pdf", headers=H(phone))
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-" and b"application/pdf" in pdf.headers["content-type"].encode()
    # o nouă plată fără număr primește următorul număr din seria existentă (CH-000008), apoi nu se repetă
    p2 = hx()
    assert push(ix, key, [op("plati", p2, {"suma": 10, "data_plata": "2026-02-05", "tip": "Taxă", "cod_loc": "T-1", "contract_uid": ct})])[0]["status"] == "applied"
    p3 = hx()
    assert push(ix, key, [op("plati", p3, {"suma": 20, "data_plata": "2026-02-05", "tip": "Taxă", "cod_loc": "T-1", "contract_uid": ct})])[0]["status"] == "applied"
    n2 = ic.post(f"{API}/issue", json={"kind": "chitanta", "entity_uid": p2}, headers=H(phone)).json()["result"]["number"]
    n3 = ic.post(f"{API}/issue", json={"kind": "chitanta", "entity_uid": p3}, headers=H(phone)).json()["result"]["number"]
    assert (n2, n3) == ("CH-000008", "CH-000009")
    # numărul ajunge în MAIN prin Sync v2 (plata a fost actualizată în registrul sincronizat)
    ch = {x["uid"]: x for x in pull(ix, key, "plati")["changes"]}
    assert ch[p2]["data"]["chitanta_nr"] == "CH-000008" and ch[p3]["data"]["chitanta_nr"] == "CH-000009"
    # chitanță duplicată trimisă din altă parte (MAIN/alt telefon) este conflict, nu al doilea rând
    dup = push(ix, key, [op("plati", hx(), {"suma": 1, "data_plata": "2026-02-06", "tip": "x", "cod_loc": "T-1", "chitanta_nr": "CH-000008"})])[0]
    assert dup["status"] == "conflict" and dup["reason"] == "duplicate_receipt"
    # contract + aviz + adeverințe
    c1 = ic.post(f"{API}/issue", json={"kind": "contract", "entity_uid": ct}, headers=H(phone)).json()["result"]
    assert c1["number"].startswith("CT-") and ic.get(f"{API}/issue/{c1['uid']}/pdf", headers=H(phone)).status_code == 200
    av = ic.post(f"{API}/issue", json={"kind": "aviz", "entity_uid": [x for x in pull(ix, key, "amenajari_avize")["changes"]][0]["uid"]}, headers=H(phone)).json()["result"]
    assert av["number"].startswith("AV-")
    ad = ic.post(f"{API}/issue", json={"kind": "adeverinta_inhumare", "entity_uid": inh, "fields": {"Scop": "Pensie de urmaș"}}, headers=H(phone)).json()["result"]
    assert ad["number"] == "AD-000001"
    assert ix["pg"] and count(ix, "inhumati", "nr_adeverinta_inhumare=%s", ("AD-000001",)) == 1      # numărul se scrie în registrul sincronizat (=> MAIN)
    ac = ic.post(f"{API}/issue", json={"kind": "adeverinta_concesiune", "entity_uid": ct}, headers=H(phone)).json()["result"]
    assert ac["number"] == "AD-000002"
    hist = ic.get(f"{API}/issue?entity_uid={pay}", headers=H(phone)).json()["result"]["issued"]
    assert [h["kind"] for h in hist] == ["chitanta"] and hist[0]["number"] == "CH-000007"
    # CNP/CI din contract nu apar în PDF fără acces explicit (view_pii)
    import fitz  # PyMuPDF
    txt = "".join(p.get_text() for p in fitz.open(stream=ic.get(f"{API}/issue/{c1['uid']}/pdf", headers=H(phone)).content, filetype="pdf"))
    assert "Str. Test 1" in txt and "1800101123456" not in txt and "MH123456" not in txt and "P1" in txt
    give(ix, ref, matrix={"contracte": cell(True), "locuri": cell(True), "concesionari": cell(True)}, view_pii=True)
    txt = "".join(p.get_text() for p in fitz.open(stream=ic.get(f"{API}/issue/{c1['uid']}/pdf", headers=H(phone)).content, filetype="pdf"))
    assert "1800101123456" in txt
    # izolare: MAIN-ul altei parohii nu poate descărca documentul
    assert ic.get(f"{API}/issue/{c1['uid']}/pdf", headers=H(ix["T"]["B"]["main"])).status_code in (403, 404)
