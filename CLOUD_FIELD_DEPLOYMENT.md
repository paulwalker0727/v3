# Cloud Field Deployment — portal mobil la distanță (Phone Capture + Field Inbox)

Scop: telefonul (4G/5G) → HTTPS public → **Phone Pairing / Phone Capture** → **Field Inbox API** → **Supabase PostgreSQL + Storage privat**. PC-ul MAIN poate fi oprit; MAIN se conectează ulterior la același API public și vede itemurile `PENDING`.

Nu se schimbă arhitectura Field Inbox / Phone Pairing, nu există migrații noi (001–019 neschimbate), nu există sync complet, Conflict Review, Cleanup, retenție sau HEIC.

## 1. Inventar (ce rulează în cloud)

| Element | Valoare |
|---|---|
| **Entrypoint** | `shared/cimitir_cloud_app.py` → `create_app()` (FastAPI, un singur proces) |
| **Start** | `python -m cimitir_cloud_app` (`PYTHONPATH=shared`; uvicorn pornește intern cu `proxy_headers=False`. Nu porni direct `uvicorn ... --factory` fără `--no-proxy-headers`) |
| Dependențe | `backend/requirements-cloud.txt` (fixate, minimale; fără Qt/Mongo/AI/pytest). `requirements.txt` și `requirements-render.txt` rămân neschimbate |
| Imagine | `Dockerfile` (python:3.12-slim, utilizator non-root 10001, `HEALTHCHECK` pe `/healthz`) |
| Templates/static | `backend/templates/phone_capture.html` + `partials/teren_capture.html` (partajat cu `/admin/teren`), `backend/static/teren.js`, `teren_ui.js`, `teren.css` (doar acestea sunt servite, pe whitelist) |
| Phone Capture | `GET /phone` (pagina; fără login de portal — autentificarea e legătura telefonului) |
| Phone Pairing | `POST /api/v1/phone-pairing/codes` (MAIN), `/redeem` (fără auth), `GET /phones`, `POST /phones/{uid}/revoke` (MAIN) |
| Field Inbox | `/api/v1/field-inbox/{me,items,items/{uid},items/{uid}/files,items/{uid}/files/{f}/url,accept,reject,imported}` |
| **Health** | `GET /healthz` (liveness) · `GET /api/v1/health` (readiness: API / DB / Storage) |

Portalul MAIN existent (`backend/server.py`, `/admin/teren`) rămâne neschimbat ca funcționalitate; nu se rulează în cloud.

### Cum merge Phone Capture fără MAIN
Pagina `/phone` e servită de același proces cloud și vorbește întotdeauna same-origin (originea paginii; CSP `connect-src 'self'`) cu `/api/v1/*`. Nu citește SQLite/registre locale, deci locul se alege cu **cod loc + `grave_uid`** (UUID-ul locului, afișat în MAIN) sau printr-un **deep-link** / cod QR:

```
https://<domeniu>/phone?cod_loc=A-1-10&grave_uid=<uuid>[&target_uid=<uuid-lucrare>&target_label=Soclu]
```
Doar UUID-uri valide sunt acceptate; restul se ignoră. (Field Inbox v1 nu validează în cloud apartenența `grave_uid` la cimitir — validarea rămâne la importul local în MAIN, `MISSING_GRAVE`.)

Emiterea codului de asociere (`POST /phone-pairing/codes`, TTL 60–900 s) cere cheia MAIN ca **client HTTP**; după emitere, `redeem` nu depinde de MAIN. Aplicația cloud nu are nevoie de procesul desktop MAIN, de `cimitir.db` sau de fișiere locale.

## 2. Variabile de mediu

Șablon: **`.env.example`** (doar placeholder-e; valorile reale NU se pun în Git — `.env` este în `.gitignore`).

| Variabilă | Obligatorie | Descriere |
|---|---|---|
| `CIMITIR_CP_DATABASE_URL` | da | Connection string PostgreSQL al rolului **`cimitir_cp_app`** (NOBYPASSRLS), pooler Supabase, `sslmode=require` |
| `SUPABASE_URL` | da | `https://<project_ref>.supabase.co` |
| `SUPABASE_SECRET_KEY` | da* | Cheie secretă Storage (doar server). *`SUPABASE_SERVICE_ROLE_KEY` se acceptă doar ca alternativă veche |
| `SUPABASE_BUCKET` | da | Bucket **privat** |
| `SUPABASE_PREFIX` | nu | Prefix în bucket |
| `CIMITIR_CLOUD_PROJECT_REF` | da | Ref-ul proiectului (20 litere); trebuie să coincidă cu `SUPABASE_URL` și cu URL-ul DB (verificat la pornire). **Singurul** proiect Supabase pe care serverul îl acceptă |
| `CIMITIR_PUBLIC_URL` | recomandat | URL-ul public HTTPS (nu e secret) |
| `CIMITIR_FIELD_INBOX_CORS_ORIGINS` | nu | Origini explicite pentru apeluri cross-origin din browser (ex. portalul MAIN). Pagina `/phone` e same-origin și nu are nevoie |
| `CIMITIR_PAIRING_TRUSTED_PROXIES` | da în spatele proxy | IP/CIDR ale proxy-urilor de încredere (vezi §3) |
| `CIMITIR_CP_SCHEMA` | nu | Schema planului de control |
| `PORT` (8080), `CIMITIR_CLOUD_HOST` (0.0.0.0), `CIMITIR_LOG_LEVEL` (INFO) | nu | Rulare |
| `CIMITIR_FIELD_INBOX_URL` | **doar pe MAIN / portalul local** | URL-ul public al API-ului cloud (profilul Cloud din MAIN). Nu se setează pe serverul cloud |

