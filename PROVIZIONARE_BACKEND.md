# Provizionare automată backend Cloud (Faza A)

Serviciul de provizionare permite **operatorului** (tu) să creeze, verifice, actualizeze, copieze de siguranță, suspende și dezactiveze instanțe Cloud izolate pentru parohii, din Centrul Cloud al MAIN (fila **G. Provizionare**) sau prin API. Administratorii obișnuiți **nu** folosesc PowerShell, SQL Editor, Docker sau generare manuală de tokenuri.

> **Stare:** implementat și testat **local** (PostgreSQL real local, procese backend reale, HTTPS real cu CA locală). **Nu** s-a făcut deploy real, nu s-a testat pe Supabase real, Docker real, Caddy/Let's Encrypt real sau Windows. Vezi secțiunea „Ce mai cere infrastructură pentru producție”.

## 1. Arhitectură

```
MAIN (Centrul Cloud, fila G)  ──HTTPS + token operator──►  Serviciu de provizionare (al operatorului)
                                                             │  registru SQLite + audit înlănțuit + seif AES-256-GCM
                                                             ├─► proiect Supabase al parohiei A  (PostgreSQL + Storage privat)
                                                             │      └─ backend izolat A (proces/container, parole proprii, port propriu) ─ HTTPS ─ domeniu A
                                                             └─► proiect Supabase al parohiei B  … (nimic comun cu A)
```

* Serviciul de provizionare e **separat** de backendul parohiei. Codul lui **nu** intră în release-ul instanței (verificat prin test).
* Fiecare instanță: proiect Supabase propriu, rol runtime `cimitir_cp_app` cu **parolă proprie** (generată per instanță), cheie Storage proprie, cheie de backup proprie, port propriu, director propriu (0700), mediu de proces **minim** (nu moștenește nimic din mediul provisionerului).
* Un proiect Supabase / o bază de date nu poate fi asociată la două instanțe (constrângere `UNIQUE`); proiectul marcat „producție” al proprietarului (`PROTECTED_PROJECT_REFS`) este refuzat permanent.

## 2. Autorizare (separată de licență)

| Ce | Cum |
|---|---|
| Cine poate provizona | doar **operatori** din registrul serviciului (token `cpv_…`, doar hash SHA-256 stocat) |
| Roluri | `operator` (înregistrare, verificare, deploy, backup, actualizare, rotire secrete) · `owner` (în plus: suspendare, reluare, dezactivare, restaurare peste baza instanței, import release, release implicit) |
| Acțiuni distructive | rol `owner` **+** cod de confirmare unic (TTL 5 min, legat de operator + acțiune + instanță) **+** slug-ul instanței tastat |
| TEST ≠ PROD | serviciul rulează cu `CIMITIR_PROVISIONER_ENV=TEST|PROD`; un operator/instanță din alt mediu este refuzat (`ENV_MISMATCH`); PROD refuză proiecte locale și conexiuni DB fără TLS |
| Licența `.lic` | **nu** este acceptată ca autorizare; nici grantul proprietarului instanței (testat: 401) |
| Forță brută | 10 tokenuri greșite / 5 min / IP ⇒ 429 |

Primul owner se creează local, pe serverul serviciului: `python -m cimitir_provisioner_app init-owner --home DIR --name NUME` (tokenul se afișează o singură dată).

## 3. Secrete

