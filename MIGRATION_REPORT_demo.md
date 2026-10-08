# MIGRATION_REPORT — SQLite → PostgreSQL

- **Mod:** DRY-RUN (nu s-a scris nimic în PostgreSQL)
- **Generat:** 2026-10-03 20:19:59 (durată 0.1 s)
- **Verdict:** **GATA (cu avertismente)**
- **Politică probleme:** `--on-problem abort`
- **Bază țintă PostgreSQL:** cimitir_dev
- **Probleme:** 0 blocante, 3 avertismente, 1 informări

## 1. Sursa SQLite (read-only)

- Fișier: `backend/data/cimitir_demo.db`  
- Dimensiune: 167936 octeți  
- SHA-256: `183f624f98f370e775d417c057e7568721ef1b32841abf0805f039e4b2a3973b`
- `PRAGMA integrity_check`: ok · `PRAGMA foreign_key_check`: 0 încălcări
- Mod deschidere: `immutable` (nu creează și nu atinge fișiere auxiliare -shm/-wal)
- Fișier WAL nevidat: nu
- **Neschimbat după rulare:** DA (dimensiune, mtime și SHA-256 identice)
- Fișiere media: **neatinse** (doar verificare de existență).

## 2. Inventar tabele

| Tabel | Rânduri SQLite | Se încarcă | Rânduri PostgreSQL | Verificare | Coloane SQLite nemapate |
|---|---:|---:|---:|---|---|
| ai_scan_history | 30 | 30 | — | — | — |
| amenajari_avize | 0 | 0 | — | — | — |
| cereri_concesionare | 0 | 0 | — | — | — |
| concesionari | 1 | 1 | — | — | — |
| contracte | 1 | 1 | — | — | — |
| documents | 8 | 8 | — | — | — |
| guides | 0 | 0 | — | — | — |
| inhumati | 1 | 1 | — | — | — |
| loc_extra | 0 | 0 | — | — | — |
| locuri_de_veci | 34 | 34 | — | — | — |
| photos | 0 | 0 | — | — | — |
| plati | 0 | 0 | — | — | — |
| rows_ghid | 0 | 0 | — | — | — |
| sectoare | 3 | 3 | — | — | — |
| settings | 6 | 6 | — | — | — |
| users | 2 | 2 | — | — | — |

Tabele din schema PostgreSQL absente în sursă (rămân goale): citizens, loc_holders, citizen_resets, audit_log, sync_ops, email_outbox, qr_generations, cloud_sync_map, cloud_sync_outbox, cloud_sync_docs

## 3. ID-uri (păstrate; secvențele se aliniază după încărcare)

| Tabel | Rânduri | ID min | ID max | Goluri | ID-uri non-întregi |
|---|---:|---:|---:|---:|---:|
| concesionari | 1 | 1 | 1 | 0 | 0 |
| locuri_de_veci | 34 | 37 | 70 | 36 | 0 |
| guides | 0 | None | None | 0 | 0 |
| rows_ghid | 0 | None | None | 0 | 0 |
| contracte | 1 | 1 | 1 | 0 | 0 |
| inhumati | 1 | 1 | 1 | 0 | 0 |
| photos | 0 | None | None | 0 | 0 |
| amenajari_avize | 0 | None | None | 0 | 0 |
| plati | 0 | None | None | 0 | 0 |
| documents | 8 | 1 | 8 | 0 | 0 |
| cereri_concesionare | 0 | None | None | 0 | 0 |
| users | 2 | 1 | 2 | 0 | 0 |
| ai_scan_history | 30 | 2 | 31 | 1 | 0 |

## 4. UID-uri

UID lipsă => derivat determinist `uuid5(cemetery_id:tabel:id)` (cemetery_id din settings: ABSENT, se folosește „cimitir”).

