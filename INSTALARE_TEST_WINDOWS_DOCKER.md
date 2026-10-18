# Instalare și test pe laptop (Windows + Docker) — Supabase TEST `jqtdeuzkqtzfsdohvxmy`

Principii: **nu se șterge, nu se oprește și nu se modifică niciun container existent**; nu se atinge Supabase PROD sau Craiova TEST; parolele rămân doar în sesiunea PowerShell / fișiere în afara Git. Starea inițială: cod `D:\v3-main`, imagine `cimitir-cloud`, container `cimitir-cloud-etapa789-test` (port 8084).
Noua versiune rulează **alături**, într-un container nou de test pe **portul 8085** (cu mediul copiat din containerul 8084, fără să-l afișeze). Înlocuirea containerului 8084 este un pas OPȚIONAL (secțiunea 6), doar după decizia dumneavoastră.

## 1. Actualizarea proiectului din `D:\v3-main`
După ce ați urcat ramura pe GitHub (`etapa789-admin-provision-ui`, vezi README-ul livrării):
```powershell
cd D:\v3-main
git status --short                      # trebuie să fie curat; dacă nu, NU continua: salvează/arhivează modificările locale
git fetch origin
git checkout etapa789-admin-provision-ui
git log --oneline -3
```
Dacă nu ați urcat încă ramura: `git fetch D:\cale\branch.bundle etapa789-admin-provision-ui:etapa789-admin-provision-ui` urmat de `git checkout etapa789-admin-provision-ui`.
Dependențe MAIN (o singură dată): `py -m pip install -r desktop\requirements.txt "psycopg[binary]" segno`.

## 2. Verificarea și aplicarea migrărilor noi pe Supabase TEST
Doar proiectul TEST `jqtdeuzkqtzfsdohvxmy`. Scriptul refuză PROD și un `--project-ref` care nu coincide cu URL-ul.
```powershell
cd D:\v3-main
$env:CIMITIR_CP_ADMIN_DATABASE_URL = Read-Host "URL admin PostgreSQL TEST (postgres.jqtdeuzkqtzfsdohvxmy)"   # nu se salvează
py scripts\apply_cloud_provision_migration.py --project-ref jqtdeuzkqtzfsdohvxmy --check-only
```
Așteptat: `tabele_lipsa: []`, `023_aplicata: True`, `rls` activ, `randuri: {...}` (notați valorile). Pe Supabase TEST migrările **025 și 026 sunt deja aplicate** (de mine, la testele LIVE): vei vedea `025_aplicata: True`, `026_aplicata: True` și nu e nimic de aplicat. Dacă `025`/`026` apar `False` (alt proiect/stare), după raportul curat:
```powershell
py scripts\apply_cloud_provision_migration.py --project-ref jqtdeuzkqtzfsdohvxmy        # doar 025+026, o tranzacție; face ROLLBACK dacă numărul de rânduri se schimbă
```
**Nu rula 001–024 și nu rula `tests\test_etapa789_live_supabase_test.py` pe acest proiect** (face TRUNCATE). Rollback (doar dacă e nevoie): `... --rollback`.
La final: `Remove-Item Env:CIMITIR_CP_ADMIN_DATABASE_URL`.

## 3. Reconstruirea imaginii Docker (tag nou; imaginea existentă rămâne)
```powershell
cd D:\v3-main\deploy\windows
.\laptop_cloud_test.ps1 status                     # starea curentă; nu modifică nimic. Notează containerele și porturile
docker ps -a --filter "name=cimitir-cloud-sync-v2-canary"      # trebuie să NU existe (scriptul canary îl recreează); dacă există, oprește-te și întreabă
.\laptop_cloud_test.ps1 build -Tag etapa789-ui-1   # docker build -t cimitir-cloud:etapa789-ui-1 ; nu atinge containere
```

## 4. Container nou de test pe portul 8085 (cel de pe 8084 rămâne neatins)
```powershell
.\laptop_cloud_test.ps1 canary -Tag etapa789-ui-1 -Source cimitir-cloud-etapa789-test -CanaryPort 8085
```
Creează `cimitir-cloud-sync-v2-canary` pe `127.0.0.1:8085`, cu mediul copiat din containerul sursă (fișier temporar șters imediat). Așteptat: `healthz=True … sync-v2 (fără cheie => 401/403)=True`, apoi `Canary OK`. Oprire: `.\laptop_cloud_test.ps1 canary-stop` (șterge doar canary-ul).
Verificări suplimentare:
```powershell
curl http://127.0.0.1:8085/api/v1/health                       # database up, storage configured
curl http://127.0.0.1:8085/api/v1/admin/status                 # ok
docker logs --tail 30 cimitir-cloud-sync-v2-canary             # fără "migrările 025/026 nu sunt aplicate"
```
Browser: `http://127.0.0.1:8085/admin/` → login administrator Cloud TEST → cardul „Cereri de înregistrare de la MAIN” apare fără eroare 501.
(Canary ascultă doar pe 127.0.0.1. Pentru telefon e nevoie de HTTPS public — vezi `TEST_TELEFON_ANDROID_IPHONE.md`.)

