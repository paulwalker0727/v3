# TESTE_SI_REGRESII — Livrarea 1 (asociere MAIN ↔ Cloud, Flux B)

Bază: `d4a045d`. Mediu de rulare: Linux, Python 3.11, PostgreSQL 15 **local** (cluster temporar, fără Supabase), PySide6 6.8 (offscreen).
Legendă: **LOCAL-SQLite** = simulator de control plane + SQLite; **LOCAL-PG+HTTP** = aplicația Cloud REALĂ (`cimitir_cloud_app.create_app`) servită prin HTTP real (uvicorn) peste PostgreSQL local cu migrările 001–026, RLS activ, rol aplicație fără drepturi de superuser; **LOCAL-Qt** = fereastra MAIN reală, platformă `offscreen`.
**Nimic din acest raport nu a rulat pe Supabase real, pe Windows (DPAPI/instalator) sau pe backendul Docker `8086`.** Acestea sunt marcate SKIP/NETESTAT mai jos.

## 1. Teste noi (Livrarea 1)

| # | Test | Tip | Rezultat |
|---|---|---|---|
| 1 | `test_reported_error_is_reproduced_by_flux_a_only_and_fixed` — registru cu date + cloud DRAFT gol: `verify_code`→flow B; Flux A rămâne refuzat („Flux A indisponibil … B”); `plan()` dă plan B | LOCAL-PG+HTTP | PASS |
| 2 | `test_flux_b_end_to_end_request_approval_code_verify_associate_restart` — cerere MAIN → aprobare administrator Cloud → cod → verificare → plan → asociere → SecretStore → obiecte noi („repornire”) → `verify()`; UID-uri locale și rânduri neschimbate; 0 organizații/cimitire noi; cimitir ACTIVE; 1 dispozitiv MAIN activ; a doua asociere refuzată | LOCAL-PG+HTTP | PASS |
| 3 | `test_flux_b_direct_code_flow_and_cloud_has_data` — fără confirmare nu se scrie nimic; raport: `post_publish_verified`, `cloud_rows` = suma entităților, 3 locuri | LOCAL-PG+HTTP | PASS |
| 4 | `test_used_code_is_rejected` | LOCAL-PG+HTTP | PASS |
| 5 | `test_revoked_code_is_rejected` | LOCAL-PG+HTTP | PASS |
| 6 | `test_expired_code_is_rejected` (expirare forțată în PostgreSQL) | LOCAL-PG+HTTP | PASS |
| 7 | `test_code_of_another_organization_is_rejected_and_nothing_changes` | LOCAL-PG+HTTP | PASS |
| 8 | `test_code_for_another_cemetery_of_same_org_cannot_pair_this_registry` | LOCAL-PG+HTTP | PASS |
| 9 | `test_phone_code_cannot_pair_main` | LOCAL-PG+HTTP | PASS |
| 10 | `test_flux_b_crash_before_publish_resume_without_new_code` (cădere la încărcarea media; reluare fără cod nou) | LOCAL-PG+HTTP | PASS |
| 11 | `test_flux_b_abort_restores_clean_state_and_retry_works_with_new_code` | LOCAL-PG+HTTP | PASS |
| 12 | `test_flux_b_failure_before_publish_rolls_back_fully` | LOCAL-PG+HTTP | PASS |
| 13 | `test_missing_media_requires_explicit_acceptance` (stare `BOUND_MEDIA_PENDING`) | LOCAL-PG+HTTP | PASS |
| 14 | `test_flux_a_still_chosen_for_empty_registry_and_not_confused_with_b` (Flux A separat) | LOCAL-PG+HTTP | PASS |
| 15 | `test_cloud_with_data_and_local_with_data_is_blocked_without_changes` | LOCAL-PG+HTTP | PASS |
| 16 | `test_http_flow_does_not_leak_secrets_in_messages_or_logs` (cod/cheie absente din SQLite, rezultate, `caplog`; plan fără date personale) | LOCAL-PG+HTTP | PASS |
| 17 | `test_qt_dialog_verify_code_then_associate_flux_b` — exact scenariul raportat, prin fereastra MAIN | LOCAL-Qt + PG+HTTP | PASS |
| 18–22 | `test_cloud_associate_isolation.py` (5): URL Supabase respins; copie de folder nu refolosește asocierea; asociere veche fără amprentă rămâne validă; dezasociere șterge amprenta; `sync_v2.connection` blocat pentru copie | LOCAL-SQLite | PASS (5/5) |
| 23–25 | `desktop/tests/test_cloud_simple.py` (+3): mediu strict, PROD+localhost refuzat, TEST nu devine PROD, CUSTOM păstrat | LOCAL (Qt/SQLite) | PASS (16/16 în fișier) |

