"""Validează migrările oficiale 008–013 pe Supabase TEST (nhhczdmcocbobptogvsw), în scheme izolate (e2_*), cu date fictive.

    python plan/etapa2/validate_migrations_on_supabase_test.py

A. fresh install: schemă goală -> 001..013. B. upgrade: 001..007 + date fictive -> 008..013 (fără pierderi de date).
C. preflight: date incompatibile oprește migrarea cu mesaj explicit, fără modificare parțială.
D. funcții reale ale aplicației (postgres_store) pe doi tenanți. Scrie DOAR în schemele e2_*, șterse la final.
Refuză orice altă țintă decât proiectul TEST; nu se conectează la producție.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "shared"), str(ROOT / "backend" / "cimitir"), str(ROOT / "backend")]
import cimitir_config  # noqa: E402

cimitir_config.load_env()
import cimitir_test_guard as guard  # noqa: E402

TEST_REF = "nhhczdmcocbobptogvsw"
issues = guard.find_issues()
if issues:
    raise SystemExit("REFUZAT de guard:\n  " + "\n  ".join(issues))
if (os.environ.get("CIMITIR_TEST_PROJECT_REF") != TEST_REF or os.environ.get("CIMITIR_LIVE_TESTS") != "1"
        or f".{TEST_REF}" not in os.environ["DATABASE_URL"] or any(p in os.environ["DATABASE_URL"] for p in guard.PROTECTED_PROJECT_REFS)):
    raise SystemExit("REFUZAT: se acceptă doar proiectul TEST cu CIMITIR_LIVE_TESTS=1")

os.environ["CIMITIR_ALLOW_TENANT_MIGRATIONS"] = "1"   # doar după verificările de mai sus (proiect TEST, fără ref de producție)
import psycopg  # noqa: E402
import pg_compat  # noqa: E402
import postgres_store as ps  # noqa: E402

OFFICIAL = ps.MIGRATIONS_DIR
DSN = os.environ["DATABASE_URL"]
results = []
_orig_configure = pg_compat._configure


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


def owner(schema):
    c = psycopg.connect(DSN, autocommit=True, connect_timeout=20)
    c.execute(f'SET search_path TO "{schema}"')
    return c


def new_schema(name):
    with psycopg.connect(DSN, autocommit=True, connect_timeout=20) as c:
        c.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        c.execute(f'CREATE SCHEMA "{name}"')
    pg_compat.close_pool()

    def cfg(raw):
        _orig_configure(raw)
        raw.execute(f'SET search_path TO "{name}"')
    pg_compat._configure = cfg
    ps._initialized.clear()


def migrations_upto(n):
    d = Path(tempfile.mkdtemp(prefix="mig_"))
    for p in OFFICIAL.glob("[0-9][0-9][0-9]_*.sql"):
        if int(p.name[:3]) <= n:
            shutil.copy(p, d / p.name)
    return d


def migrate_to(n):
    ps.MIGRATIONS_DIR = OFFICIAL if n >= 13 else migrations_upto(n)
    return ps.migrate()


def versions(c):
    return [r[0] for r in c.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()]


TABLES = ["settings", "sectoare", "locuri_de_veci", "concesionari", "contracte", "inhumati", "plati", "photos", "amenajari_avize",
          "loc_extra", "loc_holders", "guides", "rows_ghid", "documents", "citizens", "cereri_concesionare", "users", "sync_ops"]


def snapshot(c):
    out = {}
    for t in TABLES:
        out[t] = c.execute(f"SELECT count(*), md5(coalesce(string_agg(j::text, '|' ORDER BY j::text), '')) FROM "
                           f"(SELECT to_jsonb(x) - 'organization_uid' - 'cemetery_uid' AS j FROM {t} x) q").fetchone()
    return out


def seed_legacy(c, cem_uid, dup_email=False):
    c.execute("INSERT INTO settings (key, value) VALUES ('cemetery_id', %s)", (str(cem_uid),))
    c.execute("INSERT INTO sectoare (id, points, color) VALUES ('S1','[]','#111'),('S2','[]','#222')")
    for i in range(1, 5):
        c.execute("INSERT INTO locuri_de_veci (cod_loc, sector, parcela, rand, numar, extra_data) VALUES (%s,'S1','P1','R1',%s,'{}')", (f"U-{i}", str(i)))
    c.execute("INSERT INTO concesionari (nume, prenume, cnp) VALUES ('FICTIV','Unu','1111111111111'),('FICTIV','Doi','2222222222222')")
    loc = {r[0]: r[1] for r in c.execute("SELECT cod_loc, id FROM locuri_de_veci").fetchall()}
    con = [r[0] for r in c.execute("SELECT id FROM concesionari ORDER BY id").fetchall()]
    c.execute("INSERT INTO contracte (numar_contract, id_loc, concesionar_id, cod_loc) VALUES ('C-1',%s,%s,'U-1'),('C-2',%s,%s,'U-2')",
              (loc["U-1"], con[0], loc["U-2"], con[1]))
    ctr = [r[0] for r in c.execute("SELECT id FROM contracte ORDER BY id").fetchall()]
    c.execute("INSERT INTO inhumati (nume, prenume, id_loc, contract_id) VALUES ('FICTIV','Mort1',%s,%s),('FICTIV','Mort2',%s,%s)",
              (loc["U-1"], ctr[0], loc["U-2"], ctr[1]))
    c.execute("INSERT INTO plati (cod_loc, contract_id, suma) VALUES ('U-1',%s,100.5),('U-2',%s,200)", (ctr[0], ctr[1]))
    c.execute("INSERT INTO photos (cod_loc, file_path) VALUES ('U-1','/fictiv/1.jpg'),('U-3','/fictiv/3.jpg')")
    c.execute("INSERT INTO amenajari_avize (cod_loc, nr_aviz) VALUES ('U-1','AV-1')")
    c.execute("INSERT INTO loc_extra (cod_loc, extra_json) VALUES ('U-1','{\"a\":1}')")
    c.execute("INSERT INTO loc_holders (cod_loc, holder_hash) VALUES ('U-1','h1'),('U-2','h2')")
    c.execute("INSERT INTO guides (kind, points, color) VALUES ('line','[]','#000')")
    c.execute("INSERT INTO rows_ghid (sector_label, name, order_index, baseline, width, depth) VALUES ('S1','R1',1,'[]',1,1)")
    c.execute("INSERT INTO documents (entity_type, entity_id, doc_type, file_path) VALUES ('loc','U-1','act','/fictiv/d.pdf')")
    c.execute("INSERT INTO citizens (email, password_hash) VALUES ('ana@fictiv.invalid','x'),(%s,'x')", ("ANA@fictiv.invalid" if dup_email else "bob@fictiv.invalid",))
    cid = c.execute("SELECT id FROM citizens ORDER BY id LIMIT 1").fetchone()[0]
    c.execute("INSERT INTO cereri_concesionare (created_at, status, cod_urmarire, cod_loc, nume, prenume, citizen_id) VALUES ('01.01.2026','nou','CR-FICTIV1','U-3','FICTIV','Cerere',%s)", (cid,))
    c.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES ('fictiv_admin','x','admin','2026'),('fictiv_user','x','user','2026')")
    c.execute("INSERT INTO sync_ops (op_id, kind) VALUES ('fictiv-op-1','test')")
    return loc, ctr


def second_tenant(c):
    org, cem = uuid.uuid4(), uuid.uuid4()
    c.execute("INSERT INTO organizations (uid, organization_type, official_name, display_name) VALUES (%s,'PARISH','FICTIV P2','FICTIV 2')", (org,))
    c.execute("INSERT INTO cemeteries (uid, organization_uid, name, internal_code) VALUES (%s,%s,'FICTIV Cimitir 2','F2')", (cem, org))
    return org, cem


def preflight_case(label, upto_before, mutate, expect_fail_at, expect_text, dup_email=False):
    new_schema("e2_preflight")
    migrate_to(7)
    c = owner("e2_preflight")
    seed_legacy(c, uuid.uuid4(), dup_email)
    migrate_to(upto_before)
    mutate(c)
    before = versions(c)
    try:
        migrate_to(13)
        failed, msg = False, ""
    except Exception as exc:  # noqa: BLE001
        failed, msg = True, str(exc)
    after = versions(c)
    check(f"PREFLIGHT {label}: migrarea se oprește cu mesaj explicit", failed and f"PREFLIGHT {expect_fail_at}" in msg and expect_text in msg, msg[:200])
    check(f"PREFLIGHT {label}: nicio modificare parțială (versiuni neschimbate)", after == before, (before, after))
    return c


def main():
    # ============================================================== A. FRESH INSTALL
    new_schema("e2_fresh")
    ps.MIGRATIONS_DIR = OFFICIAL
    ps.init_db(force=True)
    c = owner("e2_fresh")
    check("FRESH 001–013: toate migrațiile înregistrate o singură dată", versions(c) == list(range(1, 14)), versions(c))
    check("FRESH: re-rulare = nimic aplicat din nou", ps.migrate() == [] and versions(c) == list(range(1, 14)))
    check("FRESH: tenant activ rezolvat din settings.cemetery_id", pg_compat.tenant() is not None and
          c.execute("SELECT value FROM settings WHERE key='cemetery_id'").fetchone()[0] == pg_compat.tenant()[1])
    nn = c.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema='e2_fresh' AND column_name='cemetery_uid' AND is_nullable='YES' "
                   "AND table_name IN ('locuri_de_veci','concesionari','contracte','inhumati','plati','photos','sectoare','sync_ops','sync_tombstones')").fetchone()[0]
    check("FRESH: schema finală — cemetery_uid NOT NULL pe tabelele de registru", nn == 0, nn)
    keys = {r[0] for r in c.execute("SELECT conname FROM pg_constraint WHERE connamespace = 'e2_fresh'::regnamespace").fetchall()}
    check("FRESH: chei compuse + FK tenant-safe prezente",
          {"locuri_de_veci_cemetery_cod_uq", "sectoare_cemetery_pkey", "loc_extra_cemetery_pkey", "loc_holders_cemetery_pkey"} <= keys
          and any(k.endswith("_tenant") for k in keys))
    rls = c.execute("SELECT count(*) FROM pg_policies WHERE schemaname='e2_fresh' AND policyname='tenant_isolation'").fetchone()[0]
    check("FRESH: politici RLS create", rls >= 20, rls)

    # ============================================================== D. COD REAL AL APLICAȚIEI (2 tenanți)
    org1, cem1 = pg_compat.tenant()
    org2, cem2 = second_tenant(c)
    T1, T2 = (org1, cem1), (str(org2), str(cem2))

    def at(t):
        pg_compat.set_tenant(*t)

    at(T1)
    id1 = ps.save_plot_to_db("F-1", [(0, 0), (1, 1)], "#fff")
    check("APP: creare loc", id1 is not None and ps.get_loc_id_by_cod("F-1") == id1)
    check("APP: modificare loc", ps.save_loc_metadata("F-1", {"sector": "S1", "stare": "liber"}) and ps.load_loc_by_cod("F-1")["sector"] == "S1")
    check("APP: ON CONFLICT compus — același cod_loc în același cimitir nu se duplică", ps.save_plot_to_db("F-1", [(2, 2)], "#000") == id1
          and c.execute("SELECT count(*) FROM locuri_de_veci WHERE cod_loc='F-1' AND cemetery_uid=%s", (cem1,)).fetchone()[0] == 1)
    at(T2)
    id2 = ps.save_plot_to_db("F-1", [(5, 5)], "#abc")
    check("APP: același cod_loc în al doilea cimitir (alt id, alt tenant)", id2 is not None and id2 != id1
          and (ps.load_loc_by_cod("F-1")["sector"] or "") != "S1" and len(ps.list_locuri()) == 1)
    at(T1)
    check("APP: listarea vede doar cimitirul curent", len(ps.list_locuri()) == 1 and len(ps.map_rows()) == 1)
    try:
        c.execute("INSERT INTO locuri_de_veci (cod_loc, organization_uid, cemetery_uid) VALUES ('F-1',%s,%s)", (org1, cem1))
        dup = False
    except psycopg.errors.UniqueViolation:
        dup = True
    check("APP/DB: duplicat cod_loc în același cimitir respins", dup)

    cid = ps.save_concesionar({"nume": "FICTIV", "prenume": "Unu", "cnp": "1"})
    ctr = ps.save_contract({"numar_contract": "FC-1", "cod_loc": "F-1", "concesionar_id": cid})
    inh = ps.save_inhumat("F-1", {"nume": "FICTIV", "prenume": "Mort"})
    pl = ps.add_plata("F-1", 50, "2026-01-01", contract_id=ctr)
    ph1 = ps.save_photo("F-1", "/fictiv/p1.jpg")
    sec = ps.save_sector_to_db("SX", [(0, 0)], "#111")
    ex = ps.save_extra_data("F-1", {"k": 1})
    av = ps.add_aviz("F-1", "AV-9", "2026-01-01", "x")
    cer = ps.add_cerere_concesionare({"cod_loc": "F-1", "nume": "FICTIV", "prenume": "C"})
    check("APP: contract/concesionar/înhumat/plată/photo/sector/loc_extra/aviz/cerere (cimitir 1)",
          all([cid, ctr, inh, pl, ph1, sec, ex, av, cer]) and ps.get_contract(ctr)["id_loc"] == id1
          and len(ps.load_inhumati_for_loc("F-1")) == 1 and ps.total_plati("F-1") == 50
          and len(ps.list_photos_for_loc("F-1")) == 1 and ps.load_extra_data("F-1") == {"k": 1})
    at(T2)
    ph2 = ps.save_photo("F-1", "/fictiv/p2.jpg")
    ctr2 = ps.save_contract({"numar_contract": "FC-2", "cod_loc": "F-1"})
    check("APP: photos tenant-safe — fiecare poză legată de locul din cimitirul ei", ph2 and len(ps.list_photos_for_loc("F-1")) == 1
          and c.execute("SELECT count(*) FROM photos WHERE cod_loc='F-1'").fetchone()[0] == 2)
    check("APP: contractul din cimitirul 2 referă locul din cimitirul 2", ps.get_contract(ctr2)["id_loc"] == id2)
    leak = [ps.load_concesionari(), ps.load_inhumati_for_loc("F-1"), ps.list_plati(), ps.list_avize_for_loc("F-1"),
            ps.list_cereri_concesionare(), ps.load_all_sectors(), ps.get_concesionar(cid), ps.get_contract(ctr), ps.get_inhumat(inh)]
    check("APP: CROSS-TENANT LEAKAGE = 0 (cimitirul 2 nu vede nimic din 1)", not any(leak) and ps.load_extra_data("F-1") == {}, leak)
    check("APP: operații cross-tenant pe id străin sunt ignorate (delete/update)",
          not ps.delete_contract(ctr) and not ps.delete_concesionar(cid) and not ps.delete_inhumat(inh) and not ps.delete_plata(pl)
          and not ps.delete_photo(ph1) and not ps.delete_sector_from_db("SX") and not ps.delete_aviz(av))
    at(T1)
    check("APP: datele cimitirului 1 neatinse după încercările din 2", ps.get_contract(ctr) and ps.get_concesionar(cid) and ps.get_inhumat(inh))
    check("APP: ștergere loc (tombstone cu tenant corect, doar pozele lui)", ps.delete_plot("F-1")
          and c.execute("SELECT count(*) FROM sync_tombstones WHERE entity='locuri_de_veci' AND cemetery_uid=%s AND organization_uid=%s", (cem1, org1)).fetchone()[0] >= 1
          and c.execute("SELECT count(*) FROM photos WHERE cod_loc='F-1' AND cemetery_uid=%s", (cem1,)).fetchone()[0] == 0
          and c.execute("SELECT count(*) FROM photos WHERE cod_loc='F-1' AND cemetery_uid=%s", (cem2,)).fetchone()[0] == 1
          and c.execute("SELECT count(*) FROM locuri_de_veci WHERE cod_loc='F-1' AND cemetery_uid=%s", (cem2,)).fetchone()[0] == 1)
    try:
        c.execute("INSERT INTO citizens (email, password_hash, organization_uid) VALUES ('Z@fictiv.invalid','x',%s),('z@fictiv.invalid','x',%s)", (org1, org2))
        ok_two = True
    except psycopg.errors.UniqueViolation:
        ok_two = False
    try:
        c.execute("INSERT INTO citizens (email, password_hash, organization_uid) VALUES ('z@FICTIV.invalid','x',%s)", (org1,))
        dup_c = False
    except psycopg.errors.UniqueViolation:
        dup_c = True
    check("APP/DB: citizen unic per organizație (același e-mail în 2 organizații permis, duplicat respins)", ok_two and dup_c)

    # RLS + pooler + device bindings (scriptul E2) pe schema e2_fresh
    env = dict(os.environ, CIMITIR_E2_SCHEMA="e2_fresh")
    proc = subprocess.run([sys.executable, str(ROOT / "plan" / "etapa2" / "validate_on_supabase_test.py")], env=env, capture_output=True, text=True, timeout=240)
    tail = [ln for ln in proc.stdout.splitlines() if ln.startswith(("PASS", "FAIL", "TOTAL"))]
    fails = [ln for ln in tail if ln.startswith("FAIL")]
    print("      [validate_on_supabase_test.py pe e2_fresh] " + (tail[-1] if tail else (proc.stderr or proc.stdout)[-300:]))
    for ln in fails:
        print("      " + ln)
    check("RLS + POOLER + DEVICE MULTI-CEMETERY + REVOCATION (script E2 pe schema nouă)", proc.returncode == 0 and tail and not fails)
    c.close()

    # ============================================================== B. UPGRADE 007 -> 013
    new_schema("e2_upgrade")
    migrate_to(7)
    c = owner("e2_upgrade")
    check("UPGRADE: schema veche 001–007 creată", versions(c) == list(range(1, 8)))
    legacy_cem = uuid.uuid4()
    seed_legacy(c, legacy_cem)
    sync_rows = c.execute("SELECT count(*) FROM locuri_de_veci WHERE version IS NOT NULL").fetchone()[0]
    before = snapshot(c)
    applied = migrate_to(13)
    after = snapshot(c)
    check("UPGRADE 007→013: aplicate exact 008–013", [a[:3] for a in applied] == ["008", "009", "010", "011", "012", "013"] and versions(c) == list(range(1, 14)), applied)
    check("DATA LOSS = 0: rânduri și conținut (inclusiv version/change_seq) identice pe 18 tabele", before == after,
          {t: (before[t], after[t]) for t in TABLES if before[t] != after[t]})
    check("UPGRADE: identitatea cimitirului păstrată (settings.cemetery_id = cemeteries.uid)",
          c.execute("SELECT count(*) FROM cemeteries WHERE uid=%s", (legacy_cem,)).fetchone()[0] == 1)
    nulls = sum(c.execute(f"SELECT count(*) FROM {t} WHERE cemetery_uid IS NULL OR organization_uid IS NULL").fetchone()[0]
                for t in ("locuri_de_veci", "concesionari", "contracte", "inhumati", "plati", "photos", "sectoare", "loc_extra", "loc_holders", "documents"))
    check("UPGRADE: backfill complet (0 rânduri fără tenant)", nulls == 0, nulls)
    check("UPGRADE: users -> identities / membri / permisiuni",
          c.execute("SELECT (SELECT count(*) FROM identities),(SELECT count(*) FROM organization_members),(SELECT count(*) FROM cemetery_permissions)").fetchone() == (2, 2, 2))
    ps._initialized.clear()
    ps.init_db(force=True)
    check("UPGRADE: aplicația rulează pe datele migrate (tenant = cimitirul vechi)", pg_compat.tenant()[1] == str(legacy_cem)
          and len(ps.list_locuri()) == 4 and len(ps.load_contracte()) == 2 and ps.total_plati() == 300.5
          and len(ps.load_inhumati_for_loc("U-1")) == 1 and len(ps.list_photos_for_loc("U-1")) == 1 and ps.load_extra_data("U-1") == {"a": 1})
    check("UPGRADE: re-rulare nu aplică nimic", ps.migrate() == [])
    c.close()

    # ============================================================== C. PREFLIGHT
    c = preflight_case("011 (e-mail duplicat lower(email) în aceeași organizație)", 10, lambda cc: None, "011", "lower(email)", dup_email=True)
    ok = (c.execute("SELECT is_nullable FROM information_schema.columns WHERE table_schema='e2_preflight' AND table_name='locuri_de_veci' AND column_name='cemetery_uid'").fetchone()[0] == "YES"
          and c.execute("SELECT count(*) FROM pg_constraint WHERE connamespace='e2_preflight'::regnamespace AND conname='locuri_de_veci_cemetery_cod_uq'").fetchone()[0] == 0)
    check("PREFLIGHT 011: schema rămâne cea veche (NOT NULL / cheie compusă neaplicate)", ok)
    c.close()

    def photo_cross(cc):
        _, cem = second_tenant(cc)
        cc.execute("UPDATE photos SET cemetery_uid=%s, organization_uid=(SELECT organization_uid FROM cemeteries WHERE uid=%s) WHERE cod_loc='U-3'", (cem, cem))
    c = preflight_case("011 (photos cu cod_loc din alt cimitir)", 10, photo_cross, "011", "photos")
    c.close()

    def contract_cross(cc):
        _, cem = second_tenant(cc)
        cc.execute("UPDATE contracte SET cemetery_uid=%s, organization_uid=(SELECT organization_uid FROM cemeteries WHERE uid=%s) WHERE numar_contract='C-1'", (cem, cem))
    c = preflight_case("012 (contract și loc în cimitire diferite)", 11, contract_cross, "012", "contracte.id_loc")
    check("PREFLIGHT 012: nicio FK *_tenant creată", c.execute("SELECT count(*) FROM pg_constraint WHERE connamespace='e2_preflight'::regnamespace AND conname LIKE '%_tenant' AND contype='f' "
          "AND conrelid IN ('contracte'::regclass,'inhumati'::regclass,'plati'::regclass)").fetchone()[0] == 0)
    c.close()


if __name__ == "__main__":
    code = 1
    try:
        main()
        failed = [n for n, ok in results if not ok]
        print(f"\nTOTAL: {len(results) - len(failed)} PASS / {len(failed)} FAIL")
        code = 1 if failed else 0
    finally:
        pg_compat.close_pool()
        pg_compat._configure = _orig_configure
        with psycopg.connect(DSN, autocommit=True, connect_timeout=20) as cc:
            for s in ("e2_fresh", "e2_upgrade", "e2_preflight"):
                cc.execute(f'DROP SCHEMA IF EXISTS "{s}" CASCADE')
            left = cc.execute("SELECT count(*) FROM information_schema.schemata WHERE schema_name LIKE 'e2\\_%'").fetchone()[0]
        print(f"curățare: scheme e2_* rămase = {left}")
    sys.exit(code)
