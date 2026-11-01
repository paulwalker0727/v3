# AUDIT MAIN ↔ MOBILE + REPARARE SINCRONIZARE CNP (Etapa 5 — implementare conservatoare)

Ramură: `fix/cnp-sync-audit` (din `origin/main` @ `ea68832`). Fără push / PR / merge. Fără acces la Supabase PROD (teste doar pe PostgreSQL 15 local + SQLite).

## 1. Traseul analizat

```
Mobile PWA (backend/static/mobile_app.js, mobile_offline.js)
  → POST /api/v1/sync-v2/push        (cimitir_sync_v2_http.py → SyncV2Service.push/_decide/_write)
  → PostgreSQL (tabele concesionari / contracte / inhumati …, trigger versiune + change_seq)
  → GET /api/v1/sync-v2/pull         (cursor change_seq, _wire, drop_wire/hidden_for)
  → MAIN: desktop/sync_v2.run → entity_sync.pull_entities/_apply_remote/_wire_to_local → SQLite
Sens invers: SQLite (triggere cs_*_ai/au/ad → cloud_sync_outbox) → entity_sync.flush_entities → push (MAIN) → PG → pull Mobile.
```

Specificația comună a câmpurilor (`shared/cimitir_entities.py`) este folosită de ambele capete; `cnp` este `text` peste tot (SQLite `TEXT`, PostgreSQL `TEXT`, JSON string) — zerourile inițiale se păstrează (verificat, vezi §4).

## 2. Defecte DEMONSTRATE și corectate

| # | Defect | Dovadă (test care pica înainte) | Corecție (minimă, aditivă) |
|---|--------|---------------------------------|----------------------------|
| D1 | **CNP/CI trimis de un telefon fără „acces date sensibile” (`view_pii`) este ignorat în tăcere**, iar serverul răspunde `applied` fără nicio indicație. Formularul Mobile afișa câmpul CNP (nemarcat `pii`) și utilizatorul credea că s-a salvat; în PostgreSQL valoarea rămânea `NULL`, deci nu ajungea niciodată în MAIN. Aceasta este cauza cea mai probabilă a bugului raportat. | `test_phone_without_pii_cannot_silently_lose_cnp` (versiunea inițială): `status=applied, stocat=None`, fără semnal | Contractul existent (valoarea sensibilă se ignoră, `applied` — verificat de `test_etapa789_pii_docs_pg`) **a fost păstrat**. `shared/cimitir_sync_v2.py::_decide/_process`: răspunsul listează acum explicit `ignored_fields` (ex. `["cnp"]`), fără a expune valoarea. PWA: câmpurile CNP (concesionar, înhumat) și Serie/nr. CI sunt marcate `pii` ⇒ nu mai apar fără drept; dacă totuși se trimit (ciornă veche), se afișează avertisment clar. Un CNP gol/ascuns NU șterge CNP-ul din MAIN (testat). |
| D2 | Formularul Mobile „Concesionar” **nu avea câmpul Serie/nr. CI (`bi_ci`)**, deși există în MAIN, în spec și în PostgreSQL ⇒ imposibil de completat/sincronizat de pe telefon. | comparație câmpuri (`parity_check.py`) | câmp adăugat în `FORMS.concesionari` (`pii:1`). |
| D3 | **Jurnalul operațiilor** (`sync_v2_ops.response`) memora în clar snapshot-ul serverului (inclusiv CNP/CI) în răspunsul unui conflict, pentru telefoanele cu `view_pii`. | `test_ops_journal_never_stores_cnp_in_clear` | `SyncV2Service._journal_safe`: la memorare se aplică `redact_wire`. Starea completă rămâne în `sync_v2_conflicts` (necesară pentru `accept_incoming`/`restore_previous`; vizibilă doar MAIN, RLS pe tenant) — vezi §5. |
| D4 | **Cache Mobile (IndexedDB) păstra CNP/CI după revocarea `view_pii`**, atât în rânduri cât și în ciorne (`draft:*`). La acordarea ulterioară a dreptului, cursorul incremental nu mai readucea CNP-ul. | analiză cod (`mobile_offline.js` curăța doar la upgrade de schemă) | `reconcilePiiCache()` (la fiecare sincronizare): la tranziția `view_pii` true→false se șterg CNP/CI din rânduri + ciorne; la orice schimbare se resetează cursoarele registrelor cu date personale. SW cache `v7→v8` ca să se încarce noul JS. |

## 3. Mod de funcționare păstrat intenționat (nu este bug)
* Telefonul **fără** `view_pii` nu primește CNP/CI la pull/row/search/conflicte (redactare pe server) — neschimbat, testat.
* Conflict: MAIN câștigă; scrierea Mobile cu `base_version` depășit → conflict deschis, nu se aplică (testat pe CNP).
* Field Inbox v1 (captură foto/formular de pe telefon) **nu transportă CNP prin design** (`cimitir_field_inbox.py`, `field_import.py`); CNP circulă doar prin Sync v2.

## 4. Rezultate teste (PASS / FAIL / SKIP)

Vezi `REZULTATE_TESTE_CNP.md`.

