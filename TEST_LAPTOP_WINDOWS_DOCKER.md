# Test complet pe laptop (Windows + Docker) — Supabase TEST `jqtdeuzkqtzfsdohvxmy`

Ramură: `etapa789-admin-provision-ui` (conține și `etapa789-asociere-finala`). **Nu folosi Craiova TEST sau PROD.** Parolele rămân doar în variabile de mediu/`.env` local, niciodată în Git.
Coloana „Rezultat” rămâne GOALĂ până rulezi pasul; un pas neexecutat = NETESTAT, nu PASS.

## 0. Pregătire (o singură dată)
```powershell
cd D:\
git clone https://github.com/paulwalker0727/v3 v3-test ; cd v3-test
git fetch /cale/spre/branch.bundle etapa789-admin-provision-ui:etapa789-admin-provision-ui     # sau: git checkout -b etapa789-admin-provision-ui ; git am etapa789-admin-provision-ui-cumulat.patch
git checkout etapa789-admin-provision-ui
```
Python 3.11+ în PATH, Docker Desktop pornit, pachete: `pip install -r desktop\requirements.txt psycopg[binary] segno`.

## 1. Migrări pe Supabase TEST (o singură dată; NU reaplica 001–024)
```powershell
$env:CIMITIR_CP_ADMIN_DATABASE_URL = "postgresql://postgres.jqtdeuzkqtzfsdohvxmy:<PAROLA>@aws-0-eu-west-1.pooler.supabase.com:5432/postgres?sslmode=require"
python scripts\apply_cloud_provision_migration.py --project-ref jqtdeuzkqtzfsdohvxmy --check-only     # raport: trebuie 023_aplicata: True și tabele_lipsa: []
python scripts\apply_cloud_provision_migration.py --project-ref jqtdeuzkqtzfsdohvxmy                  # aplică DOAR 025 + 026 (aditive); refuză dacă nr. de rânduri se schimbă
```
Rollback (doar dacă e nevoie): `... --rollback`.

## 2. Cloud în Docker (port 8084)
Fișier `D:\v3-test\.env.cloud-test` (NU în Git):
```
CIMITIR_CP_DATABASE_URL=postgresql://cimitir_cp_app.jqtdeuzkqtzfsdohvxmy:<PAROLA_RUNTIME>@aws-0-eu-west-1.pooler.supabase.com:5432/postgres?sslmode=require
CIMITIR_CLOUD_PROJECT_REF=jqtdeuzkqtzfsdohvxmy
SUPABASE_URL=https://jqtdeuzkqtzfsdohvxmy.supabase.co
SUPABASE_SECRET_KEY=<service_role / secret key>
SUPABASE_BUCKET=cimitir-private-test
CIMITIR_PUBLIC_URL=http://192.168.x.x:8084
```
(`192.168.x.x` = IP-ul laptopului în Wi-Fi, ca telefonul să ajungă la el; pentru HTTPS/cameră pe telefon vezi `TEST_TELEFON_ANDROID_IPHONE.md`.)
```powershell
docker build -f deploy\cloud\Dockerfile -t cimitir-cloud:admin-provision-ui .
docker rm -f cimitir-cloud-etapa789-test 2>$null
docker run -d --name cimitir-cloud-etapa789-test -p 8084:8080 --env-file .env.cloud-test cimitir-cloud:admin-provision-ui
curl http://localhost:8084/api/v1/health          # așteptat: ok, database UP, storage configured
```
| # | Verificare | Rezultat |
|---|---|---|
| 2.1 | `/api/v1/health` = 200, `database: up`, `storage: configured` | |
| 2.2 | `http://localhost:8084/admin/` → login cu administratorul Cloud TEST | |
| 2.3 | Cardul „Cereri de înregistrare de la MAIN” apare (gol, fără eroare 501) | |

## 3. MAIN (Windows) — registru NOU de la zero
1. Pornește aplicația cu un director de date curat: `set CIMITIR_DATA_DIR=D:\cimitir-test-nou` apoi `python desktop\main.py`.
2. Creează organizația (ex. `ParohiaTest2`) și cimitirul (ex. `test2`). **Notează numele, nu UID-urile.**
3. **Configurare inițială** → alege administrator local + parolă (≥12 caractere). `admin/admin123` NU trebuie să funcționeze.
4. Autentifică-te local → 🔗 **Asociază cu Cloud** → adresa `http://localhost:8084`.

