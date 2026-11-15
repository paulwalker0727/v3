# Simplificarea formularului de contract

Formularul comun `AddContractDialog` afișează numărul și datele contractului,
codul locului / Date loc, selectorul de concesionar / Adaugă concesionar /
Date concesionar, observațiile și butoanele de salvare/anulare. Datele personale
și detaliile locului nu mai au rânduri în formular. Controalele interne de
instantaneu sunt ascunse explicit, cu parent în dialog, pentru păstrarea
mecanismului existent de colectare și compatibilitatea testelor istorice.

Selectorul rezolvă ID-ul din eticheta exactă afișată: tastarea unui nume incomplet
nu mai poate salva accidental ID-ul selecției anterioare. Reconstruirea listei
blochează semnalele intermediare; adăugarea reutilizează ID-ul deja salvat de
fișa concesionarului, fără un al doilea INSERT.

La editare, selecțiile existente rămân blocate. Schimbările explicite de titular
sau loc păstrează operațiunile existente cu confirmare și jurnal. Salvarea
obișnuită omite atât owner_*, cât și instantaneul locului, inclusiv valorile
istorice NULL, goale sau zero. Nu există migrări, eliminări de coloane ori
modificări în generatorul de documente.

## Căi verificate

- Contracte: `ContractsWindow.add_contract` și `edit_contract`.
- Locuri: `LocuriWindow.new_contract`, istoricul filtrat și dialogul comun.
- Concesionari: `ConcesionariWindow.new_contract`, istoricul filtrat și dialogul comun.
- Fișa locului / Juridic: `panels._save_contract_from_juridic`.
- Navigarea comună: `entity_links_ui.open_contract_dialog` și `open_contracts_window`.

## Validare — 15 noiembrie 2026

68 teste trecute pe macOS ARM64, Python 3.9, PySide6 6.10.3, Qt offscreen,
ReportLab 4.5.1. Exclusiv SQLite fictiv în directoare temporare; fără Cloud PROD
și fără baze reale. Verificare vizuală offscreen cu tema dark la 640×560.

Suite executate cu `python -m pytest -q`:

- `desktop/tests/test_contract_form_simplification.py` (4 cazuri noi)
- `desktop/tests/test_concesionar_duplicat.py`
- `desktop/tests/test_contract_double_save.py`
- `desktop/tests/test_legaturi_loc_concesionar_contract.py`
- `desktop/tests/test_p2_documente.py`
- `desktop/tests/test_p3_istoric_contracte.py`
- `desktop/tests/test_p4_interconectare.py`
- `desktop/tests/test_f01_main_dates.py`

Mediul de test a setat CIMITIR_DATA_DIR și CIMITIR_LOG_DIR în /private/tmp,
CIMITIR_ENV_FILE către un fișier inexistent, CIMITIR_DB_ENGINE=sqlite,
CIMITIR_MODE=local, DATABASE_URL gol și QT_QPA_PLATFORM=offscreen.
Testele noi reutilizează izolarea în subprocese și baze temporare a suitei de
deduplicare. Verifică selecția, textul invalid/completat, vizibilitatea,
salvarea/anularea, preselectarea la editare, egalitatea tuturor celorlalte câmpuri
istorice după editarea observațiilor și conținutul DOCX/PDF efectiv generat.

Limitări: nu s-au rulat executabile împachetate sau o sesiune Windows nativă.
Codul modificat este comun Qt, fără ramuri dependente de platformă. Deschiderea
DOCX în Word/LibreOffice și tipărirea nu au fost testate. Mediul Python local
emite un avertisment urllib3/LibreSSL și unul PyMuPDF de depreciere; testele trec.
