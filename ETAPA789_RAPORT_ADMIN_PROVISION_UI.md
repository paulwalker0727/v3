# Etapa 7-9 — Formular /admin/ + cereri MAIN, proceduri laptop/telefon, instrument de curățare TEST

Ramură: `etapa789-admin-provision-ui`. **`origin/main` este încă `6344c52`** (ramura anterioară `etapa789-asociere-finala` nu a fost integrată/împinsă de mine), deci aceasta se construiește peste ea. Patch cumulativ față de `main` + patch incremental livrate separat.

## Ce s-a implementat
1. **Cereri de înregistrare MAIN → /admin/** (migrarea 026 + API + UI + MAIN):
   - MAIN („Asociază cu Cloud” → bifă + **Trimite cererea de înregistrare**) trimite automat UID-urile și denumirile; secretul cererii rămâne doar în SecretStore (în Cloud doar sha256).
   - `/admin/` arată cardul „Cereri de înregistrare de la MAIN” (ID scurt, organizație/cimitir, efect NOUĂ/existentă, UID-uri originale trunchiate) cu **Confirmă / Respinge** (confirmare explicită în browser).
   - La Confirmă: se rulează `cp_admin_provision` (UID-urile originale; idempotent), iar MAIN, care interoghează cererea la 4 s, primește **automat** codul MAIN (emis la prima interogare, o singură dată, nestocat), rulează planul Flux A și activează sincronizarea. Nicio copiere manuală de UID/cod.
   - Protecții: UID de cimitir din altă organizație ⇒ 400; cerere duplicat în așteptare per cimitir ⇒ 409; organizație nouă fără nume ⇒ 400; secret scurt ⇒ 400; limite 10 cereri/15 min/IP, 20 în așteptare, expirare 24 h; vizibilitate/decizie: Owner-ul organizației existente sau (organizație nouă) administratorul inițial/Platform Admin (verificat în DB, SECURITY DEFINER; RLS neatinsă, tabelul fără grant pentru rolul runtime); `approve` doar boolean real.
   - Formular **Avansat** (manual, UID-uri) în /admin/, pentru cazul în care MAIN nu poate trimite cererea.
2. **Proceduri**: `TEST_LAPTOP_WINDOWS_DOCKER.md` (comenzi exacte, tabel PASS/FAIL gol), `TEST_TELEFON_ANDROID_IPHONE.md` (QR, cameră, foto online/offline, reluare, permisiuni, izolare).
3. **Curățare TEST**: `scripts/test_data_inventory.py` — `inventory`, `show` (read-only, orice cimitir), `archive` (previzualizare implicită; aplicare doar cu `--apply --confirm <token din previzualizare>`; doar `LIVE-VERIFY-*` și cimitire din organizații `LIVE-PROVISION-*`; orice altceva „PROTEJAT”; nu șterge nimic; arhivare reversibilă; revocarea legăturilor doar cu `--revoke-bindings`).
4. Timeout HTTP al clientului admin 25 s (Supabase). `apply_cloud_provision_migration.py` aplică acum 025+026 (și rollback ambele).

## Rezultate

### LOCAL (Linux, PostgreSQL 15 local, Cloud real pe HTTP)
- Noi `test_etapa789_regreq_pg.py` — **7 PASS** (flux complet cerere→confirmare→cod automat→BOUND; secret/cod nestocate, cod emis o singură dată; duplicate/conflicte/limite; vizibilitate pe Owner/Admin/instalare + RLS; respingere; buton Qt offscreen; instrument de arhivare: previzualizare nu scrie, token greșit refuzat, registru protejat refuzat, arhivare confirmată, nimic șters) + `approve` boolean.
- Regresie: provision/admin/associate **36 PASS**; suita etapa789/sync/pairing/Mobile v2/PWA (Chromium) **137 PASS**; `test_first_run_bootstrap` **8 PASS** (cu `CIMITIR_FIRSTRUN_ADMIN_URL` superuser); desktop **439 PASS**, 4 roșii preexistente (aceleași ca pe `6344c52`).
- UI `/admin/` (agent de testare, Playwright + API, 16 teste): login, stare goală, rând cerere, Confirmă/Respinge, org apare în selector, poll aprobat o singură dată, secret greșit 404, 401 fără sesiune, 403 fără antet CSRF, UUID invalid 400, formular Avansat idempotent. Rezultat: toate OK. Ulterior am reparat observațiile de UX (selectare org aprobată, spațiere butoane, derulare orizontală pe mobil) și validarea strictă a `approve`; verificat prin screenshot desktop (1920) și mobil (390), fără depășire în afara containerelor derulabile.

### LIVE (Supabase TEST `jqtdeuzkqtzfsdohvxmy`, Cloud rulat din Emergent spre PostgreSQL real, rol runtime fără BYPASSRLS)
- Schemă verificată read-only; **026 aplicată** (025 era deja aplicată); doar adăugări, numărul de rânduri neschimbat.
- `verify_provision_live.py`: **9 PASS / 0 FAIL** — provizionare cu UID originale + asociere + BOUND + izolare + revocare + **(nou) cerere MAIN → confirmare administrator → cod automat → asociere + duplicat respins**.
- `test_data_inventory.py inventory` pe Supabase: 11 cimitire, 7 fictive arhivabile, 4 protejate (incl. seed-ul „Parohia Test (FICTIV)/Cimitir Test FICTIV”, rândurile 6C etc.). **`archive` NU a fost executat pe Supabase** (nu ați cerut curățarea efectivă; doar previzualizarea a fost testată local).
- Legăturile active rămase din două rulări întrerupte (timeout local de client) au fost revocate (2). Registrele fictive `LIVE-PROVISION-*`/`LIVE-VERIFY-*` rămân (nearhivate).
- NU rulat: `test_etapa789_live_supabase_test.py` (face TRUNCATE).

### NETESTAT (explicit)
- **Windows real** (UI PySide6, DPAPI/Credential, butonul de cerere în aplicația instalată), **imaginea Docker de pe laptop**, circuitul 4.3–4.8 din `TEST_LAPTOP_WINDOWS_DOCKER.md` (loc de veci → PostgreSQL → Mobile; documente; foto) pe laptopul dvs.
- **Telefon real Android și iPhone**: toate procedurile din `TEST_TELEFON_ANDROID_IPHONE.md` (cameră nativă, scanare în PWA, foto online/offline pe dispozitiv, reluare reală).
- Reverse proxy/Cloudflare/HTTPS real; formularul /admin/ nu are teste Playwright automate în repo (rulate o dată de agentul de testare; fișier în afara repo).

## Riscuri / limitări rămase
1. **Squatting/DoS pe endpoint-ul public al cererilor**: oricine ajunge la Cloud poate depune cereri (limite: 10/15 min/IP, 20 în așteptare global, expirare 24 h) și poate bloca temporar înregistrarea unui UID de cimitir până la respingere în /admin/. Nu creează nimic fără confirmare. Dacă Cloud-ul e pe internet, rezervați-l în spatele unui proxy/rate-limit sau măriți plafonul.
2. Organizația nouă o poate confirma doar administratorul inițial/Platform Admin (verificat pe TEST: `admin.test@example.invalid`).
3. Migrările 025/026 sunt aplicate pe Supabase TEST; rollback: `apply_cloud_provision_migration.py --rollback`.
4. Parolele DB/administrator/`service_role` au circulat în chat: rotiți-le. Parola rolului runtime și parola DB admin sunt identice în datele primite — recomand parole diferite.
5. READY TO MERGE: **NU** — teste critice NETESTATE (Windows/Docker laptop, telefon real).
