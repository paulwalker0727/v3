-- 016: 6B — fix pentru code review (HIGH): (1) separarea drepturilor delete/cleanup, (2) cursa cleanup vs upload/finalizare. 015 rămâne NESCHIMBATĂ.
-- (1) cleanup = operație de serviciu, ÎNGUSTĂ: doar funcții SECURITY DEFINER executabile de rolul intern cimitir_storage_cleanup (NOLOGIN); cimitir_cp_app pierde DELETE pe tabel
--     și nu poate rula funcțiile. Ștergerea normală a unui owner rămâne UPDATE ... -> DELETED sub RLS (cp_tenant), fără DELETE SQL.
-- (2) automatul de stări e impus de DB (trigger + UPDATE condiționat pe rând blocat): STAGING(PENDING/UPLOADED/FAILED) -> READY și STAGING -> CLEANING -> DELETED
--     sunt mutual exclusive; CLEANING nu poate ajunge niciodată READY, iar READY/IMPORTED nu pot fi revendicate.
-- Schema-agnostic (current_schema()), idempotent. Rolul de login al worker-ului de cleanup se creează de operator (IN ROLE cimitir_storage_cleanup), parola nu e în Git.

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cimitir_storage_cleanup') THEN
        CREATE ROLE cimitir_storage_cleanup NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB;
    END IF;
EXCEPTION WHEN duplicate_object OR unique_violation THEN NULL;
END $$;

-- ---------------------------------------------------------------------------------------------------- coloane + stări noi (CLEANING = revendicat de cleanup; IMPORTED = rezervat Field Inbox)
ALTER TABLE cp_storage_objects ADD COLUMN IF NOT EXISTS cleanup_token uuid;
ALTER TABLE cp_storage_objects ADD COLUMN IF NOT EXISTS cleanup_prev_status text;
ALTER TABLE cp_storage_objects ADD COLUMN IF NOT EXISTS cleanup_claimed_at timestamptz;

