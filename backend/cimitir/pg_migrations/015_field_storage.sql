-- 015: fundația 6B — metadata obiectelor media din teren (Field Inbox) păstrate în Storage privat; PostgreSQL ține tenant, SHA-256, cale și status.
-- Aditiv: un singur tabel nou, același model RLS ca 014 (cp_org_ok + cp_cem_ok), fără drepturi noi pe tabelele 001–014. Octeții NU sunt în PostgreSQL.

CREATE TABLE IF NOT EXISTS cp_storage_objects (
    cemetery_uid       uuid NOT NULL,
    uid                text NOT NULL CHECK (uid ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),
    organization_uid   uuid NOT NULL,
    kind               text NOT NULL CHECK (kind IN ('document', 'photo')),
    filename           text NOT NULL CHECK (length(filename) BETWEEN 1 AND 200),
    mime               text NOT NULL CHECK (mime IN ('application/pdf', 'image/jpeg', 'image/png')),
    size               bigint NOT NULL CHECK (size > 0 AND size <= 26214400),
    sha256             text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    storage_path       text NOT NULL,
    status             text NOT NULL CHECK (status IN ('PENDING', 'UPLOADED', 'READY', 'FAILED', 'DELETED')),
    error              text,
    created_by_binding uuid,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    ready_at           timestamptz,
    PRIMARY KEY (cemetery_uid, uid),
    UNIQUE (storage_path),
    FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES cemeteries (uid, organization_uid)
);
CREATE INDEX IF NOT EXISTS cp_storage_objects_status_idx ON cp_storage_objects (cemetery_uid, status, updated_at);

DROP TRIGGER IF EXISTS cp_fill_org ON cp_storage_objects;
CREATE TRIGGER cp_fill_org BEFORE INSERT ON cp_storage_objects FOR EACH ROW EXECUTE FUNCTION cp_fill_org();

ALTER TABLE cp_storage_objects ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS cp_tenant ON cp_storage_objects;
CREATE POLICY cp_tenant ON cp_storage_objects TO cimitir_cp_app USING (cp_org_ok(organization_uid) AND cp_cem_ok(cemetery_uid))
    WITH CHECK (cp_org_ok(organization_uid) AND cp_cem_ok(cemetery_uid));

GRANT SELECT, INSERT, UPDATE, DELETE ON cp_storage_objects TO cimitir_cp_app;
