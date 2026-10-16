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
