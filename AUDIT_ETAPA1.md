# Etapa 1 — Audit și stabilizare Cimitir (raport)

Data: 2026-07 · Lucrat pe codul din `github.com/paulwalker0727/appdeincercat` (commit `e66da18`), pe Linux,
cu date fictive. Nimic nu a fost rulat pe Windows și nu s-a atins vreun `cimitir.db` real.

## 1. Ipoteze: confirmate / infirmate (cu dovadă)

| # | Ipoteză | Verdict | Dovadă |
|---|---|---|---|
| 1 | `start_local.py` lansează `server.py`, care nu pornește Uvicorn | **Infirmată în forma dată**, dar există defecte reale | `server.py` are `if __name__ == "__main__": uvicorn.run(...)`; rulat: `Uvicorn running on http://0.0.0.0:8123`. Defecte: asculta pe **0.0.0.0** ignorând `--host` (panoul cu date personale expus în LAN), fără health-check, fără verificare port/erori, portalul eșuat nu oprea desktopul |
| 2 | Conflict de rute `/portal` | **Infirmată ca rulare, confirmată ca duplicare** | `/portal` e definit o singură dată în backend. Dar există a doua implementare (`desktop/web_public.py` + `desktop/web_server.py`, server LAN) și paginile admin erau la rădăcină (`/`, `/access`, `/harta`…), fără prefix `/admin` |
| 3 | Schemă SQLite duplicată desktop/web | **Rezolvată deja în P1** (confirmat) | `desktop/database.py` și `backend/cimitir/database.py` sunt adaptoare; `CREATE TABLE` doar în `shared/cimitir_database.py` (test existent). Rămâne oglinda `mongo_store.py` (vezi §5) |
| 4 | `CIMITIR_DATA_DIR` inconsistent | **Confirmată** | Fără variabilă: desktop → `desktop/cimitir.db`, backend → `backend/data/cimitir.db`. În `.exe` (frozen) desktopul punea baza în folderul aplicației (`/tmp/repo/desktop/cimitir.db` simulat) în loc de `%LOCALAPPDATA%\Cimitir` |
| 5 | Module orfane | **Confirmată parțial** | `cloud_sync*`: client pentru un server cloud „CimiPlus" absent din repo (neverificabil aici). `mongo_store.py`: 55/72 funcții (lipsesc poze, ghiduri, rânduri, date extra…), neutilizat de desktop. `desktop/web_server.py`: servește `web_build/` care **nu există** în repo; folosit doar de `launcher.py` (mod server LAN) și scanarea mobilă AI |
| 6 | Căi/importuri Windows | **Verificat doar prin citire** | `%APPDATA%`/`%LOCALAPPDATA%` tratate corect; HWID folosește `wmic` (absent implicit din Windows 11 24H2+) → de verificat manual |

## 2. Defecte suplimentare găsite (nu erau în ipoteze)

