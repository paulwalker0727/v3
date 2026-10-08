# MIGRATION_REPORT — SQLite → PostgreSQL

- **Mod:** EXECUTE
- **Generat:** 2026-10-05 12:31:47 (durată 1.1 s)
- **Verdict:** **MIGRARE REUȘITĂ și verificată**
- **Politică probleme:** `--on-problem abort`
- **Bază țintă PostgreSQL:** cimitir_test
- **Probleme:** 0 blocante, 1 avertismente, 1 informări

## 1. Sursa SQLite (read-only)

- Fișier: `/tmp/pytest-of-root/pytest-1/popen-gw0/test_execute_twice_is_determin0/legacy.db`  
- Dimensiune: 245760 octeți  
- SHA-256: `1d1559ca626b2210848ed086f059c51d026db9ae7b96a9ef96dd8493e7552941`
- `PRAGMA integrity_check`: ok · `PRAGMA foreign_key_check`: 0 încălcări
- Mod deschidere: `immutable` (nu creează și nu atinge fișiere auxiliare -shm/-wal)
- Fișier WAL nevidat: nu
- **Neschimbat după rulare:** DA (dimensiune, mtime și SHA-256 identice)
- Fișiere media: **neatinse** (doar verificare de existență).

## 2. Inventar tabele

| Tabel | Rânduri SQLite | Se încarcă | Rânduri PostgreSQL | Verificare | Coloane SQLite nemapate |
|---|---:|---:|---:|---|---|
| ai_scan_history | 1 | 1 | 1 | OK | — |
| amenajari_avize | 1 | 1 | 1 | OK | — |
| audit_log | 3 | 3 | 3 | OK | — |
| cereri_concesionare | 1 | 1 | 1 | OK | — |
| citizen_resets | 1 | 1 | 1 | OK | — |
| citizens | 1 | 1 | 1 | OK | — |
| cloud_sync_docs | 1 | 1 | 1 | OK | — |
| cloud_sync_map | 1 | 1 | 1 | OK | — |
| cloud_sync_outbox | 1 | 1 | 1 | OK | — |
| concesionari | 2 | 2 | 2 | OK | — |
| contracte | 2 | 2 | 2 | OK | — |
| documents | 2 | 2 | 2 | OK | — |
| email_outbox | 1 | 1 | 1 | OK | — |
| guides | 1 | 1 | 1 | OK | — |
| inhumati | 1 | 1 | 1 | OK | — |
| loc_extra | 1 | 1 | 1 | OK | — |
| loc_holders | 2 | 2 | 2 | OK | — |
| locuri_de_veci | 3 | 3 | 3 | OK | — |
| photos | 1 | 1 | 1 | OK | — |
| plati | 1 | 1 | 1 | OK | — |
| qr_generations | 1 | 1 | 1 | OK | — |
| rows_ghid | 1 | 1 | 1 | OK | — |
| sectoare | 1 | 1 | 1 | OK | — |
| settings | 3 | 3 | 3 | OK | — |
| sync_ops | 1 | 1 | 1 | OK | — |
| users | 2 | 2 | 2 | OK | — |

Tabele din schema PostgreSQL absente în sursă (rămân goale): sync_tombstones, sync_conflicts, media_objects

## 3. ID-uri (păstrate; secvențele se aliniază după încărcare)

| Tabel | Rânduri | ID min | ID max | Goluri | ID-uri non-întregi |
|---|---:|---:|---:|---:|---:|
| concesionari | 2 | 3 | 7 | 5 | 0 |
| citizens | 1 | 4 | 4 | 3 | 0 |
| locuri_de_veci | 3 | 7 | 25 | 22 | 0 |
| guides | 1 | 4 | 4 | 3 | 0 |
| rows_ghid | 1 | 2 | 2 | 1 | 0 |
| contracte | 2 | 5 | 9 | 7 | 0 |
| inhumati | 1 | 12 | 12 | 11 | 0 |
| photos | 1 | 3 | 3 | 2 | 0 |
| amenajari_avize | 1 | 2 | 2 | 1 | 0 |
| plati | 1 | 8 | 8 | 7 | 0 |
| documents | 2 | 6 | 11 | 9 | 0 |
| cereri_concesionare | 1 | 15 | 15 | 14 | 0 |
| citizen_resets | 1 | 1 | 1 | 0 | 0 |
| users | 2 | 2 | 9 | 7 | 0 |
| audit_log | 3 | 100 | 105 | 102 | 0 |
| email_outbox | 1 | 3 | 3 | 2 | 0 |
| ai_scan_history | 1 | 2 | 2 | 1 | 0 |
| qr_generations | 1 | 5 | 5 | 4 | 0 |
| cloud_sync_outbox | 1 | 2 | 2 | 1 | 0 |

## 4. UID-uri

UID lipsă => derivat determinist `uuid5(cemetery_id:tabel:id)` (cemetery_id din settings: prezent).

