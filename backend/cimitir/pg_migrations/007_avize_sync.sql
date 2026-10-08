-- 007_avize_sync.sql — Pasul 6: amenajari_avize intră în sincronizarea pe entități (Etapa 6), ca un aviz creat în MAIN
-- să existe pe server și să primească documente de pe telefon (același uid). Repetabilă.
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['amenajari_avize'] LOOP
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 0', t);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS change_seq BIGINT', t);
        EXECUTE format('UPDATE %I SET version = 1, change_seq = nextval(''sync_change_seq'') WHERE change_seq IS NULL', t);
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_%1$s_change_seq ON %1$I(change_seq)', t);
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%1$s_sync_touch ON %1$I', t);
        EXECUTE format('CREATE TRIGGER trg_%1$s_sync_touch BEFORE INSERT OR UPDATE ON %1$I FOR EACH ROW EXECUTE FUNCTION cimitir_sync_touch()', t);
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%1$s_sync_tomb ON %1$I', t);
        EXECUTE format('CREATE TRIGGER trg_%1$s_sync_tomb AFTER DELETE ON %1$I FOR EACH ROW EXECUTE FUNCTION cimitir_sync_tombstone()', t);
    END LOOP;
END $$;
