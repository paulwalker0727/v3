-- 014: planul de control al bootstrap-ului (Etapa 4, Pasul 6A) PESTE tenancy-ul canonic 008–013.
-- NU dublează organizations / cemeteries / devices / device_cemetery_bindings / device_pairing_codes: le folosește (FK compuse) și le extinde aditiv.
-- Schema-agnostic (fără `public` hardcodat): totul se creează în current_schema(); funcțiile SECURITY DEFINER își fixează search_path la current_schema().
-- Rol runtime: cimitir_cp_app (LOGIN, NOBYPASSRLS; parola se setează separat, nu în Git). Contextul RLS se setează per tranzacție/conexiune prin GUC `cp.*`
-- (cp.platform, cp.org, cp.cems, cp.principal, cp.binding_uid, cp.key_hash, cp.code_hash, cp.installation, cp.run_id), vezi ETAPA4_PAS6A_POSTGRES.md.
-- Amânat explicit (D/E): epoch_registry, scheme bootstrap_stage / epoch_archive, funcții de swap. Fundația: cemeteries.data_epoch + cp_bootstrap_runs.data_epoch.

-- ---------------------------------------------------------------------------------------------------- extensii aditive ale tabelelor canonice
ALTER TABLE cemeteries ADD COLUMN IF NOT EXISTS data_epoch integer NOT NULL DEFAULT 1;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'cemeteries_data_epoch_check' AND conrelid = 'cemeteries'::regclass) THEN
        ALTER TABLE cemeteries ADD CONSTRAINT cemeteries_data_epoch_check CHECK (data_epoch >= 1);
    END IF;
END $$;
-- cod de provizionare (emis de Owner, MAIN propune cimitirul): cemetery_uid poate fi NULL; legătura cu run-ul care l-a consumat
ALTER TABLE device_pairing_codes ALTER COLUMN cemetery_uid DROP NOT NULL;
ALTER TABLE device_pairing_codes ADD COLUMN IF NOT EXISTS used_bootstrap_id uuid;

-- ---------------------------------------------------------------------------------------------------- rol runtime
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cimitir_cp_app') THEN
        CREATE ROLE cimitir_cp_app LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB;
    END IF;
EXCEPTION WHEN duplicate_object OR unique_violation THEN NULL;   -- rol cluster-global creat concurent de alt proces
END $$;

-- ---------------------------------------------------------------------------------------------------- context RLS (GUC cp.*; lipsă ⇒ NULL ⇒ acces refuzat)
CREATE OR REPLACE FUNCTION cp_org() RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT NULLIF(current_setting('cp.org', true), '')::uuid $$;
CREATE OR REPLACE FUNCTION cp_platform() RETURNS boolean LANGUAGE sql STABLE AS $$ SELECT COALESCE(current_setting('cp.platform', true), '') = '1' $$;
CREATE OR REPLACE FUNCTION cp_principal() RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT NULLIF(current_setting('cp.principal', true), '')::uuid $$;
CREATE OR REPLACE FUNCTION cp_binding_uid() RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT NULLIF(current_setting('cp.binding_uid', true), '')::uuid $$;
CREATE OR REPLACE FUNCTION cp_key_hash() RETURNS text LANGUAGE sql STABLE AS $$ SELECT NULLIF(current_setting('cp.key_hash', true), '') $$;
CREATE OR REPLACE FUNCTION cp_code_hash() RETURNS text LANGUAGE sql STABLE AS $$ SELECT NULLIF(current_setting('cp.code_hash', true), '') $$;
CREATE OR REPLACE FUNCTION cp_installation() RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT NULLIF(current_setting('cp.installation', true), '')::uuid $$;
CREATE OR REPLACE FUNCTION cp_run_id() RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT NULLIF(current_setting('cp.run_id', true), '')::uuid $$;
CREATE OR REPLACE FUNCTION cp_org_ok(org uuid) RETURNS boolean LANGUAGE sql STABLE AS $$ SELECT cp_platform() OR (org IS NOT NULL AND org = cp_org()) $$;
-- cp.cems = '*' (toate cimitirele organizației din context) sau listă de UUID separată prin virgulă
CREATE OR REPLACE FUNCTION cp_cem_ok(cem uuid) RETURNS boolean LANGUAGE sql STABLE AS
$$ SELECT cp_platform() OR COALESCE(current_setting('cp.cems', true), '') = '*'
          OR (cem IS NOT NULL AND cem::text = ANY (string_to_array(NULLIF(current_setting('cp.cems', true), ''), ','))) $$;

