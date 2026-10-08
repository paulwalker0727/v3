# PRD – v3 (Etapa 4 – Pasul 6A)

## Problema originală
Clonare integrală github.com/paulwalker0727/v3 și continuare Etapa 4 – Pasul 6A (Supabase TEST nhhczdmcocbobptogvsw, production txuxkjgftopznvmooexq hard-blocked): teste PostgreSQL, migrare 013→014, fresh install 001→014, Flux A/B live, RLS, cleanup, suite complete.

## Stare (2026-10-08)
- v3 clonat în /app/v3 (origin/main @ b0ebb63). Fără push, fără merge, fără force push.
- Branch `origin/conflict_081026_2054` @ 634f34c: istorie NEÎNRUDITĂ cu main (fără merge-base), 3 commituri, 2069 fișiere = șablon gol + director `.pgdata` (date PostgreSQL binare). NU conține cod 6A.
- Codul Pasului 6A (migrarea 014, tabele cp_*, adapter PostgreSQL pentru control plane, Flux A/B pe PG, bugfix-ul de citire rânduri PG) NU există nici pe main, nici pe branch. memory/PRD.md din repo: „Neimplementat: Pasul 6A, 014".
- Migrări existente: 001–013. Teste Etapa 4 existente: până la Pas 5.1.
- Supabase TEST: guard-urile (ref, CIMITIR_LIVE_TESTS=1, CIMITIR_TEST_PROJECT_REF) au trecut; conexiunea READ-ONLY a eșuat cu „password authentication failed". Zero scrieri. Fără reîncercări.
- PostgreSQL local neinstalat încă; nu s-a rulat nicio suită.

## Backlog
- P0: primirea codului 6A (branch/commit corect) SAU decizia de implementare de la zero; parolă Supabase TEST corectă.
- P1: instalare PG local, teste adapter/migrări/RLS, apoi inspecție read-only TEST.
- P2: Flux A/B live pe TEST, cleanup, suite complete.
