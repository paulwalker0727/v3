-- 018: Field Inbox v1 — elemente trimise din teren (telefon 4G/5G) care așteaptă aprobarea operatorului MAIN. Aditiv; 001–017 rămân NESCHIMBATE.
-- NU stochează octeți: fișierele sunt obiecte 6B (cp_storage_objects, Storage privat); aici doar metadata itemului + legătura item <-> obiect.
-- Statusuri: PENDING -> ACCEPTED | REJECTED ; ACCEPTED -> IMPORTED. Nu există stare tehnică intermediară; REJECTED și IMPORTED sunt terminale. Fără DELETE pentru aplicație.
-- Idempotență: UNIQUE (organization_uid, cemetery_uid, idempotency_key) + request_hash (aceeași cheie cu conținut diferit = conflict).
-- Schema-agnostic (fără `public`), idempotent. Același model RLS ca 014/015 (cp_org_ok AND cp_cem_ok), doar pentru cimitir_cp_app.

CREATE TABLE IF NOT EXISTS cp_field_inbox_items (
    uid                uuid PRIMARY KEY,
    organization_uid   uuid NOT NULL,
    cemetery_uid       uuid NOT NULL,
    item_type          text NOT NULL CHECK (item_type IN ('DECEASED', 'WORK', 'PERMIT', 'DOCUMENT', 'GRAVE_PHOTO', 'WORK_PHOTO')),
    work_stage         text CHECK (work_stage IN ('BEFORE', 'DURING', 'AFTER')),
    target_uid         text CHECK (target_uid IS NULL OR length(target_uid) BETWEEN 1 AND 64),
    grave_uid          text CHECK (grave_uid IS NULL OR length(grave_uid) BETWEEN 1 AND 64),
    payload            jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 65536),
    status             text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'ACCEPTED', 'REJECTED', 'IMPORTED')),
    idempotency_key    text NOT NULL CHECK (length(idempotency_key) BETWEEN 16 AND 128 AND idempotency_key ~ '^[A-Za-z0-9._:-]+$'),
    request_hash       text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    created_at         timestamptz NOT NULL DEFAULT now(),
    created_by         uuid NOT NULL REFERENCES device_cemetery_bindings (binding_uid),
    reviewed_at        timestamptz,
    reviewed_by        uuid REFERENCES device_cemetery_bindings (binding_uid),
    imported_at        timestamptz,
    imported_by        uuid REFERENCES device_cemetery_bindings (binding_uid),
    rejection_reason   text CHECK (rejection_reason IS NULL OR length(rejection_reason) <= 500),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (cemetery_uid, uid),
    UNIQUE (organization_uid, cemetery_uid, idempotency_key),
    FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES cemeteries (uid, organization_uid),
    CONSTRAINT cp_field_inbox_items_stage_check CHECK ((item_type = 'WORK_PHOTO') = (work_stage IS NOT NULL)),
    CONSTRAINT cp_field_inbox_items_target_check CHECK (target_uid IS NOT NULL OR grave_uid IS NOT NULL),
    CONSTRAINT cp_field_inbox_items_pending_check CHECK (status <> 'PENDING' OR (reviewed_at IS NULL AND reviewed_by IS NULL AND imported_at IS NULL AND imported_by IS NULL AND rejection_reason IS NULL)),
    CONSTRAINT cp_field_inbox_items_accepted_check CHECK (status <> 'ACCEPTED' OR (reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL AND imported_at IS NULL AND imported_by IS NULL AND rejection_reason IS NULL)),
    CONSTRAINT cp_field_inbox_items_rejected_check CHECK (status <> 'REJECTED' OR (reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL AND imported_at IS NULL AND imported_by IS NULL
                                                                                   AND rejection_reason IS NOT NULL AND length(btrim(rejection_reason)) > 0)),
    CONSTRAINT cp_field_inbox_items_imported_check CHECK (status <> 'IMPORTED' OR (reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL AND imported_at IS NOT NULL AND imported_by IS NOT NULL AND rejection_reason IS NULL))
);
CREATE INDEX IF NOT EXISTS cp_field_inbox_items_list_idx ON cp_field_inbox_items (cemetery_uid, status, created_at);

