-- 024_sync_v2_documents.sql — documente și fotografii MAIN <-> Cloud <-> Mobile (metadate în PostgreSQL, octeți în Supabase Storage PRIVAT).
--
-- ADITIVĂ și idempotentă: un singur tabel nou (sync_v2_documents) + o funcție trigger. Nu modifică 001–023, nu atinge `documents`/`photos`/`media_objects`/
-- `cp_storage_objects` (Field Inbox rămâne neschimbat) și nu șterge date. Rollback: pg_rollbacks/024_sync_v2_documents_ROLLBACK.sql.
--
-- Garanții:
--   * RLS pe tenant (organization_uid = cp_org() AND cp_cem_ok(cemetery_uid)); fără DELETE pentru rolul runtime (ștergerea e logică: status DELETED,
--     doar MAIN o poate cere — impus în aplicație; octeții NU se șterg automat => protecție împotriva pierderii).
--   * Coloane imutabile (uid, tenant, entitate, sha256, size, mime, storage_path, origin) protejate prin trigger; UPDATE permis doar pe status/caption/error/deleted_*.
--   * Fără duplicate: UNIQUE (cemetery_uid, uid) și UNIQUE parțial (cemetery_uid, entity, entity_uid, sha256) pentru rândurile neșterse.
--   * Calea din Storage se construiește numai pe server (docs/<org>/<cemetery>/<uid>); clientul nu trimite niciodată o cale.
--   * Tipurile de documente cu date de identitate (carte_identitate, cerere_doc) NU se sincronizează (refuzate și la nivel de CHECK).

CREATE TABLE IF NOT EXISTS sync_v2_documents (
    organization_uid   uuid NOT NULL,
    cemetery_uid       uuid NOT NULL,
    uid                text NOT NULL CHECK (uid ~ '^([0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$'),
    entity             text NOT NULL CHECK (entity IN ('locuri_de_veci','concesionari','contracte','inhumati','plati','amenajari_avize')),
    entity_uid         text NOT NULL CHECK (length(entity_uid) BETWEEN 1 AND 80),
    doc_type           text NOT NULL DEFAULT '' CHECK (length(doc_type) <= 60 AND doc_type NOT IN ('carte_identitate','cerere_doc')),
    kind               text NOT NULL CHECK (kind IN ('document','photo')),
    filename           text NOT NULL CHECK (length(filename) BETWEEN 1 AND 200),
    mime               text NOT NULL CHECK (mime IN ('application/pdf','image/jpeg','image/png')),
    size               bigint NOT NULL CHECK (size > 0 AND size <= 26214400),
    sha256             text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    caption            text NOT NULL DEFAULT '' CHECK (length(caption) <= 500),
    storage_path       text NOT NULL,
    status             text NOT NULL CHECK (status IN ('PENDING','READY','FAILED','DELETED')),
    error              text,
    origin             text NOT NULL CHECK (origin IN ('MAIN','MOBILE')),
    created_by_binding uuid,
    deleted_by_binding uuid,
    version            integer NOT NULL DEFAULT 1,
    change_seq         bigint,
    created_at         timestamptz NOT NULL DEFAULT now(),
    modified_at        timestamptz NOT NULL DEFAULT now(),
    deleted_at         timestamptz,
    PRIMARY KEY (cemetery_uid, uid),
    UNIQUE (storage_path),
    FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES cemeteries (uid, organization_uid)
);
CREATE UNIQUE INDEX IF NOT EXISTS sync_v2_documents_nodup ON sync_v2_documents (cemetery_uid, entity, entity_uid, sha256) WHERE status <> 'DELETED';
CREATE INDEX IF NOT EXISTS sync_v2_documents_seq ON sync_v2_documents (cemetery_uid, change_seq);
CREATE INDEX IF NOT EXISTS sync_v2_documents_entity ON sync_v2_documents (cemetery_uid, entity, entity_uid);

CREATE OR REPLACE FUNCTION cimitir_sync_v2_documents_touch() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $f$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.version := 1;
    ELSE
        IF (NEW.uid, NEW.organization_uid, NEW.cemetery_uid, NEW.entity, NEW.entity_uid, NEW.kind, NEW.filename, NEW.mime, NEW.size, NEW.sha256, NEW.storage_path, NEW.origin, NEW.created_at)
           IS DISTINCT FROM (OLD.uid, OLD.organization_uid, OLD.cemetery_uid, OLD.entity, OLD.entity_uid, OLD.kind, OLD.filename, OLD.mime, OLD.size, OLD.sha256, OLD.storage_path, OLD.origin, OLD.created_at) THEN
            RAISE EXCEPTION 'sync_v2_documents: câmpuri imutabile' USING ERRCODE = 'check_violation';
        END IF;
        IF OLD.status = 'DELETED' AND NEW.status <> 'DELETED' THEN
            RAISE EXCEPTION 'sync_v2_documents: un document șters nu se reactivează' USING ERRCODE = 'check_violation';
        END IF;
        NEW.version := OLD.version + 1;
    END IF;
    NEW.change_seq := nextval('sync_change_seq');
    NEW.modified_at := now();
    IF NEW.status = 'DELETED' AND NEW.deleted_at IS NULL THEN NEW.deleted_at := now(); END IF;
    RETURN NEW;
END
$f$;
DROP TRIGGER IF EXISTS trg_sync_v2_documents_touch ON sync_v2_documents;
CREATE TRIGGER trg_sync_v2_documents_touch BEFORE INSERT OR UPDATE ON sync_v2_documents FOR EACH ROW EXECUTE FUNCTION cimitir_sync_v2_documents_touch();

ALTER TABLE sync_v2_documents ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS sync_v2_docs_select ON sync_v2_documents;
DROP POLICY IF EXISTS sync_v2_docs_insert ON sync_v2_documents;
DROP POLICY IF EXISTS sync_v2_docs_update ON sync_v2_documents;
CREATE POLICY sync_v2_docs_select ON sync_v2_documents FOR SELECT TO cimitir_cp_app USING (organization_uid = cp_org() AND cp_cem_ok(cemetery_uid));
CREATE POLICY sync_v2_docs_insert ON sync_v2_documents FOR INSERT TO cimitir_cp_app WITH CHECK (organization_uid = cp_org() AND cp_cem_ok(cemetery_uid));
CREATE POLICY sync_v2_docs_update ON sync_v2_documents FOR UPDATE TO cimitir_cp_app USING (organization_uid = cp_org() AND cp_cem_ok(cemetery_uid))
    WITH CHECK (organization_uid = cp_org() AND cp_cem_ok(cemetery_uid));

REVOKE ALL ON sync_v2_documents FROM PUBLIC;
GRANT SELECT, INSERT ON sync_v2_documents TO cimitir_cp_app;
GRANT UPDATE (status, caption, error, deleted_by_binding, deleted_at) ON sync_v2_documents TO cimitir_cp_app;
