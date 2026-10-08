# Etapa 4 — Bootstrap MAIN ↔ Cloud (DOCUMENT DE PROIECTARE v2, aprobat cu modificări)

Stare: **v2 — arhitectură aprobată cu modificări** (roluri, swap/arhivă, media, retenție, SecretStore). Pasul 1 (identitate + pairing + stări + Flux A) se implementează exclusiv pe date temporare/locale; B/C/D/E rămân neimplementate. Nu s-a atins Supabase (production sau TEST), Render, migrările 008–013 ori datele Banovița.
Etapa 4 nu implementează Sync V2; folosește sincronizarea pe entități existentă (`entity_sync`, `media_sync`) doar **după** ce bootstrap-ul a
adus cele două părți în aceeași stare.

---------------------------------------------------------------------------------------------------------------------------------------

## 0. Principii (invariante care nu se negociază)

1. **MAIN rămâne local-first.** Fără cloud, MAIN lucrează complet. Bootstrap-ul nu blochează editarea locală mai mult decât momentul instantaneului (secunde) și poate fi oprit oricând.
2. **Fără ștergere ireversibilă a datelor live înainte de expirarea retenției.** „Înlocuirea” se face prin **epocă** (cloud: copiere verificată în `epoch_archive`, apoi scoaterea rândurilor din tabelele live **numai în aceeași tranzacție atomică** a swap-ului; vezi §10) și prin **mutarea în `_pre_rebuild_<ts>/`** (local). Datele arhivate se păstrează cel puțin cât retenția; purjarea este o operațiune separată, explicită, doar de proprietar. Singurul `DELETE` permis în afara swap-ului este curățarea zonei `bootstrap_stage` (date care n-au devenit niciodată live), strict după `bootstrap_id`.
3. **O singură sursă de adevăr pe flux**, declarată în preflight și afișată utilizatorului; niciodată „merge” automat între două baze nevide.
4. **Identitatea = UUID**: `organization_uid`, `cemetery_uid` (identice local și în cloud), `installation_uid` (PC), `device_uid`/`binding_uid` (cloud), `bootstrap_run_id`, `sync_epoch`.
5. **Confirmare legată de plan**: utilizatorul confirmă un `plan_hash` (hash al raportului de preflight). Dacă starea se schimbă între preflight și confirmare, operațiunea se oprește (anti-TOCTOU).
6. **Idempotent și reluabil**: fiecare lot are `(bootstrap_run_id, batch_no, sha256)`; reluarea după cădere nu dublează nimic.
7. **Verificare finală obligatorie** (manifest comparat pe ambele părți) înainte ca registrul să devină `BOUND`. Fără verificare reușită nu se face comutarea.
8. **Production și Banovița**: nicio etapă din acest document nu le atinge. Migrarea production este o etapă separată, cu runbook propriu, după Etapa 6.

## 1. Modelul de identitate și stare

### 1.1 Entități și unde trăiesc

| Concept | Local (MAIN) | Cloud (PostgreSQL) | Observații |
|---|---|---|---|
| Organizație | `registry_catalog.sqlite` / `organizations.uid` | `organizations.uid` | UUID generat de MAIN la creare; cloud o creează **cu același UID** (upsert idempotent) |
| Cimitir | `cemeteries.uid` = folderul `registries/<uid>/` | `cemeteries.uid` (+ `organization_uid`) | același UUID local = cloud; `status` DRAFT → ACTIVE → ARCHIVED |
| Instalare | `app_state.installation_uid` în catalog (generat la prima pornire) | `devices.installation_uid` | identifică PC-ul, NU registrul; nu se copiază cu registrul |
| Dispozitiv | — | `devices.device_uid` | un PC = un `device` per organizație |
| Legătură | setări în registru: `cloud_binding_uid`, `cloud_url`, `cloud_secret_ref` (cheia stă în SecretStore, nu în SQLite) | `device_cemetery_bindings` (`sync_key_hash`, `sync_epoch`, `last_change_seq`) | **un dispozitiv × un cimitir**; cheia e per legătură |
| Epocă | `sync_epoch` în registru | `sync_epoch` (propunere: la nivel de **cimitir**, `cemeteries.data_epoch`; legăturile o copiază) | schimbarea epocii = „altă istorie”; clienții cu epocă veche sunt respinși (409 `epoch_mismatch`) |
| Rulare bootstrap | `bootstrap_runs` (SQLite, în registru) | `bootstrap_runs` (propus, aditiv, nu în 008–013) | stare, checkpoint, plan_hash, rezultat |

### 1.2 Mașina de stări a unui registru (local, în `settings`: `sync_state`)

```
UNBOUND ──pairing──▶ PAIRED ──preflight──▶ PLANNED ──confirm──▶ BOOTSTRAPPING ──verify OK──▶ BOUND
   ▲                    │                      │                     │  └─ fail/abort ─▶ ABORTED ─▶ (reluare / rollback) ─▶ UNBOUND|PAIRED
   │                    └────────── cancel ────┘                     └─ cădere proces ─▶ BOOTSTRAPPING (reluabil din checkpoint)
   └── revocare/clonă detectată / epoch_mismatch ──────────────────────────────────────▶ REBUILD_REQUIRED (sync oprit; local rămâne utilizabil)
```
În C/E registrul poate trece în **`BOUND_MEDIA_PENDING`** (entități încărcate, fișiere încă în descărcare; vezi §11) și apoi în `BOUND`. Sincronizarea normală (outbox/pull/media) pornește **numai** în `BOUND` sau `BOUND_MEDIA_PENDING`. În toate celelalte stări MAIN funcționează local, fără erori de sync în UI (doar o stare explicită).

### 1.3 Preflight comun (raport de inspecție, aceeași structură pe ambele părți)

`InspectionReport` (local din SQLite, cloud printr-un endpoint de citire; propus `GET /api/bootstrap/inspect`):
* `organization_uid`, `cemetery_uid`, `schema_version`, `app_version`, `sync_epoch`, `status`;
* pe entitate (`locuri_de_veci, concesionari, contracte, inhumati, plati, amenajari_avize, users/staff, documents/media`): `count`, `max_version`, `digest` = SHA-256 peste liniile sortate `uid|version|hash_conținut_canonic`;
* media: `count`, `bytes`, `digest` peste `(entity_uid|sha256|size)` sortat;
* outbox local (`pending`, `conflict`), tombstone-uri (cloud: număr + `oldest_deleted_at`), `last_change_seq`, dispozitive active legate;
* mediu: spațiu liber local, versiune Python/SQLite, ceas (diferență față de server, limită ±5 min), conectivitate, rol/drepturi ale utilizatorului;
* `empty` = toate numărătorile entităților utile sunt 0 (utilizatorii/setările implicite nu contează — aceeași regulă ca `detect_legacy`).

### 1.4 Matricea de decizie (selectarea automată a fluxului)

| Local | Cloud | Legătură | Flux propus | Observație |
|---|---|---|---|---|
| gol | gol / inexistent | — | **A** Registru nou | |
| nevid | gol / inexistent | — | **B** MAIN → cloud nou | |
| gol | nevid | — | **C** Cloud → MAIN nou | |
| nevid | nevid, **digest identic** | lipsă | **LINK** (legare fără transfer; doar pairing + epocă) | caz sigur: aceleași date |
| nevid | nevid, digest diferit | orice | **BLOCAT** — se oferă doar D sau E, explicit, cu avertisment | fără merge automat |
| orice | orice | `REBUILD_REQUIRED` | **E** (sau D de către proprietar) | |
| nevid | gol dar legătură BOUND (cloud a fost golit!) | BOUND | **BLOCAT + alarmă** (posibilă ștergere accidentală în cloud) → doar D, cu confirmare dublă | |

Regula „sursă goală ≠ adevăr”: **niciun flux nu poate lua o sursă goală drept adevăr pentru o țintă nevidă** (excepție doar prin `--allow-empty-source` cu frază tastată, în D/E).

## 2. Mecanisme de siguranță comune tuturor fluxurilor

| Mecanism | Descriere |
|---|---|
| Autorizare | A/B/C: administrator al organizației în cloud + `manage_config` local. D/E: **proprietar** (rol `owner`/admin de organizație) + reintroducerea parolei + frază tastată. Toate se scriu în jurnalul de audit (cloud + local). |
| Pairing | Cod de unică folosință (hash în `device_pairing_codes`, expiră în 15 min, max. 5 încercări greșite, legat de `organization_uid` și, opțional, `cemetery_uid`), emis de **Organization Owner** al organizației (vezi §8). MAIN îl răscumpără cu `installation_uid` + nume dispozitiv → primește `device_uid`, `binding_uid`, **cheia de sincronizare (afișată o singură dată; doar hash în cloud)**, `sync_epoch`, `last_change_seq=0`. Cheia se salvează **numai în SecretStore** (DPAPI pe Windows; vezi §9); în SQLite rămâne doar `cloud_secret_ref`. |
| Detecția clonelor | Registrul reține `bound_installation_uid`. Dacă folderul registrului e copiat pe alt PC (alt `installation_uid`) → sync dezactivat, stare `REBUILD_REQUIRED`/„re-pair necesar”; editarea locală continuă. Previne două PC-uri cu aceeași cheie și același cursor. |
| Lock | Cloud: `pg_advisory_lock(hash(cemetery_uid))` + rând `bootstrap_runs` unic activ pe cimitir. Local: `registries/<uid>/bootstrap.lock` (proces + timp) + `sync_state=BOOTSTRAPPING`; `activate()`/comutarea registrului este refuzată cât timp rulează un bootstrap fixat (`pinned()` existent). |
| Gard de epocă | După D/E, orice cerere cu epocă veche primește 409 `epoch_mismatch` și **nu** poate reînvia rânduri șterse (învie-rânduri-șterse este riscul principal al tombstone-urilor). |
| Instantaneu înainte de risc | Local: copie consistentă (`cimitir_backup.backup` / API SQLite backup) înainte de orice flux care schimbă SQLite. Cloud: **epocă arhivată** (rânduri vechi păstrate cu `superseded_epoch`), nu copie separată; export logic tenant-scoped opțional în storage. |
| Arhivare în loc de ștergere | Cloud: înlocuire = epocă nouă construită în `bootstrap_stage`, apoi swap atomic (arhivare verificată + scoatere din live + inserare, **o singură tranzacție**, strict pe `cemetery_uid`). Local: `rename` în `_pre_rebuild_<ts>/`. Purjarea epocilor arhivate: minim 30 zile (configurabil 30–365), doar proprietar, comandă explicită cu `plan_hash`. |
| Confirmare | Ecran cu numere concrete (`N` locuri, `M` fișiere / `X` MB, `D` dispozitive afectate), sursa, ținta, `plan_hash`; pentru D/E: tastezi numele cimitirului **și** cuvântul `ÎNLOCUIESC`. Confirmarea expiră în 10 min. |
| Reluare | Checkpoint după fiecare lot (`bootstrap_runs.checkpoint = {phase, entity, last_seq/uid, batch_no, media_done}`); după cădere, MAIN reia din checkpoint; cloud ignoră loturile duplicate (`sha256` + `batch_no`). |
| Verificare finală | Manifest-ul sursei vs. țintei: numărători + `digest` pe entitate + media (`count`, `bytes`, `digest`) + FK orfane = 0 + unicitate `cod_loc` + `PRAGMA integrity_check`/`foreign_key_check` local + numărători cloud sub RLS; plus eșantion 1% comparat câmp cu câmp și hash-ul proiecției hărții. Rezultatul = „raport de bootstrap” salvat local și în cloud. |
| Observabilitate | Progres pe faze (UI + log), erori clasificate: `retryable` (rețea), `fatal` (date invalide), `blocked` (preflight). Nicio operațiune „în tăcere”. |

