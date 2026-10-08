-- 004_media_objects.sql — Etapa 7: documente/fotografii în storage online (S3-compatibil / Supabase Storage).
-- PostgreSQL păstrează NUMAI metadatele fișierului; PDF/JPG NU se stochează în baza de date (octeții sunt în storage,
-- la `storage_path`). Coloanele de business sunt exact cele cerute; `change_seq` este doar infrastructură de sincronizare
-- (ca la entitățile din Etapa 6): permite clienților să descarce incremental și să afle ștergerile (sync_tombstones).
-- Repetabilă (IF NOT EXISTS / OR REPLACE).

CREATE TABLE IF NOT EXISTS media_objects (
    id           TEXT PRIMARY KEY,                 -- ID (UUID generat de client la crearea documentului)
    entity_uid   TEXT NOT NULL,                    -- uid-ul entității de care aparține (loc, înhumat, contract, ...)
    filename     TEXT NOT NULL,
    storage_path TEXT NOT NULL UNIQUE,             -- cheia obiectului în storage
    mime         TEXT NOT NULL DEFAULT 'application/octet-stream',
    size         BIGINT NOT NULL CHECK (size >= 0),
    sha256       TEXT NOT NULL CHECK (length(sha256) = 64),
    uploaded_by  TEXT,
    uploaded_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    change_seq   BIGINT                            -- doar sincronizare incrementală
);
CREATE INDEX IF NOT EXISTS idx_media_objects_entity ON media_objects(entity_uid);
CREATE INDEX IF NOT EXISTS idx_media_objects_seq ON media_objects(change_seq);

CREATE OR REPLACE FUNCTION cimitir_media_touch() RETURNS trigger AS $$
BEGIN
    NEW.change_seq = nextval('sync_change_seq');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION cimitir_media_tombstone() RETURNS trigger AS $$
BEGIN
    INSERT INTO sync_tombstones (entity, uid, version, change_seq)
    VALUES ('media_objects', OLD.id, 1, nextval('sync_change_seq'))
    ON CONFLICT (entity, uid) DO UPDATE SET change_seq = EXCLUDED.change_seq, deleted_at = now();
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_media_objects_touch ON media_objects;
CREATE TRIGGER trg_media_objects_touch BEFORE INSERT ON media_objects FOR EACH ROW EXECUTE FUNCTION cimitir_media_touch();
DROP TRIGGER IF EXISTS trg_media_objects_tomb ON media_objects;
CREATE TRIGGER trg_media_objects_tomb AFTER DELETE ON media_objects FOR EACH ROW EXECUTE FUNCTION cimitir_media_tombstone();

-- legătura rândurilor locale (documents / photos) cu obiectul din storage
ALTER TABLE documents ADD COLUMN IF NOT EXISTS media_id TEXT;
ALTER TABLE photos    ADD COLUMN IF NOT EXISTS media_id TEXT;
CREATE INDEX IF NOT EXISTS idx_documents_media ON documents(media_id);
CREATE INDEX IF NOT EXISTS idx_photos_media ON photos(media_id);
