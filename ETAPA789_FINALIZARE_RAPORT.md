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

### LIVE (Supabase TEST `jqtdeuzkqtzfsdohvxmy`, PostgreSQL real cu rol runtime `cimitir_cp_app` fără BYPASSRLS, RLS activ, Storage real)
Aplicația Cloud a rulat pe Linux (Emergent) cu `python -m cimitir_cloud_app` pe 127.0.0.1, conectată la Supabase; testele au mers prin HTTP către ea. Adresa publică HTTPS/Cloudflare: NETESTAT.
- Schemă (read-only, înainte de orice modificare): 023 aplicată, 024 (`sync_v2_documents`) prezentă cu RLS, rol runtime fără BYPASSRLS/SUPERUSER, tabele de control complete. Date existente: 3 organizații, 4 cimitire, 8 dispozitive, 1 document — numărul de rânduri a rămas identic după aplicare.
- **Migrarea 025 aplicată doar pe Supabase TEST** (`apply_cloud_provision_migration.py`, tranzacție unică, 001–024 NU au fost reaplicate). Administratorul `admin.test@example.invalid` este administratorul inițial al instalării ⇒ poate provizionă.
- `verify_provision_live.py` (registru FICTIV nou, UID-uri generate local): **8 PASS / 0 FAIL / 0 SKIP** — registrul lipsește ⇒ fără confirmare nu se scrie nimic ⇒ provizionare cu UID-urile originale + cod + asociere + verificare ⇒ BOUND, cheia doar în SecretStore ⇒ reutilizarea unui UID de cimitir din altă organizație respinsă (400) ⇒ dispozitivul revocat.
  - Prima rulare a eșuat (FAIL) la asociere: timeout HTTP implicit de 5 s al clientului, insuficient pentru latența Supabase. Reparat: `CIMITIR_CLOUD_HTTP_TIMEOUT` (implicit 20 s); a doua rulare PASS.
- `verify_cloud_live.py` (cimitir DRAFT izolat într-o organizație FICTIVĂ): **15 PASS / 0 FAIL / 1 SKIP** (SKIP = cod cross-cimitir, variabilă nesetată) — health db+storage, asociere, cod consumat respins, MAIN→Cloud (idempotent), telefon asociat cu cod și vede locul, telefon→Storage privat→MAIN pentru PDF, izolare între organizații, cheie de telefon ≠ sesiune de administrator, chei inventate 401, revocare dispozitive.
- Storage: bucketul `cimitir-private-test` este privat; upload/citire cu cheie OK; acces public/anonim respins.
- **NU am rulat** `tests/test_etapa789_live_supabase_test.py`: acesta face TRUNCATE pe tabelele proiectului (ar șterge datele existente). Rămâne de rulat doar pe un proiect gol sau după decizia dvs.
- Date fictive create pe TEST: 2 organizații `LIVE-PROVISION-…` (+1 cimitir fiecare), 1 cimitir `LIVE-VERIFY-…` în „Parohia Test (FICTIV)”, dispozitive revocate. Nu există API de ștergere; curățarea rămâne la decizia dvs. Organizația `65702ca7-…`/cimitirul `d7e338df-…` (ParohiaTest) NU au fost create de mine: la asociere din MAIN-ul dvs. vor fi provizionate automat.

### NETESTAT (explicit)
Windows real (UI PySide6, DPAPI, `.bat`, instalator), imaginea/containerul dumneavoastră Docker, cameră nativă / `getUserMedia` pe telefon real (iOS/Android), reverse proxy / Cloudflare real, fișiere mari peste rețea mobilă, UI web `/admin/` (nu are încă formular de provizionare; se folosește aplicația MAIN), Supabase PROD/Craiova (neatinse).

### Teste vechi roșii (preexistente, verificat identic pe `6344c52`)
`test_etapa1_desktop_flows`, `test_field_import_history::…no_migration`, `test_phone_capture::…no_secret…`, `test_work_native_fields::test_cloud_migrations_untouched` (va rămâne roșu cât există migrări noi), plus lansatoarele Windows/packaging (mediu). Nu sunt cauzate de această ramură.

## Probleme rămase / riscuri
1. **Circuitul real de pe laptopul dvs. (MAIN Windows + Docker + ParohiaTest) este NETESTAT** ⇒ NU declar READY TO MERGE. Pași: reconstruiți imaginea cu ramura, în MAIN: Asociază cu Cloud → administrator Cloud → Provizionează și asociază; apoi QR telefon și fotografii. Fotografiere pe telefon real: NETESTAT.
2. Dreptul de a crea organizații noi: doar administratorul inițial al instalării Cloud sau Platform Admin. Verificat pe Supabase TEST: `admin.test@example.invalid` este administratorul inițial.
3. Parola rolurilor `postgres`/`cimitir_cp_app`, parola administratorului Cloud și cheia `service_role` au fost comunicate în chat (nu sunt în repo/patch): rotiți-le acum că testele LIVE s-au încheiat. Parola administratorului Cloud și cea a rolurilor de bază de date sunt aceeași valoare: schimbați-le.
4. Instalările existente cu `admin/admin123` păstrează conturile (sunt forțate la schimbarea parolei la autentificare); nu le șterg automat.
5. Fotografiile de pe telefon intră prin `sync_v2_documents` (nu prin aprobarea manuală Field Inbox) — decizie preexistentă de confirmat.
