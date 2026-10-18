-- Rollback 026: elimină cererile de înregistrare și funcțiile lor. NU șterge organizații/cimitire create prin aprobare. Cererile în așteptare se pierd. Rulează cu rol administrativ.
DROP FUNCTION IF EXISTS cp_regreq_unclaim(uuid, text);
DROP FUNCTION IF EXISTS cp_regreq_claim(uuid, text);
DROP FUNCTION IF EXISTS cp_regreq_poll(uuid, text);
DROP FUNCTION IF EXISTS cp_regreq_decide(uuid, uuid, boolean);
DROP FUNCTION IF EXISTS cp_regreq_list(uuid);
DROP FUNCTION IF EXISTS cp_regreq_create(text, uuid, text, uuid, text, text);
DROP FUNCTION IF EXISTS cp_regreq_visible(uuid, uuid);
DROP TABLE IF EXISTS cp_registry_requests;
