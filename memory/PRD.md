
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

## Etapa 4 Pasul 5 — FastAPI local transport A/B (în /app/v2, simulator SQLite, fără Supabase) — 2026-08
- Nou: shared/cimitir_cloud_wire.py (contract /api/v1, coduri de eroare), cimitir_cloud_http.py (server, rute fixe), cimitir_cloud_client.py (RemoteControlPlane), cimitir_remote.py (@remotable). Auth: `Pairing <cod>` (pre-binding) / `Binding <cheie>`; binding B `pending` → `active` doar la publish; scope derivat server-side.
- Bug de securitate găsit/corectat: begin_run nu verifica org-ul cimitirului față de cod (cross-tenant).
- Teste: test_etapa4_pas5_http.py (39) + test_etapa4_pas5_tester_extra.py (20, testing agent). Suită: 521+ passed, 3 legacy failed neschimbate. Raport: /app/test_reports/iteration_3.json (fără probleme).
- NEVERIFICAT: Windows DPAPI/PID/msvcrt. Neimplementat (la cerere): Supabase/PostgreSQL/Storage real/Render, Flux C/D/E, 014, Sync V2.

## Import v2@862d8a8 în v3 (repo final) — 2026-10-08
- Fișierele reale din github.com/paulwalker0727/v2 @ 862d8a8 (git archive, fără .git) copiate în rădăcina v3; gitlink `v2` eliminat; memory/ și test_reports/ din v3 păstrate; rapoartele vechi v2 în test_reports/v2_history/, PRD-ul v2 în memory/PRD_v2_862d8a8.md; frontend/yarn.lock (șablon) eliminat.
- Suită completă (`cd backend && pytest tests`): 540 passed / 3 failed legacy (test_etapa1_audit route `/api/m/{token}/info`, 2× test_etapa9b_cloud_config — lipsă backend/.env* din clonă) / 154 skipped / 1 error (lipsă PyMuPDF în mediu; după instalare 8/8 PASS) → efectiv 541 passed. Etapa 4: 321 passed, 2 skipped, 0 failed. Fără regresii noi. Fără push; producție neatinsă.


## Etapa 4 Pasul 5.1 — validarea planului pe server (v3, FastAPI local + simulator SQLite) — 2026-10-08
- Pre-verificări: clonă curată v3 OK (cod real, fără gitlink v2, 30× shared/cimitir_*.py, teste Etapa 4, ETAPA4_BOOTSTRAP_DESIGN.md); ruff.toml restrâns la F811 pe 5 fișiere de test Etapa 4; PyMuPDF declarat în backend/requirements.txt (pymupdf==1.28.2; era deja în requirements-render.txt și desktop/requirements.txt).
- Nou: shared/cimitir_plan_guard.py (check_begin/check_pairing/check_complete/check_transfer, claim_for, require_bid). Server: plan_hash recalculat din preimagine, expirare pe ceasul serverului (410 PLAN_EXPIRED), bootstrap_id UUID, scope, stare run, drift țintă (409 TARGET_DRIFT), compatibilitate flux (409 PLAN_BLOCKED), sursă legată de plan (409 SOURCE_DRIFT la staging/publish), B fără complete_run. HTTP: plan obligatoriu la begin_run, bootstrap_id obligatoriu la pair. Zero scrieri la refuz (amprenta bazei).
- Teste: test_etapa4_pas51_server_validation.py (26) + test_etapa4_pas51_tester_extra.py (39, testing agent; bug minor 500→400 la bootstrap_id non-text corectat). Testele Pasului 5 care foloseau plan fals ("p"*64) prin HTTP folosesc acum planuri reale. Suită: 606 passed / 3 legacy failed / 154 skipped; Etapa 4: 395 passed, 2 skipped. Raport: test_reports/iteration_4.json.
- Neimplementat (la cerere): Supabase/PostgreSQL TEST, Pasul 6A, Flux C/D/E, 014, Sync V2. Producție neatinsă.

