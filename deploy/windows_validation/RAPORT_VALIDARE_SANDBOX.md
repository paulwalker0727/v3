# Raport PASS/FAIL — ce s-a verificat EFECTIV, unde, și ce NU

**Important:** acest raport provine din mediul meu de dezvoltare (Linux, fără ecran grafic, fără acces la laptopul, Supabase-ul sau backendul tău).
Nu am avut și nu am cerut parole/chei. **Validarea reală pe Windows cu Supabase TEST `vcpmcdardnumvlpxknlj` și backend `127.0.0.1:8085` NU a fost rulată — se rulează de tine cu `RUN_VALIDATION.bat`** (produce același tip de raport, din rezultate reale).

## A. Rezultate obținute aici (SIMULAT: PostgreSQL local în rol de „Supabase”, Qt offscreen)
| Verificare | Rezultat | Mediu |
|---|---|---|
| Instalare în copie: sursa neschimbată (amprentă cale+mărime+mtime identică), fișiere patch aplicate și verificate prin hash, `.env`/`.git` necopiate, date + licență copiate identic | **PASS** | arbore real 60f292c (786 fișiere), Linux |
| Drift: fișier modificat local → instalarea se oprește înainte să creeze ceva | **PASS** | test automat |
| Destinații periculoase (în sursă, = sursa, în datele reale, ne-goală) refuzate | **PASS** | test automat |
| Verificare versiune exactă (acceptă și CRLF) | **PASS** | test automat |
| Cele 6 file, markere INDISPONIBIL, `manage_config` (admin permis / non-admin refuzat, cu conturile din baza copiei) | **PASS** | Qt offscreen |
| Persistența configurației într-un proces nou, fără parolă/secret salvat | **PASS** | SQLite copie |
| Verificare proiect cu cele **26 migrări** aplicate: detectate 26/26, 0 lipsă, 0 blocate; previzualizare fără pași; baza neschimbată înainte/după; parolă greșită respinsă; ref≠URL respins fără conexiune; bucket privat OK | **PASS** | PostgreSQL 15 local cu migrările reale 001–026 |
| Port 8084 refuzat de validator; parola scrubbed din raport și absentă din fișiere | **PASS** | test automat |
| Licența: `desktop/license/*`, `license_manager.py`, `login_window.py`, `auth.py`, `cimitir_auth.py` — zero diferențe față de `60f292c` | **PASS** | `git diff` |
| Suita completă `desktop/tests` | aceleași 19 eșecuri + 2 erori ca pe commitul de bază (mediu) | comparat cu baza |
| `tools/setup_manager/tests` | 138 PASS | cu PostgreSQL real |
| Testele noi (Centru Cloud + validare) | trec toate (98 cu PostgreSQL local, 1 skip fără el) | |
| Izolare copie: `backup_dir` real, sincronizări automate și profil PROD/8084 din baza copiată neutralizate DOAR în copie; originalele byte-identice | **PASS** | test automat (a fost un defect găsit în verificarea independentă, corectat) |

Rulare efectivă a validatorului din ZIP-ul livrat, pe copie creată de installer, în acest mediu (fără parolă, fără backend, fără licență reală):
ENV-01, LIC-02, LIC-03, AUTH-01..03, PERM-01/02, UI-01/02, CFG-01, BE-00 = PASS; **LIC-01 FAIL** (licență falsă de test — așteptat aici) și **BE-01 FAIL** (nu există backend aici — așteptat). Pe laptopul tău acestea trebuie să iasă PASS.

## B. NU verificat (necesită laptopul tău)
* Windows (căi, `.bat`, DPAPI pentru secretele copiate), licența reală `.lic` + HWID.
* Conexiunea reală la `*.pooler.supabase.com` (TLS, Session pooler, rolul `postgres.<ref>`, `storage.buckets` pe Supabase real).
* Backendul 8085: `/api/v1/health`, `/api/v1/admin/status`, asocierea MAIN, crearea primului administrator Cloud.
* Aspectul real al ferestrelor pe Windows (capturile sunt randate offscreen).

## C. Ce lucrează din MAIN vs. ce rămâne manual
Vezi tabelul din `CITESTE-MA.md` §5. Pe scurt: verificarea Supabase, profilul, verificarea backendului, asocierea (cu cont admin Cloud) și starea inițializării sunt din MAIN; **crearea primului administrator Cloud cere ca serverul 8085 să aibă un BOOTSTRAP_TOKEN valid** — setarea lui este o modificare a mediului TEST și nu o fac fără aprobarea ta; livrarea automată a tokenului este ETAPA 3 (indisponibilă).

## D. Riscuri cunoscute
* Dialogul vechi „Configurare Cloud simplificată” (cod preexistent, neschimbat) poate converti în loc un profil TEST în PROD la salvare; Centrul Cloud nu o face (creează profil separat). Nu folosi dialogul vechi pe copie pentru schimbarea mediului.
* Copia moștenește asocierea MAIN (chei în `test_env\data\secrets`); sincronizarea automată e oprită, cea manuală ar folosi aceeași asociere ca instalarea reală.
* Dacă `D:\v3-main` are modificări locale în fișierele atinse (`ui_mainwindow.py`, `cloud_simple.py`, `cimitir.spec`, `guard.py`, `cimitir_cloud_admin_client.py`), installer-ul se oprește și le listează — nu se forțează.
* Dacă baza de date a backendului TEST are un istoric al migrărilor cu stări „NEVERIFICABILĂ”/„DIFERITĂ”, verificarea SB-04 va da FAIL cu lista exactă — fără a modifica nimic.
* Parola DB nu trebuie resetată pentru acest test.
