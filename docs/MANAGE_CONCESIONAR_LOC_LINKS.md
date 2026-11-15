# Eliminarea și înlocuirea locurilor asociate

În Concesionari → Locuri de veci, selectarea unui rând permite:

- **Elimină asocierea**: elimină legătura locului din contractele dintre persoana
  curentă și locul selectat. Locul rămâne în cimitir; contractele rămân în istoricul
  concesionarului, fără loc asociat.
- **Înlocuiește locul**: alege un loc existent și actualizează legătura acelorași
  contracte. Lista se reîncarcă imediat; lista principală de concesionari se
  reîncarcă la închiderea dialogului, conform fluxului existent.

Asocierea este derivată din contracte, nu există o tabelă separată de asocieri.
Confirmarea enumeră toate contractele afectate de rândul selectat, inclusiv cele
istorice. Pentru corectarea unui singur contract se păstrează operațiunea
existentă din Contracte → Modifică → Asociază / schimbă loc.

Operațiunea necesită dreptul edit, reverificat la scriere. Se solicită confirmare
(implicit Nu) și un motiv opțional. Modificările sunt atomice, cu verificarea
listei de contracte sub BEGIN IMMEDIATE; dacă asocierea s-a modificat între
confirmare și salvare, operațiunea este refuzată și lista se reîncarcă.

Se modifică exclusiv contracte.id_loc și contracte.cod_loc. Nu se șterg locuri,
concesionari sau contracte; nu se mută plăți, documente ori înhumați. Instantaneele
owner_* și ale locului rămân identice, astfel încât documentele istorice pot
continua să afișeze datele de la emitere. Corecțiile sunt jurnalizate per contract,
cu identificatorii asocierilor vechi/noi, folosind mecanismul de audit existent.
Nu există migrări de schemă și nu se creează contracte suplimentare.

## Validare (15 noiembrie 2026)

73 teste trecute pe macOS ARM64, Qt offscreen, exclusiv baze SQLite fictive:

- test_manage_concesionar_loc_links.py (18 cazuri noi)
- test_p4_interconectare.py
- test_legaturi_loc_concesionar_contract.py
- test_p3_istoric_contracte.py
- test_contract_form_simplification.py
- test_p2_documente.py

Acoperire: multiple contracte pentru un loc, alte persoane/locuri neafectate,
destinație deja asociată, legături vechi doar prin cod, precedența id_loc,
păstrarea tuturor câmpurilor istorice și plăților, jurnal, selecție și reîncărcare,
anulare în fiecare etapă, utilizator fără drepturi, refuz la listă învechită,
rollback la eroare și generarea efectivă DOCX/PDF după eliminare/înlocuire.

Verificare vizuală cu tema dark la 760×440. Fără acces la Cloud PROD sau baze reale.
Cod comun Windows/macOS; rularea nativă Windows și executabilele împachetate nu
au fost testate. Avertismente locale PyMuPDF și urllib3/LibreSSL, fără teste eșuate.