## 3. Cele 5 fluxuri

Notație: **S** = sursa de adevăr, **T** = ținta. „Preflight” = raportul din 1.3 + verificările specifice.

### A. Registru nou — MAIN gol + cloud gol

| Aspect | Proiectare |
|---|---|
| Sursa de adevăr | Niciuna (creare). Identitatea (UUID organizație/cimitir) vine din catalogul local; cloud o adoptă. |
| Preflight | Local gol (regula `empty`); cloud: organizația/cimitirul lipsesc sau există `DRAFT` fără date; rol admin; conectivitate; ceas ±5 min; nume cimitir unic în organizație. Dacă există organizația în cloud cu alt `official_name`/CUI → avertisment (potențial duplicat), fără blocare. |
| Confirmare | Simplă (un dialog): „Creez organizația X și cimitirul Y în cloud cu aceleași identificatoare”. Fără frază tastată (nu se pierde nimic). |
| SQLite | Neschimbat în afară de setări: `organization_uid`/`cemetery_uid` (deja), `sync_state`, `bound_installation_uid`, `sync_epoch=1`, `cloud_binding_uid`, cursori 0. Triggerele outbox se instalează la `BOUND`. |
| PostgreSQL | Upsert idempotent `organizations`, `cemeteries(status=DRAFT, data_epoch=1)`; la final `ACTIVE`. Nicio altă tabelă nu se scrie. |
| Media | Niciuna. Se creează prefixul de storage al cimitirului (gol). |
| Outbox | Gol. Triggerele se instalează după legare; primele modificări locale intră normal în outbox. |
| Tombstone-uri | Niciunul (istorie goală). `epoch=1`. |
| Device binding | Pairing complet (vezi §2): creare `devices` (dacă nu există), `device_cemetery_bindings`, cheie nouă, `sync_epoch=1`. |
| Prevenirea ștergerilor | Operațiune doar aditivă; refuză dacă cimitirul există `ACTIVE` cu date (→ matricea trimite la C/LINK). |
| Verificare finală | `GET /inspect` cloud = 0 peste tot; `ping` cu cheia nouă OK; un `push` de probă NU se face cu date reale (se verifică doar autentificarea și epoca). |
| Rollback | Înainte de `BOUND`: cloud rămâne `DRAFT` (arhivabil), local revine la `UNBOUND` (șterge doar setările de legare; cheia se revocă din cloud). După `BOUND`: se poate dezlega (revocă legătura), fără pierderi de date. |

### B. MAIN existent → cloud nou

| Aspect | Proiectare |
|---|---|
| Sursa de adevăr | **MAIN** (S). Cloud (T) trebuie să fie gol/DRAFT. |
| Preflight | Local: `integrity_check`, `foreign_key_check`, conflicte/outbox vechi (outbox vechi de dinainte de legare se arhivează, nu se trimite — starea completă îl subsumează), `cod_loc` unice, referințe orfane = 0, spațiu liber ≥ 2× baza, media: toate fișierele din `documents` există pe disc (lista lipsurilor se arată; lipsurile nu blochează dacă utilizatorul le acceptă). Cloud: cimitir gol/DRAFT, schema ≥ 013, fără alt bootstrap activ. Estimare timp/volum. |
| Confirmare | Dialog cu numere (`N` înregistrări / `M` fișiere / `X` MB), „MAIN este sursa; cloud-ul va primi o copie”. Bifă „am făcut backup” (oferă backup automat). Fără frază tastată (ținta e goală). |
| SQLite | 1) backup consistent; 2) instalare triggere outbox **înainte** de instantaneu (modificările ulterioare nu se pierd); 3) instantaneu citit într-o tranzacție `BEGIN IMMEDIATE` scurtă (copie prin API-ul de backup într-un fișier temporar; editarea locală continuă pe baza vie, modificările merg în outbox); 4) după succes: `sync_state=BOUND`, `last_change_seq` = al serverului la finalizare, rândurile primesc `version` = cea atribuită de server (1) prin mapare uid→version; 5) outbox-ul acumulat în timpul transferului se trimite normal, cu `base_version` corect. |
| PostgreSQL | Rânduri scrise în **epoca de bootstrap** a unui cimitir `DRAFT` (invizibil pentru API-ul normal): ordinea `ORDER` (părinți → copii), traducere FK prin uid, `version`/`change_seq` atribuite de triggerele existente, RLS pe `cemetery_uid`. Finalizare atomică: verificare manifest ⇒ `status=ACTIVE`, activare legătură. Un lot greșit nu strică nimic (se poate relua; tabelele au chei compuse tenant-safe). |
| Media | Încărcare **chunked** existentă (`/api/media/{id}/chunks`, `complete`) cu `sha256`; dedup pe `sha256`; obiecte în prefix de staging `bootstrap/<run_id>/` mutat la cheia finală la `complete`; `media_objects` se publică la finalizare. Reluare pe fișier. Fișierele lipsă pe disc → raportate, rândul rămâne cu marcaj `media_missing` (nu se inventează). |
| Outbox | Vezi SQLite: arhivare outbox pre-existent (`outbox_archive_<ts>.json`), apoi outbox nou captează delta. În `BOOTSTRAPPING` auto-sync normal este oprit; delta se trimite imediat după `BOUND`. |
| Tombstone-uri | Cloud: gol (istorie nouă). MAIN nu are tombstone-uri locale; ștergerile locale dinainte de bootstrap nu se transmit (rândul pur și simplu nu există în instantaneu). Ștergerile din timpul transferului → operație `delete` în outbox (după BOUND) → tombstone cloud normal. |
| Device binding | Pairing; `sync_epoch` = cel al cimitirului (1); `last_change_seq` setat de server la finalizare; `bound_installation_uid` salvat. |
| Prevenirea ștergerilor | Nicio ștergere în cloud sau local. Gard: refuză dacă cloud-ul NU e gol (matricea de decizie). Dacă cloud-ul are conținut → mesaj „folosește D (rebuild) sau LINK/E”. |
| Verificare finală | Manifest local (din instantaneu) = manifest cloud (numărători, `digest` per entitate, media `count/bytes/digest`); FK orfane cloud = 0; eșantion 1%; hash proiecție hartă; apoi `pull` de probă cu cursor 0 nu aduce nimic neașteptat. La nepotrivire: cimitirul rămâne `DRAFT`, local `ABORTED`, raport cu diferențele. |
| Rollback | Înainte de finalizare: cloud `DRAFT` nu a fost vizibil → se marchează `ARCHIVED` (sau epoca de bootstrap se abandonează); staging media se curăță prin GC amânat; local: `sync_state=UNBOUND`, triggere scoase, **baza locală neatinsă** (nu a fost modificată). După finalizare: dezlegare (revocare legătură) fără pierderi; sau D pentru refacere. |

### C. Cloud existent → MAIN nou

| Aspect | Proiectare |
|---|---|
| Sursa de adevăr | **Cloud** (S). MAIN (T) gol. |
| Preflight | Local: registru nou/gol (sau creat acum cu **același** `cemetery_uid` ca în cloud — registrul local se creează pe baza identificatorilor din cloud, nu invers), spațiu liber ≥ estimarea din cloud (`bytes` media + 2× baza), versiune aplicație compatibilă cu schema cloud; cloud: cimitir `ACTIVE`, utilizatorul/pairing-ul are drept de citire, `data_epoch`, `snapshot_seq` la momentul preflight; număr de dispozitive deja legate (informativ). |
| Confirmare | Dialog: „Descarc `N` înregistrări și `M` fișiere (`X` MB) din cloud în registrul gol”. Fără frază tastată (ținta e goală). Dacă registrul local NU e gol → flux blocat (matricea) – se oferă E. |
| SQLite | Registru nou (`database.sqlite.building`), schema creată de `init_db`, încărcare pe entități în ordinea `ORDER`, un lot = o tranzacție cu `_sync_applying=1` (nu intră în outbox), FK prin uid → id local. La final: verificare, **rename atomic** `building → database.sqlite`, setare cursori = `snapshot_seq` (per entitate), `sync_epoch`, `BOUND`. MAIN nu devine utilizabil pentru editare până nu s-a încheiat faza de entități (nu există stare parțială vizibilă). |
| PostgreSQL | Doar **citire**: instantaneu consistent (`REPEATABLE READ`, `change_seq ≤ snapshot_seq`), paginat pe `change_seq`. Se înregistrează în `bootstrap_runs` cine a descărcat și când (audit). Nimic nu se modifică în cloud. |
| Media | Politică propusă: **metadate imediat, fișiere în fundal** (stări per document: `missing → downloading → present`), prioritate pentru fișierele deschise recent; fiecare fișier verificat `sha256` la scriere; fișiere corupte → refăcute. MAIN afișează „fișier în curs de descărcare”. Alternativ (opțiune): descărcare completă înainte de BOUND. |
| Outbox | Gol și neactiv în timpul încărcării; triggerele se instalează după rename (modificările făcute în MAIN după acel moment intră în outbox normal). |
| Tombstone-uri | Nu se descarcă (registrul e nou: lipsa rândului = șters). Cursorul de tombstone începe la `snapshot_seq`; ștergerile ulterioare vin prin `pull` normal. **Gard de orizont**: dacă `snapshot_seq` < orizontul de retenție al tombstone-urilor, bootstrap-ul e oricum corect (instantaneu complet), dar un dispozitiv vechi (nu acesta) cu cursor sub orizont trebuie să facă E. |
| Device binding | Pairing nou pentru acest PC (alt `device_uid` decât al celorlalte PC-uri); cheie nouă; mai multe MAIN pe același cimitir sunt permise (limită configurabilă). Se salvează `bound_installation_uid`. |
| Prevenirea ștergerilor | Operațiune read-only pe cloud; local scrie doar în `*.building`. Dacă `activate()` găsește registrul țintă nevid → refuz. Nu se suprascrie niciun registru existent. |
| Verificare finală | Manifest cloud (la `snapshot_seq`) = manifest calculat din SQLite după încărcare (aceleași `digest`-uri, FK orfane = 0, `integrity_check`); media: `count/bytes/digest` când faza de fundal se termină (BOUND poate începe după entități; „media completă” e o a doua bifă). |
| Rollback | Înainte de rename: se abandonează `database.sqlite.building` (nimic din registrul real nu s-a schimbat), `UNBOUND`, legătura se revocă din cloud. După rename: registrul poate fi mutat în `_pre_rebuild_<ts>/` (e doar o copie a cloud-ului), fără pierderi. |

