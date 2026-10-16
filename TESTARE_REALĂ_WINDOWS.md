# TESTARE REALĂ pe Windows — PC MAIN + laptop Cloud TEST

Ramura de lucru: `sync-v2-emergent-wip` · Commit de bază al patch-ului: `12a3c7ba83f680d10a4da457643d73cd5c484ddb` (`12a3c7b`, `mobile-v2`).
**Nu se atinge `main`, nu se atinge PROD. Migrarea pe Supabase TEST se rulează DOAR după aprobarea ta explicită (Pasul L4).**
Comenzile sunt PowerShell. Tot ce urmează este **neverificat pe Windows real** (eu am testat pe Linux + PostgreSQL local; vezi `STATUS_IMPLEMENTARE.md`) — de aceea fiecare pas are o verificare și un rollback.

## 0. Ce primești în ZIP
`sync_v2.patch` (de la `12a3c7b`), `sync_v2_incremental.patch` (de la `9457db4`, dacă ai aplicat deja patch-ul vechi), `INSTALL_PATCH.ps1`, `install_patch.py`, `laptop_cloud_test.ps1`, `SHA256SUMS.txt`, aceste documente.
Verifică integritatea: `Get-FileHash .\sync_v2.patch -Algorithm SHA256` = valoarea din `SHA256SUMS.txt`.

## A. PC MAIN — `C:\Users\xboxw\Desktop\v3-main`

**A1. Backup (obligatoriu, 1 minut)**
```powershell
$ts = Get-Date -Format yyyyMMdd_HHmmss
Copy-Item "C:\Users\xboxw\Desktop\v3-main" "C:\Users\xboxw\Desktop\v3-main_BACKUP_$ts" -Recurse
# copie separată a bazei SQLite (cu MAIN închis):
cd C:\Users\xboxw\Desktop\v3-main ; py -c "import sys; sys.path[:0]=['desktop','shared']; import database as d; print(d.DB_PATH)"
```
Copiază fișierul afișat (`*.db`) într-un loc sigur. **Închide aplicația MAIN.**

**A2. Verifică ramura și starea locală** (nu schimba nimic, doar citește)
```powershell
cd C:\Users\xboxw\Desktop\v3-main
git branch --show-current ; git log --oneline -3 ; git status --short
```
Dacă `git log` conține `12a3c7b` ca strămoș → folosești `sync_v2.patch`. Dacă ai deja aplicat patch-ul vechi (`9457db4`) → folosești `sync_v2_incremental.patch` cu `-Base 9457db4…` (vezi A3).

**A3. Instalare (întâi simulare)**
```powershell
cd <folderul unde ai dezarhivat ZIP-ul>
.\INSTALL_PATCH.ps1 -Repo "C:\Users\xboxw\Desktop\v3-main" -Branch (git -C "C:\Users\xboxw\Desktop\v3-main" branch --show-current) -DryRun
```
Rezultate posibile:
- `Verificări OK …` → rulează fără `-DryRun`.
- `OPRIT — Fișiere atinse de patch au modificări locale` → **nu s-a modificat nimic**. Comite/`git stash` doar acele fișiere sau trimite-mi lista; **nu forța**.
- `Ramura curentă …` → treci pe ramura corectă sau repetă cu `-Branch` corect, doar dacă știi că e aceeași istorie.
- `DEJA aplicat` → nimic de făcut.

Instalare reală: aceeași comandă fără `-DryRun`. Se creează `.patch_backups\<data>_<id>\` (copiile fișierelor înlocuite) și `.patch_applied\`. Anulare: `.\INSTALL_PATCH.ps1 -Repo … -Rollback` (refuză dacă ai modificat fișierele după instalare).
Patch incremental: `.\INSTALL_PATCH.ps1 -Repo … -Patch .\sync_v2_incremental.patch -Base 9457db4ab6178eaf0df11e17c8bf591f675a2c6f -Branch …` *(folosește hash-ul complet din `SHA256SUMS.txt`/`git log`)*.

**A4. Conectarea MAIN la Cloud TEST — cheia Binding existentă (SecretStore)**
Dacă pairing-ul pilot există deja în MAIN, cheia este deja în SecretStore și **este găsită automat** (setările `cloud_url` + `cloud_secret_ref`). Verificare (nu afișează cheia):
```powershell
py scripts\configure_main_sync.py --data-dir "<folderul bazei MAIN>" --test
```
Trebuie să vezi `Sync v2: cheie MAIN Binding din: registry` și `Conexiune reușită (Sync v2).`
Dacă vezi `NIMIC`: `py scripts\configure_main_sync.py --data-dir "<…>" --url https://<adresa-cloud-test> --binding-key-prompt` (cheia se tastează ascuns; se salvează criptat în Windows SecretStore).

**A5. Test Fluxul 1 (MAIN → Cloud → Telefon)**
1. Pornește MAIN. Creează un loc de veci (cu poligon), un concesionar, **două** persoane înhumate, un contract (+ o plată).
2. Apasă **Sincronizează** → raportul trebuie să arate „aplicate = N”, „în așteptare = 0”, fără erori.
3. Pe telefon (PWA): **Sincronizează** → caută locul/numele → fișa trebuie să conțină locul, concesionarul, **ambii** înhumați, contractul, plata. Nu trebuie să vezi UUID-uri.
4. Verificare read-only din PC: `$env:CIMITIR_VERIFY_URL="https://…"; $env:CIMITIR_VERIFY_PHONE_KEY="<cheia telefonului>"; py scripts\verify_sync_v2_live.py` → cod ieșire 0.