* **Parola administrativă Supabase** (URL cu parolă): se trimite per apel (verify/migrate/secure/backup/restore/update/suspend…), se folosește din memorie, **nu se stochează, nu se loghează** (redactare în audit/pași/erori; testat cu valori-sentinelă în registru, audit, fișiere, seif, loguri). După configurare nu mai este necesară pentru funcționarea instanței.
* **Seif** (`Vault`): AES-256-GCM, cheia-master doar din `CIMITIR_PROVISIONER_MASTER_KEY` sau `CIMITIR_PROVISIONER_MASTER_KEY_FILE` (refuzat dacă fișierul nu e 0600). Numele secretului este legat ca AAD ⇒ un fișier copiat între instanțe nu se decriptează (testat). Echivalent „secret manager” pentru un singur server; pentru HA folosește Vault/KMS al furnizorului (neimplementat).
* În Git: nimic. `deploy/provisioner/*.example` conțin doar placeholdere.
* Mediul backendului instanței (inclusiv URL-ul DB runtime și cheia Storage) se transmite prin **mediul procesului** (local) sau `-e NUME` (Docker), niciodată prin argv sau fișier în repository.

## 4. Pașii provizionării (idempotenți, reluabili)

| # | Cerință | Implementare |
|---|---|---|
| 1 | Înregistrare instanță | `register` — slug, domeniu, parohie; cheia proprietarului: furnizată (publică) **sau** generată (privata se returnează o singură dată și merge în SecretStore-ul MAIN) |
| 2 | Asociere proiect Supabase | `link` — ref, URL, gazdă/port/bază DB, bucket, cheie Storage (în seif); validări + unicitate + proiect protejat |
| 3 | Verificare conectivitate | `verify` — PostgreSQL (versiune, drept de creare roluri) + Storage (bucket privat creat/verificat, scriere/citire/ștergere de probă) |
| 4 | Migrări controlate, idempotente | `migrate` — analiza reală a stării bazei (aceeași logică ca Setup Manager), o tranzacție, lock consultativ, verificarea obiectelor, fără scădere de rânduri, ROLLBACK la eroare; backup obligatoriu dacă baza are date |
| 5 | Rol runtime minim | `secure` — `cimitir_cp_app` fără superuser/BYPASSRLS/CREATEROLE/CREATEDB/REPLICATION, parolă proprie; **verificare prin login real** (nu poate crea tabele/roluri/șterge `schema_migrations`); rolul `cimitir_app` fără parolă |
| 6–7 | Backend izolat + config fără secrete în repo | `deploy` — release verificat (manifest SHA-256), proces propriu, mediu generat la pornire |
| 8–9 | Domeniu + HTTPS/TLS | local: CA locală + certificat per domeniu + terminator TLS; producție: `caddy_site()` generează site Caddy (ACME + HSTS) |
| 10 | Verificare sănătate | `health` — procese, HTTPS cu verificarea numelui, `/healthz`, `/api/v1/health` (DB, Storage), stare administrator, zile certificat |
| 11 | Primul administrator | cheia proprietarului instanței (Ed25519, **distinctă** de cheia licențelor) ⇒ fila existentă „D. Administratori” din MAIN; serviciul raportează `admin_initialized` |
| 12 | Status în Centrul Cloud | fila **G. Provizionare** (listă + stare + sănătate) |
| 13 | Actualizare fără pierdere de date | `update` — backup verificat → migrări (ROLLBACK la eșec, vechiul backend continuă) → repornire pe noul release → health → **revenire automată** la release-ul anterior dacă nu e sănătos |
| 14 | Backup / recuperare | `backup` (pg_dump criptat AES-256-GCM, cheie per instanță, verificat `pg_restore --list`), `restore` (în bază goală separată, sau peste instanță cu confirmare + backup de siguranță) |
| 15 | Suspendare / dezactivare | `suspend`/`resume`/`deactivate` — doar `owner` + confirmare; oprește procesele și pune rolul `NOLOGIN`; dezactivarea șterge secretele runtime; **nu șterge date** |
| 16 | Audit | tabel append-only, înlănțuit SHA-256 (`audit/verify`); acțiuni `ok`/`error`/`denied`; fără secrete |

## 5. Utilizare

### Demonstrație locală
```bash
sudo apt install postgresql postgresql-client        # server local PostgreSQL (doar pentru demonstrație/teste)
pip install -r backend/requirements-cloud.txt
python scripts/provision_demo.py                      # două parohii izolate, HTTPS, administrator, backup
```

