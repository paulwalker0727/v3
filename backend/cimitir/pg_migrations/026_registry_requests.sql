-- 026: cereri de înregistrare a registrului MAIN în Cloud (MAIN trimite UID-urile; administratorul Cloud confirmă în /admin/). Aditivă și idempotentă.
-- Tabelul NU are politici/grant-uri pentru cimitir_cp_app: accesul se face doar prin funcțiile SECURITY DEFINER de mai jos (RLS rămâne activă, fără ocolire).
-- Autorizarea e în baza de date: vede/decide o cerere doar Owner-ul organizației existente, sau (organizație nouă) administratorul inițial / Platform Admin.
-- Secretul cererii nu se stochează (doar sha256). Codul MAIN nu se stochează deloc: se emite la revendicarea cererii aprobate, de către aprobator.
-- Rollback: pg_rollbacks/026_registry_requests_ROLLBACK.sql

CREATE TABLE IF NOT EXISTS cp_registry_requests (
    request_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    secret_hash       text NOT NULL CHECK (secret_hash ~ '^[0-9a-f]{64}$'),
    organization_uid  uuid NOT NULL,
    organization_name text NOT NULL DEFAULT '',
    cemetery_uid      uuid NOT NULL,
    cemetery_name     text NOT NULL DEFAULT '',
    device_label      text NOT NULL DEFAULT '',
    state             text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'approved', 'rejected', 'consumed')),
    created_at        timestamptz NOT NULL DEFAULT now(),
    expires_at        timestamptz NOT NULL DEFAULT now() + interval '24 hours',
    decided_by        uuid,
    decided_at        timestamptz,
    consumed_at       timestamptz
);
CREATE INDEX IF NOT EXISTS cp_registry_requests_cem_idx ON cp_registry_requests (cemetery_uid, state);
ALTER TABLE cp_registry_requests ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION cp_regreq_visible(p_identity uuid, p_org uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER AS $$
    SELECT EXISTS (SELECT 1 FROM cp_admin_credentials c WHERE c.identity_uid = p_identity AND NOT c.disabled)
       AND CASE WHEN EXISTS (SELECT 1 FROM organizations WHERE uid = p_org)
                THEN EXISTS (SELECT 1 FROM cp_org_roles r WHERE r.identity_uid = p_identity AND r.organization_uid = p_org AND r.role = 'owner')
                ELSE cp_admin_is_provisioner(p_identity) END $$;

CREATE OR REPLACE FUNCTION cp_regreq_create(p_secret_hash text, p_org uuid, p_org_name text, p_cem uuid, p_cem_name text, p_label text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_cem_org uuid; v_id uuid;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('cimitir:cp:regreq', 0));
    IF (SELECT count(*) FROM cp_registry_requests WHERE state = 'pending' AND expires_at > now()) >= 20 THEN
        RAISE EXCEPTION 'too_many_requests' USING ERRCODE = '54000';
    END IF;
    SELECT organization_uid INTO v_cem_org FROM cemeteries WHERE uid = p_cem;
    IF v_cem_org IS NOT NULL AND v_cem_org <> p_org THEN
        RAISE EXCEPTION 'cemetery_other_org' USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM organizations WHERE uid = p_org) AND COALESCE(trim(p_org_name), '') = '' THEN
        RAISE EXCEPTION 'org_name_required' USING ERRCODE = '22023';
    END IF;
    IF EXISTS (SELECT 1 FROM cp_registry_requests WHERE cemetery_uid = p_cem AND state IN ('pending', 'approved') AND expires_at > now()) THEN
        RAISE EXCEPTION 'duplicate_pending';
    END IF;
    INSERT INTO cp_registry_requests (secret_hash, organization_uid, organization_name, cemetery_uid, cemetery_name, device_label)
        VALUES (p_secret_hash, p_org, left(trim(p_org_name), 200), p_cem, left(trim(p_cem_name), 200), left(trim(p_label), 120)) RETURNING request_id INTO v_id;
    RETURN v_id;
END $$;

