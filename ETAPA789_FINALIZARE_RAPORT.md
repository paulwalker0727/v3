# Etapa 7-9 — Finalizare: asociere MAIN, provizionare Cloud, administrator local (raport)

Ramură: `etapa789-asociere-finala` (din `main` @ `6344c52`; `main` neatins). Mediu de test: Linux (Emergent). Nu am avut acces la Windows-ul/Docker-ul dumneavoastră.

## Cauza problemei critice
`preflight_for_code` verifica un cod emis pentru alt cimitir (cel fictiv existent în Cloud). Organizația `65702ca7-…` și cimitirul `d7e338df-…` create în MAIN nu existau în PostgreSQL, iar singurul mod de a le crea era SQL manual / seed. Nu exista nicio operație „provizionează registrul cu UID-urile originale”.

## Ce s-a implementat
| Zonă | Modificare |
|---|---|
| Cloud (migrarea **025**, doar funcții, fără modificări de tabele/date) | `cp_admin_registry_lookup` (read-only), `cp_admin_provision` (SECURITY DEFINER; autorizare în DB: administrator activ; organizație existentă ⇒ Owner; organizație nouă ⇒ administratorul inițial al instalării sau Platform Admin; cimitir cu UID din altă organizație ⇒ refuz; idempotent). RLS neatins. Rollback versionat. |
| API Cloud | `GET /api/v1/admin/registry`, `POST /api/v1/admin/provision` (sesiune de administrator, CSRF ca restul). Fără migrarea 025 răspunde 501 `SCHEMA_OUTDATED`; serverul avertizează la pornire. |
| MAIN logică | `Association.registry_in_cloud()` + `associate_with_admin()`: verifică UID-urile locale în Cloud → (după confirmare) provizionează cu UID-urile ORIGINALE → emite codul MAIN → `verify_code` → plan Flux A (plan_hash, verify_empty, BLOCKED_ACTIVE_EMPTY neschimbate) → cheia în SecretStore → `verify()` → abia apoi sync activ. |
| MAIN UI | „🔗 Asociază cu Cloud”: autentificare administrator Cloud, rezumat (există / se creează / refuzat), confirmare explicită, buton unic „Provizionează și asociază”. Textul explică separat contul LOCAL și contul CLOUD. Varianta manuală cu cod rămâne. Fereastra se deschide doar cu `manage_config`. |
| Administrator local | `desktop/first_run_wizard.py`: la prima pornire (registru fără administrator) creează primul administrator (nume ≠ admin/root/user, parolă ≥12, ≠ nume, confirmare). **Conturile implicite `admin/admin123`, `user/user123` nu se mai creează** nici local/SQLite (doar cu `CIMITIR_ALLOW_DEFAULT_USERS=1`, pentru dezvoltare). Conturile implicite existente rămân semnalate și schimbarea parolei se cere ca înainte. |
| Scripturi | `scripts/apply_cloud_provision_migration.py` (raport read-only + aplicare doar 025 pe Cloud existent, fără reaplicarea 001–024, refuză PROD), `scripts/verify_provision_live.py` (circuit LIVE complet pe un registru FICTIV). |
| Test existent actualizat | `test_first_run_bootstrap::test_sqlite_local_desktop_mode_*` asertă acum noul comportament (fără conturi implicite). |

## Rezultate teste

