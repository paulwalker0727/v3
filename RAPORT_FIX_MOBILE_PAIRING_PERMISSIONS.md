# Raport — fix pairing Mobile și permisiuni (bază: `main` @ 1a63c5c, ramura `fix/mobile-pairing-permissions`)

Mediu de testare: sandbox Linux, PostgreSQL 15 local (rol aplicație cu RLS, migrările repo-ului), HTTP real (uvicorn), MAIN real în proces separat (SQLite + `desktop/sync_v2.py`), UI PySide6 `offscreen`,
PWA în Chromium (Playwright). Date fictive. **Nu s-a folosit Supabase, PROD, chei reale sau containerele 8086/8087; nu s-a testat pe Windows.**

## Cauze (ce s-a găsit, ce s-a schimbat)

| # | Defect | Cauză | Fix |
|---|---|---|---|
| A1 | Codul Mobile v2 nu se putea genera „direct din MAIN” | Singura cale Mobile v2 din MAIN era fila a doua din «Asociază» și cerea email + parolă de administrator Cloud (nu legătura MAIN deja asociată). Fereastra Field Inbox v1 trimitea utilizatorul spre ea. | Fila nouă **Asociere telefon** (`mobile_pairing_panel.py`) folosește legătura MAIN (BOUND) și **același** mecanism de pairing (`/api/v1/phone-pairing/codes`, `/redeem`). Fără browser, fără parolă. Nu există sistem nou. |
| A2 | „Buton care nu face nimic” | În fila cu administrator, `_issue` ieșea tăcut fără autentificare/organizație/cimitir; erorile apăreau într-o etichetă sub tabel; cimitirul implicit era primul din listă, nu cel al registrului local. | Mesaje explicite pe fiecare ramură, stare „Se generează…”, eroare vizibilă în română, preselectarea cimitirului local. |
| A3 | Expirare afișată greșit pe PC cu ceas diferit | Contorul compara `expires_at` (ceasul serverului) cu `time.time()` local. | Contor local calculat din `ttl` primit. |
| A4 | „Reînnoiește” putea eșua cu „prea multe coduri” | Limita de 10 coduri active număra și codurile revocate până la expirare. | Numărătoarea exclude codurile revocate (test `test_10`). |
| A5 | Nu existau listare/revocare de coduri din MAIN | Revocarea exista doar în API-ul de administrator. | `GET /phone-pairing/codes` (fără cod brut) și `POST /phone-pairing/codes/revoke`, cu cheie MAIN, strict pe cimitirul propriu. |
| B1 | Telefonul rămâne „Doar citire” | (1) Un telefon nou este corect doar-citire (`mode:legacy`, păstrat). (2) MAIN era îndreptat spre un backend vechi (8086) fără rutele de matrice/instanță: 404 mascat drept „Elementul nu există”, deci permisiunile nu se puteau salva/vedea. (3) PWA: insigna „Doar citire” nu se reîmprospăta după sincronizare, deși butoanele se actualizau. | (2) `404/405` fără corp API => mesaj „backend vechi, actualizează”; verificare de instanță înainte de orice operație. (3) `renderPermPill` apelat din `loadSyncInfo`; reîncărcarea permisiunilor la revenirea în prim-plan; SW `shell-v7`. Modul „Doar citire” **nu** a fost eliminat. |
| C1 | Două backenduri pe aceeași bază | Același Postgres, deci permisiunile salvate prin unul sunt vizibile imediat prin celălalt (testat). Problema reală este diferența de versiune, nu baza. | `GET /api/v1/sync-v2/instance` (release, capabilități, amprenta bazei), `scripts/verify_backend_instance.py`, `deploy/windows/update_test_backend.ps1` (canary, verificare, switch cu `-Confirm`, rollback), `ACTUALIZARE_BACKEND_TEST.md`. |

