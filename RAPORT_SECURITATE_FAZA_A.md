# RAPORT SECURITATE — Faza A (provizionare backend)

Autoevaluare a codului nou (`shared/cimitir_prov_*.py`, `cimitir_provisioner*.py`). **Nu** este audit independent și **nu** s-a făcut test de penetrare extern. „Verificat” = test automat rulat local (vezi `TESTE_SI_REGRESII_FAZA_A.md`).

## Cerințe de arhitectură → status

| Cerință | Status | Dovadă |
|---|---|---|
| Fără parole Supabase în cod/Git | ✅ verificat | căutare de valori-sentinelă în registru, audit, pași, fișiere de instanță, seif, loguri backend (`test_07`); doar fișiere `*.example` |
| Secret manager / echivalent | ✅ parțial | seif AES-256-GCM, cheie-master din fișier 0600/mediu; **nu** e KMS/HSM |
| Separare TEST / PROD | ✅ verificat | `CIMITIR_PROVISIONER_ENV`; `ENV_MISMATCH`; PROD refuză proiecte locale și DB fără TLS; seif namespaced `TEST/…` vs `PROD/…` |
| Fără porturi PostgreSQL publice | ✅ pentru ce controlăm | provisionerul nu deschide PostgreSQL; clusterele demo și backendurile ascultă doar pe `127.0.0.1`; portul Supabase aparține furnizorului |
| Fără acces între parohii | ✅ verificat | credențialele runtime/Storage ale A sunt respinse de B (`test_07`); proiect/DB `UNIQUE` per instanță; director/porturi/seif separate; mediu de proces minim |
| Fără reutilizare credențiale | ✅ verificat | parole runtime, chei Storage, chei de backup distincte per instanță |
| `.lic` nu dă drept de creare instanțe | ✅ verificat | token `.lic` / grant ⇒ 401; autorizarea e doar tokenul de operator; licențierea nu a fost atinsă |
| Provisionare autorizată separat | ✅ | serviciu separat, operatori cu roluri |
| Fără păstrarea parolei admin Supabase | ✅ verificat | URL administrativ doar per apel; redactat; nepersistat |

## Controale implementate
* Autentificare: tokenuri `cpv_` (256 biți), stocate ca SHA-256; 10 eșecuri/5 min/IP ⇒ 429; `/docs` și OpenAPI dezactivate.
* Autorizare: `operator`/`owner`; acțiunile distructive (`suspend`, `deactivate`, `restore` peste instanță) cer `owner` + cod unic (TTL 300 s, legat de operator/acțiune/instanță, o singură folosire) + slug tastat.
* Audit: tabel append-only (trigger), lanț SHA-256; modificarea după eliminarea trigger-ului este **detectată** (testat pe o copie). Un atacator cu acces complet la fișier poate reface lanțul: ancorare externă a ultimului hash — neimplementată.
* Integritate release: manifest SHA-256 per fișier; release modificat ⇒ `RELEASE_TAMPERED`; release-urile sunt imuabile. Semnarea pachetelor: Faza D.
* Backup: AES-256-GCM, cheie per instanță, AAD = nume fișier; modificare ⇒ `BACKUP_CORRUPT`; restaurare peste instanță = confirmare + backup de siguranță.
* Migrări: o tranzacție + lock; ROLLBACK complet la eroare (testat).
* Rol runtime: verificat prin login real.
* Validare intrări: `supabase_url` doar `<ref>.supabase.co`/loopback(TEST); gazdă DB doar `*.supabase.com|co`/loopback; regex pentru slug/domeniu; nume de secrete fără `..`.
* Concurență: lock per instanță pentru operațiuni mutabile.

## Riscuri reziduale / neacoperite
1. Cine administrează serverul poate citi memoria/mediul proceselor instanțelor (vezi nota de confidențialitate din Centrul Cloud).
2. Cheia-master a seifului e pe același server ⇒ compromiterea serverului expune secretele runtime (nu parolele administrative Supabase, care nu sunt stocate).
3. Izolarea locală între instanțe = proces/mediu/director 0700, **nu** sandbox OS; `DockerProvider` e netestat.
4. Terminatorul TLS local nu e pentru producție (fără HSTS/ACME).
5. Parola administrativă tranzitează către provisioner în corpul cererii ⇒ **HTTPS obligatoriu** în față (Caddy); serviciul ascultă doar pe 127.0.0.1.
6. `ALTER ROLE … PASSWORD` poate apărea în jurnalul PostgreSQL dacă `log_statement=all`.
7. Fără MFA pentru operatori.
8. Nu s-au rulat scanere automate (bandit, pip-audit): **netestat**. Teste pe Supabase real, Docker real, Windows: **neexecutate**.

## Modificări în fișiere existente relevante pentru securitate
* `shared/cimitir_cloud_app.py::preflight`: acceptă `SUPABASE_URL` loopback **doar** cu `CIMITIR_LOCAL_DEMO=1` și `CIMITIR_COMMERCIAL!=1` (demonstrație locală). Nicio modificare la licențe, Ed25519, Hardware ID, `cimitir_test_guard.py` sau `PROTECTED_PROJECT_REFS`.
