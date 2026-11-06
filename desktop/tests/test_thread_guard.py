"""QThread-urile pornite din ferestrele de sincronizare/integritate/duplicate sunt așteptate la ieșire (fără abort Qt)."""
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
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    import thread_guard

    class Slow(QThread):
        def run(self):
            time.sleep(0.6)

    t = thread_guard.track(Slow())
    t.start()
    {tail}
    print("EXIT_REACHED", len(thread_guard._TRACKED))
''')


def _run(tail):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_") and k not in ("DATABASE_URL", "SUPABASE_URL")}
    env["QT_QPA_PLATFORM"] = "offscreen"
    code = SCRIPT.format(desktop=str(DESKTOP), shared=str(SHARED), tail=tail)
    return subprocess.run([sys.executable, "-X", "faulthandler", "-c", code], env=env, capture_output=True, text=True, timeout=60)


def test_exit_with_tracked_running_thread_does_not_abort():
    p = _run("pass")
    assert "Destroyed while thread is still running" not in p.stderr, p.stderr[-2000:]
    assert p.returncode == 0, (p.returncode, p.stderr[-2000:])


def test_wait_all_releases_finished_thread():
    p = _run("assert thread_guard.wait_all(10000) is True\napp.processEvents()")
    assert p.returncode == 0, p.stderr[-2000:]
    assert "EXIT_REACHED 0" in p.stdout, p.stdout


def test_all_worker_start_sites_are_tracked():
    for name in ("cloud_sync_auto", "cloud_sync_window", "cloud_profiles_dialog", "duplicate_people_window", "integrity_window"):
        src = (DESKTOP / f"{name}.py").read_text(encoding="utf-8")
        assert "thread_guard.track(" in src, name
