-- 005_attachments.sql — arhivă generală de atașamente: `documents` devine modelul generic (nu se creează un tabel paralel).
-- entity_type + entity_id exista deja (loc|concesionar|contract|inhumat|plata|aviz|cerere); doc_type = clasificarea documentului.
-- Coloane noi: descriere, nume original, mime, cine a încărcat, sursa (web|telefon|main). Octeții rămân în Storage (media_objects).
-- Repetabilă (IF NOT EXISTS).
ALTER TABLE documents ADD COLUMN IF NOT EXISTS description       TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS original_filename TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS mime_type         TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS uploaded_by       TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source            TEXT;
CREATE INDEX IF NOT EXISTS idx_documents_type ON documents(entity_type, doc_type);
