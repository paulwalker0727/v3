# Cimitir Sync v2 — Setup Manager (TEST)

Instrument local pentru Windows care înlocuiește comenzile manuale PowerShell/Docker/SQL pentru pregătirea mediului **TEST** al Cloud-ului Cimitir Sync v2.
Rulează doar pe `127.0.0.1`, **pornește în modul Dry Run / Check Only** și nu scrie nimic fără confirmare explicită.

## Pornire (o singură comandă)

```powershell
cd D:\v3-main-actualizat
.\setup-test.ps1                # deschide http://127.0.0.1:8765/
.\setup-test.ps1 -Port 8770     # alt port pentru interfață (nu 8084/8085)
.\setup-test.ps1 -SelfTest      # rulează testele automate
```

La prima rulare creează un mediu Python izolat în `%LOCALAPPDATA%\CimitirSetupManager\venv` (fastapi, uvicorn, psycopg). Necesită Python 3.10+ și (pentru secțiunea Docker) Docker Desktop.
Scriptul nu primește parole ca parametri, nu rulează `docker`, `git` sau SQL.

## Ce face (corespondență cu cerințele)

| Secțiune UI | Funcționalitate |
|---|---|
| **1 Configurare** | Project Ref, Supabase URL, Connection String, Username, Password, Secret Key, Bucket, Cloud Public URL, container, port, tag imagine, MAIN/Mobile. Parolele/cheile sunt mascate (butonul 👁 doar le arată în câmp), nu sunt returnate niciodată de server și rămân în memorie (opțional, Windows DPAPI, doar pentru utilizatorul curent). Parola din connection string este mutată automat în câmpul secret. |
| **2 Verificări** | Butonul **„Verifică toate conexiunile”**: 1) DNS+TCP, 2) autentificare PostgreSQL, 3) identificare proiect Supabase TEST (ref ↔ URL ↔ utilizator ↔ pooler ↔ cheie), 4) permisiuni PostgreSQL, 5) Storage + bucket privat, 6) tabele/funcții/RLS/politici, 7) migrări existente (023–026 evidențiate), 8) fișierele Mobile din imaginea Docker, 9) `/healthz` și `/api/v1/health`. Rezultat PASS/WARN/FAIL/SKIP + explicație concretă, fără date sensibile. Bucketul lipsă poate fi creat PRIVAT (mod scriere + confirmare). |
| **3 Migrări** | Starea fiecărei migrări = `schema_migrations` **și** obiectele reale (tabele, coloane, funcții, politici, RLS, indexuri, trigger-e) extrase din fișierele SQL. Stări: APLICATĂ, APLICATĂ_NEÎNREGISTRATĂ, LIPSĂ, PARȚIALĂ, INCONSISTENTĂ. Plan ordonat cu dependențe; verificare backup; aplicare într-o **singură tranzacție**. |
| **4 Docker TEST** | Detectare containere/porturi, build imagine (tag nou), pornire container nou pe `127.0.0.1:8085`, actualizare doar a containerului TEST selectat (cu revenire automată), loguri mascate. |
| **5 Asociere MAIN·Cloud·Mobile** | Sonde automate (health, admin, sync-v2 protejat, `/mobile/`, toate fișierele `/mobile-assets/*`, manifest, service worker), teste izolate prin scripturile existente (`verify_provision_live.py`, `verify_cloud_live.py`) cu aprobare explicită, listă de pași manuali (MAIN, telefon, conflicte, offline) cu stare PASS/FAIL salvată local. |
| **6 Raport** | Raport Markdown fără secrete, salvat în `%LOCALAPPDATA%\CimitirSetupManager\reports`. |

## Reguli de securitate implementate în cod

