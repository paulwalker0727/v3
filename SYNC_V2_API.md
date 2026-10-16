# Sync v2 — API (`/api/v1/sync-v2`)

**Autentificare (toate rutele):** `Authorization: Binding <cheie-binding>` — cheia dispozitivului (MAIN sau Mobile) obținută la pairing. Organizația și cimitirul se deduc EXCLUSIV din binding (niciodată din payload) și sunt impuse și de RLS. Cheie lipsă/invalidă/revocată → `401`. Răspuns: `{"ok": true, "result": {...}}`; eroare: `{"ok": false, "code": ..., "message": ...}`.
Notă: exemplele folosesc `$K` = cheia (variabilă de mediu, nu o scrieți în fișiere).

| Metodă | Cale | Cine | Scop |
|---|---|---|---|
| GET | `/status` | MAIN, Mobile | rol, numărări pe registre, conflicte deschise, registre editabile, ultima rulare |
| GET | `/pull?entity=&since=&limit=` | MAIN, Mobile | modificări incrementale (`change_seq`) + ștergeri; `more` + `cursor` pentru paginare |
| GET | `/row?entity=&uid=` | MAIN, Mobile | un rând (versiune curentă) — baza unei editări |
| POST | `/push` | MAIN, Mobile | operațiuni idempotente (max 200/cerere, 512 KB) |
| GET | `/conflicts?all=0\|1` | MAIN (toate), Mobile (doar ale sale) | conflicte deschise (`all=1` include `main_wins`) |
| POST | `/conflicts/{id}/resolve` | doar MAIN | `keep_main` · `accept_incoming` · `restore_previous` |
| GET | `/devices` | doar MAIN | telefoane asociate cimitirului + registre editabile |
| PUT | `/devices/{ref}/permissions` | doar MAIN | `{"entities": ["inhumati", ...]}` — drepturi de editare pe telefon |
| GET | `/runs?limit=` | doar MAIN | jurnal de rulări (push/pull) |

`entity` ∈ `locuri_de_veci, concesionari, contracte, inhumati, plati, amenajari_avize`. Relațiile circulă prin uid-ul părintelui: `contracte.loc_uid / concesionar_uid`, `inhumati.loc_uid / contract_uid`, `plati.contract_uid`.

## Exemple
```bash
curl -s -H "Authorization: Binding $K" "$URL/api/v1/sync-v2/status"
curl -s -H "Authorization: Binding $K" "$URL/api/v1/sync-v2/pull?entity=inhumati&since=0&limit=500"
curl -s -H "Authorization: Binding $K" "$URL/api/v1/sync-v2/row?entity=inhumati&uid=<uid>"

curl -s -X POST -H "Authorization: Binding $K" -H "Content-Type: application/json" "$URL/api/v1/sync-v2/push" -d '{
  "ops": [{"op_id": "m-3f2a9c1e7b", "entity": "inhumati", "uid": "<uid>", "action": "upsert",
           "base_version": 2, "data": {"nume": "Popescu", "prenume": "Ion", "observatii": "verificat", "loc_uid": "<loc-uid>"}}]}'
# rezultat per op: {"status": "applied|conflict|rejected|retry", "version": 3, "reason": "...", "duplicate": true?}

curl -s -X PUT -H "Authorization: Binding $KMAIN" -H "Content-Type: application/json" \
  "$URL/api/v1/sync-v2/devices/<ref>/permissions" -d '{"entities": ["inhumati", "concesionari"]}'
curl -s -X POST -H "Authorization: Binding $KMAIN" -H "Content-Type: application/json" \
  "$URL/api/v1/sync-v2/conflicts/12/resolve" -d '{"resolution": "keep_main"}'
```

## Semantica `push`
- **Idempotent:** același `op_id` → același răspuns (`duplicate: true`), fără efect dublu. Același `uid` cu date identice → `applied` + `adopted` (fără duplicat). Un refuz `not_authorized` nu se memorează (după ce MAIN acordă dreptul, același `op_id` poate fi retrimis).
- `status=retry` (părinte lipsă `missing_parent:*`, eroare temporară de server) → clientul păstrează operațiunea și reîncearcă; nu se memorează.
- **Cheie naturală:** alt `uid` cu același `cod_loc` în cimitir → `conflict` (`natural_key`), niciodată duplicat.
- **Conflict (MAIN câștigă):** MAIN cu `base_version` depășit → se aplică, starea suprascrisă rămâne în jurnal (`main_wins`, recuperabilă cu `restore_previous`). Mobile cu bază depășită → `conflict` (`version_mismatch`), nu se aplică.
- **Mobile:** scrie doar registrele bifate din MAIN; `delete` interzis (`delete_not_allowed`). Un înhumat adăugat de pe telefon marchează locul `are_inhumari`.
- Fiecare operațiune → `sync_v2_ops`; fiecare cerere push/pull → `sync_v2_runs`; conflictele → `sync_v2_conflicts`.

## Adăugiri (sesiunea „Mobile V2 — sincronizare completă")

| Rută | Metodă | Cine | Ce face |
|---|---|---|---|
| `/roles` | GET | orice dispozitiv autentificat | capabilitățile și șabloanele de rol (administrator, preot_paroh, operator, limitat) |
| `/devices/{ref}/capabilities` | PUT | doar MAIN | `{"role": "operator"}` sau `{"capabilities": ["view_map", ...]}` (rol „personalizat"). Se aplică **pe server** la `pull`/`row`/`history`/`push` și în `/mobile/*` (căutare, hartă, fișă, numărători) |
| `/history?entity=locuri_de_veci&uid=…` | GET | dispozitiv cu drept de citire | evenimente (cine/când/ce tip) pentru rând și, la loc, pentru înhumații/contractele/plățile lui; fără date personale în clar |
| `/sectors` | PUT | doar MAIN | `{"sectors":[{"id":"A","points":[[x,y],…],"color":"#…"}]}` — upsert pe (cimitir, id); nu șterge nimic |
| `status` | GET | — | acum include `role`, `capabilities` și doar registrele `entities` citibile |

**Capabilități**: `view_map, read_registers, read_finance, edit_graves, register_burials, manage_contracts, register_payments, upload_documents, request_permits, approve_requests, manage_users, export_print`.
**Implicit** (telefon asociat, fără rol): șablonul **limitat** = hartă + evidențe, **fără plăți**, fără scriere (hotărât după auditul de securitate SEC-001; înainte citea tot). `upload_documents`, `approve_requests`, `manage_users`, `export_print` sunt păstrate în model dar nu au încă funcții asociate pe telefon (vezi `STATUS_IMPLEMENTARE.md`).
**Citire/scriere pe registre**: `plati` ← `read_finance` / `register_payments`; `contracte`+`concesionari` ← `read_registers` / `manage_contracts`; `inhumati` ← `register_burials`; `locuri_de_veci` ← `view_map|read_registers` / `edit_graves`; `amenajari_avize` ← `read_registers` / `request_permits`.
**DELETE în registre**: permis de RLS doar dacă dispozitivul este MAIN (`cp.device_type`, setat de server din binding).