| # | Verificare | Rezultat |
|---|---|---|
| 3.1 | Asistentul de configurare inițială apare; conturile implicite nu există | |
| 3.2 | „Asociază cu Cloud” se deschide doar autentificat cu `manage_config` | |

### Varianta A — cerere confirmată în /admin/ (recomandată, fără parolă Cloud pe MAIN)
5. În MAIN: bifează confirmarea → **Trimite cererea de înregistrare**. Apare „Cerere trimisă (ID XXXXXXXX)”.
6. În `/admin/` (browser): cardul „Cereri…” arată cererea (organizație NOUĂ + cimitir NOU, ID XXXXXXXX) → **Confirmă**.
7. MAIN continuă singur (≈4 s): cod MAIN emis automat → plan → asociere → verificare → „Sincronizarea este activă”.

| # | Verificare | Rezultat |
|---|---|---|
| 3.A1 | Cererea apare în /admin/ cu UID-urile originale (primele 8 caractere) și efectul „NOUĂ/NOUĂ” | |
| 3.A2 | După Confirmă, MAIN afișează „Sincronizarea este activă” fără intervenție | |
| 3.A3 | O a doua cerere pentru același cimitir cât prima e în așteptare → respinsă (409) | |

### Varianta B — cu parola administratorului Cloud în MAIN
„Autentifică-te în Cloud și verifică registrul” → rezumat → bifează → „Provizionează (dacă e nevoie) și asociază automat”. Rezultatul așteptat e identic.

## 4. Verificări după asociere
```powershell
python scripts\test_data_inventory.py show --project-ref jqtdeuzkqtzfsdohvxmy --cemetery <UID_CIMITIR_din_MAIN>
```
(UID-ul îl vezi în fereastra de asociere / `Registru → Detalii`.)

| # | Verificare | Rezultat |
|---|---|---|
| 4.1 | Registrul local: stare BOUND; cheia în Windows Credential/DPAPI (nu în fișiere) | |
| 4.2 | `show`: cimitirul există în PostgreSQL, `legaturi_active: 1` | |
| 4.3 | Creezi în MAIN un loc de veci → așteaptă 1–2 min / „Sincronizează acum” → `show` arată `locuri_de_veci: 1` | |
| 4.4 | Un document atașat în MAIN apare în `sync_v2_documents` (`show`) și, pe telefon asociat, în lista documentelor | |
| 4.5 | O fotografie încărcată de pe telefon ajunge în MAIN (fișier + metadate asociate corect) | |
| 4.6 | Dublă încărcare a aceleiași fotografii (reluare după întrerupere) NU creează duplicat | |
| 4.7 | Revoci dispozitivul în /admin/ → MAIN/telefonul nu mai sincronizează (401) | |
| 4.8 | Datele unui alt cimitir/organizație NU apar pe telefon | |

## 5. Verificare automată LIVE (aceleași operații, registru fictiv)
```powershell
$env:CLOUD_URL="http://localhost:8084"; $env:CIMITIR_LIVE_ADMIN_EMAIL="admin.test@example.invalid"; $env:CIMITIR_LIVE_ADMIN_PASSWORD="<parola>"
python scripts\verify_provision_live.py --yes-write-test-data      # ≈2–3 min pe Supabase; așteptat: PASS=9 FAIL=0
```

## 6. Curățarea datelor TEST (doar previzualizare implicit)
```powershell
python scripts\test_data_inventory.py inventory --project-ref jqtdeuzkqtzfsdohvxmy
python scripts\test_data_inventory.py archive --project-ref jqtdeuzkqtzfsdohvxmy --cemetery <UID> [--cemetery <UID2>]       # doar previzualizare, nu scrie
python scripts\test_data_inventory.py archive ... --apply --confirm ARHIVEAZA-xxxxxxxxxx [--revoke-bindings]                # după ce ai citit previzualizarea
```
Se pot arhiva doar cimitire `LIVE-VERIFY-*` și cimitirele din organizații `LIVE-PROVISION-*`; orice alt registru e „PROTEJAT”. Nu se șterge nimic; arhivarea e reversibilă (`UPDATE cemeteries SET status='DRAFT' ...`).

## Depanare rapidă
- `501 SCHEMA_OUTDATED` în /admin/ ⇒ nu ai aplicat pasul 1 (025/026).
- „Serverul Cloud nu răspunde (ReadTimeout)” ⇒ latență mare; `set CIMITIR_CLOUD_HTTP_TIMEOUT=60`.
- Cererea nu apare în /admin/ ⇒ contul nu e administratorul instalării (organizație nouă) sau Owner în organizația existentă.
