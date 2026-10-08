
## Etapa 4 Pasul 1 — închidere (repo clonat în /app/v2 din github.com/paulwalker0727/v2)
- Lock între procese (flock/msvcrt), kill -9 resume/rollback real, offline (seccomp), SecretStore de test criptat (Fernet, base64 eliminat), ACTIVE+gol fail-closed, scripts/windows_verify_step1.py (de rulat pe Windows). Nu e commit/push făcut. Windows real: NEVERIFICAT.

## Etapa 4 Pasul 2 — Preflight + Manifest (în /app/v2)
- shared/cimitir_manifest.py, shared/cimitir_preflight.py, backend/tests/test_etapa4_pas2_manifest.py (68 teste), ETAPA4 design §13A. Suită: 363 passed, 3 legacy failed (aceleași ca la baseline). Pasul 1 neschimbat funcțional. Fără B/C/D/E, 014, HTTP, Sync V2; producție neatinsă.

## Etapa 4 Pasul 3 — Transfer contract + staging (în /app/v2)
- shared/cimitir_transfer_contract.py, cimitir_cloud_staging.py, cimitir_transfer.py + backend/tests/test_etapa4_pas3_transfer.py (23 teste), design §13B. Suită: 386 passed, 3 legacy failed (neschimbate). Nu e Flux B; live neatins; producție neatinsă.

## Etapa 4 Pasul 3.5 — Hardening (în /app/v2)
- shared/cimitir_identity.py (cloud_entity_identity + promovare atomică), batching canonic, kill real, staging STALE, permisiune `bootstrap` ≠ `rebuild`; 19 teste + 9 teste suplimentare de la testing agent. Suită: 405 passed / 3 legacy failed. Testing agent: fără probleme (iteration_1.json).

## Etapa 4 Pasul 4 — Flux B MAIN existent → cloud nou (în /app/v2, doar simulator local) — 2026-08
- Module noi: shared/cimitir_flow_b.py (plan/execute reluabil/abort), cimitir_snapshot.py (cut atomic + arhivare outbox), cimitir_cloud_media.py (bytes media pe bucăți, verificare strictă), cimitir_cloud_publish.py (publish atomic, epoca 1). Extinse: cimitir_identity.py (cloud_media_identity pending→active), cimitir_transfer.py (sursă snapshot, media după local_ref, upload_media), cimitir_manifest.py (_local_ref), cimitir_cloud_staging.py (snapshot_from), cimitir_bootstrap.py (incomplete_bootstrap include `verifying`).
- Teste: test_etapa4_pas4_flux_b.py (30), test_etapa4_pas4_crash.py (25, SIGKILL real), test_etapa4_pas4_tester_extra.py (13, testing agent). Suită: 469+ passed, 3 legacy failed neschimbate. Raport testing agent: /app/test_reports/iteration_2.json (fără probleme).
- NEVERIFICAT: Windows DPAPI / PID / msvcrt lock (rulate manual de utilizator). Mocked: cloud = LocalControlPlane SQLite; SecretStore de test.
- Rămâne (neimplementat, la cererea utilizatorului): Flux C/D/E, PostgreSQL/Supabase TEST, migrarea 014, endpoint-uri HTTP, Sync V2.
