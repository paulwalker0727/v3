# P4 — Design propus: șabloane Word/PDF pentru contracte și avize de construcție

**Stare: PROPUNERE. Nimic din acest document nu este implementat în P4.** Șablonul actual (`desktop/templates/contract_template.docx`) și `contract_generator.py` rămân neschimbate. Nu se inventează texte juridice, numere oficiale sau formulare administrative: conținutul șabloanelor îl furnizează proprietarul. Completează `docs/P3_DESIGN_EVIDENTA_DOCUMENTE.md` (evidența documentelor generate).

## 1. Situația actuală (verificată în cod)

| Element | Realitate |
|---|---|
| Generator contract | `desktop/contract_generator.py`: `generate_contract(contract_id, template_path, out_path)`; `_build_mapping(contract)` produce 13 chei fixe. |
| Șablon | un singur fișier `templates/contract_template.docx`, cale fixă în `ContractsWindow` / `EditorActions.generate_contract_document`. Conține exact: `contract_number, contract_date, owner_fullname, owner_fullname_2, owner_address, owner_id_card, owner_cnp, owner_phone, owner_email, cemetery_parcel, cemetery_row, cemetery_tomb, tomb_surface_m2` (formă `{{cheie}}`). |
| Înlocuire | paragrafe + celule de tabel; placeholder-ul „spart” între runs se reunește în primul run (stilul primului run se păstrează; formatarea mixtă din interiorul unui placeholder se pierde). |
| Sursa datelor titularului | `owner_*` din contract (instantaneu istoric, P3/P4); doar dacă sunt goale se folosește fișa concesionarului. |
| Avize | `AddAvizDialog` (`panels.py`) + tabul **Amenajări** din fișa locului; tabela `amenajari_avize` (`nr_aviz, data_aviz, lucrare, executant, data_inceput, stare, data_finalizare, observatii`). Nu există generare de document pentru aviz. |
| Evidența documentelor | tabela `documents` (atașări); documentul Word generat nu se înregistrează (vezi P3, variantele A/B/C). |

P4 a pregătit terenul: legăturile contract ↔ loc ↔ concesionar sunt acum verificabile (id-uri reale), iar `owner_*` este protejat — deci un șablon poate citi un singur „context de date” coerent.

## 2. Arhitectură țintă

### 2.1 Registru de șabloane (fără migrare în prima fază)
Director `templates/<tip>/` cu fișiere `.docx` + un manifest `templates/<tip>/<nume>.json` alăturat fiecărui șablon:
```json
{"kind": "contract_concesiune", "name": "Contract standard 2026", "version": "1.0", "description": "...", "sha256": "<calculat la încărcare>"}
```
* `kind` ∈ {`contract_concesiune`, `aviz_constructie`} (extensibil).
* Șablonul actual devine implicit ca „Contract (șablon curent)” fără a fi modificat.
* Utilizatorul adaugă șabloane cu un buton „Adaugă șablon…” (copiază fișierul, calculează `sha256`, validează placeholder-ele — vezi 2.3). Un șablon nu se șterge dacă există documente generate din el (doar „dezactivat”).
* Faza ulterioară (opțional): tabelă `document_templates` dacă se dorește sincronizare între calculatoare — **migrare aditivă**, decisă separat.

### 2.2 Alegerea șablonului înainte de generare
Pasul nou în fluxul existent „Generează contract”: **Alege șablon** (listă filtrată pe `kind`, implicit ultimul folosit) → **Previzualizare** → **Salvează ca**. Același dialog servește avizul: de pe fișa locului → Amenajări → „Generează aviz” (aviz selectat + loc), fără a schimba `AddAvizDialog`.

### 2.3 Câmpuri dinamice `{{...}}`
Sintaxă păstrată: `{{nume_camp}}` (litere, cifre, `_`). Spații permise în interior. Câmpuri grupate pe context (propunere de nume, prefixate pentru a nu intra în conflict cu cele existente):

| Context | Exemple de câmpuri | Sursa |
|---|---|---|
| Contract | `contract_number, contract_date, contract_start, contract_end, contract_validity, contract_notes` | `contracte` |
| Titular (instantaneu) | `owner_fullname, owner_address, owner_id_card, owner_cnp, owner_phone, owner_email` | `contracte.owner_*` (instantaneu) |
| Concesionar curent | `holder_current_fullname, holder_current_address, …` | `concesionari` (doar dacă șablonul o cere explicit) |
| Loc | `cemetery_sector, cemetery_parcel, cemetery_row, cemetery_tomb, tomb_surface_m2, loc_code` | `contracte.cemetery_*` → fallback `locuri_de_veci` |
| Aviz | `aviz_number, aviz_date, aviz_work, aviz_contractor, aviz_start, aviz_status, aviz_end, aviz_notes` | `amenajari_avize` |
| Sistem | `generated_on, generated_by` | data/ora, utilizatorul MAIN |

