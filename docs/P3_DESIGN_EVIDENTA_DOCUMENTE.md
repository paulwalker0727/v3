# P3 — Design propus: evidența documentelor generate (Word/PDF)

**Stare: PROPUNERE. Nimic din acest document nu este implementat. Necesită aprobarea proprietarului înainte de orice modificare a evidenței oficiale.**

## 1. Situația actuală (verificată în cod)

| Document | Generat de | Se înregistrează în DB? | Fișier |
|---|---|---|---|
| Contract Word | `desktop/contract_generator.py` (`generate_contract`) | NU | cale aleasă în dialog; se suprascrie dacă numele coincide (dialogul cere confirmare) |
| Contract / chitanță / adeverință PDF | `desktop/pdf_reports.py`, `backend/cimitir/pdf_reports.py` | NU | salvate la cerere (desktop) sau trimise inline (backend `/admin/pdf/...`) |
| Fișă loc PDF | `desktop/fisa_pdf.py` | NU | la cerere |
| Documente „issued” (chitanță/aviz/adeverință/contract) | `shared/cimitir_mobile_issue.py` (portal/mobil) | DA, cu snapshot și număr | vezi acel modul; flux distinct, neatins |

Tabela existentă `documents` (`entity_type, entity_id, doc_type, file_path, caption, nr_evidenta, date_added, media_id`) este destinată fișierelor **atașate** (scanări, poze), nu documentelor generate.
Jurnalul `audit_log` înregistrează deja `pdf.generate` în backend (țintă `tip:id`, mărime) — fără fișier și fără versiune.

## 2. Ce se dorește a fi păstrat

Tip document · identificator înregistrare asociată (`contract:ID`, `plata:ID`, `inhumat:ID`) · data generării · fișierul generat · autorul (dacă e disponibil) · versiunea (dacă e necesară).

## 3. Variante

### Varianta A — fără schimbare de schemă (recomandată ca prim pas)
Reutilizează `documents` cu `doc_type = 'generat_<tip>'` (ex. `generat_contract_word`, `generat_chitanta_pdf`), `entity_type/entity_id` = înregistrarea, `caption` = autor + marcaj de timp, `file_path` = fișierul copiat în `media/generate/`. Se înregistrează **la salvarea explicită a fișierului**, nu la fiecare previzualizare.
- Pro: compatibil cu schema, backup, sincronizare media și verificarea de integritate (DOC004) existente; fără migrare.
- Contra: nu are număr oficial/versiune; amestecă generatele cu scanările în aceeași listă (se filtrează după `doc_type`).
- Nu suprascrie niciodată: la regenerare se creează un rând nou (nume cu marcaj de timp), cel vechi rămâne.

### Varianta B — tabelă nouă `generated_documents` (migrare aditivă)
Coloane: `id, kind, entity_type, entity_id, generated_at, generated_by, file_path, sha256, version (INT, începe de la 1 per entitate+kind), supersedes_id NULL, note`. Versiunea = max+1 pentru aceeași (entity, kind); `sha256` permite detectarea modificărilor ulterioare ale fișierului.
- Pro: istoric curat și verificabil, fără amestec.
- Contra: migrare (nouă coloană/tabelă), extindere sync/backup/portal; necesită teste de migrare pe scheme istorice.

### Varianta C — numerotare oficială
Registru oficial cu numere consecutive (chitanțe, contracte). **Nu se implementează fără decizie juridică/contabilă** (serii, anulare, goluri admise, reset anual). Nu am inventat reguli.

## 4. Decizii necesare de la proprietar
1. A sau B (sau A acum, B mai târziu)?
2. Se înregistrează la salvarea fișierului sau și la tipărire/previzualizare?
3. Chitanțele generate trebuie să fie imuabile (fără regenerare cu alt conținut)?
4. Se dorește numerotare oficială (C)? Dacă da: serii, reguli de anulare, responsabil.
5. Autorul: se folosește utilizatorul MAIN autentificat (`access_session.current_user()`), deja disponibil pe desktop.

## 5. Impact asupra fluxurilor existente (A)
Un singur punct de apel după `generate_contract`/`save_pdf` (desktop) și, opțional, în `_pdf_response` (backend, doar la `download=1`). Fără modificarea șabloanelor, fără a atinge `documents` existente.
