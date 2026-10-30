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
- **Mobile:** scrie doar ce permite matricea din MAIN (vezi secțiunea „Permisiuni Mobile"); `delete` doar cu „Șterge/Anulează" (altfel `delete_not_allowed`). Un înhumat adăugat de pe telefon marchează locul `are_inhumari`.
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

## Adăugiri (ramura `feature/mobile-complet`) — Permisiuni Mobile pe categorii, emitere documente, fundal hartă

Migrarea **`027_mobile_permissions_matrix.sql`** (aditivă, idempotentă; rollback: `pg_rollbacks/027_…_ROLLBACK.sql`). Fără 027, telefoanele rămân pe capabilitățile/registrele din 022 (comportament neschimbat).

### Matricea de permisiuni (stocată în `sync_v2_permissions`, același mecanism de pairing/binding/roluri; nu e un sistem paralel)
Categorii: `locuri`, `harta`, `concesionari`, `inhumati`, `contracte`, `plati`, `avize`, `fisiere`. Acțiuni: `view` (Vede), `add` (Adaugă), `edit` (Editează), `delete` (Șterge/Anulează).
`add/edit/delete` implică `view`. `fields` (opțional, per categorie) = lista câmpurilor pe care telefonul le poate **modifica** (`null` = toate). `harta` controlează `points`+`color` ale locului; `locuri` restul câmpurilor.

| Rută | Metodă | Cine | Ce face |
|---|---|---|---|
| `/permissions/profiles` | GET | doar MAIN | `{categories, actions, fields:{categorie:[câmpuri]}, profiles:{cheie:{label, matrix, custom}}}` — profiluri predefinite: `administrator`, `secretar`, `personal_cimitir`, `doar_consultare` (+ cele personalizate) |
| `/permissions/profiles/{cheie}` | PUT | doar MAIN | `{"label": "...", "matrix": {...}}` — personalizează/creează un profil (per cimitir); se aplică la următoarea atribuire |
| `/devices/{ref}/matrix` | PUT | doar MAIN | `{"profile": "secretar"}` și/sau `{"matrix": {...}, "view_pii": false}` — efect imediat pe server, inclusiv pentru operațiile offline în așteptare; auditat (`permissions_changed`, cu înainte/după) |
| `/devices` | GET | doar MAIN | acum include `mode` (`matrix`/`legacy`), `profile`, `matrix`, `view_pii` per telefon |
| `/status` | GET | orice | acum include `permissions: {matrix, profile, mode, view_pii, categories}` și `capabilities` derivate din matrice |
| `/audit?limit=` | GET | doar MAIN | evenimente: `permissions_changed`, `profile_changed`, `delete` (cu starea de dinainte), `denied` (operațiune refuzată), `issued` (document emis) |

**Impunere pe server** (nu doar în UI): `pull`/`row`/`history`/`conflicts`/căutare/hartă/fișă loc/documente → 403 sau coloane omise dacă lipsește `view`; `push` verifică fiecare operațiune la momentul trimiterii (deci și cele puse în coadă offline înainte de revocare):
`insert` ⇒ `add` (+ `harta.add` dacă are poligon); `update` ⇒ `edit` pe categoria fiecărui câmp **modificat față de server** (+ lista `fields`); golirea poligonului ⇒ `harta.delete`; `delete` ⇒ `delete`.
Refuzuri: `status=rejected`, `reason=not_authorized | field_not_allowed | delete_not_allowed`, `detail="not_authorized:edit" | "field_not_allowed:cod_loc" | …` (nu se memorează în `sync_v2_ops`: după o acordare ulterioară aceeași operațiune poate trece).
**Ștergerea de pe telefon** este logică (tombstone, ajunge în MAIN prin pull), auditată (`sync_v2_audit.kind='delete'`, cu rândul de dinainte) și refuzată cu `reason=are_dependente` dacă înregistrarea are legături (loc→înhumați/contracte; concesionar→contracte; contract→înhumați/plăți). RLS: DELETE permis doar pentru MAIN sau când serverul a setat `cp.mobile_del` după verificarea permisiunii.
**Chitanțe**: același `plati.chitanta_nr` nu poate exista de două ori în cimitir → `status=conflict`, `reason=duplicate_receipt`.

### Emitere documente PDF (`/issue*`)
| Rută | Metodă | Permisiune | Ce face |
|---|---|---|---|
| `/issue/templates` | GET | orice | tipurile (`contract, chitanta, aviz, adeverinta_inhumare, adeverinta_concesiune`) cu `can_view`/`can_issue` |
| `/issue/preview/pdf?kind=&entity_uid=` | GET | `view` pe categorie | PDF fără emitere (previzualizare) |
| `/issue` | POST | `add` pe categorie | `{"kind","entity_uid","fields":{}}` → emite **o singură dată** per (tip, înregistrare); numărul continuă seria existentă (chitanță `CH-000008` după `CH-000007`), se scrie în rândul sincronizat (→ MAIN) și în `sync_v2_issued` |
| `/issue?entity_uid=&kind=` | GET | `view` | istoricul emiterilor |
| `/issue/next?kind=` | GET | `add` | următorul număr din serie, fără rezervare |
| `/issue/{uid}/pdf` | GET | `view` | PDF regenerat din `snapshot` (amprenta `pdf_sha256` verificată). Contractul folosește modelul `backend/doc_templates/contract_template.docx`; CNP/CI doar cu `view_pii` |

### Fundalul hărții
`PUT /map/background` (doar MAIN; `{mime,data(base64),sha256,width,height,version}`, ≤ 8 MB, amprenta verificată) și `GET /map/background` (necesită `harta.view`; antete `X-Map-Width/Height`, `ETag`). Stocare privată (PostgreSQL, rol cu RLS), fără URL public.