### D. Rebuild Cloud from MAIN (MAIN → cloud, înlocuire)

| Aspect | Proiectare |
|---|---|
| Sursa de adevăr | **MAIN** (S) — declarat explicit: „datele din cloud pentru acest cimitir vor fi înlocuite”. Folosit după corupere/ștergere în cloud sau migrare. |
| Preflight | Tot preflight-ul B pe MAIN (+ `integrity_check` obligatoriu) **și** inspecția cloud: câte rânduri/fișiere/dispozitive/cereri vor fi *înlocuite*, ce rânduri există doar în cloud (ex. modificate din panoul web de la ultimul sync) → listă „se vor pierde din epoca activă (rămân în arhivă)”. Verifică starea legăturii, outbox local (de golit sau subsumat), alți utilizatori conectați. Gard: sursă goală + țintă nevidă = refuz (excepție cu `--allow-empty-source` + frază). |
| Confirmare | **Dublă**: (1) ecran cu diferențele (cloud-only `K` rânduri/`F` fișiere; MAIN-only; `D` dispozitive care vor deveni `REBUILD_REQUIRED`); (2) tastezi numele cimitirului + `ÎNLOCUIESC` + reintroduci parola; doar proprietar. Expiră în 10 min; legată de `plan_hash`. Se creează automat backup local și export logic al epocii active. |
| SQLite | Neschimbat în afară de setări (`sync_epoch=E+1`, `BOUND`, cursori resetați la valorile epocii noi). Backup obligatoriu înainte. Instantaneu și delta ca în B (triggere înainte de instantaneu). |
| PostgreSQL | Epoca nouă E+1 se construiește în `bootstrap_stage` (invizibilă API-ului normal); la final, **swap atomic** (§10): arhivare verificată a epocii E în `epoch_archive`, scoaterea rândurilor E din tabelele live și inserarea E+1, `data_epoch=E+1`, legăturile celorlalte dispozitive → `epoch_stale` — totul într-o tranzacție, strict pe `cemetery_uid`. Fără ștergere ireversibilă înainte de expirarea retenției. |
| Media | Reutilizare pe `sha256`: obiectele existente cu același hash nu se reîncarcă (legate în epoca nouă); cele noi urcă ca în B; obiectele orfane ale epocii vechi rămân (GC doar după retenție). Lipsurile locale raportate. |
| Outbox | Outbox-ul local de dinainte se arhivează (conținutul e subsumat de instantaneu). Delta acumulată în timpul transferului se trimite după `BOUND`. |
| Tombstone-uri | Epoca E: tombstone-urile se păstrează arhivate (`epoch=E`). Epoca E+1 pornește cu tombstone-uri goale; dispozitivele cu epocă veche sunt **respinse** (409 `epoch_mismatch`) ⇒ nu pot reînvia rânduri șterse. |
| Device binding | MAIN executant: legătura continuă pe E+1 (cursor nou). Celelalte dispozitive: `REBUILD_REQUIRED` — fac E (sau, dacă au modificări nesincronizate, mai întâi „export pachet modificări” `unsynced_<ts>.json` pentru recuperare manuală). Cheile nu se schimbă (nu se revocă) dar sunt valabile doar pe epoca nouă după re-sync. |
| Prevenirea ștergerilor | Arhivare verificată + swap atomic (§10); retenție; confirmare dublă + reautentificare; gard sursă-goală; blocare dacă există altă rulare activă; refuz dacă ceasul diferă >5 min; jurnal de audit imuabil (cine, când, `plan_hash`). |
| Verificare finală | Ca în B, pe epoca E+1 **înainte** de comutarea pointerului; după comutare: smoke (`ping`, `pull` cursor 0 = manifestul sursei), iar epoca E rămâne intactă pentru comparație. |
| Rollback | Înainte de comutare: epoca E+1 se abandonează (E activă neatinsă). După comutare (în retenție): **comutare înapoi la E** printr-o comandă de proprietar (o tranzacție), cu aceleași gărzi; MAIN executant revine la `REBUILD_REQUIRED`/E. După retenție: doar prin restaurare din export. |

### E. Rebuild MAIN from Cloud (cloud → MAIN, înlocuire)

| Aspect | Proiectare |
|---|---|
| Sursa de adevăr | **Cloud** (S) — pentru un MAIN stricat, pierdut, clonat sau cu epocă veche (`REBUILD_REQUIRED`). |
| Preflight | Local: inspecția registrului curent (poate fi corupt — `integrity_check` doar informativ), **modificări nesincronizate** (outbox `pending`+`conflict`), diferența față de cloud (rânduri doar locale), spațiu liber ≥ 2× (copia veche + noua). Cloud: epocă activă, `snapshot_seq`, drepturi, numere. Se arată explicit ce se pierde local „ca sursă activă” (nu ca fișiere). |
| Confirmare | **Dublă** dacă există modificări nesincronizate sau diferențe locale: tastezi numele cimitirului + `ÎNLOCUIESC MAIN`. Simplă dacă outbox gol și baza locală = cloud (caz recuperare după clonare). Doar utilizator cu `manage_config`/proprietar. |
| SQLite | 1) export `unsynced_<ts>.json` (outbox + rândurile cu `version` > ultima sincronizată) în `_pre_rebuild_<ts>/`; 2) registrul curent (`database.sqlite`, media, cache, outbox) se **mută** în `registries/<uid>/_pre_rebuild_<ts>/`; 3) construcție ca în C (`*.building` → verificare → rename atomic); 4) cursori = `snapshot_seq`, `sync_epoch` = epoca curentă; `BOUND`. Utilizatorii/parolele revin din cloud (lease). |
| PostgreSQL | Doar citire (ca în C) + actualizare legătură (`last_change_seq`, `sync_epoch`) și audit. Dacă MAIN avea binding revocat/clonă: se emite re-pair (cod nou) și binding vechi marcat `replaced_by_binding_uid`. |
| Media | Reutilizare locală: fișierele din `_pre_rebuild_<ts>/media` cu `sha256` egal cu cele din cloud se **leagă dur/copiază** în registrul nou (fără re-descărcare); restul se descarcă (politica din C); fișierele existente doar local (nesincronizate) rămân în `_pre_rebuild_<ts>/` și apar în raport. |
| Outbox | Vechiul outbox se arhivează (în `_pre_rebuild_<ts>/`), nu se golește în cloud. Nou: gol; opțional „reaplică pachetul de modificări” ca operații noi cu `base_version` din cloud (flux separat, manual, conflict-aware) — **nu automat**. |
| Tombstone-uri | Registrul nou nu conține rânduri șterse în cloud (instantaneu viu); cursor tombstone = `snapshot_seq`. Dacă `snapshot_seq` e peste orizont, nu există risc de reînviere: registrul nou nu poartă nimic vechi. Rândurile locale șterse în cloud dispar odată cu înlocuirea (se văd în raportul de diferențe). |
| Device binding | Același `device_uid`/`installation_uid`; cheia reînnoită (rotire) dacă preflight-ul a detectat clonă/compromitere; cursor resetat la snapshot; `bound_installation_uid` actualizat. |
| Prevenirea ștergerilor | Fără `rmtree`: vechiul registru e **mutat**, nu șters; ștergerea lui doar manual sau după retenție, cu confirmare. Refuz dacă `_pre_rebuild_<ts>/` nu se poate crea (disc plin). Mutarea se face doar după ce `*.building` e complet și verificat. |
| Verificare finală | Ca în C (manifest cloud la `snapshot_seq` = manifest calculat local; `integrity_check`; FK; media `count/bytes/digest` după faza de fundal). Test de fum: deschiderea registrului, login, căutare `cod_loc`. |
| Rollback | Oricând înainte de rename: abandon `*.building` (registrul vechi neatins). După rename: **swap invers** (mută `_pre_rebuild_<ts>/` înapoi) — disponibil cât timp folderul există; sync-ul se oprește la `REBUILD_REQUIRED` dacă epoca nu mai corespunde. |

## 4. Propuneri de contract (doar design; nu se implementează în Etapa 4-design)

**Cloud, aditiv (migrare nouă ≥ 014, NU modifică 008–013 și NU se scrie în Pasul 1):** `cemeteries.data_epoch`, `epoch_registry(cemetery_uid, epoch, state{building,active,archived,purged}, ...)`, scheme `bootstrap_stage` și `epoch_archive` (copii ale tabelelor sincronizate + `bootstrap_id`, `epoch`, `archived_at`), `bootstrap_runs(bootstrap_id, cemetery_uid, kind A–E, device_uid, plan_hash, state{planned,running,verifying,completed,failed,rolled_back}, checkpoint JSONB, manifest_source, manifest_target, started_at, finished_at, error)`, `audit_log` imuabil, `organization_roles` (§8). **`bootstrap_stage` și `epoch_archive` NU sunt expuse către Data API/public/anon/authenticated:** `REVOKE ALL` pentru aceste roluri, fără politici RLS permisive, schemele nu intră în `exposed_schemas`; acces doar rolului backend autorizat.

**Endpoint-uri (prefix `/api/bootstrap`, autentificare prin cheia legăturii sau token de pairing):** `inspect`, `pair` (răscumpărare cod), `begin(kind, plan_hash)`, `batch(run_id, entity, batch_no, sha256, rows)`, `media/*` (reuse chunked), `finalize(run_id, manifest)`, `abort(run_id)`, `status(run_id)`, `snapshot/pull(run_id, entity, after_seq)` pentru C/E, `epoch/switch` (proprietar, D).

