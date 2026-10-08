# Pasul 6 — Test pe telefon real (4G/5G): checklist și diagnostic

Arhitectura (neschimbată): **MAIN / Telefon → HTTPS → FastAPI → PostgreSQL Supabase + Supabase Storage (`cimitir-private`)**.
Cheile (`DATABASE_URL`, `SUPABASE_SECRET_KEY`) există doar în `backend/.env` de pe server. MAIN și telefonul nu le primesc.
Telefonul primește doar un link cu token temporar (5–10 min, o singură entitate, o singură utilizare).

> Acest document pregătește testul. **Nu a fost executat pe un telefon fizic.** Rezultatul „PASS" îl pui tu după pașii de mai jos.

## 0. Înainte (o singură dată)
1. Intră în `/admin` cu contul `Alin` și schimbă parola temporară (se cere automat la primul login).
2. În `/admin/setari` → **Sincronizare MAIN ↔ server** → *Generează cheie nouă de sincronizare* și copiaz-o (apare o singură dată).
   Atenție: o cheie nouă o înlocuiește pe cea veche. Cheile emise de testele automate nu mai sunt valabile după asta.
3. În MAIN → *Sincronizare cloud*: URL server = valoarea `CIMITIR_PUBLIC_URL` (https), cheia de la pasul 2 → *Sincronizează acum*.
   Locul/contractul/etc. pe care îl alegi mai jos trebuie să fi fost sincronizat măcar o dată (MAIN o face singur dacă lipsește).
4. Opțional, pe server: `python scripts/diagnose_mobile_upload.py --key <cheia MAIN>` → trebuie să afișeze `TOT OK`.

## 1. Checklist (fă-l pe telefon cu Wi-Fi OPRIT, doar 4G/5G)
| # | Pas | Ce trebuie să vezi |
|---|-----|--------------------|
| 1 | Deschide MAIN | fereastra principală |
| 2 | Alege o entitate: loc, concesionar (fereastra Concesionari), contract, înhumat, plată (fereastra Plăți) sau aviz | entitatea e selectată |
| 3 | Apasă **Continuă pe telefon** | apare dialogul cu cod QR și textul „Aștept încărcarea…" + timp rămas (max. 10 min) |
| 4 | Scanează QR-ul cu camera telefonului (Wi-Fi oprit) | se deschide o pagină „Încarcă document" cu numele entității |
| 5 | Apasă **Fă fotografie** (sau *Alege PDF / imagine*) și fă poza | apare numele fișierului |
| 6 | Apasă **Încarcă** | bara de progres, apoi „Document încărcat… Poți închide pagina" |
| 7 | Înapoi în MAIN | dialogul scrie „Document primit de pe telefon: <fișier>"; închide dialogul |
| 8 | Redeschide entitatea / apasă *Reîncarcă* | documentul apare în lista de documente a entității |
| 9 | Închide complet MAIN și repornește-l | documentul e încă acolo (este și în Supabase) |
| 10 | Scanează din nou același QR | pagina spune „Linkul a fost deja folosit" (corect) |

Rezultat: scrie **PASS** dacă pașii 1–9 au mers și 10 a fost respins; altfel **FAIL** la pasul X și rulează diagnosticul de mai jos.

## 2. Dacă pică — diagnostic rapid
Pe server: `python scripts/diagnose_mobile_upload.py --key <cheia MAIN> --url "<linkul din QR, dacă îl ai>"`

| Simptom | Cauză probabilă | Verifică / rezolvă |
|---|---|---|
| MAIN: „Serverul a refuzat: CIMITIR_PUBLIC_URL nu este configurat / trebuie https public" | **URL public** | `CIMITIR_PUBLIC_URL=https://…` în `backend/.env`, repornește backend-ul |
| MAIN: „Entitatea nu există pe server" | sincronizare nefăcută | *Sincronizează acum* în MAIN și reîncearcă |
| MAIN: „Nu mă pot conecta la server" / 401 | **FastAPI** sau cheia de sincronizare | serverul oprit, URL greșit în MAIN, cheie înlocuită (generează alta în `/admin/setari`) |
| Telefonul nu deschide linkul / eroare de certificat | **URL public** / TLS / DNS | `diagnose` pasul 1; linkul din QR trebuie să înceapă cu `https://` și să nu fie IP local |
| Pagina spune „Linkul a expirat" | **token** (>10 min) | generează un QR nou |
| „Linkul a fost deja folosit" înainte de upload | **token** consumat (single-use) | generează un QR nou |
| „Link invalid" | **token** greșit/modificat | scanează din nou QR-ul din MAIN; după 5 încercări greșite: blocare temporară (429) |
| Încărcarea dă „Salvarea a eșuat. Încearcă din nou" | **Storage** | `diagnose` pasul 4: cheie Supabase / bucket `cimitir-private` |
| Eroare 503 la emiterea tokenului | **PostgreSQL** | `diagnose` pasul 3: `DATABASE_URL` (Session Pooler IPv4), migrările 006/007 |
| Upload „ok", dar MAIN nu vede documentul | sync media | în MAIN rulează *Sincronizează acum*; verifică în Supabase: `media_objects` are rândul cu `entity_uid` = UID-ul entității |
| Fotografia e refuzată | tip/dimensiune | acceptate: JPEG, PNG, PDF; max. conform paginii (poza se micșorează automat pe telefon) |

Unde se vede originea mobilă: `audit_log` (acțiunea `attachment.add`, `actor_type = mobile`, `detail.source = "mobile"`) și coloana `documents.source = 'mobile'`.

## 3. Ce NU face acest flux
Nu rotește cheile Supabase, nu schimbă parola PostgreSQL, nu atinge Mongo, nu migrează SQLite, nu creează altă bază și nu e o aplicație Android.
Modul LAN (URL din cerere) rămâne doar fallback când serverul **nu** e în `CIMITIR_MODE=online`.
