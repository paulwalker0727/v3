# RAPORT FAZA A — Provizionare automată backend

Bază: `origin/main` @ `d0289c8`. Ramură: `feature/cimitir-sync-etape-finale`. Un singur commit pentru Faza A.

## 1. Ce exista deja (auditat, nereimplementat)
* Centrul Cloud (6 file) cu fila Supabase: verificare + aplicare migrări **în copie TEST** (Setup Manager), backup local obligatoriu.
* Backend Cloud (`cimitir_cloud_app`): health, admin, bootstrap (Flux B), sync v2, mobile.
* Inițializarea primului administrator prin grant Ed25519 al proprietarului instanței (`cimitir_instance_grant`).
* `deploy/cloud/` (Caddy/nginx/compose **exemple**), `Dockerfile` imagine Cloud, `scripts/*` operaționale ad-hoc (aplicare migrări, rotire secrete Supabase, smoke).
* Protecții: `PROTECTED_PROJECT_REFS`, `cimitir_test_guard`.

## 2. Ce lipsea (constatat la audit)
* Nicio orchestrare: crearea unei instanțe cerea pași manuali (Supabase SQL Editor/PowerShell/Docker/variabile de mediu scrise manual).
* Niciun registru de instanțe, nicio autorizare de operator separată de licență, niciun jurnal de audit al acțiunilor administrative de infrastructură.
* Niciun rol runtime creat/verificat automat, nicio parolă per instanță, niciun seif.
* Niciun mecanism de actualizare/backup/recuperare/suspendare per instanță; migrările aplicate doar în TEST și cu fraze de confirmare manuale.
* Cheia secretă Storage și parola DB ajungeau în mediul containerului configurat manual.

## 3. Ce s-a implementat
Vezi `PROVIZIONARE_BACKEND.md` (arhitectură + cele 16 cerințe → implementare). Componente:

| Fișier | Rol |
|---|---|
| `shared/cimitir_prov_store.py` | registru SQLite, operatori (hash), confirmări, audit înlănțuit, seif AES-GCM, redactare |
| `shared/cimitir_prov_pg.py` | verificare admin, migrări idempotente (reutilizează analiza Setup Manager), rol runtime + verificare prin login, backup/restore criptat |
| `shared/cimitir_prov_storage.py` | bucket privat + probă scriere/citire/ștergere |
| `shared/cimitir_prov_runtime.py` | releases cu manifest, CA locală + certificate, procese izolate, `DockerProvider`, `caddy_site()` |
| `shared/cimitir_prov_edge.py` | terminator TLS local (demonstrație) |
| `shared/cimitir_provisioner.py` | orchestratorul (register/link/verify/migrate/secure/deploy/health/provision/update/backup/restore/suspend/resume/deactivate/rotate/renew/reconcile) |
| `shared/cimitir_provisioner_app.py` | API HTTP + CLI (`init-owner`, `serve`, `new-master-key`) |
| `shared/cimitir_prov_client.py` + `desktop/cloud_provision_tab.py` | client și fila „G. Provizionare” în MAIN |
| `shared/cimitir_prov_demo.py` + `scripts/provision_demo.py` | „proiect Supabase” local izolat (PG + Storage emulat) și demonstrația completă |
| `shared/cimitir_cloud_app.py` (1 linie) | `CIMITIR_LOCAL_DEMO=1` permite SUPABASE_URL loopback în demonstrații (nu în comercial) |
| `deploy/provisioner/*` | exemple systemd/Caddy/env, fără secrete |
| `ACTUALIZARE_BACKEND.md`, `RAPORT_SECURITATE_FAZA_A.md`, `TESTE_SI_REGRESII_FAZA_A.md` | documentație |

## 4. Defecte descoperite în timpul lucrului (în codul nou, reparate)
* Alegerea implicită „ultimul release” ar fi pornit un release defect importat recent ⇒ înlocuit cu release implicit setat explicit de owner (`DEFAULT`).
* Context FastAPI cu anotări amânate în emulatorul de Storage (422) ⇒ importuri la nivel de modul.
* Lipsa serializării operațiunilor pe aceeași instanță ⇒ lock per instanță.
* Gazdă DB arbitrară acceptată la `link` ⇒ restricționată la `*.supabase.com|co` sau loopback (TEST).

## 5. Observații despre codul existent (nemodificate)
* `tools/setup_manager.apply_plan` este legat de ghidul TEST (`guard.assert_write_allowed`), de aceea provisionerul are propriul `apply_migrations` (aceeași analiză, ghid propriu: mediu + proiect protejat + backup).
* Mediul de test al acestei sesiuni are doar 1024 de porturi efemere (`32768–33791`); suita backend completă produce erori „Cannot assign requested address” **și pe `main`** (vezi `TESTE_SI_REGRESII_FAZA_A.md`).

## 6. Nerezolvat / pentru fazele următoare
Fazele B, C, D; deploy real; test pe Supabase/Docker/Caddy/Windows reale; backup al obiectelor din Storage; izolare OS puternică între instanțe.