**Local:** `shared/cimitir_bootstrap.py` (mașina de stări, preflight, manifest), reutilizează `cimitir_registry` (identitate/activare), `cimitir_backup` (backup), `entity_sync`/`media_sync` (formatul pe fir, outbox), `cimitir_registry_adopt` (model „plan → confirmare → verificare → rollback”).

## 5. Ce există deja vs. ce lipsește

| Există | Lipsește (de proiectat/implementat în Etapa 4) |
|---|---|
| Tenancy 008–013 (organizations, cemeteries, devices, bindings cu `sync_key_hash`, `sync_epoch`, `last_change_seq`, `device_pairing_codes`), RLS, chei compuse, tombstone cu tenant | Utilizarea efectivă a `sync_epoch` (coloană fără cod) și a `device_pairing_codes` (fără cod); `data_epoch`/arhivare epoci; `bootstrap_runs`; audit imuabil |
| Sync pe entități (`push/pull`, `change_seq`, tombstone-uri, conflicte, op_id idempotent), media chunked cu `sha256` | Încărcare/descărcare **în masă** reluabilă cu manifest; mod „instantaneu consistent” pe cloud; staging media |
| Registre locale (Etapa 3), adoptare cu dry-run/rollback, backup consistent | `installation_uid`, `bound_installation_uid`, detecția clonelor, `sync_state`, lock local de bootstrap |
| Preflight/guards pentru migrări (protecție production) | Preflight comun local+cloud, `plan_hash`, confirmări tastate, raport de bootstrap |

## 6. Plan de testare (la implementare; numai date temporare)

* Matrice 2×2 (gol/nevid × gol/nevid) + legătură BOUND/REBUILD_REQUIRED → fluxul corect sau blocare.
* Idempotență: același lot de 2–3 ori; reluare după `kill -9` la fiecare fază (checkpoint), după rețea tăiată, după disc plin.
* Date: același `cod_loc` în două cimitire; ștergeri în timpul transferului; fotografii cu același nume; fișiere lipsă pe disc; media mare întreruptă.
* Epoci: dispozitiv cu epocă veche respins (nu reînvie rânduri șterse); rollback D în retenție; swap invers E.
* Securitate: cod de pairing expirat/refolosit, cheie a altui cimitir, clonă pe alt `installation_uid`, utilizator fără drept de proprietar.
* Verificare manifest: coruperea intenționată a unui rând/fișier în tranzit trebuie detectată și bloca `BOUND`.
* Local-first: MAIN rămâne utilizabil fără internet în toate stările; UI-ul nu se blochează.
* Mediu: PostgreSQL efemer local / schema izolată; **Supabase TEST doar la aprobare explicită** și numai scheme izolate; production și Banovița exclus.

## 7. Decizii aprobate (v2)

| Nr. | Decizie | Rezultat final |
|---|---|---|
| 1 | UID-uri | **Aprobat.** Același `organization_uid` și `cemetery_uid` local și cloud; definitive, niciodată reutilizate (nici după arhivare). |
| 2 | Cine creează ce | **Modificat.** Platform Admin creează organizația inițială; **Organization Owner** creează/administrează cimitire numai în propria organizație; MAIN poate *propune* un cimitir prin cod de provizionare emis de Owner. Fără intervenția administratorului central pentru fiecare cimitir (§8). |
| 3 | Epocă/arhivă | **Aprobat cu corecție.** `bootstrap_stage` + `epoch_archive`; principiul este „fără ștergere ireversibilă a datelor live înainte de expirarea retenției”; DELETE din live permis doar în tranzacția atomică a swap-ului, după copierea verificată în arhivă (§10). Schemele nu sunt expuse Data API/public/anon/authenticated. |
| 4 | Surse nevide și diferite | **Aprobat.** Blocare, fără merge automat; digest identic = LINK. |
| 5 | Media | **Aprobat cu precizare** (§11): B/D media completă și verificată înainte ca epoca să devină ACTIVE; C/E metadate imediat + media în fundal, stare `BOUND_MEDIA_PENDING`; dedup SHA-256 scoped la `(organization_uid, cemetery_uid)`; cale de storage cu organizație + cimitir + epocă/bootstrap. |
| 6 | D/E | **Aprobat cu precizare.** Organization Owner **sau** rol explicit `REBUILD`; reautentificare/confirmare puternică + `plan_hash` + audit; celelalte dispozitive → `REBUILD_REQUIRED`. |
| 7 | Tombstone-uri | **Modificat.** Retenție implicită **180 zile**, configurabilă **90–365** per organizație; dispozitiv sub orizont → `REBUILD_REQUIRED`. |
| 8 | Cheia de sync | **Modificat.** `SecretStore` de acum (§9); DPAPI pe Windows; fallback explicit doar dev/test; fără plaintext în SQLite/loguri/backup-uri obișnuite. |
| 9 | Ordine | **Aprobat.** A → pairing/identitate/stări → manifest/preflight → B → C → D/E; fiecare pas închis și testat înainte de următorul. |

Aprobate în plus: `bootstrap_id`; `plan_hash`; stări persistente `planned, running, verifying, completed, failed, rolled_back`; idempotență per lot; counts + digest + SHA-256 + verificare FK orfane; resume/checkpoint; lock simultan cloud + local; 409 `epoch_mismatch`; arhivarea outbox-ului înainte de snapshot; `_pre_rebuild_<timestamp>` local; interdicția ca o sursă goală să suprascrie automat o țintă nevidă; operații limitate strict la un singur `cemetery_uid`.

## 8. Roluri și domeniul de acțiune

| Rol | Poate | Nu poate |
|---|---|---|
| **Platform Admin** | creează/dezactivează **organizații**, desemnează primul Organization Owner | nu e necesar pentru cimitire noi; nu face rebuild în numele unei organizații fără rol în ea |
| **Organization Owner** (per organizație) | creează/arhivează cimitire **în propria organizație**, emite coduri de pairing/provizionare, revocă legături, rulează D/E, purjează epoci arhivate, setează retenția | nu atinge alte organizații |
| **REBUILD** (rol explicit în organizație) | rulează D/E (cu reautentificare) | nu creează cimitire, nu purjează |
| **Admin** (organizație) | administrare uzuală | nu rulează D/E |

* Toate verificările sunt `can(principal, action, organization_uid[, cemetery_uid])` și se aplică **înainte** de orice citire/scriere; o cerere cu `cemetery_uid` din altă organizație primește eroare de scop (tratată ca „inexistent”), fără scurgeri de informații.
* **Cod de provizionare** (emis de Owner, `cemetery_uid` nul): MAIN îl răscumpără și propune `(cemetery_uid, nume)`; cloud creează cimitirul `DRAFT` în organizația codului, **cu UID-ul propus** (respins dacă UID-ul există deja oriunde sau aparține altei organizații). **Cod de pairing** (cu `cemetery_uid`): leagă dispozitivul la un cimitir existent al aceleiași organizații.
* Cheia legăturii autentifică doar `(device × cemetery_uid)`-ul ei; orice acțiune pe alt `cemetery_uid` este respinsă.

## 9. SecretStore

Interfață `SecretStore`: `set(name, value)`, `get(name)`, `delete(name)`, `exists(name)`; nume = `binding/<binding_uid>`. SQLite păstrează doar `cloud_secret_ref` (nume), niciodată valoarea.

| Backend | Utilizare |
|---|---|
| `DpapiSecretStore` | **Windows (implicit comercial).** `CryptProtectData`/`CryptUnprotectData` (domeniu utilizator, entropie specifică aplicației); blob-ul criptat stă în `<DATA_DIR>/secrets/` (nu în registru, nu în backup-urile obișnuite ale registrului). |
| `TestEncryptedSecretStore` | **EXCLUSIV TEST.** Fernet (AES-128-CBC + HMAC-SHA256) cu cheie **efemeră, doar în memorie** (un proces copil de test o primește prin `CIMITIR_TEST_SECRET_KEY`). Fără cheie, fișierele sunt nedecriptabile. Cere `CIMITIR_ALLOW_TEST_SECRETSTORE=1`; **refuzat** când `CIMITIR_COMMERCIAL=1` sau aplicația e înghețată (PyInstaller). |
| `MemorySecretStore` | **EXCLUSIV TEST**, nu persistă nimic; aceleași interdicții ca mai sus. |
| ~~`DevFileSecretStore`~~ | **ELIMINAT** (folosea base64, care nu e criptare). Nu există niciun backend base64/plaintext. |

Alegerea implicită: Windows → DPAPI; alt sistem fără backend explicit de test → `SecretStoreUnavailable` (nu se coboară tăcut la plaintext). Cheia nu apare în SQLite, loguri, rapoarte sau `cimitir_backup`; verificarea se face prin test cu căutare în fișierele registrului.

## 10. Swap atomic și rollback (algoritm exact)

Notații: `:c` = `cemetery_uid`, `:o` = `organization_uid`, `:b` = `bootstrap_id`, `:E` = epoca curentă, `:expected_E` = epoca din plan. `T` = fiecare tabel sincronizat (ordine părinți→copii la inserare, copii→părinți la scoatere). Rolul folosit: **backend autorizat** (singurul cu drepturi pe `bootstrap_stage`/`epoch_archive`).

```sql
BEGIN ISOLATION LEVEL SERIALIZABLE;
SET LOCAL cimitir.bootstrap_swap = 'on';            -- triggerele live nu creează tombstone-uri/change_seq pentru scoaterea epocii vechi
SELECT pg_advisory_xact_lock(hashtextextended('bootstrap:' || :c, 0));
-- 0. precondiții (orice abatere => RAISE EXCEPTION => ROLLBACK automat)
SELECT state FROM bootstrap_runs WHERE bootstrap_id = :b AND cemetery_uid = :c FOR UPDATE;        -- trebuie 'verifying'
SELECT data_epoch FROM cemeteries WHERE uid = :c AND organization_uid = :o FOR UPDATE;            -- trebuie = :expected_E
-- 1. arhivare (pentru fiecare T)
INSERT INTO epoch_archive.T SELECT :b, :E, now(), t.* FROM live.T t WHERE t.cemetery_uid = :c AND t.organization_uid = :o;
-- 2. verificarea copiei: count + digest(live.T, :c) = count + digest(epoch_archive.T, :b, :E); altfel RAISE
-- 3. scoatere din live (copii -> părinți) — PERMIS DOAR AICI: după copia verificată, în aceeași tranzacție
DELETE FROM live.T WHERE cemetery_uid = :c AND organization_uid = :o;
-- 4. inserare epoca nouă (părinți -> copii), strict din stage-ul acestui bootstrap
INSERT INTO live.T (...) SELECT ... FROM bootstrap_stage.T s WHERE s.bootstrap_id = :b AND s.cemetery_uid = :c AND s.organization_uid = :o;
-- 5. verificare: count + digest(live.T, :c) = manifest_source din plan; FK orfane = 0; altfel RAISE
-- 6. comutarea epocii
UPDATE cemeteries SET data_epoch = :E + 1, status = 'ACTIVE', updated_at = now() WHERE uid = :c AND organization_uid = :o;
UPDATE epoch_registry SET state = 'archived', archived_at = now() WHERE cemetery_uid = :c AND epoch = :E;
UPDATE epoch_registry SET state = 'active' WHERE cemetery_uid = :c AND epoch = :E + 1;
UPDATE device_cemetery_bindings SET status = 'epoch_stale' WHERE cemetery_uid = :c AND binding_uid <> :executor;
UPDATE device_cemetery_bindings SET sync_epoch = :E + 1, last_change_seq = :new_seq WHERE binding_uid = :executor AND cemetery_uid = :c;
UPDATE bootstrap_runs SET state = 'completed', finished_at = now() WHERE bootstrap_id = :b;
INSERT INTO audit_log (...) VALUES (... :b, :plan_hash, :principal ...);
COMMIT;    -- sau ROLLBACK: nimic nu s-a schimbat; stage-ul rămâne pentru resume
```

