# Asociere MAIN ↔ Cloud ↔ Telefon — fără PowerShell, SQL sau Python manual

Ramura `sync-v2-emergent-wip`. Acest document descrie cauza HTTP 404, fluxurile noi din interfață, ce a fost testat (LOCAL) și ce trebuie rulat de dumneavoastră pentru verificarea LIVE.

## 1. Cauza HTTP 404 (`preflight_for_code: eroare server 404 None`)

* Clientul MAIN (`shared/cimitir_cloud_client.py → RemoteControlPlane`) apelează `POST {url}/api/v1/bootstrap/preflight_for_code` (ruta reală; tabelul `cimitir_cloud_wire.OPS`).
* Imaginea Docker rulează `python -m cimitir_cloud_app`. Acea aplicație monta **doar** `/api/v1/health`, Field Inbox, Phone Pairing, Mobile v2 și Sync v2. **Rutele `/api/v1/bootstrap/*` nu erau montate deloc**: existau doar în `cimitir_cloud_http.create_app()`, un simulator SQLite folosit în teste. Orice cerere de asociere primea 404 de la FastAPI (`{"detail":"Not Found"}`), iar clientul îl afișa ca „eroare server 404 None”. `backend/server.py` (vechiul backend) nu are nicio legătură cu Cloud-ul.
* Reparat: `cimitir_cloud_http.create_router(cp)` este acum reutilizabil peste **orice** plan de control; `cimitir_cloud_app` îl montează peste `PostgresControlPlane` (rolul runtime `cimitir_cp_app`, RLS activ). S-a adăugat `GET /api/v1/bootstrap/info` (public, fără secrete) pentru testul de compatibilitate client↔server, iar clientul transformă un 404 pe aceste rute într-un mesaj clar (`ServerIncompatible`).
* Autentificarea codului: `Authorization: Pairing <cod>`; serverul caută sha256(cod), respinge cod inexistent/expirat/folosit/**revocat**, cere rolul `owner` al emitentului, limitează încercările greșite per IP/cont. Codurile din interfață sunt de 20 de caractere (alfabet fără ambiguități), afișate `XXXXX-XXXXX-XXXXX-XXXXX`; clientul normalizează spațiile/cratimele.

## 2. Ce există acum

| Cerință | Implementare |
|---|---|
| Asociere MAIN din interfață | `desktop/cloud_associate_window.py` (buton „🔗 Asociază cu Cloud” în bara principală) peste `shared/cimitir_cloud_associate.py`: URL → verificare API → selectare organizație/cimitir local → cod → verificare cod+drepturi → rezumat → **confirmare explicită** → cheie definitivă în SecretStore (DPAPI pe Windows) → test automat → activare sincronizare |
| Cheia nu apare în clar | doar în `cimitir_secretstore` (existent; DPAPI). Verificarea post-asociere cere ca cheia să nu apară în SQLite; nu e în loguri/JSON/UI |
| Sincronizare activată doar după verificare | setarea locală `cloud_assoc_unverified=1` blochează `sync_v2.connection()` până trece `verify()` |
| Întrerupere → reluare/anulare | `Association.incomplete()/resume()/abort()` (butoanele „Reia asocierea” / „Anulează asocierea”); `abort` revocă legătura, șterge cheia, readuce registrul la UNBOUND, fără a atinge datele locale |
| Coduri MAIN/telefon, copiere, revocare, regenerare, dispozitive | Interfața web `https://<cloud>/admin/` și fila „Coduri și dispozitive” din aplicația Windows (`cimitir_cloud_admin_client.py`) |
| Autentificare administrator | Email + parolă (scrypt), sesiuni opace revocabile în PostgreSQL (migrarea **023**), blocare 5 încercări/15 min, cookie HttpOnly+SameSite=Strict + antet CSRF pentru UI, `Bearer` pentru MAIN. Autorizare pe server prin `cp_org_roles` (doar `owner` emite/revocă). Nu folosește cheia de sincronizare și nu folosește utilizatorul `postgres` |
| Primul administrator | `BOOTSTRAP_TOKEN` + `BOOTSTRAP_TOKEN_EXPIRES_AT` (generate de `scripts/generate_bootstrap_token.py`), formular HTTPS `/admin/`, utilizabil o singură dată (starea e în PostgreSQL: supraviețuiește repornirii), refuzat dacă există deja un administrator, tokenul nu e logat |
| Provizionare coerentă organizație + cimitir + Owner | funcția `cp_admin_bootstrap` (atomică, sub lock): adoptă organizația existentă (ex. Craiova) sau creează organizație + cimitir DRAFT + Owner; ulterior „Cimitir nou” din `/admin/` |
| Recuperare administrativă | `scripts/cloud_admin_recovery.py --email …` pe serverul Cloud → token de unică folosință (15 min) → formular „Am pierdut accesul”; sesiunile vechi sunt revocate, datele nu se ating |
| Protecții păstrate | RLS neschimbat, `BLOCKED_ACTIVE_EMPTY`, `verify_empty`, `plan_hash` — neatinse |

## 3. Pașii pentru instalarea TEST (Craiova)

1. **Migrarea 023** (o singură dată; fără ștergeri; tranzacție unică, idempotentă, rollback disponibil):
   ```
   set CIMITIR_CP_ADMIN_DATABASE_URL=<URL administrativ al proiectului TEST, NU producție>
   python scripts/apply_cloud_admin_migration.py --project-ref fommbiwndlxkfsnlqatq --check-only
   python scripts/apply_cloud_admin_migration.py --project-ref fommbiwndlxkfsnlqatq
   ```
   Fără 023, noul server refuză să pornească (cod 3, mesaj clar) ca să nu ruleze pe jumătate.
2. **Token de inițializare** (pe laptopul cu Docker): `python scripts/generate_bootstrap_token.py --env-file .env` → repornește containerul (`docker compose … up -d --build`). Imaginea trebuie rebuilduită (conține rutele noi).
3. Deschide `https://<adresa-cloud>/admin/` → formular „Inițializare primul administrator”: token, email, parolă (≥12 caractere), **UID organizație** `88b61aa0-0572-4a6c-ac3e-539af51e67a4`, **UID cimitir** `309810ae-0fec-4055-b504-44b197fd65a4`. Se adoptă Owner-ul existent al organizației (UID `0bc9e380-…`) — nu se creează dubluri și nu se modifică datele. Apoi scoate `BOOTSTRAP_TOKEN*` din `.env`.
4. Autentifică-te → alege cimitirul → **Generează cod pentru MAIN** → copiază.
5. În aplicația Windows: **🔗 Asociază cu Cloud** → URL → *Verifică conexiunea* → cimitirul local → codul → *Verifică codul* → citește rezumatul → bifează confirmarea → *Asociază*. Rezultatul afișează testele automate; abia apoi sincronizarea devine activă.
6. Telefon: în `/admin/` → **Generează cod pentru telefon** (valabil 10 min) → se introduce în aplicația mobilă (Phone Pairing existent).
7. Dispozitive pierdute/compromise: `/admin/` → „Dispozitive asociate” → *Revocă* (cheia încetează imediat să funcționeze).

## 4. Teste

**LOCAL (Linux, PostgreSQL local cu rol `cimitir_cp_app` NOBYPASSRLS, uvicorn real pe port local, Supabase Storage SIMULAT, SQLite local) — executate:**
* `backend/tests/test_cloud_admin_pg.py` (14): bootstrap o singură dată + persistență, token expirat/lipsă, lockout, sesiuni/logout/parolă, CSRF, recuperare, asociere MAIN prin HTTP real cu cod emis din interfață (fix 404), cod expirat/revocat, `BLOCKED_ACTIVE_EMPTY` neocolit, cod telefon emis/revocat/folosit + revocare dispozitiv, izolare între organizații.
* `backend/tests/test_cloud_associate_pg.py` (7): fluxul complet cu aplicația Cloud reală, întrerupere → reluare fără cod nou, întrerupere → anulare → reluare de la zero, eșec verificare ⇒ sync rămâne blocat, URL greșit; fereastra Qt **offscreen** end-to-end (PySide6).
* `backend/tests/test_verify_cloud_live_local.py`: rulează `scripts/verify_cloud_live.py` pe stiva locală (PASS=16).
* Regresie pe suitele existente (etapa4, phone pairing, sync v2, field inbox, deploy).
* Migrarea 023 aplicată/repetată/rollback pe o bază cu 001–022, rânduri existente neschimbate.

**NETESTAT (nu considerați PASS):**
* **LIVE pe Supabase TEST** — nu am avut acces. Rulați `scripts/verify_cloud_live.py`.
* **Windows / DPAPI / aplicația PySide6 pe Windows** — verificat doar pe Linux (SecretStore de test în RAM și Qt offscreen).
* **HTTPS public prin tunel Cloudflare** — testat HTTP pe localhost; trebuie confirmat prin script LIVE.

## 5. Verificarea LIVE (PASS/FAIL)

```
set CLOUD_URL=https://<adresa-publică>
set CIMITIR_LIVE_ADMIN_EMAIL=<email Owner TEST>
set CIMITIR_LIVE_ADMIN_PASSWORD=<parola Owner TEST>
set CIMITIR_LIVE_ORG_UID=88b61aa0-0572-4a6c-ac3e-539af51e67a4
set CIMITIR_LIVE_CEMETERY_UID=309810ae-0fec-4055-b504-44b197fd65a4      (opțional: test respingere cross-cimitir; nu se modifică)
set CIMITIR_LIVE_OTHER_ORG_UID=<altă organizație TEST>                   (opțional: test izolare între organizații)
python scripts/verify_cloud_live.py --yes-write-test-data
```
Secțiuni: A conexiune (health db/storage, `/bootstrap/info`, login) · B asociere MAIN (cimitir izolat `LIVE-VERIFY-…`, cheie în SecretStore, nu în SQLite, cod refolosit respins) · C sincronizare MAIN→Cloud→telefon · D document PDF → Storage → URL semnat · E izolare · F revocarea dispozitivelor create. Un test care nu poate rula apare **SKIP**, nu PASS. Cod de ieșire 1 dacă există FAIL.

**Permisiuni minime necesare pentru LIVE:** (1) un cont **Owner** al organizației TEST în `/admin/` (nu `postgres`, nu cheia MAIN); (2) migrarea 023 aplicată; (3) niciun acces direct la PostgreSQL pentru script — folosește doar HTTPS. Pentru **aplicarea migrării** e nevoie de un rol cu drept DDL pe schema proiectului TEST (în `CIMITIR_CP_ADMIN_DATABASE_URL`); acesta nu e folosit de serverul runtime. Scriptul se oprește dacă ref-ul proiectului e cel de producție.

Scriptul nu șterge nimic din Craiova TEST: creează un cimitir DRAFT izolat (denumit `LIVE-VERIFY-<data>`), un MAIN și un telefon temporare, un loc și un document, apoi **revocă** cele două dispozitive. Cimitirul de test rămâne (nu există API de ștergere).

## 6. Limitări cunoscute / de urmărit

* Dezasocierea/anularea revocă legătura; o nouă asociere pe același cimitir după ce cloud-ul conține date (ACTIVE, nevid) intră pe fluxurile B/C existente, nu pe fluxul A din interfață.
* Fără UI de invitare a administratorilor suplimentari (doar primul administrator se creează prin bootstrap; alții se pot adăuga ulterior).
* Limitarea per IP depinde de `CIMITIR_TRUSTED_PROXIES` corect setat în spatele proxy-ului/tunelului; blocarea per cont funcționează oricum.