* **PROD blocat**: ref-ul `txuxkjgftopznvmooexq` este refuzat în *orice* câmp de configurare, în mediul copiat din container și în URL-urile de test (`guard.py`; același ref ca în `cimitir_test_guard.py`). Nu există opțiune de ocolire; mediul este fix `TEST`.
* **Proiectul TEST anterior `nhhczdmcocbobptogvsw` rămâne intact**: nicio scriere/migrare/creare bucket pe el.
* **Port 8084 și containerul vechi**: portul 8084 este refuzat; containerele vechi (`cimitir-cloud-etapa789-test` etc.) nu pot fi ținta niciunei operații. Mediul se poate *citi* dintr-un container sursă doar cu `docker inspect`.
* **Docker**: listă albă de subcomenzi; `compose`, `down`, `system`, `prune`, `volume`, `network`, `rmi`, `kill`, `-v`, `--privileged` sunt respinse înainte de a lansa vreun proces. `docker rm` este permis numai pentru containerul creat în aceeași operație (la eșec de actualizare). Containerul vechi se păstrează ca `<nume>-prev` (nu se șterge).
* **Dry Run implicit**: modul scriere se activează cu fraza `ACTIVEAZA SCRIEREA TEST`, expiră în 15 min și se dezarmează după prima operație de scriere. În plus, fiecare operație cere propria frază (migrări: `APLIC 024-025-026`, Docker: `CONSTRUIESC <tag>` / `PORNESC <container>` / `ACTUALIZEZ <container>`, bucket: `CREEAZA BUCKET <nume>`, teste izolate: `APROB DATE TEST IZOLATE`).
* **Secrete**: mascare centralizată (`redact.py`) pe toate răspunsurile, logurile Docker, rapoartele și mesajele de eroare; fișierul de mediu temporar al containerului are permisiuni restrânse și este șters imediat; `config.json` nu conține secrete. Interfața nu încarcă nimic de pe Internet; singurele apeluri externe sunt către Supabase/PostgreSQL-ul configurat și către `127.0.0.1`.
* **Acces local**: server legat doar pe `127.0.0.1`, token de sesiune, verificare `Host`/`Origin`, CSP strict.
* **Privilegii minime**: avertisment dacă folosești rol superuser sau dacă Cloud-ul ar rula cu credențiale administrative (recomandat: „Runtime DB URL” cu rolul `cimitir_cp_app`).

## Fluxul recomandat pe laptopul TEST

1. `.\setup-test.ps1` → **Configurare**: completează *Password*, *Secret Key* (și opțional Runtime DB URL, calea backupului). Salvează.
2. **Verificări** → *Verifică toate conexiunile*. Citește FAIL/WARN. (Pe starea descrisă: 023 = APLICATĂ; 024 = LIPSĂ; 025–026 = LIPSĂ.)
3. **Migrări** → *Generează planul*. Dacă obiectele unei migrări există dar istoricul lipsește, planul propune doar `ÎNREGISTREAZĂ ÎN ISTORIC`; dacă o migrare e înregistrată dar îi lipsesc obiecte (INCONSISTENTĂ) sau e parțială, planul este **blocat** și explică motivul — nu se presupune succesul.
   * Introdu calea backupului → *Verifică backup* (lizibil: ZIP/TAR/GZ/SQL/pg_dump).
   * *Activează scrierea* (antet) → tastează fraza planului → *Aplică planul*. Totul rulează într-o tranzacție: la orice eroare/verificare eșuată se face ROLLBACK complet (inclusiv istoricul). După fiecare migrare se verifică obiectele; la final, niciun tabel nu are mai puține rânduri (nu se șterg înregistrări), iar `schema_migrations` are exact un rând per versiune.
4. **Docker TEST**: *Detectează* → *Previzualizează build* → *Activează scrierea* → *Construiește*; apoi *Previzualizează pornire* → *Pornește* (pe `127.0.0.1:8085`). Dacă containerul există deja: *Actualizează* (versiunea veche rămâne `-prev`, oprită; revenire automată dacă noul nu devine sănătos în 45 s).
   * Mediul containerului = câmpurile din Configurare + (opțional) mediul copiat din „Container sursă” (valorile din Configurare au prioritate; `PORT` e mereu 8080 intern).
