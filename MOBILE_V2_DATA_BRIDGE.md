# Cimitir Mobile v2 — puntea lipsă MAIN ↔ Cloud TEST ↔ PWA

## Ce se știe sigur din arhiva de la 16.10.2026

1. Serverul vechi `backend/server.py` montează `backend/sync_api.py` și `backend/entity_sync_api.py` (`/api/sync/entities/push`, `/pull`). `desktop/entity_sync.py` se conectează acolo cu `X-Sync-Key`.
2. Cloud-ul TEST pornit în Docker utilizează **alt entrypoint**, `shared/cimitir_cloud_app.py`, care montează Phone Pairing, Field Inbox și Mobile v2 sub `/api/v1/*`. **Acest proces NU include routerele `/api/sync/*`**. Trimiterea entităților MAIN la endpointul vechi nu poate alimenta automat această aplicație.
3. Fluxul de bootstrap/control-plane poate publica în `cp_registry_rows`/`cp_registry_row_data`, NU în `locuri_de_veci`/`concesionari` etc. Documentul `ETAPA4_PAS6A_POSTGRES.md` precizează că mapping-ul din `cp_registry_rows` spre tabelele Sync V2 a fost **amânat**.
4. `shared/cimitir_mobile_registry.py` citește direct tabelele *live*, nu pe cele generice CP. Migrarea `020_mobile_v2_read.sql` acordă SELECT prin rolul `cimitir_cp_app` + RLS, fără drepturi de scriere.
5. Pairing funcțional dovedește identitatea dispozitivului, **nu** existența unei sincronizări a datelor reale.

## Ce aduce această modificare (etapa de diagnostic sigur)

- `GET /api/v1/mobile/data-status` (autentificare `Binding`, strict scoping organizație + cimitir și RLS): numără rânduri *live*, poligoane desenate, rânduri CP publicate. Nu întoarce nume, CNP, UID-uri, credențiale sau date din alt tenant.
- În PWA apar mesaje distincte pentru: import CP existent dar nematerializat, registru live gol, respectiv locuri fără poligoane. „Reverifică datele” / „Actualizează” refac cererea către cloud, nu trimit/suprascriu date.
- Fișa unui loc funcționează și pentru `uid` hexadecimal de 32 caractere (formatul generat în SQLite), și pentru UUID36, fără schimbarea valorii TEXT stocate în PostgreSQL.
- Fișa include avizele de construcție/amenajare deja existente în cloud (read-only).
- `scripts/diagnose_mobile_v2_local.py` verifică **strict read-only** tabelul MAIN real: numărători, geometrii, versiuni `0`, triggere sync, coadă și setări fără chei/date personale.
- Teste independente de baza reală în `validation/test_mobile_v2_data_pipeline.py`.

**Atenție:** aceasta este o etapă de diagnostic și corecție, nu pretinde că transferă automat datele MAIN, că implementează editări mobile sau că rezolvă sincronizarea completă. Nu porni o migrare a datelor reale înainte de implementarea punții noi.

## Ce trebuie implementat ulterior, în ordine

### A. Convergență de identificitate/tenant

- MAIN are un `Binding` MAIN activ, persistat doar în SecretStore; sync cloud v2 folosește **exclusiv** acesta la API-ul nou, nu cheia legacy `X-Sync-Key`.
- Pentru fiecare operațiune se deduc server-side organization_uid și cemetery_uid din binding. Nici clientul și nici fișierul local nu pot alege tenantul prin payload.
- Datele din CP bootstrap și tabelele live trebuie să aibă o regulă unică de materializare/versionare. Nu accepta două surse de adevăr active simultan.

### B. Snapshot inițial, în TEST

- Transmite snapshotul real din MAIN (inclusiv `locuri_de_veci.points`, `sectoare`, concesionari, contracte, înhumați, plăți, avize); rândurile copil păstrează relațiile prin UID, nu prin ID-ul numeric SQLite.
- Folosește transfer verificabil: manifest, SHA-256 per batch, numărători și recuperare la întrerupere; reutilizează validările `cimitir_transfer_contract` și mecanismul CP staging deja existente.
- Materializează în registrul ales pentru Mobile numai după verificarea integrității, într-o operațiune atomică, cu FK/tenant coerente, și fără ștergeri în MAIN.
- Include harta completă: poligoane, sectoare, sistemul local de coordonate și ulterior fundalul/scala; nu transforma pixelii în coordonate GPS.

### C. Sync continuu bidirecțional

- API nou sub `/api/v1/sync-v2/`: `status`, `pull` incremental (`change_seq`), `push` idempotent (`op_id`, `base_version`) și `conflicts`.
- MAIN: outbox SQLite existent → API nou cu Binding MAIN; pull nou → oglindă SQLite. Nu îndrepta direct cererile existente `/api/sync/*` spre serviciul CP, deoarece folosesc altă identitate și alte politici de tenant.
- Mobile: IndexedDB + outbox, retry, tombstones și rezolvare explicită a conflictelor; datele nu se suprascriu în tăcere.

### D. Ecran/permisiuni/documente

- Permisiuni administrate din MAIN, per dispozitiv/rol, pe module și acțiuni (citire/editare/aprobare). Impunere obligatorie **pe backend**, nu doar ascunderea meniurilor.
- UI: hartă completă; fișe conexe; contracte, plăți, avize, adeverințe; generare PDF și arhivare; fotografii private via URL semnat. În UI se afișează codul locului și numărul documentului, nu UUID-ul intern.

## Acceptanță minimă înainte de utilizare cu date reale

1. Selectăm un singur cimitir TEST legat la MAIN; numărul locurilor și poligoanelor din MAIN corespunde cu numărul lor din cloud, apoi cu PWA.
2. Un loc desenat recent în MAIN apare în harta Mobile, se deschide și afișează legăturile cu concesionari/contracte/înhumați/plăți/avize.
3. Offline MAIN: modificările rămân în outbox. Reconectare: acestea ajung în cloud și pot fi recitite pe mobil, fără duplicare.
4. Concurență: `base_version` depășit → conflict vizibil și recuperabil, fără pierderi de date.
5. Un binding din alt cimitir nu poate citi, publica sau descărca datele acestuia; revocarea unui telefon îl blochează imediat la server.
6. Absență date/secrete în loguri și UI, test migrare 020 + granturi RLS, TEST validat separat de PROD.

## Pași locali (Windows / branch `mobile-v2`)

Din `C:\Users\xboxw\Desktop\v3-main`:

```powershell
git switch mobile-v2
git status --short
py scripts\diagnose_mobile_v2_local.py --output mobile-v2-local-report.json
py -m pytest -q validation\test_mobile_v2_data_pipeline.py
```

Dacă descoperirea registrului selectat de MAIN nu merge, rulează cu `--db "calea_bazei_SQLite_a_registrului_activ"`.

Raportul nu conține nume, CNP-uri, UUID-uri sau chei. Într-un cloud TEST actualizat cu acest patch și migrarea 020, PWA `/mobile/` afișează automat starea publicării. Nu lipi în conversație tokenuri, stringuri PostgreSQL sau fișiere `.env`.

**În această etapă NU se rulează operațiuni de transfer/deschidere PROD și NU se modifică branch-ul `main`.**
