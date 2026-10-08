-- 012: legăturile prin id surogat (contracte→locuri, plăți→contracte etc.) rămân în ACELAȘI cimitir (cetățeni → aceeași organizație).
-- FK-urile simple devin compuse; necesită PostgreSQL 15+ (SET NULL (coloană)). Începe cu PREFLIGHT explicit.

-- PREFLIGHT 012: copil și părinte trebuie să aparțină aceluiași cimitir / aceleiași organizații.
DO $$
DECLARE
    p RECORD;
    n BIGINT;
    problems TEXT := '';
BEGIN
    FOR p IN SELECT * FROM (VALUES
        ('contracte','id_loc','locuri_de_veci','cemetery_uid'),
        ('contracte','concesionar_id','concesionari','cemetery_uid'),
        ('inhumati','contract_id','contracte','cemetery_uid'),
        ('inhumati','id_loc','locuri_de_veci','cemetery_uid'),
        ('plati','contract_id','contracte','cemetery_uid'),
        ('qr_generations','loc_id','locuri_de_veci','cemetery_uid'),
        ('cereri_concesionare','concesionar_id','concesionari','cemetery_uid'),
        ('cereri_concesionare','contract_id','contracte','cemetery_uid'),
        ('cereri_concesionare','citizen_id','citizens','organization_uid'),
        ('citizen_resets','citizen_id','citizens','organization_uid')) AS v(child, col, parent, scope)
    LOOP
        CONTINUE WHEN to_regclass(p.child) IS NULL OR to_regclass(p.parent) IS NULL;
        EXECUTE format('SELECT count(*) FROM %I c WHERE c.%I IS NOT NULL AND NOT EXISTS '
                       '(SELECT 1 FROM %I q WHERE q.id = c.%I AND q.%I IS NOT DISTINCT FROM c.%I)',
                       p.child, p.col, p.parent, p.col, p.scope, p.scope) INTO n;
        IF n > 0 THEN
            problems := problems || format(E'\n  - %s.%s -> %s: %s rânduri cu părinte în alt %s (sau lipsă)',
                                           p.child, p.col, p.parent, n, CASE p.scope WHEN 'cemetery_uid' THEN 'cimitir' ELSE 'organizație' END);
        END IF;
    END LOOP;
    IF problems <> '' THEN
        RAISE EXCEPTION E'PREFLIGHT 012 EȘUAT — migrarea oprită fără modificări:%', problems;
    END IF;
END $$;

DO $$
DECLARE
    p RECORD;
    fk RECORD;
    uq TEXT;
    del_sql TEXT;
BEGIN
    FOR p IN SELECT * FROM (VALUES
        ('contracte','id_loc','locuri_de_veci','cemetery_uid'),
        ('contracte','concesionar_id','concesionari','cemetery_uid'),
        ('inhumati','contract_id','contracte','cemetery_uid'),
        ('inhumati','id_loc','locuri_de_veci','cemetery_uid'),
        ('plati','contract_id','contracte','cemetery_uid'),
        ('qr_generations','loc_id','locuri_de_veci','cemetery_uid'),
        ('cereri_concesionare','concesionar_id','concesionari','cemetery_uid'),
        ('cereri_concesionare','contract_id','contracte','cemetery_uid'),
        ('cereri_concesionare','citizen_id','citizens','organization_uid'),
        ('citizen_resets','citizen_id','citizens','organization_uid')) AS v(child, col, parent, scope)
    LOOP
        CONTINUE WHEN to_regclass(p.child) IS NULL OR to_regclass(p.parent) IS NULL;
        uq := p.parent || '_' || p.scope || '_id_uq';
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE connamespace = current_schema()::regnamespace AND conname = uq) THEN
            EXECUTE format('ALTER TABLE %I ADD CONSTRAINT %I UNIQUE (%I, id)', p.parent, uq, p.scope);
        END IF;
        FOR fk IN SELECT con.conname, con.confupdtype, con.confdeltype FROM pg_constraint con
                   WHERE con.contype = 'f' AND con.conrelid = p.child::regclass AND con.confrelid = p.parent::regclass
                     AND con.conkey = ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid = p.child::regclass AND attname = p.col)]
        LOOP
            EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I', p.child, fk.conname);
            del_sql := CASE fk.confdeltype WHEN 'c' THEN 'CASCADE' WHEN 'n' THEN format('SET NULL (%I)', p.col)
                                           WHEN 'r' THEN 'RESTRICT' ELSE 'NO ACTION' END;
            EXECUTE format('ALTER TABLE %I ADD CONSTRAINT %I FOREIGN KEY (%I, %I) REFERENCES %I (%I, id) ON UPDATE %s ON DELETE %s',
                           p.child, fk.conname || '_tenant', p.scope, p.col, p.parent, p.scope,
                           CASE fk.confupdtype WHEN 'c' THEN 'CASCADE' WHEN 'r' THEN 'RESTRICT' ELSE 'NO ACTION' END, del_sql);
        END LOOP;
    END LOOP;
END $$;
