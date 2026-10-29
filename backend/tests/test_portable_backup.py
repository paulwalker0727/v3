# ruff: noqa: F811
"""Livrarea 4 — backup portabil: export → verificare → previzualizare → import sigur → prima sincronizare (Flux B) cu simulatorul local de control plane (SQLite).
LOCAL-SQLite; fără PostgreSQL/Supabase/rețea."""
from __future__ import annotations

import json
import shutil
import sqlite3
import zipfile
from pathlib import Path

import pytest

import cimitir_bootstrap as bs
import cimitir_manifest as mf
import cimitir_portable as pt
import cimitir_registry as reg
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas2_manifest import DATA, add_file, put_local, sql
from test_etapa4_pas4_flux_b import assert_completed, b_world, plan_b, run_b


def dest_catalog(env, name="dest"):
    return reg.RegistryCatalog(Path(env.tmp) / name)


def exported(env, tmp_name="home.cimitir-portabil", **kw):
    w, b, code = b_world(env, **kw)
    cat = env.cat
    arc = Path(env.tmp) / tmp_name
    rep = pt.export_registry(b, arc, cat.get_organization(b.organization_uid), cat.get_cemetery(b.cemetery_uid))
    return w, b, code, arc, rep


def test_export_verify_preview_import_preserves_identity_and_content_digest(env):
    w, b, code, arc, rep = exported(env)
    assert rep["entities"]["locuri_de_veci"] == 3 and rep["media"] == 3 and arc.is_file() and not arc.with_name(arc.name + ".part").exists()
    assert pt.verify_archive(arc)["ok"]
    dc = dest_catalog(env)
    pv = pt.preview(arc, dc)
    assert pv["can_import"] and not pv["blockers"] and pv["organization"]["uid"] == b.organization_uid and pv["cemetery"]["uid"] == b.cemetery_uid
    assert dc.get_cemetery(b.cemetery_uid) is None                                           # previzualizarea nu scrie nimic
    out = pt.import_archive(arc, dc, b.organization_uid, b.cemetery_uid)
    nb = dc.binding_for(b.cemetery_uid)
    assert out["entities"] == rep["entities"] and out["sync_state"] == "UNBOUND" and out["media_problems"] == []
    assert (nb.organization_uid, nb.cemetery_uid) == (b.organization_uid, b.cemetery_uid) and nb.root != b.root
    src_m, new_m = mf.local_manifest(b), mf.local_manifest(nb)
    assert src_m["content_digest"] == new_m["content_digest"]                                # UID-uri, versiuni, relații și media identice
    assert [c.name for c in nb.media.iterdir()] and (nb.logs / f"import-{out['import_id']}.json").is_file()


def test_export_sanitizes_cloud_binding_users_and_secrets(env):
    w, b, code, arc0, _ = exported(env, "x.cimitir-portabil")
    loc = bs.LocalRegistry(b)
    loc.put(cloud_binding_uid="u", cloud_url="http://x", cloud_secret_ref="REF-SECRET-123", bound_installation_uid=env.inst, sync_epoch="4", sync_state="BOUND", bound_root_fp="ff")
    sql(b, "INSERT OR REPLACE INTO users (username, password_hash, role, created_at) VALUES ('admin','HASH-DE-PAROLA','admin','2026-01-01')")
    arc = Path(env.tmp) / "y.cimitir-portabil"
    rep = pt.export_registry(b, arc, env.cat.get_organization(b.organization_uid), env.cat.get_cemetery(b.cemetery_uid))
    assert "cloud_secret_ref" in rep["sanitized"]["removed_settings"] and "users" in rep["sanitized"]["cleared_tables"]
    raw = arc.read_bytes()
    with zipfile.ZipFile(arc) as z, __import__("tempfile").TemporaryDirectory() as td:
        z.extract(pt.DB_ARC, td)
        blob = (Path(td) / pt.DB_ARC).read_bytes()
        c = sqlite3.connect(Path(td) / pt.DB_ARC)
        assert c.execute("SELECT value FROM settings WHERE key='sync_state'").fetchone()[0] == "UNBOUND"
        assert c.execute("SELECT COUNT(*) FROM settings WHERE key IN ('cloud_secret_ref','cloud_url','cloud_binding_uid','bound_installation_uid')").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        c.close()
    assert b"REF-SECRET-123" not in blob and b"HASH-DE-PAROLA" not in blob and b"REF-SECRET-123" not in raw
    assert bs.LocalRegistry(b).get("cloud_secret_ref") == "REF-SECRET-123"                   # sursa rămâne neatinsă


