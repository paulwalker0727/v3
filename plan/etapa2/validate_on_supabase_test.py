"""Validare multi-tenant pe proiectul Supabase TEST (date fictive, șterse la final). Refuză orice altă țintă.

    python plan/etapa2/validate_on_supabase_test.py
Rolul `cimitir_app` (NOBYPASSRLS) se conectează prin Session Pooler ca `cimitir_app.<ref>`; parola se generează aleatoriu la fiecare rulare,
rămâne doar în memorie (nu se afișează, nu se salvează).
"""
import os
import secrets
import sys
import threading
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "shared"), str(ROOT / "backend" / "cimitir"), str(ROOT / "backend")]
import cimitir_config  # noqa: E402

cimitir_config.load_env()
import cimitir_test_guard as guard  # noqa: E402
import psycopg  # noqa: E402
from urllib.parse import urlparse  # noqa: E402

issues = guard.find_issues()
if issues:
    raise SystemExit("REFUZAT de guard:\n  " + "\n  ".join(issues))
REF = os.environ["CIMITIR_TEST_PROJECT_REF"]
assert guard.valid_test_ref(REF) and f".{REF}" in os.environ["DATABASE_URL"]
U = urlparse(os.environ["DATABASE_URL"])
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


SCHEMA = os.environ.get("CIMITIR_E2_SCHEMA", "public")


def owner():
    c = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, connect_timeout=20)
    c.execute(f'SET search_path TO "{SCHEMA}"')
    return c


def app_password() -> str:
    pw = secrets.token_urlsafe(24)
    with owner() as c:
        c.execute(psycopg.sql.SQL("ALTER ROLE cimitir_app PASSWORD {}").format(psycopg.sql.Literal(pw)))
    return pw


def app_conn(pw):
    c = psycopg.connect(host=U.hostname, port=U.port, dbname="postgres", user=f"cimitir_app.{REF}", password=pw, sslmode="require", connect_timeout=20)
    c.execute(f'SET search_path TO "{SCHEMA}"')
    c.commit()
    return c


def ctx(cur, org, cems):
    cur.execute("SELECT set_config('app.org_uid', %s, true), set_config('app.cemetery_uids', %s, true)", (str(org), ",".join(map(str, cems))))


def raises(cur, conn, sql, args, *errs):
    try:
        cur.execute(sql, args)
        conn.rollback()
        return False
    except errs:
        conn.rollback()
        return True


