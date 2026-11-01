# REZULTATE TESTE — reparare sincronizare CNP (Etapa 5)

Mediu: PostgreSQL 15.19 local (rol `cimitir_cp_app` fără BYPASSRLS, RLS activ), SQLite pentru MAIN (proces separat, `desktop/sync_v2.py`), HTTP real (uvicorn). **Fără Supabase.** Valorile au fost citite direct din PostgreSQL (rol admin) și din SQLite MAIN, nu doar din răspunsurile API.

## Teste noi
| Test | Rezultat |
|---|---|
| `test_cnp_sync_pg.py` — telefon cu `view_pii` creează concesionar cu CNP `0123456789012` + CI: valoare exactă în PG și la pull MAIN | **PASS** |
| idem — modificare CNP + contract cu `owner_cnp` de pe telefon | **PASS** |
| idem — telefon fără `view_pii`: CNP nu se persistă, dar răspunsul raportează `ignored_fields` (înainte: `applied` fără nicio indicație → **FAIL demonstrat**) | **PASS** (după corecție) |
| idem — editare de pe telefon fără `view_pii` nu șterge CNP-ul din MAIN (nici cu câmp gol) | **PASS** |
| idem — CNP/zerouri inițiale (`0000000000000`) MAIN→cloud→telefon exact | **PASS** |
| idem — conflict: nici răspunsul, nici `/conflicts` către telefon nu conțin CNP | **PASS** |
| idem — jurnal `sync_v2_ops.response` și `sync_v2_audit` fără CNP/CI în clar (înainte: CNP în clar → **FAIL demonstrat**) | **PASS** (după corecție) |
| `test_cnp_main_e2e.py` — MAIN(SQLite)→cloud→Mobile: CNP exact cu zerou inițial, `typeof(cnp)='text'`, CI | **PASS** |
| idem — Mobile→cloud→MAIN: concesionar nou + contract asociat la loc; SQLite MAIN conține CNP exact, `owner_cnp`, FK `concesionar_id`/`id_loc` corecte, outbox gol; apoi modificare CNP | **PASS** |
| idem — MAIN modifică CNP: ajunge în cloud și la telefon cu `view_pii`, restul câmpurilor intacte | **PASS** |
| idem — telefon fără `view_pii`: CNP ignorat + raportat; valoarea din MAIN rămâne; alt câmp (telefon) se sincronizează | **PASS** |
| idem — conflict (versiune depășită): scrierea Mobile nu se aplică, MAIN câștigă | **PASS** |
| idem — ștergere din MAIN → tombstone la telefon; revocare telefon → operație offline trimisă târziu nu modifică nimic | **PASS** |

## Regresii existente (aceleași suite, după corecție)
| Suită | Rezultat |
|---|---|
| `test_sync_v2_pg.py` (14) | **PASS** 14/14 |
| `test_mobile_permissions_pg.py` (12) | **PASS** 12/12 (după `pip install reportlab python-docx …`; fără ele 1 test de PDF dădea 500 — dependență lipsă în mediul de test, nu defect de cod) |
| `test_etapa789_pii_docs_pg.py` (13) | **PASS** 13/13 |
| `test_sync_v2_main_e2e.py` (9) | **PASS** 9/9 |
| `test_mobile_complet_main_e2e.py` (1) | **PASS** |

Notă de mediu: containerul de test are `ip_local_port_range` de 1024 porturi; suitele lungi dau uneori `Cannot assign requested address` (epuizare TIME_WAIT). Testele afectate au fost rerulate individual / în loturi mici și au trecut. Aceeași eroare apare și pe codul nemodificat (baseline).

## SKIP / necesită MAIN real sau telefon real
* **SKIP** — PWA în browser real (Chromium/Playwright indisponibil aici): ascunderea câmpurilor CNP/CI fără drept, avertismentul `ignored_fields`, `reconcilePiiCache` (IndexedDB) și Service Worker `shell-v8` sunt verificate doar sintactic (`node --check`) și prin cod; necesită verificare pe telefon real.
* **SKIP** — MAIN PySide6 pe Windows (UI desktop). S-a testat motorul `desktop/sync_v2.py` / `entity_sync.py` + SQLite într-un proces separat, nu fereastra.
* **SKIP** — Supabase TEST/PROD (interzis).
* **NU s-au testat** (în afara scopului acestei livrări): Etapele 1–4 și 6 (fișa loc unificată, generare contract din fișă, documente multi-entitate etc.).

## Verificare independentă (testing agent)
Toate suitele de mai sus au fost rerulate independent: PASS. Teste suplimentare în `backend/tests/test_cnp_independent_verify_pg.py` (5/5 PASS: CNP numeric, CNP doar spații, replay conflict redactat). Limitări raportate: outbox-ul local păstrează CNP până la trimitere după revocare; PWA netestată în browser.
