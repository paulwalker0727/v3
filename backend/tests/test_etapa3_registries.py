"""Etapa 3 — registre locale multiple (MAIN local-first): organizații, cimitire, izolare SQLite/media/cache/outbox,
comutare A→B→A, restart, funcționare fără internet, portal local pe registrul activ, adoptarea instalației vechi.

Folosește NUMAI foldere temporare. Nu atinge Supabase, Render, migrările 008–013 sau datele reale.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHARED, DESKTOP = ROOT / "shared", ROOT / "desktop"
for _p in (str(SHARED),):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    for k, v in {"CIMITIR_DATA_DIR": str(data), "CIMITIR_IGNORE_PROJECT_ENV": "1", "CIMITIR_DB_ENGINE": "sqlite",
                 "CIMITIR_MODE": "local", "DATABASE_URL": "", "CIMITIR_JOBS": "0"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CIMITIR_REGISTRY_UID", raising=False)
    before = set(sys.modules)
    import cimitir_database as db
    import cimitir_registry as reg
    reg.deactivate()
    reg._listeners.clear()
    reg._initialized.clear()
    cat = reg.RegistryCatalog(data)
    yield SimpleNamespace(data=data, tmp=tmp_path, reg=reg, db=db, cat=cat)
    reg.deactivate()
    reg._listeners.clear()
    reg._initialized.clear()
    for name in set(sys.modules) - before:  # modulele încărcate aici sunt legate de folderul temporar: nu le lăsăm altor teste
        mod = sys.modules.get(name)
        if str(getattr(mod, "__file__", "") or "").startswith(str(ROOT)):
            sys.modules.pop(name, None)


def make_world(env):
    cat = env.cat
    p = cat.create_organization("parohie", "Parohia Sf. Gheorghe Banovița", "Parohia Banovița", cui="RO123", county="Suceava")
    m = cat.create_organization("primarie", "Primăria Exemplu", locality="Exemplu")
    return {"P_A": cat.create_cemetery(p, "Cimitir Parohial"), "P_B": cat.create_cemetery(p, "Cimitir Sat Y"),
            "M_A": cat.create_cemetery(m, "Cimitir Central"), "M_B": cat.create_cemetery(m, "Cimitir Nord"),
            "org_P": p, "org_M": m}


def add_plot(db, cod="A-01-01", sector="A"):
    db.save_plot_to_db(cod, [(0, 0), (40, 0), (40, 30), (0, 30)], "#22c55e")
    db.save_loc_metadata(cod, {"sector": sector, "parcela": "1", "rand": "1", "numar": "1", "disponibilitate": "Liber"})


# ------------------------------------------------------------------ modelul organizațiilor / cimitirelor
def test_organization_model_types_and_fields(env):
    reg, cat = env.reg, env.cat
    assert set(reg.ORG_TYPES.values()) == {"Parohie", "Primărie / UAT", "Operator privat", "Mănăstire", "Asociație", "Altul"}
    uids = []
    for t in reg.ORG_TYPES:
        uids.append(cat.create_organization(t, f"Org {t}", "", cui="RO1", county="Cluj", locality="Cluj", address="Str. 1",
                                            phone="0700", email="a@b.ro", logo_path="/x/logo.png"))
    org = cat.get_organization(uids[0])
    assert org["uid"] == uids[0] and reg.valid_uid(org["uid"])
    for key in ("org_type", "official_name", "display_name", "cui", "county", "locality", "address", "phone", "email", "logo_path", "active"):
        assert key in org
    assert org["display_name"] == org["official_name"] and org["active"] == 1
    cat.update_organization(uids[0], display_name="Alt nume", phone="0711")
    assert cat.get_organization(uids[0])["display_name"] == "Alt nume"
    cat.set_organization_active(uids[0], False)
    assert uids[0] not in [o["uid"] for o in cat.list_organizations()]
    assert uids[0] in [o["uid"] for o in cat.list_organizations(include_inactive=True)]
    with pytest.raises(reg.RegistryError):
        cat.create_organization("inexistent", "X")
    with pytest.raises(reg.RegistryError):
        cat.create_organization("parohie", "   ")
    with pytest.raises(reg.RegistryError):
        cat.create_cemetery(uids[0], "Cimitir într-o organizație inactivă")


def test_cemetery_model_registry_layout_uses_uuid_not_names(env):
    reg = env.reg
    w = make_world(env)
    cems = env.cat.list_cemeteries()
    assert len(cems) == 4 and len(env.cat.list_cemeteries(w["org_P"])) == 2
    weird = env.cat.create_cemetery(w["org_P"], "../../etc/passwd")
    root = env.data / "registries" / weird
    assert root.is_dir() and root.parent == env.data / "registries"
    for uid in (w["P_A"], w["M_B"]):
        b = reg.activate(uid, env.data, catalog=env.cat)
        assert b.root == env.data / "registries" / uid
        assert b.database.is_file()
        for sub in ("media", "cache", "logs", "outbox"):
            assert (b.root / sub).is_dir()
        assert (b.root / "registry.json").is_file()
    for bad in ("../x", "A", "", "00000000-0000-0000-0000-00000000000G"):
        with pytest.raises(reg.RegistryError):
            reg.valid_uid(bad)
    env.cat.archive_cemetery(w["P_B"])
    assert w["P_B"] not in [c["uid"] for c in env.cat.list_cemeteries()]
    with pytest.raises(reg.RegistryError):
        reg.activate(w["P_B"], env.data, catalog=env.cat)


# ------------------------------------------------------------------ izolare: SQLite, media, cache, outbox
def _src_photo(tmp: Path, who: str) -> Path:
    d = tmp / f"src_{who}"
    d.mkdir(exist_ok=True)
    f = d / "foto.jpg"  # ACELAȘI nume de fișier în toate registrele
    f.write_bytes(b"\xff\xd8photo-of-" + who.encode())
    return f


def test_sqlite_media_cache_isolation_same_codes_same_photo_names(env):
    reg, db = env.reg, env.db
    w = make_world(env)
    files = {}
    for name in ("P_A", "P_B", "M_A", "M_B"):
        b = reg.activate(w[name], env.data, catalog=env.cat)
        add_plot(db, "A-01-01")  # același cod_loc în toate cimitirele
        db.set_setting("marker", name)
        doc_id = db.add_document("loc", "A-01-01", "poza_loc", str(_src_photo(env.tmp, name)), caption=name)
        assert doc_id
        cache_file = Path(db.cache_dir("media_cache")) / "m1" / "foto.jpg"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(name.encode())
        files[name] = (b, Path(db.list_documents("loc", "A-01-01")[0]["file_path"]), cache_file)
    assert len({str(v[0].database) for v in files.values()}) == 4
    for name, (b, media_path, cache_file) in files.items():
        reg.activate(w[name], env.data, catalog=env.cat)
        assert db.get_setting("marker") == name
        assert [r["cod_loc"] for r in db.search_locuri({})] == ["A-01-01"]
        docs = db.list_documents("loc", "A-01-01")
        assert len(docs) == 1 and docs[0]["caption"] == name
        assert media_path.name.endswith("_foto.jpg") and b.contains(media_path) and media_path.read_bytes().endswith(name.encode())
        assert Path(db.media_root()) == b.media and Path(db.cache_dir()) == b.cache
        assert cache_file.read_bytes() == name.encode() and b.contains(cache_file)
        others = [v for k, v in files.items() if k != name]
        assert all(not b.contains(o[1]) and not b.contains(o[2]) for o in others)
    # ștergerea unui document din A nu atinge fișierele din B
    reg.activate(w["P_A"], env.data, catalog=env.cat)
    foreign = files["P_B"][1]
    row = db.list_documents("loc", "A-01-01")[0]
    conn = db.get_connection()
    conn.execute("UPDATE documents SET file_path=? WHERE id=?", (str(foreign), row["id"]))
    conn.close()
    assert db.delete_document(row["id"])
    assert foreign.exists(), "fișierul din alt registru nu poate fi șters"


def test_outbox_and_settings_isolation(env):
    reg, db = env.reg, env.db
    for m in ("database", "cloud_sync"):
        sys.modules.pop(m, None)
    sys.path.insert(0, str(DESKTOP))
    try:
        import cloud_sync
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"cloud_sync indisponibil: {exc}")
    finally:
        sys.path.remove(str(DESKTOP))
    w = make_world(env)
    reg.activate(w["P_A"], env.data, catalog=env.cat)
    cloud_sync.save_config("https://cloud.example/a", "cimitir.key-A-" + "x" * 20, True)
    for i in range(3):
        cloud_sync._enqueue("test", {"status": "x", "n": i})
    assert cloud_sync.pending_count() == 3
    reg.activate(w["P_B"], env.data, catalog=env.cat)
    assert cloud_sync.pending_count() == 0 and cloud_sync.get_config()["url"] == ""
    cloud_sync._enqueue("test", {"status": "x"})
    assert cloud_sync.pending_count() == 1
    reg.activate(w["P_A"], env.data, catalog=env.cat)
    assert cloud_sync.pending_count() == 3 and cloud_sync.get_config()["url"] == "https://cloud.example/a"
    assert (reg.require_binding().outbox).is_dir()


# ------------------------------------------------------------------ comutare, restart, offline
def test_repeated_switch_a_b_a_keeps_state_and_binds_identity(env):
    reg, db = env.reg, env.db
    w = make_world(env)
    seen = []
    reg.add_switch_listener(lambda old, new: seen.append((old.cemetery_uid if old else None, new.cemetery_uid)))
    a, b = w["P_A"], w["M_A"]
    reg.activate(a, env.data, catalog=env.cat)
    add_plot(db, "ONLY-A")
    reg.activate(b, env.data, catalog=env.cat)
    add_plot(db, "ONLY-B")
    for _ in range(5):
        reg.activate(a, env.data, catalog=env.cat)
        assert [r["cod_loc"] for r in db.search_locuri({})] == ["ONLY-A"]
        assert db.get_setting("cemetery_uid") == a and db.get_setting("organization_uid") == w["org_P"]
        reg.activate(b, env.data, catalog=env.cat)
        assert [r["cod_loc"] for r in db.search_locuri({})] == ["ONLY-B"]
        assert db.get_setting("cemetery_uid") == b and db.get_setting("organization_uid") == w["org_M"]
    assert len(seen) == 12 and seen[1] == (a, b)
    assert env.cat.get_state("active_cemetery_uid") == b


def _run_py(code: str, env_extra: dict):
    e = {**os.environ, "CIMITIR_IGNORE_PROJECT_ENV": "1", "CIMITIR_DB_ENGINE": "sqlite", "CIMITIR_MODE": "local",
         "DATABASE_URL": "", "CIMITIR_JOBS": "0", **env_extra}
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=e, timeout=120, cwd=str(ROOT / "backend"))


def test_restart_main_new_process_and_startup_plan(env):
    reg, db = env.reg, env.db
    w = make_world(env)
    reg.activate(w["P_A"], env.data, catalog=env.cat)
    add_plot(db, "SURVIVES")
    # 2+ registre: întotdeauna selector; 1 registru + „deschide automat” -> auto
    assert reg.startup_plan(env.data)["action"] == "selector"
    for k in ("P_B", "M_A", "M_B"):
        env.cat.archive_cemetery(w[k])
    assert reg.startup_plan(env.data)["action"] == "selector"  # opțiunea nu e bifată încă
    reg.set_auto_open(True, env.data)
    plan = reg.startup_plan(env.data)
    assert plan == {"action": "auto", "cemetery_uid": w["P_A"], "registry_count": 1}
    code = f"""