**A6. Test Fluxul 2 (Telefon → Cloud → MAIN)**
1. Din MAIN → *Cloud → 📱 Telefoane Mobile*: pentru telefon alege **Rol = Operator** (sau bifează registrele) → *Salvează permisiunile*.
2. Pe telefon: *Sincronizează* (primește permisiunile), editează o observație la un înhumat → *Salvează* → apare „1 în coadă” → se trimite automat.
3. În MAIN: **Sincronizează** → observația apare la înhumatul respectiv, **fără** înhumați duplicat.
4. Conflict: modifică același înhumat în MAIN și pe telefon (telefonul fără să sincronizeze înainte) → telefonul primește „de rezolvat”, MAIN păstrează varianta MAIN, iar varianta telefonului rămâne în jurnal (📱 Telefoane Mobile → Conflicte).
5. Offline: pe telefon pune modul avion → adaugă o plată → indicatorul arată „Offline · 1 în coadă” și plata apare marcată „nesincronizat” în fișă. Oprește modul avion → se trimite singură; de două ori „Sincronizează” nu dublează.

**A7. Rollback MAIN**: `.\INSTALL_PATCH.ps1 -Repo … -Rollback`, apoi revino la baza SQLite copiată la A1 dacă ai observat date neașteptate.

## L. Laptop Cloud TEST — `D:\v3-main` (Docker)

**L1. Instalează patch-ul** (identic cu A3, cu `-Repo "D:\v3-main"`).

**L2. Stare curentă (nu modifică nimic)**
```powershell
cd D:\v3-main\deploy\windows
.\laptop_cloud_test.ps1 status
```

**L3. Construiește imaginea cu TAG nou și pornește un container „canary” pe 8084** (containerele 8080–8083 rămân neatinse; 8080 `cimitir-cloud-test` nu este înlocuit niciodată de script)
```powershell
.\laptop_cloud_test.ps1 build  -Tag sync-v2-1
.\laptop_cloud_test.ps1 canary -Tag sync-v2-1 -Source cimitir-cloud-mobile-v2-next
```
Așteptat: `healthz=True … sync-v2 (fără cheie => 401/403)=True` și `Canary OK`. Canary folosește mediul copiat din `-Source` (fără a-l afișa). *Atenție:* fără migrarea 022 canary răspunde la rutele de bază, dar scrierile Sync v2 nu merg încă (vezi L4).
Oprire canary: `.\laptop_cloud_test.ps1 canary-stop`.

**L4. MIGRAREA 022 pe Supabase TEST — NUMAI după aprobarea ta scrisă**
Mai întâi doar verificări (nu modifică nimic):
```powershell
cd D:\v3-main
$env:CIMITIR_CP_ADMIN_DATABASE_URL = Read-Host "URL admin TEST (nhhczdmcocbobptogvsw)"   # nu se salvează, nu se afișează
py scripts\apply_sync_v2_migration_test.py --project-ref nhhczdmcocbobptogvsw --check-only
```
Trimite-mi rezultatul. **După „Aprob migrarea 022 pe TEST”**:
```powershell
py scripts\apply_sync_v2_migration_test.py --project-ref nhhczdmcocbobptogvsw
```
Script: refuză PROD, rulează într-o singură tranzacție, verifică înainte/după (numărul de rânduri, drepturi pe coloane, RLS, fără BYPASSRLS) și face ROLLBACK automat dacă ceva nu se potrivește. Starea anterioară (politici/drepturi/funcții) este salvată în `sync_v2_migration_backup`.
**Rollback verificabil** (restaurează exact starea anterioară; refuză dacă amprenta nu coincide, fără să modifice nimic):
```powershell
py scripts\apply_sync_v2_migration_test.py --project-ref nhhczdmcocbobptogvsw --rollback
```
Poligonul de test, organizația `Banovita` și cimitirul `banovita` **nu sunt create/șterse/modificate** de migrare (nu conține DELETE/INSERT/UPDATE pe date).

**L5. Comutare container (după L4)**
```powershell
cd D:\v3-main\deploy\windows
.\laptop_cloud_test.ps1 switch -Tag sync-v2-1 -Target cimitir-cloud-mobile-v2-next     # păstrează versiunea veche ca cimitir-cloud-mobile-v2-next-prev; revine automat dacă nu e sănătos
# anulare oricând:
.\laptop_cloud_test.ps1 rollback -Target cimitir-cloud-mobile-v2-next
```
Pentru 8081/8083 repetă cu `-Target`. Containerul 8080 nu se schimbă.

**L6. Rulează A5/A6** și, pentru izolare, încearcă pe telefonul asociat cu `banovita` să cauți un cod din alt cimitir (nu trebuie să apară nimic).

## Ce să-mi raportezi
Pentru fiecare pas: OK / mesajul exact afișat (fără chei/URL-uri secrete). Dacă un pas pică, **oprește-te** și trimite-mi mesajul — instalatorul și migrarea sunt făcute să nu modifice nimic în caz de eșec.
