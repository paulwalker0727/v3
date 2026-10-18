# Etapa 7-9 — Instrucțiuni scurte (Windows și Docker)

## 1. Integrarea ramurii
```bash
git clone https://github.com/paulwalker0727/v3 && cd v3
git fetch /cale/spre/branch.bundle etapa789-asociere-finala:etapa789-asociere-finala     # sau: git checkout -b etapa789-asociere-finala && git am etapa789-asociere-finala.patch
git checkout etapa789-asociere-finala
```
`main` nu se modifică; integrarea se face printr-un Pull Request din `etapa789-asociere-finala`.

## 2. Cloud (Docker, pe server Linux)
1. Copiază `.env.example` → `.env` și completează: `DATABASE_URL`/`CIMITIR_CP_DATABASE_URL` (rol **cimitir_cp_app**, nu `postgres`), `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `SUPABASE_BUCKET` (bucket **PRIVAT**),
   `CIMITIR_CLOUD_PROJECT_REF`, `CIMITIR_PUBLIC_URL=https://cloud.parohia.ro` (adresa publică din QR) și, dacă Cloudflare/nginx/Caddy stă în față, `CIMITIR_PAIRING_TRUSTED_PROXIES=<IP/rețeaua proxy-ului>`.
2. Migrații (după aprobare, pe TEST întâi): vezi `ETAPA789_MIGRATII_ROLLBACK.md`. Pe un Cloud existent (ex. Supabase TEST cu date) NU reaplica 001–024: rulează `python scripts/apply_cloud_provision_migration.py --project-ref <REF> --check-only` (raport read-only), apoi fără `--check-only` pentru a aplica doar 025.
3. `docker compose -f deploy/cloud/docker-compose.example.yml --env-file .env up -d --build`
4. Verificare: `https://<domeniu>/api/v1/health` (liveness) și `/api/v1/ready` (readiness: DB + Storage).

## 3. MAIN (Windows) — instalare de la zero, fără SQL/PowerShell pentru asociere
1. Instalează/actualizează aplicația (`deploy/windows/INSTALL_PATCH.ps1` sau reinstalare).
2. **Prima pornire**: creezi organizația și cimitirul (selectorul de registre), apoi apare **Configurare inițială**: îți alegi numele și parola **administratorului local MAIN** (min. 12 caractere).
   Nu există conturi implicite (`admin/admin123` nu se mai creează). Contul LOCAL (în aplicație) este diferit de contul CLOUD (administratorul serverului).
3. Autentifică-te local → **🔗 Asociază cu Cloud** (necesită dreptul `manage_config`, altfel nu se deschide):
   - Introdu adresa Cloud (HTTPS) și emailul/parola **administratorului Cloud** → **Autentifică-te în Cloud și verifică registrul**.
   - Aplicația compară UID-urile locale (organizație + cimitir) cu Cloud-ul și afișează: *există* / *lipsește – se creează cu aceleași UID-uri* / *refuzat* (cont fără drepturi sau cimitir din altă organizație).
   - Bifează confirmarea → **Provizionează (dacă e nevoie) și asociază automat**: se creează registrul în Cloud (dacă lipsește; doar administratorul instalării poate crea organizații noi, un Owner poate adăuga cimitire în organizația lui),
     se emite codul MAIN, se rulează planul Flux A cu protecțiile existente, cheia definitivă merge direct în SecretStore (DPAPI), legătura este verificată și abia apoi sincronizarea se activează.
   - Varianta manuală (cod primit de la administrator) rămâne disponibilă în același ecran.
4. Indicatorul arată: Conectat · Offline · Eroare · Sincronizare în curs. Documentele și fotografiile se sincronizează automat (acte de identitate/cereri NU).

Timeout-ul HTTP către Cloud este 20 s; se poate schimba cu variabila `CIMITIR_CLOUD_HTTP_TIMEOUT` (3–120).

## 4. Telefon (PWA)
Administratorul generează codul/QR în `/admin/`; pe telefon: camera nativă (deschide linkul `…/mobile/#pair=…`) **sau** `Scanează codul QR` în aplicație. Pentru încărcarea de fotografii,
dispozitivul trebuie să aibă dreptul `Încărcare documente`. Datele personale (CNP, act de identitate) nu ajung pe telefon decât dacă administratorul acordă explicit `view_pii`.

## 5. Teste
```bash
bash scripts/dev_postgres.sh start                        # PostgreSQL local (necesar testelor *_pg)
export CIMITIR_TEST_PG_ADMIN_URL=postgresql://postgres@127.0.0.1:55432/postgres
cd backend && python -m pytest tests/test_etapa789_*.py -n 0 -q
cd ../desktop && python -m pytest tests/test_etapa789_cloud_indicator_docs.py -n 0 -q
```

## 6. Verificare LIVE a circuitului nou (după aplicarea 025)
```bash
set CLOUD_URL=https://<adresa-cloud-TEST>
set CIMITIR_LIVE_ADMIN_EMAIL=<administrator Cloud TEST>
set CIMITIR_LIVE_ADMIN_PASSWORD=<parola>
python scripts/verify_provision_live.py --yes-write-test-data
```
Scrie doar un registru FICTIV „LIVE-PROVISION-…”, îl asociază, apoi revocă dispozitivul de test.
