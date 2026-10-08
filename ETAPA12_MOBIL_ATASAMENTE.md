# Etapa 12 — Interfață staff pentru telefon + arhivă generală de atașamente

Telefonul folosește **același** panou `/admin` (Jinja2 + HTMX), aceleași sesiuni, drepturi, audit și PostgreSQL/Storage ca desktopul.
Telefonul vorbește doar cu FastAPI prin HTTPS; nu primește acces la PostgreSQL și nu primește `SUPABASE_SECRET_KEY`.
Acces din afară: `/admin` trebuie servit de proxy-ul tău (nginx/Caddy, HTTPS). Pe infrastructura Emergent doar `/api/*` ajunge la backend.

## Ce s-a livrat (fazele 1 și 2)
| Zonă | Detalii |
|---|---|
| Layout mobil (≤768 px) | bară de navigare jos (Caută, Hartă, Locuri, Cereri, Meniu), tabele → carduri, câmpuri 16 px (fără zoom iOS), ținte ≥44 px, sertar/modale pe tot ecranul |
| Căutare unificată | `GET /admin/cauta?q=` (HTMX, live): locuri (cod/localizare), înhumați (nume/CNP/cod), concesionari (nume/CNP/telefon/email, cu `tel:`), contracte, plăți. Fără diacritice, min. 2 caractere, max. 25/categorie. Drept: `view` |
| Hartă | pan cu 1 deget, **pinch-zoom** cu 2 degete (doar vizualizare; desenarea rămâne în MAIN) |
| Atașamente | Model unic peste `documents` (vezi mai jos), panou generic, cameră (`capture`) + fișiere |
| Cereri online | Aprobare / respingere din web **numai cu dreptul `approve`** (admin, operator) |

## Arhiva de atașamente
Nu s-a creat un tabel paralel: `documents` era deja generic (`entity_type`, `entity_id`, `doc_type`, `caption`, `sha256`, `size`, `media_id` → Storage cu sincronizare MAIN).
Migrația `005_attachments.sql` adaugă doar: `description`, `original_filename`, `mime_type`, `uploaded_by`, `source`.

* `entity_type`: `loc` (cod_loc), `concesionar`, `contract`, `inhumat`, `plata`, `aviz`, `cerere` (id).
* `doc_type` (clasificare): contract_concesiune, act_aditional, chitanta, dovada_plata, certificat, cerere, aviz_constructie, schita, carte_identitate, declaratie, fotografie_loc, fotografie_monument, proces_verbal, alt_document (+ `cerere_doc` pentru încărcările din portal). Tipurile propuse diferă pe entitate.
* Formate: **JPEG, PNG, PDF**; verificare extensie **și** semnătură (magic bytes); max. 40 MB (`CIMITIR_MEDIA_MAX_BYTES`).
* `carte_identitate`: doar la concesionar / înhumat / cerere și cu motiv scris (descriere ≥5 caractere).
* Rute (cookie de sesiune): `GET/POST /admin/att/{tip}/{id}`, `GET /admin/attfile/{doc_id}[?download=1]`, `POST /admin/attdel/{doc_id}`. Citire: `view`; încărcare/ștergere: `edit`.
* Audit: `attachment.add` / `attachment.delete` cu doar metadate (id, tip, nume, mime, mărime, sha256) — niciodată conținut, căi de Storage sau chei.
* Fișierele se servesc cu `nosniff` și `Cache-Control: private, no-store`.
* Dacă Storage nu e disponibil la încărcare, documentul NU rămâne înregistrat doar pe discul serverului (se anulează și se raportează eroare).
* Ștergerea este definitivă (obiect din Storage + tombstone de sincronizare). `deleted_at` (ștergere logică) nu s-a introdus: ar fi dublat tombstone-urile existente.

## Decizii de politică
* **Contractele se pot crea din web/telefon cu dreptul `approve`** (politica MAIN-only a fost schimbată la cererea explicită a utilizatorului); `delete` pe contract rămânea deja permis cu `edit`.
* **Cereri**: aprobarea rulează aceeași funcție ca MAIN (`approve_cerere`: concesionar + contract draft + loc rezervat; idempotentă).
  O cerere decisă din web nu mai e livrată la preluarea MAIN, iar MAIN primește `409 decided_on_server` dacă încearcă să o decidă. Limitare: dacă MAIN a preluat deja cererea și apoi o aprobă și el local, decizia lui e refuzată la sincronizare (409) și trebuie rezolvată manual.

## Faza 3 (livrată)
* **Contract nou din web/telefon**: `POST /admin/ui/loc/{cod}/contract` (formular „Adaugă contract” în sertarul locului), numai cu dreptul `approve`.
  Validări: număr obligatoriu și unic, un singur contract activ pe loc, date `zz.ll.aaaa` valide (inclusiv data contractului și a începutului), sfârșit ≥ început,
  concesionar existent sau nou. Datele titularului se copiază din concesionar. La eroare formularul își păstrează valorile și revine pe tabul Contracte.
  Numărul contractului e text liber (ca în MAIN): dacă MAIN lucrează offline cu același număr, coliziunea nu se poate detecta între noduri.
* **PDF-uri** (aceleași generatoare ca în MAIN, `backend/cimitir/pdf_reports.py`): `/admin/pdf/contract/{id}`, `/chitanta/{id}`, `/adeverinta/{id}`, `/expirari?days=`,
  `/raport-financiar?start&end`, `/registru-inhumari?start&end`, plus `fisa.pdf`. Drept `view`; `?download=1` pentru descărcare; `no-store`; fiecare generare e auditată (`pdf.generate`).
  Butoane în sertarul locului și în pagina Rapoarte. Nu s-a portat contractul DOCX din șablon (necesită `python-docx`).
* **Istoric / audit**: `GET /admin/istoric/{tip}/{id}`, drept `approve` — creare/modificare (doar câmpurile schimbate, vechi → nou), atașamente, PDF-uri. Se deschide din panoul de atașamente.

## Teste
`backend/tests/test_attachments.py`, `backend/tests/test_logout_session.py`, `backend/tests/test_etapa5_main_authority.py` (politica pe cereri actualizată).