* Dacă orice pas eșuează, PostgreSQL anulează tranzacția: tabelele live, arhiva și epoca rămân exact ca înainte. `bootstrap_stage` se păstrează pentru resume sau se curăță după `:b` la `rolled_back`.
* După `COMMIT`, MAIN care nu a primit confirmarea întreabă `status(:b)` și își finalizează local idempotent.
* **Rollback în retenție** (doar Owner/REBUILD, nou `plan_hash`, tot atomic, `SET LOCAL cimitir.bootstrap_swap='on'`): precondiție `data_epoch = :E+1` și `epoch_registry(E).state='archived'`; (a) arhivează epoca live E+1 în `epoch_archive` (marcată `:E+1`) cu verificare count+digest; (b) `DELETE` live pentru `:c`; (c) `INSERT INTO live.T SELECT ... FROM epoch_archive.T WHERE epoch = :E AND cemetery_uid = :c`; (d) verificare față de manifestul epocii E; (e) `data_epoch = :E`, stări în `epoch_registry`, legăturile revin la `epoch_stale` pentru dispozitivele care au lucrat pe E+1. Epoca E+1 rămâne arhivată.
* **Purjare** (după retenție, doar Owner, comandă separată cu `plan_hash`): `epoch_registry.state='purged'` + ștergerea rândurilor din `epoch_archive` pentru acel `(cemetery_uid, epoch)`; refuzată înainte de `archived_at + retention`.
* Securitate: `bootstrap_stage` și `epoch_archive` — `REVOKE ALL ... FROM anon, authenticated, public`; fără politici RLS permisive; nu apar în `exposed_schemas` Data API; funcțiile de swap sunt `SECURITY DEFINER` cu `search_path` fixat și execuție acordată doar rolului backend.

## 11. Media la bootstrap/rebuild

* **Stări de registru:** `BOUND` (media completă) și **`BOUND_MEDIA_PENDING`** (entitățile încărcate și registrul utilizabil, fișiere încă în descărcare/verificare; sync-ul de entități funcționează, UI arată progresul). `BOUND_MEDIA_PENDING → BOUND` doar după verificarea `count/bytes/sha256` pe toată media; până atunci `bootstrap_runs.state='running'` (phase `media`), apoi `verifying → completed`.
* **B/D:** upload cu `sha256`; **noua epocă nu devine ACTIVE** până când media completă nu e încărcată și verificată; fișierele lipsă de pe disc → raport + acceptare explicită.
* **C/E:** metadate imediat, fișiere în fundal (`missing → downloading → present`), reutilizare locală pe `sha256` în E.
* **Dedup SHA-256 scoped:** cheia de unicitate `(organization_uid, cemetery_uid, sha256)` — niciodată globală între organizații/clienți (un hash identic la două organizații = două obiecte).
* **Cale de storage:** `org/<organization_uid>/cem/<cemetery_uid>/e<epoch>/<sha256[0:2]>/<sha256>` pentru media live; staging: `org/<organization_uid>/cem/<cemetery_uid>/e<epoch+1>/stage-<bootstrap_id>/...`. La swap, obiectele staging devin cele ale epocii noi; cele ale epocii vechi rămân neatinse (arhivă) până la purjare.

## 12. Bootstrap: stare, lock, idempotență, verificare

* `bootstrap_id` (UUID, creat odată cu planul) și `plan_hash` (SHA-256 pe JSON canonic: tip flux, `organization_uid`, `cemetery_uid`, digest-urile manifestelor sursă/țintă, epocă, `installation_uid`, versiuni app/schema); confirmarea expiră în 10 min și este legată de `plan_hash`.
* Stări persistente (cloud și local): `planned → running → verifying → completed`, plus `failed` și `rolled_back`. `planned` expiră după 10 min (nu blochează cimitirul).
* Un singur bootstrap activ per cimitir: index unic parțial pe `(cemetery_uid)` pentru stările `planned|running|verifying`, `pg_advisory_lock` în cloud, lock-file local; al doilea bootstrap este refuzat.
* Loturi idempotente: cheia `(bootstrap_id, entity, batch_no, batch_sha256)`; resume din `checkpoint` doar dacă `plan_hash` este încă valid.
* Verificare finală: numărători + digest per entitate + manifest media SHA-256 + FK orfane = 0 (+ eșantion 1%); fără ea nu există `completed`.

## 13. Pasul 1 (implementat și închis, doar pe date temporare/locale)

Cloud-ul este un plan de control local (SQLite temporar) cu aceleași reguli ca în design; **nu există** backend PostgreSQL, migrarea 014 sau endpoint-uri HTTP.

### 13.1 Implementat efectiv (cod: `shared/cimitir_secretstore.py`, `cimitir_cloud_control.py`, `cimitir_bootstrap.py`)

| Zonă | Ce face efectiv |
|---|---|
| Identitate | `installation_uid` (în catalog), `organization_uid`/`cemetery_uid` identice local și în cloud, `bootstrap_id`, `plan_hash` (SHA-256 pe JSON canonic) |
| Roluri/scope | Platform Admin / Owner / REBUILD / Admin (§8); cheie/cod din alt cimitir sau altă organizație ≡ „inexistent” (fără scurgeri) |
| Pairing | cod cu hash, expiră 15 min, o singură folosire, max. 5 încercări greșite/instalare; provizionare (UID propus de MAIN) și pairing pe cimitir existent |
| Legături | `device × cemetery`, cheie cu hash în cloud, valabilă doar pe cimitirul ei; revocare |
| SecretStore | DPAPI (Windows, **neverificat aici**); backend de test criptat Fernet cu cheie efemeră; Memory (test); base64 **eliminat** (§9) |
| Stări | `bootstrap_runs` local + cloud (`planned→running→verifying→completed`, `failed`, `rolled_back`), stări registru UNBOUND/PAIRED/PLANNED/BOOTSTRAPPING/BOUND/REBUILD_REQUIRED, expirare plan 10 min, anti-TOCTOU |
| Lock | **Local, între procese:** lock de SO (`flock`; `msvcrt.locking` pe Windows — neverificat aici) pe `registries/<uid>/bootstrap.lock`, eliberat de SO la moartea procesului (nu depinde de PID; PID-ul e doar informativ). Fișierul nu se șterge niciodată. Se ia **înaintea** oricărei verificări de stare; refuzul lock-ului nu modifică starea comună. **Cloud:** index unic parțial „un singur bootstrap activ per cimitir”. (`pg_advisory_lock` apare doar la backend-ul PostgreSQL — neimplementat) |
| Flux A | MAIN gol ↔ cloud gol/DRAFT/inexistent; resume și rollback; idempotent (același plan completat întoarce același raport) |
| Detecție | `incomplete_bootstrap()` raportează un run `running` rămas (cădere), dacă deținătorul e viu și acțiunile permise (`resume`/`rollback`); un plan nou sau alt plan peste el este **refuzat** (`INCOMPLETE_BOOTSTRAP`) |
| Resume | reia din orice fază; după `redeem` nu mai depinde de codul de pairing (poate fi expirat) — folosește cheia din SecretStore (`resume_run`); dacă cheia s-a pierdut între `redeem` și `store.set`, `redeem` rotește cheia aceleiași legături (nu creează alta) |
| Rollback | revocă legătura creată de run (și **fără cheie**, pe baza `bootstrap_id`+`installation_uid`), șterge secretul inclusiv cel orfan (scris, dar fără referință locală), curăță setările, run `rolled_back`; cimitirul rămâne DRAFT. Dacă run-ul era deja `completed` în cloud, rollback = dezlegare (cimitirul rămâne ACTIVE) |
| Local-first/offline | Pasul 1 nu deschide niciun socket (verificat: proces cu rețea blocată la nivel de kernel) |

### 13.2 Decizia ACTIVE + gol (NU este implicită)

| Starea cimitirului în cloud | Starea locală | Decizie | Implementat |
|---|---|---|---|
| inexistent (cod de provizionare) | gol | Flux A permis | da |
| `DRAFT` + gol | gol | **Flux A permis** | da |
| `ACTIVE` + gol | gol | **BLOCAT, fail-closed**: `BlockedFlow("BLOCKED_ACTIVE_EMPTY")`, fără niciun efect lateral (cod neconsumat, fără legătură/run/secret). ACTIVE **nu** este echivalent cu „nou”: poate însemna un cloud golit accidental | da (refuz) |
| `ARCHIVED` | orice | blocat (UID nu se reutilizează) | da |
| `ACTIVE` + gol | **nevid** | clasificat ca B de `decide_flow`; B nu e implementat, regula nu e extinsă aici | doar clasificare |

**Regulă PROPUSĂ pentru ACTIVE + gol — NU implementată, așteaptă aprobarea ta (implică risc: o ștergere în cloud ar fi „blanchiată” ca registru nou):**
1. Condiție *necesară* pentru a considera cimitirul „niciodată populat”: în cloud `data_epoch = 1`, 0 rânduri, 0 tombstone-uri, 0 epoci arhivate, `last_change_seq = 0` pe toate legăturile și **audit** care arată că singurul run `completed` este un Flux A cu 0 rânduri.
2. Chiar dacă (1) e adevărată: doar un **Organization Owner**, cu reautentificare și frază tastată (numele cimitirului + `LEGAT CIMITIR GOL`), jurnalizat în audit; dispozitivul nou primește o legătură nouă, fără să se schimbe epoca.
3. Dacă (1) nu e adevărată (există istoric de date, tombstone-uri, epocă > 1 sau arhivă): rămâne **BLOCAT + alarmă**; singura ieșire este Flux D/E (neimplementate) de către proprietar.
4. Întrebări deschise pentru aprobare: (a) cum se recunoaște un ACTIVE+gol „legitim” (ex. al doilea PC pe un cimitir creat dar încă neîncărcat) fără audit complet în cloud; (b) dacă regula se aplică și local (registru ACTIVE+gol — local nu are status DRAFT/ACTIVE azi); (c) dacă pentru B (MAIN nevid + cloud ACTIVE gol) se aplică aceeași blocare.
Până la aprobare comportamentul este **refuz**; nu se aplică nicio excepție automată.

