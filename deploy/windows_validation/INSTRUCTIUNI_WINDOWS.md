# Instrucțiuni Windows — Centru Cloud (ETAPA 1+2) în COPIE TEST

**Instalarea funcțională `D:\v3-main` NU se modifică.** Tot ce urmează se face într-o copie nouă, cu date și licență proprii (copiate, nu partajate).
Nu se atinge containerul de pe portul **8084**, nici backendul TEST de pe **8085** (se fac doar cereri GET de citire), nici PROD. Nu se execută migrări.

## 0. Ce ai nevoie
* Același Python ca pentru MAIN (cu PySide6). În plus: `pip install "psycopg[binary]" httpx` (dacă lipsesc; MAIN le folosește deja pentru Cloud).
* Parola bazei de date Supabase TEST (cea din Supabase → *Connect*). **Nu o reseta** ca s-o obții: backendul 8085 ar putea depinde de ea. Nu o trimite în conversație: scriptul o cere ascuns, local.
* Gazda **Session pooler** (nu e secretă): Supabase → *Connect* → *Session pooler*, ex. `aws-0-<regiune>.pooler.supabase.com`, port 5432, utilizator `postgres.vcpmcdardnumvlpxknlj`.

## 1. Descarcă și verifică ZIP-ul
Dezarhivează în **afara** lui `D:\v3-main`, de ex. `D:\cimitir-cc-pack\`. Verifică integritatea:
`certutil -hashfile cimitir-cloud-center-etapa12.zip SHA256` — trebuie să fie identic cu fișierul `.sha256`.

## 2. Creează copia TEST (nu modifică sursa)
1. Închide MAIN dacă rulează (copia datelor SQLite e mai sigură cu aplicația oprită).
2. Dublu-click `INSTALL_TEST_COPY.bat` (sau: `python install_test_copy.py --source D:\v3-main --target D:\v3-main-cloudcenter-test`).
   Dacă datele tale nu sunt în `D:\v3-main\shared_data`, adaugă `--live-data "calea\datelor"`.
3. Scriptul:
   * verifică **exact versiunea**: fișierele pe care le modifică patch-ul trebuie să fie identice cu cele din commitul `60f292c` (acceptă și CRLF). Dacă ceva diferă, **se oprește înainte să creeze orice** și îți listează fișierele; nu forța fără să ne uităm împreună (`--allow-drift` omite fișierele diferite și produce o copie posibil inconsistentă, raportată FAIL — doar pentru diagnostic);
   * copiază proiectul (fără `.git`, `.env`, date) în folderul nou și suprapune fișierele patch-ului, verificându-le prin hash;
   * copiază datele în `test_env\data` și licența în `test_env\appdata\CimitirDesktop\license.key` (hash comparat);
   * **neutralizează în copie** (nu în original) setările moștenite din baza copiată care ar ieși din izolare: `backup_dir` → `test_env\backups`, sincronizările automate (`cloud_sync_auto`, entități, media, publicare) oprite, profilul Cloud activ dacă este **PROD** sau pe portul **8084** dezactivat (linia `SAN-01`). Profilul/asocierea TEST (ex. 8085) rămân, ca să poți vedea starea reală;
   * compară amprenta instalării sursă înainte/după: trebuie **identică**;
   * scrie `test_env\reports\install_report.json`.
   Rezultat așteptat: toate liniile `[PASS]`, apoi `REZULTAT INSTALARE: PASS`.

**Limită cunoscută:** copia primește și asocierea MAIN (cheia de sincronizare din `test_env\data\secrets`, legătura registrului). Sincronizarea automată e oprită, dar o sincronizare MANUALĂ din copie ar folosi aceeași asociere ca instalarea reală. Nu apăsa „Sincronizează” în copie dacă asocierea reală nu este cea de TEST (8085).

## 3. Validare reală (Supabase TEST + backend 8085)
`D:\v3-main-cloudcenter-test\RUN_VALIDATION.bat` — cere gazda pooler și parola (ascunsă). Scrie raportul în `test_env\reports\validation_*.md`.
Verifică: izolarea (inclusiv `ENV-02`: backup-ul copiei merge în test_env; `ENV-03`: sincronizări automate oprite, niciun profil activ PROD/8084), licența (validă + hash identic), baza locală, `manage_config` (admin permis / non-admin refuzat), cele 6 file, persistența configurației într-un proces nou,
conexiunea Supabase doar-citire cu TLS, detectarea celor **26** migrări aplicate (0 lipsă), bucket `cimitir-private-test` privat, că verificarea nu a modificat baza,
respingerea proiectului greșit (fără rețea). Respingerea parolei greșite (`SB-08`) este **opțională** (`--test-wrong-password`): face o autentificare eșuată reală la pooler, iar repetarea ei poate declanșa limitarea Supabase, backendul 8085 (`/health`, starea inițializării Cloud), absența parolei din fișiere.
Linii `MANUAL` = pași care cer o acțiune/aprobare a ta (vezi §5).

## 4. Deschide MAIN-ul copiei și vezi Centrul Cloud
`RUN_MAIN_TEST.bat` → autentifică-te ca administrator local → butonul **⚙ Centru Cloud** în bara de sus.
* A: stare (licență, bază locală, Supabase, backend, asociere, sincronizare).
* B: după RUN_VALIDATION, câmpurile sunt precompletate (ref, gazdă, bucket `cimitir-private-test`). Introdu parola → **„1. Verifică proiectul”**. Așteptat: `toate cele 26 migrări sunt aplicate` și bucket privat. **NU apăsa „3. Instalează”** (nu e nevoie; ar refuza oricum: nu există nimic de aplicat).
* C: „Verifică conexiunea” pe `http://127.0.0.1:8085`.
* D–F: starea inițializării Cloud, backup local, acces la pairing/telefoane.

