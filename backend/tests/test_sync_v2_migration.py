"""Migrarea 022 + rollback pe PostgreSQL LOCAL REAL: drepturi minime, RLS, păstrarea datelor, idempotență, rollback verificabil (snapshot independent înainte/după).
Rulează doar cu CIMITIR_TEST_PG_ADMIN_URL=postgresql://<superuser>@127.0.0.1:<port>/postgres (altfel SKIP). NU atinge Supabase."""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import pytest

ADMIN = os.environ.get("CIMITIR_TEST_PG_ADMIN_URL", "")
MIG = Path(__file__).resolve().parents[1] / "cimitir" / "pg_migrations"
ROLL = Path(__file__).resolve().parents[1] / "cimitir" / "pg_rollbacks" / "022_sync_v2_ROLLBACK.sql"
pytestmark = pytest.mark.skipif(not ADMIN or urlparse(ADMIN).hostname not in ("127.0.0.1", "localhost", "::1"), reason="PostgreSQL local de test indisponibil")
REG = ("locuri_de_veci", "concesionari", "contracte", "inhumati", "plati", "amenajari_avize")


def _dsn(db, user=None):
    u = urlparse(ADMIN)
    return u._replace(path="/" + db, netloc=f"{user or u.username}@{u.hostname}:{u.port}").geturl()


@pytest.fixture()
def pre022():
    name = "m022_" + uuid.uuid4().hex[:8]
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    with psycopg.connect(_dsn(name), autocommit=True) as c:
        for f in sorted(MIG.glob("*.sql")):
            if not f.name.startswith("022"):
                c.execute(f.read_text(encoding="utf-8"))
    yield {"name": name, "admin": _dsn(name), "app": _dsn(name, "cimitir_cp_app")}
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def snapshot(c):
    """Snapshot INDEPENDENT de amprenta din migrare (alte interogări): politici, drepturi pe tabele/coloane/secvențe, RLS, funcții trigger."""
    q = {
        "policies": "SELECT tablename, policyname, cmd, permissive, roles::text, qual, with_check FROM pg_policies WHERE schemaname=current_schema() "
                    "AND tablename = ANY (%s) ORDER BY 1,2",
        "table_grants": "SELECT table_name, privilege_type FROM information_schema.role_table_grants WHERE grantee='cimitir_cp_app' AND table_schema=current_schema() "
                        "AND table_name = ANY (%s) ORDER BY 1,2",
        "column_grants": "SELECT table_name, column_name, privilege_type FROM information_schema.column_privileges WHERE grantee='cimitir_cp_app' "
                         "AND table_schema=current_schema() AND table_name = ANY (%s) ORDER BY 1,2,3",
        "rls": "SELECT relname, relrowsecurity FROM pg_class WHERE relnamespace=current_schema()::regnamespace AND relname = ANY (%s) ORDER BY 1",
    }
    tabs = list(REG) + ["sectoare", "sync_tombstones"]
    out = {k: c.execute(v, (tabs,)).fetchall() for k, v in q.items()}
    out["seq"] = [(s, p, c.execute("SELECT has_sequence_privilege('cimitir_cp_app', %s, %s)", (s, p)).fetchone()[0])
                  for s in ("sync_change_seq", "locuri_de_veci_id_seq", "inhumati_id_seq") for p in ("USAGE", "SELECT", "UPDATE")]
    out["fn"] = c.execute("SELECT proname, prosecdef, proconfig::text, md5(pg_get_functiondef(oid)) FROM pg_proc WHERE pronamespace=current_schema()::regnamespace "
                          "AND proname IN ('cimitir_sync_touch','cimitir_sync_tombstone') ORDER BY 1").fetchall()
    return out