def main():
    pw = app_password()
    O1, O2 = uuid.uuid4(), uuid.uuid4()
    C1a, C1b, C2a, C2b = (uuid.uuid4() for _ in range(4))
    tenants = {C1a: O1, C1b: O1, C2a: O2, C2b: O2}
    o = owner()
    try:
        o.execute("INSERT INTO organizations (uid, organization_type, official_name, display_name) VALUES (%s,'PARISH','FICTIV Parohie 1','FICTIV 1'),(%s,'MUNICIPALITY','FICTIV Primărie 2','FICTIV 2')", (O1, O2))
        for i, (cem, org) in enumerate(tenants.items()):
            o.execute("INSERT INTO cemeteries (uid, organization_uid, name, internal_code) VALUES (%s,%s,%s,%s)", (cem, org, f"FICTIV Cimitir {i}", f"T{i}"))
        for cem, org in tenants.items():
            o.execute("INSERT INTO locuri_de_veci (cod_loc, organization_uid, cemetery_uid) VALUES ('L-001',%s,%s)", (org, cem))
        o.execute("INSERT INTO locuri_de_veci (cod_loc, organization_uid, cemetery_uid) VALUES ('L-002',%s,%s)", (O1, C1a))
        o.execute("INSERT INTO concesionari (nume, organization_uid, cemetery_uid) VALUES ('FICTIV-C1a',%s,%s),('FICTIV-C2a',%s,%s)", (O1, C1a, O2, C2a))
        o.execute("INSERT INTO citizens (email, password_hash, organization_uid) VALUES ('c@fictiv.invalid','x',%s),('c@fictiv.invalid','x',%s)", (O1, O2))

        # --- chei compuse
        check("același cod_loc în 4 cimitire diferite (2 organizații) e permis", o.execute("SELECT count(*) FROM locuri_de_veci WHERE cod_loc='L-001' AND organization_uid IN (%s,%s)", (O1, O2)).fetchone()[0] == 4)
        dup = lambda sql, a: _raises_owner(o, sql, a)  # noqa: E731
        check("cod_loc duplicat în ACELAȘI cimitir e respins", dup("INSERT INTO locuri_de_veci (cod_loc, organization_uid, cemetery_uid) VALUES ('L-001',%s,%s)", (O1, C1a)))
        o.execute("INSERT INTO sectoare (id, organization_uid, cemetery_uid) VALUES ('S1',%s,%s),('S1',%s,%s)", (O1, C1a, O1, C1b))
        check("sectoare: același id în două cimitire permis, duplicat în același cimitir respins", dup("INSERT INTO sectoare (id, organization_uid, cemetery_uid) VALUES ('S1',%s,%s)", (O1, C1a)))
        o.execute("INSERT INTO loc_extra (cod_loc, organization_uid, cemetery_uid) VALUES ('L-001',%s,%s),('L-001',%s,%s)", (O1, C1a, O1, C1b))
        check("loc_extra: aceeași cheie pe cimitire diferite permisă, duplicat respins", dup("INSERT INTO loc_extra (cod_loc, organization_uid, cemetery_uid) VALUES ('L-001',%s,%s)", (O1, C1a)))
        o.execute("INSERT INTO loc_holders (cod_loc, holder_hash, organization_uid, cemetery_uid) VALUES ('L-001','h',%s,%s),('L-001','h',%s,%s)", (O1, C1a, O1, C1b))
        check("loc_holders: aceeași cheie pe cimitire diferite permisă, duplicat respins", dup("INSERT INTO loc_holders (cod_loc, holder_hash, organization_uid, cemetery_uid) VALUES ('L-001','h',%s,%s)", (O1, C1a)))
        check("citizens: același e-mail în două organizații permis", o.execute("SELECT count(*) FROM citizens WHERE email='c@fictiv.invalid'").fetchone()[0] == 2)
        check("citizens: e-mail duplicat în aceeași organizație respins", dup("INSERT INTO citizens (email, password_hash, organization_uid) VALUES ('c@fictiv.invalid','x',%s)", (O1,)))

        # --- photos FK tenant-safe
        o.execute("INSERT INTO photos (cod_loc, file_path, organization_uid, cemetery_uid) VALUES ('L-001','/f/1.jpg',%s,%s),('L-001','/f/2.jpg',%s,%s)", (O1, C1a, O1, C1b))
        o.execute("INSERT INTO photos (cod_loc, file_path, organization_uid, cemetery_uid) VALUES ('L-002','/f/3.jpg',%s,%s)", (O1, C1a))
        check("photos: același cod_loc, fiecare poză legată de locul din cimitirul ei", o.execute("SELECT count(*) FROM photos WHERE cod_loc='L-001' AND organization_uid=%s", (O1,)).fetchone()[0] == 2)
        check("photos: poză pentru un cod_loc care există doar în ALT cimitir e respinsă (FK compus)", dup("INSERT INTO photos (cod_loc, file_path, organization_uid, cemetery_uid) VALUES ('L-002','/f/4.jpg',%s,%s)", (O1, C1b)))
        o.execute("DELETE FROM locuri_de_veci WHERE cod_loc='L-002' AND cemetery_uid=%s", (C1a,))
        check("ștergerea locului din cimitirul A elimină doar pozele lui (L-001 din A și B rămân)", o.execute("SELECT count(*) FROM photos WHERE organization_uid=%s", (O1,)).fetchone()[0] == 2)
        o.execute("UPDATE locuri_de_veci SET cod_loc='L-001-R' WHERE cod_loc='L-001' AND cemetery_uid=%s", (C1a,))
        check("redenumirea cod_loc în A se propagă doar la pozele din A", o.execute("SELECT count(*) FROM photos WHERE cod_loc='L-001-R' AND cemetery_uid=%s", (C1a,)).fetchone()[0] == 1 and o.execute("SELECT count(*) FROM photos WHERE cod_loc='L-001' AND cemetery_uid=%s", (C1b,)).fetchone()[0] == 1)
        locB = o.execute("SELECT id FROM locuri_de_veci WHERE cemetery_uid=%s AND cod_loc='L-001'", (C1b,)).fetchone()[0]
        check("contract din cimitirul A nu poate referi un loc din cimitirul B (FK pe id, compus)", dup("INSERT INTO contracte (id_loc, organization_uid, cemetery_uid) VALUES (%s,%s,%s)", (locB, O1, C1a)))
        check("cerere din organizația 2 nu poate referi cetățeanul organizației 1", dup("INSERT INTO cereri_concesionare (citizen_id, organization_uid, cemetery_uid) SELECT id, %s, %s FROM citizens WHERE organization_uid=%s", (O2, C2a, O1)))
        check("sync_tombstones: ștergerea fără context RLS (întreținere) păstrează tenantul", o.execute("SELECT count(*) FROM sync_tombstones WHERE cemetery_uid=%s", (C1a,)).fetchone()[0] >= 1)

        # --- personal + dispozitiv
        ident = o.execute("INSERT INTO identities (display_name, password_hash) VALUES ('FICTIV Angajat','x') RETURNING uid").fetchone()[0]
        member = o.execute("INSERT INTO organization_members (organization_uid, identity_uid, username) VALUES (%s,%s,'fictiv.angajat') RETURNING member_uid", (O1, ident)).fetchone()[0]
        for cem in (C1a, C1b):
            o.execute("INSERT INTO cemetery_permissions (member_uid, organization_uid, cemetery_uid) VALUES (%s,%s,%s)", (member, O1, cem))
        check("personal cu acces la 2 cimitire ale aceleiași organizații", o.execute("SELECT count(*) FROM cemetery_permissions WHERE member_uid=%s", (member,)).fetchone()[0] == 2)
        check("permisiune către cimitir din altă organizație e respinsă", dup("INSERT INTO cemetery_permissions (member_uid, organization_uid, cemetery_uid) VALUES (%s,%s,%s)", (member, O1, C2a)))
        dev = o.execute("INSERT INTO devices (organization_uid, installation_uid, device_name, device_type) VALUES (%s,gen_random_uuid(),'FICTIV PC','MAIN') RETURNING device_uid", (O1,)).fetchone()[0]
        bA = o.execute("INSERT INTO device_cemetery_bindings (device_uid, organization_uid, cemetery_uid, sync_key_hash, key_prefix) VALUES (%s,%s,%s,%s,'a') RETURNING binding_uid", (dev, O1, C1a, secrets.token_hex(32))).fetchone()[0]
        o.execute("INSERT INTO device_cemetery_bindings (device_uid, organization_uid, cemetery_uid, sync_key_hash, key_prefix) VALUES (%s,%s,%s,%s,'b')", (dev, O1, C1b, secrets.token_hex(32)))
        check("DEVICE MULTI-CEMETERY: un dispozitiv, 2 legături (cheie proprie fiecare)", o.execute("SELECT (SELECT count(*) FROM devices WHERE device_uid=%s),(SELECT count(DISTINCT sync_key_hash) FROM device_cemetery_bindings WHERE device_uid=%s)", (dev, dev)).fetchone() == (1, 2))
        check("legătură către cimitir din altă organizație respinsă", dup("INSERT INTO device_cemetery_bindings (device_uid, organization_uid, cemetery_uid, sync_key_hash, key_prefix) VALUES (%s,%s,%s,%s,'c')", (dev, O1, C2a, secrets.token_hex(32))))
        o.execute("UPDATE device_cemetery_bindings SET active=false, revoked_at=now(), revoked_by='test' WHERE binding_uid=%s", (bA,))
        st = dict(o.execute("SELECT cemetery_uid, active FROM device_cemetery_bindings WHERE device_uid=%s", (dev,)).fetchall())
        check("REVOCATION PER BINDING: revocarea pentru A nu afectează B", st[C1a] is False and st[C1b] is True)

        # --- RLS ca rol aplicație, prin Session Pooler
        with app_conn(pw) as c, c.cursor() as cur:
            cur.execute("SELECT current_user, (SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname=current_user)"); who = cur.fetchone()
            check("SESSION POOLER: conectat ca cimitir_app, fără BYPASSRLS", who[0] == "cimitir_app" and who[1] is False, who)
            cur.execute("SELECT count(*) FROM locuri_de_veci"); check("fără context: 0 rânduri", cur.fetchone()[0] == 0); c.rollback()
            ctx(cur, O1, [C1a]); cur.execute("SELECT count(*) FROM locuri_de_veci"); check("cimitir 1a vede doar locurile lui", cur.fetchone()[0] == 1); c.rollback()
            ctx(cur, O1, [C1a, C1b]); cur.execute("SELECT count(*) FROM locuri_de_veci"); check("personal cu 2 cimitire vede ambele (nu și organizația 2)", cur.fetchone()[0] == 2); c.rollback()
            ctx(cur, O1, [C2a]); cur.execute("SELECT count(*) FROM locuri_de_veci"); a = cur.fetchone()[0]; c.rollback()
            ctx(cur, O2, [C1a]); cur.execute("SELECT count(*) FROM locuri_de_veci"); b = cur.fetchone()[0]; c.rollback()
            check("organizație + cimitir din altă organizație în context: 0 rânduri", a == 0 and b == 0, (a, b))
            ctx(cur, O1, [C1a]); cur.execute("SELECT count(*) FROM citizens"); x = cur.fetchone()[0]; c.rollback()
            ctx(cur, O2, [C2a]); cur.execute("SELECT count(*) FROM citizens"); y = cur.fetchone()[0]; c.rollback()
            check("cetățeni: fiecare organizație își vede doar cetățenii", (x, y) == (1, 1), (x, y))
            ctx(cur, O1, [C1a]); cur.execute("UPDATE locuri_de_veci SET cod_loc=cod_loc WHERE cemetery_uid=%s", (C2a,)); u = cur.rowcount; c.rollback()
            ctx(cur, O1, [C1a]); cur.execute("DELETE FROM locuri_de_veci WHERE cemetery_uid=%s", (C2a,)); d = cur.rowcount; c.rollback()
            check("UPDATE/DELETE cross-tenant: 0 rânduri", (u, d) == (0, 0), (u, d))
            ctx(cur, O1, [C1a])
            check("INSERT cross-tenant (cimitir străin) respins", raises(cur, c, "INSERT INTO locuri_de_veci (cod_loc, organization_uid, cemetery_uid) VALUES ('X',%s,%s)", (str(O2), str(C2a)), psycopg.errors.InsufficientPrivilege))
            ctx(cur, O1, [C1a])
            check("INSERT în cimitirul B (aceeași organizație) respins când contextul e doar A", raises(cur, c, "INSERT INTO locuri_de_veci (cod_loc, organization_uid, cemetery_uid) VALUES ('X',%s,%s)", (str(O1), str(C1b)), psycopg.errors.InsufficientPrivilege))
            ctx(cur, O1, [C1a]); cur.execute("INSERT INTO locuri_de_veci (cod_loc) VALUES ('AUTO') RETURNING cemetery_uid"); got = cur.fetchone()[0]; c.rollback()
            check("INSERT fără tenant primește cimitirul din context", got == C1a)
            ctx(cur, O1, [C1a]); c.commit()
            cur.execute("SELECT count(*) FROM locuri_de_veci"); check("contextul NU se scurge după COMMIT", cur.fetchone()[0] == 0); c.rollback()
            ctx(cur, O1, [C1a]); cur.execute("DELETE FROM locuri_de_veci WHERE cod_loc='L-001' AND cemetery_uid=%s", (C1a,))
            cur.execute("SELECT count(*) FROM sync_tombstones WHERE cemetery_uid=%s", (C1a,)); t_ok = cur.fetchone()[0] >= 1; c.rollback()
            check("DELETE cu context: tombstone cu tenant corect (aceeași tranzacție)", t_ok)

        # concurență: 2 conexiuni partajate, 8 fire, alternând tenanții
        pool = [app_conn(pw) for _ in range(2)]
        locks = [threading.Lock() for _ in pool]
        leaks = []
        want = {C1a: 1, C2a: 1}

        def worker(n):
            for i in range(30):
                k = (n + i) % 2
                cem = C1a if (n + i) % 3 else C2a
                with locks[k]:
                    cn = pool[k]
                    with cn.cursor() as cur:
                        if i % 4 == 0:
                            cur.execute("SELECT count(*) FROM locuri_de_veci")
                            if cur.fetchone()[0] != 0: leaks.append("fără context")
                            cn.rollback()
                        ctx(cur, tenants[cem], [cem]); cur.execute("SELECT cemetery_uid FROM locuri_de_veci")
                        rows = {r[0] for r in cur.fetchall()}
                        if rows != {cem}: leaks.append((str(cem)[:4], len(rows)))
                        cn.commit()
        ths = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        [t.start() for t in ths]; [t.join() for t in ths]; [p.close() for p in pool]
        check("CONCURENȚĂ: 8 fire × 30 pe 2 conexiuni partajate, 0 scurgeri", not leaks, leaks[:3])
    finally:
        with owner() as c2:
            tabs = [r[0] for r in c2.execute("SELECT table_name FROM information_schema.columns WHERE table_schema=current_schema() AND column_name='organization_uid' AND table_name NOT IN ('organizations','cemeteries','organization_members','cemetery_permissions','devices','device_cemetery_bindings','device_pairing_codes')").fetchall()]
            for t in ("cereri_concesionare", "plati", "inhumati", "contracte", "photos"):
                if t in tabs: c2.execute(f"DELETE FROM {t} WHERE organization_uid IN (%s,%s)", (O1, O2))
            for t in tabs:
                c2.execute(f"DELETE FROM {t} WHERE organization_uid IN (%s,%s)", (O1, O2))
            for t in ("cemetery_permissions", "device_cemetery_bindings", "devices", "organization_members"):
                c2.execute(f"DELETE FROM {t} WHERE organization_uid IN (%s,%s)", (O1, O2))
            c2.execute("DELETE FROM identities WHERE display_name='FICTIV Angajat'")
            c2.execute("DELETE FROM cemeteries WHERE organization_uid IN (%s,%s)", (O1, O2))
            c2.execute("DELETE FROM organizations WHERE uid IN (%s,%s)", (O1, O2))
            left = c2.execute("SELECT (SELECT count(*) FROM organizations WHERE uid IN (%s,%s)),(SELECT count(*) FROM locuri_de_veci WHERE organization_uid IN (%s,%s))", (O1, O2, O1, O2)).fetchone()
        check("curățare: datele fictive șterse", left == (0, 0), left)
        o.close()
    failed = [n for n, ok in results if not ok]
    print(f"\nTOTAL: {len(results) - len(failed)} PASS / {len(failed)} FAIL")
    sys.exit(1 if failed else 0)


def _raises_owner(o, sql, args):
    try:
        o.execute(sql, args)
        return False
    except (psycopg.errors.UniqueViolation, psycopg.errors.ForeignKeyViolation):
        return True


if __name__ == "__main__":
    main()
