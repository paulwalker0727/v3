# Mod local integrat — Desktop + portal web

## Principiu

MAIN (PySide6) este sursa de adevăr. Portalul/administrarea web deschid **exact același fișier SQLite**
(`<CIMITIR_DATA_DIR>/cimitir.db`), prin `shared/cimitir_database.py`. Folderul se stabilește într-un singur
loc: `shared/cimitir_config.py` (variabila `CIMITIR_DATA_DIR` sau `.env`).

| Prefix | Rol | Autentificare |
|---|---|---|
| `/portal/*` | pagini publice: hartă, căutare, cerere, urmărire | nu |
| `/api/public/*` | JSON public; scrie doar în `cereri_concesionare` (+ `documents` ale cererii) | nu |
| `/admin/*` | panou personal (HTML/HTMX) | da (cookie JWT) |
| `/api/admin/*` | JSON administrare | da |

`/` redirecționează la `/portal`.

## Pornire (PowerShell)

```powershell
cd <repo>\desktop
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r web_requirements.txt
Copy-Item ..\.env.example ..\.env
$env:CIMITIR_DATA_DIR = "C:\Cimitir\data-test"
python start_local.py --data-dir $env:CIMITIR_DATA_DIR
```

`start_local.py`: citește configurarea, pornește `python -m uvicorn server:app` (cu `sys.executable`, fără shell),
așteaptă `GET /api/public/health`, apoi deschide desktopul. Dacă portul e ocupat sau portalul cade, iese cu cod
3/4 și afișează jurnalul (`<data>/logs/portal.log`). Opțiuni: `--portal-only`, `--desktop-only`, `--host`, `--port`.

Portal: `http://127.0.0.1:8000/portal` · Administrare: `http://127.0.0.1:8000/admin`.

## Copie de lucru pentru teste

```powershell
python ..\scripts\copy_db_for_testing.py "C:\Cimitir\data" "C:\Cimitir\data-test"
```

Originalul se deschide read-only; media se copiază și căile din `documents`/`photos` se rescriu spre copie.

## Reguli

- Nu rula baza pe OneDrive/Dropbox/Google Drive/SMB (pornirea avertizează).
- Mai mulți operatori simultan: WAL + `busy_timeout=15s` sunt active (testat cu 4 procese × 25 scrieri).
  Pentru peste câțiva operatori, pe termen lung, un singur proces care deține scrierea.
- Rolul `user` este read-only în `/admin`; `operator` și `admin` pot modifica; doar `admin` gestionează utilizatori.
- Parolele implicite `admin/admin123` și `user/user123` se creează la prima inițializare a bazei: schimbă-le.

## Protecția datelor

Portalul public nu citește și nu întoarce CNP, adresă, telefon, email, documente sau date de autentificare
(verificat automat în `backend/tests/test_etapa1_audit.py`). Nu expune panoul `/admin` pe internet de pe același PC
cu baza cu date personale (decizie de hosting: Etapa 2).
