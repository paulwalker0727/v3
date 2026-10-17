-- 023: autentificare administrativă Cloud (Owner/Admin) + inițializare unică a primului administrator + revocarea codurilor de pairing.
-- Aditivă și idempotentă. NU atinge datele existente. Rol runtime: cimitir_cp_app (NOBYPASSRLS) — drepturi minime, fără acces direct la cp_admin_state.
-- Parolele se stochează DOAR ca hash (scrypt, calculat în aplicație); sesiunile DOAR ca sha256 al tokenului opac; tokenurile de recuperare DOAR ca sha256.
-- Rollback: pg_rollbacks/023_cloud_admin_auth_ROLLBACK.sql

-- ---------------------------------------------------------------------------------------------------- revocarea codurilor (MAIN și MOBILE) înainte de folosire
ALTER TABLE device_pairing_codes ADD COLUMN IF NOT EXISTS revoked_at timestamptz;
ALTER TABLE device_pairing_codes ADD COLUMN IF NOT EXISTS revoked_by text;

CREATE OR REPLACE FUNCTION cp_mobile_pairing_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.device_type <> 'MOBILE' THEN RETURN NEW; END IF;
        IF NEW.cemetery_uid IS NULL OR NEW.used_at IS NOT NULL OR NEW.used_by_binding_uid IS NOT NULL OR NEW.used_bootstrap_id IS NOT NULL OR NEW.revoked_at IS NOT NULL THEN
            RAISE EXCEPTION 'device_pairing_codes: cod MOBILE invalid la creare' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.code_hash !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'device_pairing_codes: se stochează doar hash sha256' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.expires_at <= now() OR NEW.expires_at > now() + interval '30 minutes' THEN
            RAISE EXCEPTION 'device_pairing_codes: TTL cod MOBILE între 0 și 30 de minute' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.device_type <> 'MOBILE' AND NEW.device_type <> 'MOBILE' THEN RETURN NEW; END IF;
    IF (NEW.code_hash, NEW.organization_uid, NEW.cemetery_uid, NEW.device_type, NEW.created_by, NEW.created_at, NEW.expires_at)
       IS DISTINCT FROM (OLD.code_hash, OLD.organization_uid, OLD.cemetery_uid, OLD.device_type, OLD.created_by, OLD.created_at, OLD.expires_at) THEN
        RAISE EXCEPTION 'device_pairing_codes: câmpuri imutabile pentru coduri MOBILE' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
        IF OLD.revoked_at IS NOT NULL OR OLD.used_at IS NOT NULL OR NEW.used_at IS NOT NULL OR NEW.used_by_binding_uid IS NOT NULL THEN
            RAISE EXCEPTION 'device_pairing_codes: cod MOBILE deja folosit sau revocat' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.revoked_at IS NOT NULL OR OLD.used_at IS NOT NULL OR NEW.used_at IS NULL OR NEW.used_by_binding_uid IS NULL OR NEW.used_bootstrap_id IS NOT NULL THEN
        RAISE EXCEPTION 'device_pairing_codes: cod MOBILE deja folosit sau consum invalid' USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.expires_at <= now() THEN
        RAISE EXCEPTION 'device_pairing_codes: cod MOBILE expirat' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

