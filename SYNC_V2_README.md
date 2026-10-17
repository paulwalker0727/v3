# Sync v2 — MAIN ↔ Cloud TEST ↔ Mobile v2 (ramura `mobile-v2`)

Lucrează exclusiv pe TEST. Nu atinge `main`, PROD, parole sau chei. Nu șterge/recreează baze de date.

## 1. Analiza (ce exista și unde era defectul)

**Surse de adevăr**
- MAIN: SQLite local (`locuri_de_veci`, `concesionari`, `contracte`, `inhumati`, `plati`, `amenajari_avize`, `sectoare`). Identificator stabil = `uid` (hex32/UUID, text). Relațiile locale sunt prin `id` numeric; pe fir circulă prin uid-ul părintelui (`loc_uid`, `concesionar_uid`, `contract_uid`) — `shared/cimitir_entities.py`.
- Cloud TEST: PostgreSQL Supabase, aceleași tabele „live", cu `organization_uid`/`cemetery_uid` + RLS (migrările 008–013); `version` + `change_seq` puse de triggere (003).
- Mobile v2 citește **doar** tabelele live (`shared/cimitir_mobile_registry.py`, migrarea 020 = doar SELECT).

**Defectul (cauza reală a „loc verde, fără persoană, buton fără efect")**
1. Cloud-ul TEST rulează `shared/cimitir_cloud_app.py`, care **nu monta niciun endpoint de sincronizare de entități**. `desktop/entity_sync.py` vorbește cu `/api/sync/entities/*` (server vechi, `X-Sync-Key`), deci ce făcea MAIN nu putea ajunge în aplicația cloud folosită de telefon.
2. Singura punte construită era `POST /mobile/test-polygon/import`: INSERT al **unui singur poligon** în `locuri_de_veci` (migrarea 021 = INSERT doar pe acel tabel). Nu există cale pentru `inhumati`, `concesionari`, `contracte`, `plati`; rolul `cimitir_cp_app` nici nu avea drept de scriere pe ele.
3. Fișa locului din Mobile caută înhumații după `inhumati.id_loc` → fiind 0 rânduri în `inhumati`, persoana nu apărea.
4. PWA nu avea buton „Sincronizează" (doar „Reverifică datele" = re-citire), iar în MAIN „Sincronizează" rula pașii legacy (`X-Sync-Key`) care nu ajung la această aplicație.

## 2. Ce s-a implementat

| Componentă | Fișier |
|---|---|
| Migrare aditivă: drepturi de scriere + RLS pe tenant, jurnal, conflicte, permisiuni, rulări | `backend/cimitir/pg_migrations/022_sync_v2.sql` |
| Serviciu server (autorizare binding+RLS, idempotență, conflicte, jurnal) | `shared/cimitir_sync_v2.py`, `shared/cimitir_sync_v2_http.py` (montat în `cimitir_cloud_app.py`) |
| API `/api/v1/sync-v2/`: `status`, `pull`, `push`, `row`, `conflicts`, `conflicts/{id}/resolve`, `devices`, `devices/{ref}/permissions`, `runs` | idem |
| MAIN (Windows): transport Binding + ciclu „Sincronizează" | `desktop/sync_v2.py`; `cloud_sync.sync_all()` îl folosește automat când profilul Cloud are cheie MAIN (Binding); `entity_sync.py` doar a primit `_call()` |
| MAIN: fila/dialog „📱 Telefoane Mobile" (permisiuni per telefon, conflicte, jurnal) | `desktop/mobile_devices_window.py` (+ buton în `cloud_sync_window.py`) |
| Mobile PWA: buton „Sincronizează", panou de stare, coadă offline (IndexedDB), reluare, editare/adăugare | `backend/static/mobile_app.js/.css` |
| Aplicare migrare pe TEST (verificări înainte/după, rollback automat, gărzi anti-PROD) | `scripts/apply_sync_v2_migration_test.py` |
| Rollback acces scriere (fără ștergere date) | `backend/cimitir/pg_rollbacks/022_sync_v2_ROLLBACK.sql` |
| Verificare read-only pe cloud real (locuri·poligon·concesionari·înhumați) | `scripts/verify_sync_v2_live.py` |
| API documentat | `SYNC_V2_API.md` |

**Protocol**: `push` = op idempotent `{op_id, entity, uid, action, base_version, data}`; răspuns `applied | conflict | rejected | retry`. Reluarea aceluiași `op_id` întoarce același răspuns (`duplicate`), același `uid` cu date identice este „adoptat" (fără duplicat), iar cheia naturală (`cod_loc` per cimitir) nu poate duplica locuri. `pull` = incremental pe `change_seq` (rânduri + ștergeri/tombstones). Tenantul se deduce **doar** din binding (nu din payload) și este impus și de RLS (`cimitir_cp_app`, fără BYPASSRLS).

