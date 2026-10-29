# TESTE_SI_REGRESII — Livrările 2, 4 și „Token automat” (peste Livrarea 1)

Bază: ramura `feature/cloud-sync-finalizare` (Livrarea 1, commit `7b4a585`) peste `main` @ `d4a045d`. Ramura acestei livrări: `feature/cloud-sync-livrari-2-4` (3 commituri).
Mediu: Linux, Python 3.11, PostgreSQL 15 **local**, PySide6 offscreen. Legendă: **LOCAL-PG+HTTP** = aplicația Cloud reală prin HTTP real peste PostgreSQL local (migrări 001–026, rol runtime fără superuser, RLS); **LOCAL-SQLite**; **LOCAL-Qt**.
**Nimic nu a rulat pe Supabase real, pe Windows/DPAPI, pe containerul 8086 sau pe un telefon fizic.**

## 1. Teste noi
| Fișier | Teste | Tip | Rezultat |
|---|---|---|---|
| `test_cloud_init_admin_pg.py` | 2 unitare (grant: valid/expirat/audiență/cheie/modificat; TTL plafonat) + 4 PG+HTTP (status, MAIN inițializează admin cap-coadă, grant fals/străin/expirat ⇒ 403, tokenul clasic rămâne separat, avertisment `legacy_token_configured`) + 1 Qt (fila de inițializare) | LOCAL-PG+HTTP, LOCAL-Qt | **PASS 7/7** (cu PG); fără PG: 2 PASS + 5 SKIP |
| `test_rls_isolation_pg.py` | 3: rol runtime non-superuser; vizibilitate doar în contextul propriu după încărcare Flux B; INSERT/UPDATE/DELETE cross-tenant refuzate | LOCAL-PG | **PASS 3/3** |
| `test_portable_backup.py` | 9: export/verificare/previzualizare/import cu identitate și `content_digest` identice; sanitizare (cheie/legătură/parole); hartă + fișiere externe; arhivă coruptă/zip-slip/versiune nouă/non-arhivă; EXISTS + confirmare + fără suprascriere; duplicate (aceeași denumire, alt UID); rollback la eșec + reluare; prima sincronizare prin Flux B; Qt (export → previzualizare → confirmare → import) | LOCAL-SQLite + Qt | **PASS 9/9** |
| `LIVRARE2_AUDIT_SINCRONIZARE.md` | matricea cerințelor §4 → teste existente rulate efectiv acum | LOCAL-PG+HTTP | vezi documentul: 75 teste executate PASS; SKIP/LIPSĂ marcate |

## 2. Regresii (fiecare test PG rulat separat pe loopback propriu; sandbox cu ~1000 porturi efemere)
| Suită | Rezultat |
|---|---|
| PG: `test_cloud_admin_pg` 14, `test_etapa789_regreq_pg` 7, `test_etapa789_provision_pg` 8, `test_cloud_associate_pg` 7, `test_cloud_associate_flux_b_pg` 17 (Livrarea 1), `test_rls_isolation_pg` 3, `test_cloud_init_admin_pg` 7, `test_cloud_field_deployment_pg` 4 | **67/67 PASS** (rulat după toate modificările din Livrările 2/4/token) |
| PG sincronizare (Livrarea 2): `test_sync_v2_main_e2e` 9, `test_sync_v2_pg` 14 (rulat în Livrarea 1), `test_mobile_v2_verification_pg` 7, `test_etapa789_docs_main_e2e` 2, `test_etapa6b_field_storage` 3, `test_etapa6_ops` 8, `test_etapa2_sync` 17, `test_etapa789_pii_docs_pg` 12 | **PASS** (fără modificări de cod în modulele de sincronizare) |
| `test_etapa6_entity_sync` (12), `test_etapa7_media` (13) | **SKIP** (cer serverul preview/`DATABASE_URL` extern și Storage Supabase real) |
| Desktop (Qt offscreen, fiecare fișier separat) vs `main` | **identic** cu `main` pe toate fișierele; singura diferență: `test_cloud_simple` 16 (13 + 3 din Livrarea 1). Eșecurile pre-existente (11 fișiere: `test_bugfix_and_qrcustom`, `test_build_packaging`, `test_client_guide`, `test_etapa1_desktop_flows`, `test_field_import_history`, `test_inhumat_empty_dates`, `test_local_first_launcher`, `test_phone_capture`, `test_startup_unification`, `test_windows_guide_flow`, `test_work_native_fields`) sunt aceleași pe `main` |
| Backend non-PG (`pytest -n 3`, 671 passed / 12 failed / 61 errors, paralel) | eșecurile și erorile (port ocupat/serverul preview, `.env` real absent) au fost reluate **serial** pe `main` și pe ramură pentru fișierele afectate: **aceleași**, cu o singură excepție găsită și **reparată**: `test_cloud_field_deployment::test_requirements_cloud_pinned_and_minimal` (cerea pini `==`; `cryptography` era nefixat) ⇒ acum `cffi==2.1.1`, `cryptography==50.0.2`, `pycparser==3.11` în `requirements-cloud.txt` |
| Licență | fișierele de licență nu sunt modificate; `desktop/tests/test_license.py` nu colectează teste în acest mediu (identic cu `main`) |

## 3. Defecte găsite în timpul lucrului (reparate)
1. Fila de inițializare nu cerea denumirea organizației (serverul o cere pentru o organizație nouă) — găsit de testul Qt; câmpuri adăugate.
2. `cryptography` nefixat în `requirements-cloud.txt` — găsit de testul de regresie existent; fixat.
3. Testele PG din `test_cloud_init_admin_pg.py` produceau ERROR (nu SKIP) fără PostgreSQL — marcate `skipif`.

## 4. Netestat / limite
Windows real, DPAPI, Supabase real, container Docker real (8086), telefon fizic, volume mari, offline lung; migrare de schemă între versiuni de arhivă; bară de progres în timp real la încărcarea Flux B; **Livrarea 3 (provisionare per parohie) NU a fost începută** (nu a fost cerută în această sesiune).

## 5. Comenzi
```
export CIMITIR_TEST_PG_ADMIN_URL=postgresql://postgres@127.0.0.1:<port>/postgres
cd backend && PYTHONPATH=../shared:../desktop:tests python -m pytest -n 0 -q tests/test_portable_backup.py tests/test_cloud_init_admin_pg.py tests/test_rls_isolation_pg.py
```
(PG: câte un test per proces dacă mediul are puține porturi efemere.) Windows: `scripts\verify_livrari_2_4.ps1` (netestat pe Windows).