### 13.3 Verificat vs. neverificat (închidere Pas 1)

| Punct | Stare | Cum |
|---|---|---|
| Lock între procese | verificat (Linux, procese reale) | exact un proces ia lock-ul pe același `cemetery_uid`; al doilea refuzat controlat; cursă simultană (3 runde); 3 cimitire diferite (2 organizații) progresează simultan (fiecare proces așteaptă markerul celuilalt) |
| Crash + resume | verificat (Linux, `SIGKILL`) | 8 faze × (resume | rollback+retry); fără dublare organizație/cimitir/device/legătură; secretul nu se pierde; stare coerentă |
| Offline | verificat (Linux) | proces cu `socket()` non-UNIX refuzat de kernel (seccomp; `unshare -n` nu e permis în acest mediu → nu e namespace de rețea), plus 0 încercări de rețea în proces |
| Secret în SQLite/log/backup | verificat (Linux, backend de test criptat) | scanare octeți (secret, base64, urlsafe) în toate fișierele: registru + WAL, control-plane, loguri worker, backup (`cimitir_backup`), `secrets/` |
| DPAPI real | **NEVERIFICAT** (nu e Windows) | `scripts/windows_verify_step1.py` (de rulat pe Windows) |
| `_pid_alive` Windows | **NEVERIFICAT** | același script (+ `msvcrt.locking`) |

### 13.4 Rămâne neimplementat

Flux B, C, D, E; migrarea 014 și schemele `bootstrap_stage`/`epoch_archive`/`epoch_registry`; backend PostgreSQL pentru control-plane și `pg_advisory_lock`; endpoint-uri `/api/bootstrap/*`; swap atomic (§10); media la bootstrap (§11); `LINK`; manifest cu digest pe conținut (`inspect` folosește `rowid`, nu conținut canonic — suficient doar pentru „gol”); regula ACTIVE+gol aprobată (§13.2); rotirea cheii legăturii în afara resume-ului; protecție la reutilizarea PID (nu mai contează: lock-ul e de SO); verificarea pe Windows (§13.3). Rollback fără cheie este autorizat doar de `bootstrap_id` (UUID aleator) + `installation_uid` — acceptabil într-un control-plane local, **de înlocuit cu autentificare reală la endpoint-urile HTTP**.

## 13A. Pasul 2 — Manifest canonic + Preflight (implementat; simulator local, date temporare)

Pasul 1 rămâne înghețat (nicio modificare de pairing/SecretStore/mașină de stări; în simulatorul cloud s-au adăugat doar tabele aditive pentru conținutul rândurilor, media și conflicte + metode `sim_*`/`export_snapshot`). Nu s-a implementat B/C/D/E, 014, endpoint-uri HTTP, Sync V2; nu s-a atins Supabase/Render/producție/Banovița.

**Cod:** `shared/cimitir_manifest.py` (manifest), `shared/cimitir_preflight.py` (clasificare, matrice, plan, invalidare), `backend/tests/test_etapa4_pas2_manifest.py` (68 teste).

### Manifest canonic (același format local și cloud)
`organization_uid, cemetery_uid, exists, data_epoch, schema_version, app_version, protocol_version, cemetery_status (cloud), sync_state (local), entities{count, digest, uid_digest, uid_coverage, max_version}, total_rows, entities_digest, media{count, bytes, digest, missing, mismatch, duplicates, items[]}, content_digest, integrity{orphans, orphan_total, fk_check_violations}, outbox{total,pending,conflict,error} (local), open_conflicts, tombstones, change_seq, classification, generated_at (metadată), stable_digest`.
* **Digest de rând** = SHA-256 pe JSON canonic al coloanelor normalizate (aceleași reguli ca sincronizarea) + cheile străine ca **hash de conținut al părintelui** (nu id/uid). Deci două baze cu aceleași date au aceleași digest-uri chiar dacă id-urile/uid-urile diferă; FK către un părinte inexistent = `ORPHAN` (raportat).
* **Digest per entitate** = SHA-256 peste lista **sortată** a hash-urilor de rând → ordinea fizică nu contează. `uid_digest` (uid|conținut) doar dacă toate rândurile au uid (citirea NU atribuie uid-uri).
* **Excluse din digestul de conținut** (metadate de sincronizare, nu date reale): `id`, `uid`, `version`/`sync_version`, `updated_at`. `generated_at` este exclus din `stable_digest`.
* `content_digest` = hash(entities_digest, media.digest) — **singura valoare comparată între părți** (LINK); `stable_digest` acoperă tot manifestul (inclusiv stare/epocă/outbox/conflicte) și leagă planul.
* **Media** (doar manifest, fără upload/download): pentru fiecare `documents`/`photos`: nume (basename), mărime și SHA-256 **calculate de pe disc**, cale relativă la `media/` sau la rădăcina registrului ori absolută; stări `ok | missing | hash_mismatch | size_mismatch`; duplicate după hash raportate. Linia de digest: `sha|size|name` (sau `MISSING|sha-înregistrat|name`).
* Clasificare: **NONEMPTY** = există rânduri de entitate sau media; **METADATA_ONLY** = fără date dar cu outbox / conflicte / tombstone-uri; **EMPTY** altfel (inclusiv cimitir inexistent în cloud).

### Preflight (strict read-only)
Conexiuni `immutable=1` (dacă nu există WAL cu conținut) sau `mode=ro` (WAL viu) pentru registru; `mode=ro` pentru simulatorul cloud, cu verificarea rolului `read` în organizație (un cimitir din altă organizație ≡ inexistent). Nu se creează `bootstrap_runs`, binding, `installation_uid` (se primește ca argument), nu se schimbă epoca/outbox/date; testat: conținut, mtime și lista de fișiere sub folderul de test identice înainte/după.

| Local | Cloud | Decizie | Executabil |
|---|---|---|---|
| EMPTY | inexistent / DRAFT gol | `A` | da (prin Pasul 1) |
| orice | **ACTIVE + gol** | `BLOCKED_ACTIVE_EMPTY` | nu |
| NONEMPTY | gol / inexistent / DRAFT | `B` (recomandare) | nu |
| EMPTY | NONEMPTY | `C` (recomandare) | nu |
| NONEMPTY | NONEMPTY, `content_digest` identic | `LINK_CANDIDATE` | nu |
| NONEMPTY | NONEMPTY, digest diferit | `BLOCK_DIFFERENT` (doar D/E explicit, ulterior) | nu |
| orice | ARCHIVED | `BLOCKED_ARCHIVED` | nu |
| BOUND* | cloud gol, local cu date | `BLOCKED_ALARM`; altfel `ALREADY_BOUND` | nu |
| REBUILD_REQUIRED / BOOTSTRAPPING | — | `REBUILD` / `BLOCKED_INCOMPLETE` | nu |
| **METADATA_ONLY** (oricare parte) | — | `REVIEW_METADATA_ONLY` | nu |

Decizie adăugată de implementare (fail-closed, nu era în matricea aprobată): o parte **METADATA_ONLY** (fără date, dar cu outbox/conflicte/tombstone-uri) nu produce niciun flux automat; cere revizuire. Regula ACTIVE+gol a fost aplicată **indiferent de local** (inclusiv local nevid), conform deciziei ta; regula propusă din §13.2 rămâne neaprobată.

### Plan și invalidare
`build_plan` păstrează **ambele manifeste** în plan; `plan_hash` = hash(decizie, org, cimitir, `stable_digest` local, `stable_digest` cloud, installation_uid, versiuni). `validate_plan` regenerează manifestele (citire) și compară; orice diferență (rând, câmp, media, stare locală, outbox, status/epocă/tombstone/conflicte în cloud) → `PlanInvalidated` cu lista câmpurilor schimbate; plan expirat după 10 min → `PlanExpired`. `gate_execution(plan, …, confirm_hash)` = poarta pe care fluxurile o apelează înainte de a scrie: refuză confirmare greșită, plan invalid/expirat și decizii neexecutabile (`FlowNotImplemented`). Flux A (Pasul 1) își păstrează verificarea proprie; nu a fost modificat.

### Neimplementat în Pasul 2
Manifest pe un backend PostgreSQL real și endpoint-ul `GET /api/bootstrap/inspect`; autentificare prin cheia legăturii (acum: principal cu rol `read`); media cloud verificată pe obiecte reale din storage; eșantion 1% câmp-cu-câmp; manifestul `users`/`staff`; execuția B/C/D/E; legarea `gate_execution` în Flux A; cuplarea la backup/lock în timpul citirii (citirea unui registru în uz folosește WAL consistent, fără lock).

## 13B. Pasul 3 — Contract de transfer + staging MAIN → Cloud (implementat; simulator local)

Pasul 1 și Pasul 2 rămân înghețate (singura atingere: `gate_execution` primește parametrul opțional `allowed`, iar `media_summary` ignoră cheile `_*`, ca să nu intre calea fișierului în manifest). **Nu este Flux B**: nu activează cloud-ul, nu face swap, nu crește epoca, nu schimbă bindings/outbox, nu scrie în live. Cod: `shared/cimitir_transfer_contract.py`, `cimitir_cloud_staging.py`, `cimitir_transfer.py`; teste: `backend/tests/test_etapa4_pas3_transfer.py` (23).

### Contract de batch
Câmpuri obligatorii: `bootstrap_id, plan_hash, organization_uid, cemetery_uid, entity_type, batch_no, batch_count, batch_sha256, source_manifest_digest, schema_version, data_epoch` (+ `contract_version`, `row_count`). `batch_sha256` = SHA-256 pe JSON canonic al rândurilor `{uid, version, data}` sortate după uid. Identitatea batch-ului = `(bootstrap_id, entity_type, batch_no)`:
* același batch, același SHA → `duplicate` (nicio scriere); același batch, alt SHA → `ConflictingBatch` (BLOCK); conținut ≠ SHA din contract → `CorruptBatch` (BLOCK); contract incomplet/invalid → `ContractError`;
* `source_manifest_digest` = `content_digest` al manifestului local din plan (Pasul 2); `data_epoch` = epoca cloud din plan; ambele, plus `plan_hash`/`schema_version`/org/cimitir, trebuie să fie egale cu cele ale sesiunii de staging, altfel `SessionMismatch` (BLOCK).

