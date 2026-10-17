# REZULTATE TESTE — Sync v2 / Mobile v2

Mediu: Linux, Python 3.11, PostgreSQL 15 LOCAL (cluster dev, rol runtime `cimitir_cp_app` fără BYPASSRLS), SQLite real pentru MAIN, Chromium (Playwright) 390×844. **NU Windows, NU Docker, NU Supabase TEST** — acestea rămân de verificat de tine (`TESTARE_REALĂ_WINDOWS.md`).

Comandă: `cd backend && CIMITIR_TEST_PG_ADMIN_URL=postgresql://postgres@127.0.0.1:55432/postgres python -m pytest tests/test_sync_v2_*.py tests/test_install_patch.py -n 0` (PostgreSQL local: `scripts/dev_postgres.sh start`).

## Rezultat final (rulare completă, ultima): **35 trecute, 0 eșuate** (95,9 s)

### `test_sync_v2_main_e2e.py` — 9 teste — MAIN real (SQLite, subproces) ↔ server HTTP real ↔ PostgreSQL
- ✅ `test_first_result_main_to_mobile`
- ✅ `test_internet_interruption_then_resume_no_duplicates`
- ✅ `test_mobile_edit_reaches_main_and_conflict_main_wins`
- ✅ `test_full_scenario_polygon_concessionaire_burials_then_reverse`
- ✅ `test_verify_script_reports_real_relations`
- ✅ `test_main_uses_pilot_binding_key_from_secretstore_without_manual_setup`
- ✅ `test_payments_permits_delete_sync_and_phone_repair_lose_nothing`
- ✅ `test_sectors_main_to_cloud_phone_map_idempotent_main_only`
- ✅ `test_main_assigns_role_to_phone_via_sync_v2_module`

### `test_sync_v2_migration.py` — 3 teste — Migrare 022 + rollback pe PostgreSQL
- ✅ `test_migration_least_privilege_idempotent_and_data_preserved`
- ✅ `test_rollback_restores_exact_previous_state`
- ✅ `test_rollback_refuses_when_backup_missing`

### `test_sync_v2_pg.py` — 14 teste — PostgreSQL real (rol cimitir_cp_app, RLS): API HTTP + serviciu
- ✅ `test_main_to_mobile_grave_with_burial`
- ✅ `test_idempotent_replay_and_no_duplicates`
- ✅ `test_natural_key_never_duplicates`
- ✅ `test_mobile_write_needs_main_permission_and_cannot_delete`
- ✅ `test_mobile_to_main_roundtrip_and_grave_flag`
- ✅ `test_conflicts_main_wins_and_mobile_blocked`
- ✅ `test_tenant_isolation_and_revocation`
- ✅ `test_journal_and_delete_tombstone`
- ✅ `test_roles_capabilities_enforced_on_server`
- ✅ `test_history_of_grave_and_children`
- ✅ `test_two_devices_same_field_conflict_and_resolution`
- ✅ `test_concurrent_same_request_never_duplicates`
- ✅ `test_mobile_reads_still_work_before_migration_022`
- ✅ `test_unassigned_phone_defaults_to_limited_read_no_payments`

### `test_sync_v2_pwa.py` — 4 teste — PWA în Chromium real (Playwright) peste aplicația cloud + PostgreSQL
- ✅ `test_pwa_sync_button_edit_offline_resume`
- ✅ `test_pwa_full_forms_offline_mirror_roles_and_privacy`
- ✅ `test_pwa_repairing_to_other_cemetery_purges_mirror_and_quarantines_queue`
- ✅ `test_pwa_map_colors_pinch_zoom_and_tap_opens_grave`

### `test_install_patch.py` — 5 teste — Instalator patch pe repo-uri git temporare
- ✅ `test_wrong_branch_changes_nothing`
- ✅ `test_local_modification_on_patched_file_stops_without_overwrite`
- ✅ `test_clean_apply_with_unrelated_local_changes_backup_and_idempotent_and_rollback`
- ✅ `test_rollback_refuses_if_file_changed_after_install`
- ✅ `test_patch_with_path_traversal_is_refused`

