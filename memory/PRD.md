
## Etapa 4 Pasul 1 — închidere (repo clonat în /app/v2 din github.com/paulwalker0727/v2)
- Lock între procese (flock/msvcrt), kill -9 resume/rollback real, offline (seccomp), SecretStore de test criptat (Fernet, base64 eliminat), ACTIVE+gol fail-closed, scripts/windows_verify_step1.py (de rulat pe Windows). Nu e commit/push făcut. Windows real: NEVERIFICAT.

## Etapa 4 Pasul 2 — Preflight + Manifest (în /app/v2)
- shared/cimitir_manifest.py, shared/cimitir_preflight.py, backend/tests/test_etapa4_pas2_manifest.py (68 teste), ETAPA4 design §13A. Suită: 363 passed, 3 legacy failed (aceleași ca la baseline). Pasul 1 neschimbat funcțional. Fără B/C/D/E, 014, HTTP, Sync V2; producție neatinsă.

## Etapa 4 Pasul 3 — Transfer contract + staging (în /app/v2)
- shared/cimitir_transfer_contract.py, cimitir_cloud_staging.py, cimitir_transfer.py + backend/tests/test_etapa4_pas3_transfer.py (23 teste), design §13B. Suită: 386 passed, 3 legacy failed (neschimbate). Nu e Flux B; live neatins; producție neatinsă.