**Limită:** nu am avut acces la mediul tău Windows/8086/8087, deci nu pot confirma care dintre cauzele B1(2)/(3) a produs exact simptomul tău; ambele sunt reproduse și remediate în sandbox. Aplicarea ghidului de actualizare și verificarea `instance` pe 8086 vor confirma.

## Teste obligatorii (executate)

| # | Cerință | Test | Rezultat |
|---|---|---|---|
| 1 | MAIN generează cod Mobile v2 (UI PySide6 real, proces MAIN) | `test_01` | PASS |
| 2 | Telefonul se asociază cu codul | `test_01` | PASS |
| 3 | Telefon nou: drepturi limitate, serverul respinge scrierea (`not_authorized`) | `test_01`, `test_03` | PASS |
| 4 | MAIN acordă Vede+Adaugă+Editează la Locuri de veci (panou real) | `test_03` | PASS |
| 5 | Mobile afișează butoanele (PWA în Chromium) | `test_07` | PASS |
| 6-7 | Mobile modifică un loc fictiv; Cloud acceptă (PWA + API) | `test_07`, `test_03` | PASS |
| 8 | MAIN primește modificarea prin Sync v2 | `test_03`, `test_07` | PASS |
| 9-10 | MAIN revocă „Editează”; butonul dispare; operația offline trimisă după revocare e respinsă (și la retrimitere) | `test_03`, `test_07` | PASS |
| 11 | Telefon din altă parohie: fără citire/scriere, fără configurare din MAIN-ul străin; RLS direct în PostgreSQL | `test_04` | PASS |
| 12 | Cod expirat / revocat / refolosit respins; cod din alt cimitir nu poate fi revocat; telefonul nu emite coduri | `test_02` | PASS |

## Teste suplimentare
- `test_05` identitate instanță, două backenduri pe aceeași bază (permisiuni propagate), backend vechi și cheie greșită => mesaje clare: PASS
- `test_06` calea cu administrator Cloud rămâne compatibilă; telefonul nou e doar-citire: PASS
- `test_08` UI «Asociază» (administrator): mesaje la lipsă autentificare/parolă greșită, cod valid: PASS
- `test_09` script de verificare instanță (aceeași bază, backend vechi, cheie respinsă, secretul nu se afișează): PASS
- `test_10` codurile revocate nu mai ocupă limita: PASS
- `test_mobile_pairing_panel_unit.py` (4 teste: contor/expirare, erori în română, reînnoire/revocare, rezumat drepturi): PASS
- Rezultat: **14 PASS / 0 FAIL** în fișierele noi (`test_mobile_pairing_permissions_e2e.py`: 10, `test_mobile_pairing_panel_unit.py`: 4).

## Regresii
Suita completă a depozitului (`backend/tests`, 1.3k teste, `-n 0`, PostgreSQL local): 1113 PASS, 200 SKIP, 17 FAIL, 85 ERROR. Fișierele cu eșecuri au fost rulate izolat pe codul de bază (`git stash`) și pe codul patch-uit:
ambele dau **6 failed + 3 errors** (probleme de mediu sandbox: `.env`, porturi, `test_sync_v2_migration` care cere instalare PostgreSQL proprie). `test_route_inventory_uses_only_clear_prefixes` eșuează și pe baza nemodificată. `test_mobile_complet_pwa` a trecut izolat (6/6) după un eșec în rularea completă (dependent de ordine).
Seturile exacte de teste eșuate nu sunt identice între cele două rulări izolate (am comparat numărul și am verificat individual testul care diferea) — de revalidat pe Windows.

## SKIP (neexecutat — nu raportat ca PASS)
- UI PySide6 pe Windows real, scanarea QR cu telefon real, PWA pe telefon real.
- `update_test_backend.ps1` (PowerShell/Docker) — scris, neexecutat aici; `verify_backend_instance.py` este testat.
- Supabase TEST real (`bphvhpawdjhwtfdhbbnq`) și containerele 8086/8087 — neatinse.
- Test live Supabase (`test_etapa789_live_supabase_test`) — SKIP/ERROR fără chei.
