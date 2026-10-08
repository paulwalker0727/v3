-- 011: chei și FK compatibile cu mai multe cimitire (același cod_loc în cimitire diferite, nu și duplicat în același cimitir).
-- Începe cu PREFLIGHT: dacă datele nu permit conversia, se oprește cu mesaj explicit, fără nicio modificare (migrarea e o singură tranzacție).
-- Aplicația trebuie să folosească ON CONFLICT (cemetery_uid, ...) începând cu acest release.

-- PREFLIGHT 011: nicio modificare; doar verificări. Orice problemă oprește migrarea cu un mesaj explicit.
DO $$
DECLARE
    t TEXT;
    n BIGINT;
    fk RECORD;
    problems TEXT := '';
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'locuri_de_veci','concesionari','contracte','inhumati','plati','amenajari_avize','sectoare','photos','documents',
        'media_objects','loc_extra','guides','rows_ghid','loc_holders','qr_generations','ai_scan_history',
        'cereri_concesionare','mobile_upload_tokens','sync_ops','sync_tombstones','sync_conflicts']
    LOOP
        IF to_regclass(t) IS NULL THEN CONTINUE; END IF;
        EXECUTE format('SELECT count(*) FROM %I WHERE cemetery_uid IS NULL OR organization_uid IS NULL', t) INTO n;
        IF n > 0 THEN problems := problems || format(E'\n  - %s: %s rânduri fără organization_uid/cemetery_uid', t, n); END IF;
    END LOOP;
    IF to_regclass('citizens') IS NOT NULL THEN
        SELECT count(*) INTO n FROM citizens WHERE organization_uid IS NULL;
        IF n > 0 THEN problems := problems || format(E'\n  - citizens: %s rânduri fără organization_uid', n); END IF;
        SELECT count(*) INTO n FROM (SELECT 1 FROM citizens GROUP BY organization_uid, lower(email) HAVING count(*) > 1) d;
        IF n > 0 THEN problems := problems || format(E'\n  - citizens: %s e-mailuri duplicate (lower(email)) în aceeași organizație', n); END IF;
    END IF;
    SELECT count(*) INTO n FROM (SELECT 1 FROM locuri_de_veci GROUP BY cemetery_uid, cod_loc HAVING count(*) > 1) d;
    IF n > 0 THEN problems := problems || format(E'\n  - locuri_de_veci: %s cod_loc duplicate în același cimitir', n); END IF;
    IF to_regclass('sectoare') IS NOT NULL THEN
        SELECT count(*) INTO n FROM (SELECT 1 FROM sectoare GROUP BY cemetery_uid, id HAVING count(*) > 1) d;
        IF n > 0 THEN problems := problems || format(E'\n  - sectoare: %s id duplicate în același cimitir', n); END IF;
    END IF;
    FOR fk IN SELECT con.conrelid::regclass::text AS tbl, att.attname AS col
                FROM pg_constraint con
                JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = con.conkey[1]
               WHERE con.contype = 'f' AND con.confrelid = 'locuri_de_veci'::regclass AND array_length(con.conkey, 1) = 1
                 AND att.attname = 'cod_loc'
    LOOP
        EXECUTE format('SELECT count(*) FROM %s c WHERE c.cod_loc IS NOT NULL AND NOT EXISTS '
                       '(SELECT 1 FROM locuri_de_veci l WHERE l.cemetery_uid = c.cemetery_uid AND l.cod_loc = c.cod_loc)', fk.tbl) INTO n;
        IF n > 0 THEN problems := problems || format(E'\n  - %s: %s rânduri cu cod_loc care nu aparține aceluiași cimitir (FK tenant-safe ar deveni invalid)', fk.tbl, n); END IF;
    END LOOP;
    IF problems <> '' THEN
        RAISE EXCEPTION E'PREFLIGHT 011 EȘUAT — migrarea oprită fără modificări:%', problems;
    END IF;
END $$;

DO $$
DECLARE
    t TEXT;
    n BIGINT;
    c RECORD;
    fk RECORD;
    fks JSONB := '[]'::jsonb;