CREATE TABLE IF NOT EXISTS cp_field_inbox_attachments (
    cemetery_uid       uuid NOT NULL,
    organization_uid   uuid NOT NULL,
    item_uid           uuid NOT NULL,
    object_uid         text NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    created_by         uuid NOT NULL REFERENCES device_cemetery_bindings (binding_uid),
    PRIMARY KEY (cemetery_uid, item_uid, object_uid),
    UNIQUE (cemetery_uid, object_uid),                                   -- un obiect Storage aparține cel mult unui item
    FOREIGN KEY (cemetery_uid, item_uid) REFERENCES cp_field_inbox_items (cemetery_uid, uid),
    FOREIGN KEY (cemetery_uid, object_uid) REFERENCES cp_storage_objects (cemetery_uid, uid),
    FOREIGN KEY (cemetery_uid, organization_uid) REFERENCES cemeteries (uid, organization_uid)
);

DROP TRIGGER IF EXISTS cp_fill_org ON cp_field_inbox_items;
CREATE TRIGGER cp_fill_org BEFORE INSERT ON cp_field_inbox_items FOR EACH ROW EXECUTE FUNCTION cp_fill_org();
DROP TRIGGER IF EXISTS cp_fill_org ON cp_field_inbox_attachments;
CREATE TRIGGER cp_fill_org BEFORE INSERT ON cp_field_inbox_attachments FOR EACH ROW EXECUTE FUNCTION cp_fill_org();

-- ---------------------------------------------------------------------------------------------------- automatul de stări, impus de DB
-- Tranziții permise: PENDING->ACCEPTED, PENDING->REJECTED, ACCEPTED->IMPORTED. Câmpurile de conținut sunt imutabile. Aprobarea/respingerea/importul
-- cer o legătură activă a unui dispozitiv MAIN din același cimitir (telefonul MOBILE nu se poate auto-aproba). Compare-and-set-ul concret îl face UPDATE ... WHERE status = <de la>.
CREATE OR REPLACE FUNCTION cp_field_inbox_items_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_actor uuid;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'PENDING' THEN
            RAISE EXCEPTION 'cp_field_inbox_items: rând nou doar PENDING' USING ERRCODE = 'check_violation';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM device_cemetery_bindings b WHERE b.binding_uid = NEW.created_by AND b.cemetery_uid = NEW.cemetery_uid
                       AND b.organization_uid = NEW.organization_uid AND b.active AND b.revoked_at IS NULL) THEN
            RAISE EXCEPTION 'cp_field_inbox_items: legătură invalidă pentru acest cimitir' USING ERRCODE = 'check_violation';
        END IF;
        NEW.created_at := now();
        NEW.updated_at := now();
        RETURN NEW;
    END IF;
    IF (NEW.uid, NEW.organization_uid, NEW.cemetery_uid, NEW.item_type, NEW.work_stage, NEW.target_uid, NEW.grave_uid, NEW.payload, NEW.idempotency_key,
        NEW.request_hash, NEW.created_at, NEW.created_by)
       IS DISTINCT FROM (OLD.uid, OLD.organization_uid, OLD.cemetery_uid, OLD.item_type, OLD.work_stage, OLD.target_uid, OLD.grave_uid, OLD.payload, OLD.idempotency_key,
        OLD.request_hash, OLD.created_at, OLD.created_by) THEN
        RAISE EXCEPTION 'cp_field_inbox_items: câmpuri imutabile' USING ERRCODE = 'check_violation';
    END IF;
    IF NOT ((OLD.status = 'PENDING' AND NEW.status IN ('ACCEPTED', 'REJECTED')) OR (OLD.status = 'ACCEPTED' AND NEW.status = 'IMPORTED')) THEN
        RAISE EXCEPTION 'cp_field_inbox_items: tranziția % -> % nu este permisă', OLD.status, NEW.status USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'IMPORTED' AND (NEW.reviewed_at, NEW.reviewed_by) IS DISTINCT FROM (OLD.reviewed_at, OLD.reviewed_by) THEN
        RAISE EXCEPTION 'cp_field_inbox_items: câmpurile de review sunt imutabile la import' USING ERRCODE = 'check_violation';
    END IF;
    v_actor := CASE WHEN NEW.status = 'IMPORTED' THEN NEW.imported_by ELSE NEW.reviewed_by END;
    IF NOT EXISTS (SELECT 1 FROM device_cemetery_bindings b JOIN devices d ON d.device_uid = b.device_uid AND d.organization_uid = b.organization_uid
                   WHERE b.binding_uid = v_actor AND b.cemetery_uid = NEW.cemetery_uid AND b.organization_uid = NEW.organization_uid
                     AND b.active AND b.revoked_at IS NULL AND d.device_type = 'MAIN') THEN
        RAISE EXCEPTION 'cp_field_inbox_items: doar o legătură MAIN activă din acest cimitir poate schimba statusul' USING ERRCODE = 'check_violation';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS cp_field_inbox_items_guard ON cp_field_inbox_items;
