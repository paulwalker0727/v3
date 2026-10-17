# Etapa 7-9 — Rezultate teste (PASS / FAIL / SKIP)

Mediu: Linux, Python 3.11, PostgreSQL 15 LOCAL REAL (rol `cimitir_cp_app`, RLS), Chromium (Playwright), Storage SIMULAT local (nu LIVE) — cu excepția secțiunii A (LIVE).

## A. LIVE — Supabase TEST jqtdeuzkqtzfsdohvxmy (PostgreSQL + RLS + Storage reale)
`backend/tests/test_etapa789_live_supabase_test.py`: **16 PASS, 0 FAIL, 0 SKIP** (detalii în ETAPA789_RAPORT.md §2.1).

## B. LOCAL — suita Sync v2 / pairing / cloud admin / field inbox / PWA (14 fișiere)
`207 PASS, 1 FAIL, 0 SKIP`. Singurul FAIL: `test_etapa6c_field_inbox_pg::test_production_is_hard_blocked` — test învechit (asertă că ultimele 5 migrări sunt 015..019; există 020–024); eșuează identic pe `main`.
Include: test_sync_v2_pg, test_sync_v2_main_e2e, test_sync_v2_pwa, test_mobile_v2_pwa_pairing, test_phone_pairing_pg/_proxy, test_cloud_admin_pg, test_cloud_field_deployment, test_etapa6b_field_storage, test_etapa6c_field_inbox_pg, test_etapa789_* (20 teste noi).

## C. Desktop (MAIN, fără Windows)
Teste noi + cloud_simple + cloud_profiles: **49 PASS**. Suita desktop completă: 28 FAIL + 2 ERROR identice pe `main` și pe ramură (după instalarea `qrcode`/`PyInstaller`: 15 FAIL + 2 ERROR; cauze în raport §2.3); 477 PASS (3 teste noi).

## D. Suita backend completă (rulare cu `-n 0`)
Pe ramură: 968 PASS, 41 FAIL, 81 ERROR, 192 SKIP — din care 2 regresii reale introduse de mine (reparate, verificate) și restul identice cu `main` (lipsă dependențe/`.env`/proces Windows; vezi raport §2.3). Comparația pe aceleași fișiere: `main` 14 FAIL + 1 ERROR; ramura, după reparații, nu are eșecuri noi.

## E. NETESTATE
Windows real, cameră reală (QR + foto), Cloudflare/reverse proxy real, fișiere 25 MB pe rețea mobilă, PROD.
