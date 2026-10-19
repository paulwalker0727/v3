# REZULTATE TESTE FINALE

Mediu: Linux (Emergent). PostgreSQL 15 local (rol `cimitir_cp_app` fără BYPASSRLS), aplicația Cloud reală pe HTTP, Qt offscreen, Chromium headless, Storage SIMULAT în testele LOCAL.
Legendă: **PASS** = executat și trecut · **FAIL** · **SKIP** = sărit de test · **NETESTAT** = neexecutat. Numerele sunt din rulările efectuate efectiv, pe starea codului indicată.

## A. Teste automate LOCAL

### A.1 Teste noi (scrise în această etapă)
| Fișier | Rezultat |
|---|---|
| `backend/tests/test_etapa789_provision_pg.py` | **8 PASS** — provizionare cu UID originale + asociere + BOUND; idempotență; non-provisioner refuzat; non-Owner refuzat; UID de cimitir din altă organizație ⇒ conflict; sesiune/UID invalide; dialog Qt end-to-end (parolă greșită → corectă → provizionare → BOUND) |
| `backend/tests/test_etapa789_regreq_pg.py` | **7 PASS** — cerere → confirmare → cod automat → BOUND; secret/cod nestocate, cod emis o singură dată; duplicate 409/400/limite; vizibilitate pe roluri + RLS; respingere; buton Qt; instrument arhivare (preview nu scrie, token greșit refuzat, registru protejat refuzat, arhivare confirmată, nimic șters); `approve` doar boolean |
| `desktop/tests/test_first_run_wizard.py` | **4 PASS** — fără conturi implicite, reguli primul admin, flag de dezvoltare, dialog |

### A.2 Regresie (rulate pe cod final, fără modificări de cod ulterioare, doar documentație)
| Set | Rezultat |
|---|---|
| `test_etapa789_regreq_pg` + `test_etapa789_provision_pg` + `test_cloud_admin_pg` + `test_cloud_associate_pg` + `test_first_run_bootstrap` | **44 PASS, 0 FAIL, 0 SKIP** (`CIMITIR_FIRSTRUN_ADMIN_URL` cu superuser local; fără el 7 teste din `test_first_run_bootstrap` dau ERROR de mediu „must be owner of schema public”, nu de cod) |
| Suita etapa 7-9 / sync v2 / pairing / Mobile v2 / field deployment / PWA Chromium (13 fișiere, rulată pe cod aproape final) | **137 PASS, 0 FAIL**, 7 ERROR doar din `test_first_run_bootstrap` (aceeași cauză de mediu, re-rulat separat: 8 PASS) |
| Aceeași suită pe prima versiune a ramurii (fără PWA/first-run) | 131 PASS, 1 SKIP |
| `desktop/tests` (lansatoare Windows/packaging/QR-map **excluse de mine**: 69 deselectate) | **439 PASS, 2 SKIP, 4 FAIL** (vezi A.3) |

### A.3 Teste care eșuează — preexistente (verificat identic pe `main` @ `6344c52` curat)
- `desktop/tests/test_etapa1_desktop_flows.py::test_desktop_flows_on_fictitious_data` (`assert 360 > 1000`)
- `desktop/tests/test_field_import_history.py::test_history_does_not_change_import_mechanism_and_adds_no_migration`
- `desktop/tests/test_phone_capture.py::test_no_secret_or_signed_url_in_client_code_and_page`
- `desktop/tests/test_work_native_fields.py::test_cloud_migrations_untouched` (numără migrările; rămâne roșu cât există 024+)
Nu sunt cauzate de aceste modificări; **nu sunt reparate** (în afara scopului).

### A.4 Agentul de testare (UI `/admin/` + API, instanță locală)
16 teste API (pytest) + fluxuri Playwright: login, stare goală, rând cerere cu ID scurt și „organizație NOUĂ/cimitir NOU”, Confirmă (org apare în selector), Respinge (nu se creează nimic), poll aprobat o singură dată apoi `consumed`, secret greșit 404, duplicat 409, cimitir al altei organizații 400, org nouă fără nume 400, secret scurt 400, 401 fără sesiune, 403 fără antetul CSRF, UUID invalide 400, JSON malformat 400, formular „Avansat” idempotent. **Toate OK.** Ulterior: reparate observațiile de UX și validarea `approve` (acoperită de test propriu); screenshot desktop (1920) și mobil (390) verificat. Fișierul de test al agentului nu face parte din patch.