CREATE TRIGGER cp_field_inbox_items_guard BEFORE INSERT OR UPDATE ON cp_field_inbox_items FOR EACH ROW EXECUTE FUNCTION cp_field_inbox_items_guard();

-- legătura item <-> obiect: doar cât itemul e PENDING (FOR SHARE serializează față de accept/reject) și doar pentru obiecte READY ale aceluiași tenant
CREATE OR REPLACE FUNCTION cp_field_inbox_attachments_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_status text;
    v_item_org uuid;
    v_obj_status text;
    v_obj_org uuid;
BEGIN
    SELECT i.status, i.organization_uid INTO v_status, v_item_org FROM cp_field_inbox_items i WHERE i.cemetery_uid = NEW.cemetery_uid AND i.uid = NEW.item_uid FOR SHARE;
    IF v_status IS NULL OR v_item_org <> NEW.organization_uid THEN
        RAISE EXCEPTION 'cp_field_inbox_attachments: item inexistent' USING ERRCODE = 'check_violation';
    END IF;
    IF v_status <> 'PENDING' THEN
        RAISE EXCEPTION 'cp_field_inbox_attachments: itemul nu mai este PENDING (%)', v_status USING ERRCODE = 'check_violation';
    END IF;
    SELECT o.status, o.organization_uid INTO v_obj_status, v_obj_org FROM cp_storage_objects o WHERE o.cemetery_uid = NEW.cemetery_uid AND o.uid = NEW.object_uid FOR SHARE;
    IF v_obj_status IS NULL OR v_obj_org <> NEW.organization_uid OR v_obj_status <> 'READY' THEN
        RAISE EXCEPTION 'cp_field_inbox_attachments: obiectul trebuie să fie READY în același tenant' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS cp_field_inbox_attachments_guard ON cp_field_inbox_attachments;
CREATE TRIGGER cp_field_inbox_attachments_guard BEFORE INSERT ON cp_field_inbox_attachments FOR EACH ROW EXECUTE FUNCTION cp_field_inbox_attachments_guard();

-- un obiect READY legat de un item nerespins nu poate fi șters (READY -> DELETED); rolul de cleanup nu e afectat (nu atinge READY, iar trigger-ul ignoră alte tranziții)
CREATE OR REPLACE FUNCTION cp_storage_objects_inbox_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'READY' AND NEW.status = 'DELETED'
       AND EXISTS (SELECT 1 FROM cp_field_inbox_attachments a JOIN cp_field_inbox_items i ON i.cemetery_uid = a.cemetery_uid AND i.uid = a.item_uid
                   WHERE a.cemetery_uid = OLD.cemetery_uid AND a.object_uid = OLD.uid AND i.status <> 'REJECTED') THEN
        RAISE EXCEPTION 'cp_storage_objects: obiectul este legat de un item Field Inbox activ' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS cp_storage_objects_inbox_guard ON cp_storage_objects;
CREATE TRIGGER cp_storage_objects_inbox_guard BEFORE UPDATE ON cp_storage_objects FOR EACH ROW EXECUTE FUNCTION cp_storage_objects_inbox_guard();

-- ---------------------------------------------------------------------------------------------------- RLS + grants minime (fără DELETE/TRUNCATE; fără UPDATE pe coloanele de conținut)
ALTER TABLE cp_field_inbox_items ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS cp_tenant ON cp_field_inbox_items;
CREATE POLICY cp_tenant ON cp_field_inbox_items TO cimitir_cp_app USING (cp_org_ok(organization_uid) AND cp_cem_ok(cemetery_uid))
    WITH CHECK (cp_org_ok(organization_uid) AND cp_cem_ok(cemetery_uid));
ALTER TABLE cp_field_inbox_attachments ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS cp_tenant ON cp_field_inbox_attachments;
CREATE POLICY cp_tenant ON cp_field_inbox_attachments TO cimitir_cp_app USING (cp_org_ok(organization_uid) AND cp_cem_ok(cemetery_uid))
    WITH CHECK (cp_org_ok(organization_uid) AND cp_cem_ok(cemetery_uid));

GRANT SELECT, INSERT ON cp_field_inbox_items TO cimitir_cp_app;
GRANT UPDATE (status, reviewed_at, reviewed_by, imported_at, imported_by, rejection_reason, updated_at) ON cp_field_inbox_items TO cimitir_cp_app;
GRANT SELECT, INSERT ON cp_field_inbox_attachments TO cimitir_cp_app;