**Nu se setează pe serverul cloud:** `CIMITIR_CP_ADMIN_DATABASE_URL`, `CIMITIR_CP_CLEANUP_DATABASE_URL` (Cleanup nu face parte din această etapă), `DATABASE_URL`, `JWT_SECRET`.

Pornirea e **fail-fast**: variabile lipsă/inconsistente → ieșire cu cod 2 și mesaj cu *numele* variabilelor (niciodată valori).

### Ghidul de proiect Supabase (important)
Planul de control are două protecții permanente, neschimbate de această etapă:
1. Proiectul de producție listat în `PROTECTED_PROJECT_REFS` (`shared/cimitir_cloud_control_pg.py`, `shared/cimitir_test_guard.py`) este **blocat permanent** (și dacă apare în `CIMITIR_CLOUD_PROJECT_REF`).
2. Orice alt host Supabase era refuzat fără `CIMITIR_LIVE_TESTS=1` + ref-ul de TEST. Singura modificare de cod într-un fișier de securitate existent: `_guard()` acceptă acum un host Supabase **doar** dacă ref-ul lui este exact `CIMITIR_CLOUD_PROJECT_REF` (și nu e protejat).

⇒ Pentru deploy public este nevoie de un proiect Supabase **dedicat** (neprotejat) cu migrațiile 001–019 aplicate (`scripts/check_postgres.py`) și rolul `cimitir_cp_app` cu parolă setată. **Dacă dorești ca serverul să ruleze pe proiectul marcat „producție”, trebuie o decizie și un commit separat, explicit, al proprietarului** (scoaterea ref-ului din lista protejată) — nu a fost făcut aici.

## 3. HTTPS / reverse proxy / X-Forwarded-For

* TLS se termină la proxy (Caddy sau nginx, exemple în `deploy/cloud/`). Aplicația ascultă HTTP intern și **nu** se publică direct.
* Uvicorn rulează cu `proxy_headers=False`: singura sursă de încredere pentru IP-ul clientului este **`CIMITIR_PAIRING_TRUSTED_PROXIES`** (existent). `X-Forwarded-For` se ia în calcul **doar** dacă IP-ul conexiunii este în listă; se folosește intrarea cea mai din dreapta care nu e proxy de încredere; valoare invalidă/`/0`/XFF malformat ⇒ ignorat (rate limiting rămâne activ pe IP-ul conexiunii; la pornire valoarea invalidă oprește procesul).
* Docker compose (`deploy/cloud/docker-compose.example.yml`): rețea `edge` cu subrețea fixă `172.28.0.0/24` ⇒ `CIMITIR_PAIRING_TRUSTED_PROXIES=172.28.0.0/24`.
* nginx pe host + aplicație pe `127.0.0.1:8080` ⇒ `CIMITIR_PAIRING_TRUSTED_PROXIES=127.0.0.1`; nginx **rescrie** XFF (`proxy_set_header X-Forwarded-For $remote_addr`).
* Caddy nu are încredere în XFF primit de la client și îl rescrie.
* HSTS se setează la proxy (în exemple). Dacă `CIMITIR_PAIRING_TRUSTED_PROXIES` e gol în spatele unui proxy, toți clienții partajează aceeași găleată de rate limiting (avertisment la pornire).

## 4. CORS

* Doar origini explicite `http(s)://gazdă[:port]` din `CIMITIR_FIELD_INBOX_CORS_ORIGINS`; `*`, intrările fără schemă și alte scheme sunt ignorate (avertisment la pornire). Fără valoare ⇒ CORS nemontat.
* `allow_credentials=False`, metode `GET, POST`, antete `Authorization, Content-Type, X-File-Uid, X-File-Name, X-File-Sha256`. Cheia telefonului circulă doar în `Authorization` (nu cookie).
* Origine respinsă: fără `Access-Control-Allow-Origin`; preflight ⇒ 400.

