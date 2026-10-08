# Etapa 5 — Serverul pe PostgreSQL

Activare (backend/.env sau mediu): `CIMITIR_MODE=online`, `CIMITIR_DB_ENGINE=postgres`, `DATABASE_URL=postgresql://...` (producție: `?sslmode=require`).
Aici (preview): PostgreSQL local de dezvoltare pornit cu `scripts/dev_postgres.sh start` (127.0.0.1:55432, db `cimitir_dev`).
Atenție: este local la pod; pentru producție folosește un PostgreSQL gestionat (ex. Supabase).

## GET /api/health (public, fără secrete)
{"database":"postgres","database_status":"online","server_status":"online","version":"1.0.0-etapa5","time":"..."}
503 + `database_status: offline` dacă baza nu răspunde. Nu expune DATABASE_URL, utilizator/parolă DB sau JWT_SECRET.

## Teste
`cd backend && CIMITIR_TEST_DATABASE_URL=postgresql://cimitir@127.0.0.1:55432/cimitir_test python -m pytest tests --ignore=tests/test_live_smoke.py -n 0`
(151 passed). `tests/conftest.py` forțează SQLite implicit, ca backend/.env (PostgreSQL) să nu influențeze testele.
Desktopul nu a fost modificat. Etapa 6 NU a fost pornită.