## B. Teste LIVE efectuate efectiv pe Supabase TEST `jqtdeuzkqtzfsdohvxmy`
Cloud-ul a rulat din Emergent (`python -m cimitir_cloud_app`, 127.0.0.1), conectat la PostgreSQL-ul real (rol runtime `cimitir_cp_app`) și la Storage-ul real; testele au mers prin HTTP către el. Adresa publică HTTPS: **NETESTAT**.
| Test | Rezultat |
|---|---|
| Schemă (read-only): 023 aplicată, 024 prezentă cu RLS, rol runtime fără BYPASSRLS, date existente (3 org / 4 cem / 8 dispozitive / 1 document la prima verificare) | **PASS** |
| Aplicare migrări 025 apoi 026 (tranzacții, numărul de rânduri neschimbat) | **PASS** (025, 026 aplicate) |
| Storage: bucket `cimitir-private-test` privat; upload/citire cu cheie; acces public/anonim respins; obiectul de test șters | **PASS** |
| `verify_provision_live.py` — **prima rulare** | **FAIL** la asociere (timeout HTTP 5 s) ⇒ bug reparat (20 s) |
| `verify_provision_live.py` — după reparație (registru fictiv: provizionare UID originale, fără confirmare nu scrie, asociere, BOUND, izolare UID, revocare) | **8 PASS / 0 FAIL** |
| `verify_provision_live.py` — cu fluxul de cerere MAIN→confirmare→cod automat→asociere + duplicat respins | **9 PASS / 0 FAIL / 0 SKIP** |
| `verify_cloud_live.py` (cimitir DRAFT izolat într-o organizație fictivă: asociere, sync MAIN→Cloud, telefon asociat cu cod, PDF telefon→Storage privat→MAIN, izolare între organizații, chei invalide 401, revocare) | **15 PASS / 0 FAIL / 1 SKIP** (SKIP = cod cross-cimitir, variabilă nesetată) |
| `test_data_inventory.py inventory` (read-only) | **PASS** — 11 cimitire, 7 fictive arhivabile, 4 protejate |
| Revocare a 2 legături active rămase din rulări întrerupte (timeout local) | efectuată |
| `tests/test_etapa789_live_supabase_test.py` | **NERULAT intenționat** — face TRUNCATE pe tabelele proiectului |
| `test_data_inventory.py archive --apply` pe Supabase | **NERULAT** (previzualizarea și confirmarea au fost testate doar LOCAL) |
Nu s-a atins Supabase Craiova TEST sau PROD. Nu au fost modificate/șterse organizațiile, cimitirele sau documentele existente (doar adăugate registre fictive).
Date fictive rămase pe TEST: 6 organizații `LIVE-PROVISION-*` (+ câte un cimitir) și 1 cimitir `LIVE-VERIFY-*`.

## C. Teste de securitate și permisiuni (efectuate)
| Verificare | Unde | Rezultat |
|---|---|---|
| Doar administratorul instalării/Platform Admin creează organizații noi; Owner simplu refuzat (403) | LOCAL + logică în DB | PASS |
| Doar Owner adaugă cimitir în organizație existentă; simplu ADMIN refuzat (403) | LOCAL | PASS |
| UID de cimitir aparținând altei organizații nu se suprascrie (400/conflict) | LOCAL + LIVE | PASS |
| Cererile: Owner vede doar cererile organizației lui; simplu ADMIN nu vede nimic; organizație nouă doar provisioner | LOCAL | PASS |
| Secretul cererii stocat doar ca sha256; niciun câmp de cod în tabel; cod emis o singură dată; secret greșit 404 | LOCAL | PASS |
| Fără confirmare explicită nu se scrie nimic (API MAIN + UI) | LOCAL + LIVE | PASS |
| Sesiune obligatorie pe rutele administrative (401); antet CSRF obligatoriu (403) | LOCAL (agent) | PASS |
| Validare intrări: UUID invalid, secret scurt, `approve` ne-boolean, nume lipsă ⇒ 400 | LOCAL | PASS |
| Rate-limit cereri publice (10/15 min/IP), plafon 20 în așteptare, expirare 24 h | cod; plafonul global testat doar prin construcție | PARȚIAL (limita pe IP exercitată de agent; plafonul 20 nu a fost saturat) |
| Fără conturi implicite în instalări comerciale; parole slabe/egale cu numele refuzate | LOCAL | PASS |
| RLS: rolul runtime fără BYPASSRLS; tabelul `cp_registry_requests` fără grant direct | LOCAL + schema LIVE | PASS |
| Suitele existente (nemodificate): izolare organizații, filtrare CNP/acte (`view_pii`), drept „Încărcare documente”, revocare dispozitive, cheie telefon ≠ sesiune administrator, coduri consumate/expirate | LOCAL (137 PASS) + LIVE (`verify_cloud_live`) | PASS |

## D. NETESTAT (nu pretind nimic)
- **Windows real:** UI PySide6 instalat, DPAPI/Windows Credential Manager, `.bat`/`.ps1`, butonul „Trimite cererea” în aplicația instalată.
- **Docker:** imaginea `cimitir-cloud` și containerul `cimitir-cloud-etapa789-test` (8084) de pe laptopul dumneavoastră; `Dockerfile`-ul nu a fost construit în Emergent.
- **Telefon real** Android/iPhone: scanare QR cu camera nativă, scanare în PWA, `getUserMedia`, fotografii online/offline pe dispozitiv, reluare reală, HTTPS/Cloudflare real, reverse proxy real.
- Circuitul complet „loc de veci MAIN → PostgreSQL → Mobile” cu aplicația MAIN instalată (în LIVE a fost acoperit prin `verify_cloud_live.py`, nu prin UI-ul MAIN).
- Sincronizare documente/fotografii cu fișiere mari prin rețea mobilă.

## E. Erori rămase (rezumat)
1. 4 teste desktop vechi roșii (preexistente pe `main`).
2. Prima rulare LIVE a picat (timeout) — reparat și re-rulat PASS; rulări locale întrerupte au lăsat 2 legături active (revocate).
3. Date fictive netouched/nearhivate pe Supabase TEST.
4. Plafonul global de 20 cereri nu a fost saturat într-un test.
5. Toate verificările de pe Windows/Docker/telefon: NETESTAT.
**Concluzie: NU este READY TO MERGE** până la executarea pașilor din `INSTALARE_TEST_WINDOWS_DOCKER.md` și `TEST_TELEFON_ANDROID_IPHONE.md`.
