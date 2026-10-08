# ARHITECTURA COMERCIALĂ — ETAPA 1 (audit + proiectare)

Stare: **doar proiect**. Nu s-a modificat schema, Supabase real, SQLite-urile, Storage sau datele. Nu s-a implementat nimic.
Sursa auditului: codul din `paulwalker0727/v5` / `v6` (`backend/`, `shared/`, `desktop/`, `backend/cimitir/pg_migrations/001–007`) și citiri anterioare (doar citire) din PostgreSQL.

**DECIZII FINALE (după revizuire) sunt în §13 și au prioritate față de §4 și §12.**

Cuprins: 1 Audit · 2 Organizații · 3 Cimitire · 4 Dispozitive · 5 MAIN local (registre) · 6 Multi-tenant în cloud · 7 Analiza sincronizării · 8 Fluxuri A–F · 9 Modificări DB · 10 Plan de migrare · 11 Riscuri · 12 Decizii cerute · Anexa DDL.

---

## 1. Audit: ce există acum

### 1.1 Componente
| Componentă | Unde | Observație |
|---|---|---|
| Backend FastAPI (portal + admin + API) | `backend/server.py` (2111 rânduri), `sync_api.py`, `entity_sync_api.py`, `media_api.py`, `mobile_upload.py`, `citizen_api.py` | O singură instanță = **un singur cimitir** (single-tenant). |
| Nucleu SQLite comun | `shared/cimitir_database.py` | `DATA_DIR`/`DB_PATH` sunt constante de modul (`DB_PATH = DATA_DIR/cimitir.db`). Există un hook `_active_db_path` (contextvar), nefolosit pentru registre multiple în MAIN. |
| Nucleu PostgreSQL | `backend/cimitir/postgres_store.py` (1075), `pg_compat.py` | `pg_compat` traduce SQL-ul cu `?` și este **punctul unic de conexiune**, important pentru izolare (§6). |
| MAIN (PySide6) | `desktop/*.py` (`cloud_sync.py`, `entity_sync.py`, `media_sync.py`) | Lucrează pe SQLite local, outbox local, sincronizare prin HTTPS. |
| Sursă de date per cerere | `backend/cimitir/store.py` | În mod SQLite scanează `DATA_DIR/*.db` (mai multe cimitire = mai multe fișiere `.db`); în PostgreSQL există **o singură sursă** (`{"id":"cimitir"}`). |

### 1.2 Unde este deja o bază pentru „mai multe cimitire”
- `settings.cemetery_id` (UUID generat la inițializarea fiecărei baze, `cimitir_database.py:465`) este candidatul natural pentru `cemeteries.uid`.
- `users.cemetery_id TEXT` există și este verificat la autentificare (`cimitir_auth.access_state`).
- Toate entitățile sincronizate au deja `uid` (UUID text) + `version` + `change_seq` (migrările 001, 003, 007).

### 1.3 Unde proiectul este încă single-tenant (blocanți comerciali)
1. **Nicio coloană de tenant** în cele 31 de tabele PostgreSQL. Unicitățile sunt globale: `locuri_de_veci.cod_loc UNIQUE`, `users.username UNIQUE`, `citizens.email UNIQUE`, `sectoare.id` PK text, `settings.key` PK, `loc_extra.cod_loc` PK, `loc_holders (cod_loc, holder_hash)`.
2. **Cheie de sincronizare unică** per instalare: `settings.sync_key_hash` (un singur hash) și `settings.sync_clients` (JSON cu clienții), adică exact opusul unor chei per dispozitiv.
3. `photos.cod_loc` are **FK către `locuri_de_veci(cod_loc)`**: nu poate funcționa cu cod_loc duplicat între cimitire.
4. `documents.entity_id` este text liber (`cod_loc` pentru loc, id numeric local pentru restul), nu uid.
5. În MAIN: baza, media și constantele sunt **globale** (`DATA_DIR`, `DB_PATH`, `DATA_DIR/media`). Căile fișierelor sunt **absolute** în `photos.file_path` / `documents.file_path`.
6. Denumirile instituției („Cimitir”, „Concesionar”, texte din șabloane/PDF) sunt în template-uri și cod; nu există configurație de terminologie per organizație.

### 1.4 Rețea locală: abateri de la „bind doar 127.0.0.1”
- `shared/cimitir_config.portal_host()` și `start_local.py` au implicit **127.0.0.1**. Corect.
- **Abateri găsite:** `desktop/launcher.py:179` pornește „Server local” pe **`0.0.0.0`**; `launcher.py:186` pornește portul de scanare mobilă (`port + MOBILE_SCAN_PORT_OFFSET`) pe **`0.0.0.0`**; `desktop/web_server.py:203 run(host="0.0.0.0", port=8001)`. Scopul lor este accesul de pe telefon în LAN (QR). Detalii și corecție în §5.4.

### 1.5 Clasificarea celor 31 de tabele (din PostgreSQL real, schema `public`)
| Categorie | Tabele |
|---|---|
| Date de registru (per cimitir) | `locuri_de_veci`, `concesionari`, `contracte`, `inhumati`, `plati`, `amenajari_avize`, `sectoare`, `photos`, `documents`, `media_objects`, `loc_extra`, `guides`, `rows_ghid`, `loc_holders`, `qr_generations`, `ai_scan_history`, `cereri_concesionare`, `mobile_upload_tokens` |
| Sincronizare (per cimitir + dispozitiv) | `sync_ops`, `sync_tombstones`, `sync_conflicts` |
| Per organizație | `users` (personal), `citizens` + `citizen_resets` (conturi portal public), `email_outbox`, `audit_log` (cu `cemetery_uid` opțional) |
| Configurare mixtă | `settings` (platformă / organizație / cimitir; se scindează) |
| Paritate fără rol în server | `cloud_sync_map`, `cloud_sync_outbox`, `cloud_sync_docs` (copie a cozii MAIN; neutilizate pe server) |
| Global | `schema_migrations` |

