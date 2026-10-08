# Moduri de rulare: LOCAL și ONLINE

Un singur portal (`backend/server.py`) și un singur punct de pornire (`desktop/start_local.py`, apelat și de
`launcher.py` / `Cimitir.exe`). Aprobarea/respingerea cererilor și crearea contractelor se fac **numai în MAIN**.

## Rezumat

| | LOCAL | ONLINE |
|---|---|---|
| Unde rulează | MAIN + portal pe același calculator | MAIN la cimitir; serverul (portal + admin) public |
| Baza | **aceeași** `cimitir.db` (SQLite, WAL) | MAIN: SQLite locală · Server: SQLite proprie, doar proiecție publică + cereri în tranzit |
| Legătura | fișierul comun | HTTPS `/api/sync/*` (cheie `X-Sync-Key`) |
| `CIMITIR_MODE` | `local` (implicit) | `online` **doar pe server** |
| Rezultatul unei decizii în portal | imediat (aceeași bază) | la următoarea sincronizare |
| Configurare | `config/cimitir-local.env.example` | `config/cimitir-online-server.env.example` (server), `config/cimitir-online-main.env.example` (MAIN) |

Fișierele din `config/` sunt exemple noi; **nu** înlocuiesc `.env` / `backend/.env`. Se folosesc cu
`CIMITIR_ENV_FILE=<calea fișierului copiat>` (variabilele deja setate în mediu au prioritate).

## Modul LOCAL

```powershell
cd <repo>\desktop
pip install -r requirements.txt -r web_requirements.txt
$env:CIMITIR_DATA_DIR = "C:\Cimitir\data"
python start_local.py                      # portal + desktop (MAIN) pe aceeași bază
python start_local.py --portal-only        # doar portalul
python start_local.py --desktop-only       # doar MAIN
python launcher.py                         # ecranul de pornire: „Local" = MAIN + portal pe 127.0.0.1
```

URL-uri (port implicit 8000): portal `http://127.0.0.1:8000/portal` · hartă `/portal/harta` · cerere `/portal/cerere` ·
cont cetățean `/portal/cont` · administrare personal `http://127.0.0.1:8000/admin/access` · health `/api/public/health`.
Pe acest mod `/api/sync/*` răspunde 404 (nu există sincronizare).

## Modul ONLINE

**Server** (Linux/Windows, în spatele unui proxy HTTPS sau cu `--ssl-keyfile/--ssl-certfile`):

```bash
export CIMITIR_ENV_FILE=/etc/cimitir/server.env     # din config/cimitir-online-server.env.example
python scripts/issue_sync_key.py --source cimitir   # cheia, afișată o singură dată
cd backend && python -m uvicorn server:app --host 127.0.0.1 --port 8000 --no-proxy-headers   # în spatele unui proxy: vezi „Limitare de rată"
```

**MAIN** (calculatorul din cimitir): meniul „Sincronizare cloud" sau, fără interfață:

```powershell
python scripts\configure_main_sync.py --data-dir C:\Cimitir\data --url https://cimitir.exemplu.ro --key cimitir.<secret> --auto
python scripts\configure_main_sync.py --data-dir C:\Cimitir\data --test
```

MAIN refuză `http://` (în afară de localhost) și, implicit, certificate nevalide; pentru un CA intern se setează
`REQUESTS_CA_BUNDLE`. Operațiunile eșuate rămân în coadă și se reiau la reconectare.

URL-uri server: `https://<domeniu>/portal` · `/admin/access` · `/api/public/health` · `/api/sync/ping` (cu cheie).

## Autoritatea MAIN

* `POST /admin/ui/cerere/{id}/status` și `POST /admin/ui/loc/{cod}/contract` răspund **403** `{"error": "main_only"}`
  în backend (după verificarea autentificării); butoanele sunt înlocuite cu un mesaj explicativ.
* Canalul MAIN → server (`POST /api/sync/requests/{cod}/status`, cheie de sincronizare) rămâne singura cale prin care
  portalul primește „validat / respins / completări".
* Panoul web poate consulta cererile, documentele și monitorizarea.

## Monitorizare (`/admin/monitorizare`, drept `manage_config`)

Fiecare MAIN trimite `X-Sync-Client: main-xxxxxxxxxx` (generat o dată) și, la fiecare sincronizare, `POST /api/sync/status`
cu ultima sincronizare reușită, eroarea curentă și numărul operațiunilor locale restante. Pagina arată: client MAIN, ultima
activitate, ultima sincronizare reușită, operațiuni restante **la momentul raportului**, ultima eroare (marcată „rezolvată"
după recuperare) și **„Informație veche"** când raportul are peste 15 minute (`sync_stale_minutes`).
Dacă MAIN nu poate ajunge la server, nu poate raporta: coada rămâne locală și se raportează la reconectare.

Alerta „MAIN offline": dacă un client MAIN nu a contactat serverul de mai mult de 60 minute (`sync_offline_alert_minutes`),
`/admin/monitorizare` afișează un banner roșu cu clientul și ultima lui activitate; în modul online, fără niciun client,
apare avertismentul „Niciun MAIN nu a contactat încă serverul".

## Limitare de rată (înainte de lansare publică)