DO $$
DECLARE c TEXT;
BEGIN
    FOR c IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'cp_storage_objects'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%PENDING%' AND conname <> 'cp_storage_objects_status_check2'
    LOOP
        EXECUTE format('ALTER TABLE cp_storage_objects DROP CONSTRAINT %I', c);
    END LOOP;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'cp_storage_objects'::regclass AND conname = 'cp_storage_objects_status_check2') THEN
        ALTER TABLE cp_storage_objects ADD CONSTRAINT cp_storage_objects_status_check2
            CHECK (status IN ('PENDING', 'UPLOADED', 'READY', 'FAILED', 'DELETED', 'CLEANING', 'IMPORTED'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'cp_storage_objects'::regclass AND conname = 'cp_storage_objects_cleaning_claim_check') THEN
        ALTER TABLE cp_storage_objects ADD CONSTRAINT cp_storage_objects_cleaning_claim_check
            CHECK (status <> 'CLEANING' OR (cleanup_token IS NOT NULL AND cleanup_claimed_at IS NOT NULL AND cleanup_prev_status IN ('PENDING', 'UPLOADED', 'FAILED', 'DELETED')));
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS cp_storage_objects_cleaning_idx ON cp_storage_objects (cemetery_uid, cleanup_claimed_at) WHERE status = 'CLEANING';

-- ---------------------------------------------------------------------------------------------------- drepturi: cimitir_cp_app nu mai are DELETE (cleanup nu trece prin DELETE arbitrar)
REVOKE DELETE ON cp_storage_objects FROM cimitir_cp_app;

-- ---------------------------------------------------------------------------------------------------- automatul de stări, impus de DB
-- Tranzițiile care implică CLEANING (și crearea de rânduri non-PENDING) sunt permise doar codului care rulează ca PROPRIETARUL tabelului,
-- adică funcțiile SECURITY DEFINER de mai jos; cimitir_cp_app (non-proprietar) nu le poate face. Nimeni nu poate trece CLEANING -> READY/IMPORTED sau READY/IMPORTED -> CLEANING.
CREATE OR REPLACE FUNCTION cp_storage_objects_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_ok boolean;
    v_owner boolean := current_user = (SELECT pg_get_userbyid(c.relowner) FROM pg_class c WHERE c.oid = TG_RELID);
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NOT v_owner AND (NEW.status <> 'PENDING' OR NEW.cleanup_token IS NOT NULL OR NEW.cleanup_prev_status IS NOT NULL OR NEW.cleanup_claimed_at IS NOT NULL) THEN
            RAISE EXCEPTION 'cp_storage_objects: rând nou doar PENDING' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status = 'CLEANING' AND NEW.status IN ('READY', 'IMPORTED') THEN
        RAISE EXCEPTION 'cp_storage_objects: CLEANING -> % interzis', NEW.status USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'CLEANING' AND OLD.status IN ('READY', 'IMPORTED') THEN
        RAISE EXCEPTION 'cp_storage_objects: % nu poate fi revendicat de cleanup', OLD.status USING ERRCODE = 'check_violation';
    END IF;
    IF NOT v_owner THEN
        IF (NEW.cemetery_uid, NEW.uid, NEW.organization_uid, NEW.storage_path, NEW.sha256, NEW.size, NEW.mime, NEW.kind, NEW.filename)
           IS DISTINCT FROM (OLD.cemetery_uid, OLD.uid, OLD.organization_uid, OLD.storage_path, OLD.sha256, OLD.size, OLD.mime, OLD.kind, OLD.filename)
           OR (NEW.cleanup_token, NEW.cleanup_prev_status, NEW.cleanup_claimed_at) IS DISTINCT FROM (OLD.cleanup_token, OLD.cleanup_prev_status, OLD.cleanup_claimed_at) THEN
            RAISE EXCEPTION 'cp_storage_objects: câmpuri imutabile' USING ERRCODE = 'check_violation';
        END IF;
        v_ok := CASE OLD.status
                WHEN 'PENDING'  THEN NEW.status IN ('PENDING', 'UPLOADED', 'READY', 'FAILED', 'DELETED')
                WHEN 'UPLOADED' THEN NEW.status IN ('UPLOADED', 'READY', 'FAILED', 'DELETED')
                WHEN 'FAILED'   THEN NEW.status IN ('FAILED', 'UPLOADED', 'READY', 'DELETED')
                WHEN 'READY'    THEN NEW.status IN ('READY', 'DELETED', 'IMPORTED')
                WHEN 'IMPORTED' THEN NEW.status = 'IMPORTED'
                WHEN 'DELETED'  THEN NEW.status = 'DELETED'
                ELSE false
              END;
        IF NOT v_ok THEN
            RAISE EXCEPTION 'cp_storage_objects: tranziția % -> % nu este permisă', OLD.status, NEW.status USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS cp_storage_objects_guard ON cp_storage_objects;
CREATE TRIGGER cp_storage_objects_guard BEFORE INSERT OR UPDATE ON cp_storage_objects FOR EACH ROW EXECUTE FUNCTION cp_storage_objects_guard();

-- ---------------------------------------------------------------------------------------------------- funcții înguste de cleanup (SECURITY DEFINER, search_path fixat mai jos)
-- apărarea de tenant: perechea (organizație, cimitir) trebuie să existe exact așa; calea Storage se construiește AICI, nu vine de la apelant.
CREATE OR REPLACE FUNCTION cp_storage_cleanup_scope(p_org uuid, p_cem uuid) RETURNS void LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
    IF p_org IS NULL OR p_cem IS NULL OR NOT EXISTS (SELECT 1 FROM cemeteries WHERE uid = p_cem AND organization_uid = p_org) THEN
        RAISE EXCEPTION 'cleanup: organizație/cimitir nepotrivite' USING ERRCODE = 'insufficient_privilege';
    END IF;
END $$;

-- revendică atomic rânduri STAGING abandonate (PENDING/UPLOADED/FAILED mai vechi de prag) și rânduri CLEANING cu lease expirat (worker căzut). READY/IMPORTED/DELETED nu apar niciodată.
CREATE OR REPLACE FUNCTION cp_storage_cleanup_claim(p_org uuid, p_cem uuid, p_token uuid, p_older_than_s double precision, p_limit integer DEFAULT 100, p_lease_s double precision DEFAULT 900)
RETURNS TABLE (out_uid text, out_storage_path text, out_prev_status text) LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
    PERFORM cp_storage_cleanup_scope(p_org, p_cem);
    IF p_token IS NULL OR p_older_than_s IS NULL OR p_older_than_s < 0 OR p_limit IS NULL OR p_limit < 1 OR p_lease_s IS NULL OR p_lease_s <= 0 THEN
        RAISE EXCEPTION 'cleanup: parametri invalizi' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN QUERY
    WITH cand AS (
        SELECT o.cemetery_uid AS c_cem, o.uid AS c_uid FROM cp_storage_objects o
        WHERE o.cemetery_uid = p_cem AND o.organization_uid = p_org
          AND ((o.status IN ('PENDING', 'UPLOADED', 'FAILED') AND o.updated_at < now() - make_interval(secs => p_older_than_s))
               OR (o.status = 'CLEANING' AND o.cleanup_claimed_at < now() - make_interval(secs => p_lease_s)))
        ORDER BY o.updated_at, o.uid LIMIT p_limit FOR UPDATE SKIP LOCKED)
    UPDATE cp_storage_objects t
       SET cleanup_prev_status = CASE WHEN t.status = 'CLEANING' THEN t.cleanup_prev_status ELSE t.status END,
           status = 'CLEANING', cleanup_token = p_token, cleanup_claimed_at = now()
      FROM cand WHERE t.cemetery_uid = cand.c_cem AND t.uid = cand.c_uid
    RETURNING t.uid, t.storage_path, t.cleanup_prev_status;
END $$;

-- revendică un obiect din Storage identificat prin uid: fără rând (se creează atomic un rând-santinelă CLEANING; uid-ul rămâne „ars”) sau cu rând DELETED învechit (rămășiță).
-- Orice alt rând (PENDING/UPLOADED/FAILED/READY/IMPORTED/CLEANING) -> NULL. Vârsta obiectului vine de la apelant (Storage), eligibilitatea o decide DB.
CREATE OR REPLACE FUNCTION cp_storage_cleanup_claim_object(p_org uuid, p_cem uuid, p_uid text, p_token uuid, p_older_than_s double precision, p_object_age_s double precision)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_path text;
BEGIN
    PERFORM cp_storage_cleanup_scope(p_org, p_cem);
    IF p_token IS NULL OR p_older_than_s IS NULL OR p_older_than_s < 0 OR p_object_age_s IS NULL THEN
        RAISE EXCEPTION 'cleanup: parametri invalizi' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_uid IS NULL OR p_uid !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' OR p_object_age_s < p_older_than_s THEN
        RETURN NULL;
    END IF;
    v_path := 'field/' || p_org::text || '/' || p_cem::text || '/' || p_uid;
    INSERT INTO cp_storage_objects (cemetery_uid, uid, organization_uid, kind, filename, mime, size, sha256, storage_path, status, error,
                                    cleanup_token, cleanup_prev_status, cleanup_claimed_at)
    VALUES (p_cem, p_uid, p_org, 'document', 'orphan-object', 'application/pdf', 1, repeat('0', 64), v_path, 'CLEANING', 'obiect fără rând (cleanup)',
            p_token, 'DELETED', now())
    ON CONFLICT (cemetery_uid, uid) DO NOTHING;
    IF FOUND THEN
        RETURN v_path;
    END IF;
    UPDATE cp_storage_objects SET status = 'CLEANING', cleanup_prev_status = 'DELETED', cleanup_token = p_token, cleanup_claimed_at = now()
     WHERE cemetery_uid = p_cem AND organization_uid = p_org AND uid = p_uid AND status = 'DELETED' AND updated_at < now() - make_interval(secs => p_older_than_s)
    RETURNING storage_path INTO v_path;
    RETURN v_path;
END $$;

-- închide cleanup-ul DOAR pentru rândul revendicat cu tokenul exact (după ștergerea obiectului din Storage)
CREATE OR REPLACE FUNCTION cp_storage_cleanup_finish(p_org uuid, p_cem uuid, p_uid text, p_token uuid) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
    PERFORM cp_storage_cleanup_scope(p_org, p_cem);
    UPDATE cp_storage_objects SET status = 'DELETED', error = 'abandonat (cleanup)', updated_at = now()
     WHERE cemetery_uid = p_cem AND organization_uid = p_org AND uid = p_uid AND status = 'CLEANING' AND cleanup_token = p_token;
    RETURN FOUND;
END $$;

-- ștergerea din Storage a eșuat: rândul revine la starea de dinainte de claim (reîncercare la următorul cleanup)
CREATE OR REPLACE FUNCTION cp_storage_cleanup_release(p_org uuid, p_cem uuid, p_uid text, p_token uuid) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
    PERFORM cp_storage_cleanup_scope(p_org, p_cem);
    UPDATE cp_storage_objects SET status = cleanup_prev_status
     WHERE cemetery_uid = p_cem AND organization_uid = p_org AND uid = p_uid AND status = 'CLEANING' AND cleanup_token = p_token;
    RETURN FOUND;
END $$;

DO $$
DECLARE f TEXT; r TEXT;
BEGIN
    FOREACH f IN ARRAY ARRAY['cp_storage_cleanup_scope(uuid, uuid)',
                             'cp_storage_cleanup_claim(uuid, uuid, uuid, double precision, integer, double precision)',
                             'cp_storage_cleanup_claim_object(uuid, uuid, text, uuid, double precision, double precision)',
                             'cp_storage_cleanup_finish(uuid, uuid, text, uuid)',
                             'cp_storage_cleanup_release(uuid, uuid, text, uuid)']
    LOOP
        EXECUTE format('ALTER FUNCTION %s SET search_path = %I, pg_catalog', f, current_schema());
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f);
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM cimitir_cp_app', f);
        FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role']
        LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
                EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I', f, r);
            END IF;
        END LOOP;
        IF f NOT LIKE 'cp_storage_cleanup_scope%' THEN
            EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO cimitir_storage_cleanup', f);
        END IF;
    END LOOP;
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO cimitir_storage_cleanup', current_schema());
END $$;
