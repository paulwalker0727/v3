-- Rollback 024: elimină metadatele documentelor sincronizate (Sync v2). NU șterge nimic din Storage și nu atinge `documents`/`photos` din MAIN.
-- ATENȚIE: metadatele din cloud se pierd; octeții din bucket rămân (recuperabili după calea docs/<org>/<cemetery>/<uid>). Fă mai întâi backup:
--   CREATE TABLE sync_v2_documents_backup AS SELECT * FROM sync_v2_documents;   -- opțional, înainte de rollback
-- Rulează cu un rol administrativ, doar după aprobare.
DROP TRIGGER IF EXISTS trg_sync_v2_documents_touch ON sync_v2_documents;
DROP TABLE IF EXISTS sync_v2_documents;
DROP FUNCTION IF EXISTS cimitir_sync_v2_documents_touch();
