# Etapa 7-9 — Instrucțiuni scurte (Windows și Docker)

## 1. Integrarea ramurii
```bash
git clone https://github.com/paulwalker0727/v3 && cd v3
git fetch /cale/spre/branch.bundle etapa789-finalizare:etapa789-finalizare     # sau: git checkout -b etapa789-finalizare && git am etapa789.patch
git checkout etapa789-finalizare
```
`main` nu se modifică; integrarea se face printr-un Pull Request din `etapa789-finalizare`.

## 2. Cloud (Docker, pe server Linux)
1. Copiază `.env.example` → `.env` și completează: `DATABASE_URL`/`CIMITIR_CP_DATABASE_URL` (rol **cimitir_cp_app**, nu `postgres`), `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `SUPABASE_BUCKET` (bucket **PRIVAT**),
   `CIMITIR_CLOUD_PROJECT_REF`, `CIMITIR_PUBLIC_URL=https://cloud.parohia.ro` (adresa publică din QR) și, dacă Cloudflare/nginx/Caddy stă în față, `CIMITIR_PAIRING_TRUSTED_PROXIES=<IP/rețeaua proxy-ului>`.
2. Migrații (după aprobare, pe TEST întâi): vezi `ETAPA789_MIGRATII_ROLLBACK.md`.
3. `docker compose -f deploy/cloud/docker-compose.example.yml --env-file .env up -d --build`
4. Verificare: `https://<domeniu>/api/v1/health` (liveness) și `/api/v1/ready` (readiness: DB + Storage).

## 3. MAIN (Windows)
1. Actualizează aplicația cu `deploy/windows/INSTALL_PATCH.ps1` (sau reinstalează).
2. În MAIN: **Cloud → Configurare simplă**: introdu adresa Cloud și mediul (TEST), apoi **Asociază acest calculator** cu codul generat de administrator în `/admin/`.
   Cheia se păstrează automat în SecretStore (DPAPI); la repornire MAIN se reconectează singur. Indicatorul arată: Conectat · Offline · Eroare · Sincronizare în curs.
3. Documentele și fotografiile din MAIN se trimit automat la fiecare ciclu de sincronizare (acte de identitate/cereri NU se trimit).

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
