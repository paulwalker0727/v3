"""Etapa 4 / Pasul 2 — manifest canonic + preflight (read-only) + invalidarea planului. Doar foldere temporare și control-plane SQLite local.
NU execută B/C/D/E; NU atinge Supabase/Render/producție/Banovița; fără endpoint-uri HTTP.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path

import pytest

import cimitir_entities as ents
import cimitir_manifest as mf
import cimitir_preflight as pf
from test_etapa4_pas1 import World, env  # noqa: F401  (fixture `env` reutilizat)

NS = uuid.UUID("12345678-1234-5678-1234-567812345678")
LEAKS = []  # accese cross-tenant reușite (trebuie să rămână 0)

DATA = {
    "locuri_de_veci": [{"cod_loc": "A-1", "sector": "A", "parcela": "1", "suprafata": 2.5, "points": "[[0,0],[1,1]]", "ocupat": 1},
                       {"cod_loc": "A-2", "sector": "A", "parcela": "1", "suprafata": 3.0, "points": "[]", "ocupat": 0},
                       {"cod_loc": "B-1", "sector": "B", "parcela": "2", "suprafata": 2.0, "points": "[]", "ocupat": 0}],
    "concesionari": [{"nume": "Popescu", "prenume": "Ion", "cnp": "1800101000001"}, {"nume": "Ionescu", "prenume": "Maria", "cnp": "2800101000002"}],
    "contracte": [{"numar_contract": "C1", "cod_loc": "A-1", "owner_fullname": "Popescu Ion", "_conc": "Popescu", "_loc": "A-1"},
                  {"numar_contract": "C2", "cod_loc": "B-1", "owner_fullname": "Ionescu Maria", "_conc": "Ionescu", "_loc": "B-1"}],
    "inhumati": [{"nume": "Popescu", "prenume": "Vasile", "_loc": "A-1", "_contract": "C1"}, {"nume": "Popescu", "prenume": "Ana", "_loc": "A-1", "_contract": "C1"}],
    "plati": [{"cod_loc": "A-1", "suma": 100.0, "data_plata": "2024-01-01", "created_at": "2024-01-01", "_contract": "C1"}],
    "amenajari_avize": [{"cod_loc": "A-1", "nr_aviz": "7", "data_aviz": "2024-02-02", "created_at": "2024-02-02", "lucrare": "soclu"}],
}
KEYS = {"locuri_de_veci": "cod_loc", "concesionari": "nume", "contracte": "numar_contract"}


def put_local(b, data=DATA, reverse=False, uids=False):
    c = sqlite3.connect(b.database)
    c.execute("PRAGMA foreign_keys=ON")
    ids = {}

    def ins(table, rows):
        for r in (reversed(rows) if reverse else rows):
            cols = {k: v for k, v in r.items() if not k.startswith("_")}
            if table == "contracte":
                cols["concesionar_id"] = ids[("concesionari", r["_conc"])]
                cols["id_loc"] = ids[("locuri_de_veci", r["_loc"])]
            if table == "inhumati":
                cols["id_loc"] = ids[("locuri_de_veci", r["_loc"])]
                cols["contract_id"] = ids[("contracte", r["_contract"])]
            if table == "plati":
                cols["contract_id"] = ids[("contracte", r["_contract"])]
            cur = c.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))
            if table in KEYS:
                ids[(table, r[KEYS[table]])] = cur.lastrowid

    for t in ents.ORDER:
        if t in data:
            ins(t, data[t])
    c.commit()
    c.close()


def sql(b, query, *args):
    c = sqlite3.connect(b.database)
    try:
        r = c.execute(query, args).fetchall()
        c.commit()
        return r
    finally:
        c.close()


def mirror_cloud(env, w, b, status="DRAFT", owner=None):
    """Plasează în cloud-ul simulat exact datele locale (cu uid-uri), ca după un Flux B reușit."""
    try:
        env.cp.create_cemetery(owner or w.owner1, b.organization_uid, b.cemetery_uid, "C")
    except env.cc.ConflictError:
        pass
    snap = mf.local_snapshot(b)
    uid = lambda ent, ref: str(uuid.uuid5(NS, f"{b.cemetery_uid}:{ent}:{ref}"))  # noqa: E731
    for ent in ents.ORDER:
        for r in snap["entities"].get(ent, []):
            d = dict(r["data"])
            for wire, (parent, pref) in r["fks"].items():
                d[wire] = uid(parent, pref) if pref else None
            env.cp.sim_put_row(b.cemetery_uid, ent, uid(ent, r["ref"]), d)
    for i, m in enumerate(snap["media"]):
        env.cp.sim_put_media(b.cemetery_uid, f"m{i}", m["name"], m["size"] or 0, m["sha256"] or "", m["kind"], m["entity_type"], m["entity_id"], present=m["present"])
    env.cp.sim_set_status(b.cemetery_uid, status)


def add_file(b, name, content: bytes, doc=True, rel=True):
    b.media.mkdir(parents=True, exist_ok=True)
    f = b.media / name
    f.write_bytes(content)
    if doc:
        sql(b, "INSERT INTO documents (entity_type, entity_id, doc_type, file_path, sha256, size) VALUES (?,?,?,?,?,?)",
            "loc", "A-1", "poza", name if rel else str(f), hashlib.sha256(content).hexdigest(), len(content))
    return f


def man(b):
    return mf.local_manifest(b)


def tree_state(root):
    out = {}
    for f in sorted(Path(root).rglob("*")):
        st = f.stat()
        out[str(f)] = (st.st_size, st.st_mtime_ns, hashlib.sha256(f.read_bytes()).hexdigest() if f.is_file() else "dir")
    return out


# ================================================================== MANIFEST CANONIC + DIGESTE PE ENTITĂȚI
def test_canonical_manifest_repeat_is_identical_and_generated_at_excluded(env):
    w = World(env)
    b = w.local(w.org1)
    put_local(b)
    m1 = man(b)
    time.sleep(0.01)
    m2 = man(b)
    assert m1["generated_at"] != m2["generated_at"] and m1["stable_digest"] == m2["stable_digest"]
    assert {k: v for k, v in m1.items() if k != "generated_at"} == {k: v for k, v in m2.items() if k != "generated_at"}
    for k in ("organization_uid", "cemetery_uid", "data_epoch", "schema_version", "app_version", "protocol_version", "cemetery_status", "entities",
              "entities_digest", "media", "integrity", "outbox", "open_conflicts", "change_seq", "generated_at", "stable_digest", "classification"):
        assert k in m1
    assert set(m1["entities"]) == set(ents.ORDER) and all(m1["entities"][e]["digest"] and "count" in m1["entities"][e] for e in ents.ORDER)
    assert {"count", "bytes", "digest", "missing", "duplicates", "items"} <= set(m1["media"])
    assert m1["entities"]["locuri_de_veci"]["count"] == 3 and m1["total_rows"] == 11


def test_row_physical_order_and_local_ids_do_not_change_digest(env):
    w = World(env)
    a, b = w.local(w.org1, "A"), w.local(w.org1, "B")
    put_local(a)
    put_local(b, reverse=True)
    assert [r[0] for r in sql(a, "SELECT cod_loc FROM locuri_de_veci ORDER BY id")] != [r[0] for r in sql(b, "SELECT cod_loc FROM locuri_de_veci ORDER BY id")]
    ma, mb = man(a), man(b)
    assert ma["entities"] == mb["entities"] and ma["entities_digest"] == mb["entities_digest"] and ma["content_digest"] == mb["content_digest"]
    assert ma["stable_digest"] != mb["stable_digest"]  # alt cemetery_uid => alt manifest complet, același conținut


@pytest.mark.parametrize("entity,where,col", [("locuri_de_veci", "cod_loc='A-2'", "sector"), ("concesionari", "nume='Ionescu'", "telefon"),
                                              ("contracte", "numar_contract='C2'", "observatii"), ("inhumati", "prenume='Ana'", "nume"),
                                              ("plati", "suma=100.0", "suma"), ("amenajari_avize", "nr_aviz='7'", "lucrare")])
def test_single_field_change_changes_entity_digest_only_where_expected(env, entity, where, col):
    w = World(env)
    b = w.local(w.org1)
    put_local(b)
    before = man(b)
    sql(b, f"UPDATE {entity} SET {col}=? WHERE {where}", 999.5 if col == "suma" else "SCHIMBAT")
    after = man(b)
    assert after["entities"][entity]["digest"] != before["entities"][entity]["digest"]
    assert after["entities"][entity]["count"] == before["entities"][entity]["count"]
    assert after["entities_digest"] != before["entities_digest"] and after["stable_digest"] != before["stable_digest"]
    for other in ("concesionari", "amenajari_avize"):  # entități fără dependență de cea modificată rămân identice
        if other != entity:
            assert after["entities"][other]["digest"] == before["entities"][other]["digest"]


def test_foreign_key_change_and_parent_change_propagate_through_content_hash(env):
    w = World(env)
    b = w.local(w.org1)
    put_local(b)
    before = man(b)
    sql(b, "UPDATE inhumati SET id_loc=(SELECT id FROM locuri_de_veci WHERE cod_loc='B-1') WHERE prenume='Ana'")
    mid = man(b)
    assert mid["entities"]["inhumati"]["digest"] != before["entities"]["inhumati"]["digest"]
    assert mid["entities"]["locuri_de_veci"]["digest"] == before["entities"]["locuri_de_veci"]["digest"]
    sql(b, "UPDATE concesionari SET prenume='Alt' WHERE nume='Popescu'")  # părintele se schimbă => contractul (FK către conținutul lui) se schimbă
    after = man(b)
    assert after["entities"]["contracte"]["digest"] != mid["entities"]["contracte"]["digest"]


def test_uid_digest_only_when_all_rows_have_uid_and_content_digest_ignores_uid_and_version(env):
    w = World(env)
    b = w.local(w.org1)
    put_local(b)
    m0 = man(b)
    assert m0["entities"]["concesionari"]["uid_digest"] is None  # fără uid pe disc: citirea NU atribuie uid-uri
    assert sql(b, "SELECT COUNT(*) FROM concesionari WHERE uid IS NOT NULL")[0][0] == 0 if "uid" in {r[1] for r in sql(b, "PRAGMA table_info(concesionari)")} else True
    sql(b, "UPDATE locuri_de_veci SET uid='u-'||id, sync_version=7")
    m1 = man(b)
    assert m1["entities"]["locuri_de_veci"]["uid_digest"] and m1["entities"]["locuri_de_veci"]["digest"] == m0["entities"]["locuri_de_veci"]["digest"]
    sql(b, "UPDATE locuri_de_veci SET uid='x-'||id")
    assert man(b)["entities"]["locuri_de_veci"]["uid_digest"] != m1["entities"]["locuri_de_veci"]["uid_digest"]


def test_orphan_and_fk_status_reported(env):
    w = World(env)
    b = w.local(w.org1)
    put_local(b)
    assert man(b)["integrity"]["orphan_total"] == 0 and man(b)["integrity"]["fk_check_violations"] == 0
    sql(b, "UPDATE contracte SET id_loc=9999 WHERE numar_contract='C1'")
    m = man(b)
    assert m["integrity"]["orphans"]["contracte"] == 1 and m["integrity"]["fk_check_violations"] >= 1


def test_local_and_cloud_same_data_same_content_digest_and_cloud_scope(env):
    w = World(env)
    b = w.local(w.org1)
    put_local(b)
    mirror_cloud(env, w, b)
    ml, mc = man(b), mf.cloud_manifest(env.cp, w.owner1, b.organization_uid, b.cemetery_uid)
    assert ml["entities"]["locuri_de_veci"]["digest"] == mc["entities"]["locuri_de_veci"]["digest"]
    assert ml["content_digest"] == mc["content_digest"] and mc["cemetery_status"] == "DRAFT" and mc["data_epoch"] == 1 and mc["side"] == "cloud"
    assert mc["entities"]["contracte"]["uid_digest"] and mc["change_seq"] and mc["classification"] == "NONEMPTY"


# ================================================================== MEDIA
def test_media_manifest_add_remove_missing_duplicates_mismatch(env):
    w = World(env)
    b = w.local(w.org1)
    put_local(b)
    m0 = man(b)["media"]
    assert m0["count"] == 0 and m0["bytes"] == 0
    f1 = add_file(b, "a.jpg", b"AAAA")
    m1 = man(b)["media"]
    assert m1["count"] == 1 and m1["bytes"] == 4 and m1["digest"] != m0["digest"] and m1["items"][0]["sha256"] == hashlib.sha256(b"AAAA").hexdigest()
    add_file(b, "b.jpg", b"AAAA")  # același conținut, alt nume => duplicat după hash
    m2 = man(b)["media"]
    assert m2["count"] == 2 and m2["bytes"] == 8 and [d["count"] for d in m2["duplicates"]] == [2] and m2["digest"] != m1["digest"]
    f1.unlink()  # fișier lipsă pe disc
    m3 = man(b)["media"]
    assert m3["missing"] == 1 and any(i["status"] == "missing" and i["name"] == "a.jpg" for i in m3["items"]) and m3["bytes"] == 4 and m3["digest"] != m2["digest"]
    add_file(b, "a.jpg", b"CHANGED!", doc=False)  # conținut diferit față de sha-ul înregistrat
    m4 = man(b)["media"]
    assert m4["mismatch"] == 1 and any(i["status"] == "hash_mismatch" for i in m4["items"])
    sql(b, "DELETE FROM documents WHERE file_path='a.jpg'")
    m5 = man(b)["media"]
    assert m5["count"] == 1 and m5["digest"] != m4["digest"]
    sql(b, "DELETE FROM documents")
    assert man(b)["media"]["digest"] == m0["digest"] and man(b)["media"]["count"] == 0


def test_media_content_name_and_absolute_path_and_photos(env):
    w = World(env)
    b = w.local(w.org1)
    put_local(b)
    f = add_file(b, "x.pdf", b"PDF-1", rel=False)  # cale absolută
    base = man(b)["media"]
    assert base["count"] == 1 and base["items"][0]["status"] == "ok"
    f.write_bytes(b"PDF-2")
    sql(b, "UPDATE documents SET sha256=?, size=5", hashlib.sha256(b"PDF-2").hexdigest())
    assert man(b)["media"]["digest"] != base["digest"]
    sql(b, "INSERT INTO photos (cod_loc, file_path) VALUES ('A-1', 'poza.jpg')")
    m = man(b)["media"]
    assert m["count"] == 2 and any(i["kind"] == "photo" and i["status"] == "missing" for i in m["items"])


def test_media_cloud_vs_local_same_files_same_digest_missing_cloud_object_differs(env):
    w = World(env)
    b = w.local(w.org1)
    put_local(b)
    add_file(b, "a.jpg", b"AAAA")
    add_file(b, "b.jpg", b"BBBBBB")
    mirror_cloud(env, w, b)
    ml, mc = man(b), mf.cloud_manifest(env.cp, w.owner1, b.organization_uid, b.cemetery_uid)
    assert ml["media"]["digest"] == mc["media"]["digest"] and ml["media"]["bytes"] == mc["media"]["bytes"] == 10 and ml["content_digest"] == mc["content_digest"]
    env.cp.sim_put_media(b.cemetery_uid, "m0", "a.jpg", 4, hashlib.sha256(b"AAAA").hexdigest(), present=False)
    mc2 = mf.cloud_manifest(env.cp, w.owner1, b.organization_uid, b.cemetery_uid)
    assert mc2["media"]["missing"] == 1 and mc2["media"]["digest"] != ml["media"]["digest"]


# ================================================================== CLASIFICARE
def test_classification_empty_metadata_only_nonempty_local(env):
    w = World(env)
    b = w.local(w.org1)
    assert man(b)["classification"] == "EMPTY" and man(b)["total_rows"] == 0
    c = sqlite3.connect(b.database)
    c.execute("CREATE TABLE cloud_sync_outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, state TEXT)")
    c.execute("INSERT INTO cloud_sync_outbox (state) VALUES ('pending')")
    c.commit()
    c.close()
    m = man(b)
    assert m["classification"] == "METADATA_ONLY" and m["outbox"] == {"total": 1, "pending": 1, "conflict": 0, "error": 0}
    sql(b, "UPDATE cloud_sync_outbox SET state='conflict'")
    assert man(b)["open_conflicts"] == 1 and man(b)["classification"] == "METADATA_ONLY"
    sql(b, "DELETE FROM cloud_sync_outbox")
    assert man(b)["classification"] == "EMPTY"
    sql(b, "INSERT INTO locuri_de_veci (cod_loc) VALUES ('Z-1')")
    assert man(b)["classification"] == "NONEMPTY"
    sql(b, "DELETE FROM locuri_de_veci")
    add_file(b, "only-media.bin", b"1")
    assert man(b)["classification"] == "NONEMPTY"  # doar media = nevid


def test_classification_cloud_nonexistent_empty_metadata_only_nonempty(env):
    w = World(env)
    b = w.local(w.org1)
    mc = lambda: mf.cloud_manifest(env.cp, w.owner1, b.organization_uid, b.cemetery_uid)  # noqa: E731
    assert mc()["exists"] is False and mc()["classification"] == "EMPTY" and mc()["cemetery_status"] is None
    env.cp.create_cemetery(w.owner1, w.org1, b.cemetery_uid, "C")
    assert mc()["exists"] is True and mc()["classification"] == "EMPTY" and mc()["cemetery_status"] == "DRAFT"
    env.cp.sim_put_row(b.cemetery_uid, "locuri_de_veci", "u1", {"cod_loc": "A-1"})
    assert mc()["classification"] == "NONEMPTY"
    env.cp.sim_delete_row(b.cemetery_uid, "locuri_de_veci", "u1")
    assert mc()["classification"] == "METADATA_ONLY" and mc()["tombstones"] == 1
    c = sqlite3.connect(env.cp.path)
    c.execute("DELETE FROM tombstones")
    c.commit()
    c.close()
    env.cp.sim_add_conflict(b.cemetery_uid, "k")
    assert mc()["classification"] == "METADATA_ONLY" and mc()["open_conflicts"] == 1


# ================================================================== MATRICEA PREFLIGHT (decide, pur) + INTEGRARE
def fake(cls, status=None, exists=True, state="UNBOUND", content="x"):
    return {"classification": cls, "cemetery_status": status, "exists": exists, "sync_state": state, "content_digest": content}


@pytest.mark.parametrize("local,cloud,expected", [
    (fake("EMPTY"), fake("EMPTY", exists=False), "A"),
    (fake("EMPTY"), fake("EMPTY", "DRAFT"), "A"),
    (fake("EMPTY"), fake("EMPTY", "ACTIVE"), "BLOCKED_ACTIVE_EMPTY"),
    (fake("NONEMPTY"), fake("EMPTY", "ACTIVE"), "BLOCKED_ACTIVE_EMPTY"),
    (fake("NONEMPTY"), fake("EMPTY", "DRAFT"), "B"),
    (fake("NONEMPTY"), fake("EMPTY", exists=False), "B"),
    (fake("EMPTY"), fake("NONEMPTY", "ACTIVE"), "C"),
    (fake("EMPTY"), fake("NONEMPTY", "DRAFT"), "C"),
    (fake("NONEMPTY", content="s"), fake("NONEMPTY", "ACTIVE", content="s"), "LINK_CANDIDATE"),
    (fake("NONEMPTY", content="s"), fake("NONEMPTY", "ACTIVE", content="t"), "BLOCK_DIFFERENT"),
    (fake("METADATA_ONLY"), fake("EMPTY", "DRAFT"), "REVIEW_METADATA_ONLY"),
    (fake("EMPTY"), fake("METADATA_ONLY", "DRAFT"), "REVIEW_METADATA_ONLY"),
    (fake("NONEMPTY"), fake("METADATA_ONLY", "ACTIVE"), "REVIEW_METADATA_ONLY"),
    (fake("EMPTY"), fake("EMPTY", "ARCHIVED"), "BLOCKED_ARCHIVED"),
    (fake("NONEMPTY"), fake("NONEMPTY", "ARCHIVED"), "BLOCKED_ARCHIVED"),
    (fake("NONEMPTY", state="BOUND"), fake("EMPTY", "ACTIVE"), "BLOCKED_ALARM"),
    (fake("NONEMPTY", state="BOUND"), fake("NONEMPTY", "ACTIVE"), "ALREADY_BOUND"),
    (fake("EMPTY", state="BOUND_MEDIA_PENDING"), fake("EMPTY", "ACTIVE"), "ALREADY_BOUND"),
    (fake("EMPTY", state="REBUILD_REQUIRED"), fake("NONEMPTY", "ACTIVE"), "REBUILD"),
    (fake("EMPTY", state="BOOTSTRAPPING"), fake("EMPTY", "DRAFT"), "BLOCKED_INCOMPLETE"),
])
def test_preflight_matrix_decide(local, cloud, expected):
    assert pf.decide(local, cloud)["decision"] == expected


def scenario(env, w, b, local, cloud):
    """Construiește starea reală (local/cloud) și întoarce planul."""
    if local == "NONEMPTY":
        put_local(b)
    if cloud in ("DRAFT-EMPTY", "ACTIVE-EMPTY", "ARCHIVED-EMPTY"):
        env.cp.create_cemetery(w.owner1, w.org1, b.cemetery_uid, "C")
        env.cp.sim_set_status(b.cemetery_uid, cloud.split("-")[0])
    elif cloud in ("SAME", "DIFF"):
        if local != "NONEMPTY":
            put_local(b)
            mirror_cloud(env, w, b, status="ACTIVE")
            sql(b, "DELETE FROM concesionari WHERE 1=0")
            for t in reversed(ents.ORDER):
                sql(b, f"DELETE FROM {t}")
        else:
            mirror_cloud(env, w, b, status="ACTIVE")
        if cloud == "DIFF":
            env.cp.sim_put_row(b.cemetery_uid, "locuri_de_veci", "extra", {"cod_loc": "ZZ"})
    return pf.build_plan(b, env.cp, w.owner1, env.inst, clock=env.clock)


@pytest.mark.parametrize("local,cloud,expected,flow,executable", [
    ("EMPTY", "NONE", "A", "A", True), ("EMPTY", "DRAFT-EMPTY", "A", "A", True), ("EMPTY", "ACTIVE-EMPTY", "BLOCKED_ACTIVE_EMPTY", None, False),
    ("NONEMPTY", "ACTIVE-EMPTY", "BLOCKED_ACTIVE_EMPTY", None, False), ("NONEMPTY", "DRAFT-EMPTY", "B", "B", False), ("NONEMPTY", "NONE", "B", "B", False),
    ("EMPTY", "SAME", "C", "C", False), ("NONEMPTY", "SAME", "LINK_CANDIDATE", "LINK", False), ("NONEMPTY", "DIFF", "BLOCK_DIFFERENT", None, False),
    ("EMPTY", "ARCHIVED-EMPTY", "BLOCKED_ARCHIVED", None, False),
])
def test_preflight_matrix_integration_with_real_manifests(env, local, cloud, expected, flow, executable):
    w = World(env)
    b = w.local(w.org1)
    plan = scenario(env, w, b, local, cloud)
    assert (plan["decision"], plan["recommended_flow"], plan["executable"]) == (expected, flow, executable), plan["reason"]
    assert plan["local_manifest"]["classification"] == ("NONEMPTY" if local == "NONEMPTY" else "EMPTY")
    assert plan["plan_hash"] and plan["local_manifest"]["stable_digest"] and plan["cloud_manifest"]["stable_digest"]  # manifestele sunt păstrate în plan


def test_active_empty_stays_blocked_after_real_flux_a_and_unbind(env):
    w = World(env)
    b = w.local(w.org1)
    code = env.cp.issue_code(w.owner1, w.org1)
    plan1 = env.bs.plan_flow_a(b, env.cp, code, env.inst, clock=env.clock)
    env.bs.execute_flow_a(b, env.cp, env.store, plan1, plan1["plan_hash"], code, env.inst, clock=env.clock)
    env.bs.unbind(b, env.cp, env.store, w.owner1)
    p = pf.build_plan(b, env.cp, w.owner1, env.inst, clock=env.clock)
    assert p["decision"] == "BLOCKED_ACTIVE_EMPTY" and not p["executable"] and p["cloud_manifest"]["cemetery_status"] == "ACTIVE"


# ================================================================== INVALIDAREA PLANULUI
def fresh_plan(env, w, b, local="NONEMPTY", cloud="DRAFT-EMPTY"):
    return scenario(env, w, b, local, cloud)


def test_plan_stays_valid_when_nothing_changes_and_gate_blocks_unimplemented_flows(env):
    w = World(env)
    b = w.local(w.org1)
    plan = fresh_plan(env, w, b)  # decizia B (recomandare)
    pf.validate_plan(plan, b, env.cp, w.owner1, clock=env.clock)
    with pytest.raises(pf.PreflightError):
        pf.gate_execution(plan, b, env.cp, w.owner1, "0" * 64, clock=env.clock)
    with pytest.raises(pf.FlowNotImplemented):
        pf.gate_execution(plan, b, env.cp, w.owner1, plan["plan_hash"], clock=env.clock)
    pa = scenario(env, w, w.local(w.org1, "gol"), "EMPTY", "NONE")
    assert pa["executable"]


def test_plan_gate_passes_for_flow_a_and_refuses_after_any_change(env):
    w = World(env)
    b = w.local(w.org1)
    plan = pf.build_plan(b, env.cp, w.owner1, env.inst, clock=env.clock)
    assert plan["decision"] == "A"
    pf.gate_execution(plan, b, env.cp, w.owner1, plan["plan_hash"], clock=env.clock)
    sql(b, "INSERT INTO locuri_de_veci (cod_loc) VALUES ('NOU')")
    with pytest.raises(pf.PlanInvalidated) as e:
        pf.gate_execution(plan, b, env.cp, w.owner1, plan["plan_hash"], clock=env.clock)
    assert "local:entities.locuri_de_veci" in e.value.changed and "generează" in str(e.value)


@pytest.mark.parametrize("mutation,expect", [
    ("local_row", "local:entities.locuri_de_veci"), ("local_field", "local:entities.locuri_de_veci"), ("local_media", "local:media"),
    ("local_state", "local:sync_state"), ("cloud_row", "cloud:entities.locuri_de_veci"), ("cloud_status", "cloud:cemetery_status"),
    ("cloud_media", "cloud:media"), ("cloud_epoch", "cloud:data_epoch"), ("cloud_tombstone", "cloud:tombstones"), ("cloud_conflict", "cloud:open_conflicts"),
    ("local_outbox", "local:outbox"),
])
def test_plan_invalidated_when_source_or_target_changes(env, mutation, expect):
    w = World(env)
    b = w.local(w.org1)
    put_local(b)
    mirror_cloud(env, w, b, status="DRAFT")
    plan = pf.build_plan(b, env.cp, w.owner1, env.inst, clock=env.clock)
    pf.validate_plan(plan, b, env.cp, w.owner1, clock=env.clock)
    if mutation == "local_row":
        sql(b, "INSERT INTO locuri_de_veci (cod_loc) VALUES ('NOU')")
    elif mutation == "local_field":
        sql(b, "UPDATE locuri_de_veci SET sector='Q' WHERE cod_loc='A-2'")
    elif mutation == "local_media":
        add_file(b, "n.jpg", b"1")
    elif mutation == "local_state":
        sql(b, "INSERT OR REPLACE INTO settings (key, value) VALUES ('sync_state','PLANNED')")
    elif mutation == "local_outbox":
        sql(b, "CREATE TABLE cloud_sync_outbox (id INTEGER PRIMARY KEY, state TEXT)")
        sql(b, "INSERT INTO cloud_sync_outbox (state) VALUES ('pending')")
    elif mutation == "cloud_row":
        env.cp.sim_put_row(b.cemetery_uid, "locuri_de_veci", "zz", {"cod_loc": "ZZ"})
    elif mutation == "cloud_status":
        env.cp.sim_set_status(b.cemetery_uid, "ACTIVE")
    elif mutation == "cloud_media":
        env.cp.sim_put_media(b.cemetery_uid, "mm", "x.jpg", 1, "ab")
    elif mutation == "cloud_epoch":
        env.cp.sim_set_status(b.cemetery_uid, "DRAFT", data_epoch=2)
    elif mutation == "cloud_tombstone":
        env.cp.sim_put_row(b.cemetery_uid, "plati", "t1", {"suma": 1.0})
        env.cp.sim_delete_row(b.cemetery_uid, "plati", "t1")
    elif mutation == "cloud_conflict":
        env.cp.sim_add_conflict(b.cemetery_uid, "c1")
    with pytest.raises(pf.PlanInvalidated) as e:
        pf.validate_plan(plan, b, env.cp, w.owner1, clock=env.clock)
    assert expect in e.value.changed, e.value.changed
    assert plan["plan_hash"] != pf.build_plan(b, env.cp, w.owner1, env.inst, clock=env.clock)["plan_hash"]


def test_plan_expires_and_reverting_change_makes_manifest_equal_again(env):
    w = World(env)
    b = w.local(w.org1)
    put_local(b)
    plan = pf.build_plan(b, env.cp, w.owner1, env.inst, clock=env.clock)
    env.clock.t += pf.PLAN_TTL + 1
    with pytest.raises(pf.PlanExpired):
        pf.validate_plan(plan, b, env.cp, w.owner1, clock=env.clock)
    env.clock.t -= pf.PLAN_TTL + 1
    sql(b, "UPDATE locuri_de_veci SET sector='Q' WHERE cod_loc='A-2'")
    with pytest.raises(pf.PlanInvalidated):
        pf.validate_plan(plan, b, env.cp, w.owner1, clock=env.clock)
    sql(b, "UPDATE locuri_de_veci SET sector='A' WHERE cod_loc='A-2'")
    pf.validate_plan(plan, b, env.cp, w.owner1, clock=env.clock)  # starea revenită = același conținut


# ================================================================== ZERO WRITES
def test_preflight_manifest_and_validation_make_zero_writes(env):
    w = World(env)
    b = w.local(w.org1)
    put_local(b)
    add_file(b, "a.jpg", b"AAAA")
    mirror_cloud(env, w, b)
    tables_before = sql(b, "SELECT name FROM sqlite_master ORDER BY name")
    settings_before = sql(b, "SELECT key, value FROM settings ORDER BY key")
    audit_before = [sqlite3.connect(env.cp.path).execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("audit_log", "bindings", "devices", "bootstrap_runs", "pairing_codes")]
    cat_before = (env.cat.get_state("installation_uid"), len(env.cat.list_cemeteries(include_archived=True)))
    time.sleep(0.05)
    before = tree_state(env.tmp)  # după interogările de bază ale testului (care deschid conexiuni scriibile)
    plan = pf.build_plan(b, env.cp, w.owner1, env.inst, clock=env.clock)
    pf.validate_plan(plan, b, env.cp, w.owner1, clock=env.clock)
    mf.local_manifest(b), mf.cloud_manifest(env.cp, w.owner1, w.org1, b.cemetery_uid)
    assert tree_state(env.tmp) == before  # niciun fișier creat/modificat/șters (conținut, mtime, listă) — nici -wal/-shm/-journal
    assert sql(b, "SELECT name FROM sqlite_master ORDER BY name") == tables_before and sql(b, "SELECT key, value FROM settings ORDER BY key") == settings_before
    assert [sqlite3.connect(env.cp.path).execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("audit_log", "bindings", "devices", "bootstrap_runs", "pairing_codes")] == audit_before
    assert (env.cat.get_state("installation_uid"), len(env.cat.list_cemeteries(include_archived=True))) == cat_before
    assert not (b.root / "bootstrap.lock").exists() and "bootstrap_runs" not in {r[0] for r in tables_before}


def test_read_connections_are_really_read_only(env):
    w = World(env)
    b = w.local(w.org1)
    put_local(b)
    c = mf.ro_connect(b.database)
    with pytest.raises(sqlite3.OperationalError):
        c.execute("INSERT INTO locuri_de_veci (cod_loc) VALUES ('X')")
    c.close()
    ro = sqlite3.connect(f"{env.cp.path.resolve().as_uri()}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("DELETE FROM audit_log")
    ro.close()


def test_live_wal_is_read_consistently_without_touching_main_db_file(env):
    w = World(env)
    b = w.local(w.org1)
    writer = sqlite3.connect(b.database)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("INSERT INTO locuri_de_veci (cod_loc) VALUES ('IN-WAL')")
    writer.commit()  # rămâne în WAL cât timp `writer` e deschis
    assert Path(str(b.database) + "-wal").stat().st_size > 0
    main_before = hashlib.sha256(b.database.read_bytes()).hexdigest()
    m = man(b)
    assert m["total_rows"] == 1 and m["classification"] == "NONEMPTY"
    assert hashlib.sha256(b.database.read_bytes()).hexdigest() == main_before
    writer.close()


# ================================================================== TENANT-URI
def test_two_tenants_do_not_influence_each_other_and_no_leakage(env):
    w = World(env)
    b1, b2 = w.local(w.org1, "T1"), w.local(w.org2, "T2")
    put_local(b1)
    put_local(b2, reverse=True)
    mirror_cloud(env, w, b1)
    mirror_cloud(env, w, b2, owner=w.owner2)
    m1 = (man(b1), mf.cloud_manifest(env.cp, w.owner1, w.org1, b1.cemetery_uid))
    p1 = pf.build_plan(b1, env.cp, w.owner1, env.inst, clock=env.clock)
    add_file(b2, "t2.jpg", b"T2")
    sql(b2, "UPDATE locuri_de_veci SET sector='ALT'")
    env.cp.sim_put_row(b2.cemetery_uid, "plati", "p9", {"suma": 5.0})
    env.cp.sim_set_status(b2.cemetery_uid, "ACTIVE")
    m1b = (man(b1), mf.cloud_manifest(env.cp, w.owner1, w.org1, b1.cemetery_uid))
    assert [m["stable_digest"] for m in m1] == [m["stable_digest"] for m in m1b]
    pf.validate_plan(p1, b1, env.cp, w.owner1, clock=env.clock)
    foreign = mf.cloud_manifest(env.cp, w.owner1, w.org1, b2.cemetery_uid)  # owner1 cere cimitirul tenantului 2 în organizația lui
    unknown = mf.cloud_manifest(env.cp, w.owner1, w.org1, str(uuid.uuid4()))
    if foreign["exists"] or foreign["total_rows"] or foreign["media"]["count"] or foreign["cemetery_status"]:
        LEAKS.append("foreign-cemetery-visible")
    assert {k: v for k, v in foreign.items() if k not in ("cemetery_uid", "generated_at", "stable_digest")} == {k: v for k, v in unknown.items() if k not in ("cemetery_uid", "generated_at", "stable_digest")}
    with pytest.raises(env.cc.PermissionDenied):
        mf.cloud_manifest(env.cp, w.owner1, w.org2, b2.cemetery_uid)  # organizația altuia: refuzat
    with pytest.raises(env.cc.PermissionDenied):
        mf.cloud_manifest(env.cp, w.owner2, w.org1, b1.cemetery_uid)
    dumped = json.dumps(m1b[1]) + json.dumps(p1["cloud_manifest"])
    for needle in (b2.cemetery_uid, w.org2, "t2.jpg"):
        if needle in dumped:
            LEAKS.append(needle)
    # două cimitire din ACEEAȘI organizație au manifeste independente
    b3 = w.local(w.org1, "T3")
    put_local(b3)
    assert man(b3)["content_digest"] == man(b1)["content_digest"] and man(b3)["stable_digest"] != man(b1)["stable_digest"]
    assert LEAKS == [], LEAKS


def test_cloud_read_requires_a_role_in_the_organization(env):
    w = World(env)
    b = w.local(w.org1)
    stranger = env.cp.create_principal("strain")
    with pytest.raises(env.cc.PermissionDenied):
        mf.cloud_manifest(env.cp, stranger, w.org1, b.cemetery_uid)
    adm = env.cp.create_principal("adm")
    env.cp.grant_role(w.owner1, adm, w.org1, env.cc.ADMIN)
    assert mf.cloud_manifest(env.cp, adm, w.org1, b.cemetery_uid)["classification"] == "EMPTY"


def test_step1_flow_a_still_works_after_manifest_reads(env):
    """Pasul 1 înghețat: Flux A rămâne neschimbat și nu e perturbat de citirile de manifest."""
    w = World(env)
    b = w.local(w.org1)
    plan = pf.build_plan(b, env.cp, w.owner1, env.inst, clock=env.clock)
    assert plan["decision"] == "A"
    code = env.cp.issue_code(w.owner1, w.org1)
    p1 = env.bs.plan_flow_a(b, env.cp, code, env.inst, clock=env.clock)
    assert env.bs.execute_flow_a(b, env.cp, env.store, p1, p1["plan_hash"], code, env.inst, clock=env.clock)["result"] == "completed"
    with pytest.raises(pf.PlanInvalidated):
        pf.validate_plan(plan, b, env.cp, w.owner1, clock=env.clock)  # binding-ul/starea s-au schimbat => planul de preflight nu mai e valid
    assert pf.build_plan(b, env.cp, w.owner1, env.inst, clock=env.clock)["decision"] == "ALREADY_BOUND"
