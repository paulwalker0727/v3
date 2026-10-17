-- Rollback 023: elimină autentificarea administrativă Cloud. NU șterge identități/organizații/cimitire/legături. Revocările de coduri se pierd (coloanele dispar).
-- Rulează cu un rol administrativ. După rollback, Cloud-ul revine la comportamentul 022 (fără /api/v1/admin).
DROP FUNCTION IF EXISTS cp_admin_status();
DROP FUNCTION IF EXISTS cp_admin_session_identity(text);
DROP FUNCTION IF EXISTS cp_admin_session_revoke(text);
DROP FUNCTION IF EXISTS cp_admin_bootstrap(text,text,text,uuid,text,uuid,text);
DROP FUNCTION IF EXISTS cp_admin_recovery_issue(text,text,integer);
DROP FUNCTION IF EXISTS cp_admin_recover(text,text,text);
DROP TABLE IF EXISTS cp_admin_recovery;
DROP TABLE IF EXISTS cp_admin_sessions;
DROP TABLE IF EXISTS cp_admin_credentials;
DROP TABLE IF EXISTS cp_admin_state;
DROP FUNCTION IF EXISTS cp_admin_email();
UPDATE device_pairing_codes SET expires_at = LEAST(expires_at, now()) WHERE revoked_at IS NOT NULL AND device_type <> 'MOBILE';
ALTER TABLE device_pairing_codes DISABLE TRIGGER cp_mobile_pairing_guard;
UPDATE device_pairing_codes SET used_at = COALESCE(used_at, revoked_at), used_by_binding_uid = COALESCE(used_by_binding_uid, '00000000-0000-0000-0000-000000000000') WHERE revoked_at IS NOT NULL AND used_at IS NULL AND device_type = 'MOBILE';
ALTER TABLE device_pairing_codes ENABLE TRIGGER cp_mobile_pairing_guard;
ALTER TABLE device_pairing_codes DROP COLUMN IF EXISTS revoked_at;
ALTER TABLE device_pairing_codes DROP COLUMN IF EXISTS revoked_by;
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
