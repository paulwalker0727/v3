"""BUG-02 — restaurarea unei copii (mod clasic, fără registre) scoate din folderul de date cheile de sincronizare,
licența și jurnalele; în modul „bază veche” cu catalog de registre, scoate și catalogul + toate registrele.

Fișier/cauză: shared/cimitir_backup.py:147-161 (`restore`): mută ÎNTREGUL `data_dir` în `.inainte-de-restaurare-*` și
readuce doar `.jwt_secret` și `registry.json`. În modul clasic `working_dir() == data_dir()` (shared/cimitir_registry.py:517-525),
iar în același folder trăiesc: `secrets/` (cheia de sync / profilele Cloud, shared/cimitir_secretstore.py:192), `license.dat`
(desktop/security/settings_store.py:32: %LOCALAPPDATA%\\Cimitir = data_dir pentru .exe), `logs/`, catalogul și `registries/`.
Copia (shared/cimitir_backup.py:50-75) conține doar cimitir.db + media, deci aceste fișiere nu pot reveni din copie.

AȘTEPTAT: după restaurare, secretele/licența/registrele rămân în folderul de date (copia înlocuiește doar baza + media).
OBȚINUT (fără fix): ele există doar în `<data>.inainte-de-restaurare-*`; MAIN pierde cheia Cloud (sync oprit), licența
și — în modul cu catalog — vede zero registre. Datele NU sunt distruse (copia de siguranță există), dar MAIN e nefuncțional.
"""
import sqlite3

import cimitir_backup as cb
import cimitir_registry as reg
import cimitir_secretstore as ss


def _mk_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO settings VALUES ('marker', 'copie')")
    conn.commit()
    conn.close()


def test_classic_restore_keeps_secrets_license_logs(tmp_path):
    data = tmp_path / "Cimitir"
    data.mkdir()
    _mk_db(data / "cimitir.db")
    assert ss.secrets_dir(data) == data / "secrets"           # aici salvează MAIN cheia de sincronizare
    (data / "secrets").mkdir()
    (data / "secrets" / "legacy_sync_key.bin").write_bytes(b"cheie-criptata-fictiva")
    (data / "license.dat").write_bytes(b"licenta-fictiva")
    (data / "logs").mkdir()
    (data / "logs" / "cimitir.log").write_text("jurnal", encoding="utf-8")
    copy = cb.backup(data, tmp_path / "copii")
    assert cb.verify(copy) == []
    cb.schedule_restore(copy, data)
    msg = cb.apply_pending_restore(data)
    assert msg and "restaurată" in msg, msg
    missing = [p for p in ("secrets/legacy_sync_key.bin", "license.dat", "logs/cimitir.log") if not (data / p).is_file()]
    assert not missing, f"după restaurare lipsesc din folderul de date: {missing} (rămase doar în .inainte-de-restaurare-*)"


def test_legacy_mode_with_catalog_restore_keeps_registries(tmp_path):
    data = tmp_path / "Cimitir"
    data.mkdir()
    _mk_db(data / "cimitir.db")
    reg.set_legacy_mode(True, data)                         # MAIN a ales explicit baza veche, catalogul există
    other = data / "registries" / "alt-registru"
    other.mkdir(parents=True)
    _mk_db(other / "database.sqlite")
    assert reg.catalog_exists(data)
    assert reg.working_dir(data) == data                    # restaurarea țintește TOT data_dir
    copy = cb.backup(data, tmp_path / "copii")
    cb.restore(copy, data, force=True)
    lost = [p for p in (reg.catalog_path(data).name, "registries/alt-registru/database.sqlite") if not (data / p).exists()]
    assert not lost, f"restaurarea bazei vechi a scos din folderul de date: {lost}"


def test_e2e_main_loses_cloud_key_after_restore(tmp_path):
    """Variantă cap-coadă cu codul MAIN real: cheia salvată prin `cloud_sync.save_config` nu mai e găsită după restaurare."""
    from conftest import run_main
    data = tmp_path / "Cimitir"
    before = run_main(data, "out['key'] = bool(cloud_sync.get_config()['key'])", url="http://127.0.0.1:9", key="cimitir.cheie-fictiva")
    assert before["key"] is True
    copy = cb.backup(data, tmp_path / "copii")
    cb.restore(copy, data, force=True)
    after = run_main(data, "out['key'] = bool(cloud_sync.get_config()['key']); out['url'] = cloud_sync.get_config()['url']", configure=False)
    assert after["key"] is True, f"după restaurare MAIN nu mai are cheia de sincronizare (url={after['url']!r}): sincronizarea se oprește"
