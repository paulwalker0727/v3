-- 010: Row Level Security ca strat suplimentar, peste filtrele explicite din aplicație.
-- Contextul (app.org_uid / app.cemetery_uids) se setează de aplicație pe conexiune; valorile implicite ale coloanelor de tenant îl folosesc la INSERT.

CREATE OR REPLACE FUNCTION app_org_uid() RETURNS uuid LANGUAGE sql STABLE AS
$$ SELECT NULLIF(current_setting('app.org_uid', true), '')::uuid $$;

CREATE OR REPLACE FUNCTION app_cemetery_uids() RETURNS uuid[] LANGUAGE sql STABLE AS
$$ SELECT CASE WHEN COALESCE(current_setting('app.cemetery_uids', true), '') = '' THEN ARRAY[]::uuid[]
               ELSE string_to_array(current_setting('app.cemetery_uids', true), ',')::uuid[] END $$;

-- valoare implicită pentru INSERT-urile existente: cimitirul activ, doar dacă contextul are exact unul
CREATE OR REPLACE FUNCTION app_default_cemetery_uid() RETURNS uuid LANGUAGE sql STABLE AS
$$ SELECT CASE WHEN cardinality(app_cemetery_uids()) = 1 THEN (app_cemetery_uids())[1] END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cimitir_app') THEN
        CREATE ROLE cimitir_app LOGIN NOBYPASSRLS NOSUPERUSER NOCREATEROLE NOCREATEDB;   -- parola se setează separat, nu în Git
    END IF;
END $$;

DO $$
BEGIN
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO cimitir_app', current_schema());
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO cimitir_app', current_schema());
    EXECUTE format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA %I TO cimitir_app', current_schema());
    EXECUTE format('GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA %I TO cimitir_app', current_schema());
END $$;

DO $$
DECLARE t TEXT;
BEGIN
    -- registru + sincronizare: organizația ȘI cimitirul trebuie să corespundă contextului
    FOREACH t IN ARRAY ARRAY[
        'locuri_de_veci','concesionari','contracte','inhumati','plati','amenajari_avize','sectoare','photos','documents',
        'media_objects','loc_extra','guides','rows_ghid','loc_holders','qr_generations','ai_scan_history',
        'cereri_concesionare','mobile_upload_tokens','sync_ops','sync_tombstones','sync_conflicts']
    LOOP
        IF to_regclass(t) IS NULL THEN CONTINUE; END IF;
        EXECUTE format('ALTER TABLE %I ALTER COLUMN cemetery_uid SET DEFAULT app_default_cemetery_uid()', t);
        EXECUTE format('ALTER TABLE %I ALTER COLUMN organization_uid SET DEFAULT app_org_uid()', t);
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
        EXECUTE format($p$CREATE POLICY tenant_isolation ON %I
            USING (organization_uid = app_org_uid() AND cemetery_uid = ANY (app_cemetery_uids()))
            WITH CHECK (organization_uid = app_org_uid() AND cemetery_uid = ANY (app_cemetery_uids()))$p$, t);
    END LOOP;

    -- audit / e-mail: cimitirul poate lipsi (acțiune la nivel de organizație)
    FOREACH t IN ARRAY ARRAY['audit_log','email_outbox']
    LOOP
        IF to_regclass(t) IS NULL THEN CONTINUE; END IF;
        EXECUTE format('ALTER TABLE %I ALTER COLUMN organization_uid SET DEFAULT app_org_uid()', t);
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
        EXECUTE format($p$CREATE POLICY tenant_isolation ON %I
            USING (organization_uid = app_org_uid() AND (cemetery_uid IS NULL OR cemetery_uid = ANY (app_cemetery_uids())))
            WITH CHECK (organization_uid = app_org_uid() AND (cemetery_uid IS NULL OR cemetery_uid = ANY (app_cemetery_uids())))$p$, t);
    END LOOP;

    -- nivel organizație: cetățeni
    FOREACH t IN ARRAY ARRAY['citizens','citizen_resets']
    LOOP
        IF to_regclass(t) IS NULL THEN CONTINUE; END IF;
        EXECUTE format('ALTER TABLE %I ALTER COLUMN organization_uid SET DEFAULT app_org_uid()', t);
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
        EXECUTE format($p$CREATE POLICY tenant_isolation ON %I
            USING (organization_uid = app_org_uid()) WITH CHECK (organization_uid = app_org_uid())$p$, t);
    END LOOP;

    -- identitate/personal/dispozitive: scoped pe organizație
    FOREACH t IN ARRAY ARRAY['organization_members','cemetery_permissions','devices','device_cemetery_bindings','device_pairing_codes']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
        EXECUTE format($p$CREATE POLICY tenant_isolation ON %I
            USING (organization_uid = app_org_uid()) WITH CHECK (organization_uid = app_org_uid())$p$, t);
    END LOOP;

    -- cemeteries / organizations: doar rândurile din context
    ALTER TABLE cemeteries ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS tenant_isolation ON cemeteries;
    CREATE POLICY tenant_isolation ON cemeteries USING (organization_uid = app_org_uid() AND uid = ANY (app_cemetery_uids()))
        WITH CHECK (organization_uid = app_org_uid());
    ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS tenant_isolation ON organizations;
    CREATE POLICY tenant_isolation ON organizations USING (uid = app_org_uid()) WITH CHECK (uid = app_org_uid());
END $$;
-- `identities` NU are RLS aici: autentificarea (căutare după username în organization_members, apoi identitate) rulează cu un rol
-- de autentificare separat, cu drepturi minime. De proiectat în pasul următor.
