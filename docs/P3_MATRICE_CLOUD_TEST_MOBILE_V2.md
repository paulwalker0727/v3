# P3 — Matrice de teste Cloud TEST + Mobile v2 (PREGĂTITĂ, NEEXECUTATĂ)

**Stare: nimic din această matrice nu a fost executat.** În mediul de audit nu există acreditări Supabase TEST
(`CIMITIR_LIVE_TESTS`, `CIMITIR_TEST_PROJECT_REF`, `DATABASE_URL`, `SUPABASE_URL` pentru un proiect TEST — toate absente), deci
`cimitir_test_guard` sare corect testele „live”. Nu s-a încercat nicio conectare alternativă; **Supabase PROD nu a fost atins** (ref-ul PROD este blocat permanent de gardă).

## Ce lipsește exact pentru a rula
1. Ref-ul proiectului Supabase TEST (20 litere, diferit de cel protejat) → `CIMITIR_TEST_PROJECT_REF`.
2. `DATABASE_URL` (pooler, `sslmode=require`, utilizator `*.<ref>`) și `SUPABASE_URL=<ref>.supabase.co` pentru acel proiect.
3. `CIMITIR_LIVE_TESTS=1`.
4. Un backend FastAPI de TEST pornit (URL în `CIMITIR_TEST_BASE_URL`) și o cheie de sincronizare de TEST emisă pentru MAIN și pentru un telefon (Mobile v2).
5. Un telefon/emulator cu Mobile v2 asociat cimitirului de TEST.
Verificare de identitate a mediului înainte de orice scriere: `python scripts/verify_backend_instance.py` + `deploy/windows_validation/validate_windows.py` (citire-doar). Dacă mediul nu poate fi identificat fără echivoc → STOP.

## Matricea
Legendă rezultat: `NEEXECUTAT` până la rularea reală. „Local” = ce acoperă deja testele automate fără Cloud.

| # | Scenariu | Pași | Așteptat | Acoperire locală existentă | Rezultat |
|---|---|---|---|---|---|
| C1 | MAIN → Cloud TEST | Creează loc/concesionar/contract/plată fictive în MAIN; sincronizează | Înregistrări prezente o dată în Cloud TEST; `owner_*` din contract identic cu MAIN | `test_etapa6_entity_sync`, `test_p3_istoric_contracte` | NEEXECUTAT |
| C2 | Mobile → Cloud TEST → MAIN | Adaugă înhumat/poză de pe telefon; MAIN trage din inbox | Apare în Inbox MAIN, nu direct în date; aprobarea creează o singură înregistrare | `test_etapa6c_field_inbox_*`, `test_field_inbox_main` | NEEXECUTAT |
| C3 | Retrimitere aceeași operațiune | Repetă push cu același `op_id` (timeout simulat după aplicare) | Fără dubluri; versiune neschimbată | `test_p2_sync_erori_client` | NEEXECUTAT |
| C4 | Conflicte offline | Modifică același contract în MAIN și pe telefon offline; sincronizează ambele | Conflictul apare în „Conflicte cloud”, nimic nu se pierde silențios; `owner_*` nu e rescris de un client care nu l-a trimis | `cloud_conflicts_window` (teste desktop) | NEEXECUTAT |
| C5 | Fotografii și documente | Încarcă poză loc + chitanță pe plată de pe telefon; descarcă în MAIN | Fișier identic (sha256), asociat entității corecte, vizibil doar utilizatorilor autorizați | `test_etapa7_media`, `test_etapa6b_field_storage` | NEEXECUTAT |
| C6 | Permisiuni | Cont `user` pe Cloud/mobile: încearcă modificare/ștergere; cont `operator`: aprobare | `user`: doar consultare + adăugare (politica P3); modificarea/ștergerea refuzate **și pe server** | `test_etapa2_sync` (drepturi), `test_p3_permisiuni_roluri` (doar MAIN) | NEEXECUTAT — **atenție:** politica `add` din P3 este implementată în MAIN; serverul/portalul nu a fost modificat (vezi raport) |
| C7 | Poligoane | Desenează/editează poligon pe telefon; sincronizează în MAIN | Geometrie identică; fără suprascrierea metadatelor locului | `test_polygon_sync_client`, `MOBILE_V2_POLYGON_TEST_STEP.md` | NEEXECUTAT |
| C8 | Restaurare după modificări nesincronizate | MAIN: modificări locale nesincronizate → restaurare backup → sincronizare | Modificările nesincronizate nu dispar fără avertisment; după restaurare nu se re-trimit operațiuni vechi ca noi | `test_restore_offline_protection`, `test_p2_erori_backup_restaurare` | NEEXECUTAT |
| C9 | Plăți legate de contract | Plată cu `contract_id` din MAIN → Cloud → Mobile | `contract_id` păstrat; plățile istorice fără `contract_id` rămân neschimbate | `test_p3_plati_contracte` (local) | NEEXECUTAT |

## Reguli de execuție
- Doar date fictive, prefix `ZZ-TEST-`; la final se șterg doar înregistrările create de test (după prefix), nu altele.
- Nu se rulează `apply_schema`/migrări pe Cloud din acest plan.
- Orice abatere de identitate (ref necunoscut/protejat) → oprire și raport.