## 5. Ce funcționează din MAIN și ce rămâne manual (la momentul acestei livrări)
| Operație | Din MAIN | Manual / condiție |
|---|---|---|
| Verificare proiect Supabase TEST, schema 26/26, bucket privat | **Da** (fila B) | parola DB introdusă de tine |
| Instalare/actualizare schemă | Da, dar **nu e necesară** acum | backup + frază de confirmare |
| Salvare profil Cloud (TEST/PROD) | Da (fila B/C) | PROD cere confirmare |
| Verificare backend 8085 | Da (fila A/C) | — |
| Asociere MAIN ↔ backend | Da (dialogul existent) | cont administrator Cloud (email+parolă) |
| Creare prim administrator Cloud | Da (fila D) **doar dacă** `/admin/status` arată „inițializare deschisă” | serverul 8085 trebuie să aibă `BOOTSTRAP_TOKEN` (≥32 car.) și `BOOTSTRAP_TOKEN_EXPIRES_AT`; setarea lor = modificare a mediului TEST → **doar cu aprobarea ta** |
| Livrare automată a tokenului de bootstrap | **Nu** (ETAPA 3, indisponibil) | — |
| Pairing telefoane (cod/QR) | Da, după asociere | — |
| Provisionare backend, export/import portabil, prima sincronizare | **Nu** (ETAPA 3/4) | — |

Dacă `RUN_VALIDATION` arată că administratorul Cloud există deja, fila D va refuza (corect) o a doua inițializare.

## 6. Curățare / revenire
Șterge folderul `D:\v3-main-cloudcenter-test`. Instalarea ta funcțională, datele și licența reale nu au fost modificate.

## 7. Despre integrarea în instalarea funcțională (mai târziu, doar cu aprobarea ta)
După validare, patch-ul din `patch\` se aplică pe o ramură Git: `git checkout -b feature/cloud-config-center && git am patch\*.patch`. Fără merge în `main`.