5. **Verificări** din nou: toate cele 9 trebuie PASS (7 poate rămâne WARN dacă mai sunt migrări de înregistrat).
6. **Asociere**: sondele automate → testele izolate (opțional, cu aprobare) → pașii manuali din MAIN și telefon → **Raport**.

## Arhitectură

```
tools/setup_manager/
  server.py     FastAPI (loopback, token, moduri), endpointuri /api/*
  guard.py      PROD/TEST, port/container protejate, coerență ref↔URL↔user
  config.py     stocare config (secrete doar în memorie / DPAPI)
  redact.py     mascare secrete
  checks.py     cele 9 verificări (dependențe injectabile pentru teste)
  migrations.py extragere obiecte din SQL, analiză, plan, backup, aplicare tranzacțională
  dockermgr.py  build/start/update controlat, listă albă de comenzi
  pairing.py    sonde, teste izolate (scripturile existente), checklist manual
  report.py     raport fără secrete
  static/       UI (HTML/CSS/JS, fără resurse externe)
  tests/        135+ teste (PostgreSQL real local pentru migrări)
setup-test.ps1  launcher
```

Reutilizează: directorul `backend/cimitir/pg_migrations` (nu își duplică SQL-ul), cheia advisory-lock a aplicației (`7346102`, serializare cu `postgres_store.migrate`), regulile de preflight ale Cloud-ului (test de „drift” față de `cimitir_cloud_app.preflight`), refurile protejate din `cimitir_test_guard.py`, scripturile `verify_provision_live.py` / `verify_cloud_live.py` / `test_data_inventory.py`. **Nu modifică** autentificarea, sincronizarea, MAIN, Cloud sau Mobile.

## Modificare în afara `tools/setup_manager`: `Dockerfile` (justificată)

`backend/static/mobile_app.js` importă `./mobile_photos.js` și încarcă `/mobile-assets/jsQR.js`, iar Cloud-ul le servește (`_MOBILE_STATIC`), dar `Dockerfile` **nu le copia** în imagine ⇒ în container aplicația Mobile primește 404 la module și nu pornește. Verificarea nr. 8 și sondele de asociere detectează exact această lipsă; patch-ul adaugă cele două fișiere în `COPY`. Nicio altă linie nu se schimbă. Reconstruiește imaginea după integrare.

## Teste

```powershell
.\setup-test.ps1 -SelfTest                                         # fără PostgreSQL: testele de migrare cu PG real sunt omise (skip)
.\setup-test.ps1 -SelfTest -PgUrl "postgresql://postgres:<parola>@127.0.0.1:5432/postgres"   # PostgreSQL LOCAL descartabil (creează/șterge doar baze sm_*)
```
Nu rula testele pe Supabase (launcherul refuză). Pentru un PostgreSQL local rapid: `docker run -d --name pg-sm-test -e POSTGRES_PASSWORD=<parola> -p 127.0.0.1:55432:5432 postgres:15` (container separat, nu cel TEST), apoi `-PgUrl postgresql://postgres:<parola>@127.0.0.1:55432/postgres`.

## Limite cunoscute

* Detectarea obiectelor este statică (regex pe SQL): obiectele create în blocuri `DO`/SQL dinamic nu sunt verificate; migrările formate doar din astfel de blocuri (007, 009, 011, 012, 017, 020) sunt „neverificabile” și se deduc aplicate numai dacă o migrare ulterioară este verificată. Pe o bază goală sunt aplicate normal în ordine.
* Testele izolate MAIN↔Cloud↔Mobile care cer MAIN desktop (Qt) sau telefon se execută manual conform listei din secțiunea 5; Setup Manager doar le ghidează și înregistrează rezultatul.
* Salvarea criptată a secretelor (DPAPI) funcționează doar pe Windows.
