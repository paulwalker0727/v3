# TESTE ȘI REGRESII — Faza A (provizionare backend)

Mediu de execuție: container Linux (aarch64), Python 3.11, PostgreSQL 15 local (clustere izolate), PySide6 6.8 offscreen. **Nu** Windows, **nu** Docker, **nu** Supabase real, **nu** Caddy/Let's Encrypt.

## 1. Teste noi (executate)
| Suită | Rezultat | Ce acoperă |
|---|---|---|
| `backend/tests/test_provisioner_unit.py` | **13 passed** | redactare, seif (cheie-master 0600, cheie greșită, fișier modificat, nume invalid), release (manifest, fără cod provisioner în release, alterare), comenzi Docker generate fără secrete, site Caddy, porturi/chei unice, PROD (proiect local/TLS/protejat refuzate, separare TEST/PROD), API HTTP (401 fără token/`.lic`/grant, 403 operator, 429, `/docs` oprit, audit `denied`), client MAIN, tokenuri hash-uite, expirarea confirmărilor |
| `backend/tests/test_provisioner_pg.py` | **21 passed** | pe PostgreSQL real + procese backend reale + HTTPS real: înregistrare/validări, asociere + proiect protejat + unicitate, URL admin nepotrivit, provizionare a două parohii (health complet), HTTPS cu CA locală (CA greșită și nume greșit respinse), reprovizionare idempotentă (fără duplicate), izolare între parohii și secrete (credențiale A respinse de B, nicio parolă în fișiere/audit/seif în clar, fișier de seif mutat nu se decriptează), rol runtime minim prin login real, inițializare administrator doar cu grant corect (`.lic`/grant străin refuzate, al doilea grant 409), backup criptat + alterare detectată + restaurare în bază separată, restaurare peste instanță (confirmare, cod unic, backup de siguranță, date refăcute după „dezastru”), actualizare cu migrare nouă fără pierdere de date, migrare eșuată ⇒ ROLLBACK + vechiul backend continuă, release defect ⇒ revenire automată, release alterat refuzat, autorizarea acțiunilor distructive, suspendare/reluare/dezactivare (acces DB revocat, date păstrate), rotirea parolei runtime + reînnoire TLS fără repornire, `reconcile` după cădere, audit complet + lanț (alterare detectată), circuit complet prin API HTTP + client |
| `desktop/tests/test_cloud_provision_tab.py` | **4 passed** | fila G (Qt offscreen): token în SecretStore, URL nesigur refuzat, cheia privată doar în SecretStore, acces blocat pentru non-admin |
| `desktop/tests/test_cloud_center.py` (modificat: a 7-a filă) | **15 passed, 1 skipped** | Centrul Cloud |

Rulare: `cd backend && python -m pytest -n 0 tests/test_provisioner_unit.py tests/test_provisioner_pg.py` (~30 s; sare automat dacă PostgreSQL server nu e instalat).

## 2. Regresii (executate)
| Verificare | Rezultat |
|---|---|
| `test_cloud_field_deployment.py`, `_pg.py`, `test_cloud_init_admin_pg.py`, `test_cloud_admin_pg.py` (după modificarea `preflight`) | **65 passed** |
| Suita `desktop/tests`, fișier cu fișier, `main` vs ramura mea | **identic** (aceleași fișiere cu aceleași rezultate; singura diferență: fișierul nou) — `test_pdf_reports`, `test_qr_labels`, `test_license`, `test_qr_public_data`: „no tests ran”/dependențe lipsă; `test_qr_custom_and_map`, `test_qr_scanning`: timeout 90 s **și pe main**; `test_phone_capture`: 1 failed **și pe main** |
| Suita `backend/tests` completă, `main` vs ramura mea | **INCONCLUDENTĂ**: mediul are doar 1024 de porturi efemere (`32768–33791`); suita produce „Cannot assign requested address”/„Address already in use” **și pe main** (main: 24 failed + 421 errors; ramura: 41 failed + 416 errors, din care 18–19 sunt testele mele de integrare căzute tocmai din epuizarea porturilor într-o rulare monolitică). |
| Verificare țintită pe fișiere cu erori (`main` vs ramura): `test_etapa789_provision_pg` (8 errors), `test_cloud_associate_pg` (1 passed, 6 errors), `test_etapa9b_cloud_config` (2 failed, 6 passed), `test_first_run_bootstrap` (1 passed, 7 skipped) | **rezultate identice pe main și pe ramură** ⇒ defecte/cerințe de mediu preexistente (nu cauzate de Faza A). `test_etapa4_pas6a_reruns_on_pg`: 19 passed + 148 errors pe ramură; pe `main` rularea individuală nu a produs linie de rezumat — **comparație neconcludentă** |
| O rulare paralelă (`-n 2`) a arătat un singur eșec în `test_etapa4_pas6a_reruns_on_pg.py[media:after_chunk:1]` | trece izolat și pe `main` și pe ramură (4 passed) ⇒ instabil sub paralelism |

**Concluzie onestă:** nu am o dovadă completă „zero regresii” pentru întreaga suită backend din cauza mediului. Modificările la cod existent sunt trei: o condiție în `preflight` (cu 40+25 teste verzi), o filă în Centrul Cloud (teste desktop identice cu `main`) și un test actualizat. **Recomand rularea suitei complete pe Windows/Linux cu porturi efemere normale înainte de merge.**

## 3. Ce NU s-a putut testa (neexecutat — nu se declară PASS)
* Supabase real (pooler `user.ref`, `sslmode=require`, rolul `postgres` Supabase, Storage real) — flag-ul `pooler` e implementat dar netestat.
* Docker real (`DockerProvider` verificat doar prin comanda generată), Caddy/ACME (doar configurația generată).
* Windows (fila G, DPAPI, MAIN).
* Încărcare/performanță, scanere de securitate (bandit, pip-audit), test de penetrare.
* Backup al obiectelor din Storage (nu este implementat).
* Faza B/C/D: neînceput în această livrare.

## 4. Demonstrație executată
`python scripts/provision_demo.py` (două parohii, HTTPS, administrator prin grant, backup, audit verificat): rulată cu succes — ambele instanțe `running`, `ready=True`, `admin_initialized=True`, audit 22 înregistrări, lanț valid.
