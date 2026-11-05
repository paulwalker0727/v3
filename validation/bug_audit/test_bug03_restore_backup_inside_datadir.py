"""BUG-03 — o copie aflată ÎN folderul de date nu poate fi restaurată niciodată (iar fereastra o oferă).

Fișier/cauză: shared/cimitir_backup.py:155-161: `restore` mută `data_dir` (cu tot cu folderul de copii din interior) în
`.inainte-de-restaurare-*`, apoi citește `backup_dir/cimitir.db` din calea veche, care nu mai există -> FileNotFoundError ->
rollback. Nicio validare nu împiedică alegerea unui folder de copii în interiorul folderului de date
(desktop/backup_window.py:207-218 `_choose_dir`/`_save_settings`, backend/server.py:1879-1880).

AȘTEPTAT: fie setarea este refuzată (folder de copii în folderul de date), fie restaurarea reușește.
OBȚINUT (fără fix): restaurarea eșuează la fiecare pornire; starea curentă rămâne intactă (rollback) — fără pierdere de date,
dar copia nu poate fi folosită exact când e nevoie de ea; în plus copiile ar fi mutate odată cu folderul.
"""
import sqlite3

import cimitir_backup as cb


def test_restore_from_backup_folder_inside_data_dir(tmp_path):
    data = tmp_path / "Cimitir"
    data.mkdir()
    conn = sqlite3.connect(data / "cimitir.db")
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()
    settings = {"backup_dir": str(data / "copii")}         # folder ales de utilizator în interiorul folderului de date
    res = cb.run_backup_job(data, settings.get, force=True, trigger="manual")
    assert res["result"] == "ok", res
    cb.schedule_restore(res["path"], data)
    msg = cb.apply_pending_restore(data)
    assert msg and "restaurată" in msg, f"restaurarea nu s-a aplicat: {msg}"