---

## 2. Modelul `organizations`

Entitate de top-level (client comercial). Tabel în PostgreSQL; în SQLite-ul fiecărui registru există o **copie read-mostly** (un singur rând) ca MAIN să afișeze corect titlurile și offline.

```
organizations
  uid                 UUID PK
  organization_type   TEXT NOT NULL CHECK IN ('PARISH','MUNICIPALITY','PRIVATE_OPERATOR','MONASTERY','ASSOCIATION','OTHER')
  official_name       TEXT NOT NULL            -- denumirea juridică exactă
  display_name        TEXT NOT NULL            -- cum apare în aplicație
  cui_cif             TEXT NULL                -- CUI/CIF opțional
  county              TEXT, locality TEXT, address TEXT, phone TEXT, email TEXT
  logo_media_uid      UUID NULL                -- referință la un obiect media (logo), nu octeți în DB
  terminology_profile TEXT NULL                -- override al profilului implicit pe tip
  labels              JSONB NOT NULL DEFAULT '{}'  -- override-uri punctuale de text
  locale              TEXT NOT NULL DEFAULT 'ro-RO', timezone TEXT NOT NULL DEFAULT 'Europe/Bucharest'
  active              BOOLEAN NOT NULL DEFAULT true
  created_at, updated_at TIMESTAMPTZ, version INT, change_seq BIGINT
```

### 2.1 Terminologie fără „Parohie/Primărie” hardcodate
- Profilurile de termeni sunt **date**, nu cod în UI: câte un profil implicit per `organization_type` (în fișier de resurse versionat, ex. `shared/terminology/*.json`), cu chei stabile, de exemplu:
  `org.noun`, `org.head_title`, `cemetery.noun`, `cemetery.registry_title`, `contract.title`, `contract.holder_noun`, `report.header_line1`, `portal.welcome`.
- Rezolvare în ordine: `organizations.labels[key]` → profil `terminology_profile` → profil implicit pe `organization_type` → valoare generică neutră.
- UI-ul, portalul, PDF-urile și șabloanele folosesc `L("cheie")` (Jinja global + helper Python/Qt). Titluri compuse: `display_name` al organizației + `display_name` al cimitirului (ex. „Primăria Comunei X — Cimitir Central”).
- Profilurile implicite pe tip (conținutul exact se validează cu clienții): `PARISH` → „Parohia”, „Paroh”; `MUNICIPALITY` → „Primăria/Municipiul”, „Primar”; `PRIVATE_OPERATOR` → „Administrator”; `MONASTERY` → „Mănăstirea”, „Stareț/Stareță”; `ASSOCIATION`/`OTHER` → neutru.

---

## 3. Modelul `cemeteries`

```
cemeteries
  uid               UUID PK                      -- existent: settings.cemetery_id al bazei curente
  organization_uid  UUID NOT NULL REFERENCES organizations(uid)
  name              TEXT NOT NULL                -- denumire afișată (modificabilă)
  internal_code     TEXT NOT NULL                -- cod intern, UNIQUE (organization_uid, internal_code)
  locality TEXT, county TEXT, address TEXT
  status            TEXT NOT NULL DEFAULT 'ACTIVE' CHECK IN ('DRAFT','ACTIVE','ARCHIVED')
  map_config        JSONB NOT NULL DEFAULT '{}'  -- px_per_meter, imagine fundal, centru/zoom, stiluri
  public_slug       TEXT NULL UNIQUE             -- /portal/<slug>; NULL = portal public dezactivat
  public_enabled    BOOLEAN NOT NULL DEFAULT false
  branding          JSONB NOT NULL DEFAULT '{}'  -- doar peste cel al organizației, dacă diferă
  created_at, updated_at TIMESTAMPTZ, version INT, change_seq BIGINT
```
Exemplu: `Primăria X → {Cimitir Central, Cimitir Nord, Cimitir Sat Y}`.

Tabele însoțitoare necesare (în loc de `users.cemetery_id TEXT` unic):
- `organization_members (organization_uid, user_uid, role)` și `user_cemetery_access (user_uid, cemetery_uid, role)`: un angajat poate lucra pe mai multe cimitire ale aceleiași organizații.
- `platform_admins` (cont de suport/vânzător, separat de personalul clientului, cu audit explicit).

---

## 4. Modelul `devices` (cheie de sincronizare per dispozitiv) — ÎNLOCUIT DE §13.2 (device + device_cemetery_bindings)