| Tabel | UID existente păstrate | UID derivate | Format ne-UUID (păstrate) |
|---|---:|---:|---:|
| concesionari | 0 | 2 | 0 |
| citizens | 0 | 1 | 0 |
| locuri_de_veci | 2 | 1 | 1 |
| sectoare | 0 | 1 | 0 |
| guides | 0 | 1 | 0 |
| rows_ghid | 0 | 1 | 0 |
| contracte | 0 | 2 | 0 |
| inhumati | 0 | 1 | 0 |
| photos | 0 | 1 | 0 |
| amenajari_avize | 0 | 1 | 0 |
| plati | 0 | 1 | 0 |
| documents | 0 | 2 | 0 |
| cereri_concesionare | 0 | 1 | 0 |
| users | 1 | 1 | 0 |
| ai_scan_history | 0 | 1 | 0 |
| qr_generations | 0 | 1 | 0 |

## 5. Chei străine și referințe orfane

| Cheie străină | Rânduri | Fără referință (NULL) | Orfani |
|---|---:|---:|---:|
| contracte.concesionar_id -> concesionari.id | 2 | 0 | 0 |
| contracte.id_loc -> locuri_de_veci.id | 2 | 0 | 0 |
| inhumati.id_loc -> locuri_de_veci.id | 1 | 0 | 0 |
| inhumati.contract_id -> contracte.id | 1 | 0 | 0 |
| photos.cod_loc -> locuri_de_veci.cod_loc | 1 | 0 | 0 |
| amenajari_avize.cod_loc -> locuri_de_veci.cod_loc | 1 | 0 | 0 |
| cereri_concesionare.concesionar_id -> concesionari.id | 1 | 0 | 0 |
| cereri_concesionare.citizen_id -> citizens.id | 1 | 0 | 0 |
| cereri_concesionare.contract_id -> contracte.id | 1 | 0 | 0 |
| citizen_resets.citizen_id -> citizens.id | 1 | 0 | 0 |
| plati.contract_id -> contracte.id | 1 | 0 | 0 |
| qr_generations.loc_id -> locuri_de_veci.id | 1 | 0 | 0 |

## 6. Duplicate

Niciun duplicat găsit.

## 7. Relații lipsă

- Contracte: 2 · **fără concesionar: 0** · fără loc valid: 0
- Înhumări: 1 · **fără loc valid: 0**
- Documente: 2 · **fără entitate: 0** · tip de entitate necunoscut: 0

## 8. Poligoane (JSONB)

| Tabel.coloană | Total | Valide | Goale | Invalide |
|---|---:|---:|---:|---:|
| locuri_de_veci.points | 3 | 3 | 0 | 0 |
| sectoare.points | 1 | 1 | 0 | 0 |
| guides.points | 1 | 1 | 0 | 0 |
| rows_ghid.baseline | 1 | 1 | 0 | 0 |

## 9. Utilizatori și parole

- Utilizatori personal: **2** (roluri: {'admin': 1, 'operator': 1}; dezactivați: 0) · hash-uri: {'pbkdf2': 2}
- Conturi cetățeni: **1** · hash-uri: {'pbkdf2': 1}
- Hash-urile se copiază **octet cu octet**; după încărcare se compară un checksum SHA-256 al perechilor (id, hash). Valorile NU apar aici.
- Checksum parole personal: **OK** · cetățeni: **OK**

## 10. audit_log

- Intrări: **3** · primul: 2024-01-01T10:00:00 · ultimul: 2024-01-02T10:00:00 · fără timestamp: 1
- Se păstrează ID-urile și textul original `ts` (data/ora nemodificate).
- Checksum audit: **OK**

## 11. Conversii de valori

- contracte.tomb_surface_m2: '' -> NULL: 1

Data și ora: coloanele de dată/oră existente rămân **text original, neschimbat**. Coloanele noi `created_ts`/`modified_at` primesc momentul încărcării.

## 12. Fișiere media (doar verificare)

- documents: 2 înregistrări · lipsă pe disc aici: 0
- photos: 1 înregistrări · lipsă pe disc aici: 0

## 13. Corecții aplicate / rânduri sărite (`--on-problem fix`)

Nicio corecție aplicată.

## 14. Verificare după încărcare

- Tranzacție: **COMMIT**
- Secvențe ID aliniate (last_value ≥ MAX(id)): OK
- Chei străine nevalidate: 0

## 15. Constatări

**AVERTISMENTE**

- `audit_null_ts` · audit_log · Intrări de audit fără timestamp. (1)

**INFORMĂRI**

- `secret_in_settings` · settings · settings conține 'web_jwt_secret' — se migrează neschimbat (valoarea NU e în raport); restricționează accesul la baza PostgreSQL. (0)

## 16. Garanții și pașii următori

- Baza SQLite **nu a fost ștearsă sau modificată**; fișierele media nu au fost atinse.
- Reia cu `--dry-run` după orice corecție; `--execute` doar după verdict `GATA` (sau după ce accepți `--on-problem fix`).
