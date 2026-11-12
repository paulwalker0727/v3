# P4 — Interconectare completă Locuri ↔ Concesionari ↔ Contracte

Branch: `feature/p4-interconectare-completa` (din `origin/main` @ `a53e56b`, merge PR #59). Un singur branch, un singur PR.

## 1. Ce era deja implementat și a fost reutilizat
* `entity_links.py` (strat de date doar-citire: istoric loc, locuri/concesionari asociați, candidați duplicat) și `entity_links_ui.py` (`LinkedRecordsDialog`, `PickDialog`, deschideri) — PR #43/#46.
* `AddContractDialog` (autocompletare loc/concesionar, instantaneu `owner_*` read-only la editare), `AddConcesionarDialog` (propune persoane existente, nu unifică automat), `LocuriWindow`/`ConcesionariWindow` cu butoane de legătură, `PlatiWindow`, `contract_generator`, drepturile P3 (`access_session.gate` + `_allowed` în funcțiile care scriu), `audit_log`, triggere de sincronizare `entity_sync` (FK transmise prin `uid`).
* Planul de legătură rămâne: **asocierea loc↔concesionar există numai prin contract** (`contracte.concesionar_id`, `id_loc`, `cod_loc`). Niciun contract fictiv, nicio schimbare de schemă.

## 2. Probleme reale descoperite
1. **Bug confirmat (tab Juridic).** `_save_contract_from_juridic()` trimitea `observatii=""`, `data_inceput=data_contract`, `data_sfarsit`, și nu trimitea câmpurile de loc; `save_contract()` completa „lipsa” cu valori goale/valori ale locului. Reproducere înainte de corecție (contract complet, se modifică doar valabilitatea): `observatii` „nota importanta” → `""`; `data_inceput` 2009-05-05 → 2010-01-01 (= data contractului); `cemetery_parcel` „PX” (valoare proprie contractului) → „P” (din loc). Demonstrat de `test_s6_defectul_juridic_demonstrat_cu_payloadul_vechi` și de scriptul `probe.py` (înainte/după).
2. **`save_contract()` la actualizare** scria `""` pentru orice cheie lipsă (cu excepția `owner_*`, `valabilitate`, `extra_data`) și recalcula `id_loc`/`cod_loc`/`concesionar_id` din payload → un apelant parțial putea muta/dezlega un contract.
3. **Editarea unui contract vechi permitea schimbarea concesionarului prin combo** (P3 doar avertiza după fapt) → titular asociat ≠ titular istoric.
4. **„Locul pe hartă” din Concesionari nu funcționa** (`MainWindow.highlight_loc_by_cod` nu exista; apărea „funcție indisponibilă”).
5. Tabelul Contracte arăta doar număr/date/cod loc (fără concesionar, sector/parcelă/rând, stare), fără filtru de concesionar, fără navigare spre fișe, hartă, plăți; nu păstra selecția după reîncărcare; nu trata contracte șterse.
6. Tabul Juridic din fișa locului (hartă) nu avea nicio legătură spre concesionar/contracte/istoric/plăți; selecția de pe hartă ducea la aceeași fișă ca din tabel, dar fără relațiile.
7. `LocuriWindow` deschisă dintr-o fereastră fără `actions` rămânea goală (nu căuta `actions` în lanțul de părinți).
8. Dialogul de contract nu avertiza la anulare cu modificări nesalvate; nu avertiza la contract posibil duplicat.

## 3. Ce s-a reparat / îmbunătățit
* **`shared/cimitir_database.py`**
  * `save_contract()` la actualizare scrie **doar cheile prezente**; nu atinge `concesionar_id`/`id_loc`/`cod_loc`/`owner_*` decât dacă sunt trimise (owner) și **refuză** schimbarea concesionarului/locului cu mesaj explicit (`last_refusal()`); autocompletarea din loc doar pentru cheile prezente dar goale. Crearea rămâne neschimbată.
  * `reassign_contract_concesionar(contract_id, new_id, update_owner_snapshot=False, reason="")` — **butonul „Schimbă concesionarul…”**: operațiune separată, drept `edit`, confirmări explicite, jurnal `contract.reassign_concesionar` (id-uri, fără date personale). Implicit instantaneul `owner_*` se păstrează; rescrierea lui din fișa persoanei noi este o alegere explicită (corectura unei asocieri greșite).
  * `associate_contract_loc(contract_id, cod_loc, reason="")` — asociere explicită a unui contract istoric unui loc **existent** (nu creează locuri), completează doar câmpurile `cemetery_*`/`sector` goale, jurnal `contract.associate_loc`.
* **`desktop/contracts_window.py`**: dialog — în editare concesionarul și locul sunt blocate; butoane „Schimbă concesionarul…” / „Asociază / schimbă loc…” (gated `edit`); anulare cu confirmare doar dacă există modificări; avertizare contract posibil duplicat (același număr+concesionar+loc); contract istoric fără persoană rămâne editabil. `ContractsWindow`: coloane Număr, Data, Concesionar, Cod loc, Sector, Parcelă, Rând, Perioadă, Stare (doar dacă `data_sfarsit` este interpretabil; altfel gol); filtre după număr / concesionar / cod loc + „Șterge filtrele”; butoane Fișa concesionarului, Fișa locului, Locul pe hartă, Istoricul locului, Plăți; dublu-click = modificare; selecția se păstrează la reîncărcare; contractele șterse sunt tratate cu mesaj. Generarea documentului extrasă în `generate_contract_flow()` (reutilizată din Juridic).
* **`desktop/panels.py` (tab Juridic)**: „Modifică / adaugă contract…” **reutilizează `AddContractDialog`** (singurul mecanism de salvare); butoane: Fișa concesionarului, Concesionari asociați/alege (adaugă nou, alege existent, contract nou), Contract nou, Istoric contracte, Generează contract, Plăți contract.
* **`desktop/entity_links.py`**: `contracts_overview()`, `contract_status()`, `contract_context()`.
* **`desktop/entity_links_ui.py`**: `open_plati_window`, `show_on_map`; evitarea ferestrelor duplicate (dacă `LocuriWindow`/`ConcesionariWindow` există deja în lanțul de părinți, se selectează înregistrarea acolo și dialogurile intermediare se închid).
* **`desktop/ui_mainwindow.py`**: `highlight_loc_by_cod()` (aceeași cale ca la clic pe hartă: `on_plot_clicked` → aceeași fișă și relații).
* **`desktop/locuri_window.py`**: butoane „Pe hartă”, „Plăți”; `actions` căutat în lanțul de părinți.
* **`desktop/concesionari_window.py`**: „Evidențiere pe hartă” folosește locurile reale (prin contracte); la mai multe locuri se alege.
* **`desktop/map_editor_actions.py`**: wrapper-e `reassign_contract_concesionar` / `associate_contract_loc` (emit semnal de reîmprospătare).
* Teste: `desktop/tests/test_p4_interconectare.py` (19 teste); `test_p3_istoric_contracte.py` — **un test actualizat** (`test_schimbarea_concesionarului_...`): vechea așteptare „combo schimbă titularul la salvare” înlocuită de cerința P4 (schimbarea numai prin operațiunea explicită, instantaneul păstrat).

## 4. Cum funcționează asocierea în fiecare modul
* **Locuri de veci** (tabel sau hartă → aceeași fișă): „Concesionari” (listă titulari, deschide fișa, alege existent → contract nou preselectat cu locul și persoana, adaugă persoană nouă → contract), „Contract nou”, „Contracte / istoric” (toate, inclusiv istorice; generare document), „Plăți”, „Pe hartă”, Dosare/documente/avize existente.
* **Concesionari**: „Locuri” (toate locurile prin contracte; alege loc existent → contract nou; deschide fișa locului), „Contract nou” (persoana preselectată; locul se alege în formular), „Contracte”, „Evidențiere pe hartă”. Persoanele nu se deduplică pe nume.
* **Contracte**: formular cu concesionar existent (sau „Adaugă concesionar”), loc existent (datele locului se completează automat), număr, date/perioadă; din rândul selectat se deschid fișa persoanei, fișa locului, harta, istoricul locului, plățile și documentul. Deschiderea acestor ferestre **nu modifică** contractul (test).
* Schimbarea titularului / asocierea locului unui contract existent: numai din dialogul contractului, cu dublă confirmare + motiv + jurnal.

## 5. Confirmare: nu s-au eliminat funcționalități
Nu s-a șters nicio funcție publică (`_save_contract_from_juridic`, `generate_contract`, `highlight_on_map` etc. există; își păstrează rolul). Singura modificare de comportament: editarea obișnuită nu mai poate schimba concesionarul/locul unui contract existent (cerință P4 §4) — înlocuită de operațiunea explicită. Nu s-au schimbat schema, șablonul Word, sincronizarea, backup-ul, AI, QR, fotografiile.

## 6. Fișiere schimbate
`shared/cimitir_database.py`, `desktop/contracts_window.py`, `desktop/panels.py`, `desktop/entity_links.py`, `desktop/entity_links_ui.py`, `desktop/locuri_window.py`, `desktop/concesionari_window.py`, `desktop/ui_mainwindow.py`, `desktop/map_editor_actions.py`, `desktop/tests/test_p3_istoric_contracte.py`, `desktop/tests/test_p4_interconectare.py` (nou), `docs/P4_DESIGN_SABLOANE_DOCUMENTE.md` (nou), `docs/P4_RAPORT_INTERCONECTARE.md` (nou).

## 7. Propunere (necesită aprobare) — procedura de transfer a titularului
Nu am inventat reguli juridice. Propunere de discuție: (a) transferul real al unei concesiuni = **contract nou** pe același loc pentru noul titular (istoricul ambilor titulari rămâne, ca în Scenariul 4), eventual cu câmp „contract anterior”; (b) „Schimbă concesionarul…” rămâne **doar pentru corectarea unei asocieri greșite**; (c) eventual un drept distinct pentru aceste operațiuni (acum: `edit`). Decizia aparține proprietarului.

## 8. Riscuri rămase / puncte NEVERIFICATE
Vezi `docs/P4_RAPORT_INTERCONECTARE.md` secțiunea de rezultate (generată la final) și pașii pentru Windows de mai jos.

### Pași de verificare ulterioară pe Windows cu interfață grafică (NEVERIFICAT în offscreen)
1. Selectați un loc **pe hartă** → fișa din dreapta → tab Juridic: butoanele (Fișa concesionarului, Contract nou, Istoric, Plăți) deschid ferestrele corecte și se închid fără blocarea ferestrei principale.
2. Contracte → rând selectat → „Locul pe hartă”: dialogurile se închid, locul este selectat/centrat pe hartă.
3. Dublu-click rapid pe „Salvează” în dialogul de contract (un singur contract).
4. Contract istoric → „Schimbă concesionarul…”: parcurgeți confirmările, verificați documentul Word generat (titular = instantaneu sau actualizat, după alegere).
5. Cont cu drepturi doar `add` / doar `edit`: butoanele dezactivate, operațiunile refuzate.
6. Aspect: lățimea coloanelor din tabelul Contracte la rezoluții mici; ordinea butoanelor în Locuri.
7. Sincronizare **doar pe mediu TEST** (nu PROD): după „Schimbă concesionarul…”, contractul ajunge pe cloud cu noul `concesionar_uid` și reapare corect pe Mobile v2.

## 9. Rezultate teste (SQLite, date sintetice, QT_QPA_PLATFORM=offscreen; fără PROD/Supabase)
* `desktop/tests/test_p4_interconectare.py`: **19 PASS** (scenariile 1–8, defectul Juridic înainte/după, schimbare explicită titular/loc + jurnal, permisiuni, anulare cu modificări, dublu-click, persoane omonime, contract istoric fără loc/persoană, contract șters, filtre/stare, hartă, FK de sincronizare după asociere).
* `desktop/tests/` (77 fișiere, rulate fișier cu fișier): 56 fișiere PASS integral + `test_p4_interconectare`; 21 fișiere cu eșecuri/erori/„no tests” **identice ca număr și rezultat cu `origin/main`** (comparate pe același mediu; cauze de mediu: dependențe lipsă — ex. Pillow/qrcode/segno, mediu fără Windows/Docker). Nu a apărut nicio regresie. P3 (`test_p3_*`, permisiuni, istoric, plăți) PASS; singurul test modificat: `test_p3_istoric_contracte::test_schimbarea_concesionarului_...` (vezi §3).
* `validation/final_integration`, `validation/bug_audit` (backup, restaurare, sincronizare, mobil): 72 PASS, 19 SKIP, 1 XFAIL, 1 ERROR de import (`test_mobile_v2_data_pipeline.py`, **identic pe `origin/main`**) — rezultat identic cu baza.
* `validation` + `backend/tests` complet: rularea se oprește la ~41% într-un test care se blochează **și pe `origin/main`** (mediu); nu a fost finalizată — **NEVERIFICAT** integral.
* UI offscreen: **NEVERIFICATE vizual** aspectul/ergonomia și comportamentul real al ferestrelor modale suprapuse (pași §8). Sincronizare reală cu cloud/Mobile v2: **NEVERIFICATĂ** (numai verificarea locală a payload-ului FK prin `uid` și intrarea în coadă).

## 10. Livrare
Branch publicat pe GitHub: `feature/p4-interconectare-completa` (un singur PR către `main`, deschis manual după revizuire; fără merge automat). Primul commit de funcționalitate: `5e3eb3f`. Patch și bundle git sunt păstrate separat doar ca rezervă.