```
devices
  device_uid        UUID PK
  organization_uid  UUID NOT NULL REFERENCES organizations(uid)
  cemetery_uid      UUID NOT NULL REFERENCES cemeteries(uid)
  installation_uid  UUID NOT NULL                -- grupează registrele aceleiași instalări MAIN
  device_name       TEXT NOT NULL                -- „PC Secretariat”
  device_type       TEXT NOT NULL CHECK IN ('MAIN','MOBILE','WEB_KIOSK','SERVICE')
  sync_key_hash     TEXT NOT NULL                -- sha256(secret 256 biți); cheia în clar nu se stochează
  key_prefix        TEXT NOT NULL                -- primele 6 caractere (identificare în UI), nu secret
  active            BOOLEAN NOT NULL DEFAULT true
  app_version TEXT, platform TEXT
  created_at TIMESTAMPTZ, created_by TEXT
  last_seen_at TIMESTAMPTZ, last_sync_at TIMESTAMPTZ, pending_ops INT NULL   -- raportate de MAIN
  revoked_at TIMESTAMPTZ NULL, revoked_by TEXT NULL, replaced_by_device_uid UUID NULL
device_pairing_codes
  code_hash TEXT PK, organization_uid, cemetery_uid, device_type, created_by, expires_at, used_at NULL
```
Decizii:
- **Un dispozitiv = o pereche (instalare MAIN, registru).** Un PC cu 3 registre are 3 rânduri `devices` (aceeași `installation_uid`), deci chei, cursoare și revocări izolate per cimitir.
- Format cheie: `<device_uid>.<secret>`. Autentificarea caută `device_uid` și compară în timp constant. Rezolvă și organizația și cimitirul din rând, **nu din cerere** (clientul nu poate cere alt tenant).
- **Înrolare prin cod de împerechere de unică folosință** (generat de administratorul organizației, valabil 15 minute): MAIN îl schimbă pe cheia dispozitivului, afișată o singură dată. Nu se mai copiază chei lungi manual.
- Revocare/rotație din administrare; cheia veche devine invalidă imediat (comportament deja existent la cheia globală, reținut).
- Limitări de rată pe `device_uid` și IP (mecanismul `portal_ratelimit` + `loginfail:sync:*` există, se parametrizează).
- Compatibilitate temporară: cheia globală actuală devine un rând `devices` „legacy-main” legat de cimitirul implicit, apoi se retrage.

---

## 5. MAIN local-first: registre locale

### 5.1 Structură pe disc
```
DATA_DIR/
  installation.json              # installation_uid, limbă, ultimul registru deschis, versiune format
  registries/
    <cemetery_uuid>/
      registry.json              # {format:1, cemetery_uid, organization_uid, display_name, created_at, db_schema}
      database.sqlite            # (+ -wal / -shm) conține și cloud_sync_outbox, setările de sync, cursoarele
      media/                     # fișiere originale gestionate (calea în DB devine RELATIVĂ la registru)
      media_cache/               # octeți aduși din cloud
      backups/
      logs/
      exports/
  logs/                          # jurnal general al aplicației
```
- **Nume afișat ≠ cale.** `display_name` se schimbă în `registry.json` (și în `cemeteries.name` la următoarea sincronizare); `<cemetery_uuid>` și calea nu se schimbă niciodată.
- Fiecare registru are baza, media, outbox-ul și cursoarele lui; configurarea cloud (URL, cheia dispozitivului) este în `settings` din registru, deci izolarea vine natural.
- „Adaugă registru/cimitir nou”: (1) alege organizația (sau o creează local) și numele; (2) generează UUID, creează folderul și `database.sqlite` cu schema curentă; (3) scrie `settings.cemetery_id = UUID` și copia organizației; (4) scrie `registry.json`; (5) opțional „Conectează la cloud” (flux A sau C).

### 5.2 Ce trebuie schimbat în cod (de proiectat la implementare)
- Constantele `DATA_DIR`/`DB_PATH` din `cimitir_database.py` devin **rezolvare per registru activ** (se poate sprijini pe `_active_db_path`, deja existent), iar `_media_dir()` folosește folderul registrului.
- Schimbarea registrului în UI = închide ferestrele deschise, redeschide conexiunea; serverul local servește **un registru activ** (cel mai simplu și sigur) sau folosește prefix de registru în rute.
- `photos.file_path` / `documents.file_path` din absolut în **relativ** (`media/<fișier>`) cu migrare care verifică existența fișierului. Rezolvarea prin `media_id` există deja (`_local_media_path`).
- Fiecare registru păstrează `db_schema_version`; migrarea SQLite se aplică individual la deschidere.

### 5.3 Migrarea instalării existente
`DATA_DIR/cimitir.db` + `DATA_DIR/media` → `registries/<settings.cemetery_id>/…` după copie de siguranță verificabilă; originalul nu se șterge până la confirmare; marcaj de rollback. Se face doar în aplicația nouă, la prima pornire, cu confirmarea utilizatorului.

### 5.4 Rețeaua locală
- Implicit **127.0.0.1:8000** pentru toate procesele locale (portal local, admin, API local).
- Modul „Server local pe rețea” (QR pentru telefon în LAN) devine **opt-in explicit** în setări, cu avertisment, port separat, token de sesiune și oprire automată după inactivitate; nu mai este implicit `0.0.0.0` (`launcher.py:179`, `:186`, `web_server.py:203`).
- Încărcarea de pe telefon trece, ca regulă, prin **cloud** (`mobile_upload.py` + `CIMITIR_PUBLIC_URL` există deja), deci LAN-ul rămâne doar pentru situații fără internet.

---

## 6. Cloud multi-tenant (un singur PostgreSQL comun)

### 6.1 Principii
- Un proiect Supabase comun, **fără proiect per client**. Izolarea se face pe două straturi: (a) aplicație: context de tenant obligatoriu; (b) bază de date: **Row Level Security** ca plasă de siguranță.
- Fiecare rând de registru poartă `organization_uid` și `cemetery_uid` (UUID, NOT NULL după backfill).
- **Punct unic de aplicare:** `pg_compat.get_connection()`. La începutul fiecărei tranzacții se face `SET LOCAL app.org_uid = …; SET LOCAL app.cemetery_uid = …` din dispozitivul/utilizatorul autentificat. Politicile RLS compară coloanele cu `current_setting(...)`, iar `DEFAULT current_setting(...)` pe `cemetery_uid` face ca INSERT-urile existente să rămână neschimbate. Asta evită rescrierea a ~1000 de rânduri de SQL din `postgres_store.py` și reduce riscul ca o interogare uitată să scape de filtru. **De validat într-un prototip:** pooler-ul Supabase în mod tranzacție, rolul aplicației (nu proprietarul, ca să nu ocolească RLS) și performanța.
- Rolul PostgreSQL al aplicației: `cimitir_app` fără `BYPASSRLS`; migrările rulează cu alt rol. Astăzi aplicația se conectează cu rolul `postgres`, care ocolește RLS (de schimbat).