**Politica de conflict (aleasă de utilizator): MAIN câștigă, conflictul se marchează și se jurnalizează**
- scriere MAIN cu `base_version` depășit → se aplică; starea suprascrisă rămâne în `sync_v2_conflicts` (`main_wins`) și se poate restaura din MAIN („Restaurează varianta telefonului");
- scriere Mobile cu bază depășită → NU se aplică; conflict `open`, vizibil pe telefon (Renunță / Reia peste varianta nouă) și în MAIN (Păstrează MAIN / Acceptă modificarea telefonului).

**Permisiuni Mobile → MAIN**: implicit telefonul este doar pentru citire. Din MAIN → „📱 Telefoane Mobile" bifezi registrele editabile per telefon (locuri, concesionari, contracte, înhumați, plăți, avize). Ștergerea de pe telefon este refuzată. Impunere server-side. Un înhumat creat pe telefon marchează locul ca având înhumări.

**Jurnal**: `sync_v2_ops` (fiecare operațiune, cu rezultat), `sync_v2_conflicts`, `sync_v2_runs` (fiecare push/pull). UUID-urile nu apar în UI (telefoanele apar după nume; în PWA nu se afișează identificatori).

## 3. Rulare / instrucțiuni

### Cloud TEST (o singură dată, înainte de deploy)
```powershell
$env:CIMITIR_CP_ADMIN_DATABASE_URL = "<URL admin proiect TEST>"   # nu se salvează nicăieri
py scripts\apply_sync_v2_migration_test.py --project-ref nhhczdmcocbobptogvsw
```
Apoi redeploy-ul serviciului cloud din ramura `mobile-v2` (același `shared/cimitir_cloud_app.py`).

### MAIN pe PC-ul Windows
1. `git pull` pe `mobile-v2` în `C:\Users\xboxw\Desktop\v3-main`; reporniți aplicația.
2. Profilul Cloud activ trebuie să aibă cheia MAIN (Binding) a cimitirului TEST (cea existentă din pairing). Nu este nevoie de alte setări: **„Sincronizare cloud → Sincronizează acum"** (și sincronizarea automată la 5 min) folosesc acum Sync v2.
3. Prima sincronizare trimite tot registrul local (rândurile cu `version=0`): locul de test deja importat (același `uid`) este actualizat de MAIN (MAIN câștigă), nu duplicat.
4. „📱 Telefoane Mobile" → bifați ce poate edita fiecare telefon.
> MAIN nu se conectează niciodată direct la PostgreSQL/Supabase și SQLite-ul local nu este accesat de cloud: totul trece prin HTTPS cu cheia Binding.
> Cu Sync v2 activ, ciclul „Sincronizează" rulează doar registrul (locuri/concesionari/contracte/înhumați/plăți/avize). Publicarea hărții pe portalul public și cererile online (flux legacy `X-Sync-Key`) nu sunt rulate în acest mod până la o etapă separată.

### Teste (necesită PostgreSQL local de test; nu se atinge Supabase)
```bash
scripts/dev_postgres.sh start
export CIMITIR_TEST_PG_ADMIN_URL=postgresql://postgres@127.0.0.1:55432/postgres
cd backend && python -m pytest tests/test_sync_v2_pg.py tests/test_sync_v2_main_e2e.py tests/test_sync_v2_pwa.py -n 0 -q
# PWA: pip install playwright && playwright install chromium
```
- `test_sync_v2_pg.py` (8): MAIN→Mobile cu relația, idempotență/fără duplicate, cheie naturală, permisiuni, Mobile→MAIN, conflicte (MAIN câștigă, rezolvări), izolare între cimitire/organizații, binding revocat, tombstones, jurnal.
- `test_sync_v2_main_e2e.py` (5, inclusiv scenariul complet loc+concesionar+contract+2 înhumați → Mobile → modificări în ambele sensuri, și scriptul de verificare): proces MAIN real (SQLite + `sync_all()`), HTTP real, PostgreSQL real: primul rezultat cerut; **întrerupere internet** (server oprit → coadă → reluare, fără duplicate); Mobile→MAIN și conflict.
- `test_sync_v2_pwa.py` (1): PWA în Chromium: buton Sincronizează, persoana vizibilă în fișă, editare pe telefon, adăugare **offline** → coadă → reluare, fără UUID în UI.

## 4. Limitări cunoscute (onest)
- Neverificat pe Supabase TEST real (parola nu a fost disponibilă în sesiune); verificat doar pe PostgreSQL local cu aceleași migrări și rol `cimitir_cp_app` fără BYPASSRLS. Rulați migrarea 022 pe TEST și apoi testul manual din secțiunea 5.
- Dialogul Qt „Telefoane Mobile" a fost verificat doar în modul offscreen (construcție, populare, salvare) — nu pe Windows.
- Telefonul citește online (nu are oglindă IndexedDB completă): editarea unei înregistrări existente cere conexiune pentru a citi versiunea curentă; adăugarea și coada funcționează offline.
- UI-ul de editare de pe telefon acoperă înhumați, locuri (stare/observații) și concesionari; serverul acceptă toate cele 6 registre. Plăți/contracte/avize în UI vor urma.
- Fundalul hărții/scala și PDF-urile nu fac parte din acest pas.

## 5. Test manual pe TEST (MAIN → Mobile → MAIN)
1. În MAIN adaugi/modifici un loc și o persoană înhumată → „Sincronizare cloud → Sincronizează acum" (rezultat: trimise N, conflicte 0).
2. Pe telefon (Mobile v2) → „Sincronizează" → Caută persoana → fișa locului o arată.
3. MAIN → „📱 Telefoane Mobile" → bifează „Înhumați" pentru telefon.
4. Pe telefon editezi persoana → se sincronizează automat; în MAIN „Sincronizează acum" → modificarea apare în MAIN.
