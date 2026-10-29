# Centru de configurare Cimitir Sync — ETAPA 1 + ETAPA 2

Ramură Git: `feature/cloud-config-center` (bază: `60f292c`). Nu s-a făcut merge în `main`. Nu s-a făcut deploy pe nicio infrastructură.

## 1. Ce a fost analizat și reutilizat

| Componentă existentă | Folosită pentru |
|---|---|
| `cloud_profiles.py`, `cloud_simple.py` | profilul Cloud (TEST/PROD separate, confirmare PROD, secrete doar în SecretStore) |
| `cloud_associate_window.py`, `field_devices_window.py`, `mobile_devices_window.py` | asocierea MAIN, coduri/QR pentru telefoane, dispozitive, conflicte — deschise din Centru, fără a fi rescrise |
| `backup_window.py`, `shared/cimitir_backup.py` | copii de rezervă locale; backup verificat automat înainte de instalarea schemei |
| `tools/setup_manager/` (`migrations`, `guard`, `redact`) | analiza migrărilor reale din PostgreSQL, planul, aplicarea într-o singură tranzacție cu rollback, protecțiile PROD |
| `shared/cimitir_cloud_admin_client.py` + `cimitir_cloud_admin.py` (`/api/v1/admin/bootstrap`) | inițializarea unică a administratorului Cloud (s-a adăugat doar metoda `bootstrap()` în client) |
| `access_session` (`manage_config`) | accesul doar pentru administratorul local |

Nu s-au creat sisteme paralele de conturi, autentificare sau configurare.

## 2. Ce s-a implementat

Meniu MAIN: buton **⚙ Centru Cloud** (doar utilizatori cu dreptul `manage_config`, verificat și la deschidere, și la fiecare acțiune din fereastră).

* **A. Stare generală** — licență (citită, nemodificată), baza locală (`PRAGMA quick_check` read-only), Supabase (ultima verificare), backend (health, la cerere), asociere MAIN, sincronizare Mobile.
* **B. Supabase** (ETAPA 1+2) — formular ghidat + completare din șirul de conexiune (parola e extrasă separat, șirul se șterge din câmp), verificare read-only (coerență ref/URL/utilizator, TLS, este proiect Supabase, migrări lipsă, bucket Storage privat), salvare în profil **fără parolă**, instalare/actualizare schemă: previzualizare → backup local MAIN verificat → backup bază Cloud (sau proiect gol documentat) → frază exactă de confirmare → o tranzacție cu rollback complet → creare bucket **privat** → reverificare.
* **C. Backend Cloud** — adresă/mediu (TEST/PROD cu confirmare), verificare conexiune, asociere MAIN (dialogul existent), text despre confidențialitate. Provisionarea automată este marcată **INDISPONIBILĂ**.
* **D. Administratori** — administratorul local rămâne cel din primul start; ghid de inițializare a administratorului Cloud (verifică starea, formular, confirmare, operațiune unică; câmpurile secrete se golesc imediat).
* **E. Backup** — copii locale existente; exportul/importul portabil și prima sincronizare sunt marcate **INDISPONIBILE (ETAPA 4)**.
* **F. Mobile** — acces la pairing cod/QR, telefoane de teren și Mobile v2 (aprobare/revocare/permisiuni există deja acolo).

### Autorizare temporară și secrete
* Parola PostgreSQL trăiește doar într-un `AdminSession` (memorie, TTL 15 min, ștearsă la „Șterge autorizarea” și la închiderea ferestrei). Nu se scrie în SQLite, SecretStore, loguri, rapoarte sau profil (testat).
* Tokenul de inițializare Cloud și parola administratorului se trimit o singură dată și nu se păstrează. **Limitare onestă:** Python nu poate șterge garantat șirurile din memorie; câmpurile Qt se golesc, referințele se elimină.
* `.lic`, Ed25519, Hardware ID, `license_manager.py`, autentificarea locală: **neatinse** (test care compară cu commitul de bază).

### Modificări în cod existent (minime, justificate)
* `tools/setup_manager/guard.py`: mediu nou `PARISH` (proiectul propriu al parohiei). Regula „bucket conține test” se aplică doar mediului TEST; refurile PROD/TEST ale dezvoltatorului rămân blocate la scriere; testele Setup Manager (138) trec neschimbat.
* `shared/cimitir_cloud_admin_client.py`: metoda `bootstrap()` (endpoint-ul exista deja pe server).
* `desktop/cloud_simple.py`: salvarea adresei backendului nu mai șterge referința Supabase când adresa era goală (altfel configurarea Supabase → Backend ar fi pierdut proiectul).
* `desktop/ui_mainwindow.py`: butonul „Centru Cloud”; `desktop/cimitir.spec`: modulele noi + `tools/setup_manager` + `pg_migrations` incluse în pachet.

## 3. Fișiere