| Ce | Limită | Răspuns |
|---|---|---|
| Cheie de sincronizare greșită (inclusiv format invalid / cimitir necunoscut), per IP | 5 eșecuri → blocat 5 minute | 401, apoi 429 + `Retry-After` (cheia corectă rămâne utilizabilă) |
| Login cetățean: per (email, IP) | 5 eșecuri → 5 minute | 429 |
| Login cetățean: eșecuri per IP, oricare email | 20 / 15 minute | 429 + `Retry-After` |
| Înregistrare cont cetățean, per IP | 10 / oră | 429 |
| Depunere cerere, per IP | 20 / oră | 429 |
| Completări la cerere, per IP | 20 / oră | 429 |

Contoarele sunt în tabela `settings` (valabile pe mai multe procese). IP-ul clientului este cel văzut de uvicorn:
* expus direct: `uvicorn server:app --no-proxy-headers` (implicit în `start_local.py`); antetul `X-Forwarded-For` este ignorat, deci nu poate fi folosit pentru a ocoli limitele;
* în spatele nginx/Caddy: `uvicorn server:app --proxy-headers --forwarded-allow-ips <ip-proxy>` (în `start_local.py`: `CIMITIR_BEHIND_PROXY=1`, `FORWARDED_ALLOW_IPS=<ip-proxy>`).
Fără una dintre aceste opțiuni, uvicorn implicit (`127.0.0.1` de încredere) acceptă `X-Forwarded-For` de la procese locale.
Limitele sunt constante în `backend/portal_ratelimit.py` (`LIMITS`). Login-ul de personal are blocare per utilizator (5 / 5 min).

## Cod vechi eliminat

`desktop/web_server.py` conține doar scanarea de pe telefon (`/mobile-scan`, `/api/ai-scan-*`). API-ul operator vechi,
paginile `/public/loc` (acum redirect în portal: `/public/loc/<cod>` → `/portal/loc/<cod>`) și `web_build/` (React) au fost șterse.

## Parola implicită

Regula este una singură, în `shared/cimitir_auth.py` (`DEFAULT_USERS`, `is_default_password`,
`password_change_required`). Web: redirect forțat la `/admin/schimba-parola`. MAIN: `LoginWindow` cere parola nouă
(minim 8 caractere, diferită de cele implicite) și refuză accesul dacă dialogul e închis; `main.py` re-verifică după login.

## Teste

```bash
cd backend && python -m pytest tests -q --ignore=tests/test_live_smoke.py        # include test_etapa5_main_authority.py
cd desktop && QT_QPA_PLATFORM=offscreen python -m pytest tests -q --ignore=tests/test_qr_scanning.py
```

| Fișier | Ce acoperă |
|---|---|
| `desktop/tests/test_startup_unification.py` | pornire unificată (start_local, `launcher.py --portal`, `ensure_portal`), spec/installer, **modul LOCAL** pe aceeași bază |
| `desktop/tests/test_main_password_gate.py` | `LoginWindow` real (offscreen): dialog obligatoriu, renunțare, parolă slabă, succes |
| `backend/tests/test_etapa5_main_authority.py` | 403 la aprobare/contract din web, monitorizare, **modul ONLINE** (server real), HTTPS cu certificat autosemnat |
| `backend/tests/test_etapa2_sync.py`, `test_etapa3_requests.py` | sincronizare hartă/personal/cereri, flux complet cerere → MAIN → cetățean |

## Build Windows

Verificarea pe Windows (CI + instalare + pași manuali) este în `WINDOWS_VERIFICARE.md`; scriptul `desktop/installer/verify_install.ps1`
se copiază în folderul de instalare și face verificările automate pe PC.

Verificat aici (Linux): spec evaluat pe cele 4 combinații (`test_build_packaging`), PyInstaller rulează pe același
`cimitir.spec`, iar executabilul rezultat pornește portalul (`Cimitir --portal`) cu șabloanele incluse. **Neverificat aici:**
build real pe Windows, Inno Setup, semnare. Pașii se rulează în CI (`.github/workflows/build-windows.yml`: teste, build,
smoke test `Cimitir.exe --portal` + `/portal`, `/admin/access`) sau local cu `desktop\build_exe.bat`.

## Ghid pas cu pas pe Windows

Instalare, comenzi PowerShell, URL-uri, conturi, `.env`, căi, configurarea sincronizării și testul complet cerere → MAIN → portal:
`GHID_WINDOWS.md`. Recuperare/deblocare cont de personal: `scripts/reset_admin_password.py`.

## Resetare parolă cetățean, alertă MAIN offline, copii de rezervă

* `POST /api/public/account/forgot` (răspuns generic, 3/oră per email, 10/oră per IP) și `POST /api/public/account/reset` (token de unică folosință, 1 oră, hash în `citizen_resets`;
  sesiunile vechi se invalidează). Trimitere: `shared/cimitir_notify.py` (`mock` → `<DATA_DIR>/logs/mock_mail.log`; `smtp` → `SMTP_*`).
* Alertă offline: `backend/portal_jobs.py` (fir de fundal, la 5 minute, `CIMITIR_JOBS=0` îl oprește) → `sync_api.check_offline_alerts()` către `alert_emails` (setare din `/admin/setari`), o alertă per episod.
* Copii: `shared/cimitir_backup.py` (`run_scheduled`, `prune`, `schedule_restore`, `apply_pending_restore`); MAIN: `desktop/backup_window.py`; server: `/admin/monitorizare`. Restaurarea se aplică la pornire, înainte de deschiderea bazei.
