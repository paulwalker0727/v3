# Phone Capture v1 — pagina „Trimite din teren”

Pagina mobilă din portalul local (`/admin/teren`) prin care un telefon **deja asociat** (Phone Pairing v1) trimite în **Field Inbox v1** (API existent + Storage privat existent).
Fără backend paralel, fără migrare nouă (001–019 neschimbate), fără sistem nou de autentificare, fără coadă offline.

## Fluxul
```
Hartă (existentă) → drawer loc → „Trimite din teren” → /admin/teren?cod_loc=…      (sau „Caută locul” / „Alege pe hartă” în pagină)
Telefon (JS: static/teren.js) --Authorization: Binding <cheia telefonului>--> API cloud Field Inbox (/api/v1/field-inbox/…)
```
- Portalul local oferă doar: pagina, cimitirul registrului activ, locul selectat (`cod_loc`, `grave_uid` = `locuri_de_veci.uid`) și lucrările locului care au deja `uid` (țintă pentru poze lucrare). Nu primește și nu retransmite cheia telefonului.
- Telefonul vorbește direct cu API-ul cloud existent (HTTPS). Configurare: `CIMITIR_FIELD_INBOX_URL` pe portal (URL public, nu secret) și `CIMITIR_FIELD_INBOX_CORS_ORIGINS` pe API-ul cloud (origini explicite ale portalului; `*` este ignorat; fără valoare nu se montează CORS).
- Singura adăugire în API: `GET /api/v1/field-inbox/me` → `{cemetery_uid, device_type}` (autentificat cu legătura; fără alte date).

## Meniu
Persoană înhumată (DECEASED) · Lucrare (WORK) · Aviz (PERMIT) · Document (DOCUMENT: PDF/JPEG/PNG) · Poză loc (GRAVE_PHOTO) · Poză lucrare (WORK_PHOTO: stage BEFORE/DURING/AFTER obligatoriu + lucrarea țintă).
- Câmpuri: exact cele acceptate de `FieldInboxService` (`_FIELDS`); WORK: tip lucrare → `descriere`, `executant`, `data_inceput` (+ `data_sfarsit`, `observatii`) — ajung în câmpurile native MAIN la import.
- Datele goale rămân goale (nu se trimit); nimic nu se completează automat cu data de azi. `<input type=date>` → `ZZ.LL.AAAA` (formatul MAIN).
- Poză loc / poză lucrare: `<input type="file" accept="image/*" capture="environment">`. Documentele trec prin `POST /items/{uid}/files` (Storage privat); niciun octet nu ajunge în PostgreSQL.

## Selectarea locului și cimitirul
Înainte de trimitere pagina afișează cimitirul, locul selectat și `grave_uid`. Trimiterea e blocată dacă locul nu are `grave_uid`, dacă telefonul e asociat altui cimitir decât registrul activ al portalului (verificat de server prin `/me`, înainte de fiecare trimitere), dacă legătura e revocată/expirată/neasociată sau dacă cheia salvată nu e a unui telefon (MOBILE).

## UX Save / retry / offline
- „Trimitere...” + buton dezactivat de la primul tap (și o singură cerere în zbor: dublu-tap = aceeași promisiune).
- Succes: „Trimis în Field Inbox — ref. XXXXXXXX” (primele 8 caractere din UID-ul itemului).
- Eșec rețea/5xx/răspuns pierdut: „Nu s-a putut trimite. Încearcă din nou.”; formularul rămâne; retry-ul refolosește același `idempotency_key` și același `file_uid` (același conținut). Conținut modificat după eșec = cheie nouă (altfel 409). Nu se raportează succes fals.
- Fără coadă persistentă: reîncărcarea paginii pierde formularul și cheia (nu implementat, conform cerinței).

## Securitate
- Pe telefon se păstrează o singură valoare: cheia propriei legături (`localStorage`, cheia `cimitir.phone.binding.v1`, + `cemetery_uid`), revocabilă din MAIN; se șterge la 401. Codul de pairing nu se salvează (se folosește o dată), URL-urile semnate nu se cer niciodată, nimic nu se loghează (fără `console.*`).
- Cheia apare doar în antetul `Authorization`; niciodată în URL/corp. Telefonul nu primește parola PostgreSQL, `SUPABASE_SECRET_KEY`, `service_role` sau credențiale de cleanup (nu există în pagină/răspunsuri; verificat prin teste).

## Compatibilitate cu Field Inbox Local Import
Itemurile produse sunt importate în teste cu `field_import.import_item` (DECEASED, WORK cu `executant`/`data_inceput`, PERMIT, DOCUMENT PDF/JPEG/PNG, GRAVE_PHOTO, WORK_PHOTO BEFORE/DURING/AFTER). `WORK_PHOTO.target_uid` = `uid` al lucrării locale sau UID-ul itemului WORK trimis în sesiune (UID-ul entității importate = UID-ul itemului).

## Limitări cunoscute
- Field Inbox v1 nu validează în cloud că `grave_uid` aparține cimitirului (nu există registru de locuri în cloud); importul local blochează `MISSING_GRAVE`.
- Dacă încărcarea fișierului eșuează definitiv după crearea itemului, itemul rămâne PENDING fără fișier (MAIN nu îl poate accepta; se respinge).
- iOS poate oferi HEIC pentru `image/*`: serverul acceptă doar JPEG/PNG; pagina afișează mesaj clar.

## Teste
- `cd desktop/tests && python -m pytest test_phone_capture.py` (JS-ul real al telefonului în Node, față de routerele reale Field Inbox/Phone Pairing pe HTTP local + import local; necesită `node`).
- `cd backend && python -m pytest tests/test_phone_capture_portal.py -n 0` (pagina portalului, fără secrete în răspunsuri).
- PostgreSQL/Supabase nu sunt atinse; producția nu e atinsă.
