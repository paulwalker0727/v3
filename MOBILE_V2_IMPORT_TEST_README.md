# Cimitir Mobile v2 — import pilot al unui poligon în registrul live TEST

Acest patch adaugă `POST /api/v1/mobile/test-polygon/import`. Implicit, endpointul este **dezactivat** și nu scrie în cloud până la aplicarea explicită a migrării `021` în PostgreSQL **TEST** și setarea `CIMITIR_MOBILE_TEST_POLYGON_IMPORT=1` în containerul **TEST**.

## Restricții
- Exclusiv binding de dispozitiv MAIN valid și activ; tenantul se deduce server-side din binding.
- Un singur poligon cu hash SHA-256 conform exportului existent `mobile-v2-polygon-test.json`.
- Doar INSERT; coliziunile UID/cod loc sunt refuzate. Nu există UPDATE / DELETE / ștergere în MAIN.
- Nu există autentificare cu parola veche `/api/sync`; se folosește `Authorization: Binding <binding key MAIN>`.
- Nu afișa/posta cheia Binding sau JSON-ul în chat, GitHub ori loguri publice.

## Instalare conservatoare
1. Pe PC, suprascrie numai fișierele patch-ului în `C:\Users\xboxw\Desktop\v3-main`; verifică `git diff`, rulează `py -m pytest -q validation\test_mobile_v2_polygon_import.py validation\test_mobile_v2_data_pipeline.py`, apoi commit și push pe `mobile-v2`.
2. Pe laptop, din `D:\v3-main`, fă `git pull --ff-only origin mobile-v2`, apoi construiește imaginea cu un TAG NOU; NU înlocui containerele live.
3. Confirmă că migrarea este aplicată **doar** în schema/proiectul PostgreSQL TEST. Nu rula 021 pe PROD. Revizuiește SQL și verifică drepturile RLS înainte de activarea flagului.
4. Pornește un nou container izolat pe alt port folosind configurația TEST existentă și flagul `CIMITIR_MOBILE_TEST_POLYGON_IMPORT=1`. Verifică `/api/v1/health`, pairing MAIN și scope; nu muta Cloudflare până la aceste verificări.
5. POST pe endpoint cu cheia *MAIN* și JSON-ul exportat, numai după testul verificărilor de acces; răspunsul `imported:1` indică un INSERT reușit. Verifică separat `GET /api/v1/mobile/data-status` și harta pe PWA. Nu repeta POST dacă a reușit (conflictul unic este comportamentul așteptat).

## Atenție
- Aceasta **nu** este sincronizare automată sau bidirecțională. Este o probă explicită pentru un singur poligon.
- Nicio migrare nu este aplicată automat de generarea patch-ului.
- La resetarea containerelor Quick Tunnel, domeniul se poate schimba; cheia MAIN rămâne una distinctă de pairing-ul telefonului.
