# Etapa 10 — Generarea `Cimitir.exe` pe Windows și verificarea post-build

> **Stare:** instrucțiunile și scripturile sunt pregătite în mediu Linux. **Executabilul NU a fost construit și NU a fost validat pe Windows** în această etapă.
> Verificarea statică a intrărilor build-ului rulează și pe Linux (`scripts/windows/verify_build_inputs.py`); restul este **PENDING MANUAL VALIDATION**.

## 0. Cerințe (o singură dată)
- Windows 10/11 64-bit, **Python 3.11** (bifează „Add Python to PATH"), PowerShell 5.1+.
- (opțional) **Inno Setup 6** pentru instalator; **Windows SDK** (`signtool`) pentru semnare.
- **Nu** este nevoie de `git` pe calculatorul de build: `build_exe.bat` exclude automat `emergentintegrations` (instalat din Git; nu se livrează clientului) — la fel ca `.github/workflows/build-windows.yml`.

## 1. Verificare înainte de build (rapidă, fără a construi)
```powershell
python scripts\windows\verify_build_inputs.py
```
Verifică: resursele din `cimitir.spec` există (fonturi, șabloane, `portal/`, migrările 001–028, `tools/setup_manager`), modulele din `hiddenimports` se rezolvă,
ferestrele MAIN (backup, integritate, persoane duplicate) sunt importate, `launcher.py` tratează `--portal` și `--backup-run`, `build_exe.bat` este CRLF și nu cere Git.
Orice `[FAIL]` oprește livrarea; `[WARN]` este informativ.

## 2. Construirea
**Varianta A — local (recomandat pentru prima validare)**
```bat
cd desktop
build_exe.bat
```
Produce `desktop\dist\Cimitir\Cimitir.exe` (folder), `desktop\dist\Cimitir-portabil.exe` și, dacă există Inno Setup, `desktop\dist_installer\CimitirSetup-<ver>.exe`.

**Varianta B — GitHub Actions:** *Actions → „Construiește Cimitir pentru Windows" → Run workflow* (sau tag `v*`). Rulează testele de packaging, `PyInstaller` și smoke-ul portalului. Verificările Etapei 10 (static + post-build) se adaugă în workflow aplicând `git apply scripts/windows/build-windows.workflow.patch` (necesită drepturi de editare a workflow-urilor; nu a putut fi publicat direct din cauza scope-ului tokenului).

Semnare (opțional): `set CIMITIR_CERT=...` / `set CIMITIR_CERT_PASS=...` sau `set CIMITIR_CERT_SUBJECT=...` (vezi `desktop\BUILD.md`). Fără semnare, SmartScreen avertizează.

## 3. Verificare post-build (automată, pe folder TEMPORAR)
```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\Verify-CimitirBuild.ps1 -DistDir desktop\dist\Cimitir
```
Scriptul:
1. verifică `Cimitir.exe` (dimensiune, versiune, **SHA-256** de notat în livrare, semnătură — informativ);
2. verifică resursele din pachet (`_internal\fonts`, `templates`, `docs`, `portal\templates|static|fonts`, `backend\cimitir\pg_migrations` ≥ 28, `tools\setup_manager`, `icon.ico`, `LICENSE.txt`);
3. pornește `Cimitir.exe --portal` pe `127.0.0.1:8766` cu `CIMITIR_DATA_DIR` **temporar** și cere `/api/public/health`, `/portal`, `/admin/access`, `/portal/harta` (200);
4. rulează `Cimitir.exe --backup-run --force --data-dir <temp>` și verifică exit 0, `manifest.json` și jurnalul `*.backup-log.jsonl`;
5. șterge folderul temporar și tipărește **PASS / FAIL / WARN / SKIP** și lista **PENDING MANUAL VALIDATION**.

Cod de ieșire: 0 = niciun FAIL; 1 = cel puțin un FAIL. Nu folosește Supabase, Docker sau baza reală.

## 4. Validare manuală obligatorie (nu o poate face un script)
| # | Pas | Rezultat |
|---|---|---|
| M1 | Instalare pe un Windows **curat** (fără Python) și pornire MAIN | |
| M2 | Autentificare, hartă, **💾 Copii de rezervă** (copie acum, verificare, jurnal, avertizare), **🧪 Verificare integritate**, **🔁 Persoane duplicate**, avize (tab „Amenajări" al locului) | |
| M3 | Programare Task Scheduler: `scripts\windows\Register-CimitirBackupTask.ps1 -Exe "<cale>\Cimitir.exe" -At 02:00`, apoi `Start-ScheduledTask`; verificare în MAIN › Copii de rezervă › Jurnal | |
| M4 | Generare PDF (fișă loc, contract) cu diacritice | |
| M5 | Asociere telefon (QR) și sincronizare — doar pe Cloud/Postgres LOCAL de test | |
| M6 | Dezinstalare: datele din `%LOCALAPPDATA%\Cimitir` rămân | |

## 5. Depanare
| Simptom | Cauză probabilă |
|---|---|
| `[FAIL] Resursă în pachet: …` | resursă lipsă din `cimitir.spec` → adaug-o la `datas` și reconstruiește |
| `Portal pornit` FAIL | construiește temporar cu `console=True` în `cimitir.spec` și rulează exe-ul din `cmd` ca să vezi eroarea |
| `--backup-run` cod 1 | vezi `*.backup-log.jsonl` (lângă folderul de date) — conține mesajul de eroare (folder inaccesibil, disc plin etc.) |
| Antivirus șterge exe-ul | fals pozitiv PyInstaller; semnare digitală / excepție |
