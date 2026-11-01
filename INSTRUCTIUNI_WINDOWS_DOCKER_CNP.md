# Aplicare patch și rulare teste — Windows și Docker

Patch-ul se aplică pe baza exactă `ea68832` (`origin/main`). Nu necesită migrări de schemă (nicio modificare SQL în această livrare).

## Aplicare (Windows PowerShell)
```powershell
git fetch origin
git checkout -b fix/cnp-sync-audit ea68832
git apply --check cnp-sync-fix.patch
git apply cnp-sync-fix.patch        # sau: git am cnp-sync-fix.mbox
```
Sau din bundle: `git fetch cnp-sync-fix.bundle fix/cnp-sync-audit:fix/cnp-sync-audit`.

Backend-ul cloud trebuie repornit (modificarea este în `shared/cimitir_sync_v2.py`); fișierele PWA (`backend/static/*.js`) se reîncarcă pe telefon automat (Service Worker `shell-v8`). MAIN (desktop) nu se modifică și rămâne compatibil.

## Teste (Docker, PostgreSQL local — NU Supabase)
```bash
docker run -d --name pgtest -e POSTGRES_HOST_AUTH_METHOD=trust -p 54329:5432 postgres:15
export CIMITIR_TEST_PG_ADMIN_URL=postgresql://postgres@127.0.0.1:54329/postgres
cd backend
pip install -r requirements-test.txt "psycopg[binary]"
python -m pytest tests/test_cnp_sync_pg.py tests/test_cnp_main_e2e.py -n 0 -q
python -m pytest tests/test_sync_v2_pg.py tests/test_mobile_permissions_pg.py tests/test_etapa789_pii_docs_pg.py tests/test_sync_v2_main_e2e.py -n 0 -q
```
Nota: pe unele containere cu interval îngust de porturi efemere (`ip_local_port_range`) un fișier lung de teste poate pica spre final cu `Cannot assign requested address` (epuizare TIME_WAIT, nu defect al aplicației) — rulează testele în loturi mai mici.

## Reversibilitate
`git revert <commit>` sau `git apply -R cnp-sync-fix.patch`. Nicio dată nu este modificată sau ștearsă.