def test_map_image_and_external_files_travel_with_the_right_registry(env):
    w, b, code = b_world(env)
    ext = Path(env.tmp) / "extern" / "poza_externa.jpg"
    ext.parent.mkdir()
    ext.write_bytes(b"EXTERN")
    sql(b, "INSERT INTO photos (cod_loc, file_path) VALUES ('A-1', ?)", str(ext))
    mp = Path(env.tmp) / "harta.png"
    mp.write_bytes(b"\x89PNG-harta-test")
    sql(b, "INSERT OR REPLACE INTO settings (key, value) VALUES ('map_path', ?)", str(mp))
    arc = Path(env.tmp) / "m.cimitir-portabil"
    pt.export_registry(b, arc, env.cat.get_organization(b.organization_uid), env.cat.get_cemetery(b.cemetery_uid))
    dc = dest_catalog(env)
    out = pt.import_archive(arc, dc, b.organization_uid, b.cemetery_uid)
    nb = dc.binding_for(b.cemetery_uid)
    mpn = Path(bs.LocalRegistry(nb).get("map_path"))
    assert nb.contains(mpn) and mpn.read_bytes() == mp.read_bytes()                          # harta aparține registrului importat, nu celui vechi
    c = sqlite3.connect(nb.database)
    fp = c.execute("SELECT file_path FROM photos WHERE cod_loc='A-1' ORDER BY id DESC").fetchone()[0]
    c.close()
    assert not Path(fp).is_absolute() and (nb.media / fp).read_bytes() == b"EXTERN" and out["media_problems"] == []


def test_corrupt_unsafe_wrong_version_and_non_archives_are_rejected_without_changes(env):
    w, b, code, arc, rep = exported(env)
    dc = dest_catalog(env)
    bad = Path(env.tmp) / "bad.cimitir-portabil"
    shutil.copy(arc, bad)
    with zipfile.ZipFile(bad) as z:
        items = {n: z.read(n) for n in z.namelist()}
    media_name = next(n for n in items if n.startswith("media/"))
    items[media_name] = items[media_name] + b"X"                                              # fișier modificat
    with zipfile.ZipFile(bad, "w") as z:
        for n, d in items.items():
            z.writestr(n, d)
    assert not pt.verify_archive(bad)["ok"]
    with pytest.raises(pt.PortableError) as ei:
        pt.import_archive(bad, dc, b.organization_uid, b.cemetery_uid)
    assert ei.value.kind == "verify"
    slip = Path(env.tmp) / "slip.cimitir-portabil"                                            # zip-slip
    with zipfile.ZipFile(slip, "w") as z:
        z.writestr("manifest.json", json.dumps({"format": pt.FORMAT, "version": 1, "files": []}))
        z.writestr("../evil.txt", "x")
    assert not pt.verify_archive(slip)["ok"]
    newer = Path(env.tmp) / "v9.cimitir-portabil"
    with zipfile.ZipFile(newer, "w") as z:
        z.writestr("manifest.json", json.dumps({"format": pt.FORMAT, "version": 9, "files": []}))
    assert any("versiune" in e for e in pt.verify_archive(newer)["errors"])
    txt = Path(env.tmp) / "nu.zip"
    txt.write_text("nu sunt arhivă")
    assert not pt.verify_archive(txt)["ok"]
    assert dc.get_cemetery(b.cemetery_uid) is None and not (dc.data_dir / reg.REGISTRIES_DIR / b.cemetery_uid).exists() and not (Path(env.tmp) / "evil.txt").exists()


def test_import_refuses_existing_cemetery_requires_confirmation_and_never_overwrites(env):
    w, b, code, arc, rep = exported(env)
    dc = dest_catalog(env)
    with pytest.raises(pt.PortableError) as ei:
        pt.import_archive(arc, dc, b.organization_uid, "00000000-0000-4000-8000-000000000000")
    assert ei.value.kind == "confirm" and dc.get_cemetery(b.cemetery_uid) is None and dc.list_organizations() == []
    pt.import_archive(arc, dc, b.organization_uid, b.cemetery_uid)
    before = mf.local_manifest(dc.binding_for(b.cemetery_uid))["content_digest"]
    with pytest.raises(pt.PortableError) as ei:
        pt.import_archive(arc, dc, b.organization_uid, b.cemetery_uid)                        # a doua oară: EXISTS
    assert ei.value.kind == "blocked" and "EXISTS" in str(ei.value)
    assert mf.local_manifest(dc.binding_for(b.cemetery_uid))["content_digest"] == before
    assert len(dc.list_cemeteries()) == 1 and len(dc.list_organizations()) == 1
    with pytest.raises(pt.PortableError) as ei:                                               # nici export peste un fișier existent
        pt.export_registry(b, arc, {}, {})
    assert ei.value.kind == "exists"


