-- 022_sync_v2_ROLLBACK.sql — restaurează EXACT starea de dinainte de 022 (politici RLS, drepturi pe tabele/coloane/secvențe, funcțiile trigger).
-- Sursa: sync_v2_migration_backup (batch 'pre-022', salvat de 022 înainte de orice modificare). NU șterge date din registre și NU șterge tabelele de jurnal
-- sync_v2_* (ele rămân, fără drepturi pentru cimitir_cp_app). Rulează EXCLUSIV pe TEST, ca administrator, într-o singură tranzacție:
-- dacă amprenta de după rollback diferă de cea salvată înainte de 022, scriptul eșuează și NIMIC nu se modifică.
-- Opțional, după verificare, jurnalul se poate șterge manual: DROP TABLE sync_v2_runs, sync_v2_conflicts, sync_v2_ops, sync_v2_permissions, sync_v2_migration_backup;
BEGIN;
DO $$
DECLARE t text; rec record; before_fp text; after_fp text;
        scope text[] := ARRAY['locuri_de_veci','concesionari','contracte','inhumati','plati','amenajari_avize','sectoare','sync_tombstones'];
BEGIN
    SELECT restore_sql INTO before_fp FROM sync_v2_migration_backup WHERE batch = 'pre-022' AND kind = 'fingerprint';
    IF before_fp IS NULL THEN RAISE EXCEPTION 'Nu există backup pre-022: rollback imposibil (022 nu a fost aplicată prin această migrare).'; END IF;

    FOREACH t IN ARRAY scope LOOP
        IF to_regclass(t) IS NULL THEN CONTINUE; END IF;
        EXECUTE format('DROP POLICY IF EXISTS mobile_cp_read ON %I', t);
        EXECUTE format('DROP POLICY IF EXISTS mobile_cp_test_insert ON %I', t);
        EXECUTE format('DROP POLICY IF EXISTS sync_v2_insert ON %I', t);
        EXECUTE format('DROP POLICY IF EXISTS sync_v2_update ON %I', t);
        EXECUTE format('DROP POLICY IF EXISTS sync_v2_delete ON %I', t);
        EXECUTE format('DROP POLICY IF EXISTS sync_v2_tomb_read ON %I', t);
        EXECUTE format('REVOKE ALL ON %I FROM cimitir_cp_app', t);
    END LOOP;
    FOR t IN SELECT c.relname FROM pg_class c WHERE c.relnamespace = current_schema()::regnamespace AND c.relkind = 'S'
                AND (c.relname = 'sync_change_seq' OR c.relname IN ('locuri_de_veci_id_seq','concesionari_id_seq','contracte_id_seq','inhumati_id_seq','plati_id_seq','amenajari_avize_id_seq')) LOOP
        EXECUTE format('REVOKE ALL ON SEQUENCE %I FROM cimitir_cp_app', t);
    END LOOP;

    FOR rec IN SELECT restore_sql FROM sync_v2_migration_backup WHERE batch = 'pre-022' AND kind <> 'fingerprint' ORDER BY ord, id LOOP
        EXECUTE rec.restore_sql;
    END LOOP;

    FOREACH t IN ARRAY ARRAY['sync_v2_ops','sync_v2_conflicts','sync_v2_permissions','sync_v2_runs'] LOOP
        IF to_regclass(t) IS NOT NULL THEN EXECUTE format('REVOKE ALL ON %I FROM cimitir_cp_app', t); END IF;
    END LOOP;
    REVOKE ALL ON SEQUENCE sync_v2_conflicts_id_seq, sync_v2_runs_id_seq FROM cimitir_cp_app;

    after_fp := cimitir_sync_v2_fingerprint();
    IF after_fp IS DISTINCT FROM before_fp THEN
        RAISE EXCEPTION 'Rollback NEVERIFICAT: amprenta diferă (înainte=%, după=%). Tranzacția se anulează.', before_fp, after_fp;
    END IF;
    RAISE NOTICE 'Rollback 022 verificat: amprenta = % (identică cu starea de dinainte).', after_fp;
END $$;
DROP FUNCTION IF EXISTS cp_device_is_main();
COMMIT;
