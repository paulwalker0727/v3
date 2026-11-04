# Etapa 10 — Backup automat programat și verificare (MAIN)

Construit peste infrastructura existentă (`shared/cimitir_backup.py`, `desktop/backup_window.py`); nu s-a creat un sistem paralel.
Interfața este una singură: **💾 Copii de rezervă** (butonul vechi „💾 Backup" a fost eliminat; funcționalitatea lui este „Fă copie acum").

## Ce face
| Cerință | Implementare |
|---|---|
| Programare configurabilă | MAIN › Copii de rezervă › *Programare și retenție*: activ/inactiv, folder, interval (ore), ora de pornire, retenție. Două mecanisme care nu se dublează (interval + lock): (1) cronometrul din MAIN (la 30 min verifică dacă e scadentă), (2) **Planificatorul de activități Windows** (rulează și cu MAIN închis): `scripts\windows\Register-CimitirBackupTask.ps1` |
| Verificare fiecare copie | Imediat după creare: SHA-256 pentru fiecare fișier din `manifest.json` + `PRAGMA integrity_check` pe copie. Copia care nu trece este redenumită `*.invalid` (carantină), nu apare în listă, nu poate fi restaurată și rulează ca **EȘEC** |
| Avertizare vizibilă | Eticheta roșie „⛔ Backup EȘUAT" în bara de stare MAIN + banner în fereastră, până la prima copie reușită; avertisment galben dacă ultima copie reușită e mai veche de 2×interval+1h sau dacă nu există nicio copie |
| Retenție + protecție | Zile + număr minim de copii (≥ 3 impus din MAIN). Nu se șterge nimic dacă cele mai noi copii nu sunt întregi; copiile marcate **Protejat** (buton în fereastră, fișier `PROTEJAT`) nu se șterg; se atinge doar `cimitir-AAAAMMZZ-HHMMSS` cu manifest, direct în folderul de copii (nu legături simbolice, nu alte foldere). Retenția rulează doar după o copie reușită și verificată |
| Jurnal | `<folder-date>.backup-log.jsonl` (lângă folderul de date, deci nu intră în copii și rămâne chiar dacă discul de copii lipsește): data, rezultat, declanșare (app / manual / windows-task), locație, fișiere, mărime, eroare. Vizibil în *Jurnal* |
| Restaurare manuală | Neschimbată: se alege copia, se confirmă explicit (implicit „Nu"), se aplică la următoarea pornire; starea curentă se păstrează în `.inainte-de-restaurare-*`. Nicio programare nu apelează restaurarea (acoperit de test) |

## Planificatorul Windows (NETESTAT pe Windows real)
```powershell
# instalare (Cimitir.exe)
.\scripts\windows\Register-CimitirBackupTask.ps1 -Exe "C:\Program Files\Cimitir\Cimitir.exe" -At 02:00
# sau din depozit (Python)
.\scripts\windows\Register-CimitirBackupTask.ps1 -Python "C:\Cimitir\.venv\Scripts\python.exe" -At 02:00 -RepeatHours 6
.\scripts\windows\Register-CimitirBackupTask.ps1 ... -DryRun      # doar afișează comanda
.\scripts\windows\Unregister-CimitirBackupTask.ps1                  # elimină activitatea (nu șterge copii)
```
Comanda rulată: `Cimitir.exe --backup-run [--data-dir D]` (sau `python scripts\backup_scheduled.py`). Cod de ieșire 1 = eșec (vizibil în Task Scheduler și, la următoarea deschidere, în MAIN).
`StartWhenAvailable`: dacă PC-ul era oprit la ora programată, copia rulează la următoarea pornire. `--status` afișează doar starea.

## Limitări
- Copiile conțin baza completă (inclusiv date personale: CNP etc.) **necriptate**; recomandat: disc extern cu BitLocker. Cheile de sincronizare nu sunt în bază (DPAPI), dar hash-urile parolelor utilizatorilor MAIN sunt.
- Dacă folderul de copii (disc extern) lipsește la ora programată, copia eșuează și avertizarea apare; nu există copie „în alt loc" automat.
- Ora de pornire se aplică prin Planificatorul Windows (la înregistrare); cronometrul din MAIN folosește doar intervalul.
- Copiile `*.invalid` nu sunt șterse automat (pentru analiză); se șterg manual.