## 5. Securitate / scurgeri

* Browserul/telefonul nu primește: parola PostgreSQL, `SUPABASE_SECRET_KEY`, `service_role`, credențiale de cleanup. Acestea există doar în mediul procesului; pagina, JS-ul, health-ul, erorile și logurile nu le conțin (verificat prin teste cu valori sentinelă).
* Storage rămâne **privat**; descărcarea se face doar cu URL semnat scurt (≤ 300 s), emis după autorizare MAIN.
* `/docs`, `/redoc`, `/openapi.json` dezactivate. `/phone` are `Content-Security-Policy` strict (`script-src 'self'`, fără inline), `X-Frame-Options: DENY`, `no-store`, `no-referrer`.
* Containerul rulează non-root, `read_only`, `cap_drop: ALL`, `no-new-privileges` (compose). Access log uvicorn dezactivat (loguri la proxy).

## 6. Health

* `GET /healthz` → `200 {"ok":true,"api":"up"}` — fără dependențe (Docker `HEALTHCHECK`, `health_uri` Caddy).
* `GET /api/v1/health` → `{"ok":bool,"api":"up","database":"up|down","storage":"configured|not_configured"}`, **200** dacă totul e ok, **503** altfel. `SELECT 1` cu rolul aplicației (timeout 3 s); Storage = doar verificare de configurare (fără apel de rețea). Fără connection string, chei sau mesaje de eroare.

## 7. Deploy pe un host Linux public (pași exacți)

Cerințe: VM Linux cu Docker + Compose, domeniu (A/AAAA către IP), porturi 80/443 deschise, proiect Supabase dedicat (§2) cu migrațiile aplicate și bucket privat creat.

```bash
git clone https://github.com/paulwalker0727/v3.git && cd v3 && git checkout cloud-field-deployment   # sau main după merge
cp .env.example .env && chmod 600 .env && $EDITOR .env            # completează valorile REALE (doar pe server)
export CIMITIR_DOMAIN=field.exemplu.ro
docker compose -f deploy/cloud/docker-compose.example.yml --env-file .env up -d --build
docker compose -f deploy/cloud/docker-compose.example.yml ps       # app: healthy
python3 scripts/cloud_smoke.py --url https://$CIMITIR_DOMAIN [--origin https://origine-portal]
```
`CIMITIR_DOMAIN` trebuie să fie și în `.env` pentru compose (`--env-file`). Alternativ, fără Compose: `docker build -t cimitir-cloud . && docker run -d --env-file .env -p 127.0.0.1:8080:8080 --restart unless-stopped --read-only --tmpfs /tmp cimitir-cloud` + `deploy/cloud/nginx.example.conf` (`certbot --nginx -d <domeniu>`; `CIMITIR_PAIRING_TRUSTED_PROXIES=127.0.0.1`).

Fără Docker: `python3.12 -m venv v && v/bin/pip install -r backend/requirements-cloud.txt && PYTHONPATH=shared v/bin/python -m cimitir_cloud_app` (sub systemd, cu `EnvironmentFile=/etc/cimitir/cloud.env`, mod 600).

### Conectarea MAIN și a telefonului
1. În MAIN: profil Cloud cu URL-ul public `https://<domeniu>` (Cloud Connection Profiles) / `CIMITIR_FIELD_INBOX_URL` pe portalul local.
2. În MAIN: *Telefoane / dispozitive* → generează cod (TTL ≤ 15 min).
3. Pe telefon: `https://<domeniu>/phone` → introdu codul → alege locul (`cod_loc` + `grave_uid`, sau deep-link/QR) → trimite.
4. MAIN (oricând, ulterior): Field Inbox → vede `PENDING` și continuă fluxul existent (accept/import).

## 8. Teste și limite

Vezi `CLOUD_FIELD_DEPLOYMENT_REPORT.md` (rezultate) — **PUBLIC DEPLOYMENT: NERULAT**. Rulare:
```bash
cd backend && python -m pytest -n 0 tests/test_cloud_field_deployment.py                 # fără DB reală (in-memory, aceleași validări)
CIMITIR_TEST_PG_ADMIN_URL=postgresql://postgres@127.0.0.1:55432/postgres \
  python -m pytest -n 0 tests/test_cloud_field_deployment_pg.py                           # PostgreSQL local real + Storage simulat (scripts/dev_postgres.sh start)
python scripts/cloud_smoke.py --clean-start --python <python-din-venv-curat>              # pornire curată ca în imagine
```
Neacoperit aici: build real `docker build` (Docker indisponibil în mediul de dezvoltare), Supabase real, DNS/TLS public, încărcare/latență 4G.
