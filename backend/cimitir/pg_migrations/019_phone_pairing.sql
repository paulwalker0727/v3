-- 019: Phone Pairing v1 — asocierea explicită a unui telefon (device MOBILE) cu UN cimitir. Aditiv; 001–018 rămân NESCHIMBATE.
-- Reutilizează tabelele canonice: device_pairing_codes (cod, DOAR hash, device_type='MOBILE'), devices (device_type='MOBILE'), device_cemetery_bindings (cheia = doar hash).
-- Nu adaugă nicio putere nouă telefonului: legătura MOBILE nu are emitent de bootstrap, nu poate schimba statusul itemurilor (018) și nu poate emite/revoca nimic.
-- Schema-agnostic (fără `public`), idempotent.

-- ---------------------------------------------------------------------------------------------------- rate limiting (contor atomic, pe găleată = hash(IP) / hash(client))
-- Încercarea se numără ÎNAINTE de verificarea codului (UPSERT atomic), deci plafonul ține și sub concurență; succesul returnează încercarea (refund).
CREATE TABLE IF NOT EXISTS cp_pairing_attempts (
    bucket        text PRIMARY KEY CHECK (length(bucket) BETWEEN 8 AND 128),
    window_start  timestamptz NOT NULL DEFAULT now(),
    attempts      integer NOT NULL DEFAULT 0 CHECK (attempts >= 0)
);
ALTER TABLE cp_pairing_attempts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS cp_pairing_bucket ON cp_pairing_attempts;
CREATE POLICY cp_pairing_bucket ON cp_pairing_attempts TO cimitir_cp_app
    USING (bucket = ANY (string_to_array(COALESCE(current_setting('cp.pair_buckets', true), ''), ',')))
    WITH CHECK (bucket = ANY (string_to_array(COALESCE(current_setting('cp.pair_buckets', true), ''), ',')));
GRANT SELECT, INSERT, UPDATE ON cp_pairing_attempts TO cimitir_cp_app;

-- ---------------------------------------------------------------------------------------------------- coduri de pairing MOBILE
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'device_pairing_codes_type_check' AND conrelid = 'device_pairing_codes'::regclass) THEN
        ALTER TABLE device_pairing_codes ADD CONSTRAINT device_pairing_codes_type_check CHECK (device_type IN ('MAIN', 'MOBILE')) NOT VALID;
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS device_pairing_codes_mobile_open_idx ON device_pairing_codes (cemetery_uid) WHERE device_type = 'MOBILE' AND used_at IS NULL;

-- Cod MOBILE: obligatoriu legat de un cimitir, TTL <= 30 min, hash sha256 hex, folosibil o singură dată; restul rândului e imutabil. Codurile MAIN rămân neatinse.
CREATE OR REPLACE FUNCTION cp_mobile_pairing_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.device_type <> 'MOBILE' THEN RETURN NEW; END IF;
        IF NEW.cemetery_uid IS NULL OR NEW.used_at IS NOT NULL OR NEW.used_by_binding_uid IS NOT NULL OR NEW.used_bootstrap_id IS NOT NULL THEN
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
    IF OLD.used_at IS NOT NULL OR NEW.used_at IS NULL OR NEW.used_by_binding_uid IS NULL OR NEW.used_bootstrap_id IS NOT NULL THEN
        RAISE EXCEPTION 'device_pairing_codes: cod MOBILE deja folosit sau consum invalid' USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.expires_at <= now() THEN
        RAISE EXCEPTION 'device_pairing_codes: cod MOBILE expirat' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS cp_mobile_pairing_guard ON device_pairing_codes;
CREATE TRIGGER cp_mobile_pairing_guard BEFORE INSERT OR UPDATE ON device_pairing_codes FOR EACH ROW EXECUTE FUNCTION cp_mobile_pairing_guard();

-- ---------------------------------------------------------------------------------------------------- revocarea e definitivă
CREATE OR REPLACE FUNCTION cp_binding_revocation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.revoked_at IS NOT NULL AND (NEW.revoked_at IS NULL OR NEW.active OR NEW.sync_key_hash IS DISTINCT FROM OLD.sync_key_hash
                                       OR NEW.cemetery_uid IS DISTINCT FROM OLD.cemetery_uid OR NEW.device_uid IS DISTINCT FROM OLD.device_uid) THEN
        RAISE EXCEPTION 'device_cemetery_bindings: o legătură revocată nu se reactivează' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS cp_binding_revocation_guard ON device_cemetery_bindings;
CREATE TRIGGER cp_binding_revocation_guard BEFORE UPDATE ON device_cemetery_bindings FOR EACH ROW EXECUTE FUNCTION cp_binding_revocation_guard();

-- ---------------------------------------------------------------------------------------------------- telefonul nu poate deveni emitent de bootstrap
-- SECURITY DEFINER (search_path fixat): rezultatul nu depinde de contextul RLS al apelantului (fără fail-open când contextul lipsește).
CREATE OR REPLACE FUNCTION cp_binding_issuers_no_mobile() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM device_cemetery_bindings b JOIN devices d ON d.device_uid = b.device_uid AND d.organization_uid = b.organization_uid
               WHERE b.binding_uid = NEW.binding_uid AND d.device_type = 'MOBILE') THEN
        RAISE EXCEPTION 'cp_binding_issuers: un dispozitiv MOBILE nu poate emite/rula bootstrap' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
DO $$ BEGIN
    EXECUTE format('ALTER FUNCTION cp_binding_issuers_no_mobile() SET search_path = %I, pg_catalog', current_schema());
END $$;
REVOKE ALL ON FUNCTION cp_binding_issuers_no_mobile() FROM PUBLIC;
DROP TRIGGER IF EXISTS cp_binding_issuers_no_mobile ON cp_binding_issuers;
CREATE TRIGGER cp_binding_issuers_no_mobile BEFORE INSERT OR UPDATE ON cp_binding_issuers FOR EACH ROW EXECUTE FUNCTION cp_binding_issuers_no_mobile();
