# Cloud Field Deployment — raport de verificare

Bază: `main` @ 7261c15 (include Phone Pairing, Field Inbox, Phone Capture v1, Map-to-Field Capture, Field Inbox Local Import, Import History, Cloud Connection Profiles — verificat în `git log`). Branch: `cloud-field-deployment`.

**PUBLIC DEPLOYMENT: NERULAT** — nu s-a făcut niciun deploy public, nu s-a atins Supabase real. `docker build` NU a fost rulat (Docker indisponibil în mediul de dezvoltare).

| Verificare | Rezultat | Cum |
|---|---|---|
| Pornire „container” curată | OK | `scripts/cloud_smoke.py --clean-start` într-un venv curat (doar `requirements-cloud.txt`) și un arbore copiat exact după liniile `COPY` din Dockerfile; + `test_container_start_*` |
| Fail-fast fără env | OK | exit 2, mesaj cu nume de variabile |
| Health (alive / DB / Storage) | OK | `/healthz`, `/api/v1/health` (200/503); DB reală PostgreSQL 15 local + DB inaccesibilă ⇒ `database:down` |
| Templates/static | OK | whitelist 3 fișiere; traversal/`app.js` ⇒ 404 |
| Phone Capture se încarcă | OK | HTTP + browser real (Playwright/Chromium, viewport 390×844): pairing → alegere loc → poză loc + înhumat trimise, reload păstrează legătura, 0 erori consolă (manual, nu în CI) |
| Pairing | OK | redeem, cod single-use, cod invalid 401; PG real: emitere cu cheie MAIN (HTTP) + redeem |
| Field Inbox create / upload | OK | in-memory + PostgreSQL real (RLS, rol `cimitir_cp_app`) + Storage simulat HTTP; MAIN vede PENDING și URL semnat ≤ 300 s |
| CORS permis / respins | OK | origine explicită; fără `Allow-Credentials`; `*`/junk ignorate; preflight respins 400 |
| Trusted proxy | OK | XFF ignorat implicit; folosit doar de la proxy declarat; invalid ⇒ fail-safe/pornire refuzată |
| Fără scurgeri de secrete | OK | valori sentinelă în env nu apar în health/pagină/JS/erori/log; `/docs` dezactivat |
| MAIN absent | OK | import closure fără module MAIN/SQLite/Qt (subprocess); flux complet cu `sqlite3.connect` pe fișier interzis, `CIMITIR_DATA_DIR` inexistent, director gol |
| nginx exemplu | `nginx -t` OK | cu certificate self-signed temporare. Caddyfile + compose: NEVALIDATE (fără caddy/docker) |

Teste noi: `test_cloud_field_deployment.py` (40), `test_cloud_field_deployment_pg.py` (4, PostgreSQL real). Regresie pe PG/pairing/portal/6B/6C: 156 trecute în rularea țintită (-n 0). Suita completă backend (xdist): 916 trecute / 21 eșuate / 67 erori — toate eșecurile reproduse identic pe `main` (lipsă `fitz`, `.env` locale, contaminare xdist `mobile_upload`/rol `cp6b_cleanup_worker`); comparație pe fișierele afectate: branch ⊆ main (0 regresii). `desktop/tests/test_phone_capture.py` nu a putut rula (lipsește PySide6); `teren.js` nu s-a modificat.

## Decizii / condiții de deploy
* `_guard` (planul de control) refuza **orice** host Supabase în afara proiectului de TEST; proiectul „producție” din `PROTECTED_PROJECT_REFS` rămâne blocat permanent. Adăugat doar `CIMITIR_CLOUD_PROJECT_REF`. Deploy-ul necesită un proiect Supabase dedicat sau o decizie explicită a proprietarului pentru cel protejat (nefăcută aici).
* Migrațiile 001–019 trebuie să fie deja aplicate în proiectul țintă (neschimbate).
* Alegerea locului în `/phone` fără registru local = `cod_loc` + `grave_uid` sau deep-link/QR.