## Mapare pe testele obligatorii din cerință
| # | Cerință | Test(e) |
|---|---|---|
| 1 | MAIN creează loc + concesionar + 2 înhumați (+contract) → Mobile le afișează | `test_full_scenario_polygon_concessionaire_burials_then_reverse`, `test_first_result_main_to_mobile`, PWA `test_pwa_full_forms_offline_mirror_roles_and_privacy` |
| 2 | Mobile modifică → MAIN primește | scenariul „reverse” din același test + `test_pwa_sync_button_edit_offline_resume` |
| 3 | Două dispozitive, aceeași informație → conflict | `test_two_devices_same_field_conflict_and_resolution`, `test_conflicts_main_wins_and_mobile_blocked`, `test_mobile_edit_reaches_main_and_conflict_main_wins` |
| 4 | Fără internet → coadă | `test_pwa_sync_button_edit_offline_resume`, `test_pwa_full_forms_offline_mirror_roles_and_privacy`, `test_internet_interruption_then_resume_no_duplicates` |
| 5 | Același request de două ori → fără duplicate | `test_concurrent_same_request_never_duplicates` (8 thread-uri), `test_idempotent_replay_and_no_duplicates`, `test_natural_key_never_duplicates` |
| 6 | Alt cimitir inaccesibil | `test_tenant_isolation_and_revocation`, `test_history_of_grave_and_children` (izolare), `test_roles_capabilities_enforced_on_server` |
| 7 | Migrarea păstrează datele | `test_migration_least_privilege_idempotent_and_data_preserved`, `test_rollback_restores_exact_previous_state` |
| 8 | Documente/fișiere existente neafectate | 🟡 documentele **nu se sincronizează**; migrarea nu atinge tabelele media (nu are test dedicat) |
| 9 | Re-pairing fără pierderi | `test_payments_permits_delete_sync_and_phone_repair_lose_nothing`, `test_pwa_repairing_to_other_cemetery_purges_mirror_and_quarantines_queue` |
| 10 | „Sincronizează” arată starea reală | PWA: stări busy/pending/offline/ok verificate în teste; MAIN: raport `applied/pending/errors` verificat în e2e |

## Regresii verificate separat
- `tests/test_etapa6_entity_sync.py`, `tests/test_etapa2_sync.py`, `tests/test_etapa6c_field_inbox_pg.py`: 71 trecute (în rularea combinată), 1 eșec pre-existent (`test_production_is_hard_blocked` — așteaptă lista veche de migrări; eșuează și pe baza `9457db4`).
- `validation/test_mobile_v2_polygon_{import,export}.py`, `validation/test_mobile_v2_data_pipeline.py`: 11/11 trecute (citirile Mobile funcționează și înainte de migrarea 022).
- Întregul repo (rulare paralelă): 972 trecute / 41 eșuate / 30 erori; la rulare serială a fișierelor eșuate: **18 eșuate + 1 eroare, identice cu baza `9457db4`** (lipsesc `reportlab`, `fitz`, `.env`, roluri PG de test) → nu sunt regresii ale acestei sesiuni.
- Audit de securitate static (read-only): fără critice/înalte; SEC-001 (medie) și 3 recomandări P3 remediate și acoperite de teste (`test_unassigned_phone_defaults_to_limited_read_no_payments`, `test_patch_with_path_traversal_is_refused`).

## Neverificat (marcat clar)
Windows/PowerShell (`INSTALL_PATCH.ps1`, `laptop_cloud_test.ps1`), SecretStore DPAPI real, dialogul Qt „Telefoane Mobile”, Docker, Supabase TEST (migrarea nu a rulat acolo), iPhone/Android reale.