BEGIN
    -- 1. tenant NOT NULL (cu verificare explicită a backfill-ului)
    FOREACH t IN ARRAY ARRAY[
        'locuri_de_veci','concesionari','contracte','inhumati','plati','amenajari_avize','sectoare','photos','documents',
        'media_objects','loc_extra','guides','rows_ghid','loc_holders','qr_generations','ai_scan_history',
        'cereri_concesionare','mobile_upload_tokens','sync_ops','sync_tombstones','sync_conflicts']
    LOOP
        IF to_regclass(t) IS NULL THEN CONTINUE; END IF;
        EXECUTE format('SELECT count(*) FROM %I WHERE cemetery_uid IS NULL OR organization_uid IS NULL', t) INTO n;
        IF n > 0 THEN RAISE EXCEPTION 'backfill incomplet pe %: % rânduri fără tenant', t, n; END IF;
        EXECUTE format('ALTER TABLE %I ALTER COLUMN cemetery_uid SET NOT NULL', t);
        EXECUTE format('ALTER TABLE %I ALTER COLUMN organization_uid SET NOT NULL', t);
    END LOOP;

    -- 2. locuri_de_veci: UNIQUE(cod_loc) -> UNIQUE(cemetery_uid, cod_loc); FK-urile pe cod_loc devin compuse
    FOR fk IN SELECT con.conname, con.conrelid::regclass::text AS tbl, att.attname AS col, con.confupdtype, con.confdeltype
                FROM pg_constraint con
                JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = con.conkey[1]
               WHERE con.contype = 'f' AND con.confrelid = 'locuri_de_veci'::regclass AND array_length(con.conkey, 1) = 1
    LOOP
        IF fk.col <> 'cod_loc' THEN RAISE NOTICE 'FK % pe %.% nu e pe cod_loc; neschimbat', fk.conname, fk.tbl, fk.col; CONTINUE; END IF;
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', fk.tbl, fk.conname);
        fks := fks || jsonb_build_array(jsonb_build_object('tbl', fk.tbl, 'name', fk.conname, 'upd', fk.confupdtype, 'del', fk.confdeltype));
    END LOOP;
    FOR c IN SELECT con.conname FROM pg_constraint con
              WHERE con.conrelid = 'locuri_de_veci'::regclass AND con.contype IN ('u', 'p')
                AND con.conkey = ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid = 'locuri_de_veci'::regclass AND attname = 'cod_loc')]
    LOOP
        EXECUTE format('ALTER TABLE locuri_de_veci DROP CONSTRAINT %I', c.conname);
    END LOOP;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE connamespace = current_schema()::regnamespace AND conname = 'locuri_de_veci_cemetery_cod_uq') THEN
        ALTER TABLE locuri_de_veci ADD CONSTRAINT locuri_de_veci_cemetery_cod_uq UNIQUE (cemetery_uid, cod_loc);
    END IF;
    FOR fk IN SELECT x->>'tbl' AS tbl, x->>'name' AS name, x->>'upd' AS upd, x->>'del' AS del FROM jsonb_array_elements(fks) x
    LOOP
        EXECUTE format('ALTER TABLE %s ADD CONSTRAINT %I FOREIGN KEY (cemetery_uid, cod_loc) REFERENCES locuri_de_veci (cemetery_uid, cod_loc) ON UPDATE %s ON DELETE %s',
                       fk.tbl, fk.name || '_tenant',
                       CASE fk.upd WHEN 'c' THEN 'CASCADE' WHEN 'n' THEN 'SET NULL' WHEN 'r' THEN 'RESTRICT' ELSE 'NO ACTION' END,
                       CASE fk.del WHEN 'c' THEN 'CASCADE' WHEN 'n' THEN 'SET NULL' WHEN 'r' THEN 'RESTRICT' ELSE 'NO ACTION' END);
    END LOOP;

    -- 3. chei primare/compuse pe tabelele care presupuneau un singur cimitir
    IF to_regclass('sectoare') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE connamespace = current_schema()::regnamespace AND conname = 'sectoare_cemetery_pkey') THEN
        FOR c IN SELECT conname FROM pg_constraint WHERE conrelid = 'sectoare'::regclass AND contype = 'p' LOOP
            EXECUTE format('ALTER TABLE sectoare DROP CONSTRAINT %I', c.conname); END LOOP;
        ALTER TABLE sectoare ADD CONSTRAINT sectoare_cemetery_pkey PRIMARY KEY (cemetery_uid, id);
    END IF;
    IF to_regclass('loc_extra') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE connamespace = current_schema()::regnamespace AND conname = 'loc_extra_cemetery_pkey') THEN
        FOR c IN SELECT conname FROM pg_constraint WHERE conrelid = 'loc_extra'::regclass AND contype = 'p' LOOP
            EXECUTE format('ALTER TABLE loc_extra DROP CONSTRAINT %I', c.conname); END LOOP;
        ALTER TABLE loc_extra ADD CONSTRAINT loc_extra_cemetery_pkey PRIMARY KEY (cemetery_uid, cod_loc);
    END IF;
    IF to_regclass('loc_holders') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE connamespace = current_schema()::regnamespace AND conname = 'loc_holders_cemetery_pkey') THEN
        FOR c IN SELECT conname FROM pg_constraint WHERE conrelid = 'loc_holders'::regclass AND contype = 'p' LOOP
            EXECUTE format('ALTER TABLE loc_holders DROP CONSTRAINT %I', c.conname); END LOOP;
        ALTER TABLE loc_holders ADD CONSTRAINT loc_holders_cemetery_pkey PRIMARY KEY (cemetery_uid, cod_loc, holder_hash);
    END IF;

    -- 4. cetățeni: e-mail unic per organizație (nu global)
    IF to_regclass('citizens') IS NOT NULL THEN
        FOR c IN SELECT conname FROM pg_constraint
                  WHERE conrelid = 'citizens'::regclass AND contype = 'u'
                    AND conkey = ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid = 'citizens'::regclass AND attname = 'email')]
        LOOP EXECUTE format('ALTER TABLE citizens DROP CONSTRAINT %I', c.conname); END LOOP;
        ALTER TABLE citizens ALTER COLUMN organization_uid SET NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS citizens_org_email_uq ON citizens (organization_uid, lower(email));
    END IF;
END $$;
