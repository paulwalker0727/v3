-- 009: coloane de tenant (organization_uid, cemetery_uid) pe tabelele existente + backfill cu organizația/cimitirul implicit.
-- Cimitirul implicit = settings.cemetery_id (dacă e UUID valid; altfel se generează și se scrie în settings). Idempotentă.
-- Coloanele rămân NULLABLE aici; NOT NULL se aplică în 011, după verificările preflight.

DO $$
DECLARE
    org_id    UUID;
    cem_id    UUID;
    raw_id    TEXT;
    t         TEXT;
    u         RECORD;
    ident_id  UUID;
    memb_id   UUID;
BEGIN
    -- organizația și cimitirul implicit, din datele existente (settings.cemetery_id dacă e UUID valid)
    SELECT value INTO raw_id FROM settings WHERE key = 'cemetery_id';
    BEGIN cem_id := raw_id::uuid; EXCEPTION WHEN others THEN cem_id := gen_random_uuid(); END;
    IF cem_id IS NULL THEN cem_id := gen_random_uuid(); END IF;

    -- identitatea cimitirului din settings trebuie să coincidă cu cemeteries.uid (aplicația o citește de acolo)
    INSERT INTO settings (key, value) VALUES ('cemetery_id', cem_id::text)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value WHERE settings.value IS DISTINCT FROM EXCLUDED.value
        AND NOT (settings.value ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$');

    SELECT c.organization_uid INTO org_id FROM cemeteries c WHERE c.uid = cem_id;
    IF org_id IS NULL THEN
        INSERT INTO organizations (organization_type, official_name, display_name)
        VALUES ('OTHER', 'Organizație implicită', 'Organizație implicită') RETURNING uid INTO org_id;
        INSERT INTO cemeteries (uid, organization_uid, name, internal_code)
        VALUES (cem_id, org_id, 'Cimitir implicit', 'DEFAULT');
    END IF;

    -- tabele cu date de registru / sincronizare: organization_uid + cemetery_uid
    FOREACH t IN ARRAY ARRAY[
        'locuri_de_veci','concesionari','contracte','inhumati','plati','amenajari_avize','sectoare','photos','documents',
        'media_objects','loc_extra','guides','rows_ghid','loc_holders','qr_generations','ai_scan_history',
        'cereri_concesionare','mobile_upload_tokens','sync_ops','sync_tombstones','sync_conflicts','email_outbox','audit_log']
    LOOP
        IF to_regclass(t) IS NULL THEN RAISE NOTICE 'sărit (nu există): %', t; CONTINUE; END IF;
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS organization_uid UUID', t);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS cemetery_uid UUID', t);
        -- backfill-ul NU trebuie să schimbe version / change_seq / modified_at (trigger-ele utilizator le-ar bumpa și clienții de sincronizare ar vedea conflicte false)
        EXECUTE format('ALTER TABLE %I DISABLE TRIGGER USER', t);
        EXECUTE format('UPDATE %I SET organization_uid = %L, cemetery_uid = %L WHERE cemetery_uid IS NULL', t, org_id, cem_id);
        EXECUTE format('ALTER TABLE %I ENABLE TRIGGER USER', t);
        EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I (cemetery_uid)', t || '_cemetery_idx', t);
    END LOOP;

    -- tabele la nivel de organizație (cetățeni): doar organization_uid
    FOREACH t IN ARRAY ARRAY['citizens','citizen_resets']
    LOOP
        IF to_regclass(t) IS NULL THEN CONTINUE; END IF;
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS organization_uid UUID', t);
        EXECUTE format('ALTER TABLE %I DISABLE TRIGGER USER', t);
        EXECUTE format('UPDATE %I SET organization_uid = %L WHERE organization_uid IS NULL', t, org_id);
        EXECUTE format('ALTER TABLE %I ENABLE TRIGGER USER', t);
        EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I (organization_uid)', t || '_org_idx', t);
    END LOOP;

    -- personal: users -> identities + organization_members + cemetery_permissions (idempotent prin legacy_user_id)
    FOR u IN SELECT id, username, password_hash, role, rights, COALESCE(active, 1) AS active FROM users LOOP
        INSERT INTO identities (display_name, password_hash, active, legacy_user_id)
        VALUES (u.username, u.password_hash, u.active = 1, u.id)
        ON CONFLICT (legacy_user_id) DO UPDATE SET active = EXCLUDED.active
        RETURNING uid INTO ident_id;
        INSERT INTO organization_members (organization_uid, identity_uid, username, org_role, active)
        VALUES (org_id, ident_id, u.username, CASE WHEN u.role = 'admin' THEN 'ORG_ADMIN' ELSE 'STAFF' END, u.active = 1)
        ON CONFLICT (organization_uid, identity_uid) DO UPDATE SET active = EXCLUDED.active
        RETURNING member_uid INTO memb_id;
        INSERT INTO cemetery_permissions (member_uid, organization_uid, cemetery_uid, role, rights, active)
        VALUES (memb_id, org_id, cem_id, u.role, jsonb_build_object('legacy_rights', u.rights), u.active = 1)
        ON CONFLICT (member_uid, cemetery_uid) DO NOTHING;
    END LOOP;
END $$;
