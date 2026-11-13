# CIMITIR pe macOS Apple Silicon (MacBook M5) — ghid și raport de compatibilitate

Stare: **pregătit pentru testare pe un Mac real. NU este declarat „complet compatibil macOS”.** Verificarea interfeței și a funcțiilor pe MacBook este încă de făcut (vezi §7).
Windows: nicio cale, comandă sau comportament Windows nu a fost modificat (ramurile noi se activează doar cu `sys.platform == "darwin"`).

## 1. Pornire pe MacBook (pași exacți)

Structura așteptată: `~/CIMITIR-MAC/{python,proiect/v3,date,cache}` (Python 3.11 + uv în `python/`).

```bash
cd ~/CIMITIR-MAC/proiect/v3
git fetch origin && git checkout feature/macos-compatibility

# o singură dată: creează .venv și instalează dependențele (fără Homebrew/Docker/instalări globale)
./scripts/macos/setup-macos.sh

# verificare fără interfață: căi, importuri
./start-macos.sh --check

# amprenta dispozitivului — se trimite emitentului de licențe (vezi §6)
./start-macos.sh --hwid

# pornire (portal local 127.0.0.1 + aplicația desktop, ca pe Windows: start_local.py)
./start-macos.sh
./start-macos.sh --desktop-only      # doar aplicația
```

`start-macos.sh` nu pornește nimic la login, nu instalează nimic, nu deschide porturi în afara `127.0.0.1`. Implicit ignoră fișierele `.env` din proiect
(`CIMITIR_IGNORE_PROJECT_ENV=1`) și dezactivează verificarea de versiune pe GitHub (`CIMITIR_DISABLE_UPDATE_CHECK=1`), ca să nu existe conexiuni către PROD în etapa de test.
Prima pornire creează o bază **goală** în `~/CIMITIR-MAC/date/cimitir-data/` (se folosesc doar date fictive la primele teste; nu se copiază nicio bază reală).

## 2. Unde se scriu datele pe macOS

Tot sub `~/CIMITIR-MAC/` (rădăcina se poate muta cu `CIMITIR_MAC_HOME`). Modulul: `shared/cimitir_platform.py`.

| Ce | Unde |
|---|---|
| Baza SQLite, registre locale, documente, fotografii, outbox, secrete, jurnale portal | `date/cimitir-data/` (= `CIMITIR_DATA_DIR`; fără variabilă, pe macOS implicit tot aici, **nu** în checkout-ul Git) |
| Copii de siguranță (destinația implicită) | `date/cimitir-data-backups/` (frate al folderului de date, ca pe Windows); destinațiile nesigure (în Git) se mută în `date/Cimitir-backups/<hash>/` |
| Jurnal erori backup | `date/logs/backup-errors.jsonl` |
| Licență (`license.key`), configurare AI | `date/config/CimitirDesktop/` |
| Cache criptat licență (`license.dat`) | `date/config/` |
| Jurnale aplicație | `date/logs/` |
| Jurnal audit criptat | `date/audit/` |
| Fișiere temporare, cache pip/uv, `__pycache__` | `cache/` (`TMPDIR`, `PIP_CACHE_DIR`, `UV_CACHE_DIR`, `PYTHONPYCACHEPREFIX` setate de scripturi) |
| Mediul virtual | `proiect/v3/.venv/` |

## 3. Fișiere create în afara `~/CIMITIR-MAC`

Un singur element, și numai dacă se folosește sincronizarea Cloud:

* **Un element în Keychain-ul utilizatorului**: serviciul `CIMITIR-MAC`, contul `secretstore-master-v1` — cheia principală care criptează secretele
  (cheia de sincronizare etc.) din `date/cimitir-data/secrets/`. Pe Windows rolul îl are DPAPI. Secretele propriu-zise stau în folderul principal, criptate (Fernet).
  La prima utilizare macOS poate cere permisiunea de acces la Keychain.
  Eliminare: `security delete-generic-password -s CIMITIR-MAC -a secretstore-master-v1`

