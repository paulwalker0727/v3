CREATE TABLE amenajari_avize (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cod_loc TEXT NOT NULL,
                nr_aviz TEXT NOT NULL,
                data_aviz TEXT,
                created_at TEXT DEFAULT (datetime('now')), lucrare TEXT,
                FOREIGN KEY(cod_loc) REFERENCES locuri_de_veci(cod_loc) ON DELETE CASCADE
            );
CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor_type TEXT, actor TEXT,
                action TEXT, target TEXT, detail TEXT
            );
CREATE TABLE cereri_concesionare (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT,
                status TEXT DEFAULT 'nou',
                cod_loc TEXT,
                nume TEXT,
                prenume TEXT,
                cnp TEXT,
                bi_ci TEXT,
                adresa TEXT,
                telefon TEXT,
                email TEXT,
                grad_rudenie TEXT,
                observatii TEXT,
                concesionar_id INTEGER,
                reviewed_at TEXT,
                review_note TEXT
            , cod_urmarire TEXT, sync_version INTEGER DEFAULT 0, acked INTEGER DEFAULT 0, citizen_id INTEGER, tip TEXT DEFAULT 'concesionare', payload TEXT, revision INTEGER DEFAULT 0, contract_id INTEGER, privacy_version TEXT, privacy_ack_at TEXT, closed_at TEXT, public_message TEXT);
CREATE TABLE citizens (
                id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE, email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL, full_name TEXT, created_at TEXT, active INTEGER DEFAULT 1
            );
CREATE TABLE concesionari (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nume TEXT,
                prenume TEXT,
                cnp TEXT,
                bi_ci TEXT,
                adresa TEXT,
                telefon TEXT,
                email TEXT,
                observatii TEXT
            );
CREATE TABLE contracte (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                numar_contract TEXT,
                data_contract TEXT,
                data_inceput TEXT,
                data_sfarsit TEXT,
                concesionar_id INTEGER,
                id_loc INTEGER,
                cod_loc TEXT,
                valabilitate TEXT,
                observatii TEXT,
                extra_data TEXT,
                owner_fullname TEXT,
                owner_fullname_2 TEXT,
                owner_address TEXT,
                owner_id_card_serie_number TEXT,
                owner_cnp TEXT,
                owner_phone TEXT,
                owner_email TEXT,
                cemetery_parcel TEXT,
                cemetery_row TEXT,
                cemetery_tomb TEXT,
                tomb_surface_m2 REAL,
                sector TEXT,
                FOREIGN KEY(concesionar_id) REFERENCES concesionari(id) ON DELETE SET NULL ON UPDATE CASCADE,
                FOREIGN KEY(id_loc) REFERENCES locuri_de_veci(id) ON DELETE SET NULL ON UPDATE CASCADE
            );
CREATE TABLE documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                doc_type TEXT,
                file_path TEXT NOT NULL,
                caption TEXT,
                nr_evidenta TEXT,
                date_added TEXT
            , media_id TEXT, sha256 TEXT, size INTEGER);
CREATE TABLE guides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT,
                points TEXT,
                color TEXT
            );
CREATE TABLE inhumati (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nume TEXT,
                prenume TEXT,
                cnp TEXT,
                data_nasterii TEXT,
                data_deces TEXT,
                data_inhumare TEXT,
                data_deshumare TEXT,
                sex TEXT,
                cauza_deces TEXT,
                profesie TEXT,
                religie TEXT,
                ultima_adresa TEXT,
                observatii TEXT,
                id_loc INTEGER,
                contract_id INTEGER, nr_certificat_deces TEXT, nr_adeverinta_inhumare TEXT,
                FOREIGN KEY(id_loc) REFERENCES locuri_de_veci(id) ON DELETE SET NULL ON UPDATE CASCADE,
                FOREIGN KEY(contract_id) REFERENCES contracte(id) ON DELETE SET NULL ON UPDATE CASCADE
            );
CREATE TABLE loc_extra (
                cod_loc TEXT PRIMARY KEY,
                extra_json TEXT
            );
CREATE TABLE loc_holders (
                cod_loc TEXT, holder_hash TEXT, PRIMARY KEY (cod_loc, holder_hash)
            );
CREATE TABLE locuri_de_veci (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cod_loc TEXT UNIQUE,
                points TEXT,
                color TEXT,
                sector TEXT,
                parcela TEXT,
                rand TEXT,
                numar TEXT,
                capacitate TEXT,
                disponibilitate TEXT,
                tip TEXT,
                stare TEXT,
                categorie TEXT,
                suprafata REAL,
                nr_vechi TEXT,
                numar_cf TEXT,
                ocupat INTEGER DEFAULT 0,
                observatii TEXT,
                extra_data TEXT
            , uid TEXT, sync_version INTEGER DEFAULT 0, are_inhumari INTEGER DEFAULT 0);
CREATE TABLE photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cod_loc TEXT NOT NULL,
                file_path TEXT NOT NULL,
                caption TEXT,
                uploader TEXT,
                date_added TEXT,
                is_cover INTEGER DEFAULT 0, media_id TEXT,
                FOREIGN KEY(cod_loc) REFERENCES locuri_de_veci(cod_loc) ON DELETE CASCADE
            );
CREATE TABLE plati (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cod_loc TEXT,
                contract_id INTEGER,
                suma REAL,
                data_plata TEXT,
                tip TEXT,
                chitanta_nr TEXT,
                observatii TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
CREATE TABLE rows_ghid (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sector_label TEXT,
                name TEXT,
                order_index INTEGER,
                baseline TEXT,
                width REAL,
                depth REAL
            );
CREATE TABLE sectoare (
                id TEXT PRIMARY KEY,
                points TEXT,
                color TEXT
            );
CREATE TABLE settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
CREATE TABLE sync_ops (
                op_id TEXT PRIMARY KEY,
                kind TEXT,
                applied_at TEXT,
                result TEXT
            );
CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL,
                last_login TEXT
            , uid TEXT, rights TEXT, active INTEGER DEFAULT 1, cemetery_id TEXT, version INTEGER DEFAULT 1, updated_at TEXT, origin TEXT DEFAULT 'local', lease_until TEXT);
CREATE INDEX idx_avize_codloc ON amenajari_avize(cod_loc);
CREATE UNIQUE INDEX idx_cereri_urmarire ON cereri_concesionare(cod_urmarire);
CREATE INDEX idx_concesionari_nume ON concesionari(nume);
CREATE INDEX idx_contracte_concesionar ON contracte(concesionar_id);
CREATE INDEX idx_contracte_loc ON contracte(id_loc);
CREATE INDEX idx_contracte_numar ON contracte(numar_contract);
CREATE INDEX idx_documents_entity ON documents(entity_type, entity_id);
CREATE INDEX idx_inhumati_contract ON inhumati(contract_id);
CREATE INDEX idx_inhumati_loc ON inhumati(id_loc);
CREATE INDEX idx_locuri_cod ON locuri_de_veci(cod_loc);
CREATE UNIQUE INDEX idx_locuri_uid ON locuri_de_veci(uid);
CREATE INDEX idx_photos_codloc ON photos(cod_loc);
