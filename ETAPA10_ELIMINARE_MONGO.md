# Etapa 10 — Raport pregătire eliminare MongoDB

**Stare: PREGĂTIRE APLICATĂ (Mongo marcat legacy/deprecated, dezactivat implicit). NU s-a șters nimic.** Ștergerea definitivă se face doar după criteriile din secțiunea 8 și aprobarea ta explicită.

## 1. Condiția cerută: „PostgreSQL funcțional, testat și folosit în producție"

| Condiție | Stare |
|---|---|
| Funcțional | ✅ Serverul rulează pe PostgreSQL (`/api/health` → `database: postgres`, `online`). `scripts/check_postgres.py`: OK, schemă 30/30 tabele, migrații 1–4. |
| Testat | ✅ Suita backend rulată cu PostgreSQL (rezultate la secțiunea 4). Etapele 6, 7 și 8 trec complet. |
| **Folosit în producție** | ❌ **Nedovedit.** Aici există doar PostgreSQL local de dezvoltare (port 55432). Nu am acces la serverul real și nu am verificat nicio bază PostgreSQL de producție (ex. Supabase). Aceasta trebuie confirmată de tine înainte de ștergere. |

Recomandare: nu aproba ștergerea până nu rulezi `CHECK_DATABASE.bat` / `/admin/system` pe serverul real și confirmi că rulează cu `CIMITIR_DB_ENGINE=postgres`.

## 2. Unde mai este folosit Mongo (căutare în tot repo-ul)

**Folosire efectivă la rulare: niciuna.**
- `CIMITIR_ENABLE_MONGO` nu este setat în niciun `.env`, fișier din `config/*.example` sau script. Implicit este `0` (`shared/cimitir_config.py: mongo_enabled()`).
- `mongo_store` este importat doar „la cerere" (`store._mongo()`), deci doar dacă o sursă are `engine == "mongo"`. Sursa Mongo apare doar când `CIMITIR_ENABLE_MONGO=1`.
- Nu există baze de date Mongo ale aplicației în instanța locală (nicio bază în afară de cele de sistem). Datele aplicației sunt în PostgreSQL.
- Nu există apeluri Mongo în `frontend/`, `desktop/*.py`, `scripts/` sau `shared/` (în afară de `cimitir_config.mongo_enabled`).
- Desktop-ul nu a folosit niciodată Mongo (confirmat și în `AUDIT_ETAPA1.md`).
- `mongo_store.py` este incomplet: 55 din 72 funcții, fără drepturi/sincronizare (`ETAPA2_SINCRONIZARE.md`).

## 3. Ce ar fi eliminat (inventar exact)

| # | Element | Locație | Acțiune propusă |
|---|---|---|---|
| 1 | `mongo_store.py` (502 linii) | `backend/cimitir/mongo_store.py` | Ștergere fișier |
| 2 | Ramura Mongo din router: `_mongo()`, `MONGO_SOURCE` (l. 34–47), sursa Mongo (l. 70–73), `_engine_ctx` „mongo" (l. 106–108), rutarea (l. 134–135), `ping` (l. 156–159), docstring-uri (l. 3–13) | `backend/cimitir/store.py` | Ștergere ramuri, rămân SQLite și PostgreSQL |
| 3 | `mongo_enabled()` și variabila **`CIMITIR_ENABLE_MONGO`** | `shared/cimitir_config.py` (l. 156–158) | Ștergere funcție |
| 4 | Afișarea „MongoDB" pe ecranul de acces | `backend/templates/access.html` (l. 31–32), `backend/static/app.css` (`.db-ic.mongo`) | Simplificare |
| 5 | `pymongo==4.6.3` | `backend/requirements.txt` | Scoatere. **Nu** se scoate `dnspython` (îl cere și `email-validator`). |
| 6 | `motor==3.3.1` | `backend/requirements.txt` | Scoatere (nu este importat nicăieri în cod) |
| 7 | Nume `mongo_store` din liste de curățare module | `backend/tests/test_etapa1_audit.py` (l. 35), `backend/tests/test_local_sync_public_api.py` (l. 44) | Scoatere nume (inofensiv dacă rămâne) |
| 8 | Comentariu despre pymongo | `desktop/web_requirements.txt` (l. 3) | Actualizare text |
| 9 | Mențiuni istorice | `AUDIT_ETAPA1.md`, `ETAPA1_POSTGRES.md`, `ETAPA2_SINCRONIZARE.md` | Se păstrează (istoric); opțional notă „eliminat în Etapa 10" |

## 4. Ce NU trebuie șters (sau are altă responsabilitate)

