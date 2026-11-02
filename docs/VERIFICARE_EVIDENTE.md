# Verificare evidențe (Pasul B) — instrument de diagnostic, DOAR CITIRE

Instrument care analizează baza SQLite a MAIN și raportează probleme de integritate. **Nu modifică nimic** și **nu corectează automat**.

## Ce garantează (și cum este verificat)
- Fișierul se deschide `mode=ro` (nu se creează dacă lipsește); dacă nu există jurnal `-wal`, se folosește `immutable=1`, deci nici măcar fișiere auxiliare `-wal/-shm` nu apar lângă bază.
- `PRAGMA query_only=ON` + **autorizator SQLite** care refuză orice altceva decât `SELECT`/citire/funcții și PRAGMA-uri de inspecție. Pe o conexiune primită din exterior, autorizatorul se instalează doar pe durata rulării și se scoate la final.
- Raportul include SHA-256 al fișierului bazei (și al `-wal`) **înainte și după**; `db_unchanged=true` dacă sunt identice.
- Repetabil: două rulări consecutive dau același raport (testat).
- Raportul **nu conține CNP, serii CI, telefoane, e-mailuri, adrese sau nume**; înregistrările se identifică prin `id`/`uid`/`cod_loc`. O plasă de siguranță mascează orice secvență de 13 cifre sau serie CI. Testat cu date sintetice sensibile.

## Folosire (Windows PowerShell)
```powershell
cd C:\cale\spre\repo
# recomandat: lucrați pe o COPIE a bazei (Backup din MAIN), nu pe fișierul în uz
python tools\verificare_evidente.py --db "C:\Temp\cimitir_copie.db" --out "C:\Temp\raport_verificare"
# opțiuni: --deep (recalculează SHA-256 ale fișierelor), --no-info, --only locuri,contracte, --max-per-check 5000, --media-root DIR
```
Cod de ieșire: `0` fără erori/critice, `1` există erori/critice, `2` eroare de utilizare. Se generează `verificare_evidente.json`, `.csv` (UTF-8 cu BOM, protejat contra formulelor Excel) și `.md`.
API Python (pentru fereastra din Pasul C): `cimitir_integrity.run_on_file(path, progress=cb, cancel=cb)` / `run_checks(conn, ...)`.

## Severități
- **critică**: referință către o înregistrare inexistentă sau identificator lipsă (legături rupte).
- **eroare**: relație obligatorie lipsă sau date contradictorii.
- **avertisment**: suspiciune ce trebuie verificată de un om.
- **informație**: observație; nu este problemă.

## Reguli respectate
- O persoană (un rând `concesionari`) legată prin mai multe contracte de mai multe locuri **nu** este duplicat.
- Duplicat de concesionar = **același CNP** (valoarea nu apare în raport). Același nume cu CNP-uri diferite = doar `CON005` informativ. Același nume + același telefon/e-mail/adresă, fără CNP care să-i diferențieze = `CON006` avertisment. Nimic nu se contopește automat.
- Relațiile opționale valide (înhumat fără contract, loc fără poligon, contract cu titular doar în text) nu sunt erori.
- Nu s-au inventat reguli de business: pragurile provin din cod (`MAX_POINTS=500`, `MAX_TEXT=4000`, stările avizului din `cimitir_entities.AVIZ_STARI`, formatul CNP legal, formatele de dată folosite de MAIN).
- Soldul financiar **nu** se verifică: schema nu are obligații/scadențe (`FIN000`).