-- ---------------------------------------------------------------------------------------------------- tabele
CREATE TABLE IF NOT EXISTS cp_admin_state (
    id                      smallint PRIMARY KEY CHECK (id = 1),
    bootstrap_completed_at  timestamptz,
    bootstrap_identity_uid  uuid
);
INSERT INTO cp_admin_state (id) VALUES (1) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS cp_admin_credentials (
    identity_uid         uuid PRIMARY KEY REFERENCES identities (uid),
    email                text NOT NULL,
    display_name         text NOT NULL DEFAULT '',
    password_hash        text NOT NULL,
    failed_attempts      integer NOT NULL DEFAULT 0,
    locked_until         timestamptz,
    disabled             boolean NOT NULL DEFAULT false,
    must_change          boolean NOT NULL DEFAULT false,
    created_at           timestamptz NOT NULL DEFAULT now(),
    password_changed_at  timestamptz NOT NULL DEFAULT now(),
    last_login_at        timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS cp_admin_credentials_email_uq ON cp_admin_credentials (lower(email));

CREATE TABLE IF NOT EXISTS cp_admin_sessions (
    session_hash  text PRIMARY KEY CHECK (session_hash ~ '^[0-9a-f]{64}$'),
    identity_uid  uuid NOT NULL REFERENCES identities (uid),
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    last_seen_at  timestamptz NOT NULL DEFAULT now(),
    revoked_at    timestamptz,
    ip_hash       text
);
CREATE INDEX IF NOT EXISTS cp_admin_sessions_identity_idx ON cp_admin_sessions (identity_uid);

CREATE TABLE IF NOT EXISTS cp_admin_recovery (
    token_hash    text PRIMARY KEY CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    identity_uid  uuid NOT NULL REFERENCES identities (uid),
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    used_at       timestamptz
);

-- ---------------------------------------------------------------------------------------------------- context RLS
CREATE OR REPLACE FUNCTION cp_admin_email() RETURNS text LANGUAGE sql STABLE AS $$ SELECT NULLIF(lower(current_setting('cp.admin_email', true)), '') $$;

ALTER TABLE cp_admin_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE cp_admin_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE cp_admin_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE cp_admin_recovery ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS cp_admin_cred_select ON cp_admin_credentials;
CREATE POLICY cp_admin_cred_select ON cp_admin_credentials FOR SELECT TO cimitir_cp_app USING (lower(email) = cp_admin_email() OR identity_uid = cp_principal());
DROP POLICY IF EXISTS cp_admin_cred_update ON cp_admin_credentials;
CREATE POLICY cp_admin_cred_update ON cp_admin_credentials FOR UPDATE TO cimitir_cp_app
    USING (lower(email) = cp_admin_email() OR identity_uid = cp_principal()) WITH CHECK (lower(email) = cp_admin_email() OR identity_uid = cp_principal());
DROP POLICY IF EXISTS cp_admin_sessions_own ON cp_admin_sessions;
CREATE POLICY cp_admin_sessions_own ON cp_admin_sessions TO cimitir_cp_app USING (identity_uid = cp_principal()) WITH CHECK (identity_uid = cp_principal());
-- cp_admin_state și cp_admin_recovery: fără politici pentru cimitir_cp_app => acces doar prin funcțiile SECURITY DEFINER de mai jos

-- ---------------------------------------------------------------------------------------------------- funcții SECURITY DEFINER
CREATE OR REPLACE FUNCTION cp_admin_status() RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER AS $$
    SELECT jsonb_build_object(
        'completed', (SELECT bootstrap_completed_at IS NOT NULL FROM cp_admin_state WHERE id = 1),
        'admins', (SELECT count(*) FROM cp_admin_credentials)) $$;

CREATE OR REPLACE FUNCTION cp_admin_session_identity(p_hash text) RETURNS uuid LANGUAGE sql SECURITY DEFINER AS $$
    UPDATE cp_admin_sessions s SET last_seen_at = now()
     WHERE s.session_hash = p_hash AND s.revoked_at IS NULL AND s.expires_at > now() AND s.last_seen_at > now() - interval '8 hours'
       AND EXISTS (SELECT 1 FROM cp_admin_credentials c WHERE c.identity_uid = s.identity_uid AND NOT c.disabled)
    RETURNING s.identity_uid $$;

CREATE OR REPLACE FUNCTION cp_admin_session_revoke(p_hash text) RETURNS void LANGUAGE sql SECURITY DEFINER AS $$
    UPDATE cp_admin_sessions SET revoked_at = COALESCE(revoked_at, now()) WHERE session_hash = p_hash $$;

CREATE OR REPLACE FUNCTION cp_admin_bootstrap(p_email text, p_display text, p_hash text, p_org uuid, p_org_name text, p_cem uuid, p_cem_name text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_owner uuid; v_cem_org uuid;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('cimitir:cp:admin_bootstrap', 0));
    IF (SELECT bootstrap_completed_at FROM cp_admin_state WHERE id = 1) IS NOT NULL OR EXISTS (SELECT 1 FROM cp_admin_credentials) THEN
        RAISE EXCEPTION 'bootstrap_closed' USING ERRCODE = '42501';
    END IF;
    IF EXISTS (SELECT 1 FROM organizations WHERE uid = p_org) THEN
        SELECT identity_uid INTO v_owner FROM cp_org_roles WHERE organization_uid = p_org AND role = 'owner' ORDER BY identity_uid LIMIT 1;
    ELSE
        IF COALESCE(trim(p_org_name), '') = '' THEN RAISE EXCEPTION 'org_name_required' USING ERRCODE = '22023'; END IF;
        INSERT INTO organizations (uid, organization_type, official_name, display_name) VALUES (p_org, 'OTHER', p_org_name, p_org_name);
    END IF;
    IF v_owner IS NULL THEN
        v_owner := gen_random_uuid();
        INSERT INTO identities (uid, display_name, password_hash) VALUES (v_owner, COALESCE(NULLIF(trim(p_display), ''), p_email), '!');
        INSERT INTO cp_org_roles (identity_uid, organization_uid, role) VALUES (v_owner, p_org, 'owner');
    END IF;
    UPDATE identities SET email = p_email WHERE uid = v_owner AND email IS NULL
        AND NOT EXISTS (SELECT 1 FROM identities i WHERE lower(i.email) = lower(p_email));
    IF p_cem IS NOT NULL THEN
        SELECT organization_uid INTO v_cem_org FROM cemeteries WHERE uid = p_cem;
        IF v_cem_org IS NULL THEN
            INSERT INTO cemeteries (uid, organization_uid, name, internal_code, status, data_epoch)
                VALUES (p_cem, p_org, COALESCE(NULLIF(trim(p_cem_name), ''), 'Cimitir'), p_cem::text, 'DRAFT', 1);
        ELSIF v_cem_org <> p_org THEN
            RAISE EXCEPTION 'cemetery_other_org' USING ERRCODE = '42501';
        END IF;
    END IF;
    INSERT INTO cp_admin_credentials (identity_uid, email, display_name, password_hash) VALUES (v_owner, p_email, COALESCE(NULLIF(trim(p_display), ''), p_email), p_hash);
    UPDATE cp_admin_state SET bootstrap_completed_at = now(), bootstrap_identity_uid = v_owner WHERE id = 1;
    RETURN jsonb_build_object('identity_uid', v_owner, 'organization_uid', p_org, 'cemetery_uid', p_cem);
END $$;

CREATE OR REPLACE FUNCTION cp_admin_recovery_issue(p_hash text, p_email text, p_ttl integer) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_id uuid;
BEGIN
    SELECT identity_uid INTO v_id FROM cp_admin_credentials WHERE lower(email) = lower(p_email);
    IF v_id IS NULL THEN RETURN false; END IF;
    INSERT INTO cp_admin_recovery (token_hash, identity_uid, expires_at) VALUES (p_hash, v_id, now() + make_interval(secs => LEAST(GREATEST(p_ttl, 60), 3600)));
    RETURN true;
END $$;

CREATE OR REPLACE FUNCTION cp_admin_recover(p_hash text, p_email text, p_pwhash text) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_id uuid;
BEGIN
    UPDATE cp_admin_recovery r SET used_at = now()
     WHERE r.token_hash = p_hash AND r.used_at IS NULL AND r.expires_at > now()
       AND r.identity_uid IN (SELECT identity_uid FROM cp_admin_credentials WHERE lower(email) = lower(p_email))
    RETURNING r.identity_uid INTO v_id;
    IF v_id IS NULL THEN RETURN false; END IF;
    UPDATE cp_admin_credentials SET password_hash = p_pwhash, failed_attempts = 0, locked_until = NULL, disabled = false, must_change = false, password_changed_at = now()
     WHERE identity_uid = v_id;
    UPDATE cp_admin_sessions SET revoked_at = COALESCE(revoked_at, now()) WHERE identity_uid = v_id;
    RETURN true;
END $$;

DO $$
DECLARE f text;
BEGIN
    FOREACH f IN ARRAY ARRAY['cp_admin_status()', 'cp_admin_session_identity(text)', 'cp_admin_session_revoke(text)',
        'cp_admin_bootstrap(text,text,text,uuid,text,uuid,text)', 'cp_admin_recovery_issue(text,text,integer)', 'cp_admin_recover(text,text,text)'] LOOP
        EXECUTE format('ALTER FUNCTION %s SET search_path = %I, pg_catalog', f, current_schema());
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f);
    END LOOP;
END $$;

-- ---------------------------------------------------------------------------------------------------- grants minime
DO $$
BEGIN
    GRANT SELECT ON cp_admin_credentials TO cimitir_cp_app;
    GRANT UPDATE (password_hash, failed_attempts, locked_until, last_login_at, must_change, password_changed_at) ON cp_admin_credentials TO cimitir_cp_app;
    GRANT SELECT, INSERT, UPDATE ON cp_admin_sessions TO cimitir_cp_app;
    GRANT EXECUTE ON FUNCTION cp_admin_email() TO cimitir_cp_app;
    GRANT EXECUTE ON FUNCTION cp_admin_status(), cp_admin_session_identity(text), cp_admin_session_revoke(text),
        cp_admin_bootstrap(text,text,text,uuid,text,uuid,text), cp_admin_recovery_issue(text,text,integer), cp_admin_recover(text,text,text) TO cimitir_cp_app;
END $$;
