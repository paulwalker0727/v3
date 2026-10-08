# Cimitir — Analiză & Recomandări de Îmbunătățire

> Document de analiză (fără modificări de cod). Aplicație desktop **PySide6 + SQLite**.
> Scop: separarea rolurilor admin/user, rapoarte PDF și îmbunătățiri generale.

---

## 1. Stare actuală (pe scurt)

**Ce funcționează bine:**
- Arhitectură modulară curată (`database.py` = single source of truth, façadă `EditorActions`).
- Editor hartă complet: poligoane, sectoare, ghidaje, rânduri, calibrare scară, grid, riglă.
- CRUD complet: locuri, concesionari, contracte, înhumați, plăți, poze, avize.
- Export/import CSV/XLSX, backup automat zilnic, export hartă PNG/PDF.
- Colorare pe status + legendă, rezumat cimitir, alerte contracte expirate.
- Autentificare cu roluri (admin/user), WAL mode pe SQLite (concurență sigură).

**Limitări actuale:**
- **User mode NU are hartă** — doar tabele (dashboard, căutare locuri/înhumați, contracte).
- User mode e **strict read-only** — nu poate adăuga înhumați / asocia concesionari / genera contracte.
- Nu există **rapoarte PDF** propriu-zise (doar contract `.docx` + export hartă imagine).
- Parole cu **SHA-256** (fără iterații) — slab pentru un produs vândut.
- Multe blocuri `except Exception: pass` — ascund erorile, îngreunează depanarea.

---

## 2. Arhitectura recomandată: 3 niveluri de permisiune

În loc de „admin poate tot / user nu poate nimic", introducem **capabilități granulare**.
Același `CemeteryMapView` este refolosit; se schimbă doar ce unelte sunt active.

| Nivel        | Structură hartă (desenare/ștergere poligon, sector, ghidaje, calibrare) | Operațiuni (înhumați, concesionari, contracte, plăți) | Vizualizare |
|--------------|:---:|:---:|:---:|
| **ADMIN / SETUP** | ✅ | ✅ | ✅ |
| **OPERATOR (user)** | ❌ | ✅ | ✅ |
| **VIEWER**   | ❌ | ❌ | ✅ |

### Flag „mod livrat / licență"
Cheie în tabela `settings`, ex. `structure_locked = 1`.
Când e activ, **uneltele structurale sunt dezactivate pentru toți** (chiar și admin),
astfel harta rămâne „înghețată" la clientul care a cumpărat programul.

**Avantaje:**
- Nu dublezi codul (un singur widget de hartă, cu nivel de permisiune).
- Harta desenată în admin apare identic în user (citită din `locuri_de_veci.points`).
- La vânzare, blochezi structura cu un singur setting — fără să atingi codul.

---

## 3. User mode nou (ce ar trebui să poată face operatorul)

1. **Harta finală interactivă** (citită din DB, fără unelte de desenare):
   - Pan/zoom, căutare loc pe hartă, colorare pe status.
   - Click pe loc → panou lateral cu fișa completă.
2. **Adaugă înhumat** pe un loc existent (dialogul `AddInhumatDialog` există deja).
3. **Asociază concesionar** pe un loc salvat (leagă `contracte.concesionar_id` / loc).
4. **Generează contract de concesiune** (folosind `contract_generator.py` + template).
5. **Înregistrează plăți** (modulul `plati` există deja în DB).
6. **Fără** editare de geometrie (poligon/sector/ghidaje/calibrare).

> Practic: se refolosește `CemeteryMapView` + `panels.py` + dialogurile existente;
> se ascund toolbar-urile de structură (`build_editor_toolbar`, `build_advanced_toolbar`).

---

## 4. Rapoarte PDF (prioritate cerută)

Recomand biblioteca **`reportlab`** (PDF nativ, control total) sau **`docxtpl` + LibreOffice headless** dacă vrei layout identic cu `.docx`.

Rapoarte utile de generat:
1. **Contract de concesiune** (deja există `.docx` → adaugă export PDF).
2. **Chitanță / dovadă plată** (din tabela `plati`).
3. **Fișă loc de veci** (loc + concesionar + înhumați + istoric plăți) — 1 pagină.
4. **Adeverință de înhumare**.
5. **Situație contracte expirate / care expiră** (din `expiring_contracts`).
6. **Raport financiar** (total încasări pe perioadă/sector).
7. **Registru înhumați** (listă pe perioadă, cu filtre).

**Recomandare de implementare:** un modul nou `pdf_reports.py` (fără Qt, ușor de testat),
similar cu `reports_export.py`, cu funcții `render_contract_pdf(...)`, `render_receipt_pdf(...)`, etc.

---

## 5. Alte îmbunătățiri recomandate (prioritizate)

### P0 — Importante pentru un produs vândut
- **Parole**: trecere la `PBKDF2-HMAC-SHA256` cu multe iterații (fără dependințe noi, e în `hashlib`) sau `bcrypt`.
- **Audit/jurnal**: cine a modificat ce și când (tabelă `audit_log`) — esențial la primării.
- **Gestionare utilizatori din UI** (creare/ștergere/resetare parolă) — funcțiile există în `auth.py`, lipsește fereastra.

### P1 — Fiabilitate & UX
- **Reducerea `except Exception: pass`** → logging real într-un fișier (`logging` modul), ca să poți depana la client.
- **Validări date**: CNP (13 cifre), date calendaristice, unicitate cod_loc.
- **Backup manual + restaurare din UI** (restore există în `reports_export`, lipsește butonul).
- **Restaurare backup cu confirmare** + listă backups disponibile.

### P2 — Funcționalități noi
- **Dashboard cu grafice** (locuri pe status, încasări lunare) — `matplotlib`/`QtCharts`.
- **Căutare globală** (un singur câmp care caută în locuri + înhumați + concesionari + contracte).
- **Hartă în user mode cu status live** (colorare automată la deschidere).
- **Notificări la pornire**: contracte care expiră în 30 zile, plăți restante.
- **Import înhumați / concesionari** (acum se importă doar locuri).
- **Multi-cimitir** (dacă vinzi la mai multe primării): coloană `cimitir_id`.

---

## 6. Concluzie & recomandare finală

**Arhitectura ideală (răspuns la întrebarea din brief):**
1. Un singur widget de hartă, cu **3 niveluri de permisiune** (Admin / Operator / Viewer).
2. **User (Operator)** vede harta finală din admin și poate: adăuga înhumați, asocia
   concesionari, genera contracte, înregistra plăți — **fără** editare de geometrie.
3. **Flag „mod livrat"** în `settings` care blochează uneltele structurale la vânzare.
4. **Evită** exportul/importul manual de DB ca flux principal (doar ca backup/migrare).

**Ordinea sugerată de lucru (când trecem la implementare):**
1. Hartă interactivă în user mode (read-only geometrie, click → fișă).
2. Operațiuni în user mode (înhumați / concesionari / contracte / plăți).
3. Modul rapoarte PDF (`pdf_reports.py`).
4. Flag „mod livrat" + gestionare utilizatori din UI.
5. Securizare parole + audit log.