import sys; sys.path.insert(0, {str(SHARED)!r})
import cimitir_registry as reg, cimitir_database as db
plan = reg.startup_plan({str(env.data)!r}); assert plan['action'] == 'auto'
b = reg.activate(plan['cemetery_uid'], {str(env.data)!r})
print([r['cod_loc'] for r in db.search_locuri({{}})], db.get_setting('cemetery_uid'))
"""
    out = _run_py(code, {"CIMITIR_DATA_DIR": str(env.data)})
    assert out.returncode == 0, out.stderr[-600:]
    assert "['SURVIVES']" in out.stdout and w["P_A"] in out.stdout


def test_main_works_fully_offline(env, monkeypatch):
    reg, db = env.reg, env.db

    def no_net(*a, **k):
        raise OSError("fără internet (test)")

    monkeypatch.setattr(socket, "getaddrinfo", no_net)
    monkeypatch.setattr(socket.socket, "connect", no_net)
    w = make_world(env)
    for name in ("P_A", "M_B", "P_A"):
        reg.activate(w[name], env.data, catalog=env.cat)
        add_plot(db, f"OFF-{name}")
        db.add_document("loc", f"OFF-{name}", "poza_loc", str(_src_photo(env.tmp, name)))
        assert db.search_locuri({})
    assert reg.working_dir(env.data) == env.data / "registries" / w["P_A"]


# ------------------------------------------------------------------ imposibilitatea scrierilor între registre
def test_cross_registry_writes_are_impossible(env):
    reg, db = env.reg, env.db
    w = make_world(env)
    a = reg.activate(w["P_A"], env.data, catalog=env.cat)
    b_binding = env.cat.binding_for(w["M_A"])
    with pytest.raises(reg.RegistryBindingError):
        db.set_active_db_path(str(b_binding.database))
    b = reg.activate(w["M_A"], env.data, catalog=env.cat)
    with pytest.raises(reg.RegistrySwitchedError):
        reg.assert_current(a)  # un binding vechi nu mai poate scrie
    reg.assert_current(b)
    # baza altui cimitir pusă în folderul greșit este refuzată la deschidere
    reg.deactivate()
    reg._initialized.clear()
    import shutil
    shutil.copy2(a.database, env.cat.binding_for(w["M_B"]).root / "database.sqlite")
    with pytest.raises(reg.RegistryBindingError):
        reg.activate(w["M_B"], env.data, catalog=env.cat)
    # operațiune fixată (sync) în curs: comutarea așteaptă / refuză, nu amestecă
    reg.activate(w["P_A"], env.data, catalog=env.cat)
    started, release = threading.Event(), threading.Event()
    result = {}

    def worker():
        with reg.pinned() as pin:
            started.set()
            release.wait(5)
            result["db"] = db.current_db_path()
            result["pin"] = pin.cemetery_uid

    t = threading.Thread(target=worker)
    t.start()
    assert started.wait(5)
    with pytest.raises(reg.RegistryBusyError):
        reg.activate(w["M_A"], env.data, wait_seconds=0.3, catalog=env.cat)
    assert reg.require_binding().cemetery_uid == w["P_A"]
    release.set()
    t.join(5)
    assert result["pin"] == w["P_A"] and result["db"] == str(a.database)
    assert reg.activate(w["M_A"], env.data, catalog=env.cat).cemetery_uid == w["M_A"]


# ------------------------------------------------------------------ portal local pe registrul activ
def test_local_portal_follows_active_registry_and_localhost_default(env):
    from fastapi.testclient import TestClient
    reg, db = env.reg, env.db
    w = make_world(env)
    reg.activate(w["P_A"], env.data, catalog=env.cat)
    add_plot(db, "PORTAL-A")
    reg.activate(w["M_A"], env.data, catalog=env.cat)
    add_plot(db, "PORTAL-B")
    reg.activate(w["P_A"], env.data, catalog=env.cat)
    reg.deactivate()  # portalul este un alt proces: nu are registru implicit, doar catalogul
    from test_etapa1_audit import _load_server
    server = _load_server(env.data)
    client = TestClient(server.app)

    def found(q):
        r = client.get("/api/public/search", params={"q": q})
        assert r.status_code == 200
        return [x["cod_loc"] for x in r.json()["results"]]

    assert client.get("/api/health").status_code == 200
    assert found("PORTAL") == ["PORTAL-A"]
    reg.activate(w["M_A"], env.data, catalog=env.cat)  # MAIN schimbă registrul; portalul NU se repornește
    reg.deactivate()
    assert found("PORTAL") == ["PORTAL-B"]
    env.cat.archive_cemetery(w["M_A"])  # registru arhivat -> portalul nu-l mai deschide
    r = client.get("/api/public/search", params={"q": "PORTAL"})
    assert r.status_code == 503 and "registru" in r.json()["detail"].lower()
    assert not (env.data / "cimitir.db").exists(), "portalul nu creează o bază veche când nu există registru activ"
    import cimitir_config
    assert cimitir_config.portal_host() == "127.0.0.1" and cimitir_config.desktop_port() == 8000


# ------------------------------------------------------------------ adoptarea instalației vechi
def _legacy_install(env, name="legacy"):
    d = env.tmp / name
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "make_demo_db.py"), "--data-dir", str(d), "--offline"],
                         capture_output=True, text=True, timeout=120,
                         env={**os.environ, "CIMITIR_IGNORE_PROJECT_ENV": "1", "CIMITIR_DB_ENGINE": "sqlite", "DATABASE_URL": ""})
    assert out.returncode == 0, out.stderr[-500:]
    media = d / "media" / "loc"
    media.mkdir(parents=True)
    for i in range(3):
        (media / f"img{i}.jpg").write_bytes(b"jpg" + bytes([i]))
    conn = sqlite3.connect(d / "cimitir.db")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    for i in range(3):
        conn.execute("INSERT INTO documents (entity_type, entity_id, doc_type, file_path, caption) VALUES ('loc','A-01-01','poza_loc',?,?)",
                     (str(media / f"img{i}.jpg"), f"c{i}"))
    conn.commit()
    conn.close()
    return d


def test_legacy_adoption_dry_run_confirm_and_rollback(env, monkeypatch):
    import cimitir_registry_adopt as adopt
    reg = env.reg
    d = _legacy_install(env)
    cat = reg.RegistryCatalog(d)
    org = cat.create_organization("parohie", "Parohia X")
    cem = cat.create_cemetery(org, "Cimitir X")
    legacy = adopt.detect_legacy(d)
    assert legacy["media_files"] >= 3 and legacy["records"] > 30
    before = _sha(d / "cimitir.db")

    plan = adopt.plan_adoption(cem, d, cat)  # DRY-RUN
    text = "\n".join(plan["lines"])
    assert "Am găsit registru existent" in text and f"{legacy['records']} înregistrări" in text
    assert f"{legacy['media_files']} fișiere media" in text and f"Va fi mutat în {cem}" in text
    assert _sha(d / "cimitir.db") == before and not (d / "registries" / cem / "database.sqlite").exists()
    with pytest.raises(adopt.AdoptionNotConfirmed):
        adopt.adopt_legacy(cem, data_dir=d, catalog=cat)
    with pytest.raises(adopt.AdoptionNotConfirmed):
        adopt.adopt_legacy(cem, confirm="yes", data_dir=d, catalog=cat)
    assert _sha(d / "cimitir.db") == before and (d / "media").is_dir()

    # verificare eșuată -> rollback, instalația veche rămâne neatinsă
    with monkeypatch.context() as m:
        m.setattr(adopt, "_count_files", lambda root: 10_000 if "registries" in str(root) else sum(1 for p in Path(root).rglob("*") if p.is_file()))
        with pytest.raises(adopt.AdoptionError):
            adopt.adopt_legacy(cem, confirm=True, data_dir=d, catalog=cat)
    assert _sha(d / "cimitir.db") == before and (d / "media").is_dir()
    assert not (d / "registries" / cem / "database.sqlite").exists()

    out = adopt.adopt_legacy(cem, confirm=True, data_dir=d, catalog=cat)
    assert out["records"] == legacy["records"] and out["media_files"] == legacy["media_files"]
    assert adopt.detect_legacy(d) is None and not (d / "cimitir.db").exists() and not (d / "media").exists()
    assert (Path(out["archive"]) / "cimitir.db").is_file() and (Path(out["archive"]) / "media").is_dir()
    b = reg.activate(cem, d, catalog=cat)
    db = env.db
    docs = db.list_documents("loc", "A-01-01", "poza_loc")
    assert len(docs) == 3 and all(b.contains(x["file_path"]) and Path(x["file_path"]).is_file() for x in docs)
    assert db.get_setting("cemetery_uid") == cem and db.get_setting("cimitir_nume") == "Cimitirul Municipal Central"
    assert len(db.search_locuri({})) == 34


def test_adopt_cli_dry_run_never_moves_without_confirm(env):
    d = _legacy_install(env, "legacy_cli")
    before = _sha(d / "cimitir.db")
    base = [sys.executable, str(ROOT / "scripts" / "adopt_legacy_registry.py"), "--data-dir", str(d),
            "--org-name", "Parohia CLI", "--cemetery-name", "Cimitir CLI"]
    dry = subprocess.run(base, capture_output=True, text=True, timeout=60)
    assert dry.returncode == 0 and "Am găsit registru existent" in dry.stdout and "înregistrări" in dry.stdout
    assert "fișiere media" in dry.stdout and "Va fi mutat în" in dry.stdout and "DRY-RUN" in dry.stdout
    no_confirm = subprocess.run(base + ["--apply"], capture_output=True, text=True, timeout=60)
    assert no_confirm.returncode == 2
    assert _sha(d / "cimitir.db") == before and not (d / "registry_catalog.sqlite").exists()
    ok = subprocess.run(base + ["--apply", "--confirm"], capture_output=True, text=True, timeout=120,
                        env={**os.environ, "CIMITIR_IGNORE_PROJECT_ENV": "1", "CIMITIR_DB_ENGINE": "sqlite", "DATABASE_URL": ""})
    assert ok.returncode == 0, ok.stdout + ok.stderr[-400:]
    assert not (d / "cimitir.db").exists() and json.loads(next(d.glob("legacy_adopted_*/adoption.json")).read_text())["records"] > 30


# ------------------------------------------------------------------ Etapa 3.5: constatări din smoke-testul real
def test_empty_legacy_db_is_not_offered_for_adoption(env):
    import cimitir_registry_adopt as adopt
    code = ("import sys; sys.path.insert(0, %r)\n"
            "import cimitir_database as db, cimitir_auth as a\n"
            "db.init_db(); a.init_auth()\n") % str(SHARED)
    out = _run_py(code, {"CIMITIR_DATA_DIR": str(env.data)})
    assert out.returncode == 0, out.stderr[-400:]
    assert (env.data / "cimitir.db").is_file()  # doar conturi implicite + setări
    assert adopt.detect_legacy(env.data) is None


def test_portal_waits_for_first_registry_without_creating_legacy_db(env):
    from fastapi.testclient import TestClient
    from test_etapa1_audit import _load_server
    env.reg.RegistryCatalog(env.data)  # catalog gol, ca după pornirea launcherului
    server = _load_server(env.data)
    client = TestClient(server.app)
    assert client.get("/api/public/health").status_code == 200  # launcherul așteaptă exact acest răspuns
    page = client.get("/portal")
    assert page.status_code == 503 and "registru" in page.text.lower() and page.headers.get("retry-after")
    assert client.get("/api/public/search", params={"q": "x"}).status_code == 503
    assert not (env.data / "cimitir.db").exists()
    w = make_world(env)  # MAIN deschide primul registru -> portalul pornește fără repornire
    env.reg.activate(w["P_A"], env.data, catalog=env.cat)
    env.reg.deactivate()
    assert client.get("/portal").status_code == 200
    assert not (env.data / "cimitir.db").exists()


def test_launcher_preferences_live_in_catalog_not_in_a_cemetery_db(env):
    sys.path.insert(0, str(DESKTOP))
    sys.modules.pop("database", None)
    try:
        import launcher
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"launcher indisponibil: {exc}")
    finally:
        sys.path.remove(str(DESKTOP))
    import cimitir_config
    monkey_dir = str(env.data)
    assert os.environ["CIMITIR_DATA_DIR"] == monkey_dir
    launcher.db.set_setting("startup_mode", "local")
    assert launcher.db.get_setting("startup_mode") == "local" and launcher.db.get_setting("inexistent") is None
    assert env.cat.get_state("launcher_startup_mode") == "local"
    assert not (env.data / "cimitir.db").exists()
