-- 028_avize_complete.sql — avize/lucrări complete MAIN <-> Cloud <-> Mobile: stare, executant, data începerii, data finalizării, observații.
--
-- ADITIVĂ și idempotentă: 5 coloane TEXT nulabile pe `amenajari_avize` + drepturi pe coloane pentru rolul runtime (în stilul 022). Nu modifică 001–027,
-- nu șterge/redenumește nimic, nu face backfill (rândurile existente rămân cu NULL), nu adaugă CHECK pe `stare` (MAIN poate avea valori vechi, libere).
-- Triggerele de versiune/tombstone/RLS din 003/007/022 se aplică automat și coloanelor noi. Rollback nedistructiv: pg_rollbacks/028_avize_complete_ROLLBACK.sql.
-- Rulați exclusiv pe TEST/dev, după verificarea project ref (scripts/apply_sync_v2_migration_test.py). NU se rulează pe PROD fără aprobare separată.

ALTER TABLE amenajari_avize ADD COLUMN IF NOT EXISTS stare           TEXT;
ALTER TABLE amenajari_avize ADD COLUMN IF NOT EXISTS executant       TEXT;
ALTER TABLE amenajari_avize ADD COLUMN IF NOT EXISTS data_inceput    TEXT;
ALTER TABLE amenajari_avize ADD COLUMN IF NOT EXISTS data_finalizare TEXT;
ALTER TABLE amenajari_avize ADD COLUMN IF NOT EXISTS observatii      TEXT;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cimitir_cp_app') THEN
        GRANT INSERT (stare, executant, data_inceput, data_finalizare, observatii) ON amenajari_avize TO cimitir_cp_app;
        GRANT UPDATE (stare, executant, data_inceput, data_finalizare, observatii) ON amenajari_avize TO cimitir_cp_app;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON amenajari_avize FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON amenajari_avize FROM authenticated;
    END IF;
END $$;
