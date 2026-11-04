"""Copie de rezervă programată, fără interfață (pentru Planificatorul de activități Windows sau cron).

    python scripts/backup_scheduled.py [--data-dir D] [--dest FOLDER] [--force] [--status]

- folosește setările salvate în MAIN (folder, interval, retenție); copia este verificată (SHA-256 + integrity_check) și jurnalizată;
- cod de ieșire 0 = reușit / nimic de făcut, 1 = EȘEC (apare în Task Scheduler și ca avertizare în MAIN);
- NU restaurează niciodată: restaurarea rămâne manuală, din fereastra „Copii de rezervă”.
În varianta instalată: `Cimitir.exe --backup-run` (același cod).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
import cimitir_backup  # noqa: E402

if __name__ == "__main__":
    sys.exit(cimitir_backup.cli())
