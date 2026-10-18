-- Rollback 025: elimină doar funcțiile de provizionare. NU șterge organizații, cimitire sau roluri deja create (date). Rulează cu rol administrativ.
DROP FUNCTION IF EXISTS cp_admin_provision(uuid, uuid, text, uuid, text);
DROP FUNCTION IF EXISTS cp_admin_registry_lookup(uuid, uuid, uuid);
DROP FUNCTION IF EXISTS cp_admin_is_provisioner(uuid);
