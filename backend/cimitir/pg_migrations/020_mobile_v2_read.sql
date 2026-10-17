-- 020_mobile_v2_read.sql — Cimitir Mobile v2, etapa 1: acces READ-ONLY la registrele live.
--
-- Telefonul nu primește niciodată parola PostgreSQL. FastAPI autentifică `Binding <cheie>`, setează contextul cp.*
-- pe conexiunea rolului runtime cimitir_cp_app, apoi RLS limitează citirea la organizația/cimitirul din binding.
-- Nicio permisiune INSERT/UPDATE/DELETE pe registrele business în această etapă.

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['locuri_de_veci','concesionari','contracte','inhumati','plati','amenajari_avize','sectoare']
    LOOP
        IF to_regclass(t) IS NULL THEN CONTINUE; END IF;
        EXECUTE format('GRANT SELECT ON %I TO cimitir_cp_app', t);
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS mobile_cp_read ON %I', t);
        EXECUTE format($p$CREATE POLICY mobile_cp_read ON %I FOR SELECT TO cimitir_cp_app
            USING (organization_uid = cp_org() AND cp_cem_ok(cemetery_uid))$p$, t);
    END LOOP;
END $$;