- **`MONGO_URL` și `DB_NAME` din `backend/.env`**: sunt variabile protejate ale platformei de preview (serviciul `mongodb` rulează din imaginea de bază). În codul aplicației, `MONGO_URL` este citit doar de `mongo_store.py`, iar `DB_NAME` doar de `MONGO_SOURCE`, deci după pasul 1–2 rămân neutilizate. **Sunt inofensive**; se pot scoate doar din `.env`-ul de producție (Windows/server propriu), nu din preview. Dacă se șterg în preview, platforma poate să nu mai pornească.
- **`.emergent/emergent.yml`** (`fastapi_react_mongo_shadcn_base_image`) și serviciul `mongodb` din supervisor: configurare a platformei, în afara aplicației.
- **`desktop/docs/DEPLOY_LICENSE_SERVER.md`**: serverul de licențe are propria bază MongoDB (`cimitir_licenses`) și nu este parte din acest repo. Nu are legătură cu `mongo_store.py`. Ghidul rămâne neschimbat.
- **`backend/system_admin.py`**: regex-ul de mascare include `MONGO_URL`. Se păstrează (mascare suplimentară, fără risc).

## 5. Rezultate teste (rulate înainte de orice ștergere)

Backend (`backend/tests`, serial `-n 0`, PostgreSQL local):
- Cu codul actual: **134 passed, 50 skipped, 7 failed**.
- Cele 7 eșecuri sunt toate în `tests/test_live_smoke.py`. Testele cheamă `/portal/*` pe URL-ul extern de preview, iar ingress-ul trimite în backend doar `/api/*` (limită cunoscută, vezi PRD). **Nu au legătură cu Mongo.**
- Simulare a ștergerii (copie în `/tmp`, fără `mongo_store.py` și fără `MONGO_URL`): rezultat în secțiunea 6.

Observații despre rulări (nu sunt probleme Mongo):
- Rularea implicită cu `pytest-xdist` (`-n 2`) concomitent cu alte suite pe același PostgreSQL produce eșecuri aleatorii în etapele 6/7. În serial (`-n 0`) trec toate. Pentru verificări folosește `-n 0`.
- `desktop/tests`: 193 passed, 13 failed, 6 errors, 1 modul nu se colectează. Cauze fără legătură cu Mongo: lipsesc `PyInstaller` și `pyzbar` în pod, iar testele cu subproces nu găsesc `postgres_store` când `.env` setează PostgreSQL. Aceste teste nu au fost reparate (în afara scopului Etapei 10).

## 6. Simulare „fără Mongo"

Copie a proiectului în `/tmp`, **fără** `backend/cimitir/mongo_store.py` și fără `MONGO_URL` în `.env`, suita backend serial (`-n 0`):
**133 passed, 50 skipped, 8 failed.** Diferența față de baseline (+1 eșec) este `test_backup_now_creates_a_verifiable_pg_dump`: testul caută backup-ul în folderul copiei, dar acțiunea „Backup acum" a rulat pe serverul real din `/app`, care scrie în alt folder. Este un artefact al simulării, nu o dependență de Mongo. Celelalte 7 eșecuri sunt cele din `test_live_smoke.py` (secțiunea 5).
Limită: testele end-to-end vorbesc cu serverul pornit din `/app` (care încă are `mongo_store.py`), deci simularea dovedește în mod real doar importul serverului și testele care încarcă `server.py` direct (`test_etapa1_audit`, `test_local_sync_public_api`, `test_postgres`), nu tot traficul HTTP. De aceea, după ștergere, suita trebuie rulată din nou (pasul 4, secțiunea 7).

## 7. Pași la ștergere (propunere, în ordine)

1. Confirmi că producția rulează pe PostgreSQL (secțiunea 1).
2. Faci un backup/punct de revenire (rollback) înainte.
3. Se aplică punctele 1–8 din secțiunea 3, într-un singur commit.
4. Se rulează din nou testele (`-n 0`) și `/admin/system`.
5. Pe serverul de producție, scoți manual `MONGO_URL` / `DB_NAME` din `.env` (dacă există) și oprești MongoDB, dacă nu mai este folosit de altceva (ex. serverul de licențe).

Dacă ceva nu merge, revenirea se face prin rollback la punctul de la pasul 2.

## 8. Criterii pentru ștergerea definitivă (stabilite de tine)

Ștergem Mongo abia când sunt îndeplinite TOATE:
1. PostgreSQL cloud persistent este activ (nu PostgreSQL local de dezvoltare).
2. `/admin/system` confirmă motorul `postgres`, starea `ONLINE`.
3. Testele complete trec (backend `-n 0` și desktop, din folderul `desktop/`).
4. Datele persistă după restartul serverului (creezi o înregistrare, repornești, o regăsești).

