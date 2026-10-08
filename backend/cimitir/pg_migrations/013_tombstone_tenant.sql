-- 013: tombstone-urile poartă tenantul rândului șters (organization_uid / cemetery_uid sunt NOT NULL după 011).

CREATE OR REPLACE FUNCTION cimitir_sync_tombstone() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO sync_tombstones (entity, uid, version, change_seq, organization_uid, cemetery_uid)
    VALUES (TG_TABLE_NAME, OLD.uid, COALESCE(OLD.version, 0) + 1, nextval('sync_change_seq'), OLD.organization_uid, OLD.cemetery_uid)
    ON CONFLICT (entity, uid) DO UPDATE SET version = EXCLUDED.version, change_seq = EXCLUDED.change_seq, deleted_at = now(),
        organization_uid = EXCLUDED.organization_uid, cemetery_uid = EXCLUDED.cemetery_uid;
    RETURN OLD;
END;
$$;

CREATE OR REPLACE FUNCTION cimitir_media_tombstone() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO sync_tombstones (entity, uid, version, change_seq, organization_uid, cemetery_uid)
    VALUES ('media_objects', OLD.id, 1, nextval('sync_change_seq'), OLD.organization_uid, OLD.cemetery_uid)
    ON CONFLICT (entity, uid) DO UPDATE SET change_seq = EXCLUDED.change_seq, deleted_at = now(),
        organization_uid = EXCLUDED.organization_uid, cemetery_uid = EXCLUDED.cemetery_uid;
    RETURN OLD;
END;
$$;