### Server real al operatorului (Linux)
```bash
python -m cimitir_provisioner_app new-master-key > /etc/cimitir/provisioner.key && chmod 600 /etc/cimitir/provisioner.key
export CIMITIR_PROVISIONER_ENV=PROD CIMITIR_PROVISIONER_MASTER_KEY_FILE=/etc/cimitir/provisioner.key
python -m cimitir_provisioner_app init-owner --home /var/lib/cimitir-prov --name <numele-tau>
python -m cimitir_provisioner_app serve --home /var/lib/cimitir-prov --port 8443      # doar 127.0.0.1; expune prin Caddy HTTPS
```
Vezi `deploy/provisioner/` (systemd, Caddy, variabile). Importul release-ului: `POST /releases {"source_path": "/opt/cimitir-src", "version": "1.0.0"}` apoi `POST /releases/default`.

### Flux pentru o parohie nouă (din MAIN, fila G)
1. Înregistrează (slug, parohie, domeniu) ⇒ cheia proprietarului ajunge în SecretStore.
2. Beneficiarul își creează proiectul Supabase și îți oferă (o singură dată) URL-ul de conexiune administrativ + cheia secretă Storage.
3. `link` + `provision` ⇒ instanța pornește, HTTPS verificat.
4. „D. Administratori” ⇒ inițializezi primul administrator. Parola administrativă Supabase poate fi schimbată/revocată de beneficiar după pasul 3.

## 6. Ce funcționează local vs. ce cere infrastructură pentru producție

| Funcție | Testat local | Producție |
|---|---|---|
| PostgreSQL, migrări, rol minim, backup/restore | ✅ PostgreSQL 15 local, clustere izolate | De verificat pe Supabase real (pooler `user.ref`, `sslmode=require`, drepturile rolului `postgres` din Supabase, `pg_dump` prin pooler cu versiune compatibilă). Flag `pooler` e implementat dar **netestat** |
| Storage | ✅ pe emulator minimal | De verificat pe Supabase Storage real (API-ul este cel folosit de `SupabaseStorage`, dar emulatorul nu e Supabase) |
| Proces backend izolat | ✅ proces Linux per instanță | Pe server: `LocalProcessProvider` sub systemd, **sau** `DockerProvider` (comenzi generate, **netestat pe Docker real**) |
| TLS | ✅ CA locală + verificarea numelui | **Caddy + ACME** (`caddy_site()` doar generează configurația; **netestat**) — cere domeniu, DNS A/AAAA, porturi 80/443 |
| Backup Storage (obiecte) | ❌ | Se salvează baza de date; fișierele din bucket **nu** sunt copiate de provisioner (folosește backupul Supabase / replicare bucket) |
| Rotirea cheii Storage / ștergerea proiectului | ❌ | manual din Supabase; apoi `link` din nou |
| Seif în HSM/KMS | ❌ | seif pe fișiere AES-GCM cu cheie-master din fișier 0600 |
| Windows | ❌ | serviciul de provizionare este pentru Linux (folosește `/proc`); clientul și fila G rulează în MAIN pe Windows (netestat pe Windows) |

## 7. Limitări cunoscute (onest)
* Terminatorul TLS local este un forwarder TCP (nu adaugă HSTS / nu rescrie `X-Forwarded-For`); în producție folosește Caddy.
* `restore` peste instanță pe un proiect Supabase real necesită ca `pg_restore --clean` să aibă drepturi asupra obiectelor (ownership Supabase) — netestat.
* Parola rolului runtime se setează cu `ALTER ROLE … PASSWORD`; dacă `log_statement=all` pe server, parola poate ajunge în jurnalul PostgreSQL (Supabase nu activează implicit).
* Backupul se criptează în memorie (arhive foarte mari: de evitat; pentru cimitire parohiale dimensiunea e mică).
* Un singur server de provizionare (SQLite); fără înalta disponibilitate.
