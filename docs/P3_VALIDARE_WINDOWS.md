# P3 — Validare pe Windows (NEEXECUTATĂ în mediul de audit)

**Stare: toate verificările de mai jos sunt NEEXECUTATE pe Windows real.** Auditul a rulat pe Linux; un test Linux nu validează Windows.
Scriptul automat a fost rulat pe Linux doar ca autotest al scriptului (8 PASS, 1 NEEXECUTAT = sistemul de operare, 3 MANUAL).

## 1. Script automat (sintetic, folder temporar, fără rețea)
```powershell
cd <repository>
python deploy\windows_validation\validate_p3_windows.py --json p3_windows.json
```
Verifică: init_db pe cale cu spații/diacritice (W01), roluri user/operator (W02), instantaneul `owner_*` (W03), plată pe contract + PDF cu diacritice și markup invalid (W04), contract Word (W05), migrare dublă pe 4 scheme istorice sintetice (W06), `wait_for_tasks` la închidere (W07), comanda Task Scheduler (doar construire, W08).
Trimite `p3_windows.json` înapoi. Scriptul nu citește datele reale, nu creează task-uri și nu se conectează la Cloud.

## 2. Pași manuali (MANUAL)
| # | Pas | Așteptat |
|---|---|---|
| M1 | Pornire `Cimitir.exe` / MAIN pe o **copie** a folderului de date (`CIMITIR_DATA_DIR` către copie) | Pornește; fără mesaj de migrare dacă baza e la zi |
| M2 | Din panoul Admin web: cont `user` cu doar `add` bifat; autentificare în MAIN | Modul utilizator; „Adaugă…” funcționează; „Modifică/Șterge” (plată, contract, înhumat) dezactivate; la salvarea bifelor cu `delete` în plus, ștergerea devine posibilă fără repornire |
| M3 | Cont `operator` cu bifele implicite, apoi cu `delete` debifat | Implicit: tot permis; după debifare: ștergerea refuzată; utilizatori/config rămân refuzate |
| M4 | Hartă → click pe loc → fișă | Se deschide; fără excepții în log |
| M5 | Contract vechi → Modifică → Salvează | Câmpurile titularului sunt read-only și arată datele de la emitere; după salvare, documentul Word are titularul original |
| M6 | Plăți → loc cu 2 contracte | Nu se poate salva până nu alegi contractul (sau „fără contract”); tabelul arată contractul și concesionarul; filtrul „Doar plăți fără contract” arată plățile istorice neatinse |
| M7 | Dublu-click rapid pe „Adaugă plata” | O singură plată |
| M8 | „Chitanță PDF” pe o plată cu contract | PDF cu titularul din contract; diacritice corecte |
| M9 | Închide aplicația în timpul unei operațiuni QThread (ex. sincronizare/scanare) | Se închide fără blocaj și fără mesaj de eroare Qt |
| M10 | Task Scheduler: MAIN → Copii de rezervă → Programare → verifică în „Task Scheduler” `Cimitir - Copie de rezerva` → „Run” | Se creează fișier de backup; jurnalul arată `result: ok` |

## 3. Actualizare peste o instalare existentă (date sintetice)
1. Copiază folderul de date într-un loc de test și setează `CIMITIR_DATA_DIR` către copie (nu atinge `%LOCALAPPDATA%\Cimitir` real).
2. Pornește versiunea VECHE, creează 2–3 înregistrări fictive, închide.
3. Instalează/rulează versiunea nouă pe aceeași copie.
4. Așteptat: apare `pre-migrare\cimitir.db.<data>.bak` (doar fișiere complete, fără `.tmp`); datele sunt identice; a doua pornire nu mai creează copii.
5. Antivirus/OneDrive: dacă folderul de date e sincronizat OneDrive, notează dacă `os.replace` al copiei eșuează (aplicația refuză migrarea cu mesaj, nu continuă fără copie).

## 4. Diferențe Linux ↔ Windows relevante
- `chmod`/baze doar-citire: testul Linux de permisiuni este sărit pe Windows/root; pe Windows se simulează `PermissionError`.
- Oprirea procesului: `Popen.kill()` = `TerminateProcess` pe Windows (echivalent practic cu SIGKILL pentru testul de migrare întreruptă).
- `os.replace` peste un fișier deschis eșuează pe Windows: copia pre-migrare închide conexiunea înainte de redenumire.
- Căi cu spații/diacritice: acoperite de W01/W04/W05, dar trebuie rulate pe Windows.