Regulă: cheile existente (13) rămân valide neschimbat. Un `{{câmp}}` necunoscut **nu** se înlocuiește în tăcere cu gol: apare în raportul de validare („câmp necunoscut în șablon”) și rămâne vizibil în previzualizare.

Se introduce un `DocumentContext` (dict plat) construit de un singur modul (extensie a `_build_mapping`), separat pe tipuri (`contract_context(contract_id)`, `aviz_context(aviz_id)`), astfel încât logica de date să nu depindă de UI.

### 2.4 Previzualizare înainte de salvare
1. Se construiește contextul, se listează câmpurile **lipsă/goale** (ca azi, `get_missing_placeholders`) și cele **necunoscute**.
2. Previzualizare text (tabel „câmp → valoare”) și, opțional, randare PDF temporară în `tempfile` (nu în `contracts/`) deschisă în vizualizatorul sistemului.
3. Numai după confirmare se scrie fișierul final. Anulare = nimic scris, nicio înregistrare.

### 2.5 Generare DOCX și, ulterior, PDF
* DOCX: python-docx, aceeași înlocuire robustă; se îmbunătățește păstrarea formatării (înlocuire la nivel de run când placeholder-ul e într-un singur run; reunire minimală doar când e spart, păstrând stilul primului run — comportamentul curent — și raportând cazurile cu formatare mixtă).
* PDF: etapa ulterioară. Opțiuni: (a) conversie prin LibreOffice/Word instalat (fidelitate maximă, dependență externă); (b) PDF propriu din `pdf_reports.py` (nu garantează aspectul șablonului). Recomandare: (a) cu detectare și mesaj clar când lipsește; PDF-ul nu înlocuiește DOCX-ul.

### 2.6 Arhivarea documentului generat și versiunea
Se aplică Varianta A din P3 (reutilizarea tabelei `documents`, fără migrare): `entity_type='contract'|'aviz'`, `entity_id`, `doc_type='generat_contract_word'|'generat_aviz_word'`, `caption` cu **numele și versiunea șablonului, `sha256` al șablonului, autor, marcaj de timp**. Regenerarea creează un rând nou (nu suprascrie). Dacă proprietarul alege Varianta B (tabelă `generated_documents`), aceleași informații devin coloane (`template_name, template_version, template_sha256, version, supersedes_id`). **Decizia A/B rămâne la proprietar** (vezi P3 §4).

Identificarea șablonului: `template_sha256` din manifest permite să se afle ulterior exact ce șablon a produs documentul, chiar dacă fișierul a fost înlocuit.

### 2.7 Permisiuni și jurnal
Generarea citește date sensibile (CNP, adresă): rămâne sub sistemul de drepturi P3 (fără rol nou). Propunere: generarea cere cel puțin `view` (ca azi); adăugarea/dezactivarea șabloanelor cere `manage_config`. Fiecare generare scrie în `audit_log` (`document.generate`, `contract:ID`/`aviz:ID`, șablon+versiune; fără date personale).

### 2.8 Sincronizare și mobil v2
Documentele generate sunt fișiere locale/atașări; dacă se arhivează în `documents`, se sincronizează prin fluxul media existent. Șabloanele sunt locale MAIN (nu intră în Sync v2 în prima fază). Datele-sursă (contract/loc/concesionar) se sincronizează deja cu FK pe `uid` (verificat în P4: `test_sync_pastreaza_legaturile_contractului_dupa_asociere_explicita`).

## 3. Aviz de construcție direct din locul selectat (pregătire)
Fluxul țintă: harta/tabelul de locuri → fișa locului → Amenajări → aviz selectat (sau „Aviz nou”) → „Generează aviz” → alege șablon aprobat → previzualizare → salvare/arhivare. P4 garantează că selecția de pe hartă și din tabel ajunge la aceeași fișă (`MainWindow.highlight_loc_by_cod` folosește `on_plot_clicked`), deci contextul locului este identic. **Nu se emite niciun document nou în P4.** Numărul avizului rămâne cel introdus manual în `AddAvizDialog`; **nu se generează numere oficiale**.

## 4. Etape propuse (fiecare cu aprobare)
1. Mapare cheie→sursă (`DocumentContext`) + teste pe șablonul actual (fără schimbarea lui).
2. Registru șabloane + alegerea șablonului + previzualizare (contract).
3. Arhivare în `documents` (A) cu versiune/șablon.
4. Șablon aviz + generare din Amenajări.
5. PDF.

## 5. Informații necesare de la proprietar
Șabloanele `.docx` (contract, aviz) cu lista câmpurilor dorite; decizia A/B pentru evidența generatelor; dacă PDF-ul este obligatoriu (și pe ce calculatoare e disponibil LibreOffice/Word); dacă documentul generat trebuie să fie imuabil; cine poate adăuga șabloane.