### Staging (cloud simulat, separat de live)
`staging_sessions / staging_batches / staging_rows` — niciun rând live nu este citit sau scris; manifestul live și preflight-ul nu le văd. Fiecare rând poartă `bootstrap_id` + `cemetery_uid`. Acces: rol `rebuild` în organizație + sesiunea (`bootstrap_id` ↔ org ↔ cimitir); alt tenant → `PermissionDenied`, propriul tenant cu `bootstrap_id` străin → `ScopeError` („inexistent”). Un singur staging deschis per cimitir (index unic parțial). Transferul cere cimitir **DRAFT** în cloud și aceeași epocă; dacă statusul/epoca se schimbă pe parcurs, următorul batch este refuzat. `staging_cleanup` șterge doar staging-ul acelui bootstrap (sesiunea rămâne `cleaned`, `bootstrap_id` nu se reutilizează).

### UID mapping
* Regulă: UID-ul existent local, dacă rândul îl are; altfel `uuid5(NS, bootstrap_id|cemetery_uid|entity|ref_local)`. Se generează **o singură dată**, la pornirea transferului, pentru toate rândurile, și se persistă în `registries/<uid>/bootstrap_state/<bootstrap_id>.sqlite` (în afara bazei live și a backup-ului de registru).
* Resume citește mapping-ul și nu regenerează nimic (testat prin modificarea unui UID în mapping). Dacă fișierul se pierde, aceeași derivare dă aceleași UID-uri. Mapping-ul se șterge numai prin `release_local_state(..., bootstrap_completed=True)` sau `abort_transfer`.
* Entitățile locale live NU sunt modificate (hash-ul bazei și schema rămân identice).

### Ordine și FK
`locuri_de_veci → concesionari → contracte → inhumati → plati → amenajari_avize → media`. Un batch nou al unei entități e refuzat (`OrderViolation`) până când toate entitățile anterioare din plan sunt complete; un FK către un părinte care nu e în staging → `OrphanFK`. Reluarea unui batch vechi (duplicat) rămâne permisă oricând, în orice ordine. O sursă cu FK orfan blochează transferul înainte de primul batch.

### Media (doar simulat)
Metadate pe rând (nume, mărime, SHA-256 de pe disc, stare), împărțite în chunk-uri (`chunk_size`) cu SHA-256 fiecare, `dedupe_of` pentru duplicate după hash, fișier lipsă (`present=false`, raportat), `published=false`. Fără upload/download și fără scriere în `media_objects` (live). Fișier modificat între plan și transfer → `SourceDrift`.

### Resume, drift, plan
* `start_transfer` cere `gate_execution(..., allowed=("B",))`: plan valid, neexpirat, `plan_hash` confirmat, decizie B. La resume nu se cere planul neexpirat (poate dura), dar se cer: `plan_hash` identic cu cel persistat, același cimitir/organizație, sursa locală cu același `content_digest` (altfel `SourceDrift`), sesiune cloud cu aceleași valori. Sursa verificată și la sfârșitul rulării (schimbare în timpul transferului → `SourceDrift`).
* La final `verify()` construiește manifestul STAGING-ului și îl compară cu `source_manifest_digest` (conținut + media).

### Neimplementat în Pasul 3 / limite
Flux B end-to-end (activare, swap, epocă, binding, golire outbox); publicarea din staging în live; upload real în Storage; autentificare prin cheia legăturii și endpoint-uri HTTP (acum: principal cu rol `rebuild`); `bootstrap_id` nu este legat de `bootstrap_runs` (Pasul 1); întreruperea a fost simulată în proces (excepție + obiecte noi care citesc starea de pe disc), nu cu `kill -9` (acela a fost testat pentru Flux A în Pasul 1); staging-urile abandonate nu au expirare automată (cleanup manual).

## 13C. Pasul 3.5 — Hardening transfer/staging (implementat; simulator local)

Cod nou/modificat: `shared/cimitir_identity.py` (nou), `cimitir_transfer.py` (canonicalizare, digest de batch-uri, hook-uri, promovare), `cimitir_cloud_staging.py` (metadate STALE, permisiune `bootstrap`), `cimitir_cloud_control.py` (permisiunea `bootstrap`, aditiv). Teste: `backend/tests/test_etapa4_pas35_hardening.py` (19) + worker real `etapa4_worker.py` (acțiuni `transfer`, `resume_transfer`, `promote`). Nu este Flux B: `bootstrap_id` nu e legat de `bootstrap_runs`, nu există activare/swap/epocă/binding final.

### Identitate durabilă local ↔ cloud_uid
* **Soluție aleasă: tabel separat `cloud_entity_identity` în baza registrului** (+ `cloud_identity_promotions` ca martor). Alternativele (coloană `cloud_uid` pe fiecare entitate; fișier lateral) au fost respinse: coloanele ating schema/PK-urile aplicației și toate INSERT-urile existente; un fișier lateral nu intră în backup și nu e tranzacțional cu registrul.
* Coloane: `cemetery_uid, entity_type, local_ref (PK local, AUTOINCREMENT), cloud_uid, status (active|retired), source_bootstrap_id, created_at`. Indecși unici parțiali pe `status='active'`: `(cemetery_uid, entity_type, local_ref)` și `(cemetery_uid, entity_type, cloud_uid)` → aceeași entitate nu poate avea două cloud_uid active, iar un cloud_uid nu poate fi al două entități. Scoped pe `cemetery_uid`; intră în backup-ul registrului.
* **Promovare atomică:** `promote_transfer(binding, bootstrap_id, bootstrap_completed=True)` copiază mapping-ul temporar în identitatea durabilă și scrie martorul într-o **singură tranzacție SQLite** (`BEGIN IMMEDIATE`); orice conflict (`IdentityConflict`) = rollback complet. Idempotentă: dacă martorul există (crash după commit) → `already=True`. Crash în tranzacție → nimic parțial (testat cu `kill -9`).
* `bootstrap_state/<id>.sqlite` se poate șterge doar după promovare (`release_local_state` refuză altfel).
* **Continuitate:** la începutul oricărui transfer, UID-ul unei entități cu identitate activă este reutilizat (prioritate: identitate durabilă > uid local > derivare deterministă) — un nou bootstrap pe aceleași date nu produce un al doilea cloud_uid.
* Media nu se promovează încă (cheia ei locală nu e stabilă); identitatea media se atribuie la publicarea media (pas ulterior). Transferul propriu-zis nu scrie în registrul live; singura scriere live este promovarea, care atinge doar cele două tabele de identitate.

### Batching determinist
Înainte de partiționare, `canonicalize` sortează rândurile după **(hash de conținut, cheie locală)** și media după (tip, nume, sha, entitate…); batch-urile sunt felii consecutive de `batch_size`. La pornire se calculează și se persistă `batch_digests` (SHA-256 pe fiecare batch, inclusiv UID-urile din mapping); la fiecare run/resume partiționarea se recalculează și trebuie să fie identică (`SourceDrift` altfel — acoperă și un mapping alterat). Testat: 5 amestecări fizice aleatorii, două registre cu id-uri și ordine fizică diferite (aceeași compoziție de batch-uri), „restart” cu stare locală ștearsă.

### Kill real (SIGKILL, proces separat)
5 puncte: înainte de primul batch, după primul, la jumătatea unei entități (locuri 2/3), în media (batch 2/4), după ultimul batch înainte de verificare. După restart: staging coerent (număr de batch-uri = cele acceptate, atomic), fără dubluri, mapping identic, resume continuă din următorul batch, `verified=True`, live neatins (toate tabelele live identice, epocă/status neschimbate, fără bindings/identitate). Plus 2 kill-uri în promovare (în tranzacție / după commit).

### Staging abandonat
`staging_sessions`: `created_at, last_activity_at, stale_after (24h), state (open|stale|cleaned)`; `staging_status().meta` expune `eligible_for_cleanup_at`/`eligible_for_cleanup`. `staging_mark_stale` marchează (nu șterge) sesiunile fără activitate; un staging STALE nu primește batch-uri și nu blochează un bootstrap nou; se redeschide explicit (resume cu aceleași valori) dacă nu e altul activ. Cleanup rămâne explicit, strict pe `bootstrap_id + cemetery_uid + organizație`. Nu există ștergere automată.

### Permisiuni
`bootstrap` = {Owner} (provizionare A/B/C: staging, transfer); `rebuild` = {Owner, REBUILD} (rezervat D/E, neschimbat). Admin și REBUILD nu pot folosi staging-ul de bootstrap; Owner le are pe ambele; permisiunile rămân distincte pentru viitoarele endpoint-uri HTTP.

### Neimplementat / limite
Flux B end-to-end; legarea de `bootstrap_runs`; publicare staging → live; identitate media; expirare/ștergere automată a staging-urilor; autentificare prin cheia legăturii; endpoint-uri HTTP; Sync V2 (identitatea durabilă este doar pregătită pentru el).

## 13D. Pasul 4 — Flux B: MAIN existent → cloud nou (implementat; DOAR simulator local)

Modul de intrare: `shared/cimitir_flow_b.py` (`plan_flow_b`, `execute_flow_b` reluabil cu ACELAȘI plan, `abort_flow_b`); module noi: `cimitir_snapshot.py` (cut local atomic),
`cimitir_cloud_media.py` (bytes media în staging, pe bucăți), `cimitir_cloud_publish.py` (publish atomic). Aplicabil numai la MAIN=NONEMPTY + cloud=DRAFT+EMPTY (`decision == B`);
orice altă decizie (ACTIVE+gol, cloud NONEMPTY, local EMPTY/METADATA_ONLY, plan expirat/invalidat, alt tenant, bootstrap concurent, FK invalid, media coruptă) refuză înainte de orice scriere.

**Flux**: preflight → `plan_hash` → `bootstrap_runs` (local + cloud, același `plan_hash`, `kind=B`) → binding (redeem, cheia doar în SecretStore) → **cut local** → staging → upload media → verify → **publish atomic** → finalize local.
Faze raportate în `checkpoint.phase`: `begin, redeemed, snapshot, entities, media, verify, publish, finalize_local, done`. Stări locale: `planned → running → verifying → completed`, `failed/rolled_back`;
cloud: `planned → running → verifying → completed` / `rolled_back`. Resume = reapel cu același plan; faza se deduce din starea durabilă (rând `bootstrap_cuts`, staging, run cloud).

