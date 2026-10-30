-- Rollback 027: restaurează politica de DELETE din 022 (doar MAIN). NU șterge tabelele de audit/istoric (păstrează datele); le puteți elimina manual după arhivare.
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['locuri_de_veci','concesionari','contracte','inhumati','plati','amenajari_avize']
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS sync_v2_delete ON %I', t);
        EXECUTE format('CREATE POLICY sync_v2_delete ON %I FOR DELETE TO cimitir_cp_app USING (organization_uid = cp_org() AND cp_cem_ok(cemetery_uid) AND cp_device_is_main())', t);
    END LOOP;
END $$;
DROP FUNCTION IF EXISTS cp_mobile_delete_ok();
UPDATE sync_v2_permissions SET can_edit=false WHERE entity = 'mode:matrix';   -- telefoanele revin la capabilitățile/registrele din 022