Nu se creează: servicii/launchd, login items, fișiere în `/Library`, `~/Library` (aplicația nu folosește QSettings), fișiere în Homebrew. Python 3.11 și `uv` rămân în `~/CIMITIR-MAC/python`.

## 4. Dezinstalare

```bash
security delete-generic-password -s CIMITIR-MAC -a secretstore-master-v1   # doar dacă s-a folosit sincronizarea Cloud
rm -rf ~/CIMITIR-MAC                                                        # aplicație, .venv, date de test, cache, python/uv
```
Backupurile sunt în `~/CIMITIR-MAC/date/` — dacă vrei să păstrezi ceva, copiază-l înainte.

## 5. Probleme Windows-only identificate și soluții

| # | Problemă (fișier) | Efect pe macOS | Soluție (doar macOS) |
|---|---|---|---|
| 1 | Căi `~/Library/Application Support/Cimitir` în `cimitir_config`, `settings_store`, `audit_log`, `app_logging` (desktop+backend), `cimitir_backup` | date în afara `~/CIMITIR-MAC` | `shared/cimitir_platform.py` + ramuri `darwin` |
| 2 | Fără `CIMITIR_DATA_DIR`, rulând din sursă, baza ar fi creată în `shared_data/` din checkout-ul Git | date reale în repo | pe macOS implicit `date/cimitir-data` |
| 3 | `SecretStore`: doar DPAPI (Windows); pe orice alt sistem `SecretStoreUnavailable` → cheia de sincronizare nu se poate salva, **Cloud inutilizabil** | fără Cloud | `KeychainSecretStore` (Keychain + Fernet), cu runner injectabil pentru teste |
| 4 | Licență: `validator.py` pe non-Windows ia primele 64 de caractere din `ioreg` (conțin contoare variabile) și numele calculatorului → **amprentă instabilă**; fișierul licenței în `~/.config` | licența s-ar invalida | `desktop/macos_support.py` (activat din `main.py`): IOPlatformUUID + serie + UUID volum, fără hostname; `XDG_CONFIG_HOME` → `date/config`. `desktop/license/` rămâne **neschimbat** (testul `test_license_and_license_generator_files_unchanged_vs_base` îl protejează) |
| 5 | `hardware_id.py` (cache licență/audit): `system_profiler SPStorageDataType` — mai multe volume, ordine nedeterministă | cheia de criptare a `license.dat`/audit ar putea varia | `diskutil info /` (volumul de sistem) |
| 6 | `ai_scanare/config.py`: cheia API AI legată de `/etc/machine-id` (inexistent pe Mac) | legare doar de numele utilizatorului | `IOPlatformUUID` |
| 7 | Planificatorul Windows (`schtasks`, `--backup-run`, `eventcreate`) în fereastra de backup | comandă fără sens afișată | pe macOS fereastra explică alternativa: planificatorul intern (cât timp aplicația e deschisă) + **copie automată la ieșire** (`backup_window.run_exit_backup`, apelată din `main.py` la închiderea aplicației: verificată, cel mult una pe oră, respectă „Copie automată activă”, se dezactivează din fereastra de backup); **fără launchd** |
| 8 | Interfață: QSS/`QFont` în puncte (pe macOS 1 pt = 1 px, pe Windows 1 pt = 1,33 px) → text cu ~25% mai mic; font „Segoe UI” inexistent | aspect | `theme.mac_scale_qss` + fallback „Helvetica Neue” (doar macOS); **etichetele din hartă** (`theme.scene_font`, 5 locuri în `map_editor_actions`, `map_editor_advanced`, `ui_mainwindow`) au aceeași mărime vizuală ca pe Windows (identic cu `QFont(familie, pt)` pe Windows/Linux) |
| 9 | `ai_training`: rezervă `~/.local/share/Cimitir` | date în afara folderului | rezervă `~/CIMITIR-MAC/date/cimitir-data` pe macOS |
| 10 | Verificarea de versiune pe GitHub la pornire | conexiune în etapa de test | `CIMITIR_DISABLE_UPDATE_CHECK=1` (opțional, setat de `start-macos.sh`) |
| 11 | Avertismentul „folder sincronizat în cloud” nu recunoștea iCloud Drive / `Library/CloudStorage` | risc corupere WAL | markeri noi în `sync_folder_warning` |