| Sev. | Defect | Dovadă | Stare |
|---|---|---|---|
| Înalt | `/access/create-db` și `/access/upload-db` **fără autentificare** | original: `POST /access/create-db` → 303 și sursă nouă creată | Reparat: necesită admin (401) |
| Înalt | Rolul `user` (read-only în desktop) putea modifica totul în web | original: `user/user123` → `POST /ui/loc/Z-1/edit` = 200, sector=HACK | Reparat: 403 pentru `user`, admin-only pentru utilizatori |
| Înalt | Pornire simultană pe bază nouă: `sqlite3.OperationalError: database is locked` la `PRAGMA journal_mode=WAL` | test cu 4 procese: 3 din 5 rulări au picat | Reparat (`_ensure_wal` + retry); 8/8 rulări OK |
| Înalt | `delete_document` șterge fișierul de pe calea absolută din DB → o **copie de lucru a DB șterge fișierele originalului** | cod `os.remove(file_path)`; căile sunt absolute | Reparat: șterge doar din `<DATA_DIR>/media`; `scripts/copy_db_for_testing.py` rescrie căile în copie |
| Mediu | `pip install -r requirements.txt -r web_requirements.txt` nu e suficient: `server.py` cerea `python-dotenv`, `pymongo`, `pandas`, `jinja2` | venv curat cu `web_requirements.txt` original: `ModuleNotFoundError: dotenv` | Reparat: `python-dotenv`/`pandas`/`pymongo` eliminate din calea obligatorie; `jinja2`, `openpyxl` adăugate; verificat în venv curat |
| Mediu | Pagina de login afișa parola precompletată `admin/admin123` + indiciu | `value="admin123"` în HTML | Reparat. **Parolele implicite încă se creează la prima inițializare** (schimbă-le) |
| Mediu | `/access` interoga MongoDB la fiecare încărcare: 4,03 s fără Mongo | măsurat | Reparat: Mongo opt-in (`CIMITIR_ENABLE_MONGO=1`); 0,01 s |
| Mediu | Selectorul de login arăta (neautentificat) numărul de locuri/contracte/cereri pe fiecare bază | template | Reparat |
| Mediu | CORS `*` pe tot; `JWT_SECRET` aleator la fiecare pornire (sesiuni pierdute) | cod | Reparat: fără CORS implicit; secret persistat în `<data>/.jwt_secret` |
| Mediu | Desktop: 8 teste „izolate" scriau de fapt în baza reală (monkeypatch pe adaptor nu ajungea la nucleu) | 17 eșecuri → 9 după fix | Reparat: adaptoarele sunt alias la modulul comun |
| Mediu | `cimitir.spec` nu includea `shared/` → `.exe` ar fi eșuat la `import cimitir_database` | grep: zero referințe `shared` | Reparat în spec (neconstruit aici — nu există PyInstaller/Windows) |
| Scăzut | Portal: `limit` necontrolat, câmpuri/fișiere fără limită, status cerere acceptat liber | cod | Reparat (limit ≤200, 2000 car., 10 fișiere, status ∈ nou/validat/respins) |
| Scăzut | `backend/cimitir/cimitir.db` (gol) și `backend/data/cimitir.db` (duplicat demo) versionate | `git ls-files` | Scoase; `.gitignore` actualizat |

## 3. Arhitectura finală

- **MAIN (PySide6, `desktop/`)** — evidență completă; aprobă/respinge cereri (`cereri_online_window.py`); nu rulează server. Deschide `CIMITIR_DATA_DIR/cimitir.db`.
- **`shared/cimitir_config.py`** — singurul loc pentru `CIMITIR_DATA_DIR`, `.env`, host/port, `JWT_SECRET`. Loghează calea absolută a bazei la pornire (`[config] backend: db=…`, `[main] DB path: …`).
- **`shared/cimitir_database.py`** — unicul modul de schemă/CRUD (WAL, `busy_timeout=15s`, `foreign_keys`). `desktop/database.py` și `backend/cimitir/database.py` = alias.
- **Backend FastAPI (`backend/server.py`)** — `/portal/*`, `/api/public/*` (public), `/admin/*`, `/api/admin/*` (autentificat), `/` → `/portal`.
- **`desktop/start_local.py`** — config → uvicorn (`sys.executable -m uvicorn`, fără shell) → health-check → desktop.
- **Legacy (neschimbat, dublură documentată):** `desktop/web_server.py` + `desktop/web_public.py` (LAN, fără UI), `cloud_sync*`, `mongo_store.py` (opt-in).

## 4. Tabel endpointuri

Existente (verificate prin inventar automat, 50 rute):

| Prefix | Rute |
|---|---|
| Public (10) | `GET /portal`, `/portal/harta`, `/portal/cerere`, `/portal/loc/{cod}`; `GET /api/public/health`, `/search`, `/map`, `/loc/{cod}`; `POST /api/public/cerere`; **nou** `GET /api/public/cerere/{cod_urmarire}` |
| Admin (40) | `GET /admin/`, `access`, `logout`, `harta`, `locuri`, `inhumati`, `contracte`, `concesionari`, `plati`, `cereri`, `rapoarte`, `utilizatori`, `setari`, `export/locuri.xlsx`, `file/document/{id}`, `loc/{cod}/fisa.pdf`, `ui/loc/{cod}`; `POST /admin/access/login|create-db|upload-db`, `ui/loc/{cod}/edit|inhumat|contract|plata|aviz|document`, `ui/*/{id}/delete`, `ui/concesionar`, `ui/cerere/{id}/status`, `ui/user…`, `ui/settings`; `GET /api/admin/map.json`, `/api/admin/map/background` |

Lipsă (neimplementate în Etapa 1): API JSON admin pentru cereri/locuri/contracte (`/api/admin/*` are doar harta); limitare de încercări la login (brute-force); rate-limit/CAPTCHA pe `POST /api/public/cerere`; validare tip/extensie la upload-ul din admin; HTTPS/`Secure` cookie (Etapa 2); rotirea/forțarea schimbării parolelor implicite; notificare cetățean la schimbarea statusului.

