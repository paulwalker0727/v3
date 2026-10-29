# Actualizarea backendului unei parohii (fără pierderea datelor)

Se face de **operator**, prin serviciul de provizionare (fila „G. Provizionare” sau API). Administratorii parohiei nu rulează comenzi.

## Pași
1. **Importă release-ul nou** (owner): `POST /api/v1/provisioner/releases {"source_path": "/opt/cimitir-src", "version": "1.1.0"}`. Se construiește o copie imuabilă cu manifest SHA-256; codul provisionerului nu intră în ea. Un release existent nu poate fi suprascris.
2. **Verifică release-ul** pe o instanță TEST (mediu separat) înainte de PROD.
3. **Actualizează instanța** (operator): `POST /instances/<slug>/update {"admin_database_url": "...", "release": "1.1.0"}`. Serviciul execută, în ordine:
   1. verifică manifestul release-ului (modificat ⇒ refuz);
   2. **backup criptat și verificat** al bazei (obligatoriu);
   3. aplică migrările noi într-o tranzacție (la eroare: ROLLBACK complet, **vechiul backend rămâne pornit**, datele neatinse);
   4. oprește vechiul backend, pornește noul release, așteaptă `/healthz`;
   5. verifică HTTPS + `/api/v1/health` (DB, Storage);
   6. dacă noul release nu este sănătos ⇒ **revenire automată** la release-ul anterior (`UPDATE_ROLLED_BACK`).
4. Verifică în Centrul Cloud: stare „Activă (sănătoasă)”, release nou, administrator existent.
5. (Opțional) setează release-ul implicit pentru instanțe noi: `POST /releases/default {"version": "1.1.0"}`.

## Ce NU se întâmplă
* Datele nu se șterg; nu se rulează `DROP`. Migrările sunt aditive; un rollback de **schemă** nu este automat (vechiul cod funcționează cu schema nouă doar dacă migrările sunt compatibile — regulă de dezvoltare: migrări doar aditive).
* Fișierele din Storage nu sunt atinse.

## Recuperare după o actualizare defectuoasă
* Backupul `pre-actualizare` apare în `GET /instances/<slug>/backups`.
* Restaurare într-o bază goală (exercițiu): `POST /instances/<slug>/restore {"backup_id": "...", "target_database_url": "..."}`.
* Restaurare peste instanță (owner): `POST /instances/<slug>/confirmations {"action": "restore"}` ⇒ cod; apoi `POST /restore {"backup_id", "confirmation": "<cod>", "confirm_slug": "<slug>", "admin_database_url"}`. Se face automat un backup de siguranță `pre-restaurare` înainte.

## Rotirea secretelor (fără întreruperi lungi)
`POST /instances/<slug>/rotate-secret {"admin_database_url": ...}` generează o parolă nouă pentru rolul runtime, o verifică prin login real, iar `POST /instances/<slug>/restart` repornește backendul cu noul secret. Reînnoirea certificatului local: `POST /instances/<slug>/renew-tls` (în producție o face Caddy automat).

## Repornirea serverului
La pornire, `serve` apelează `reconcile()`: repornește instanțele `running` ale căror procese nu mai există.