### 6.2 Matrice tabel → modificare
| Tabel | Modificare necesară |
|---|---|
| `locuri_de_veci` | + `organization_uid`, `cemetery_uid`; `cod_loc UNIQUE` → `UNIQUE (cemetery_uid, cod_loc)`; index `(cemetery_uid, change_seq)` |
| `concesionari`, `contracte`, `inhumati`, `plati`, `amenajari_avize` | + tenant; index `(cemetery_uid, change_seq)`; `contracte.numar_contract` unic **per cimitir**, dacă se dorește (azi nu e unic) |
| `sectoare` | PK text `id` → PK/UNIQUE `(cemetery_uid, id)` (există deja `uid` UNIQUE) |
| `photos` | + tenant; **eliminare FK pe `cod_loc`**, înlocuit cu `loc_uid` (FK către `locuri_de_veci.uid`); adăugare `caption`, `is_cover` pe fir |
| `documents` | + tenant; `entity_id` text → adăugare `entity_uid` (uid-ul entității); `entity_id` rămâne doar ca depreciat/local |
| `media_objects` | + tenant; `storage_path` cu prefix de tenant (§6.4); `entity_uid` rămâne |
| `loc_extra`, `loc_holders` | PK-urile devin `(cemetery_uid, cod_loc[, holder_hash])` |
| `guides`, `rows_ghid` | + tenant |
| `qr_generations`, `ai_scan_history` | + tenant |
| `cereri_concesionare` | + tenant; `cod_urmarire UNIQUE` rămâne global (codul nu dezvăluie tenantul) |
| `mobile_upload_tokens` | + `cemetery_uid`, `device_uid` |
| `sync_ops` | + `device_uid`, `cemetery_uid`; PK `op_id` rămâne unic global |
| `sync_tombstones` | + `cemetery_uid`; PK `(entity, uid)` rămâne (uid UUID global); index `(cemetery_uid, entity, change_seq)` |
| `sync_conflicts` | + `cemetery_uid`, `device_uid` |
| `users` | `username UNIQUE` global → `UNIQUE (organization_uid, lower(username))`; legături prin `organization_members` / `user_cemetery_access`; cheile `mustchange:*` și `loginfail:*` din `settings` se mută pe coloane/tabel dedicat |
| `citizens`, `citizen_resets` | `citizens.email UNIQUE` → `UNIQUE (organization_uid, email)` (decizie §12); + `organization_uid` |
| `email_outbox`, `audit_log` | + `organization_uid`, `cemetery_uid` (NULL permis), `device_uid`, `actor_uid` |
| `settings` | se scindează: `platform_settings`, `organization_settings (organization_uid, key)`, `cemetery_settings (cemetery_uid, key)`; mutarea `sync_key_hash`/`sync_clients`/`entity_sync_active`/`sync_map_*` în tabele proprii (devices, cemeteries) |
| `cloud_sync_map`, `cloud_sync_outbox`, `cloud_sync_docs` | rămân doar ca paritate cu MAIN; propun eliminarea din PostgreSQL într-o migrare ulterioară |
| `schema_migrations` | neschimbat |

Triggerele `cimitir_sync_touch` / `cimitir_sync_tombstone` rămân; tombstone-ul trebuie să scrie și `cemetery_uid` (din `OLD`). Secvența `sync_change_seq` rămâne **globală** (monotonă): cursoarele filtrează după `(cemetery_uid, change_seq > since)`; golurile nu contează.

### 6.3 Autentificare și sesiuni
- Personal: JWT cu `organization_uid`, mulțimea `cemetery_uid` permise și rolul; schimbarea cimitirului activ în UI emite un context nou. Portalul public se rezolvă prin `public_slug` (sau domeniu personalizat) → `cemetery_uid` + `organization_uid`, fără sesiune de personal.
- Dispozitive: cheia de dispozitiv (§4) fixează tenantul.

### 6.4 Storage
- Același bucket privat. Cheie obiect nouă: `org/<organization_uid>/cem/<cemetery_uid>/<table>/<entity_type>/<doc_type>/<entity_uid>/<media_id>/<fișier>`.
- Obiectele existente (`documents/...`) **nu se mută**: `media_objects.storage_path` este stocat per obiect, deci cheile vechi continuă să funcționeze; cele noi folosesc prefixul de tenant. Exportul/ștergerea unui client = operație pe prefix.
- Accesul rămâne exclusiv prin API (clienții nu primesc chei de storage), ca acum.

### 6.5 Alte efecte
Backup/export per cimitir (nu doar `pg_dump` global), cote și metrici per organizație (rânduri, GB media), jurnal de audit per organizație, ștergere GDPR pe tenant.

---

## 7. Analiza sincronizării actuale

