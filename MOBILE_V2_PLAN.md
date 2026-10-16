# Cimitir Mobile v2 — plan de implementare

## Principii

- utilizatorul nu introduce și nu vede UUID-uri, parole DB, chei Supabase sau project ref;
- PWA instalabilă, responsive: telefon + tabletă, aceeași bază de cod;
- autentificare prin Phone Pairing existent (`Binding <cheie>`), cheie păstrată local;
- tenantul este dedus exclusiv din binding și impus de RLS;
- fișierele/API-urile nu se cache-uiesc în Service Worker; doar shell-ul static;
- scrierile business vor folosi op_id + base_version + outbox/conflict, nu POST-uri ad-hoc.

## Etapa 1 — fundație (implementată în acest branch)

- `/mobile/` PWA separată de prototipul `/phone`;
- pairing direct în PWA;
- dashboard profesional + stare cloud;
- căutare universală: locuri, concesionari, înhumați, contracte;
- hartă SVG touch/pan/zoom;
- fișă loc fără expunere de grave_uid;
- API read-only `/api/v1/mobile/*`;
- migrare `020_mobile_v2_read.sql`: cimitir_cp_app primește doar SELECT, cu policy RLS pe cp.*.

## Etapa 2 — permisiuni administrate din MAIN

- tabel `mobile_device_permissions` / capability set per binding;
- MAIN → Cloud/Telefoane: checkbox-uri module + nivel (`vizualizare`, `editare`, `aprobare`);
- `/api/v1/mobile/bootstrap` întoarce doar capabilitățile efective;
- meniul Mobile ascunde automat modulele nepermise, iar serverul verifică independent drepturile.

## Etapa 3 — Sync v2 și scriere offline

- IndexedDB: mirror local + outbox;
- `pull` incremental cu cursor/change_seq;
- `push` idempotent cu `op_id`, `base_version`, rezultate `applied/conflict/retry/rejected`;
- contracte, concesionari, înhumați, plăți, lucrări;
- conflictele nu suprascriu în tăcere.

## Etapa 4 — documente și contracte

- wizard Contract nou: Loc → Concesionar → Date → Preview → Emite;
- PDF generat server-side și arhivat automat;
- documente legate de loc/concesionar/înhumat/contract;
- fotografii și PDF prin Storage privat, URL semnat scurt.

## Etapa 5 — offline complet și distribuție

- cache local selectiv (fără CNP/documente brute în Service Worker);
- sincronizare la reluare/conectivitate;
- instalare PWA; opțional ambalare Capacitor/APK fără rescriere UI.