Verificate și **fără modificări necesare**: blocarea proceselor (`fcntl`, ramura Windows `msvcrt` neatinsă), deschiderea fișierelor (`open`/`xdg-open` deja prezente), fonturile PDF/QR (Liberation Sans livrat în `desktop/fonts`),
DOCX/PDF (python-docx + reportlab, fără Word/COM/LibreOffice), SQLite/migrații (`sqlite3` din Python), `winreg`/`ctypes.windll`/PowerShell/WMI (doar în ramuri Windows sau în `desktop/license`, neexecutate pe macOS).

Nerezolvate / în afara domeniului: `tools/setup_manager` (utilitar Windows pentru Docker/PROD, DPAPI), `scripts/windows/*`, `tools/windows/*`, `installer`, `build_exe.bat`, `cimitir.spec` (build `.exe`). Nu există pachet `.app` — aplicația rulează din Python, cum s-a cerut.

## 6. Licență pe Mac

Aplicația refuză pornirea fără licență Ed25519 validă, legată de amprenta dispozitivului. Cheia privată nu este în repo, iar codul de licență nu a fost modificat.
Pașii: `./start-macos.sh --hwid` → trimiți HWID-ul emitentului → primești `.lic` → îl încarci în fereastra de activare (apare la prima pornire) sau îl copiezi în
`~/CIMITIR-MAC/date/config/CimitirDesktop/license.key`. Licența Windows nu este valabilă pe Mac (alt HWID).

## 7. Sincronizare offline/Cloud — ce este implementat

Mecanismul este cel existent (nu s-a creat unul nou): triggere SQLite → outbox `cloud_sync_outbox` (op_id, `base_version`), trimitere idempotentă, versiuni pe server,
conflicte raportate fără suprascriere (rezolvare explicită `server`/`mine`), ștergeri propagate (tombstone), SQLite local ca singură sursă de lucru (calculatoarele nu împart fișiere SQLite).

| Categorie | Mecanism | Stare |
|---|---|---|
| Locuri | `entity_sync` (`locuri_de_veci`), inclusiv poligoane/hartă | implementat (outbox, versiuni, conflict, tombstone) |
| Concesionari | `entity_sync` | implementat |
| Contracte | `entity_sync` (legături prin `uid`) | implementat |
| Plăți | `entity_sync` | implementat |
| Înhumați | `entity_sync` | implementat |
| Avize | `entity_sync` (`amenajari_avize`) | implementat |
| Documente și fotografii | `media_sync` / `sync_v2_docs`: `media_id`, `sha256`, outbox `kind='media'`, octeți în Storage privat | implementat; semantica conflictelor diferă (adresare după conținut), de verificat |
| Cereri online | `cloud_sync` (outbox + `op_id`) | implementat |

Teste noi pe căi macOS (`test_macos_offline_sync.py`): baza locală e creată doar în `~/CIMITIR-MAC/date`; modificări offline pe TOATE cele 6 entități rămân în outbox și se trimit o singură dată (fără retrimitere la al doilea ciclu);
conflictul cu alt calculator nu suprascrie datele locale; ștergerea făcută offline ajunge ca tombstone. Se folosește simulatorul de server din testele existente, **nu** Cloud-ul real.

Ce necesită dezvoltare/verificare suplimentară: o legătură Cloud reală de pe Mac către un mediu **TEST** (nu PROD) — asocierea, Keychain, `pull` pe Mac a datelor create pe Windows, documente/fotografii cu octeți reali;
nu s-a conectat nimic la PROD.

### Verificare sincronizare reală Mac ↔ Windows pe Cloud TEST

