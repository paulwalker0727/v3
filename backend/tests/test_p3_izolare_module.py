"""P3-C: regresie pentru contaminarea între teste — `portal_ratelimit` rămânea legat de un `store` (și o bază de date) dintr-un test anterior,
deci testele `public_*` din test_etapa1_audit.py picau (`no such table: settings`) când un alt test rulase înaintea lor în același proces
(cum se întâmplă cu pytest-xdist). Reproducere pe main: rulează un test care încarcă serverul și șterge folderul de date, apoi un test `public_*`."""
import shutil

from test_etapa1_audit import _load_server


def test_modulele_backend_nu_pastreaza_referinte_la_o_baza_stearsa(tmp_path):
    _load_server(tmp_path / "data1")
    import database as db
    db.init_db()
    import portal_ratelimit
    portal_ratelimit.record("cerere", "x")
    shutil.rmtree(tmp_path / "data1")

    _load_server(tmp_path / "data2")                        # alt test, alt folder de date
    import database as db2
    db2.init_db()
    import portal_ratelimit as rl2
    rl2.record("cerere", "y")                                # înainte: OperationalError (no such table: settings)
    assert rl2.retry_after("cerere", "y") == 0
    assert (tmp_path / "data2" / "cimitir.db").exists()
