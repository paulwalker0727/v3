# Cimitir Web — versiunea 100% Python (FastAPI + Jinja2 + HTMX)

Aceasta este partea `cimitir-web` rescrisă complet în **Python**, fără React/Node.
Interfața este randată server-side cu **Jinja2** + puțin **HTMX**. Baza de date
este **SQLite** (fișierele `.db` din folderul `data/`).

## Structură
```
backend/
├── server.py            # Aplicația FastAPI + toate rutele (pagini HTML + acțiuni)
├── .env                 # Config (JWT_SECRET etc.)
├── requirements.txt     # Dependențe Python
├── cimitir/             # Stratul de date (SQLite) — neschimbat
│   ├── database.py, store.py, auth.py, web_public.py, fisa_pdf.py, ...
├── data/                # Folder de runtime (baze SQLite, nu se versionează); demo: `python scripts/make_demo_db.py`
├── templates/           # Pagini Jinja2 (dashboard, harta, locuri, ...)
└── static/              # app.css + app.js
```

## Rulare locală (simplu)
```bash
cd backend
pip install -r requirements.txt
python server.py
```
Apoi deschide în browser: **http://127.0.0.1:8000/portal** (public) sau **http://127.0.0.1:8000/admin** (administrare). Recomandat: `python desktop/start_local.py` (vezi `LOCAL_SYNC.md`).

(sau: `python -m uvicorn server:app --port 8000`)

## Mod integrat cu aplicația desktop

Pentru ca portalul să citească harta și locurile direct din aplicația desktop
și ca cererile online să apară în MAIN fără import/export de baze, pornește
pachetul comun din rădăcina proiectului:

```bash
python desktop/start_local.py --data-dir "CALEA_CĂTRE_FOLDERUL_CU_cimitir.db"
```

Desktopul și portalul vor folosi aceeași variabilă `CIMITIR_DATA_DIR`. Detalii
complete despre pornire și protecția datelor sunt în `../LOCAL_SYNC.md`.

## Autentificare
| Rol   | Utilizator | Parolă   |
|-------|------------|----------|
| admin | admin      | admin123 |
| user  | user       | user123  |

## Funcționalități (toate păstrate din versiunea React)
- **Acces** — selectare bază de date SQLite (+ creare / import `.db`) și login
- **Panou principal** — KPI, distribuție status, contracte în expirare, cereri, înhumări recente
- **Hartă cimitir** — SVG cu zoom/pan, filtrare pe status, căutare, click pe loc → fișă
- **Locuri de veci** — tabel + filtre + căutare + export Excel
- **Înhumați / Contracte / Plăți** — tabele, click → fișa locului
- **Concesionari** — CRUD (adaugă / editează / șterge)
- **Cereri online** — validare/respingere, opțional creare concesionar + contract
- **Rapoarte** — sumar, ocupare pe sectoare, contracte în expirare, export Excel
- **Utilizatori** (admin) — creare, schimbare parolă, ștergere
- **Setări** — denumire cimitir
- **Fișă loc PDF**, **Portal public** (`/portal`), documente/fotografii pe locuri

## Notă despre preview-ul de pe platformă
Mediul Emergent afișează în preview doar portul 3000 (React). Această aplicație
este 100% Python și rulează pe portul backend (8001 aici / 8000 local), deci se
testează cel mai bine **local** cu comanda de mai sus.
