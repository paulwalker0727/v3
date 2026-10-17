# STATUS IMPLEMENTARE — Cimitir Mobile v2 · Sync v2 (ramura `sync-v2-emergent-wip`)

Bază: `12a3c7b` (`mobile-v2`) + patch-ul Sync v2 anterior (`9457db4`) + această sesiune. **Nu s-a atins `main`, `mobile-v2` sau PROD. Supabase TEST NU a fost modificat** (migrarea 022 așteaptă aprobarea ta). Push pe GitHub: **nu a fost posibil** (fără token) → livrare prin ZIP + patch.

Legendă: ✅ terminat și testat automat · 🟡 parțial · ⛔ neterminat · 🔬 testat doar local (Linux + PostgreSQL local), **neverificat pe Windows real / Supabase TEST**.

## 1. Cele 6 probleme din audit

| # | Problemă | Stare | Ce s-a făcut / dovadă |
|---|---|---|---|
| 1 | Cheia MAIN Binding (SecretStore) neconectată | ✅🔬 | `desktop/sync_v2.py::connection()` citește `cloud_url` + `cloud_secret_ref` din registrul local MAIN și valoarea din SecretStore (DPAPI); fallback: cheia profilului Cloud dacă are formă Binding. Cheia nu apare în SQLite/loguri. `scripts/configure_main_sync.py --binding-key-prompt` pentru configurare. Test: `test_main_uses_pilot_binding_key_from_secretstore_without_manual_setup` |
| 2 | MAIN trebuie să folosească `/api/v1/sync-v2/`, nu `/api/sync/*` | ✅🔬 | `sync_all`/`ping`/`entity_sync` trec prin Sync v2 când există cheie Binding; `cloud_sync._request` **refuză** să trimită o cheie Binding către rutele legacy (test `legacy == "blocked"`). Rutele legacy pentru media/QR rămân blocate în acest mod (vezi „Neterminat”) |
| 3 | 022 extindea permisiuni/RLS | ✅🔬 | Rescrisă pe drepturi minime: INSERT/UPDATE doar pe **coloane explicite** (fără `id/version/change_seq/modified_at`, fără mutarea tenantului), `DELETE` doar prin RLS pentru dispozitive MAIN, triggerele de versiune/tombstone `SECURITY DEFINER` (cimitir_cp_app nu mai are drepturi pe `sync_tombstones`/`sync_change_seq`), tabelele noi fără acces `anon/authenticated` (Supabase), `sectoare` doar upsert MAIN. Verificate în `test_sync_v2_migration.py` |
| 4 | Rollback verificabil | ✅🔬 | 022 salvează starea de dinainte (politici, drepturi pe tabele/coloane/secvențe, funcții) + amprentă în `sync_v2_migration_backup`; `022_sync_v2_ROLLBACK.sql` o restaurează și **refuză (ROLLBACK tranzacție)** dacă amprenta nu coincide. Test cu snapshot independent înainte/după (+ aplicare→rollback→aplicare→rollback). `apply_sync_v2_migration_test.py --rollback` |
| 5 | Scripturi Docker adaptate | 🟡🔬 | `deploy/windows/laptop_cloud_test.ps1` (status / build cu tag / canary pe 8084 / switch cu revenire automată / rollback; copiază mediul din containerul sursă fără a-l afișa; 8080 protejat). **PowerShell netestat** (fără Windows/Docker aici) |
| 6 | Instalator Windows | ✅🔬 (Python) / 🟡 (.ps1) | `scripts/install_patch.py` (+ `INSTALL_PATCH.ps1`): verifică ramura și commitul de bază, **se oprește** dacă fișiere atinse de patch au modificări locale sau patch-ul nu se aplică curat, backup + manifest SHA-256, idempotent („DEJA aplicat”), respinge căi periculoase, rollback doar dacă fișierele nu s-au schimbat. 5 teste pe repo-uri git reale. Wrapperul `.ps1` nu a fost rulat |

## 2. Etapa A — Sincronizare reală bidirecțională

| Cerință | Stare | Note |
|---|---|---|
| Locuri + coordonate/poligoane | ✅🔬 | |
| Sectoare | 🟡 | MAIN → cloud (upsert pe etichetă, doar la schimbare, doar MAIN); telefonul le afișează pe hartă. **Fără ștergere/sync invers** de sectoare |
| Concesionari, contracte, înhumați + legături | ✅🔬 | Relațiile circulă prin uid-ul părintelui (nu id numeric). Test complet: loc + concesionar + 2 înhumați + contract + plată + aviz → telefon; apoi telefon → MAIN |
| Plăți / taxe | ✅🔬 | Citire/scriere controlate de rol (`read_finance`/`register_payments`) |
| Avize/lucrări (cerere) | 🟡 | Se creează de pe telefon și ajung în MAIN; **nu există flux de aprobare/stare** |
| Documente și fotografii | ⛔ | **Nesincronizate** în Mobile v2 (necesită stocare de obiecte + drepturi). Migrarea nu atinge tabelele/fișierele de media existente. Încărcarea din PWA arată un mesaj explicit „nu este încă disponibilă” |
| „Cimitire” (entitate) | ⛔ | Cimitirul/organizația se provizionează în cloud (nu se recreează); MAIN se leagă prin binding |
| Istoric modificări | 🟡 | `/history` din jurnalul `sync_v2_ops` (cine/când/ce tip) afișat în fișă; **fără diff pe câmpuri** |
| Incremental / idempotent / conflicte | ✅🔬 | cursor `change_seq`; `op_id` idempotent (8 thread-uri simultane → 1 rând); `base_version` → conflict, MAIN câștigă, variantele rămân în `sync_v2_conflicts` |
| Coadă offline + reluare | ✅🔬 | PWA (IndexedDB) și MAIN (outbox SQLite) |
| Ștergeri/anulări | 🟡 | Ștergerea din MAIN → tombstone → cloud → telefon; telefonul **nu poate șterge**. Nu există ștergere de sectoare |
| Izolare organizații/cimitire | ✅🔬 | Tenant din binding + RLS (`cp_org/cp_cem_ok`); testat cross-tenant (pull, row, history, capabilități, conflicte) |
| Confirmare numai după salvare | ✅🔬 | `applied` doar după COMMIT; testat și cu eșec/reluare |

