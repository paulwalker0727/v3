# Etapa 2 — Infrastructura comună (personal, sincronizare, hartă publică)

Construit peste Etapa 1 (`AUDIT_ETAPA1.md`). SQLite rămâne local; MAIN funcționează fără internet.

## 1. Roluri, drepturi, cimitir

| Rol | Drepturi implicite | Observații |
|---|---|---|
| `admin` | `view, edit, approve, manage_users, manage_config` | fix, toate |
| `operator` | `view, edit, approve` | se poate restrânge per cont (ex. doar `edit`) |
| `user` | `view` | consultare |

- Sursa unică: `shared/cimitir_auth.py` (desktop `auth.py` și backend `cimitir/auth.py` sunt alias).
- Drepturile se **reîncarcă din DB la fiecare cerere web** și la fiecare operațiune sensibilă din MAIN
  (`desktop/access_session.py`): dezactivarea/revocarea se aplică imediat în sesiunea deschisă.
- Backend: `edit` pentru modificări, `approve` pentru decizii asupra cererilor, `manage_users` pentru conturi,
  `manage_config` pentru setări/cheie de sincronizare. MAIN: fereastră de editare doar cu `edit`, aprobare/respingere
  cereri cu `approve`, configurare sincronizare cu `manage_config`.
- Cimitir: un cimitir = o bază/sursă. Contul are `cemetery_id`; autentificarea refuză un cont atribuit altui cimitir.
  Cheia de sincronizare este per cimitir (`<id>.<secret>`, în DB doar hash SHA-256).
- Panou web: `/admin/utilizatori` (rol, drepturi edit/approve, activ/inactiv, ultimul acces; nu poți elimina ultimul
  administrator) și `/admin/setari` (nume, depunere cereri, ore acces offline, cheie de sincronizare).

## 2. Politica de acces fără internet

- Conturile primite de pe server (`origin=synced`) au `lease_until`. La fiecare sincronizare reușită lease-ul se
  reînnoiește cu `offline_lease_hours` (implicit **72 h**, configurabil 1–720 din `/admin/setari`).
- Fără reconectare, după expirare login-ul MAIN este refuzat (`lease_expired`) și operațiunile sensibile din sesiunea
  deschisă sunt blocate. Conturile locale (`origin=local`, ex. administratorul local) nu expiră (acces de urgență).
- Revocare (cont dezactivat/șters pe server): efect imediat online; în MAIN se aplică **la următoarea sincronizare**
  (până atunci contul poate lucra cel mult până la expirarea lease-ului). Explicit: revocarea nu ajunge offline.
- Parola și rolul se sincronizează ca hash PBKDF2 + versiune; o versiune mai veche nu suprascrie una mai nouă.

## 3. Sincronizare (API HTTPS, nu fișier .db)

Server online: `CIMITIR_MODE=online`. Emite cheia: `python scripts/issue_sync_key.py --source cimitir` sau butonul din
`/admin/setari`. În MAIN: ☁ Sincronizare cloud → adresa `https://…` + cheia (HTTP acceptat doar pentru localhost).

| Endpoint (antet `X-Sync-Key`) | Rol |
|---|---|
| `GET /api/sync/ping` | verificare cheie, `cemetery_id`, versiune hartă, ore lease |
| `POST /api/sync/map` | proiecția publică a hărții; `map_version` monoton + `op_id` + hash; `409 stale` dacă e mai veche, `409 conflict` dacă aceeași versiune cu alt conținut, `duplicate` la reluare |
| `POST /api/sync/map/image` | fundal (png/jpg/webp ≤25 MB, SHA-256, versiune) |
| `GET /api/sync/staff` | personalul cimitirului + `lease_hours` |
| `GET /api/sync/requests` | cereri neconfirmate (cu datele necesare MAIN) + lista documentelor |
| `GET /api/sync/requests/{cod}/documents/{id}` | descărcare document |
| `POST /api/sync/requests/ack` | MAIN a salvat: serverul șterge datele personale și documentele (rămân cod + stare) |
| `POST /api/sync/requests/{cod}/status` | decizia MAIN (`validat`/`respins`), `version` + `op_id`, idempotent, `409` la versiune veche |

MAIN (`desktop/cloud_sync.py`): coadă `cloud_sync_outbox` persistentă (op_id, tip, payload, încercări, ultima eroare);
harta/fundalul/deciziile se pun în coadă și **fără internet**, se reiau la următoarea rulare (inclusiv după repornire);
harta se retrimite doar dacă hash-ul proiecției s-a schimbat. Fundal: `cloud_sync_auto.py` (la 5 min + la pornire);
starea (ultima reușită, ultima eroare, operațiuni restante) apare în bara MAIN și în fereastra de sincronizare.
Prima sincronizare leagă MAIN de cimitirul serverului; cheia altui cimitir este refuzată.
Dacă serverul are o versiune mai nouă, MAIN raportează eroarea și **nu** publică; `cloud_sync.force_republish()`
adoptă versiunea serverului după decizia explicită a operatorului.