### 7.1 Rolul pieselor
| Piesă | Rol acum |
|---|---|
| `uid` (UUID text) | Identitatea stabilă a rândului între MAIN și server; cheile numerice `id` nu circulă. FK-urile se traduc în uid-ul părintelui (`concesionar_uid`, `loc_uid`, `contract_uid`). |
| `version` + `base_version` | Concurență optimistă. Trigger PostgreSQL crește `version` la orice INSERT/UPDATE (și din `/admin`). MAIN trimite `base_version` (versiunea serverului pe care s-a bazat). Dacă diferă → **conflict**: nu se suprascrie, se scrie în `sync_conflicts`, operatorul alege `server`/`mine`. |
| `change_seq` | Număr dintr-o secvență globală, pus de trigger; MAIN citește incremental `change_seq > cursor` (cursor per entitate, memorat în `settings` SQLite). |
| `sync_tombstones` | Ștergerile (entitate, uid, versiune, change_seq) create de trigger la `DELETE` în PostgreSQL, ca ștergerile să ajungă și la ceilalți clienți. |
| `sync_ops` | Registru de idempotență: `op_id` → răspunsul memorat (`response`). O retrimitere returnează același rezultat. **Nu** este un jurnal de modificări. |
| outbox (`cloud_sync_outbox`, SQLite) | Coada locală a MAIN: triggere SQLite captează INSERT/UPDATE/DELETE pe 6 entități, o modificare repetată înlocuiește operația în așteptare; `kind` ∈ `entity` / `map` / `media`. Starea: `pending`/`conflict`/`error`. |

### 7.2 De ce MAIN și cloud nu converg 100% acum
1. **Două protocoale în paralel.** Harta (`POST /api/sync/map`) face înlocuire de instantaneu (sectoare, plus locuri/înhumați când `entity_sync_active` ≠ 1), iar entitățile merg pe `/api/sync/entities`. În modul entități, instantaneul actualizează doar `sectoare` (cu `DELETE FROM sectoare` + reinserare, fără versiuni). Rezultat: sectoarele nu au versiune, nici tombstone, nici conflict.
2. **Acoperire parțială.** Se sincronizează doar `locuri_de_veci, concesionari, contracte, inhumati, plati, amenajari_avize` (`cimitir_entities.ORDER`). Nu intră: `sectoare` (doar instantaneu), `settings` (inclusiv `px_per_meter`), `guides`, `rows_ghid`, `loc_extra`, `photos` (doar fișierul, vezi 7.4), `loc_holders` (endpoint separat), `users` (flux unidirecțional `/staff`).
3. **Bootstrap incomplet.** `enqueue_bootstrap()` trimite doar rândurile cu `version = 0` și fără operație în coadă. Dacă un MAIN a avut deja `version > 0` (a fost legat de alt server, restaurat dintr-un backup, sau baza cloud a fost golită), rândurile **nu se mai retrimit**.
4. **Cursor legat de MAIN, nu de baza cloud.** `entity_cursor_<entitate>` stă în `settings` SQLite și nu știe cărui cloud/cimitir/epoci îi aparține. Re-legarea la o altă bază cu `change_seq` mai mic duce la **date pierdute** la pull, fără semnal.
5. **Nu există reconciliere/verificare.** Nu am găsit nicio comparație de numărători sau amprente per entitate între MAIN și cloud (există doar `snapshot_hash` pentru hartă). Divergențele rămân nedetectate.
6. **Conflicte care blochează.** `skipped_local_pending` păstrează local modificarea nesincronizată; până la decizia operatorului, cele două părți diferă. E corect ca regulă, dar fără indicator vizibil „sunt 3 conflicte” divergența pare un bug.
7. **Chei naturale duplicate.** La pull, un loc cu același `cod_loc` dar alt `uid` rescrie `uid`-ul local (`_apply_remote`), ceea ce poate fuziona greșit două locuri diferite din registre diferite (devine periculos în multi-tenant).
8. **Câmpuri denormalizate.** `cod_loc` text în `contracte`, `plati`, `amenajari_avize`, `documents`, `photos`; redenumirea unui `cod_loc` nu actualizează legăturile de pe fir. `plati.contract_id` nu are FK.

### 7.3 De ce datele de test din cloud nu dispar dacă lipsesc din MAIN
Sincronizarea este **incrementală și bazată pe evenimente, nu pe stare**. Serverul trimite o ștergere doar dacă există un `DELETE` în PostgreSQL (tombstone). MAIN nu poate șterge un rând pe care nu l-a avut vreodată; absența lui local nu produce nicio operație. Concret, rândurile `E6-…`, `TestPopescu`, `tst_*` au fost create **direct în cloud** (teste live și panou web), deci nu există nicio operație MAIN care să le șteargă. Mai mult, în mod entități, instantaneul hărții **nu mai șterge** locurile (`keep=[]` când `entity_sync_active=1`). Singura cale de eliminare este ștergerea explicită în cloud sau o procedură de **reset/reconciliere cu epocă** (flux B/C, §8).

### 7.4 De ce fotografiile atașate locului de veci nu se sincronizează complet
- Se sincronizează **fișierul și metadatele minime** prin `media_objects` (nume, sha256, dimensiune, `entity_uid` = uid-ul locului). Pe fir nu circulă `caption` și `is_cover`; la primirea pe alt MAIN, `_apply_object` face `INSERT INTO photos … caption = nume fișier, is_cover = 0`. Deci **legenda și poza de copertă se pierd**.
- Dacă locul nu există încă local la momentul primirii, rezultatul este `unresolved` și poza rămâne doar în storage; ordinea (entități înainte de media) nu este garantată între cicluri, iar nu există reluare dedicată a celor „nerezolvate”.
- Cheia de legătură pentru fotografii este `cod_loc` (text), iar în `documents` `entity_id` este id numeric local pentru restul entităților; ambele sunt fragile la redenumiri și la multi-tenant.
- Fotografiile preexistente nu se încarcă automat (`enqueue_existing()` este declarat, dar **nefolosit**).
- **Pe server, un fișier venit din MAIN există doar ca rând în `media_objects` + obiect în Storage.** `POST /api/sync/media/{id}/complete` (`media_api.py`) inserează numai în `media_objects` (`media_service.py:82`); nu creează rânduri în `photos` sau `documents` din PostgreSQL. Panoul `/admin` și portalul citesc din `documents` (`server.py:870`, `list_documents("loc", cod_loc, "poza_loc")`), deci **o poză încărcată din MAIN nu apare în panoul web**, iar una încărcată din web apare în `documents` și ajunge la MAIN doar prin `media_objects`. Sunt trei reprezentări (`photos`, `documents`, `media_objects`) fără proiecție între ele.
- Fișiere de zero octeți și lipsă locală sunt respinse (`error`), fără raport agregat în UI.

