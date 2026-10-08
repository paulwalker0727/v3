# Etapa 7 — Documentele online (storage S3-compatibil / Supabase, metadate în PostgreSQL)

```
Desktop PySide6 ──HTTPS──> FastAPI ──> storage (local | S3-compatibil | Supabase)   octeții (PDF/JPG)
 (fișiere locale +                └──> PostgreSQL `media_objects`                    DOAR metadatele
  cache + outbox)
```
Desktopul nu primește niciodată chei de storage; doar FastAPI le are. Etapa 8 NU a fost pornită.

## Ce s-a adăugat
| Fișier | Rol |
|---|---|
| `shared/cimitir_storage.py` | interfața `Storage` + drivere `local`, `s3` (boto3), `supabase` (REST); chei de obiect; sha256 |
| `backend/cimitir/pg_migrations/004_media_objects.sql` | tabela `media_objects` + `media_id` pe `documents`/`photos` |
| `backend/cimitir/media_service.py` | înregistrare în storage + metadate, cache cu verificare sha256, ștergere |
| `backend/media_api.py` | `/api/sync/media/*`: încărcare în bucăți, descărcare, listă incrementală, ștergere, stare |
| `desktop/media_sync.py` | triggere SQLite → outbox, încărcare, descărcare/cache, prefetch, ștergeri |
| `backend/tests/test_etapa7_media.py` | 11 teste (drivere + end-to-end cu 2 MAIN) |

## PostgreSQL păstrează numai metadatele
`media_objects`: `id`, `entity_uid`, `filename`, `storage_path`, `mime`, `size`, `sha256`, `uploaded_by`, `uploaded_at`
(+ `change_seq`, doar infrastructură de sincronizare incrementală, ca la Etapa 6). Nu există nicio coloană `bytea` în baza de date
(verificat în test). Rândurile `documents`/`photos` primesc doar `media_id` ca legătură.
`storage_path` = `documents/<entity_type>/<doc_type>/<entity_uid>/<media_id>/<fișier>` sau `photos/loc/<loc_uid>/<media_id>/<fișier>`
(tipul fișierului se deduce din cale, nu din coloane în plus).

## Fluxuri
* **Fișier nou (MAIN)** — se salvează ca până acum în `DATA_DIR/media` (merge și offline). Un trigger îi dă `media_id` și îl pune în
  `cloud_sync_outbox` (`kind='media'`). Online: încărcare în bucăți de 1 MiB (`PUT …/chunks/n`), apoi `POST …/complete`: serverul
  asamblează, **verifică dimensiunea și sha256**, pune obiectul în storage și abia apoi scrie rândul în PostgreSQL. Nepotrivire ⇒ 422, nu se
  scrie nimic, clientul reîncearcă (maxim 5 încercări, apoi stare „eroare” vizibilă în coadă). Repetare cu același sha256 ⇒ `duplicate: true`;
  alt conținut pe același `media_id` ⇒ 409.
* **Fișierele locale NU se șterg după încărcare** (nici originalul, nici copia gestionată). Ștergerea unui document de către operator păstrează
  comportamentul vechi pentru fișierul local gestionat și trimite `DELETE` pentru obiectul din storage.
* **Alt calculator** — metadatele se descarcă incremental (cursor `change_seq`) și creează rânduri locale; octeții se aduc prin API la
  prima citire (`list_documents` / `get_document_path` / `list_photos_for_loc`) sau în fundal (prefetch pentru fișiere ≤ 10 MiB, max 20/ciclu),
  se verifică sha256 și rămân în `DATA_DIR/media_cache` ⇒ se deschid apoi și offline.
* **Server** — documentele încărcate în portal/admin (inclusiv cererile cetățenilor) merg tot în storage; dacă lipsește copia locală a serverului
  (pod nou), se readuce din storage. Când MAIN confirmă o cerere (`/requests/ack`), serverul șterge și obiectele din storage (date personale).
* **Offline** — adăugarea, vizualizarea și coada funcționează fără internet; la reconectare coada se golește automat (aceleași cicluri ca Etapele 2–6, ~30 s).

## Configurare (doar pe server, în `backend/.env` sau mediul procesului)
```
CIMITIR_STORAGE_DRIVER=local            # implicit; local | s3 | supabase
# S3-compatibil (AWS S3, Cloudflare R2, Backblaze B2, MinIO, Supabase S3):
CIMITIR_STORAGE_DRIVER=s3
S3_BUCKET=...  S3_REGION=...  S3_ACCESS_KEY_ID=...  S3_SECRET_ACCESS_KEY=...   [S3_ENDPOINT_URL=...] [S3_PREFIX=prod] [S3_FORCE_PATH_STYLE=1]
# Supabase Storage (bucket PRIVAT):
CIMITIR_STORAGE_DRIVER=supabase
SUPABASE_URL=https://<proiect>.supabase.co  SUPABASE_SERVICE_ROLE_KEY=...  SUPABASE_BUCKET=...   [SUPABASE_PREFIX=prod]
CIMITIR_MEDIA_MAX_BYTES=41943040        # limită per fișier (40 MiB)
```
`GET /api/sync/ping` și `GET /api/sync/media/state` arată driverul activ (fără secrete). **În acest pod este activ driverul `local`**
(`DATA_DIR/storage_objects`), pentru că nu există încă credențiale.

## Migrarea fișierelor vechi
Nu s-a implementat (nu există fișiere de migrat). Arhitectura o permite: `media_service.register_file(...)` (server) și
`media_sync.enqueue_existing()` (MAIN) sunt blocurile pe care un script separat și verificabil se poate sprijini; nu sunt apelate nicăieri.

## Verificat vs. neverificat
* Verificat automat: driverul local; driverul S3 contra unui **S3 simulat (moto)**; driverul Supabase contra unui **server HTTP simulat**; fluxul complet
  (chunked, sha256, idempotență, offline→reconectare, al doilea calculator + cache + offline din cache, ștergere, fișiere ale serverului) pe driverul local.
* **Neverificat:** S3 / Supabase reale (nu am credențiale), comportamentul la latență/limite reale ale furnizorului, politicile bucket-ului (trebuie PRIVAT),
  UI-ul PySide6 pe Windows pentru documente (nu s-au schimbat ferestre; doar datele din spate).
* Limite: `caption`/`doc_type` custom nu circulă (doar `filename` și tipul din cale) — pe alt calculator legenda = numele fișierului;
  documentele de tip `aviz` au `entity_uid = aviz:<uid loc>:<nr_aviz>` (avizele nu au uid propriu); documentele MAIN nu apar în panoul web `/admin`
  (care listează tabela `documents` a serverului); fotografiile/documentele cererilor rămân gestionate de serverul de cereri (nu sunt retrimise de MAIN).
