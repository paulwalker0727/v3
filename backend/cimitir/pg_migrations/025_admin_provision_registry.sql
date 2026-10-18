-- 025: provizionarea registrului MAIN în Cloud (organizație + cimitir cu UID-urile ORIGINALE) de către un administrator autorizat, fără SQL manual.
-- Aditivă și idempotentă (doar CREATE OR REPLACE FUNCTION + GRANT). NU modifică tabele/date existente, NU slăbește RLS: scrierea trece prin funcții SECURITY DEFINER
-- care verifică în interiorul bazei de date: (1) identitatea e administrator activ; (2) pentru organizație existentă: rol Owner; (3) pentru organizație nouă: administratorul
-- inițial al instalării (cp_admin_state.bootstrap_identity_uid) sau Platform Admin. UID-urile nu se reutilizează între organizații.
-- Rollback: pg_rollbacks/025_admin_provision_registry_ROLLBACK.sql

CREATE OR REPLACE FUNCTION cp_admin_is_provisioner(p_identity uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER AS $$
    SELECT EXISTS (SELECT 1 FROM cp_admin_credentials c WHERE c.identity_uid = p_identity AND NOT c.disabled)
       AND (EXISTS (SELECT 1 FROM cp_admin_state s WHERE s.id = 1 AND s.bootstrap_identity_uid = p_identity)
            OR EXISTS (SELECT 1 FROM cp_platform_admins p WHERE p.identity_uid = p_identity)) $$;

CREATE OR REPLACE FUNCTION cp_admin_registry_lookup(p_identity uuid, p_org uuid, p_cem uuid) RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER AS $$
DECLARE v_org boolean; v_owner boolean := false; v_cem_org uuid; v_status text;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM cp_admin_credentials c WHERE c.identity_uid = p_identity AND NOT c.disabled) THEN
        RAISE EXCEPTION 'not_admin' USING ERRCODE = '42501';
    END IF;
    v_org := EXISTS (SELECT 1 FROM organizations WHERE uid = p_org);
    IF v_org THEN
        v_owner := EXISTS (SELECT 1 FROM cp_org_roles WHERE identity_uid = p_identity AND organization_uid = p_org AND role = 'owner');
    END IF;
    SELECT organization_uid, status INTO v_cem_org, v_status FROM cemeteries WHERE uid = p_cem;
    RETURN jsonb_build_object(
        'organization_exists', v_org,
        'is_owner', v_owner,
        'can_provision_organization', cp_admin_is_provisioner(p_identity),
        'cemetery_exists', v_cem_org IS NOT NULL,
        'cemetery_in_organization', v_cem_org IS NOT NULL AND v_cem_org = p_org,
        'cemetery_in_other_organization', v_cem_org IS NOT NULL AND v_cem_org <> p_org,
        'cemetery_status', CASE WHEN v_cem_org = p_org AND v_owner THEN v_status ELSE NULL END);
END $$;

CREATE OR REPLACE FUNCTION cp_admin_provision(p_identity uuid, p_org uuid, p_org_name text, p_cem uuid, p_cem_name text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_org_created boolean := false; v_cem_created boolean := false; v_cem_org uuid; v_status text;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('cimitir:cp:provision:' || p_org::text, 0));
    IF NOT EXISTS (SELECT 1 FROM cp_admin_credentials c WHERE c.identity_uid = p_identity AND NOT c.disabled) THEN
        RAISE EXCEPTION 'not_admin' USING ERRCODE = '42501';
    END IF;
    SELECT organization_uid INTO v_cem_org FROM cemeteries WHERE uid = p_cem;
    IF v_cem_org IS NOT NULL AND v_cem_org <> p_org THEN
        RAISE EXCEPTION 'cemetery_other_org' USING ERRCODE = '42501';
    END IF;
    IF EXISTS (SELECT 1 FROM organizations WHERE uid = p_org) THEN
        IF NOT EXISTS (SELECT 1 FROM cp_org_roles WHERE identity_uid = p_identity AND organization_uid = p_org AND role = 'owner') THEN
            RAISE EXCEPTION 'not_owner' USING ERRCODE = '42501';
        END IF;
    ELSE
        IF NOT cp_admin_is_provisioner(p_identity) THEN
            RAISE EXCEPTION 'not_provisioner' USING ERRCODE = '42501';
        END IF;
        IF COALESCE(trim(p_org_name), '') = '' THEN
            RAISE EXCEPTION 'org_name_required' USING ERRCODE = '22023';
        END IF;
        INSERT INTO organizations (uid, organization_type, official_name, display_name) VALUES (p_org, 'OTHER', trim(p_org_name), trim(p_org_name));
        INSERT INTO cp_org_roles (identity_uid, organization_uid, role) VALUES (p_identity, p_org, 'owner') ON CONFLICT DO NOTHING;
        v_org_created := true;
    END IF;
    IF v_cem_org IS NULL THEN
        INSERT INTO cemeteries (uid, organization_uid, name, internal_code, status, data_epoch)
            VALUES (p_cem, p_org, COALESCE(NULLIF(trim(p_cem_name), ''), 'Cimitir'), p_cem::text, 'DRAFT', 1);
        v_cem_created := true;
    END IF;
    SELECT status INTO v_status FROM cemeteries WHERE uid = p_cem;
    RETURN jsonb_build_object('organization_uid', p_org, 'cemetery_uid', p_cem, 'organization_created', v_org_created,
                              'cemetery_created', v_cem_created, 'cemetery_status', v_status);
END $$;

DO $$
DECLARE f text;
BEGIN
    FOREACH f IN ARRAY ARRAY['cp_admin_is_provisioner(uuid)', 'cp_admin_registry_lookup(uuid,uuid,uuid)', 'cp_admin_provision(uuid,uuid,text,uuid,text)'] LOOP
        EXECUTE format('ALTER FUNCTION %s SET search_path = %I, pg_catalog', f, current_schema());
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f);
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cimitir_cp_app') THEN
        GRANT EXECUTE ON FUNCTION cp_admin_is_provisioner(uuid), cp_admin_registry_lookup(uuid,uuid,uuid), cp_admin_provision(uuid,uuid,text,uuid,text) TO cimitir_cp_app;
    END IF;
END $$;