---

## 8. Fluxuri propuse

Reguli comune: fiecare registru are `sync_epoch` (UUID generat la prima legare a registrului cu un cimitir din cloud și stocat și în cloud). Cursoarele și outbox-ul sunt valabile **doar** pentru epoca lor; epocă diferită ⇒ pull de la zero și verificare. Toate operațiile destructive cer backup verificat și confirmare explicită.

### A. Cimitir nou, MAIN și cloud goale
1. Administratorul organizației creează cimitirul în cloud (`status=DRAFT`) și generează cod de împerechere.
2. MAIN: „Adaugă registru/cimitir nou” → registru local gol cu `cemetery_uid` din cloud; înrolare dispozitiv (cheie per dispozitiv).
3. Se creează `sync_epoch`; ambele părți confirmă „gol”. Cimitirul trece în `ACTIVE` la prima sincronizare reușită.
Variantă local-first: MAIN creează UUID-ul și „revendică” cimitirul în cloud cu codul de împerechere.

### B. MAIN existent → cloud nou
1. Verificare prealabilă (doar citire): numărători, duplicate `cod_loc`, FK orfane, fișiere media lipsă/sha256, conflicte locale deschise.
2. Backup local verificat al registrului.
3. Cloud: cimitirul trebuie să fie **gol** (altfel se refuză și se cere fluxul de reconciliere). Se creează `sync_epoch`.
4. Încărcare completă ca bootstrap **indiferent de `version`** (ordine: sectoare/hartă, locuri, concesionari, contracte, înhumați, plăți, avize, apoi media).
5. **Verificare**: pe entitate, număr + amprentă (hash agregat peste `uid:version`) între MAIN și cloud, plus media (număr + sha256). Raport PASS/FAIL; abia apoi starea „sincronizat”.

### C. Cloud existent → MAIN nou
1. Cod de împerechere → registru nou cu `cemetery_uid`/`organization_uid` din cloud; registrul local trebuie să fie **gol** (altfel se refuză).
2. Pull complet de la cursor 0 (entități, sectoare, setări de cimitir, metadate media). Media: prefetch pentru fișiere mici, la cerere pentru restul (deja așa).
3. Verificare ca la B. Poze: se aduc inclusiv `caption`/`is_cover` (după extinderea firului).

### D. Sincronizare normală bidirecțională
1. MAIN trimite outbox-ul (idempotent prin `op_id`), apoi trage modificările serverului. Conflict ⇒ nimic nu se suprascrie, apare în lista de conflicte.
2. Raportează la fiecare ciclu `last_seen_at`, `pending_ops`, `app_version` (servesc la alerte „dispozitiv offline/cu operații nesincronizate”).
3. Verificare periodică ușoară (numărători + amprentă pe entitate) cu alertă la diferență.
4. Sectoarele și setările de cimitir intră în același mecanism cu versiune/tombstone (nu mai există instantaneu cu ștergere totală).

### E. Recuperare după PC pierdut/stricat
1. Pe PC nou: autentificare personal → „Restaurează registrul” → alegere cimitir → înrolare dispozitiv nou.
2. Fluxul C (pull complet + verificare). Dispozitivul vechi se marchează `revoked` (cheia lui nu mai funcționează).
3. Ce se poate pierde: **doar** operațiile din outbox-ul PC-ului pierdut care nu ajunseseră în cloud; cloud-ul păstrează `pending_ops` raportat și `last_sync_at`, deci se vede cât a fost în risc. Se recomandă backup local periodic pe suport extern (există `cimitir_backup`) și sincronizare frecventă.

### F. Înlocuirea unui dispozitiv
- **Vechiul PC încă funcționează:** sincronizare finală → „Transferă registrul” (arhivă a folderului registrului **fără** cheia dispozitivului) → import pe PC nou → înrolare nouă → pull delta (aceeași epocă) → revocarea vechiului dispozitiv din administrare.
- **Vechiul PC nu mai există:** flux E.
- Fiecare pas se înregistrează în `audit_log` (`device_enrolled`, `device_revoked`, `device_replaced`).

---

## 9. Modificări DB necesare (propuse, nimic aplicat)

Toate migrările sunt **aditive** și repetabile; se numerotează după `007`.
- **008** `organizations`, `cemeteries`, `organization_members`, `user_cemetery_access`, `platform_admins`, `devices`, `device_pairing_codes`.
- **009** coloane `organization_uid`, `cemetery_uid` (nullable) pe toate tabelele de registru/sincronizare/audit; `DEFAULT current_setting('app.cemetery_uid', true)::uuid`; **backfill** cu organizația și cimitirul implicite create din datele existente (`settings.cemetery_id`).
- **010** după verificarea backfill-ului: `NOT NULL`, unicități compuse (`cod_loc`, `sectoare`, `loc_extra`, `loc_holders`, `users`, `citizens`), eliminare FK `photos.cod_loc` → `loc_uid`, `documents.entity_uid`, indexuri `(cemetery_uid, change_seq)`.
- **011** RLS + politici + rolul `cimitir_app`; teste de izolare (tenant A nu vede/nu poate scrie în tenant B).
- **012** `caption`/`is_cover` pe `photos`, sincronizarea `sectoare`/`guides`/`rows_ghid`/`loc_extra`/setări de cimitir ca entități, `sync_epoch`.
- **013** eliminarea tabelelor de paritate neutilizate (`cloud_sync_*`) și a cheilor globale vechi din `settings`.
SQLite (MAIN): migrare de schemă echivalentă (aceleași coloane de tenant opționale, `registries/`), plus conversia căilor media în relative.