## 3. Etapa B/E — Mobile v2 (PWA)
✅🔬 Dashboard, navigare **Acasă · Hartă · Căutare · Registre → Locuri · Înhumați · Contracte · Plăți · Documente · Sincronizare**; hartă SVG cu pan, **pinch/zoom**, atingere → fișă, culori disponibil/ocupat/rezervat, filtrare după cod; căutare după loc/sector/concesionar/persoană; fișă (loc, concesionar+contract, înhumați, plăți, avize, istoric); formulare cu validare în română, confirmare la plăți/contracte, **buton blocat în timpul salvării**, **ciorne păstrate** la închidere/eroare (`draft:*`), indicator „Conectat/Offline”, buton Sincronizare cu stări reale; **fără UUID în UI** (verificat prin regex în teste).
🟡 Formulare: concesionar, înhumat (+ asociere cu locul — se creează din fișa locului), loc, contract (cu alegere concesionar), plată, cerere aviz. **Lipsesc**: mutarea unui înhumat între locuri, ștergere, încărcare documente/foto, aprobare avize. Design vizual: funcțional, nu redesenat integral.

## 4. Etapa C — Offline
✅🔬 Copie locală **doar a registrelor permise** (descărcare incrementală pe cursor), căutare/fișă/hartă/liste offline, coadă cu marcaj „nesincronizat” și contor, sincronizare automată la reconectare (`online` event), conflicte afișate. **Purjare**: la dezasociere, 401, retragerea dreptului (403 → registrul se șterge local) și re-asociere la alt cimitir (coada veche se pune deoparte, nu se trimite). 🟡 Copia locală **nu este criptată** (IndexedDB al browserului); PWA-ul necesită prima încărcare online (service worker pentru shell există, neverificat offline-la-rece).

## 5. Etapa D — Permisiuni/securitate
✅🔬 12 capabilități; roluri-șablon **administrator, preot paroh, operator, utilizator limitat** + rol personalizat; aplicate **pe server** (pull/row/history/push, `/mobile/search|map|graves|summary|data-status`), MAIN le setează din dialogul „Telefoane Mobile” (combo „Rol”; GUI Qt **netestat**) sau API. Implicit pentru telefon fără rol = **limitat (fără plăți)** (după audit SEC-001). RLS consolidat prin 022 (vezi mai sus). Fără chei admin pe telefon; cheia Binding nu circulă prin `http`. 🟡 `upload_documents`, `approve_requests`, `manage_users`, `export_print` sunt în model dar **fără funcții asociate** încă.
Audit de securitate (static): 0 critice/înalte; SEC-001 (medie) remediat; 3 recomandări P3 remediate (numărători pe rol, path traversal în instalator, http+Binding, detaliu IntegrityError).

## 6. Teste (vezi `REZULTATE_TESTE.md` pentru numere)
Sync v2 / migrare / instalator / PWA (Chromium real): vezi fișierul de rezultate. Rulările parțiale pe întregul repo: 972 trecute / 41 eșuate la o rulare paralelă; la rulare serială **aceleași 18 eșecuri + 1 eroare există și pe baza 9457db4** (dependențe lipsă: `reportlab`, `fitz`, `.env`, roluri PG) — **nu sunt regresii**.

## 7. Blocaje / ce lipsește pentru „gata de producție”
1. **Migrarea 022 pe Supabase TEST nu a fost rulată** (aprobare necesară; `--check-only` + `--rollback` pregătite).
2. Nimic nu a fost rulat pe **Windows real**, cu **SecretStore DPAPI real**, Docker sau Supabase TEST; cheia pilot MAIN depinde de valorile `cloud_url`/`cloud_secret_ref` din registrul tău local — dacă numele diferă, scriptul `--binding-key-prompt` o reconfigurează.
3. Documente/foto, aprobare avize, sync de sectoare ștergere, export/print — neimplementate.
4. Verificarea vizuală a PWA pe iPhone/Android reale nu s-a făcut (doar Chromium 390×844).
5. Rutele legacy media/QR ale MAIN rămân nefuncționale cât timp MAIN folosește doar cheia Binding.