## 5. Testarea asocierii MAIN și Mobile din interfață
Detaliile și tabelele PASS/FAIL: `TEST_LAPTOP_WINDOWS_DOCKER.md` (secțiunile 3–5) și `TEST_TELEFON_ANDROID_IPHONE.md`. Rezumat:
1. MAIN cu director de date curat: `set CIMITIR_DATA_DIR=D:\cimitir-test-nou` → `py D:\v3-main\desktop\main.py`.
2. Creezi organizația (ex. `ParohiaTest2`) și cimitirul (`test2`); **Configurare inițială** → administrator local + parolă (≥12). `admin/admin123` nu trebuie să funcționeze.
3. Autentificat local → 🔗 **Asociază cu Cloud** → adresa `http://127.0.0.1:8085`.
4. **Varianta A:** bifează confirmarea → *Trimite cererea de înregistrare* → în `http://127.0.0.1:8085/admin/` apare cererea (organizație NOUĂ/cimitir NOU) → **Confirmă** → MAIN afișează singur „Sincronizarea este activă”.
   **Varianta B:** administrator Cloud în MAIN → *Autentifică-te și verifică registrul* → bifează → *Provizionează și asociază*.
5. Creezi un loc de veci → *Sincronizează* → verifici în PostgreSQL (citire): `py scripts\test_data_inventory.py show --project-ref jqtdeuzkqtzfsdohvxmy --cemetery <UID_CIMITIR>` (cu `CIMITIR_CP_ADMIN_DATABASE_URL` setat) ⇒ `locuri_de_veci: 1`.
6. Pe telefon asociat (QR): locul apare; documente MAIN→telefon; fotografie telefon→MAIN; offline/reluare; revocare — conform procedurii pentru telefon.
7. Verificare automată opțională (scrie doar un registru fictiv): 
   ```powershell
   $env:CLOUD_URL="http://127.0.0.1:8085"; $env:CIMITIR_LIVE_ADMIN_EMAIL="<administrator Cloud TEST>"; $env:CIMITIR_LIVE_ADMIN_PASSWORD="<parola>"
   py scripts\verify_provision_live.py --yes-write-test-data      # ≈2–3 min; așteptat PASS=9 FAIL=0
   ```

## 6. OPȚIONAL — înlocuirea containerului de pe 8084 (doar la decizia dumneavoastră, după pașii 1–5 reușiți)
Scriptul existent `switch` **nu** șterge versiunea veche: o redenumește `…-prev`, pornește noua imagine cu aceleași porturi/mount-uri/mediu și revine automat dacă noua versiune nu e sănătoasă:
```powershell
.\laptop_cloud_test.ps1 switch   -Tag etapa789-ui-1 -Target cimitir-cloud-etapa789-test
.\laptop_cloud_test.ps1 rollback -Target cimitir-cloud-etapa789-test      # revenire oricând
```
Nu rulați acest pas fără să mi-l confirmați; până atunci, 8084 rămâne exact cum este.

## 7. Date de test: previzualizare/arhivare (opțional, doar cu confirmare explicită)
```powershell
py scripts\test_data_inventory.py inventory --project-ref jqtdeuzkqtzfsdohvxmy
py scripts\test_data_inventory.py archive   --project-ref jqtdeuzkqtzfsdohvxmy --cemetery <UID>          # DOAR previzualizare
# apoi, după ce ai citit lista: ... --apply --confirm ARHIVEAZA-xxxxxxxxxx [--revoke-bindings]
```
Doar `LIVE-VERIFY-*` și organizațiile `LIVE-PROVISION-*`; orice altceva e „PROTEJAT”. Nu se șterge nimic.

## Depanare
- `501 SCHEMA_OUTDATED` în /admin/ ⇒ migrările 025/026 nu sunt aplicate (pasul 2).
- `ReadTimeout` ⇒ latență mare: `set CIMITIR_CLOUD_HTTP_TIMEOUT=60` (MAIN) și reîncearcă.
- Cererea nu apare în /admin/ ⇒ contul nu e administratorul inițial (organizație nouă) sau Owner în organizația existentă.
- Canary NEREUSIT ⇒ scriptul afișează ultimele loguri cu parolele mascate; containerele existente nu au fost atinse.