-- oracole înguste, SECURITY DEFINER (search_path fixat mai jos): existența unei organizații / proprietarul unui cimitir, indiferent de context
CREATE OR REPLACE FUNCTION cp_org_exists(org uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER AS
$$ SELECT EXISTS (SELECT 1 FROM organizations WHERE uid = org) $$;
CREATE OR REPLACE FUNCTION cp_cemetery_org(cem uuid) RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER AS
$$ SELECT organization_uid FROM cemeteries WHERE uid = cem $$;
DO $$ BEGIN
    EXECUTE format('ALTER FUNCTION cp_org_exists(uuid) SET search_path = %I, pg_catalog', current_schema());
    EXECUTE format('ALTER FUNCTION cp_cemetery_org(uuid) SET search_path = %I, pg_catalog', current_schema());
END $$;
REVOKE ALL ON FUNCTION cp_org_exists(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION cp_cemetery_org(uuid) FROM PUBLIC;

-- lacăt advisory per cimitir (tranzacție): serializează operațiile critice (redeem, begin, staging open, publish)
CREATE OR REPLACE FUNCTION cp_lock_cemetery(cem uuid) RETURNS void LANGUAGE sql AS
$$ SELECT pg_advisory_xact_lock(hashtextextended('cimitir:cp:cemetery:' || cem::text, 0)) $$;
CREATE OR REPLACE FUNCTION cp_try_lock_cemetery(cem uuid) RETURNS boolean LANGUAGE sql AS
$$ SELECT pg_try_advisory_xact_lock(hashtextextended('cimitir:cp:cemetery:' || cem::text, 0)) $$;

-- organization_uid al rândurilor copil se deduce din cimitir (și rămâne legat prin FK compus)
CREATE OR REPLACE FUNCTION cp_fill_org() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.organization_uid IS NULL THEN
        SELECT c.organization_uid INTO NEW.organization_uid FROM cemeteries c WHERE c.uid = NEW.cemetery_uid;
    END IF;
    RETURN NEW;
END $$;

-- ---------------------------------------------------------------------------------------------------- roluri de bootstrap (nu există echivalent în 008–013: org_role = ORG_ADMIN/STAFF)
CREATE TABLE IF NOT EXISTS cp_platform_admins (
    identity_uid uuid PRIMARY KEY REFERENCES identities (uid)
);
CREATE TABLE IF NOT EXISTS cp_org_roles (
    identity_uid     uuid NOT NULL REFERENCES identities (uid),
    organization_uid uuid NOT NULL REFERENCES organizations (uid),
    role             text NOT NULL CHECK (role IN ('owner', 'rebuild', 'admin')),
    PRIMARY KEY (identity_uid, organization_uid, role)
);

-- ---------------------------------------------------------------------------------------------------- bootstrap runs
CREATE TABLE IF NOT EXISTS cp_bootstrap_runs (
    bootstrap_id     uuid PRIMARY KEY,
    organization_uid uuid NOT NULL,
    cemetery_uid     uuid NOT NULL,
    kind             text NOT NULL CHECK (kind IN ('A', 'B')),
    binding_uid      uuid REFERENCES device_cemetery_bindings (binding_uid),
    installation_uid uuid,
    plan_hash        text NOT NULL,
    state            text NOT NULL CHECK (state IN ('planned', 'running', 'verifying', 'completed', 'failed', 'rolled_back')),
    checkpoint       jsonb NOT NULL DEFAULT '{}'::jsonb,
    manifest_source  jsonb,
    manifest_target  jsonb,
    data_epoch       integer NOT NULL DEFAULT 1 CHECK (data_epoch >= 1),
    created_at       double precision NOT NULL,
    started_at       double precision,
    finished_at      double precision,
    error            text,
    -- fără FK pe cimitir: în Flux A run-ul începe ÎNAINTE ca cimitirul (propus de MAIN) să fie creat la redeem; organizația rămâne legată prin FK
    FOREIGN KEY (organization_uid) REFERENCES organizations (uid)
);
CREATE UNIQUE INDEX IF NOT EXISTS cp_one_active_bootstrap ON cp_bootstrap_runs (cemetery_uid) WHERE state IN ('planned', 'running', 'verifying');

CREATE TABLE IF NOT EXISTS cp_binding_issuers (
    binding_uid      uuid PRIMARY KEY REFERENCES device_cemetery_bindings (binding_uid),
    organization_uid uuid NOT NULL REFERENCES organizations (uid),
    principal_uid    uuid NOT NULL REFERENCES identities (uid)
);

-- contor de încercări greșite, pe instalare (nu global)
CREATE TABLE IF NOT EXISTS cp_failed_redeems (
    installation_uid uuid PRIMARY KEY,
    n                integer NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cp_audit_log (
    id               bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    ts               double precision,
    actor            text,
    action           text,
    organization_uid uuid,
    cemetery_uid     uuid,
    detail           text
);

-- ---------------------------------------------------------------------------------------------------- staging (separat de datele live)
CREATE TABLE IF NOT EXISTS cp_staging_sessions (
    bootstrap_id           uuid PRIMARY KEY,
    organization_uid       uuid NOT NULL,
    cemetery_uid           uuid NOT NULL,
    plan_hash              text NOT NULL,
    source_manifest_digest text NOT NULL,
    schema_version         text NOT NULL,
    data_epoch             integer NOT NULL CHECK (data_epoch >= 1),
    entity_plan            text NOT NULL,
    state                  text NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'stale', 'cleaned', 'published')),
    created_at             double precision NOT NULL,
    last_activity_at       double precision NOT NULL DEFAULT 0,
    stale_after            double precision NOT NULL DEFAULT 86400,
    FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES cemeteries (uid, organization_uid)
);
CREATE UNIQUE INDEX IF NOT EXISTS cp_one_open_staging_per_cemetery ON cp_staging_sessions (cemetery_uid) WHERE state = 'open';

CREATE TABLE IF NOT EXISTS cp_staging_batches (
    bootstrap_id     uuid NOT NULL REFERENCES cp_staging_sessions (bootstrap_id),
    cemetery_uid     uuid NOT NULL,
    entity_type      text NOT NULL,
    batch_no         integer NOT NULL,
    batch_count      integer NOT NULL,
    batch_sha256     text NOT NULL,
    row_count        integer NOT NULL,
    received_at      double precision NOT NULL,
    organization_uid uuid NOT NULL,
    PRIMARY KEY (bootstrap_id, entity_type, batch_no),
    FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES cemeteries (uid, organization_uid)
);
CREATE TABLE IF NOT EXISTS cp_staging_rows (
    bootstrap_id     uuid NOT NULL REFERENCES cp_staging_sessions (bootstrap_id),
    cemetery_uid     uuid NOT NULL,
    entity_type      text NOT NULL,
    uid              text NOT NULL,
    batch_no         integer NOT NULL,
    version          integer NOT NULL DEFAULT 1,
    content          text NOT NULL,
    organization_uid uuid NOT NULL,
    PRIMARY KEY (bootstrap_id, entity_type, uid),
    FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES cemeteries (uid, organization_uid)
);
CREATE TABLE IF NOT EXISTS cp_staging_media_blobs (
    bootstrap_id     uuid NOT NULL REFERENCES cp_staging_sessions (bootstrap_id),
    cemetery_uid     uuid NOT NULL,
    uid              text NOT NULL,
    sha256           text NOT NULL,
    size             bigint NOT NULL,
    chunk_count      integer NOT NULL,
    state            text NOT NULL,
    organization_uid uuid NOT NULL,
    PRIMARY KEY (bootstrap_id, uid),
    FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES cemeteries (uid, organization_uid)
);
CREATE TABLE IF NOT EXISTS cp_staging_media_chunks (
    bootstrap_id     uuid NOT NULL REFERENCES cp_staging_sessions (bootstrap_id),
    cemetery_uid     uuid NOT NULL,
    uid              text NOT NULL,
    n                integer NOT NULL,
    sha256           text NOT NULL,
    size             integer NOT NULL,
    data             bytea NOT NULL,
    organization_uid uuid NOT NULL,
    PRIMARY KEY (bootstrap_id, uid, n),
    FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES cemeteries (uid, organization_uid)
);

-- ---------------------------------------------------------------------------------------------------- ținta publicării Flux A/B (format pe fir generic; maparea pe tabelele de entități = Sync V2, nu 6A)
CREATE TABLE IF NOT EXISTS cp_registry_rows (
    cemetery_uid     uuid NOT NULL,
    organization_uid uuid NOT NULL,
    entity           text NOT NULL,
    uid              text NOT NULL,
    PRIMARY KEY (cemetery_uid, entity, uid),
    FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES cemeteries (uid, organization_uid)
);
CREATE TABLE IF NOT EXISTS cp_registry_row_data (
    cemetery_uid     uuid NOT NULL,
    organization_uid uuid NOT NULL,
    entity           text NOT NULL,
    uid              text NOT NULL,
    version          integer NOT NULL DEFAULT 1,
    content          text NOT NULL DEFAULT '{}',
    change_seq       bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (cemetery_uid, entity, uid),
    FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES cemeteries (uid, organization_uid)
);
CREATE TABLE IF NOT EXISTS cp_media_objects (
    cemetery_uid     uuid NOT NULL,
    uid              text NOT NULL,
    kind             text NOT NULL DEFAULT 'document',
    entity_type      text,
    entity_id        text,
    name             text NOT NULL,
    size             bigint NOT NULL CHECK (size >= 0),
    sha256           text NOT NULL,
    present          integer NOT NULL DEFAULT 1 CHECK (present IN (0, 1)),
    organization_uid uuid NOT NULL,
    PRIMARY KEY (cemetery_uid, uid),
    FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES cemeteries (uid, organization_uid)
);
CREATE TABLE IF NOT EXISTS cp_media_blobs (
    cemetery_uid     uuid NOT NULL,
    sha256           text NOT NULL,
    size             bigint NOT NULL,
    data             bytea NOT NULL,
    organization_uid uuid NOT NULL,
    PRIMARY KEY (cemetery_uid, sha256),
    FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES cemeteries (uid, organization_uid)
);
CREATE TABLE IF NOT EXISTS cp_media_published (
    cemetery_uid     uuid NOT NULL,
    uid              text NOT NULL,
    sha256           text NOT NULL,
    size             bigint NOT NULL,
    published_at     double precision NOT NULL,
    organization_uid uuid NOT NULL,
    PRIMARY KEY (cemetery_uid, uid),
    FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES cemeteries (uid, organization_uid)
);
CREATE TABLE IF NOT EXISTS cp_open_conflicts (
    cemetery_uid     uuid NOT NULL,
    uid              text NOT NULL,
    organization_uid uuid NOT NULL,
    PRIMARY KEY (cemetery_uid, uid),
    FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES cemeteries (uid, organization_uid)
);
CREATE TABLE IF NOT EXISTS cp_tombstones (
    tombstone_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cemetery_uid     uuid NOT NULL,
    organization_uid uuid NOT NULL,
    entity           text NOT NULL,
    uid              text NOT NULL,
    FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES cemeteries (uid, organization_uid)
);

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['cp_staging_batches','cp_staging_rows','cp_staging_media_blobs','cp_staging_media_chunks','cp_registry_rows','cp_registry_row_data',
                             'cp_media_objects','cp_media_blobs','cp_media_published','cp_open_conflicts','cp_tombstones']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS cp_fill_org ON %I', t);
        EXECUTE format('CREATE TRIGGER cp_fill_org BEFORE INSERT ON %I FOR EACH ROW EXECUTE FUNCTION cp_fill_org()', t);
    END LOOP;
END $$;

-- ---------------------------------------------------------------------------------------------------- RLS
DO $$
DECLARE t TEXT;
BEGIN
    -- tabele de date cu organizație + cimitir: contextul trebuie să acopere ambele
    FOREACH t IN ARRAY ARRAY['cp_bootstrap_runs','cp_staging_sessions','cp_staging_batches','cp_staging_rows','cp_staging_media_blobs','cp_staging_media_chunks',
                             'cp_registry_rows','cp_registry_row_data','cp_media_objects','cp_media_blobs','cp_media_published','cp_open_conflicts','cp_tombstones']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS cp_tenant ON %I', t);
        EXECUTE format('CREATE POLICY cp_tenant ON %I TO cimitir_cp_app USING (cp_org_ok(organization_uid) AND cp_cem_ok(cemetery_uid)) '
                       'WITH CHECK (cp_org_ok(organization_uid) AND cp_cem_ok(cemetery_uid))', t);
    END LOOP;
END $$;

-- run-ul se poate citi/închide și pe baza capabilității bootstrap_id (UUID aleator) + installation_uid, fără cheie (rollback fără cheie)
DROP POLICY IF EXISTS cp_run_capability_select ON cp_bootstrap_runs;
CREATE POLICY cp_run_capability_select ON cp_bootstrap_runs FOR SELECT TO cimitir_cp_app USING (bootstrap_id = cp_run_id());
DROP POLICY IF EXISTS cp_run_capability_update ON cp_bootstrap_runs;
CREATE POLICY cp_run_capability_update ON cp_bootstrap_runs FOR UPDATE TO cimitir_cp_app
    USING (bootstrap_id = cp_run_id() AND installation_uid = cp_installation()) WITH CHECK (bootstrap_id = cp_run_id() AND installation_uid = cp_installation());

ALTER TABLE cp_platform_admins ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS cp_tenant ON cp_platform_admins;
CREATE POLICY cp_tenant ON cp_platform_admins FOR SELECT TO cimitir_cp_app USING (identity_uid = cp_principal() OR cp_platform());

ALTER TABLE cp_org_roles ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS cp_tenant ON cp_org_roles;
CREATE POLICY cp_tenant ON cp_org_roles TO cimitir_cp_app USING (identity_uid = cp_principal() OR cp_org_ok(organization_uid)) WITH CHECK (cp_org_ok(organization_uid));

ALTER TABLE cp_binding_issuers ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS cp_tenant ON cp_binding_issuers;
CREATE POLICY cp_tenant ON cp_binding_issuers TO cimitir_cp_app USING (cp_org_ok(organization_uid)
    OR binding_uid = cp_binding_uid()) WITH CHECK (cp_org_ok(organization_uid));

ALTER TABLE cp_failed_redeems ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS cp_tenant ON cp_failed_redeems;
CREATE POLICY cp_tenant ON cp_failed_redeems TO cimitir_cp_app USING (installation_uid = cp_installation()) WITH CHECK (installation_uid = cp_installation());

ALTER TABLE cp_audit_log ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS cp_tenant_select ON cp_audit_log;
CREATE POLICY cp_tenant_select ON cp_audit_log FOR SELECT TO cimitir_cp_app USING (cp_org_ok(organization_uid));
DROP POLICY IF EXISTS cp_tenant_insert ON cp_audit_log;
CREATE POLICY cp_tenant_insert ON cp_audit_log FOR INSERT TO cimitir_cp_app WITH CHECK (organization_uid IS NULL OR cp_org_ok(organization_uid) OR organization_uid = cp_cemetery_org(cemetery_uid));

-- politici ADITIVE pe tabelele canonice (010 rămâne neschimbat; politicile permisive se combină prin OR, deci cimitir_app nu e afectat)
DROP POLICY IF EXISTS cp_organizations_select ON organizations;
CREATE POLICY cp_organizations_select ON organizations FOR SELECT TO cimitir_cp_app USING (cp_org_ok(uid));
DROP POLICY IF EXISTS cp_organizations_insert ON organizations;
CREATE POLICY cp_organizations_insert ON organizations FOR INSERT TO cimitir_cp_app WITH CHECK (cp_platform());

DROP POLICY IF EXISTS cp_cemeteries_all ON cemeteries;
CREATE POLICY cp_cemeteries_all ON cemeteries TO cimitir_cp_app USING (cp_org_ok(organization_uid) AND cp_cem_ok(uid)) WITH CHECK (cp_org_ok(organization_uid));

DROP POLICY IF EXISTS cp_devices_all ON devices;
CREATE POLICY cp_devices_all ON devices TO cimitir_cp_app USING (cp_org_ok(organization_uid)) WITH CHECK (cp_org_ok(organization_uid));

DROP POLICY IF EXISTS cp_bindings_all ON device_cemetery_bindings;
CREATE POLICY cp_bindings_all ON device_cemetery_bindings TO cimitir_cp_app
    USING ((cp_org_ok(organization_uid) AND cp_cem_ok(cemetery_uid)) OR (binding_uid = cp_binding_uid() AND sync_key_hash = cp_key_hash()))
    WITH CHECK (cp_org_ok(organization_uid) AND cp_cem_ok(cemetery_uid));

DROP POLICY IF EXISTS cp_pairing_all ON device_pairing_codes;
CREATE POLICY cp_pairing_all ON device_pairing_codes TO cimitir_cp_app
    USING ((cp_org_ok(organization_uid) AND (cemetery_uid IS NULL OR cp_cem_ok(cemetery_uid))) OR code_hash = cp_code_hash())
    WITH CHECK (cp_org_ok(organization_uid) AND (cemetery_uid IS NULL OR cp_cem_ok(cemetery_uid)));

-- ---------------------------------------------------------------------------------------------------- grants minime (fără drepturi pe secvențele/tabelele 001–013 în afara celor de mai jos)
DO $$
DECLARE t TEXT;
BEGIN
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO cimitir_cp_app', current_schema());
    FOREACH t IN ARRAY ARRAY['cp_bootstrap_runs','cp_staging_sessions','cp_staging_batches','cp_staging_rows','cp_staging_media_blobs','cp_staging_media_chunks',
                             'cp_registry_rows','cp_registry_row_data','cp_media_objects','cp_media_blobs','cp_media_published','cp_open_conflicts','cp_tombstones',
                             'cp_org_roles','cp_binding_issuers','cp_failed_redeems']
    LOOP
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO cimitir_cp_app', t);
    END LOOP;
    GRANT SELECT ON cp_platform_admins TO cimitir_cp_app;
    GRANT SELECT, INSERT ON cp_audit_log TO cimitir_cp_app;
    GRANT USAGE ON SEQUENCE cp_audit_log_id_seq TO cimitir_cp_app;
    GRANT USAGE ON SEQUENCE cp_tombstones_tombstone_id_seq TO cimitir_cp_app;
    GRANT SELECT, INSERT ON organizations TO cimitir_cp_app;
    GRANT SELECT, INSERT, UPDATE ON cemeteries TO cimitir_cp_app;
    GRANT SELECT, INSERT ON devices TO cimitir_cp_app;
    GRANT SELECT, INSERT, UPDATE ON device_cemetery_bindings TO cimitir_cp_app;
    GRANT SELECT, INSERT, UPDATE ON device_pairing_codes TO cimitir_cp_app;
    GRANT EXECUTE ON FUNCTION cp_org(), cp_platform(), cp_principal(), cp_binding_uid(), cp_key_hash(), cp_code_hash(), cp_installation(), cp_run_id(),
        cp_org_ok(uuid), cp_cem_ok(uuid), cp_org_exists(uuid), cp_cemetery_org(uuid), cp_lock_cemetery(uuid), cp_try_lock_cemetery(uuid), cp_fill_org() TO cimitir_cp_app;
END $$;
