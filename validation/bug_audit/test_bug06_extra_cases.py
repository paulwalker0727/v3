"""BUG-06 — ora din „Ora de pornire (Planificator)” ajunge în comanda de înregistrare a activității programate."""
from ui_worker import run_ui

BODY = r'''
out["default"] = backup_window.scheduler_command()
db.set_setting("backup_time", "23:45")
out["saved"] = backup_window.scheduler_command()
out["explicit"] = backup_window.scheduler_command("07:05")
win = backup_window.BackupWindow()
box = win.findChild(QPlainTextEdit, "backup-scheduler-command")
out["initial_box"] = box.toPlainText()
win.findChild(QTimeEdit, "backup-time").setTime(QTime(4, 30))
out["after_change_box"] = box.toPlainText()
win.findChild(QPushButton, "backup-save-settings").click()
out["saved_setting"] = db.get_setting("backup_time")
out["run_cmd"] = backup_window.scheduler_run_command()
'''


def test_scheduler_command_contains_the_selected_time(tmp_path):
    out = run_ui(BODY, tmp_path)
    assert "/ST 02:00" in out["default"]                       # valoarea implicită din DEFAULTS
    assert "/ST 23:45" in out["saved"]
    assert "/ST 07:05" in out["explicit"]
    assert "/ST 23:45" in out["initial_box"]
    assert "/ST 04:30" in out["after_change_box"], "câmpul de comandă trebuie să urmărească ora aleasă"
    assert out["saved_setting"] == "04:30"
    assert out["run_cmd"] in out["default"].replace('\\"', '"'), "comanda rulată (--backup-run / backup_scheduled.py) rămâne neschimbată în schtasks"
    assert out["default"].startswith('schtasks /Create /TN "Cimitir - Copie de rezerva" /SC DAILY')
    assert out["default"].endswith("/F")