`scripts/macos/verify_cross_sync.py` (același script pe ambele calculatoare; date fictive `SYNCTEST-…`; nu citește/afișează chei). Premise: pe fiecare calculator o bază **goală** (alt `CIMITIR_DATA_DIR`),
asociată din aplicație la **același** cimitir din Cloud TEST. Protecții: `--test-url` trebuie să fie identic cu adresa din aplicație, proiectul de producție este blocat, `--confirm-test-cloud` obligatoriu,
refuz dacă baza locală are date care nu sunt SYNCTEST. Ordine: Mac `--phase write` → Windows `--phase check` → Windows `--phase write` → Mac `--phase check`;
offline: `--phase write-local` fără internet, apoi `--phase flush` după reconectare; la final `--phase cleanup` (ștergerile ajung ca tombstone). Cod de ieșire 0 = PASS, 1 = eșec, 2 = refuz de protecție.
Logica este testată cu simulator (`test_macos_cross_sync_tool.py`); **rularea pe un Cloud TEST real nu a fost făcută** (nu există acces/credențiale în acest mediu).

## 8. Teste

Rulate pe Linux ARM64, Python 3.11.18, PySide6 offscreen, cu dependențele din `desktop/requirements-macos.txt` + `PyInstaller` (dependență de build, folosită de testele de packaging):

* `origin/main` (bază): **918 trecute, 15 skip, 0 eșuate** (`desktop/tests`).
* Cu această ramură: vezi raportul de livrare (teste noi: `test_macos_compat.py`, `test_macos_offline_sync.py`, `test_macos_cross_sync_tool.py`). Testele existente nu au fost modificate.
* `backend/tests`: nerulat integral (suita se blochează la ~70% și pe `origin/main`, necesită servicii); din backend s-a modificat doar `cimitir/app_logging.py` (verificat prin testul nou).

Teste noi: `desktop/tests/test_macos_compat.py` (căi macOS și regresii Windows, HWID, Keychain simulat, QSS, fereastra de backup), `desktop/tests/test_macos_offline_sync.py`.
Aceste teste simulează macOS (`sys.platform`, comenzi `ioreg`/`diskutil`/`security`) și **nu echivalează cu testarea pe un Mac real**.

Dependențe: toate pachetele din `requirements-macos.txt` au roți `cp311` pentru `macosx arm64` (verificat cu `pip download --platform macosx_*_arm64`); `emergentintegrations` (Git) e Python pur.
Opționale fără Homebrew: `pytesseract` (OCR offline) cere binarul `tesseract`; testele QR cu `pyzbar` cer `libzbar`. Fără ele funcțiile respective se dezactivează/omit.

## 9. De testat pe un Mac real

* Pornire `./start-macos.sh`, activare licență, autentificare, creare registru/cimitir cu date fictive.
* Aspect: dimensiuni font, dialoguri, tabele, hărți (compară etichetele din hartă cu Windows; au fost aliniate prin `scene_font`, dar nu s-a văzut pe Mac real), tema întunecată/deschisă, Retina.
* Editor hartă: zoom cu trackpad/rotiță, selecție, editare poligoane; scurtături: `Ctrl` din Qt devine `Cmd` pe Mac.
* Documente/fotografii: încărcare, deschidere cu `open`, nume cu diacritice (ș, ț, ă), cameră (permisiune macOS), import/export Excel/CSV, DOCX/PDF (contracte, chitanțe, fișe), etichete QR și tipărire.
* Backup manual, verificare, restaurare (repornirea aplicației aplică restaurarea), backup periodic intern și copia automată la ieșire (închide aplicația și verifică jurnalul din fereastra de backup).
* Keychain: promptul la prima salvare a cheii de sincronizare; asociere Cloud pe mediu TEST; sincronizare bidirecțională cu Windows; offline → online.
* Verificarea că nu apare nimic în afara `~/CIMITIR-MAC` (`find ~ -newer ... -path '*Cimitir*'`) și că ștergerea folderului curăță totul.