| Tabel | UID existente păstrate | UID derivate | Format ne-UUID (păstrate) |
|---|---:|---:|---:|
| concesionari | 0 | 1 | 0 |
| locuri_de_veci | 0 | 34 | 0 |
| sectoare | 0 | 3 | 0 |
| guides | 0 | 0 | 0 |
| rows_ghid | 0 | 0 | 0 |
| contracte | 0 | 1 | 0 |
| inhumati | 0 | 1 | 0 |
| photos | 0 | 0 | 0 |
| amenajari_avize | 0 | 0 | 0 |
| plati | 0 | 0 | 0 |
| documents | 0 | 8 | 0 |
| cereri_concesionare | 0 | 0 | 0 |
| users | 0 | 2 | 0 |
| ai_scan_history | 0 | 30 | 0 |

## 5. Chei străine și referințe orfane

| Cheie străină | Rânduri | Fără referință (NULL) | Orfani |
|---|---:|---:|---:|
| contracte.concesionar_id -> concesionari.id | 1 | 0 | 0 |
| contracte.id_loc -> locuri_de_veci.id | 1 | 1 | 0 |
| inhumati.id_loc -> locuri_de_veci.id | 1 | 1 | 0 |
| inhumati.contract_id -> contracte.id | 1 | 1 | 0 |
| photos.cod_loc -> locuri_de_veci.cod_loc | 0 | 0 | 0 |
| amenajari_avize.cod_loc -> locuri_de_veci.cod_loc | 0 | 0 | 0 |
| cereri_concesionare.concesionar_id -> concesionari.id | 0 | 0 | 0 |
| plati.contract_id -> contracte.id | 0 | 0 | 0 |

## 6. Duplicate

Niciun duplicat găsit.

## 7. Relații lipsă

- Contracte: 1 · **fără concesionar: 0** · fără loc valid: 1
- Înhumări: 1 · **fără loc valid: 1**
- Documente: 8 · **fără entitate: 0** · tip de entitate necunoscut: 0

## 8. Poligoane (JSONB)

| Tabel.coloană | Total | Valide | Goale | Invalide |
|---|---:|---:|---:|---:|
| locuri_de_veci.points | 34 | 34 | 0 | 0 |
| sectoare.points | 3 | 3 | 0 | 0 |

## 9. Utilizatori și parole

- Utilizatori personal: **2** (roluri: {'admin': 1, 'user': 1}; dezactivați: 0) · hash-uri: {'pbkdf2': 2}
- Conturi cetățeni: **0** · hash-uri: {}
- Hash-urile se copiază **octet cu octet**; după încărcare se compară un checksum SHA-256 al perechilor (id, hash). Valorile NU apar aici.

## 10. audit_log

- Intrări: **0** · primul: None · ultimul: None · fără timestamp: 0
- Se păstrează ID-urile și textul original `ts` (data/ora nemodificate).

## 11. Conversii de valori

Nicio conversie necesară.

Data și ora: coloanele de dată/oră existente rămân **text original, neschimbat**. Coloanele noi `created_ts`/`modified_at` primesc momentul încărcării.

## 12. Fișiere media (doar verificare)

- documents: 8 înregistrări · lipsă pe disc aici: 8
- photos: 0 înregistrări · lipsă pe disc aici: 0

## 13. Corecții aplicate / rânduri sărite (`--on-problem fix`)

Nicio corecție aplicată.

## 15. Constatări

**AVERTISMENTE**

- `contract_no_loc` · contracte · Contracte fără loc de veci valid (id_loc gol sau orfan). (1) ID: 1
- `burial_no_loc` · inhumati · Înhumări fără loc valid (id_loc gol sau orfan). (1) ID: 1
- `media_missing` · documents · Fișiere referite de documents.file_path care nu există pe disc aici (calea se copiază neschimbată). (8) ID: 1, 2, 3, 4, 5, 6, 7, 8

**INFORMĂRI**

- `secret_in_settings` · settings · settings conține 'web_jwt_secret' — se migrează neschimbat (valoarea NU e în raport); restricționează accesul la baza PostgreSQL. (0)

## 16. Garanții și pașii următori

- Baza SQLite **nu a fost ștearsă sau modificată**; fișierele media nu au fost atinse.
- Reia cu `--dry-run` după orice corecție; `--execute` doar după verdict `GATA` (sau după ce accepți `--on-problem fix`).