### LOCAL (Linux, PostgreSQL 15 local cu rol `cimitir_cp_app` fără BYPASSRLS, aplicația Cloud reală pe HTTP real, SQLite local, Storage SIMULAT, Qt offscreen, Chromium headless)
- Noi: `backend/tests/test_etapa789_provision_pg.py` — **8 PASS** (registru lipsă ⇒ provizionat cu UID originale + asociat + BOUND + cheie doar în SecretStore; idempotență; non-provisioner nu poate crea organizații; non-Owner nu poate adăuga cimitir; UID de cimitir din altă organizație ⇒ conflict, nu se suprascrie; sesiune/UID-uri invalide; UI Qt end-to-end cu parolă greșită → corectă → provizionare → BOUND).
- Noi: `desktop/tests/test_first_run_wizard.py` — **4 PASS** (fără conturi implicite; reguli primul admin; flag dev; dialog).
- Regresie: `test_cloud_admin_pg` + `test_cloud_associate_pg` (21 PASS), `test_first_run_bootstrap` (8 PASS cu `CIMITIR_FIRSTRUN_ADMIN_URL` superuser și `CIMITIR_ALLOW_TENANT_MIGRATIONS=1`), suita Etapa 7-9/sync/pairing/Mobile v2/field deployment: **131 PASS, 1 SKIP** (documente MAIN→Cloud→Mobile, fotografii Mobile→MAIN, PII, izolare, revocare, offline/duplicate, QR/URL public), PWA în Chromium (`test_etapa789_pwa_photos`, `test_sync_v2_pwa`): **6 PASS**; desktop (fără lansatoare Windows): 439 PASS.
- Rulare „server real” locală: `verify_provision_live.py` pe aplicația Cloud pornită ca proces separat (uvicorn + PostgreSQL local): **7 PASS / 0 FAIL / 1 SKIP** (SKIP = izolare cu organizație străină, variabilă nesetată).

### LIVE (Supabase TEST `jqtdeuzkqtzfsdohvxmy`)
- **PASS (parțial)**: Storage — bucketul `cimitir-private-test` există și este privat; upload/citire cu cheie de serviciu OK; acces public și anonim respinse (HTTP 400); obiectul de test a fost șters. Doar cu cheia service_role primită; nimic scris în repo.
- **NETESTAT**: tot ce cere PostgreSQL LIVE (verificarea schemei/migrării 024, aplicarea 025, provizionarea ParohiaTest, asocierea, sincronizarea reală). Motiv: URL-ul administrativ primit conține placeholder `<PAROLA_NOUA>` (nu o parolă), URL-ul runtime nu a fost furnizat, iar Cloud-ul TEST nu are o adresă publică la care să mă conectez. Parolele candidate pentru administratorul Cloud nu au putut fi încercate: nu există endpoint de Cloud accesibil. Nimic nu a fost aplicat pe Supabase.

### NETESTAT (explicit)
Windows real (UI PySide6, DPAPI, `.bat`, instalator), imaginea/containerul dumneavoastră Docker, cameră nativă / `getUserMedia` pe telefon real (iOS/Android), reverse proxy / Cloudflare real, fișiere mari peste rețea mobilă, UI web `/admin/` (nu are încă formular de provizionare; se folosește aplicația MAIN), Supabase PROD/Craiova (neatinse).

### Teste vechi roșii (preexistente, verificat identic pe `6344c52`)
`test_etapa1_desktop_flows`, `test_field_import_history::…no_migration`, `test_phone_capture::…no_secret…`, `test_work_native_fields::test_cloud_migrations_untouched` (va rămâne roșu cât există migrări noi), plus lansatoarele Windows/packaging (mediu). Nu sunt cauzate de această ramură.

## Probleme rămase / riscuri
1. **LIVE necompletat** ⇒ NU declar READY TO MERGE. Pași pentru dumneavoastră: `apply_cloud_provision_migration.py --check-only`, apoi aplicare 025, apoi `verify_provision_live.py` și circuitul MAIN→Mobile din ghid.
2. Dreptul de a crea organizații noi: doar administratorul inițial al instalării Cloud (`cp_admin_state.bootstrap_identity_uid`) sau Platform Admin. Dacă `admin.test@example.invalid` nu este administratorul inițial pe Supabase TEST, provizionarea va răspunde „fără drept” (mesaj clar) și e nevoie de decizie (acordare Platform Admin controlată).
3. Parola `postgres`/`service_role` a fost expusă în chat: rotiți-le după testele LIVE.
4. Instalările existente cu `admin/admin123` păstrează conturile (sunt forțate la schimbarea parolei la autentificare); nu le șterg automat.
5. Fotografiile de pe telefon intră prin `sync_v2_documents` (nu prin aprobarea manuală Field Inbox) — decizie preexistentă de confirmat.
