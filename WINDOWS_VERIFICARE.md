# Verificare pe Windows (pași manuali)

Nu pot rula GitHub Actions și nici nu am acces la un PC Windows din acest mediu: această verificare o faci tu.
Ce s-a verificat deja pe Linux (spec, build PyInstaller, portalul din executabil) este în `MODURI_RULARE.md`.

## 1. Rulează CI-ul de build
1. GitHub → **Actions** → „Construiește Cimitir pentru Windows" → **Run workflow** (sau împinge un tag `v*`).
2. Pașii care trebuie să fie verzi: *Teste desktop*, *PyInstaller — varianta folder*, **Smoke test — portalul pornește din executabilul înghețat**,
   *PyInstaller — varianta portabilă*, *Instalator Inno Setup*.
3. Descarcă artefactele `CimitirSetup-<versiune>` și `Cimitir-portabil-<versiune>`.
Dacă smoke testul eșuează, deschide logul pasului: arată ce cale (`/portal`, `/admin/access`, `/portal/harta`) a răspuns diferit de 200.

## 2. Instalează pe un PC Windows curat
1. Rulează `CimitirSetup-<versiune>.exe` (SmartScreen poate avertiza dacă nu e semnat).
2. Rulează scriptul automat (nu atinge datele reale):
   `powershell -ExecutionPolicy Bypass -File "C:\Program Files\Cimitir\verify_install.ps1"` — dacă scriptul nu e copiat de instalator, rulează-l din repo: `desktop\installer\verify_install.ps1 -InstallDir "<folder instalare>"`.
   Rezultat așteptat: toate liniile **PASS**.

## 3. Pași manuali (nu pot fi automatizați)
| # | Pas | Rezultat așteptat |
|---|---|---|
| 1 | Pornește Cimitir din meniul Start | Se cere licența (HWID); după activare apare launcherul |
| 2 | Launcher → „Local" | Se deschide MAIN; `http://127.0.0.1:8000/portal` răspunde în browser |
| 3 | Autentificare `admin` / `admin123` | Dialog obligatoriu „Schimbă parola implicită"; fără el nu se intră |
| 4 | Parolă nouă (min. 8 caractere) | Intri în MAIN; `admin123` nu mai funcționează nici în `/admin/access` |
| 5 | MAIN → adaugă un loc liber; în portal `/portal/harta` | Locul apare imediat (aceeași bază) |
| 6 | Depune o cerere în portal; MAIN → Cereri online → aprobă | Portalul arată „Aprobată"; în `/admin/cereri` nu există butoane de aprobare |
| 7 | Launcher → „Server local" | Fereastră cu adresa `/portal` și QR; `/admin/access` accesibil din altă mașină din LAN |
| 8 | MAIN → Scanare de pe telefon (Gemini) | Pagina de pe telefon se deschide pe portul portalului + 1 |
| 9 | Online: MAIN → Sincronizare cloud (URL HTTPS + cheie) → „Sincronizează" | Serverul: `/admin/monitorizare` arată clientul MAIN; după 60 min fără contact apare alerta „MAIN nu a mai contactat serverul" |
| 10 | Dezinstalare | Datele din `%LOCALAPPDATA%\Cimitir` rămân (backup înainte) |

Notează orice abatere cu captură de ecran și jurnalul `%LOCALAPPDATA%\Cimitir\logs\portal.log`.

## 4. Etapa 4 / Pasul 1 — DPAPI real, `_pid_alive`, lock între procese (de rulat pe Windows)
```powershell
python scripts\windows_verify_step1.py --json windows_step1.json
python -m pytest backend\tests\test_etapa4_pas1.py backend\tests\test_etapa4_pas1_closure.py -n 0 -q
```
Rezultat așteptat: `DPAPI WINDOWS REAL: PASS`, `PID CHECK WINDOWS: PASS`, `LOCK WINDOWS (între procese): PASS`; testele Windows-only (`test_secretstore_real_dpapi_windows`, `test_windows_real_dpapi_rotate_isolation_and_pid`) nu mai apar ca SKIP. Folosește doar foldere temporare. Pe alt sistem scriptul răspunde `NOT VERIFIED`.