Noi: `desktop/cloud_center.py`, `cloud_center_common.py`, `cloud_center_dialog.py`, `cloud_center_supabase_tab.py`, `cloud_supabase.py`, `desktop/tests/test_cloud_center.py`, `desktop/tests/test_cloud_supabase.py`, `docs/CENTRU_CONFIGURARE_CLOUD.md`, `docs/cloud_center_screens/*.png`.

## 4. Teste

* `desktop/tests/test_cloud_supabase.py` (21) — cu **PostgreSQL real local** (simulează un proiect Supabase: scheme `auth`/`storage`, `storage.buckets`) și migrările reale 001–026: proiect gol → 26 migrări lipsă, verificare strict read-only; proiect non-Supabase respins; ref/URL/utilizator incoerente, port 6543, parolă greșită (mesaj fără parolă), refuri protejate respinse; instalare refuzată fără backup local/backup Cloud/confirmare exactă; instalare completă + bucket privat + idempotență; rollback complet la migrare defectă; bucket public respins; parola absentă din loguri/profil/SQLite.
* `desktop/tests/test_cloud_center.py` (15) — stare, licență neschimbată, administrator Cloud unic (a doua inițializare refuzată înainte de cerere), UI Qt offscreen, blocare completă pentru non-admin, fluxul de instalare din UI (preview/backup/confirmare), marcaje INDISPONIBIL.
* Regresii: suita `desktop/tests` are **aceleași 19 eșecuri + 2 erori ca pe commitul de bază** (mediu: Windows/launcher/CI); `tools/setup_manager/tests`: 138 trec (inclusiv PG real).
* **Nu s-a testat** cu un proiect Supabase real (nu a existat) și nu s-a testat pe Windows/DPAPI; testele PostgreSQL rulează cu `SM_TEST_PG_URL=postgresql://postgres:pw@127.0.0.1:5432/postgres` (altfel sunt omise).

Capturi: `docs/cloud_center_screens/` (randate offscreen din aplicație, `CIMITIR_SCREENSHOT_DIR=… pytest -k screenshots`).

## 5. Instalare (Windows, simplu)
1. Aplică patch-ul: `git checkout -b feature/cloud-config-center && git am 0001-*.patch` (sau `git apply`).
2. `pip install -r desktop/requirements.txt` (necesită și `psycopg[binary]`, `httpx`, deja folosite de MAIN).
3. Pornește MAIN → autentificare administrator local → **⚙ Centru Cloud**.
4. Pentru testele: `cd desktop && python -m pytest tests/test_cloud_center.py tests/test_cloud_supabase.py`.
5. Rebuild exe: `pyinstaller desktop/cimitir.spec` (include automat Setup Manager și migrările).

Folosește un proiect Supabase de TEST nou; **Session pooler** (port 5432, utilizator `postgres.<ref>`). Nu rula pe PROD sau TEST-ul existent: refurile dezvoltatorului sunt blocate la scriere.

## 6. Analiza confidențialității (explicită)
* Parohia deține proiectul Supabase; dezvoltatorul nu primește credențiale administrative (parola bazei se folosește o dată, din memoria MAIN).
* **Risc:** operatorul backendului procesează datele în clar. Poate, tehnic, citi memoria procesului, variabilele de mediu ale instanței (credențialele runtime), jurnalele și traficul. Lipsa credențialelor administrative **nu** garantează confidențialitate absolută.
* Reducerea accesului (realist): instanță izolată per parohie; rol PostgreSQL runtime cu privilegii minime și RLS (există în migrările 008–013); secrete într-un secret manager per organizație, nu în imagine; fără SSH permanent, acces nominal, aprobat și jurnalizat; jurnale fără date personale; rotirea periodică a cheilor runtime; contract de prelucrare GDPR; parohia poate revoca rolul runtime din Supabase oricând (oprește instant backendul).
* Ar necesita criptare cu chei deținute exclusiv de parohie (neimplementat): criptare end-to-end a documentelor/fotografiilor și a câmpurilor sensibile în MAIN/Mobile; serverul ar stoca doar text cifrat — dar ar dispărea portalul public, căutarea și rapoartele pe server, iar pierderea cheii ar face datele irecuperabile.

## 7. Ce trebuie construit pentru ETAPA 3 (nu este simulat)
Serviciu de provisionare autentificat (API propriu al dezvoltatorului, cu aprobarea parohiei), orchestrator de instanțe izolate (containere/VM per parohie), DNS+TLS per instanță, secret manager per organizație (conexiune runtime, `SYNC`/JWT keys), generarea și livrarea tokenului de bootstrap (cu expirare) direct în instanță și către MAIN pe canal autorizat, monitorizare/backup al instanțelor, procedură de dezafectare. Până atunci, backendul se instalează cu Setup Manager/Docker și adresa se introduce în fila C.

## 8. Nu este în această livrare
ETAPA 3 (provisionare) și ETAPA 4 (export/import portabil, prima sincronizare controlată, teste MAIN↔Cloud↔Mobile complete) — marcate INDISPONIBIL în interfață.
