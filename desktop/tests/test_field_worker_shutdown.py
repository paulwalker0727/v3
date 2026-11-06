"""Crash „QThread: Destroyed while thread is still running” (Fatal Python error: Aborted) la ieșirea procesului cu un ApiTask în curs.

Cauza (reprodusă izolat, Linux): field_worker.run_task pornește un QThread și îl ține doar într-un set global; la ieșirea procesului
(sau la teardown-ul unui test care nu așteaptă thread-ul, ex. test_ui_resume_button_only_when_safe_and_confirmed) obiectul C++ este distrus
cât timp thread-ul rulează încă => qFatal => abort (pe Windows: Aborted / access violation în thread-ul HTTP).
Testele rulează într-un proces izolat, doar cu date fictive, fără rețea.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]
SHARED = DESKTOP.parent / "shared"

SCRIPT = textwrap.dedent('''
    import sys, time
    sys.path.insert(0, {desktop!r}); sys.path.insert(0, {shared!r})
    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    import field_worker
    done = []
    field_worker.run_task(None, lambda: (time.sleep(0.6), "ok")[1], done.append, lambda m, nc: done.append(("fail", m)))
    {tail}
    print("EXIT_REACHED", done)
''')


def _run(tail, tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_") and k not in ("DATABASE_URL", "SUPABASE_URL")}
    env.update(QT_QPA_PLATFORM="offscreen", CIMITIR_DATA_DIR=str(tmp_path / "data"), CIMITIR_ENV_FILE=str(tmp_path / "nu.env"),
               CIMITIR_LOG_DIR=str(tmp_path / "logs"), CIMITIR_DB_ENGINE="sqlite", DATABASE_URL="")
    code = SCRIPT.format(desktop=str(DESKTOP), shared=str(SHARED), tail=tail)
    return subprocess.run([sys.executable, "-X", "faulthandler", "-c", code], env=env, capture_output=True, text=True, timeout=60)


def test_process_exit_with_running_api_task_does_not_abort(tmp_path):
    p = _run("pass", tmp_path)
    assert "Destroyed while thread is still running" not in p.stderr, p.stderr[-2000:]
    assert p.returncode == 0, (p.returncode, p.stderr[-2000:])
    assert "EXIT_REACHED" in p.stdout


def test_wait_for_tasks_delivers_result_and_releases_thread(tmp_path):
    p = _run("assert field_worker.wait_for_tasks(10000) is True\napp.processEvents()\nassert not field_worker._ACTIVE, field_worker._ACTIVE", tmp_path)
    assert p.returncode == 0, p.stderr[-2000:]
    assert "['ok']" in p.stdout


def test_wait_for_tasks_without_running_tasks_is_noop():
    sys.path.insert(0, str(DESKTOP))
    import field_worker
    assert field_worker.wait_for_tasks(10) is True