**Cut local** (`take_cut`): sub `BEGIN IMMEDIATE` (MAIN rămâne citibil, scrierile așteaptă): backup consistent al bazei + copia fișierelor media în `bootstrap_snapshots/<id>/` (căile absolute/`..` sunt rescrise în snapshot),
verificare manifest snapshot == manifest plan, FK = 0, arhivarea outbox-ului în `cloud_sync_outbox_archive`, outbox nou (ID-urile nu se reutilizează), rând `bootstrap_cuts` — totul într-o tranzacție. Transferul citește DOAR snapshot-ul;
modificările ulterioare rămân în noul outbox (Sync V2). Rollback: outbox-ul arhivat revine cu ID-urile originale, alături de intrările noi.

**Identitate**: entități — `cloud_entity_identity` (Pasul 3.5), promovată atomic la finalizare; media — `cloud_media_identity` cu cheie locală durabilă `document:<id>` / `photo:<id>` (nu nume, nu poziție), stări `pending → active`
(rezervată înainte de publicare; activată în aceeași tranzacție cu promovarea entităților, doar după publicarea cloud; la rollback se curăță doar `pending`-ul bootstrap-ului). Același SHA = un obiect fizic, mai multe atașamente logice (uid-uri distincte, `dedupe_of`).

**Media**: bytes urcați pe bucăți (SHA/bucată), asamblați și re-verificați strict la verify ȘI în publish (re-citiți din stocare, nu din marcaj). Media lipsă pe disc: Flux B se blochează (`MEDIA_MISSING`) până la `accept_missing_media=True`
(auditat în cloud + raport; starea locală devine `BOUND_MEDIA_PENDING`). Media coruptă (SHA/mărime înregistrate ≠ fișier) refuză planul.

**Publish atomic** (`publish_flow_b`, o tranzacție SQLite): re-verifică ținta (DRAFT, goală, epoca 1), run `verifying` de tip B cu același `plan_hash`, cheia legăturii a run-ului, sesiunea de staging (plan/manifest), completitudinea,
digest staging == digest sursă, orfani FK = 0, numărători pe entitate, media; apoi publică entități + metadate media + bytes (dedup pe SHA) + `media_published`, setează `ACTIVE` + `data_epoch = 1` (prima epocă live; fără epocă 2 artificială),
confirmă binding-ul, marchează run-ul `completed`, curăță staging-ul. Cădere înainte de COMMIT = nimic publicat; după COMMIT = doar finalizare locală idempotentă (rollback refuzat).

**Permisiuni**: se verifică capabilitatea `bootstrap` prin tabelul `PERMISSIONS` (Owner o are implicit; `rebuild` rămâne separat); nicio verificare hardcodată pe rolul Owner în Flux B (delegarea se testează prin tabel).

**Limite**: Windows (DPAPI real, PID, `msvcrt`) NEVERIFICAT; bytes media sunt BLOB-uri în control-plane-ul SQLite (un cloud real folosește object store); fără HTTP/PostgreSQL/Supabase; fără Flux C/D/E; fără Sync V2.

## 13E. Pasul 5 — FastAPI local transport pentru Flux A/B (implementat; MAIN → HTTP localhost → FastAPI → simulator SQLite)

**Componente**: `shared/cimitir_cloud_wire.py` (contract v1: căi, tip de autentificare, coduri de eroare, serializare), `cimitir_cloud_http.py` (server FastAPI — strat de transport, rute FIXE, fără rută generică spre DB),
`cimitir_cloud_client.py` (`RemoteControlPlane`), `cimitir_remote.py` (`@remotable`: aceleași servicii rulează local sau prin HTTP). `execute_flow_a/b` rulează NESCHIMBATE peste HTTP; nu există logică de bootstrap în endpoint-uri.

**Autentificare (doar în headerul `Authorization`; niciodată URL/log/traceback/SQLite plaintext)**: `Pairing <cod>` = autorizare de provizionare ÎNAINTE de binding (emitentul codului = actorul; se cere capabilitatea `bootstrap`
la `preflight/begin/pair`; `rebuild` rămâne separat) și `Binding <cheie>` = secretul binding-ului după `pair`. Un cod consumat rămâne valid DOAR pentru reluarea bootstrap-ului care l-a consumat, cât timp rulează.
Scope-ul (organizație/cimitir/actor) se derivă pe server din autorizare (`binding_issuers`); ce trimite clientul e doar verificat: nepotrivire => 404 `SCOPE_MISMATCH` (fără leakage).

**Binding**: în Flux B se creează `pending` la `pair`, devine `active` NUMAI în tranzacția de publish; rollback/abandon pre-publish => `revoked`; `authenticate` (sync) acceptă doar `active`, operațiile de bootstrap acceptă `pending|active`. Flux A rămâne `active` din redeem (neschimbat).

**HTTP**: 400 `REQUEST_INVALID/CONTRACT_INVALID/BATCH_CORRUPT/MEDIA_CORRUPT`, 401 `UNAUTHENTICATED/INVALID_CODE`, 403 `FORBIDDEN`, 404 `SCOPE_MISMATCH`, 409 `BOOTSTRAP_IN_PROGRESS/CONFLICT/BATCH_CONFLICT/SOURCE_DRIFT/SESSION_MISMATCH/ORDER_VIOLATION/PUBLISH_BLOCKED/MEDIA_MISSING/MEDIA_NOT_VERIFIED`,
422 `ORPHAN_FK`, 500 `INTERNAL` (fără mesaj/traceback). Alegere inițială (Pasul 5): expirarea planului era verificată doar în MAIN; **înlocuită în Pasul 5.1** de validarea pe server (410 `PLAN_EXPIRED`, vezi §13F).
Cădere de conexiune/timeout: clientul retrimite aceeași cerere (serviciile sunt idempotente); dacă nu răspunde => `TransportError` (derivat din `Crash`): fără rollback orb, starea cloud e necunoscută, MAIN reia cu același plan.

**Corecție de securitate găsită de testele adversariale**: `begin_run` nu verifica apartenența cimitirului la organizația codului (un cod al orgului B putea rezerva run-ul pe cimitirul orgului A). Acum => `ScopeError`.
Fault injection (kill/delay după commit, înainte de răspuns) există DOAR în lansatorul de test `backend/tests/etapa4_http_server.py`. Nesupus: Supabase/PostgreSQL/Storage real/Render/Flux C-D-E/014/Sync V2; Windows DPAPI/PID/msvcrt NEVERIFICATE.

## 13F. Pasul 5.1 — Validarea planului pe server (implementat; FastAPI local + simulator SQLite)

**Principiu**: serverul nu crede MAIN și nici un client care îl ocolește. Modul: `shared/cimitir_plan_guard.py` (doar citire), apelat ÎNAINTEA oricărei scrieri: în tranzacția
`begin_run`/`redeem_code`/`complete_run` (sub `BEGIN IMMEDIATE`, deci fără TOCTOU) și în stratul HTTP pentru staging/media/publish. Un refuz nu lasă nicio urmă în baza cloud
(verificat prin amprenta completă a bazei). Contorul de încercări greșite pentru coduri (anti-bruteforce, Pasul 1) rămâne singura scriere intenționată la un refuz de cod.

**`begin_run`** cere (prin HTTP obligatoriu, `kwargs.plan`) preimaginea `plan_hash` (`pg.claim_for(plan)`: `fields`, `created_at`, `expires_at`, plus manifestul local la B):
- `bootstrap_id` = UUID canonic; `kind` ∈ {A, B} și egal cu decizia din plan;
- `sha(fields) == plan_hash` (altfel 400 `PLAN_INVALID`);
- tenant/scope: organizația și cimitirul din plan = cele derivate din cod (altfel 404 `SCOPE_MISMATCH`); instalarea din plan = cea a cererii;
- expirare pe **ceasul serverului**: `now > expires_at` => 410 `PLAN_EXPIRED`; fereastră > `PLAN_TTL` sau `created_at` în viitor (> 60 s) => 400;
- drift țintă: starea cloud recalculată pe server (A: exists/status/rows/tombstones/epocă; B: `stable_digest` al manifestului cloud) ≠ plan => 409 `TARGET_DRIFT`;
- compatibilitate: A cere sursă goală (digesturi de liste vide) + țintă DRAFT/inexistentă goală; B cere sursă NONEMPTY + țintă DRAFT/inexistentă goală => altfel 409 `PLAN_BLOCKED`;
- sursa (B): `stable_digest(local_manifest) == fields.local`; `content_digest`-ul sursei se înregistrează în run (`checkpoint.source_content_digest`).
Reluare (același `bootstrap_id`): același plan/tip/cimitir/instalare; run `planned` => expirare + țintă reverificate; run închis => 409; run în curs => idempotent.

**`redeem_code`** (HTTP: `bootstrap_id` obligatoriu): run existent în scope, aceeași instalare, stare `planned|running`, B ⇔ binding `pending`, A ⇔ `active`; run `planned`
mai vechi decât `PLAN_TTL` (ceasul serverului) => 410. **`complete_run`**: doar Flux A (B se finalizează numai prin publish atomic) și doar cu ținta încă goală (altfel 409).
**Transfer** (staging/media/publish): run B real, al ACESTUI binding, în scope, stare `running|verifying` pentru scrieri; `plan_hash` declarat = al run-ului (400) și
`source_manifest_digest` declarat = cel înregistrat la begin (409 `SOURCE_DRIFT`); publish verifică apoi staging == sursă (Pasul 4) => lanț complet plan → sursă → staging → publish.
Capabilitatea BOOTSTRAP se verifică la autentificare (Pasul 5), înainte de orice validare de plan.

**HTTP**: 400 `PLAN_INVALID`, **410 `PLAN_EXPIRED`** (stabil), 409 `TARGET_DRIFT` / `PLAN_BLOCKED` / `SOURCE_DRIFT`; în MAIN fiecare cod redevine aceeași excepție (`pg.PlanExpired` …).
**Limite**: conținutul MAIN nu e vizibil serverului; sursa e ancorată prin manifestul legat de `plan_hash` și verificată la publish. Apelurile in-process ale simulatorului
(`LocalControlPlane.begin_run` fără `plan`, folosite de testele Pașilor 1–4) rămân permise; fluxurile A/B trimit mereu planul. Teste: `backend/tests/test_etapa4_pas51_server_validation.py`.

## 14. Ce NU face Etapa 4

Sync V2, sincronizarea continuă în timp real a fotografiilor, migrarea production, migrarea Banovița, orice modificare a migrărilor 008–013, orice acțiune pe Supabase production/TEST sau Render fără aprobare explicită separată.