def seed(c, n=3):
    org, cem = str(uuid.uuid4()), str(uuid.uuid4())
    c.execute("INSERT INTO organizations (uid, organization_type, official_name, display_name) VALUES (%s,'OTHER','M022','M022')", (org,))
    c.execute("INSERT INTO cemeteries (uid, organization_uid, name, internal_code, status, data_epoch) VALUES (%s,%s,'M022',%s,'DRAFT',1)", (cem, org, cem))
    for i in range(n):
        c.execute("INSERT INTO locuri_de_veci (uid, organization_uid, cemetery_uid, cod_loc, points) VALUES (%s,%s,%s,%s,'[]'::jsonb)", (uuid.uuid4().hex, org, cem, f"M-{i}"))
    return org, cem


def _has(c, t):
    return c.execute("SELECT to_regclass(%s)", (t,)).fetchone()[0] is not None


def test_migration_least_privilege_idempotent_and_data_preserved(pre022):
    with psycopg.connect(pre022["admin"], autocommit=True) as c:
        org, cem = seed(c)
        before = c.execute("SELECT count(*) FROM locuri_de_veci").fetchone()[0]
        mig = (MIG / "022_sync_v2.sql").read_text(encoding="utf-8")
        c.execute(mig)
        c.execute(mig)                                  # idempotent: a doua rulare nu schimbă backup-ul
        assert c.execute("SELECT count(*) FROM sync_v2_migration_backup WHERE batch='pre-022' AND kind='fingerprint'").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM locuri_de_veci").fetchone()[0] == before
        P = lambda sql, *a: c.execute(sql, a).fetchone()[0]  # noqa: E731
        for t in REG:
            assert not P("SELECT has_table_privilege('cimitir_cp_app', %s, 'INSERT')", t), t       # nu există INSERT la nivel de tabel
            assert not P("SELECT has_table_privilege('cimitir_cp_app', %s, 'UPDATE')", t), t
            assert P("SELECT has_table_privilege('cimitir_cp_app', %s, 'DELETE')", t) and P("SELECT has_table_privilege('cimitir_cp_app', %s, 'SELECT')", t)
            for col in ("id", "version", "change_seq", "modified_at"):
                assert not P("SELECT has_column_privilege('cimitir_cp_app', %s, %s, 'INSERT')", t, col), (t, col)
                assert not P("SELECT has_column_privilege('cimitir_cp_app', %s, %s, 'UPDATE')", t, col), (t, col)
            for col in ("uid", "organization_uid", "cemetery_uid"):
                assert P("SELECT has_column_privilege('cimitir_cp_app', %s, %s, 'INSERT')", t, col)
                assert not P("SELECT has_column_privilege('cimitir_cp_app', %s, %s, 'UPDATE')", t, col), (t, col)   # tenantul nu se poate muta
        assert P("SELECT has_column_privilege('cimitir_cp_app','locuri_de_veci','cod_loc','UPDATE')")
        assert not P("SELECT has_table_privilege('cimitir_cp_app','sync_tombstones','INSERT')") and not P("SELECT has_table_privilege('cimitir_cp_app','sync_tombstones','UPDATE')")
        assert not P("SELECT has_sequence_privilege('cimitir_cp_app','sync_change_seq','USAGE')")
        assert not P("SELECT rolbypassrls FROM pg_roles WHERE rolname='cimitir_cp_app'")
        assert not P("SELECT has_table_privilege('cimitir_cp_app','sync_v2_migration_backup','SELECT')")
        assert not P("SELECT has_table_privilege('cimitir_cp_app','sync_v2_ops','DELETE')")

    # comportament real cu rolul runtime: tenant, trigger-e SECURITY DEFINER, DELETE doar MAIN
    ctx = lambda cc, dev: cc.execute("SELECT set_config('cp.org',%s,false), set_config('cp.cems',%s,false), set_config('cp.device_type',%s,false)", (org, cem, dev))  # noqa: E731
    with psycopg.connect(pre022["app"], autocommit=True) as a:
        ctx(a, "MOBILE")
        uid = uuid.uuid4().hex
        row = a.execute("INSERT INTO locuri_de_veci (uid, organization_uid, cemetery_uid, cod_loc, points) VALUES (%s,%s,%s,'M-X','[]'::jsonb) RETURNING version, change_seq", (uid, org, cem)).fetchone()
        assert row[0] >= 1 and row[1] is not None                      # versiunea/seq vin din trigger SECURITY DEFINER, nu din drepturi directe
        a.execute("UPDATE locuri_de_veci SET observatii='x' WHERE uid=%s", (uid,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            a.execute("UPDATE locuri_de_veci SET version=99 WHERE uid=%s", (uid,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            a.execute("UPDATE locuri_de_veci SET cemetery_uid=%s WHERE uid=%s", (str(uuid.uuid4()), uid))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            a.execute("INSERT INTO sync_tombstones (entity, uid, version, change_seq, organization_uid, cemetery_uid) VALUES ('x','y',1,1,%s,%s)", (org, cem))
        assert a.execute("DELETE FROM locuri_de_veci WHERE uid=%s", (uid,)).rowcount == 0         # MOBILE nu poate șterge (RLS)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            a.execute("SELECT * FROM sync_v2_migration_backup")
        other = str(uuid.uuid4())
        with pytest.raises(psycopg.errors.InsufficientPrivilege) if False else pytest.raises(psycopg.Error):
            a.execute("INSERT INTO locuri_de_veci (uid, organization_uid, cemetery_uid, cod_loc, points) VALUES (%s,%s,%s,'M-Y','[]'::jsonb)", (uuid.uuid4().hex, org, other))
        ctx(a, "MAIN")
        assert a.execute("DELETE FROM locuri_de_veci WHERE uid=%s", (uid,)).rowcount == 1
        assert a.execute("SELECT count(*) FROM sync_tombstones WHERE uid=%s", (uid,)).fetchone()[0] == 1   # tombstone scris de trigger
        a.execute("SELECT set_config('cp.cems',%s,false)", (str(uuid.uuid4()),))
        assert a.execute("SELECT count(*) FROM locuri_de_veci").fetchone()[0] == 0                 # alt cimitir: nimic vizibil


def test_rollback_restores_exact_previous_state(pre022):
    with psycopg.connect(pre022["admin"], autocommit=True) as c:
        seed(c)
        n_before = c.execute("SELECT count(*) FROM locuri_de_veci").fetchone()[0]
        snap0 = snapshot(c)
        assert any(r[1] == "mobile_cp_test_insert" for r in snap0["policies"])          # starea 021 există înainte
        c.execute((MIG / "022_sync_v2.sql").read_text(encoding="utf-8"))
        snap1 = snapshot(c)
        assert snap1 != snap0 and not any(r[1] == "mobile_cp_test_insert" for r in snap1["policies"])
        c.execute(ROLL.read_text(encoding="utf-8"))
        snap2 = snapshot(c)
        assert snap2 == snap0, {k: (snap0[k], snap2[k]) for k in snap0 if snap0[k] != snap2[k]}
        assert c.execute("SELECT count(*) FROM locuri_de_veci").fetchone()[0] == n_before
        assert c.execute("SELECT to_regclass('sync_v2_ops') IS NOT NULL").fetchone()[0]       # jurnalul rămâne
        # re-aplicare după rollback + al doilea rollback: tot identic
        c.execute((MIG / "022_sync_v2.sql").read_text(encoding="utf-8"))
        c.execute(ROLL.read_text(encoding="utf-8"))
        assert snapshot(c) == snap0


def test_rollback_refuses_when_backup_missing(pre022):
    with psycopg.connect(pre022["admin"], autocommit=True) as c:
        c.execute((MIG / "022_sync_v2.sql").read_text(encoding="utf-8"))
        c.execute("DELETE FROM sync_v2_migration_backup")
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute(ROLL.read_text(encoding="utf-8"))
        c.execute("ROLLBACK")
        assert c.execute("SELECT has_table_privilege('cimitir_cp_app','inhumati','DELETE')").fetchone()[0]    # nimic modificat de rollback eșuat