## 5. Constatări NEcorectate (în afara „bug demonstrat”; necesită decizie)
1. **Documente / CI scanat:** `sync_v2_documents` + Supabase Storage privat există (migrarea 024), dar categoriile funerare cerute (față/verso CI, certificat deces etc.) nu sunt modelate (`doc_type` liber, `kind` ∈ {document, photo}); asocierea unui document la mai multe entități nu există (o singură `entity/entity_uid`).
2. **`sync_v2_conflicts.incoming/server`** conțin CNP/CI în clar în baza cloud (acces doar prin rol MAIN + RLS). Soluție recomandată pentru etapa următoare: criptare la nivel de câmp (pgcrypto/KMS) sau stocare redactată + valori sensibile într-un tabel separat.
3. **IndexedDB / outbox / ciorne** păstrează CNP în clar cât timp telefonul are `view_pii` (necesar funcțional). Recomandare: criptare WebCrypto cu cheie nonextractibilă legată de asociere.
4. **`amenajari_avize`**: coloanele SQLite `stare, executant, data_inceput, data_finalizare, observatii` NU sunt în spec/PG ⇒ nesincronizate (necesită migrare aditivă + extindere `cimitir_entities.ENTITIES` și grant-urile din 022). Nu s-a modificat (nu este bug de CNP).
5. **Formulare Mobile fără câmpuri existente în MAIN**: loc (`categorie, tip, nr_vechi, numar_cf, ocupat, extra_data`), contract (`owner_fullname, owner_fullname_2, sector, cod_loc, extra_data`), înhumat (`cauza_deces, data_deshumare, profesie, religie, ultima_adresa`), plată (`cod_loc, created_at` – setate automat). Sunt păstrate la editare de pe telefon (`data={...cur,...vals}`), dar nu se pot introduce.
6. **Redundanța `owner_cnp` (contract) vs `cnp` (concesionar)**: MAIN le păstrează separat și generatorul de contracte face fallback pe concesionar; Mobile nu propagă CNP-ul concesionarului în `owner_cnp` (comportament identic cu MAIN, neschimbat).
7. Etapele 1–4, 6 (parțial) și 7 (matricea completă pe modul) NU sunt implementate în această livrare, conform acordului (doar audit + Etapa 5).

## 6. Matrice MAIN ↔ Mobile (rezumat)

| Modul MAIN | Funcții MAIN (cod) | Echivalent Mobile actual | Lipsă | Câmpuri nesincronizate | Endpointuri | Schemă |
|---|---|---|---|---|---|---|
| Locuri de veci (`locuri_window`, `edit_plot_window`, `polygon_edit_window`) | CRUD, poligon, sector, cod, suprafață | Formular loc + editor poligon, fișă loc, hartă offline | `categorie, tip, nr_vechi, numar_cf, ocupat` în formular | — (toate în spec) | `/sync-v2/push,pull,row,history` ✔ | — |
| Concesionari (`concesionari_window`) | CRUD, CNP, CI | Formular + asociere prin contract | **`bi_ci` (reparat)**; „asociază concesionar existent” din fișă; fără duplicat după CNP | — | push/pull ✔ | — |
| Contracte (`contracts_window`, `contract_generator`) | Generare PDF/DOCX, numerotare, plată | `issue` (contract/adeverințe PDF), numerotare centralizată `sync_v2_counters`, formular contract | acte adiționale, prelungiri, contract scanat | `owner_fullname(_2)`, `sector`, `extra_data` needitabile | `/issue*` ✔ | `sync_v2_counters` ✔ |
| Înhumați (`inhumati_window`, `add_inhumat_dialog`) | CRUD, adeverință | Formular + adeverință (`adeverinta_inhumare`) | deshumare, certificat deces (document) | `cauza_deces, data_deshumare, profesie, religie, ultima_adresa` needitabile | ✔ | — |
| Plăți (`plati_window`) | Plăți, chitanțe | Formular + chitanță PDF | chitanță scanată categorizată | — | ✔ | — |
| Avize | CRUD | Formular aviz + PDF | — | **`stare, executant, data_inceput, data_finalizare, observatii`** | ✔ | migrare aditivă necesară |
| Documente/foto | `sync_v2_docs` | upload foto/PDF, listare | categorii obligatorii, multi-entitate, înlocuire/arhivare | — | `/documents*` ✔ | extindere `sync_v2_documents` |
| Căutare globală | `ui_mainwindow` | `/mobile/search` + offline | documente, filtre sector/contract | — | `/mobile/search` ✔ | — |
| Verificarea evidențelor | alerte | — | tot | — | nou | — |
| Permisiuni/dispozitive | `mobile_permissions_panel`, `mobile_devices_window` | matrice 8×4, `view_pii`, revocare | — (revocarea blochează operațiile offline: testat) | — | ✔ | — |

## 7. Teste de acceptanță recomandate pentru etapele următoare
Fișa loc unificată (o singură interogare agregată pe `loc_uid`), asociere concesionar existent fără duplicat, documente multi-entitate, ciclu complet teren (foto → sync), criptare cache, matrice per câmp pe documente.
