# Mobile v2 — export controlat al poligonului TEST

Acest patch **nu sincronizează** încă MAIN cu cloud. Pregătește un export local verificabil, exclusiv pentru un singur poligon desenat. Nu deschide rețeaua, nu citește date personale, nu scrie în SQLite și nu schimbă Docker.

## PC cu MAIN (`C:\Users\xboxw\Desktop\v3-main`)

Extragerea arhivei peste proiect adaugă două fișiere fără a suprascrie componente de producție.

În PowerShell, folosește `$db` dacă ai păstrat variabila cu calea corectă a registrului activ; într-o fereastră nouă reconstruiește-o din metoda utilizată la diagnosticul anterior. **Nu folosi o cale ghicită.**

```powershell
py scripts\export_mobile_v2_test_polygon.py --db "$db" --output mobile-v2-polygon-test.json
py -m pytest -q validation\test_mobile_v2_polygon_export.py
```

Rezultatul local JSON conține geometria, codul locului și UID-ul tehnic, dar nu nume, CNP-uri, contracte, plăți sau chei. Nu trimite JSON-ul public și nu îl face commit/push. Trimite doar mesajul cu numărul de poligoane și SHA-256.

## Pasul următor necesar pentru afișarea în telefon

Cloud-ul din `shared/cimitir_cloud_app.py` montează numai API-urile mobile read-only și pairing. Este necesar un endpoint nou pentru import **strict TEST**, autentificat cu binding-ul MAIN și autorizare server-side pe organizație/cimitir, care să verifice schema, digestul, UID-ul, geometria și conflictul de versiune, apoi să scrie tranzacțional în `locuri_de_veci` cu `organization_uid` și `cemetery_uid` corecte. Trebuie testate și granturile/rolul PostgreSQL; `020_mobile_v2_read.sql` acordă doar SELECT. Abia după aceea se poate executa un upload controlat și verifica `/api/v1/mobile/data-status`.

Nu confunda publicarea în `cp_registry_row_data` cu scrierea în `locuri_de_veci`. Nu utiliza parolele de PostgreSQL ori Supabase în browser și nu îndrepta direct `/api/sync/*` spre API-ul nou.