## 4. Harta publică

`GET /api/public/map`: `coordinate_system` (`local_px`, origine colț stânga-sus al imaginii, `gps:false`), `scale`
(`px_per_meter`, `calibrated`), `background` (URL, lățime, înălțime), `sectors`, `plots` (cod, poligon, sector,
disponibilitate, `suprafata_m2_declarata`, `suprafata_m2_calculata` și `laturi_m` **doar cu scară validă**).
`px_per_meter` valid = număr finit în [0,01; 10 000]; altfel dimensiunile nu se publică. Scara MAIN este una
uniformă (două puncte), valabilă doar pe planul calibrat — nu este georeferențiere.

Disponibilitate pentru concesionare = câmpul explicit `disponibilitate` din MAIN: `Liber/Disponibil` → `disponibil`,
`Rezervat` → `rezervat`, orice altceva/gol → `indisponibil`. Înhumările (`are_inhumari`) și expirarea contractului
**nu** o schimbă. Cererea pentru un loc indisponibil este refuzată (409).

Câmpuri publice (listă explicită, `shared/cimitir_public.py`): plot — `uid, cod_loc, points, color, sector, parcela,
rand, numar, tip, categorie, capacitate, suprafata_declarata, disponibilitate, are_inhumari`; sector — `id, points,
color`; înhumat (doar dacă operatorul bifează publicarea) — `cod_loc, nume, prenume, an_nastere, an_deces`. Orice alt
câmp trimis este ignorat de server. Concesionari, contracte, acte, CNP/adresă/telefon nu există pe proiecție.

## 5. Configurare rapidă

```powershell
# Server online (separat de baza cu date personale din MAIN)
$env:CIMITIR_MODE="online"; $env:CIMITIR_DATA_DIR="D:\cimitir-server"; $env:CIMITIR_HOST="0.0.0.0"
python scripts/issue_sync_key.py --source cimitir      # copiază cheia afișată
# în spatele unui proxy HTTPS (Caddy/nginx): python -m uvicorn server:app --app-dir backend --port 8000
# MAIN: ☁ Sincronizare cloud → https://server.exemplu.ro + cheia → Sincronizează acum
```

Nota: TLS se termină la proxy; aplicația refuză adrese `http://` non-locale în clientul de sincronizare.

## 6. Verificări

**Automat (Linux, date fictive, server uvicorn real + MAIN în procese separate):** `backend/tests/test_etapa2_sync.py`
(16 teste): harta MAIN→online (v1→v2→v3, dedup fără schimbare, ștergere), scară necalibrată, fundal+sectoare,
disponibilitate vs înhumări/contract expirat, offline→repornire→reluare fără duplicate, versiuni vechi/conflict
respinse, MAIN cu bază restaurată nu publică, doar câmpuri explicite, flux cereri (pull, ack + ștergere date
personale, decizie, reluare, documente), personal (lease, revocare după sincronizare, expirare, reînnoire),
drepturi desktop re-verificate live, drepturi web (operator fără `manage_*`, revocare imediată, ultimul admin),
izolare între chei/cimitire, refuz HTTP non-local. Desktop: `test_cereri_online_window.py` (+ operator fără
`approve`). Existente: backend 61 passed; desktop 196 passed, 9 eșecuri preexistente (vezi Etapa 1).

**Nu s-a verificat (manual):** TLS real/proxy și certificate; MAIN PySide6 pe Windows cu server real (fereastra
de sincronizare, bara de stare, dialogul de refuz al drepturilor — testat doar programatic/offscreen); creștere
(sute de locuri, rețea lentă); comportamentul reluării la întreruperea *în mijlocul* unui upload mare; ceasul
sistemului MAIN (lease-ul se bazează pe ora locală: un ceas dat înapoi prelungește accesul); editorul de hartă
MAIN nu a fost modificat (calibrarea `px_per_meter` rămâne cea existentă).

**Limite cunoscute:** `mongo_store` nu implementează drepturile/sincronizarea (opt-in, neutilizat); parolele seed
`admin/admin123`, `user/user123` încă se creează; fără limitare de încercări la login și la sync (Etapa 3);
revocarea nu se poate aplica în MAIN fără conectare — limitată doar de durata lease-ului.
