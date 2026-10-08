-- 006_mobile_upload_tokens.sql — Pasul 6: token temporar (QR MAIN -> telefon) pentru încărcarea unui document la O singură entitate.
-- Se păstrează DOAR sha256(secret); tokenul în clar nu se stochează. Repetabilă (IF NOT EXISTS).
CREATE TABLE IF NOT EXISTS mobile_upload_tokens (
    token_hash  TEXT PRIMARY KEY,
    tid         TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    entity_uid  TEXT,
    created_by  TEXT,
    created_at  BIGINT NOT NULL,
    expires_at  BIGINT NOT NULL,
    max_uses    INTEGER NOT NULL DEFAULT 1,
    uses        INTEGER NOT NULL DEFAULT 0,
    revoked     INTEGER NOT NULL DEFAULT 0,
    last_doc_id INTEGER,
    used_at     BIGINT
);
CREATE INDEX IF NOT EXISTS idx_mobile_tokens_tid ON mobile_upload_tokens(tid);
CREATE INDEX IF NOT EXISTS idx_mobile_tokens_exp ON mobile_upload_tokens(expires_at);
