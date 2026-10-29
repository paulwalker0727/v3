# Livrarea 2 — Audit și test al sincronizării MAIN ↔ Cloud ↔ Mobile

Rezultatele de mai jos sunt **teste efectiv executate în acest mediu**: aplicația Cloud reală (`cimitir_cloud_app`) servită prin HTTP real peste **PostgreSQL 15 LOCAL** (migrările 001–026, rol de runtime `NOSUPERUSER/NOBYPASSRLS`, RLS activ), cu MAIN simulat prin modulele reale (`cimitir_sync_v2`, Flux B) și Qt offscreen. **Nu este infrastructură reală**: niciun test nu a rulat pe Supabase, pe Windows, pe un telefon fizic sau pe containerul 8086. Datele sunt fictive, în organizații/registre izolate create de teste; nicio instalare existentă nu a fost sincronizată.

Marcaje: **LOCAL-PG+HTTP** (descris mai sus) · **LOCAL-SQLite** (simulator) · **SKIP** (nu a rulat) · **LIPSĂ** (nu există test).

## Matricea cerințelor (§4) → teste executate
| Cerință | Test (fișier::test) | Tip | Rezultat |
|---|---|---|---|
| Loc de veci fictiv creat în MAIN → Cloud | `test_sync_v2_main_e2e::test_first_result_main_to_mobile` | LOCAL-PG+HTTP | PASS |
| Geometrie / poligon desenat | `test_sync_v2_main_e2e::test_full_scenario_polygon_concessionaire_burials_then_reverse`, `::test_sectors_main_to_cloud_phone_map_idempotent_main_only` | LOCAL-PG+HTTP | PASS |
| Imaginea hărții asociată registrului corect | `::test_sectors_main_to_cloud_phone_map_idempotent_main_only` (+ `test_mobile_v2_verification_pg::test_phone_sees_map_graves_…`) ; la import portabil: `test_portable_backup::test_map_image_and_external_files_travel_with_the_right_registry` | LOCAL | PASS |
| Concesionari, persoane înhumate, contracte, plăți | `test_sync_v2_pg::test_main_to_mobile_grave_with_burial`, `test_mobile_v2_verification_pg::test_phone_sees_map_graves_concessionaires_burials_contracts_and_payments`, `test_sync_v2_main_e2e::test_payments_permits_delete_sync_and_phone_repair_lose_nothing` | LOCAL-PG+HTTP | PASS |
| Documente și fotografii (MAIN→Cloud→telefon; fotografie telefon→MAIN) cu integritate | `test_etapa789_docs_main_e2e::test_main_documents_reach_cloud_and_phone_then_phone_photo_returns_to_main`, `test_mobile_v2_verification_pg::test_photo_and_document_phone_to_cloud_to_main_with_integrity_and_isolation`, `test_etapa6b_field_storage` (3) | LOCAL-PG+HTTP (Storage Supabase **simulat**) | PASS |
| Modificări din Mobile recepționate în MAIN | `test_sync_v2_pg::test_mobile_to_main_roundtrip_and_grave_flag`, `test_sync_v2_main_e2e::test_mobile_edit_reaches_main_and_conflict_main_wins` | LOCAL-PG+HTTP | PASS |
| Offline → reluare conexiune → sincronizare ulterioară | `test_sync_v2_main_e2e::test_internet_interruption_then_resume_no_duplicates`, `test_etapa789_docs_main_e2e::test_offline_main_keeps_documents_pending_then_resumes` | LOCAL-PG+HTTP | PASS |
| Retry și idempotency fără duplicate | `test_sync_v2_pg::test_idempotent_replay_and_no_duplicates`, `::test_natural_key_never_duplicates`, `::test_concurrent_same_request_never_duplicates`, `test_mobile_v2_verification_pg::test_repeated_sync_and_replayed_ops_never_duplicate` | LOCAL-PG+HTTP | PASS |
| Versioning, conflicte (MAIN câștigă, telefon blocat), două dispozitive | `test_sync_v2_pg::test_conflicts_main_wins_and_mobile_blocked`, `::test_two_devices_same_field_conflict_and_resolution`, `test_mobile_v2_verification_pg::test_simultaneous_edits_never_overwrite_silently` | LOCAL-PG+HTTP | PASS |
| Tombstone și jurnal de audit, istoric | `test_sync_v2_pg::test_journal_and_delete_tombstone`, `::test_history_of_grave_and_children` | LOCAL-PG+HTTP | PASS |
| Separarea organizațiilor (aplicație) | `test_sync_v2_pg::test_tenant_isolation_and_revocation`, `test_mobile_v2_verification_pg::test_organization_isolation_end_to_end`, `test_revoked_phone_loses_access_immediately` | LOCAL-PG+HTTP | PASS |
| **RLS la nivel de bază de date** (nou) | `test_rls_isolation_pg::test_runtime_role_is_not_superuser_and_cannot_bypass_rls`, `::test_tenant_data_visible_only_in_its_own_context_after_flux_b_upload`, `::test_cross_tenant_writes_and_updates_are_denied_by_policy` | LOCAL-PG | PASS (3/3) |
| Roluri/capabilități impuse pe server, telefon nealocat = citire limitată | `test_sync_v2_pg::test_roles_capabilities_enforced_on_server`, `::test_unassigned_phone_defaults_to_limited_read_no_payments` | LOCAL-PG+HTTP | PASS |
| Încărcare inițială a unui registru cu date (poligon/media) în Cloud | `test_cloud_associate_flux_b_pg` (Livrarea 1, 17) | LOCAL-PG+HTTP+Qt | PASS |
| Sincronizare MAIN v1 (entități legacy, `etapa6_entity_sync`) | `test_etapa6_entity_sync.py` (12) | — | **SKIP** (cere serverul preview + `DATABASE_URL` extern; nu a rulat) |
| Media pe Storage Supabase real | `test_etapa7_media.py` (13) | — | **SKIP** (cere `CIMITIR_LIVE_TESTS`, `SUPABASE_*` real) |
| Mobile PWA pe telefon fizic / browser real, poligon desenat pe ecran tactil | — | — | **LIPSĂ** (netestat; doar logica serverului/protocolului) |
| Offline lung (zile), baterie/aplicație închisă pe telefon | — | — | **LIPSĂ** |
| Test de sarcină / volume mari | — | — | **LIPSĂ** |

Total executat în această livrare (per test, loopback separat): `test_sync_v2_main_e2e` 9/9, `test_sync_v2_pg` 14/14, `test_mobile_v2_verification_pg` 7/7, `test_etapa789_docs_main_e2e` 2/2, `test_etapa6b_field_storage` 3/3, `test_etapa6_ops` 8/8, `test_etapa2_sync` 17/17, `test_etapa789_pii_docs_pg` 12/12, `test_rls_isolation_pg` 3/3 (nou). Defecte de sincronizare **găsite**: niciunul în aceste teste. Nu s-a modificat codul de sincronizare (`cimitir_sync_v2`, `desktop/sync_v2.py` doar prin protecția de copie din Livrarea 1).

## Lacune / riscuri recomandate pentru următorul pas
1. Rulare pe backend TEST real (Supabase TEST + container 8086) a scenariului de mai sus, cu un registru fictiv, urmată de verificări în `/admin/`.
2. Test pe telefon fizic (iPhone/Android) pentru poligon + fotografie + mod avion.
3. Încărcarea inițială Flux B nu are încă bară de progres în timp real în MAIN (doar mesaj + rezumat final).