Instrument: `python scripts/audit_mongo.py` (doar citire) raportează activarea Mongo și toate fișierele cu referințe; cod de ieșire 0 = dezactivat.

## 9. Pregătirea aplicată acum (fără ștergeri)

- `backend/cimitir/mongo_store.py`: păstrat; antet **LEGACY / DEPRECATED** și `DeprecationWarning` la import (apare doar dacă Mongo este activat).
- `shared/cimitir_config.py`: `mongo_enabled()` marcat DEPRECATED; dacă `CIMITIR_ENABLE_MONGO=1`, se scrie un avertisment în jurnal. Implicit rămâne `0` (dezactivat).
- `backend/cimitir/store.py`: documentat ca LEGACY; nicio referință ștearsă.
- `scripts/audit_mongo.py`: scanner de referințe (nou). `backend/tests/test_etapa10_mongo_legacy.py`: 5 teste (dezactivat implicit, avertisment la activare, modul păstrat și marcat, `server.py` nu importă `mongo_store`/`pymongo`, scriptul raportează corect).
- Variabilele protejate ale platformei (`MONGO_URL`, `DB_NAME` din `backend/.env`) NU au fost atinse.

### Locuri din cod care încă depind de Mongo (rezultat `audit_mongo.py`, 15 fișiere)

| Categorie | Fișiere |
|---|---|
| Modul legacy | `backend/cimitir/mongo_store.py` |
| Cod aplicație | `backend/cimitir/store.py` (ramura `engine == "mongo"`, `MONGO_SOURCE`, `_mongo()`), `shared/cimitir_config.py` (`mongo_enabled`), `backend/templates/access.html` (etichetă „MongoDB"), `backend/static/app.css` (`.db-ic.mongo`), `backend/system_admin.py` (doar regex de mascare `MONGO_URL`: se păstrează) |
| Config/dependențe | `backend/.env` (`MONGO_URL`, `DB_NAME`: protejate), `backend/requirements.txt` (`pymongo`, `motor`), `desktop/web_requirements.txt` (comentariu) |
| Teste | `backend/tests/test_etapa1_audit.py`, `backend/tests/test_local_sync_public_api.py` (nume în listă de curățare module; inofensiv) |
| Documentație | `AUDIT_ETAPA1.md`, `ETAPA1_POSTGRES.md`, `ETAPA2_SINCRONIZARE.md`, `desktop/docs/DEPLOY_LICENSE_SERVER.md` (serverul de licențe, alt sistem: rămâne), `desktop/docs/GDPR_COMPLIANCE.md` |

Dependență la rulare: **niciuna** cât timp `CIMITIR_ENABLE_MONGO` nu este `1`.

## 10. Reparații de teste (cerute împreună cu Etapa 10)

- `backend/tests/test_live_smoke.py`: țintește serverul local (`CIMITIR_TEST_BASE_URL`, implicit `http://127.0.0.1:8001`), nu URL-ul extern/ingress; se sare dacă serverul nu răspunde; generează cheia de sincronizare cu motorul din `.env` (conftest forța SQLite); resetează limitările pe IP (persistate în DB) înainte de fiecare test, ca rulările repetate să nu se blocheze.
- `desktop/tests`: `conftest.py` nou (SQLite implicit, Qt offscreen, ca în backend); `CIMITIR_IGNORE_PROJECT_ENV=1` (nou în `cimitir_config`) împiedică testele cu subproces să citească `backend/.env` (PostgreSQL/online); `test_qr_scanning.py` se sare dacă lipsește `pyzbar` (nu e în requirements). Suita desktop se rulează din folderul `desktop/` (`cd desktop && pytest tests`), nu din rădăcina repo-ului.

## 11. Rezultate teste după pregătire (rulate la final)

- Backend (`cd backend && pytest tests -n 0`): **146 passed, 50 skipped, 0 failed** (inclusiv `test_live_smoke.py`: 9/9 pe serverul local).
- Desktop (`cd desktop && pytest tests`): **212 passed, 3 skipped, 0 failed**, cod de ieșire 0 (a fost reparat și un thread Qt rămas pornit în `test_updater.py`, care producea core dump la ieșire). Testul `test_qr_scanning.py` se sare fără `pyzbar`.
- Rămas nedovedit: criteriile 1, 2 și 4 din secțiunea 8 (PostgreSQL cloud persistent, `/admin/system` pe producție, persistență după restart).
