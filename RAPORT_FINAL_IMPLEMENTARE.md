# RAPORT FINAL — Implementare Cimitir Cloud ↔ MAIN ↔ Mobile (etapa 7-9, finalizare)

Repository: `paulwalker0727/v3` · bază: `main` @ `6344c52` · ramura de dezvoltare: `etapa789-admin-provision-ui` · 8 commit-uri, 33 de fișiere (vezi `git diff --stat main..HEAD`).
Mediu de lucru: Linux (Emergent). Nu am avut acces la Windows-ul, Docker-ul sau telefonul dumneavoastră. Rezultatele testelor sunt în `REZULTATE_TESTE_FINALE.md`.

## 1. Cauza problemei critice
`preflight_for_code` respingea codul („Codul nu este valabil pentru acest cimitir/organizație…”) pentru că organizația `65702ca7-…` și cimitirul `d7e338df-…` create în MAIN nu existau în PostgreSQL, iar codurile erau emise pentru un cimitir fictiv existent. Nu exista nicio operație „înregistrează registrul MAIN în Cloud păstrând UID-urile”; singura cale era SQL manual.

## 2. Ce am implementat efectiv
1. **Provizionarea registrului în Cloud cu UID-urile originale** (migrarea 025): organizație + cimitir create cu exact UID-urile din MAIN; idempotent; autorizarea se verifică în baza de date.
2. **Asocierea completă din interfață** (MAIN → „🔗 Asociază cu Cloud”), în două variante:
   - **Varianta A — cerere confirmată în /admin/** (migrarea 026; recomandată, fără parola Cloud pe MAIN);
   - **Varianta B — autentificare cu administrator Cloud** în MAIN: verifică registrul, provizionează după confirmare, emite codul, asociază.
   - Varianta manuală cu cod rămâne disponibilă.
3. **Formular și cereri în /admin/**: cardul „Cereri de înregistrare de la MAIN” (Confirmă / Respinge) și formularul „Avansat” (UID-uri manuale, doar ca rezervă).
4. **Asistent de configurare inițială MAIN**: creează primul administrator local; **nu mai există conturi implicite** (`admin/admin123`, `user/user123`) în instalările comerciale (doar cu `CIMITIR_ALLOW_DEFAULT_USERS=1`, pentru dezvoltare). Explică separat contul LOCAL de contul CLOUD. Fereastra de asociere se deschide doar cu dreptul `manage_config`.
5. **Instrument de inventariere/arhivare a datelor TEST** (`scripts/test_data_inventory.py`): `inventory`, `show` (read-only), `archive` (previzualizare implicită + token de confirmare; doar registre `LIVE-VERIFY-*` / organizații `LIVE-PROVISION-*`; nu șterge nimic).
6. **Scripturi operaționale**: `apply_cloud_provision_migration.py` (raport read-only + aplicare 025/026 + rollback), `verify_provision_live.py` (circuit LIVE pe registru fictiv).
7. **Timeout HTTP** către Cloud 20–25 s (implicit era 5–10 s și a picat pe Supabase); `CIMITIR_CLOUD_HTTP_TIMEOUT` îl poate modifica.
8. **Documentație**: proceduri laptop/Windows/Docker, telefon Android/iPhone, migrații + rollback.

## 3. Cum funcționează acum asocierea MAIN ↔ Cloud ↔ Mobile
**Varianta A (cerere):**
1. MAIN (autentificat local, `manage_config`): bifează confirmarea → *Trimite cererea de înregistrare*. Se trimite `POST /api/v1/admin/registry-requests` cu UID-urile și denumirile locale + un secret aleatoriu (rămâne doar în SecretStore-ul MAIN; Cloud-ul păstrează doar sha256).
2. În `/admin/`, Owner-ul organizației existente (sau, pentru organizație nouă, administratorul inițial al instalării / Platform Admin) vede cererea: efectul (organizație/cimitir NOI sau existente), denumirile, ID scurt, UID-uri trunchiate → **Confirmă** (dialog de confirmare) sau **Respinge**.
3. La *Confirmă*: `cp_admin_provision` creează (dacă lipsesc) organizația și cimitirul cu UID-urile trimise; cererea devine `approved`.
4. MAIN interoghează cererea la 4 s. La prima interogare după aprobare, serverul **emite codul MAIN ca aprobator, o singură dată** (nu se stochează) și MAIN rulează automat: `verify_code` → plan Flux A (protecțiile `plan_hash`, `verify_empty`, `BLOCKED_ACTIVE_EMPTY` neschimbate) → execuție → cheia definitivă în SecretStore → verificare → abia apoi sincronizarea e activă.
**Varianta B:** administrator Cloud în MAIN → „verifică registrul” (read-only) → rezumat → confirmare → provizionare + cod + asociere automat.
**Mobile:** neschimbat funcțional — administratorul emite cod/QR pentru telefon în `/admin/`; telefonul se asociază (camera nativă sau scanare în PWA), sincronizează locuri/contracte/înhumați/plăți/avize/documente, filtrarea CNP/actelor se face pe server (`view_pii`), fotografiile se încarcă în Storage privat (coadă offline, reluare, fără duplicate), revocarea oprește sincronizarea. Aceste fluxuri au fost verificate de suitele automate existente (vezi raportul de teste); **pe telefon real nu au fost verificate**.

## 4. Provizionarea păstrând UID-urile MAIN
- UID-urile **nu se generează și nu se modifică** în Cloud: se preiau exact din registrul local (`binding.organization_uid`, `binding.cemetery_uid`).
- `cp_admin_provision(identity, org_uid, org_name, cem_uid, cem_name)` (SECURITY DEFINER, `search_path` fixat, EXECUTE revocat de la PUBLIC, acordat doar `cimitir_cp_app`):
  - administrator activ obligatoriu;
  - organizație existentă ⇒ rol **Owner** obligatoriu; organizație nouă ⇒ doar **administratorul inițial al instalării** (`cp_admin_state.bootstrap_identity_uid`) sau **Platform Admin**;
  - cimitir cu acel UID aparținând **altei organizații** ⇒ refuz (nu se suprascrie);
  - lock advisory per organizație; idempotent (a doua apelare nu creează nimic);
  - cimitirul nou se creează `DRAFT`; RLS rămâne activă, nu se ocolește.

## 5. Probleme reparate
| Problemă | Reparație |
|---|---|
| Asociere imposibilă fără SQL (registru MAIN lipsă în Cloud) | Provizionare automată (025) + cereri confirmate în /admin/ (026) |
| Nicio verificare a permisiunilor înaintea fluxurilor administrative | Fereastra de asociere cere `manage_config` |
| Conturi implicite `admin/admin123` pe SQLite local | Eliminate; asistent de configurare inițială |
| Timeout HTTP 5 s pe Supabase (eșec la asociere în LIVE) | 20 s (client MAIN) / 25 s (client admin), configurabil |
| UX /admin/ (butoane lipite, derulare mobilă, org aprobată neselectată), `approve` acceptat ca string | Reparate după raportul agentului de testare |
| Test vechi care presupunea conturi implicite | `test_first_run_bootstrap` actualizat la comportamentul nou |

## 6. Probleme rămase (nu sunt ascunse)
1. **NETESTAT:** MAIN pe Windows, imaginea/containerul Docker de pe laptop, telefon real (Android/iPhone), HTTPS/Cloudflare real. Ecranul Qt a fost verificat doar offscreen.
2. Endpoint-ul public al cererilor (fără sesiune, cu secret) permite depunerea de cereri nevalidate: limite 10/15 min/IP, 20 în așteptare global, expirare 24 h. Un atacator poate bloca temporar înregistrarea unui UID (nu creează nimic fără confirmare). Recomandat: proxy cu rate-limit dacă Cloud-ul e public.
3. 4 teste desktop vechi sunt roșii **și pe `main` nemodificat**: `test_etapa1_desktop_flows`, `test_field_import_history::…no_migration`, `test_phone_capture::…no_secret…`, `test_work_native_fields::test_cloud_migrations_untouched` (acesta din urmă numără migrările, deci rămâne roșu cât există 024+).
4. Rămân în Supabase TEST registre fictive create de testele mele (6 organizații `LIVE-PROVISION-*` cu câte un cimitir, 1 cimitir `LIVE-VERIFY-*`); nu au fost arhivate (cerere separată).
5. Parola rolului runtime și a rolului administrativ de pe Supabase TEST sunt aceeași valoare; parolele și cheia `service_role` au circulat în chat ⇒ **rotiți-le**.
6. Formularul /admin/ nu are teste Playwright în repo (a fost testat o dată de agentul de testare; fișierul de test nu face parte din patch).

## 7. Migrații PostgreSQL necesare
| Migrare | Rol | Rollback |
|---|---|---|
| `023_cloud_admin_auth.sql` | Preexistentă (necesară; aplicată pe Supabase TEST) | existent |
| `024_sync_v2_documents.sql` | Preexistentă; tabelul `sync_v2_documents` există deja pe TEST. **Nu o reaplicați** | `024_…ROLLBACK.sql` |
| **`025_admin_provision_registry.sql`** | Funcții `cp_admin_is_provisioner`, `cp_admin_registry_lookup`, `cp_admin_provision` (doar `CREATE OR REPLACE FUNCTION` + GRANT) | `025_…ROLLBACK.sql` |
| **`026_registry_requests.sql`** | Tabel `cp_registry_requests` (RLS activă, fără grant pentru rolul runtime) + funcții `cp_regreq_*` | `026_…ROLLBACK.sql` |
Aplicare pe Cloud existent: `scripts/apply_cloud_provision_migration.py --project-ref <REF> --check-only`, apoi fără `--check-only` (o tranzacție, verifică că numărul de rânduri rămâne identic, refuză PROD). **025 și 026 sunt deja aplicate pe Supabase TEST `jqtdeuzkqtzfsdohvxmy`** (de mine, după raportul read-only). Fără 026, `/admin/registry-requests*` răspunde 501 `SCHEMA_OUTDATED`; asocierea cu parolă Cloud sau cu cod continuă să funcționeze.

## 8. Fișiere modificate / create (față de `main`)
**Migrații:** `backend/cimitir/pg_migrations/025_…sql`, `026_…sql`; `backend/cimitir/pg_rollbacks/025_…_ROLLBACK.sql`, `026_…_ROLLBACK.sql`.
**Cloud (shared):** `shared/cimitir_cloud_admin.py` (registry_status, provision_registry, cereri), `shared/cimitir_cloud_admin_http.py` (rute noi), `shared/cimitir_cloud_admin_client.py`, `shared/cimitir_cloud_app.py` (avertisment la pornire dacă 025/026 lipsesc), `shared/cimitir_cloud_associate.py` (asociere cu administrator, cerere, polling, timeout), `shared/cimitir_auth.py` (fără conturi implicite, `first_run_required`, `setup_first_admin`).
**Admin web:** `backend/static/cloud_admin.js`, `backend/static/cloud_admin.css`.
**MAIN desktop:** `desktop/cloud_associate_window.py`, `desktop/first_run_wizard.py` (nou), `desktop/main.py`, `desktop/ui_mainwindow.py`, `desktop/tests/conftest.py`.
**Scripturi:** `scripts/apply_cloud_provision_migration.py`, `scripts/verify_provision_live.py`, `scripts/test_data_inventory.py` (toate noi).
**Teste:** `backend/tests/test_etapa789_provision_pg.py`, `backend/tests/test_etapa789_regreq_pg.py`, `desktop/tests/test_first_run_wizard.py` (noi); `backend/tests/test_first_run_bootstrap.py` (actualizat).
**Documentație:** `ETAPA789_FINALIZARE_RAPORT.md`, `ETAPA789_RAPORT_ADMIN_PROVISION_UI.md`, `ETAPA789_INSTRUCTIUNI_WINDOWS_DOCKER.md`, `ETAPA789_MIGRATII_ROLLBACK.md`, `TEST_LAPTOP_WINDOWS_DOCKER.md`, `TEST_TELEFON_ANDROID_IPHONE.md`, `RAPORT_FINAL_IMPLEMENTARE.md`, `REZULTATE_TESTE_FINALE.md`, `INSTALARE_TEST_WINDOWS_DOCKER.md`.
Nu s-a modificat: Sync v2, Storage, pairing, RLS existentă, documentele existente, `main`.

## 9. Ce trebuie verificat de dumneavoastră înainte de merge
Vezi `INSTALARE_TEST_WINDOWS_DOCKER.md` (laptop) și `TEST_TELEFON_ANDROID_IPHONE.md` (telefon). **Nu declar READY TO MERGE** până nu sunt executate acele teste.
