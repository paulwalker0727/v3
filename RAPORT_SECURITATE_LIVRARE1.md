# Raport de securitate — Livrarea 1 (asociere MAIN ↔ Cloud, Flux B)

Domeniu: autentificare, pairing, secrete, izolarea organizațiilor, copii de test. Bază `d4a045d`. Rezultatele provin din teste LOCALE (SQLite simulat, PostgreSQL local + aplicația Cloud reală prin HTTP, Qt offscreen). **Nu s-a testat pe Supabase real, pe Windows/DPAPI sau pe backendul Docker 8086.**

## 1. Ce s-a păstrat neschimbat (nu s-a slăbit nimic)
- Matricea de decizie `decide_flow` / `cimitir_preflight.decide`, `plan_hash` + TOCTOU, `verify_empty`, publicarea atomică B și rollback-ul rămân neschimbate. Fluxul A refuză în continuare orice registru cu date (test: `test_reported_error_is_reproduced_by_flux_a_only_and_fixed`).
- Autorizarea rămâne pe server: codul de asociere aparține unui Owner, scope-ul (organizație/cimitir/actor) este derivat pe server din cod, nu din ce trimite clientul. Clientul MAIN trimite `None` ca actor pentru Flux B; serverul îl ignoră.
- Nu există un al treilea sistem de autentificare: se reutilizează codurile de pairing, legătura (Binding key) și administratorul Cloud existente.
- Licența (`.lic`, Ed25519, Hardware ID, verificarea la pornire) nu a fost atinsă.

## 2. Verificări de securitate executate (LOCAL, PostgreSQL + HTTP real)
| Aspect | Rezultat |
|---|---|
| Cod expirat | respins (`kind=code`), nimic scris local/cloud |
| Cod revocat | respins, fără legătură/secret/dispozitiv creat |
| Cod folosit | respins la a doua folosire |
| Cod al altei organizații | respins; fingerprint SQLite local și numărul de organizații/cimitire în cloud neschimbate |
| Cod pentru alt cimitir din aceeași organizație | respins; niciun dispozitiv creat |
| Cod de telefon folosit pe MAIN | respins |
| Asociere repetată (deja BOUND) | refuzată cu mesaj explicit |
| Cod/cheie în SQLite, în rezumat, în rezultat, în log (`caplog`) | absente (inclusiv planul salvat `cloud_assoc_plan`) |
| Plan salvat | fără cod, fără CNP/date personale (doar amprente și numere) |
| Cheia definitivă | doar în SecretStore (backend de test criptat Fernet); DPAPI real = netestat |
| Organizații/cimitire duplicate la Flux B | 0 (numărătoare directă în PostgreSQL înainte/după) |
| Rollback după eroare înainte de publicare | legătură revocată, secret șters, local neschimbat, fără rămășițe de plan |
| Cădere de proces + reluare / anulare | reluare fără cod nou; anulare curăță staging/media cu cheia legăturii începute |

## 3. Defecte reparate cu impact de securitate
1. **Copia TEST moștenea asocierea** (cale manuală): `sync_v2.connection()` oferea cheia Binding oricărui folder care conținea `cloud_secret_ref`. Acum asocierile noi sunt legate de amprenta folderului (`bound_root_fp`, hash al căii reale); o copie este trecută în `REBUILD_REQUIRED` și nu primește cheia. *Limită:* asocierile făcute înainte de această livrare nu au amprentă și rămân valabile; o copie făcută în aceeași cale (restaurare) nu poate fi deosebită. Mutarea legitimă a folderului cere re-asociere (comportament intenționat, fail-closed).
2. **Dialog Cloud: mediu TEST/PROD**: `env_value()` returna tăcut `TEST` pentru orice etichetă necunoscută; acum ridică eroare. Un profil PROD cu adresă locală este refuzat. Trecerea la PROD cere confirmare explicită (comportament păstrat și testat).
3. **Adresa Supabase ≠ adresa backendului**: URL-urile `*.supabase.co/.com/.in` sunt respinse la asociere.
4. După un rollback complet nu mai rămâne marcajul „asociere întreruptă” / „neverificată”, care putea afișa o stare înșelătoare.

## 4. Limitări / riscuri rămase (nu au fost abordate în Livrarea 1)
- Codul de asociere circulă ca header `Authorization: Pairing …` (nu în URL/log); rămâne valabil până expiră sau este folosit. TTL-ul este cel din serverul Cloud.
- Operatorul backendului are acces tehnic la date prin procesul aplicației (backendul folosește credențiale PostgreSQL runtime). Absența accesului permanent al operatorului în Supabase **nu** elimină acest acces; criptarea cu chei controlate de parohie este tratată în Livrarea 3.
- Amprenta folderului nu protejează o copie a întregului profil Windows (SecretStore DPAPI este legat de utilizator/mașină, deci copia pe alt calculator nu poate citi cheia; pe aceeași mașină și aceeași cale, da).
- Bootstrap-ul primului administrator Cloud (`BOOTSTRAP_TOKEN`) rămâne manual (Livrarea ulterioară).
- Nu s-au efectuat teste de penetrare, fuzzing sau audit independent al aplicației Cloud.
- `test_etapa6c_field_inbox_pg.py::test_production_is_hard_blocked` eșuează și pe `main` (aserție veche pe lista de migrări); neatins.