## 2. Regresii (comparate cu `main` @ `d4a045d`, aceleași comenzi)

| Suită | Comandă (rezumat) | Rezultat |
|---|---|---|
| PostgreSQL + HTTP, **fiecare test rulat separat** pe un loopback propriu (sandbox-ul are doar ~1000 porturi efemere) | `test_cloud_associate_pg` 7, `test_cloud_admin_pg` 14, `test_etapa789_regreq_pg` 7, `test_etapa789_provision_pg` 8, `test_etapa4_pas51_server_validation` 26, `test_etapa4_pas5_http` 39, `test_etapa4_pas5_tester_extra` 20, `test_etapa4_pas51_tester_extra` 39, `test_phone_pairing_pg` 37 (1 test parametrizat verificat separat: 9/9), `test_etapa6c_field_inbox_pg` 26, `test_sync_v2_pg` 14 | **PASS 251/252** + 1 FAIL pre-existent (`test_etapa6c_field_inbox_pg::test_production_is_hard_blocked` — aserție veche pe lista migrărilor; **eșuează identic pe `main`**) |
| `test_etapa4_pas6a_postgres.py` (26) | per test, 127.0.0.1 | 24 PASS; 2 FAIL într-o primă trecere (`test_server_killed_after_commit_restart_and_resume_pg[staging_put_batch-3]`, `[publish_flow_b-1]`), apoi **PASS la 8/8 reluări** pe codul din ramură și PASS pe `main`. Considerat instabil (procese server + porturi), nu regresie — dar **nu garantat**. |
| Backend, restul suitei (SQLite local) | `pytest -n 4 tests --ignore=<fișierul nou PG>` | 688 passed, 570 skipped (PG/live/Windows), 13 failed + 27 errors în rularea paralelă; la rularea **serială** a acelorași fișiere ramura și `main` au **exact aceleași** 5 FAIL + 1 ERROR (mediu: `.env` real absent, `reportlab` lipsă, port ocupat, rute preview) |
| Desktop (Qt offscreen), fiecare fișier separat | `desktop/tests/test_*.py` | rezultate **identice** cu `main` pe toate fișierele (inclusiv eșecurile pre-existente `test_client_guide`, `test_inhumat_empty_dates`, `test_qr_custom_and_map`, `test_startup_unification`); +3 teste noi PASS. `test_pdf_reports`/`test_qr_labels` nu se pot colecta aici (`reportlab` lipsă) — SKIP |
| Licență (`desktop/tests/test_license.py`) | — | „no tests ran” identic cu `main` (fișierul nu conține teste colectabile în acest mediu). Fișierele de licență **nu au fost modificate** (`git diff` nu le atinge). |
| Backup/TLS/Supabase (unit) | `test_cloud_supabase.py`, `test_backup_window.py`, `test_cloud_center.py` | identice cu `main` (PASS) |

## 3. SKIP / NETESTAT (nu este PASS)
- Windows: DPAPI real, executabil MAIN instalat, repornirea procesului MAIN real, căi Windows pentru amprenta folderului.
- Supabase real (proiectul Supabase TEST) și backendul Docker `cimitir-cloud-test-8086` — niciun apel către ele.
- Mobile/PWA, poligoane, media pe infrastructură reală, offline/reluare (Livrarea 2).
- Provisionare per parohie (Livrarea 3), backup portabil/prima sincronizare controlată (Livrarea 4).
- Automatizarea `BOOTSTRAP_TOKEN` (nu e parte din Livrarea 1).

## 4. Comenzi folosite (reproductibil, Linux)
```
export CIMITIR_TEST_PG_ADMIN_URL=postgresql://postgres@127.0.0.1:<port>/postgres
cd backend && PYTHONPATH=../shared:../desktop:tests python -m pytest -n 0 -q tests/test_cloud_associate_flux_b_pg.py::<test>
cd backend && PYTHONPATH=../shared:../desktop python -m pytest -n 0 -q tests/test_cloud_associate_isolation.py
cd desktop && PYTHONPATH=../shared:. QT_QPA_PLATFORM=offscreen python -m pytest -n 0 -q tests/test_cloud_simple.py
```
Notă tehnică: sandbox-ul de test are doar ~1000 de porturi efemere; rularea mai multor teste PostgreSQL+HTTP în același proces epuizează portul (`Cannot assign requested address`), deci fiecare test a fost rulat separat pe alt loopback `127.0.x.y` (PostgreSQL ascultă pe `*`). Pe un laptop obișnuit fișierele rulează întregi.
