# LIVRAREA 1 — Repararea asocierii MAIN ↔ Cloud (Flux B)

Bază: `main` @ `d4a045d` (merge PR #22). Ramură: `feature/cloud-sync-finalizare`. Licențierea (`.lic`, Ed25519, Hardware ID) **nu a fost atinsă**. Nu s-a adăugat nicio migrare SQL (compatibil cu 001–026). Nu s-a atins PROD, nu s-a făcut deploy, nu s-au atins containerele 8084/8085/8086.

## 1. Cauza erorii „Flux A indisponibil: decizia pentru starea curentă este B”

Lanțul real din cod:

1. `Centru Cloud → C. Backend Cloud → Asociază acest calculator` deschide `desktop/cloud_associate_window.py`.
2. „Verifică codul” apelează `Association.verify_code(cod)` (doar citire) și apoi **necondiționat** `Association.plan(cod)`.
3. `Association.plan()` chema **numai** `bootstrap.plan_flow_a()`. Acesta rulează `_preflight_a()`, care calculează `decide_flow(local, cloud)` și refuză orice decizie ≠ `A` cu `BlockedFlow("… Flux A indisponibil: decizia … este <X>")`.
4. Registrul `3213 — banovita` are **date locale** (locuri, concesionari, persoane…) și cloud-ul `banovita` este **DRAFT + gol** ⇒ matricea de decizie dă corect **B** (prima încărcare), nu A. Planul A nu avea ce căuta aici.
5. Fluxul B exista deja (`shared/cimitir_flow_b.py`, testat peste HTTP + PostgreSQL în `test_etapa4_pas6a_postgres.py` / `test_etapa4_pas51_*`), dar **nu era conectat** la `Association` / fereastra MAIN. Doar Flux A era accesibil din interfață.

Nu era o slăbiciune a validărilor de securitate: decizia B era corectă, iar interfața refuza (corect) să ruleze A pe date existente. Defectul era lipsa rutării către B.

## 2. Ce s-a schimbat

| Fișier | Schimbare |
|---|---|
| `shared/cimitir_cloud_associate.py` | `_route()`: citește starea cloud (prin cod, fără a-l consuma) + starea locală (rânduri + manifest media/coadă) și alege fluxul cu **aceeași** matrice `decide_flow`. `verify_code()` întoarce acum și `flow`/`decision`. `plan()` construiește plan **A** (local gol + cloud gol) sau **B** (local cu date + cloud gol) ori refuză cu mesaj clar (C, LINK, BLOCKED, ALREADY_BOUND, BLOCKED_ACTIVE_EMPTY, REBUILD, metadata-only). `execute()` rulează `execute_flow_b` / `execute_flow_a` după `plan["kind"]`; erorile B sunt mapate la mesaje fără secrete; `accept_missing_media` explicit. `incomplete()/resume()/abort()` știu de B (abort B autentifică curățarea staging/media cu cheia legăturii începute). `_after_failure()` nu mai lasă marcajul „asociere întreruptă” după un rollback complet. URL-ul Supabase (`*.supabase.co/.com`) este respins ca adresă de backend. |
| `desktop/cloud_associate_window.py` | Rezumatul B (conținut de încărcat, media), confirmare suplimentară dacă lipsesc fișiere media, mesaj „în curs” în timpul încărcării. |
| `shared/cimitir_bootstrap.py`, `shared/cimitir_flow_b.py` | La finalizarea A/B se salvează amprenta folderului registrului (`bound_root_fp`). `sync_allowed()` trece o **copie** a folderului în `REBUILD_REQUIRED` (nu poate reutiliza identitatea/cheia originale). Asocierile vechi, fără amprentă, rămân valabile. |
| `desktop/sync_v2.py` | `connection()` nu mai oferă cheia Binding unui registru copiat/mutat (defectul „copia TEST moștenește asocierea MAIN”, calea manuală). |
| `desktop/cloud_simple.py`, `desktop/cloud_simple_dialog.py` | Defectul „TEST ↔ PROD”: `env_value()` nu mai cade tăcut pe `TEST` pentru etichete necunoscute; profil PROD cu adresă locală (localhost/127.0.0.1) este refuzat; `CUSTOM` nu mai este transformat tăcut. |
| `backend/tests/test_cloud_associate_flux_b_pg.py` (nou) | 17 teste: Flux B cap-coadă prin HTTP real + PostgreSQL local, coduri, rollback, resume, abort, Qt. |
| `backend/tests/test_cloud_associate_isolation.py` (nou), `desktop/tests/test_cloud_simple.py` (+3) | copie izolată, URL Supabase, mediu strict. |

## 3. Matricea de decizie folosită de interfață

| Local | Cloud (cimitirul din cod) | Acțiune în MAIN |
|---|---|---|
| gol | gol, DRAFT/inexistent | **Flux A** (asociere fără date) |
| cu date | gol, DRAFT | **Flux B** (prima încărcare: staging → verificare integrală → publicare atomică) |
| cu date / gol | gol dar ACTIVE | refuz (`BLOCKED_ACTIVE_EMPTY`): posibilă ștergere în cloud |
| gol | cu date | refuz (Flux C nu e disponibil) |
| cu date | cu date | refuz (fără îmbinare automată) |
| deja BOUND | oricare | refuz „deja asociat” |
| cod expirat / revocat / folosit / altă organizație / alt cimitir | — | respins de server; nimic nu se scrie local sau în cloud |

Identitatea locală (organizație/cimitir UID, UID-urile rândurilor) se păstrează; Flux B folosește cimitirul **existent** din cloud (identificat prin UID), deci nu se creează organizații sau cimitire duplicate. Organizațiile TEST `3213` și `Parohia TEST Cimitir` nu sunt atinse sau unite: asocierea se face strict după UID-urile din registrul local și din cod.

## 4. Ce NU face această livrare

Livrările 2–4 (audit sincronizare/Mobile, provisionare per parohie, backup portabil/prima sincronizare controlată) și automatizarea `BOOTSTRAP_TOKEN` **nu sunt implementate aici**. Vezi `TESTE_SI_REGRESII.md` pentru ce a fost și ce nu a fost testat (în special: Windows/DPAPI, Supabase real, backend Docker 8086 — netestate din acest mediu).
