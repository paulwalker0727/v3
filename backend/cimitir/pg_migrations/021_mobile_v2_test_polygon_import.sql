-- Pilot MAIN -> Mobile v2: numai INSERT pe locuri, protejat prin RLS.
-- Executați EXCLUSIV pe proiectul PostgreSQL TEST, după verificarea project ref.
GRANT INSERT (uid,cod_loc,points,sector,parcela,rand,numar,organization_uid,cemetery_uid)
    ON locuri_de_veci TO cimitir_cp_app;
GRANT USAGE ON SEQUENCE locuri_de_veci_id_seq TO cimitir_cp_app;
DROP POLICY IF EXISTS mobile_cp_test_insert ON locuri_de_veci;
CREATE POLICY mobile_cp_test_insert ON locuri_de_veci FOR INSERT TO cimitir_cp_app
    WITH CHECK (organization_uid = cp_org() AND cp_cem_ok(cemetery_uid) AND jsonb_array_length(points) >= 3);