---

## 10. Plan de migrare (fără a atinge datele reale până la aprobare)

0. **Pregătire (fără risc):** bază Supabase **separată de test** (după aprobare), clonă a schemei; suita de teste live trebuie să ruleze doar acolo (protecția anti-pytest există). Backup complet al bazei reale și al Storage înainte de orice pas pe producție.
1. **Prototip RLS** pe baza de test: context de tenant în `pg_compat`, rol `cimitir_app`, pooler, performanță, 2 tenanți de test.
2. **Migrările 008–011** pe baza de test, cu backfill dintr-o clonă a datelor reale; verificări: numărători identice, FK, unicități, izolare între tenanți.
3. **Server:** context de tenant, autentificare per dispozitiv (cheia veche = dispozitiv „legacy-main” pe durata tranziției), rute `public_slug`, terminologie (`L()`).
4. **MAIN:** registre multiple (`registries/`), migrare instalare existentă, împerechere, terminologie în UI/PDF-uri, `127.0.0.1` implicit.
5. **Consolidarea sincronizării:** `sync_epoch`, bootstrap complet, entități suplimentare, fotografii cu legendă/copertă, verificare periodică, rapoarte de conflicte.
6. **Pilot** cu o singură organizație (cea actuală) pe producție, apoi onboarding de noi clienți.
7. **Rollback:** fiecare migrare are script invers sau este aditivă (coloanele noi pot rămâne nefolosite); pe MAIN, folderul original nu se șterge până la confirmare; RLS se poate dezactiva pe tabel fără pierdere de date.
Aplicarea pe producție se face numai după rularea integrală pe clonă, cu aprobarea explicită a fiecărei migrări.

---

## 11. Riscuri

| Risc | Impact | Atenuare |
|---|---|---|
| Interogare care scapă de filtrul de tenant (scurgere între clienți) | **Critic** (date personale: CNP, adrese) | RLS + rol fără `BYPASSRLS` + teste automate de izolare + context obligatoriu în `pg_compat`. |
| Rolul actual de conectare ocolește RLS | Mare | Rol dedicat pentru aplicație; migrări cu alt rol. |
| Pooler Supabase în mod tranzacție vs `SET LOCAL` | Mediu | Prototip înainte de decizie; alternativ filtru explicit în aplicație. |
| Migrarea bazelor SQLite existente (căi absolute, media) | Mare | Backup, copiere (nu mutare), verificare sha256, rollback. |
| Backfill greșit al `cemetery_uid` pe date reale | Mare | Rulare pe clonă, numărători și amprente înainte/după, `NOT NULL` abia după verificare. |
| Cursoare și epoci (pull care „sare” date) | Mare | `sync_epoch` + verificare numărători/amprentă. |
| Conflicte și experiența operatorului offline | Mediu | Indicator vizibil, politică clară, câmpuri-cheie rezolvabile automat. |
| Date personale (GDPR), ștergere/export per client, jurnal acces | Mare | Prefix storage per tenant, export/ștergere pe tenant, audit, retenție. |
| Pierdere de operații la PC pierdut | Mediu | `pending_ops` raportat, sincronizare frecventă, backup local. |
| Compatibilitate cu instalările MAIN deja distribuite | Mediu | Cheie „legacy” temporară, versiune minimă de client, actualizare asistată. |
| Complexitatea terminologiei (șabloane/PDF/contracte) | Mediu | Chei stabile `L()`, inventar al textelor, profil neutru implicit. |
| Cost/limite Supabase (conexiuni, storage, backup) | Mediu | Monitorizare per organizație, plan comercial, backup zilnic verificat. |
| Testele live pe producție | Rezolvat parțial | Guard anti-pytest în repo (v6); bază de test separată necesară. |

---

## 12. Decizii cerute înainte de implementare
1. **Dispozitiv = (instalare, registru)** (recomandat) sau un singur dispozitiv per instalare cu acces la mai multe cimitire?
2. **Conturile portalului public (`citizens`)**: per organizație (recomandat) sau per cimitir?
3. **Personal**: un angajat poate lucra pe mai multe cimitire ale aceleiași organizații (propus) — și pe mai multe organizații (consultanți)?
4. **RLS**: adoptăm RLS + `SET LOCAL` (recomandat, după prototip) sau doar filtre în aplicație?
5. **Baza de test Supabase separată**: o creăm în Etapa 2?
6. **Modul LAN pentru telefon**: îl păstrăm ca opt-in sau îl eliminăm în favoarea încărcării prin cloud?
7. **Terminologie**: lista de profiluri implicite (§2.1) și cine validează textele juridice (contracte).
8. **Cheia „legacy”**: durata de tranziție și momentul retragerii.
9. **Politica de conflicte**: păstrăm decizia manuală pe rând sau adăugăm reguli automate pe câmp?
10. **Facturare/abonamente** sunt în afara acestei etape.

---

## Anexa — DDL orientativ (nu se rulează)

