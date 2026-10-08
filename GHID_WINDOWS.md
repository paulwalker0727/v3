# Ghid Windows: pornire și test (versiunea din repo)

Legendă: ✅ = rulat efectiv aici (Linux, aceleași comenzi Python/uvicorn, vezi `desktop/tests/test_windows_guide_flow.py`) ·
🪟 = trebuie verificat de tine pe Windows (PowerShell, ferestre MAIN, licență, firewall).
Presupunere: repo-ul este în `C:\Cimitir\app`; datele în `C:\Cimitir\...`. Ajustează căile după nevoie.

## 1. Instalare și pornire

Cerințe: Python 3.11 sau 3.12 pentru Windows (bifează „Add python.exe to PATH"). 🪟

```powershell
# Director: C:\Cimitir\app  (rădăcina repo-ului)
cd C:\Cimitir\app
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1      # dacă e blocat: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
python -m pip install --upgrade pip
```

```powershell
# Director: C:\Cimitir\app\desktop
cd C:\Cimitir\app\desktop
# emergentintegrations (git) nu e necesar pe Windows; scanarea AI folosește google-genai (cheie Gemini introdusă în aplicație)
(Get-Content requirements.txt -Encoding UTF8) | Where-Object { $_ -notmatch '^emergentintegrations' } | Set-Content "$env:TEMP\cimitir-req.txt" -Encoding UTF8
pip install -r "$env:TEMP\cimitir-req.txt"
pip install -r web_requirements.txt
python start_local.py --check-deps    # trebuie să afișeze [deps] OK (verifică și psycopg/libpq în ACELAȘI Python)
```

Sau, într-un singur pas, în același interpreter cu care pornești launcherul: `tools\windows\INSTALL_DEPENDENCIES.bat`.
`web_requirements.txt` include acum `psycopg[binary]` și `psycopg-pool` (portalul le importă la pornire, chiar și în mod SQLite);
dacă lipsesc, launcherul afișează comanda exactă de instalare, nu doar „Portalul nu a pornit”.

Nu folosi `backend\requirements.txt` (listă generată pe Linux, cu pachete care nu sunt necesare). ✅ (setul de mai sus acoperă portalul: testat)

**MAIN are nevoie de licență** (`%APPDATA%\CimitirDesktop\license.key`). Fără ea, la pornire apare dialogul de activare cu HWID-ul PC-ului;
licența se emite cu `desktop\tools\license_manager.py` (cheia privată o ai tu). Nu există bypass în cod. 🪟

Alege **o singură** variantă de pornire (fiecare comandă în PowerShell cu venv activat):

```powershell
# A) MOD LOCAL, totul odată: portal + MAIN pe aceeași bază.  Director: C:\Cimitir\app\desktop
cd C:\Cimitir\app\desktop
$env:CIMITIR_DATA_DIR = "C:\Cimitir\data"
python start_local.py
```

```powershell
# B) Doar backend + portal (fără MAIN).  Director: C:\Cimitir\app\desktop     ✅ (portalul pornește și răspunde la /api/public/health)
cd C:\Cimitir\app\desktop
$env:CIMITIR_DATA_DIR = "C:\Cimitir\data"
python start_local.py --portal-only          # oprire: Ctrl+C
```

```powershell
# B') Echivalent direct cu uvicorn.  Director: C:\Cimitir\app\backend              ✅
cd C:\Cimitir\app\backend
$env:CIMITIR_DATA_DIR = "C:\Cimitir\data"
python -m uvicorn server:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

```powershell
# C) Doar MAIN (portalul rulează separat, B sau B').  Director: C:\Cimitir\app\desktop      🪟
cd C:\Cimitir\app\desktop
$env:CIMITIR_DATA_DIR = "C:\Cimitir\data"
python start_local.py --desktop-only
```

```powershell
# D) Launcher grafic (Local / Server local).  Director: C:\Cimitir\app\desktop             🪟
cd C:\Cimitir\app\desktop
python launcher.py
```

`CIMITIR_DATA_DIR` trebuie să fie **același** pentru MAIN și portal în modul local și pe un disc local (nu OneDrive/Dropbox/rețea:
aplicația avertizează, SQLite WAL se poate corupe).

## 2. URL-uri (portal pe `127.0.0.1:8000`)

| Interfață | URL |
|---|---|
| Portal public | http://127.0.0.1:8000/portal · harta `/portal/harta` · cerere `/portal/cerere` · informare date `/portal/prelucrare-date` |
| Contul cetățeanului (înregistrare, autentificare, cererile mele, documente, completări) | http://127.0.0.1:8000/portal/cont |
| Panou administrator (personal) | http://127.0.0.1:8000/admin/access (după login: `/admin/`, `/admin/cereri`, `/admin/utilizatori`, `/admin/setari`, `/admin/monitorizare`) |
| Health / documentație API | `/api/public/health` · `/docs` |

Panoul de administrare nu poate aproba/respinge cereri și nu poate crea contracte (403 `main_only`): se face numai în MAIN. ✅

## 3. Autentificare și conturi

* **Personal (MAIN și `/admin`, aceeași bază):** la prima pornire, dacă tabela `users` e goală, se creează `admin` (administrator) și `user` (doar citire),
  cu parolele implicite definite în `shared\cimitir_auth.py` (`DEFAULT_USERS`; nu le reproduc aici):
  ```powershell
  # Director: C:\Cimitir\app
  Select-String -Path shared\cimitir_auth.py -Pattern 'DEFAULT_USERS' -Context 0,4
  ```
  La prima autentificare **schimbarea parolei este obligatorie** în ambele (MAIN: dialog; web: `/admin/schimba-parola`); minim 8 caractere,
  diferită de cele implicite. ✅ (web, dialog MAIN în `test_main_password_gate.py`) · 🪟 (aspectul ferestrei pe Windows)
* Dacă baza are deja utilizatori, **nu** se mai creează conturi implicite. Listare conturi (fără parole):
  ```powershell
  # Director: C:\Cimitir\app
  python scripts\reset_admin_password.py --data-dir "C:\Cimitir\data" --list
  ```
* **Recuperare / deblocare** (oprește întâi MAIN și portalul; parola se cere de la tastatură, nu se afișează):
  ```powershell
  # Director: C:\Cimitir\app
  python scripts\reset_admin_password.py --data-dir "C:\Cimitir\data" --username admin
  # cont inexistent -> creează administrator nou:
  python scripts\reset_admin_password.py --data-dir "C:\Cimitir\data" --username admin2 --create-admin
  ```
  Scriptul reactivează și deblochează contul (după 5 greșeli contul se blochează 5 minute). ✅
* **Cetățeni:** nu există conturi implicite; își fac cont la `/portal/cont` (email + parolă de minimum 10 caractere). Implicit **contul este obligatoriu**
  pentru a depune cereri (`/admin/setari` → „Cetățenii trebuie să aibă cont pentru a depune cereri"). Recuperarea parolei de cetățean **nu este implementată**. ✅

## 4. Variabile `.env` (nu se suprascrie nimic existent)

Fișiere citite, în ordine: cel din `CIMITIR_ENV_FILE`, `C:\Cimitir\app\.env`, `C:\Cimitir\app\backend\.env`. Variabilele deja setate în PowerShell au
prioritate față de fișiere (fișierele nu suprascriu). Exemplele din `config\` sunt fișiere noi, separate de `.env`.

| Variabilă | Rol | Implicit |
|---|---|---|
| `CIMITIR_DATA_DIR` | folderul cu `cimitir.db` și `media\` | `<repo>\shared_data` (din surse), `%LOCALAPPDATA%\Cimitir` (.exe) |
| `CIMITIR_ENV_FILE` | fișier .env suplimentar | — |
| `CIMITIR_HOST` / `CIMITIR_PORT` | adresa și portul portalului | `127.0.0.1` / `8000` |
| `CIMITIR_MODE` | `local` sau `online` (API-ul `/api/sync/*` există doar în `online`, **doar pe server**) | `local` |
| `CIMITIR_PORTAL_REQUIRE_ACCOUNT` | `1` = cont obligatoriu pentru cereri (se poate schimba și din `/admin/setari`) | `1` |
| `JWT_SECRET` | semnarea sesiunilor; opțional: dacă lipsește se generează o dată în `<DATA_DIR>\.jwt_secret` | auto |
| `CIMITIR_BEHIND_PROXY`, `FORWARDED_ALLOW_IPS` | doar în spatele unui proxy HTTPS (limitările pe IP) | oprit |
| `REQUESTS_CA_BUNDLE` | certificat CA propriu al serverului, pe MAIN | — |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_SECURITY` (`starttls`/`ssl`/`none`) | trimitere reală de e-mail (reset parolă, alerte); se activează din `/admin/setari` → „Furnizor e-mail" = `smtp` | furnizor `mock` (nu trimite) |
| `CIMITIR_PUBLIC_URL` | adresa publică a portalului din linkurile din e-mail (sau setarea din `/admin/setari`) | adresa cererii |
| `CIMITIR_BACKUP_DIR` | folderul copiilor de rezervă (sau setarea din MAIN / `/admin/setari`) | `<DATA_DIR>-backups` |
| `CIMITIR_JOBS` | `0` oprește joburile de fundal ale portalului (alerte, copii) | pornite |

**Mod local** (un singur PC) — `config\cimitir-local.env.example`:
```
CIMITIR_DATA_DIR=C:\Cimitir\data
CIMITIR_HOST=127.0.0.1
CIMITIR_PORT=8000
CIMITIR_MODE=local
CIMITIR_PORTAL_REQUIRE_ACCOUNT=1
```
**Mod online, server** — `config\cimitir-online-server.env.example`:
```
CIMITIR_DATA_DIR=C:\Cimitir\server-data
CIMITIR_HOST=127.0.0.1
CIMITIR_PORT=8000
CIMITIR_MODE=online
CIMITIR_PORTAL_REQUIRE_ACCOUNT=1
```
**Mod online, MAIN** — `config\cimitir-online-main.env.example`: doar `CIMITIR_DATA_DIR=C:\Cimitir\main-data` (+ opțional `REQUESTS_CA_BUNDLE`). **Nu** pune `CIMITIR_MODE=online` pe MAIN.
Adresa serverului și cheia se introduc în MAIN (secțiunea 6), nu în `.env`.

Generare secrete, fără să suprascrii ceva și fără să afișezi valoarea:
```powershell
# Director: C:\Cimitir\app    (creează C:\Cimitir\server.env doar dacă lipsește; adaugă JWT_SECRET doar dacă nu există)
$envFile = "C:\Cimitir\server.env"
if (-not (Test-Path $envFile)) { New-Item -ItemType File -Path $envFile -Force | Out-Null }
if (-not (Select-String -Path $envFile -Pattern '^JWT_SECRET=' -Quiet)) {
  $secret = python -c "import secrets; print(secrets.token_hex(32))"
  Add-Content -Path $envFile -Value ("`r`nJWT_SECRET=" + $secret)
  Write-Host "JWT_SECRET adăugat în $envFile"
} else { Write-Host "JWT_SECRET există deja; nu a fost modificat." }
$env:CIMITIR_ENV_FILE = $envFile     # doar pentru fereastra curentă
```
Cheia de sincronizare nu se pune în `.env`; se generează cu scriptul din secțiunea 6 (și ajunge direct în clipboard).

## 5. Unde sunt baza de date și documentele

Cu `CIMITIR_DATA_DIR = C:\Cimitir\data` (MAIN și portal local folosesc **aceleași** fișiere):

| Ce | Cale |
|---|---|
| Baza SQLite (MAIN + portal + admin) | `C:\Cimitir\data\cimitir.db` (+ `cimitir.db-wal`, `cimitir.db-shm` cât timp rulează) |
| Documente încărcate de cetățeni / atașate în MAIN | `C:\Cimitir\data\media\cerere\<data>_<nume>.pdf` (la fel `media\loc`, `media\inhumat`, `media\aviz`, `media\contract`) |
| Documente descărcate temporar de MAIN la sincronizare | `C:\Cimitir\data\media\_cloud_tmp` → apoi copiate în `media\cerere` |
| Secret sesiuni (dacă `JWT_SECRET` nu e setat) | `C:\Cimitir\data\.jwt_secret` |
| Log portal pornit de `start_local.py` | `C:\Cimitir\data\logs\portal.log` |
| Copii de rezervă automate | `C:\Cimitir\data-backups\cimitir-AAAALLZZ-OOMMSS\` (`cimitir.db` + `media\` + `manifest.json` cu sume SHA-256); implicit lângă folderul de date, **alege un disc extern** |
| Restaurare programată / stare anterioară | `C:\Cimitir\data.restore-pending.json` · `C:\Cimitir\data.inainte-de-restaurare-<dată>\` |
| E-mailuri în modul `mock` | `C:\Cimitir\data\logs\mock_mail.log` (aici găsești și linkurile de resetare) |
| Log aplicație | `%LOCALAPPDATA%\Cimitir\logs\cimitir.log` (sau `CIMITIR_LOG_DIR`) |
| Licență / config AI (MAIN) | `%APPDATA%\CimitirDesktop\license.key`, `ai_config.json` |

**Mod online:** serverul are propria bază `C:\Cimitir\server-data\cimitir.db` + `media\cerere\` (fișierele cetățenilor), iar MAIN păstrează `C:\Cimitir\main-data\cimitir.db`
+ `media\`. Fișierele cererilor sunt copiate în MAIN (verificare mărime + SHA-256); după confirmarea preluării, serverul șterge fișierele și datele personale ale cererii și păstrează doar starea (cetățeanul vede „preluat de instituție").
Afișează căile efective cu: ✅
```powershell
# Director: C:\Cimitir\app\desktop
$env:CIMITIR_DATA_DIR = "C:\Cimitir\data"
python -c "import sys; sys.path.insert(0,'..\shared'); import cimitir_config as c; print(c.db_path()); print(c.data_dir())"
```

## 6. Sincronizare MAIN ↔ server (modul online)

Pe același PC folosești **două ferestre PowerShell** (variabilele sunt pe fereastră). MAIN acceptă `http://` doar pentru localhost; un server real trebuie să aibă `https://`.

```powershell
# FEREASTRA 1 — SERVER.  Director: C:\Cimitir\app\desktop                                     ✅
cd C:\Cimitir\app\desktop
.\..\.venv\Scripts\Activate.ps1
$env:CIMITIR_MODE = "online"
$env:CIMITIR_DATA_DIR = "C:\Cimitir\server-data"
$env:CIMITIR_PORT = "8000"
# cheia (se afișează o singură dată; reapelarea o rotește și o invalidează pe cea veche). Merge direct în clipboard:
$key = (python ..\scripts\issue_sync_key.py --source cimitir | Select-Object -Last 1); Set-Clipboard -Value $key; Remove-Variable key
python start_local.py --portal-only
```

```powershell
# FEREASTRA 2 — MAIN (configurare fără interfață).  Director: C:\Cimitir\app                 ✅
cd C:\Cimitir\app
.\.venv\Scripts\Activate.ps1
$env:CIMITIR_MODE = "local"
python scripts\configure_main_sync.py --data-dir "C:\Cimitir\main-data" --url "http://127.0.0.1:8000" --key (Get-Clipboard) --auto
python scripts\configure_main_sync.py --data-dir "C:\Cimitir\main-data" --test    # trebuie să afișeze conexiune reușită (cod de ieșire 0)
```

Din interfață (🪟): MAIN → bara de sus **„☁ Sincronizare cloud"** → grupul „Conexiune server": **Adresă server** și **Cheie sincronizare** (lipește cheia) →
**Salvează** → **Testează conexiunea** → **Sincronizează acum** (opțional „Sincronizează automat la fiecare 5 minute"). Pornește MAIN cu:
```powershell
# FEREASTRA 2.  Director: C:\Cimitir\app\desktop
cd C:\Cimitir\app\desktop
$env:CIMITIR_DATA_DIR = "C:\Cimitir\main-data"
$env:CIMITIR_MODE = "local"
python start_local.py --desktop-only
```
Server real cu certificat propriu: `$env:REQUESTS_CA_BUNDLE = "C:\Cimitir\ca-server.pem"` în fereastra MAIN. Ce a rulat efectiv: aceleași scripturi + `cloud_sync.sync_all()` (funcția butonului), inclusiv varianta HTTPS cu certificat autosemnat (`test_etapa5_main_authority.py`). ✅
Monitorizare pe server: `http://127.0.0.1:8000/admin/monitorizare` (client MAIN, ultima sincronizare reușită, erori, operațiuni restante, alertă offline după 60 min).

## 7. Test complet: cerere cu document → MAIN → aprobare → portal

**Pregătire (🪟):** în MAIN adaugă pe hartă un loc liber (de ex. `A-1`, disponibilitate „Liber"); în modul online apasă **Sincronizează acum** ca locul să apară în `/portal/harta`.

1. **Portal** `http://127.0.0.1:8000/portal/cont` → creează cont (email + parolă ≥ 10 caractere) → autentifică-te. ✅ (API)
2. `http://127.0.0.1:8000/portal/cerere` → completează nume, prenume, CNP, telefon, cod loc `A-1`, bifează informarea → atașează un PDF/JPG/PNG → **Trimite**; notează codul de urmărire. ✅ (API cu PDF; fără cont cererea este refuzată cu 401)
3. Verifică status: `/portal/cont` → cererea apare „Înregistrată". ✅
4. **Mod online:** în MAIN, **☁ Sincronizare cloud** → **Sincronizează acum** (documentul se descarcă în `media\cerere`). **Mod local:** nu e nevoie, cererea e deja în baza comună. ✅ (funcția) · 🪟 (butonul)
5. MAIN → **📥 Cereri online** → selectează cererea → vezi documentul (📎) → **✅ Validează → adaugă în concesionar** (creează concesionarul, contract draft, rezervă locul). ✅ (aceeași funcție `approve_cerere`) · 🪟 (fereastra și butonul)
6. **Mod online:** din nou **Sincronizează acum** (trimite decizia la server). ✅
7. **Portal:** `/portal/cont` → starea devine „Aprobată (contract în pregătire)"; `http://127.0.0.1:8000/api/public/cerere/<cod>` arată `validat`; locul apare rezervat pe `/portal/harta`. ✅
8. Verifică că `/admin/cereri` doar consultă cererea (fără butoane de aprobare). ✅

## 8. Resetare parolă cetățean, alertă MAIN offline, copii de rezervă

**Resetare parolă cetățean** (`/portal/cont` → „Ai uitat parola?"): cetățeanul introduce emailul și primește un link `…/portal/cont?reset=<token>` valabil 1 oră, de unică folosință;
răspunsul este același dacă emailul nu are cont; limitat la 3/oră per email și 10/oră per IP; după resetare sesiunile vechi se invalidează. ✅
Fără SMTP (furnizor `mock`) nu pleacă e-mail: linkul se citește din `data\logs\mock_mail.log`. Pentru trimitere reală, în fereastra serverului:
```powershell
# Director: C:\Cimitir\app\desktop    (valorile sunt ale furnizorului tău de e-mail; nu le pune în repo)
$env:SMTP_HOST = "smtp.furnizor.ro"; $env:SMTP_PORT = "587"; $env:SMTP_SECURITY = "starttls"
$env:SMTP_USER = "cont@furnizor.ro"; $env:SMTP_PASSWORD = Read-Host "Parola SMTP"; $env:SMTP_FROM = "Cimitir <cont@furnizor.ro>"
python start_local.py --portal-only
```
apoi `/admin/setari` → „Furnizor e-mail" = `smtp`, „Adresa publică a portalului" = `https://…`. Trimiterea SMTP reală: 🪟 (netestată cu un server SMTP real).

**Alertă MAIN offline:** în `/admin/setari` → „E-mailuri pentru alerte" (maxim 5, separate prin virgulă). Serverul (modul online) verifică la 5 minute și trimite **o singură alertă
per episod** când un MAIN nu a mai contactat serverul peste 60 minute; apare și în `/admin/monitorizare` („alertă pe email trimisă"). ✅ (cu e-mail mock)

**Copii de rezervă:** MAIN face zilnic, în fundal, o copie consistentă a bazei și a documentelor (păstrează 14 zile, minimum 3 copii). Serverul face la fel din procesul portalului
(`/admin/setari` → „Copii de rezervă automate"). Dacă PC-ul e oprit la ora respectivă, copia se face la următoarea pornire (cel mult o copie la 24 h). ✅
**Restaurare dintr-un clic:** MAIN → **💾 Copii de rezervă** → selectezi copia → **Verifică selecția** → **Restaurează selecția…** → confirmi; aplicația se închide și restaurarea se aplică
la următoarea pornire (starea curentă rămâne în `data.inainte-de-restaurare-<dată>`). Pe server: `/admin/monitorizare` → „Copii de rezervă ale serverului" → **Restaurează**, apoi repornești portalul. ✅ (logica) · 🪟 (fereastra MAIN)
Dacă pe Windows restaurarea nu se poate aplica pentru că un alt proces ține fișierele deschise (MAIN + portal local pornite simultan), mesajul cere să închizi ambele și să pornești din nou. 🪟
Din linie de comandă rămân: `python scripts\backup_restore.py backup|verify|restore`.

## 9. Ce NU am putut verifica și rămâne pentru tine pe Windows
* Sintaxa/comportamentul comenzilor PowerShell (`Activate.ps1`, `Set-Clipboard`, `Select-String`), politica de execuție, firewall-ul la modul „Server local".
* Instalarea pachetelor pe Windows (PySide6, PyMuPDF, opencv), licența MAIN (HWID) și toate ferestrele MAIN (login cu schimbare de parolă, Cereri online, Sincronizare cloud).
* Build-ul `.exe`/instalatorul: vezi `WINDOWS_VERIFICARE.md`.
