# Etapele 5–9 — instrucțiuni de testare pe Windows și pe telefon (pașii NU au fost executați pe Windows/telefon real)

> **Stare la livrare:** tot ce este listat aici este **NETESTAT** pe Windows și pe telefon real. Mediul agentului a fost Linux (PostgreSQL 15 local, Qt offscreen). Nu marcați PASS decât după execuție.
> Completează cu `TEST_LAPTOP_WINDOWS_DOCKER.md`, `ETAPA789_INSTRUCTIUNI_WINDOWS_DOCKER.md` și `TEST_TELEFON_ANDROID_IPHONE.md` (procedurile existente rămân valabile).

## 0. Reguli de siguranță
- Folosiți **copii** ale datelor: `scripts/copy_db_for_testing.py` sau un folder `CIMITIR_DATA_DIR` nou. Nu restaurați peste baza reală.
- **Nu** rulați migrarea 028 pe Supabase TEST/PROD fără aprobare explicită; planul rămâne în `PLAN_APLICARE_028_SUPABASE_TEST.md`.
- Nu puneți chei/parole în repository; `backend/.env` este ignorat de Git.

## 1. Windows — MAIN (PySide6)
Pregătire (PowerShell, din rădăcina repo-ului): `py -3.11 -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r desktop\requirements.txt pytest`

| # | Pas | Așteptat | Rezultat |
|---|---|---|---|
| W1 | `set CIMITIR_DATA_DIR=C:\cimitir_test\data` apoi `python desktop\launcher.py` | pornește cu asistentul de primul administrator (fără admin/admin123) | |
| W2 | Bara de instrumente: **🧪 Verificare integritate** și **🔁 Persoane duplicate** | fereastra se deschide, „Verifică” rulează, progres + export JSON/CSV/MD; baza „nemodificată: da” | |
| W3 | Creați 2 concesionari identici (același CNP) | apar ca grup „Sigur” în Persoane duplicate; nimic șters/fuzionat automat | |
| W4 | Creați un loc, un contract, o plată, un aviz cu stare/executant/date/observații | se salvează; fișa PDF a locului conține toate câmpurile avizului | |
| W5 | Bara de instrumente **💾 Copii de rezervă** (nu „💾 Backup”, buton mai vechi): „Fă copie acum” → „Verifică selecția” | „Copie validă”; folder `…-backups\cimitir-AAAAMMZZ-HHMMSS` cu `manifest.json` | |
| W6 | Modificați o setare (ex. numele cimitirului) → „Restaurează selecția…” → reporniți MAIN | starea revine; lângă `data` apare `data.inainte-de-restaurare-*` | |
| W7 | Corupeți un fișier din copie (ex. deschideți `cimitir.db` în Notepad și salvați) → „Verifică selecția” | raport de probleme; restaurarea este refuzată | |
| W8 | Restaurare pe ALT calculator/alt folder: copiați folderul copiei și restaurați | documentele/fotografiile se deschid (căile media sunt rebazate) | |
| W9 | `python -m pytest desktop\tests -n 0 -q` (cu `set QT_QPA_PLATFORM=offscreen`) | se așteaptă PASS, cu excepția testelor Windows-specifice marcate SKIP; **raportați orice FAIL** | |
| W10 | Build EXE (`desktop\build_exe.bat`) și `Cimitir.exe --portal` | portalul pornește (corecția `mobile_upload`); `/admin/access` răspunde 200 | |

## 2. Windows — Cloud local (Docker) și sincronizare
Urmați `TEST_LAPTOP_WINDOWS_DOCKER.md`. În plus:

| # | Pas | Așteptat | Rezultat |
|---|---|---|---|
| S1 | Pe Postgres LOCAL (container nou, NU cel existent) aplicați migrările 001–028 | `GET /api/public/status` → `schema_version: 28` | |
| S2 | Cloud admin → locul → tab Avize | formularul arată Stare/Executant/Început/Observații; salvarea persistă | |
| S3 | Pe o bază cu migrarea 028 **neaplicată** (sau rollback) | tabul Avize funcționează fără câmpurile noi, fără eroare 500; MAIN afișează „server fără 028” | |
| S4 | MAIN creează/editează aviz → sincronizare → Cloud → PWA | aceleași valori peste tot; fără duplicate la a doua sincronizare | |
| S5 | Opriți rețeaua în timpul sincronizării, apoi reporniți | coada se reia, fără duplicate | |
| S6 | Telefonul fără drept `view_pii` trimite CNP | CNP nu se salvează; telefonul afișează avertismentul „CNP/CI NU a fost salvat” | |
| S7 | Deschideți un link QR de încărcare, verificați jurnalul serverului | în jurnal apare `/api/m/[TOKEN]/…`, nu tokenul real | |

## 3. Telefon (Android Chrome / iPhone Safari ≥ 16.4) — PWA
Cerință: HTTPS public (ex. Cloudflare Tunnel) pentru cameră. Procedura completă: `TEST_TELEFON_ANDROID_IPHONE.md`. Specific acestei livrări:

| # | Pas | Android | iPhone |
|---|---|---|---|
| P1 | Instalați PWA pe ecranul principal, asociați cu QR, folosiți 2–3 zile | rămâne asociat; datele offline nu dispar (cerere de stocare persistentă) | |
| P2 | Mod avion: editați un aviz și faceți 3 fotografii; închideți aplicația; reporniți; dezactivați modul avion | coada se păstrează și se trimite singură (fără Background Sync pe iOS: la revenirea în aplicație / reconectare / la 45 s) | |
| P3 | În MAIN schimbați permisiunile telefonului (ex. retrageți dreptul de editare) | după următoarea sincronizare butoanele de editare dispar / acțiunea e refuzată cu mesaj clar | |
| P4 | Revocați telefonul din MAIN/Cloud admin | la următoarea cerere: „Asocierea nu mai este validă”, datele locale se șterg, se cere cod nou | |
| P5 | Emiteți document PDF (aviz/adeverință) de pe telefon, dacă aveți dreptul | PDF generat, numerotat o singură dată la retry | |

## 4. Teste rulabile pe Windows (Python 3.11)
```
cd backend
set CIMITIR_TEST_DATABASE_URL=postgresql://postgres:<parola_locala>@127.0.0.1:5432/cimitir_test
set CIMITIR_TEST_PG_ADMIN_URL=postgresql://postgres:<parola_locala>@127.0.0.1:5432/postgres
set PYTHONPATH=..\shared;..\desktop;tests;.
python -m pytest -n 0 -q tests
```
Baza `cimitir_test` este ȘTEARSĂ de unele teste (schema `public`): folosiți doar o instanță locală de test. Fără aceste variabile testele PG sunt SKIP.