CREATE OR REPLACE FUNCTION cp_regreq_list(p_identity uuid) RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER AS $$
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'request_id', q.request_id, 'short_id', upper(left(q.request_id::text, 8)), 'organization_uid', q.organization_uid, 'organization_name', q.organization_name,
        'cemetery_uid', q.cemetery_uid, 'cemetery_name', q.cemetery_name, 'device_label', q.device_label,
        'created_at', extract(epoch FROM q.created_at), 'expires_at', extract(epoch FROM q.expires_at),
        'organization_exists', EXISTS (SELECT 1 FROM organizations o WHERE o.uid = q.organization_uid),
        'cemetery_exists', EXISTS (SELECT 1 FROM cemeteries c WHERE c.uid = q.cemetery_uid)) ORDER BY q.created_at), '[]'::jsonb)
    FROM cp_registry_requests q
    WHERE q.state = 'pending' AND q.expires_at > now() AND cp_regreq_visible(p_identity, q.organization_uid) $$;

CREATE OR REPLACE FUNCTION cp_regreq_decide(p_identity uuid, p_id uuid, p_approve boolean) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE q cp_registry_requests%ROWTYPE; v_res jsonb;
BEGIN
    SELECT * INTO q FROM cp_registry_requests WHERE request_id = p_id AND state = 'pending' AND expires_at > now() FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'not_pending' USING ERRCODE = 'P0002'; END IF;
    IF NOT cp_regreq_visible(p_identity, q.organization_uid) THEN RAISE EXCEPTION 'not_authorized' USING ERRCODE = '42501'; END IF;
    IF p_approve THEN
        v_res := cp_admin_provision(p_identity, q.organization_uid, q.organization_name, q.cemetery_uid, q.cemetery_name);
        UPDATE cp_registry_requests SET state = 'approved', decided_by = p_identity, decided_at = now() WHERE request_id = p_id;
    ELSE
        v_res := '{}'::jsonb;
        UPDATE cp_registry_requests SET state = 'rejected', decided_by = p_identity, decided_at = now() WHERE request_id = p_id;
    END IF;
    RETURN v_res || jsonb_build_object('request_id', p_id, 'state', CASE WHEN p_approve THEN 'approved' ELSE 'rejected' END,
                                       'organization_uid', q.organization_uid, 'cemetery_uid', q.cemetery_uid);
END $$;

CREATE OR REPLACE FUNCTION cp_regreq_poll(p_id uuid, p_secret_hash text) RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER AS $$
DECLARE q cp_registry_requests%ROWTYPE; v_state text;
BEGIN
    SELECT * INTO q FROM cp_registry_requests WHERE request_id = p_id AND secret_hash = p_secret_hash;
    IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE = 'P0002'; END IF;
    v_state := CASE WHEN q.state = 'pending' AND q.expires_at <= now() THEN 'expired' ELSE q.state END;
    RETURN jsonb_build_object('state', v_state, 'organization_uid', q.organization_uid, 'cemetery_uid', q.cemetery_uid, 'expires_at', extract(epoch FROM q.expires_at));
END $$;

CREATE OR REPLACE FUNCTION cp_regreq_claim(p_id uuid, p_secret_hash text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE q cp_registry_requests%ROWTYPE;
BEGIN
    UPDATE cp_registry_requests SET state = 'consumed', consumed_at = now()
     WHERE request_id = p_id AND secret_hash = p_secret_hash AND state = 'approved' RETURNING * INTO q;
    IF NOT FOUND THEN RETURN NULL; END IF;
    RETURN jsonb_build_object('decided_by', q.decided_by, 'organization_uid', q.organization_uid, 'cemetery_uid', q.cemetery_uid);
END $$;

CREATE OR REPLACE FUNCTION cp_regreq_unclaim(p_id uuid, p_secret_hash text) RETURNS void LANGUAGE sql SECURITY DEFINER AS $$
    UPDATE cp_registry_requests SET state = 'approved', consumed_at = NULL WHERE request_id = p_id AND secret_hash = p_secret_hash AND state = 'consumed' $$;

DO $$
DECLARE f text;
BEGIN
    FOREACH f IN ARRAY ARRAY['cp_regreq_visible(uuid,uuid)', 'cp_regreq_create(text,uuid,text,uuid,text,text)', 'cp_regreq_list(uuid)', 'cp_regreq_decide(uuid,uuid,boolean)',
                             'cp_regreq_poll(uuid,text)', 'cp_regreq_claim(uuid,text)', 'cp_regreq_unclaim(uuid,text)'] LOOP
        EXECUTE format('ALTER FUNCTION %s SET search_path = %I, pg_catalog', f, current_schema());
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f);
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cimitir_cp_app') AND f <> 'cp_regreq_visible(uuid,uuid)' THEN
            EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO cimitir_cp_app', f);
        END IF;
    END LOOP;
END $$;
