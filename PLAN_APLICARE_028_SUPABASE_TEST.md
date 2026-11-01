# Plan — aplicarea migrării 028 (avize complete) pe Supabase TEST

**Stare: NEEXECUTAT.** Acest document este doar un plan. Migrarea 028 nu a fost rulată pe Supabase TEST, pe Supabase PROD sau pe vreun server existent. A fost verificată doar pe PostgreSQL local nou (vezi raportul de teste).

## Ce face 028
- Aditivă și idempotentă: adaugă 5 coloane `TEXT` nulabile la `amenajari_avize` (`stare`, `executant`, `data_inceput`, `data_finalizare`, `observatii`).
- Acordă `INSERT`/`UPDATE` pe aceste coloane rolului runtime `cimitir_cp_app`.
- Nu șterge, nu redenumește, nu face backfill, nu adaugă CHECK; rândurile existente rămân cu NULL.
- Rollback nedistructiv (`pg_rollbacks/028_avize_complete_ROLLBACK.sql`): revocă doar drepturile de scriere pe cele 5 coloane. Coloanele și valorile rămân.

## Ordinea obligatorie (cod înainte de bază sau invers?)
Codul nou funcționează în ambele ordini, fără pierderi:
| Situație | Comportament |
|---|---|
| Cod nou + bază fără 028 | `status.features.avize_extended=false`. Avizele fără câmpuri noi merg normal. O valoare nevidă în câmpurile noi este **refuzată explicit** (`schema_incompatible`) și rămâne în coada MAIN/telefon. Nu se pierde nimic. Mobile arată avertizare și dezactivează câmpurile. |
| Cod vechi + bază cu 028 | Funcționează: coloanele sunt ignorate de codul vechi. Un MAIN/telefon vechi nu golește valorile existente (cheia lipsă = nemodificat). |
| Cod nou + bază cu 028 | Complet. |

**Recomandat:** 1) actualizați backend-ul TEST cu codul nou; 2) aplicați 028; 3) actualizați MAIN pe Windows; 4) reîncărcați PWA pe telefon (cache nou `cimitir-mobile-shell-v9`).

## Pași (pe TEST, doar după aprobarea ta explicită)
1. **Confirmare mediu:** project ref TEST = `fommbiwndlxkfsnlqatq`; bucket `cimitir-private-test`. Se oprește dacă ref-ul nu corespunde.
2. **Backup:** copie `pg_dump` a tabelei `amenajari_avize` și a schemei (sau backup din consola Supabase) înainte de orice. Fără backup verificat, nu se continuă.
3. **Verificări prealabile (doar citire):**
   - `SELECT version FROM cimitir_schema_migrations ORDER BY version DESC LIMIT 1;` trebuie să fie 27 (sau 27 + migrările locale deja aplicate).
   - `SELECT column_name FROM information_schema.columns WHERE table_name='amenajari_avize';` nu conține deja cele 5 coloane (dacă există, 028 rămâne sigură).
   - `SELECT count(*) FROM amenajari_avize;` se notează numărul de rânduri.
4. **Aplicare (runner existent, cu confirmare):** `python scripts/apply_sync_v2_migration_test.py` extins/rulat în modul dry-run, apoi execute, sau runner-ul `tools/setup_manager` (planul arată pasul 28 ca singurul în așteptare). Se rulează o singură dată.
5. **Verificări ulterioare (doar citire):**
   - cele 5 coloane există, tip `text`;
   - `SELECT count(*) FROM amenajari_avize;` = numărul notat la pasul 3;
   - `has_column_privilege('cimitir_cp_app','amenajari_avize','stare','UPDATE')` = true;
   - `GET /api/v1/sync-v2/status` → `features.avize_extended = true`.
6. **Test funcțional pe TEST (manual):** un aviz creat în MAIN cu toate câmpurile apare pe telefon; o modificare pe telefon ajunge în MAIN.
7. **Rollback, dacă e nevoie:** rulați `028_avize_complete_ROLLBACK.sql`. Valorile rămân; serverul trece automat pe modul compatibil (refuz explicit al valorilor noi, fără pierderi).

## Ce NU se face
- Nu se rulează pe PROD.
- Nu se modifică datele existente.
- Nu se aplică 028 din codul aplicației (doar prin runner, cu confirmare).