```sql
CREATE TABLE organizations (
  uid uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_type text NOT NULL CHECK (organization_type IN
    ('PARISH','MUNICIPALITY','PRIVATE_OPERATOR','MONASTERY','ASSOCIATION','OTHER')),
  official_name text NOT NULL, display_name text NOT NULL, cui_cif text,
  county text, locality text, address text, phone text, email text,
  logo_media_uid uuid, terminology_profile text, labels jsonb NOT NULL DEFAULT '{}',
  locale text NOT NULL DEFAULT 'ro-RO', timezone text NOT NULL DEFAULT 'Europe/Bucharest',
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  version int NOT NULL DEFAULT 1, change_seq bigint);

CREATE TABLE cemeteries (
  uid uuid PRIMARY KEY, organization_uid uuid NOT NULL REFERENCES organizations(uid),
  name text NOT NULL, internal_code text NOT NULL, locality text, county text, address text,
  status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('DRAFT','ACTIVE','ARCHIVED')),
  map_config jsonb NOT NULL DEFAULT '{}', public_slug text UNIQUE,
  public_enabled boolean NOT NULL DEFAULT false, branding jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  version int NOT NULL DEFAULT 1, change_seq bigint,
  UNIQUE (organization_uid, internal_code));

CREATE TABLE devices (
  device_uid uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_uid uuid NOT NULL REFERENCES organizations(uid),
  cemetery_uid uuid NOT NULL REFERENCES cemeteries(uid),
  installation_uid uuid NOT NULL, device_name text NOT NULL,
  device_type text NOT NULL CHECK (device_type IN ('MAIN','MOBILE','WEB_KIOSK','SERVICE')),
  sync_key_hash text NOT NULL, key_prefix text NOT NULL, active boolean NOT NULL DEFAULT true,
  app_version text, platform text,
  created_at timestamptz NOT NULL DEFAULT now(), created_by text,
  last_seen_at timestamptz, last_sync_at timestamptz, pending_ops int,
  revoked_at timestamptz, revoked_by text, replaced_by_device_uid uuid);

-- RLS (schiță) pe un tabel de registru
ALTER TABLE locuri_de_veci ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON locuri_de_veci
  USING (cemetery_uid = current_setting('app.cemetery_uid', true)::uuid)
  WITH CHECK (cemetery_uid = current_setting('app.cemetery_uid', true)::uuid);
```


---

## 13. Decizii finale (înainte de Etapa 2)

1. **MAIN rămâne direct pe SQLite + media locală.** Interfața desktop NU trece prin `127.0.0.1:8000`. Trei fluxuri separate:
   `MAIN Desktop → SQLite + media locală (direct)` · `portal/admin web local → 127.0.0.1:8000 → aceeași bază locală` · `sync background → HTTPS → cloud`.
   Implementat: host implicit `127.0.0.1`, port `8000`, LAN doar opt-in, funcțional fără internet.
2. **Dispozitivul fizic ≠ legătura dispozitiv–cimitir.**
   `devices` (device_uid, organization_uid, installation_uid, device_name, device_type, active, created_at, last_seen_at) — un PC MAIN apare o singură dată.
   `device_cemetery_bindings` (binding_uid, device_uid, organization_uid, cemetery_uid, sync_key_hash, last_change_seq, active, created_at, last_seen_at, revoked_at …) — cheie, cursor și revocare **per cimitir**.
   `X-Sync-Key` are forma `<binding_uid>.<secret>` și identifică **legătura**; organizația și cimitirul se deduc din rândul legăturii, nu din cererea clientului. FK-urile compuse `(cemetery_uid, organization_uid)` împiedică o legătură să încrucișeze organizații. Schiță: `plan/etapa2/E2_008_tenancy_core.sql.draft`.
3. **Cetățeni:** identitatea la nivel de **organizație** (`citizens.organization_uid`); fiecare cerere/concesionare/document/notificare/operație care aparține unui cimitir poartă `cemetery_uid`. Fără acces între organizații.
4. **Personal:** `identities` (credențiale) → `organization_members` (apartenență, username unic per organizație) → `cemetery_permissions` (rol/drepturi per cimitir). Multi-organizație rămâne posibil în schemă (o identitate poate avea mai multe apartenențe), dar nu se implementează acum.
5. **RLS + filtre explicite în aplicație** (nu una sau alta). Rol PostgreSQL fără `BYPASSRLS`; context `organization/cemetery(-uri)` setat **tranzacțional** (`set_config(..., true)`), în aceeași tranzacție cu interogările. Schiță: `E2_010_rls.sql.draft`. Fără context, rolul aplicației nu vede nimic (închis implicit).
6. **Supabase TEST separat** (nume cu „test”), pentru migrări, RLS, teste PostgreSQL live, Storage, izolare, bootstrap/sync. Fără date reale, fără secretele producției. Guard-ul anti-pytest trebuie extins să accepte **explicit** proiectul de test (ref-ul lui într-o listă din repo), nu doar baze cu „test” în nume (în Supabase numele bazei e mereu `postgres`).
7. **Sincronizarea veche nu se repară acum** (poze, date de test din cloud, reconciliere → Sync V2 / Attachments).
8. **Etapa 2** se dezvoltă și se testează doar pe fixture-uri SQLite și pe proiectul Supabase TEST. Nicio migrare pe producție. Schițele sunt în `plan/etapa2/*.draft`, **în afara** `pg_migrations/` (runner-ul aplică automat tot ce e acolo).

### 13.1 Validare locală a schițelor (PostgreSQL 15 scratch, nu Supabase)
`plan/etapa2/validate_on_scratch_pg.py`: aplică 001–007, un fixture fictiv, apoi E2_008–E2_010 și verifică backfill, idempotență, izolare (citire/UPDATE/DELETE/INSERT), `WITH CHECK`, default de cimitir, două cimitire în context, scurgere după COMMIT, 8 fire pe 2 conexiuni partajate, legături multiple per dispozitiv, FK compus, revocare independentă. **28/28 PASS.** Neacoperit încă: pooler-ul Supabase real (mod tranzacție), cheile compuse (`cod_loc`, `sectoare`, `loc_extra`, `loc_holders`, FK `photos`), unicitatea cetățenilor pe organizație, rolul de autentificare pentru `identities`, `NOT NULL` după backfill.