## Catalogul verificărilor
| Cod | Severitate | Categorie | Descriere | Remediabil automat (etapă viitoare) |
|---|---|---|---|---|
| LOC001 | critică | Locuri de veci | Loc de veci fără identificator (cod_loc) | nu |
| LOC002 | avertisment | Locuri de veci | cod_loc cu spații la capete sau caractere de control | nu |
| LOC003 | avertisment | Locuri de veci | Loc fără uid de sincronizare | da |
| LOC004 | avertisment | Locuri de veci | Sectorul locului nu există în tabela de sectoare | nu |
| LOC005 | eroare | Locuri de veci | Poligon invalid (JSON/structură/coordonate) | nu |
| LOC006 | avertisment | Locuri de veci | Poligon degenerat (sub 3 puncte, arie zero sau autointersectat) | nu |
| LOC007 | avertisment | Locuri de veci | Același poligon folosit de mai multe locuri | nu |
| LOC008 | avertisment | Locuri de veci | Poligon peste limita de sincronizare (puncte) | nu |
| LOC009 | avertisment | Locuri de veci | Posibil loc duplicat (același cod normalizat sau aceeași poziție) | nu |
| LOC010 | informație | Locuri de veci | Loc marcat „liber” deși are înhumați (capacitate epuizată sau necunoscută) | nu |
| LOC011 | avertisment | Locuri de veci | Valori numerice/JSON invalide la loc (suprafață, extra_data) | nu |
| LOC012 | informație | Locuri de veci | Marcaj „are înhumări” fără înhumați înregistrați | nu |
| LOC013 | avertisment | Locuri de veci | Câmp text peste limita de sincronizare | nu |
| SEC001 | eroare | Sectoare | Poligon de sector invalid | nu |
| CON001 | eroare | Concesionari | Concesionar fără nume și prenume | nu |
| CON002 | informație | Concesionari | Date de identificare/contact incomplete | nu |
| CON003 | avertisment | Concesionari | CNP invalid ca structură/cifră de control | nu |
| CON004 | avertisment | Concesionari | Posibil același concesionar înregistrat de mai multe ori (CNP identic) | nu |
| CON005 | informație | Concesionari | Același nume la mai mulți concesionari (nu este dovadă de duplicat) | nu |
| CON006 | avertisment | Concesionari | Același nume și aceleași date de contact la concesionari diferiți fără CNP care să-i diferențieze | nu |
| CON007 | eroare | Concesionari | Conflict de identitate: același CNP, nume diferite | nu |
| CON008 | informație | Concesionari | Concesionar fără niciun contract | nu |
| CON009 | avertisment | Concesionari | Adresă de e-mail cu format invalid | nu |
| CTR001 | eroare | Contracte | Contract fără concesionar și fără titular în text | nu |
| CTR002 | critică | Contracte | Contract cu concesionar_id inexistent | nu |
| CTR003 | avertisment | Contracte | Contract fără concesionar asociat (doar titular în text) | nu |
| CTR004 | avertisment | Contracte | Contract fără loc asociat | nu |
| CTR005 | critică | Contracte | Contract cu id_loc inexistent | nu |
| CTR006 | eroare | Contracte | cod_loc al contractului nu corespunde locului asociat (id_loc) | da |
| CTR007 | avertisment | Contracte | Număr de contract lipsă | nu |
| CTR008 | avertisment | Contracte | Număr de contract folosit de mai multe contracte | nu |
| CTR009 | eroare | Contracte | Contracte duplicate (aceleași număr, dată, loc și concesionar) | nu |
| CTR010 | eroare | Contracte | Perioadă incoerentă (sfârșit înaintea începutului) | nu |
| CTR011 | avertisment | Contracte | Perioade suprapuse pentru același loc, cu concesionari diferiți | nu |
| CTR012 | avertisment | Contracte | Titularul din contract diferă de concesionarul asociat (nume) | nu |
| CTR013 | eroare | Contracte | CNP-ul titularului din contract diferă de al concesionarului asociat | nu |
| CTR014 | informație | Contracte | Parcela/rândul/numărul din contract diferă de datele curente ale locului | nu |
| CTR015 | avertisment | Contracte | Câmp text peste limita de sincronizare | nu |
| INH001 | eroare | Înhumați | Înhumat fără loc asociat | nu |
| INH002 | critică | Înhumați | Înhumat cu id_loc inexistent | nu |
| INH003 | critică | Înhumați | Înhumat cu contract_id inexistent | nu |
| INH004 | eroare | Înhumați | Contractul înhumatului aparține altui loc | nu |
| INH005 | eroare | Înhumați | Înhumat fără nume și prenume | nu |
| INH006 | eroare | Înhumați | Date calendaristice incoerente (naștere/deces/înhumare/deshumare) | nu |
| INH007 | avertisment | Înhumați | Dată invalidă sau în viitor | nu |
| INH008 | eroare | Înhumați | Același CNP la mai mulți înhumați | nu |
| INH009 | avertisment | Înhumați | Posibil înhumat duplicat (același nume și dată a decesului) | nu |
| INH010 | avertisment | Înhumați | Număr de înhumați peste capacitatea locului | nu |
| INH011 | avertisment | Înhumați | CNP invalid ca structură/cifră de control | nu |
| INH012 | avertisment | Înhumați | Data nașterii sau sexul nu corespund CNP-ului | nu |
| INH013 | avertisment | Înhumați | Câmp text peste limita de sincronizare | nu |
| AVZ001 | critică | Avize | Aviz cu cod_loc inexistent sau lipsă | nu |
| AVZ002 | eroare | Avize | Aviz fără număr | nu |
| AVZ003 | avertisment | Avize | Același număr de aviz repetat pentru același loc | nu |
| AVZ004 | eroare | Avize | Data finalizării înaintea datei începerii | nu |
| AVZ005 | avertisment | Avize | Stare necunoscută (în afara planificata/in_lucru/finalizata) | nu |
| AVZ006 | avertisment | Avize | Stare incompatibilă cu datele disponibile | nu |
| AVZ007 | avertisment | Avize | Dată de aviz/început/finalizare invalidă | nu |
| AVZ008 | avertisment | Avize | Câmp text peste limita de sincronizare | nu |
| DOC001 | avertisment | Documente și fotografii | Tip de entitate necunoscut la document | nu |
| DOC002 | critică | Documente și fotografii | Document asociat unei entități inexistente | nu |
| DOC003 | eroare | Documente și fotografii | Document fără fișier și fără identificator media | nu |
| DOC004 | eroare | Documente și fotografii | Fișier referențiat lipsește de pe disc (fără copie în storage) | nu |
| DOC005 | informație | Documente și fotografii | Fișierul nu este pe disc, dar există copie în storage (media_id) | nu |
| DOC006 | informație | Documente și fotografii | Metadate incomplete la document | nu |
| DOC007 | avertisment | Documente și fotografii | Document duplicat (aceeași entitate și același sha256) | nu |
| DOC008 | avertisment | Documente și fotografii | Tipul documentului nu se potrivește cu entitatea asociată | nu |
| DOC009 | avertisment | Documente și fotografii | Dimensiunea fișierului diferă de cea înregistrată | nu |
| DOC010 | critică | Documente și fotografii | Fotografie asociată unui loc inexistent | nu |
| DOC011 | avertisment | Documente și fotografii | Mai multe fotografii marcate ca principală (cover) la același loc | da |
| DOC012 | avertisment | Documente și fotografii | Document de loc referit prin id numeric în loc de cod_loc | da |
| DOC013 | eroare | Documente și fotografii | Hash-ul fișierului diferă de sha256 înregistrat | nu |
| PLT001 | eroare | Plăți | Plată fără loc și fără contract | nu |
| PLT002 | critică | Plăți | Plată cu cod_loc inexistent | nu |
| PLT003 | critică | Plăți | Plată cu contract_id inexistent | nu |
| PLT004 | eroare | Plăți | Locul plății diferă de locul contractului asociat | nu |
| PLT005 | avertisment | Plăți | Sumă lipsă sau zero | nu |
| PLT006 | eroare | Plăți | Sumă nenumerică (NaN/infinit) sau de tip invalid | nu |
| PLT007 | informație | Plăți | Sumă negativă (posibilă corecție/stornare; modelul nu o definește explicit) | nu |
| PLT008 | avertisment | Plăți | Dată a plății lipsă, invalidă sau în viitor | nu |
| PLT009 | avertisment | Plăți | Posibile plăți duplicate (aceleași loc, sumă, dată, tip, chitanță) | nu |
| PLT010 | avertisment | Plăți | Același număr de chitanță la plăți diferite | nu |
| SYN001 | informație | Sincronizare | Înregistrare fără uid de sincronizare | da |
| SYN002 | avertisment | Sincronizare | uid duplicat în același tabel | nu |
| DAT002 | avertisment | Sincronizare | Dată invalidă (format nerecunoscut sau dată inexistentă) | nu |
| DAT001 | informație | Sincronizare | Dată în format ISO (aaaa-ll-zz) într-un câmp în care MAIN folosește zz.ll.aaaa | da |
| FIN000 | informație | Evidențe financiare | Soldul nu poate fi verificat: schema nu conține obligații de plată | nu |
| META001 | informație | Sincronizare | Verificare omisă (tabel/coloană lipsă în schema analizată) | nu |
| META002 | informație | Sincronizare | Număr maxim de probleme atins pentru o verificare (lista este trunchiată) | nu |

## Limitări cunoscute
- Analizează doar SQLite (MAIN). Verificarea schemei PostgreSQL din Cloud necesită acces la un mediu real și aprobare separată.
- `immutable=1` presupune că MAIN nu scrie în timpul rulării; lucrați pe o copie dacă aplicația este deschisă.
- Verificarea fișierelor folosește doar `os.path` (existență/dimensiune); `--deep` citește fișierele pentru SHA-256.
- Unele verificări sunt euristici (ex. `CTR011` perioade suprapuse, `INH010` capacitate numerică); sunt avertismente, nu verdicte.
- Nu este testat pe o bază reală de producție și nu a rulat pe Windows (doar Linux, date sintetice).