Redirecționările vechi (`/access`, `/harta`, …) nu mai există: linkurile/favoritele vechi dau 404.

## 5. Evaluare MongoDB online

SQLite rămâne motorul. `mongo_store.py` acoperă 55 din 72 funcții ale nucleului SQLite (lipsesc poze, ghiduri, rânduri, date extra, cod de urmărire), desktopul nu îl folosește, iar `cloud_sync` vorbește cu un server extern absent din repo. Nicio dovadă pentru schimbarea motorului. Mongo este acum doar opt-in.

## 6. Comenzi exacte și rezultate reale (Linux, Python 3.11)

```
cd backend && python -m pytest tests -q
32 passed                                   # 8 existente (actualizate) + 24 noi
cd desktop && QT_QPA_PLATFORM=offscreen python -m pytest tests -q --ignore=tests/test_qr_scanning.py
193 passed, 9 failed, 2 skipped             # înainte: 185 passed, 17 failed
python desktop/start_local.py --portal-only --data-dir /root/ct_work/run1 --port 8130
[local] Bază de date comună: /root/ct_work/run1/cimitir.db
[local] Portal pornit (pid 1640); aștept health-check http://127.0.0.1:8130/api/public/health
[local] Portal OK: http://127.0.0.1:8130/portal   (administrare: http://127.0.0.1:8130/admin)
curl /portal → 200 · /api/public/health → {"ok":true} · /admin/ (anonim) → 303 · /admin/access/create-db (anonim) → 401
```

Cele 9 eșecuri rămase în `desktop/tests` existau și înainte, nu țin de aceste modificări: 8 din `test_build_packaging.py` (lipsesc PyInstaller, `.github/workflows/build-windows.yml` nu e în repo, `build_exe.bat` are LF după clonare pe Linux) și 1 din `test_qr_custom_and_map.py::test_default_keys_roundtrip` (trece izolat, pică în suita completă: stare partajată între module de test).
`test_qr_scanning.py` nu a rulat (lipsește `pyzbar`).

Sesiune de browser (Playwright) pe `start_local`: login `/admin/access` → `/admin/` → `/admin/harta` randate corect.

## 7. Ce s-a testat efectiv / ce rămâne manual

**Testat efectiv (Linux, date fictive, automat):** inventar rute și prefixe; toate rutele `/admin` și `/api/admin` resping anonimul; confidențialitate portal (CNP, adresă, telefon, email, BI, nume concesionar/solicitant absente din toate răspunsurile publice); portalul scrie doar în cereri/documente; cod de urmărire; limite portal; roluri; creare/încărcare DB doar admin; același `cimitir.db` pentru desktop/backend/shared; `.env`; `JWT_SECRET` stabil; log cu calea absolută; WAL cu 4 procese concurente (100 scrieri, 0 pierderi); `start_local` (health-check, port ocupat, portal mort); cerere depusă în portal vizibilă în procesul desktop; hartă/locuri/concesionari/contracte/inhumați/documente/export Excel/PDF contract pe date fictive; copia de lucru; protecția la ștergere; instalare în venv curat.

**De verificat manual pe Windows:** `start_local.py` cu PowerShell (comenzile din `LOCAL_SYNC.md`); fereastra MAIN reală (hartă, editor, cereri online) — aici doar `offscreen`; HWID (`wmic` pe Windows 11 24H2+), activare licență, `%APPDATA%\CimitirDesktop\license.key`; build PyInstaller cu `shared/` și instalare Inno Setup (datele trebuie să ajungă în `%LOCALAPPDATA%\Cimitir`); comportamentul SQLite WAL pe discul real al operatorilor; firewall/antivirus pentru uvicorn pe 127.0.0.1; migrarea datelor dacă folosiți dev-mode: **implicitul din surse s-a mutat la `./shared_data`** — setează `CIMITIR_DATA_DIR` spre baza existentă.

**Nefăcut:** verificare independentă de un tester uman (pasul 9); Etapa 2 (hosting, HTTPS, brute-force).

## 8. Riscuri și decizii

- Portalul public nu trebuie expus pe internet de pe același PC cu baza cu date personale; separarea rutelor nu rezolvă asta (Etapa 2).
- Mai mulți operatori: WAL + `busy_timeout` testate; pe termen lung un singur proces care scrie.
- Parolele implicite încă se creează; fără limitare de încercări la login.
