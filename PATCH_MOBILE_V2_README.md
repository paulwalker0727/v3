# Patch Cimitir Mobile v2 — diagnostic + corecții de citire

Acest patch este pentru arhiva primită din branch-ul **`mobile-v2`** la 16.10.2026. Se extrage **peste rădăcina aceluiași branch**, nu peste `main`. Conține doar fișierele enumerate mai jos, nu `.env`, DB local, chei sau media.

## Instalează în copia locală (fără PROD)

1. În PowerShell, din proiect: `cd C:\Users\xboxw\Desktop\v3-main`.
2. Verifică branch-ul curent: `git switch mobile-v2` și `git status --short`. Ar trebui să fie curat înainte de extragere.
3. Extrage arhiva patch în acest director, păstrând subdirectoarele `shared`, `backend/static`, `scripts`, `validation`.
4. Rulează `py -m pytest -q validation\test_mobile_v2_data_pipeline.py` și `py scripts\diagnose_mobile_v2_local.py`.
5. Verifică `git diff`/`git status` și apoi fă **commit pe mobile-v2** dacă verificările corespund.
6. Rebuild/deploy **numai** pe containerul Cloud TEST, în modul deja folosit pe laptop. Endpointul nou `/api/v1/mobile/data-status` este disponibil numai după redeploy. Migrarea `020_mobile_v2_read.sql` trebuie să fie aplicată numai pe schema PostgreSQL TEST, conform procedurii deja existente; patch-ul nu rulează migrări.
7. Deschide `/mobile/`, apoi „Reverifică datele”, ca să vezi diferența între datele din bootstrap și cele live.

## Conține

- `shared/cimitir_mobile_registry.py` — diagnostic read-only, rezolvare UID hex32/UUID36, avize în fișa locului;
- `shared/cimitir_mobile_http.py` — endpoint `GET /api/v1/mobile/data-status`;
- `backend/static/mobile_app.js`, `.css` — mesaje de diagnostic și butoane de reverificare;
- `scripts/diagnose_mobile_v2_local.py` — diagnostic SQLite local, fără scrieri și fără secrete;
- `validation/test_mobile_v2_data_pipeline.py` — teste independente de datele reale;
- `MOBILE_V2_DATA_BRIDGE.md` — explicația cauzei și contractul de implementare a sincronizării reale.

## Important

**Nu implementează încă transferul MAIN → Cloud TEST.** Cloud Docker servește API-ul nou `/api/v1`, în timp ce sincronizarea legacy MAIN folosește `/api/sync`. O simplă modificare de URL nu unește sigur cele două protocoale. Folosește diagnosticul ca să stabilești starea exactă înainte de realizarea Sync V2.
