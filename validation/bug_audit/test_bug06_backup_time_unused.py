"""BUG-06 — setarea „Ora de pornire (Planificator)” din MAIN > Copii de rezervă nu are niciun efect.

Fișier/cauză: desktop/backup_window.py:160-165 și :218 salvează `backup_time`, dar nimic nu o citește: programarea din aplicație
folosește doar intervalul (shared/cimitir_backup.py:246-248 `is_due`), comanda afișată pentru Planificator nu conține ora
(desktop/backup_window.py:41-46 `scheduler_command`), iar ora Task Scheduler vine exclusiv din `-At` al scriptului
scripts/windows/Register-CimitirBackupTask.ps1. Utilizatorul crede că a programat copia la ora aleasă.
Gravitate: Medie (funcție înșelătoare; datele nu sunt afectate). Test static: caută orice cititor al cheii în afara UI.
"""
import re

from conftest import ROOT

UI_FILE = ROOT / "desktop" / "backup_window.py"


def test_backup_time_setting_is_used_by_some_scheduler():
    readers = []
    for path in list((ROOT / "shared").glob("*.py")) + list((ROOT / "desktop").glob("*.py")) + list((ROOT / "backend").glob("*.py")) \
            + list((ROOT / "scripts").rglob("*.py")) + list((ROOT / "scripts").rglob("*.ps1")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            if re.search(r"""["']backup_time["']|-At\b.*backup""", line) and path != UI_FILE and not re.search(r'DEFAULTS\s*=', line):
                readers.append(f"{path.relative_to(ROOT)}: {line.strip()[:100]}")
    ui = UI_FILE.read_text(encoding="utf-8")
    cmd_uses_time = "backup_time" in ui.split("def scheduler_command", 1)[1].split("def ", 1)[0]
    assert readers or cmd_uses_time, "`backup_time` este salvată din MAIN, dar nu este citită de nicio programare"
