-- 022_sync_v2_ROLLBACK.sql — anulează ACCESUL de scriere dat de 022 (NU șterge date din registre, NU șterge tabelele de jurnal).
-- Rulează EXCLUSIV pe TEST, ca administrator. După rollback rulați din nou 021_mobile_v2_test_polygon_import.sql (idempotent) dacă vreți importul de poligon vechi.
-- Tabelele sync_v2_* rămân (conțin doar jurnal); pentru ștergerea lor, decomentați blocul de la final DUPĂ ce ați făcut backup.
BEGIN;
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['locuri_de_veci','concesionari','contracte','inhumati','plati','amenajari_avize']
    LOOP
        IF to_regclass(t) IS NULL THEN CONTINUE; END IF;
        EXECUTE format('DROP POLICY IF EXISTS sync_v2_insert ON %I', t);
        EXECUTE format('DROP POLICY IF EXISTS sync_v2_update ON %I', t);
        EXECUTE format('DROP POLICY IF EXISTS sync_v2_delete ON %I', t);
        EXECUTE format('REVOKE INSERT, UPDATE, DELETE ON %I FROM cimitir_cp_app', t);
    END LOOP;
END $$;
REVOKE INSERT, UPDATE ON sync_tombstones FROM cimitir_cp_app;
REVOKE USAGE ON SEQUENCE sync_change_seq FROM cimitir_cp_app;
COMMIT;

-- Opțional (distruge DOAR jurnalul sync v2; faceți backup înainte):
-- DROP TABLE IF EXISTS sync_v2_runs, sync_v2_conflicts, sync_v2_ops, sync_v2_permissions;