def test_duplicate_detection_same_name_other_uid_and_duplicate_plot_codes(env):
    w, b, code, arc, rep = exported(env)
    dc = dest_catalog(env)
    other = dc.create_organization("parohie", env.cat.get_organization(b.organization_uid)["official_name"])        # aceeași denumire, alt UID
    pv = pt.preview(arc, dc)
    assert pv["can_import"] and any("aceeași denumire" in x for x in pv["warnings"])
    with pytest.raises(pt.PortableError) as ei:
        pt.import_archive(arc, dc, b.organization_uid, b.cemetery_uid)
    assert ei.value.kind == "warnings" and dc.get_cemetery(b.cemetery_uid) is None
    out = pt.import_archive(arc, dc, b.organization_uid, b.cemetery_uid, accept_warnings=True)
    assert out["organization_created"] and {o["uid"] for o in dc.list_organizations()} == {other, b.organization_uid}   # nu s-a îmbinat automat


def test_failure_during_import_rolls_back_catalog_and_folders_then_retry_works(env, monkeypatch):
    w, b, code, arc, rep = exported(env)
    dc = dest_catalog(env)
    real = reg.prepare_registry_db

    def boom(binding):
        raise reg.RegistryBindingError("eșec simulat după mutare")
    monkeypatch.setattr(reg, "prepare_registry_db", boom)
    with pytest.raises(reg.RegistryBindingError):
        pt.import_archive(arc, dc, b.organization_uid, b.cemetery_uid)
    assert dc.get_cemetery(b.cemetery_uid) is None and dc.get_organization(b.organization_uid) is None
    assert not dc.registry_root(b.cemetery_uid).exists() and not list((dc.data_dir / reg.REGISTRIES_DIR).glob(".import-*"))
    monkeypatch.setattr(reg, "prepare_registry_db", real)
    assert pt.import_archive(arc, dc, b.organization_uid, b.cemetery_uid)["sync_state"] == "UNBOUND"


def test_first_sync_after_import_uses_flux_b_with_preview_resume_and_no_overwrite(env):
    """Registrul importat nu e legat la Cloud; prima sincronizare = Flux B (plan aprobat explicit, verificare integrală, publicare atomică)."""
    w, b, code, arc, rep = exported(env)
    dc = dest_catalog(env)
    pt.import_archive(arc, dc, b.organization_uid, b.cemetery_uid)
    nb = dc.binding_for(b.cemetery_uid)
    loc = bs.LocalRegistry(nb)
    assert loc.sync_state == "UNBOUND" and not loc.get("cloud_secret_ref") and loc.get("bound_installation_uid") == ""
    assert not bs.sync_allowed(nb, env.inst)                                                  # nicio sincronizare automată
    plan = plan_b(env, w, nb)
    assert plan["kind"] == "B" and plan["local_manifest"]["content_digest"] == mf.local_manifest(b)["content_digest"]
    run = run_b(env, w, nb, plan, code)
    assert_completed(env, w, nb, plan, run)
    again = dc.binding_for(b.cemetery_uid)                                                    # cloud cu date: o a doua încărcare nu suprascrie
    with pytest.raises(Exception):
        plan_b(env, w, again)


# ===================================================================== Qt (offscreen): fila „Backup portabil”
def _pump(qt, cond, timeout=30):
    import time
    t = time.time()
    while time.time() - t < timeout:
        qt.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_qt_portable_tab_export_then_preview_confirm_import(env, monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    desk = Path(__file__).resolve().parents[2] / "desktop"
    monkeypatch.syspath_prepend(str(desk))
    from PySide6.QtWidgets import QApplication
    qt = QApplication.instance() or QApplication([])
    import portable_tab as ptab
    w, b, code = b_world(env)
    arc = Path(env.tmp) / "ui.cimitir-portabil"
    tab = ptab.PortableTab(env.cat, lambda: True, pick_save=lambda: str(arc), pick_open=lambda: str(arc))
    tab.cmb.setCurrentIndex(tab.cmb.findData(b.cemetery_uid))
    tab.btn_export.click()
    assert _pump(qt, lambda: "Arhivă creată și verificată" in tab.out.toPlainText()) and arc.is_file()
    dc = dest_catalog(env, "dest_ui")
    tab2 = ptab.PortableTab(dc, lambda: True, pick_open=lambda: str(arc))
    tab2.btn_open.click()
    assert _pump(qt, lambda: "Destinație:" in tab2.out.toPlainText())
    assert not tab2.btn_import.isEnabled()                                                    # fără confirmarea destinației
    tab2.chk_dest.setChecked(True)
    assert tab2.btn_import.isEnabled()
    tab2.btn_import.click()
    assert _pump(qt, lambda: "Import finalizat și verificat" in tab2.out.toPlainText())
    assert dc.get_cemetery(b.cemetery_uid) and "neasociat" in tab2.out.toPlainText() or "Asociază" in tab2.out.toPlainText()
    assert bs.LocalRegistry(dc.binding_for(b.cemetery_uid)).sync_state == "UNBOUND"
