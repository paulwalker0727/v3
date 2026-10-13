# Etapa 6D — Phone Pairing v1 (migrare 019 + service + API; fără UI)

Asociază explicit un telefon (device `MOBILE`) cu UN singur cimitir, ca telefonul să poată folosi Field Inbox v1 (`Authorization: Binding <cheie>`). Nu reimplementează Field Inbox și nu schimbă istoricul.

## Ce exista deja și se reutilizează
- Tabelele canonice `devices` (`device_type` = MAIN/MOBILE), `device_cemetery_bindings` (cheie = doar hash, `revoked_at`), `device_pairing_codes` (cod = doar hash, expirare, `used_at`), RLS `cimitir_cp_app` (`cp_org_ok/cp_cem_ok`), autentificarea `PostgresControlPlane.authenticate`, `FieldInboxService.authenticate`.
- Înainte de 6D, `redeem_code` crea doar dispozitive MAIN și doar în bootstrap; legătura MOBILE se crea manual (seed). Nu exista nicio componentă de pairing mobil.

## Design
```
MAIN (operator, legătură activă)  --POST /codes-->  cod (20 car., ~99 biți, TTL 60..900 s, implicit 600)   [DB: doar sha256]
Telefon  --POST /redeem {code, device_name?, client_id?}-->  device MOBILE nou + legătură NOUĂ (cheie 256 biți, DB: doar sha256)
Telefon  --Authorization: Binding <cheie>-->  Field Inbox v1 (neschimbat)
MAIN     --GET /phones, POST /phones/{binding_uid}/revoke-->  administrare / revocare
```
Rute: `/api/v1/phone-pairing/{codes,redeem,phones,phones/{uid}/revoke}`; `cimitir_field_inbox_http.create_app()` le montează lângă Field Inbox.

## Garanții de securitate
| Cerință | Implementare |
|---|---|
| explicit, limitat la tenant/cimitir | codul îl emite doar MAIN, pentru cimitirul legăturii lui; org/cimitir se iau DIN rândul codului, niciodată din cererea telefonului; legătura rezultată e per cimitir |
| random, expirare, o singură folosire | `secrets.choice` pe 31 simboluri × 20; TTL ≤ 15 min (trigger DB: ≤ 30 min); consum atomic `UPDATE … WHERE used_at IS NULL AND expires_at > now()` în aceeași tranzacție cu device + legătură (lacăt per cimitir) |
| fără token brut în DB | cod și cheie: doar sha256; apar o singură dată în răspuns; nu apar în audit/loguri/erori |
| replay / reutilizare | cod consumat = respins (mesaj generic identic cu greșit/expirat); trigger DB: un cod MOBILE folosit nu mai poate fi modificat |
| izolare A/B | legătura e per cimitir; RLS + filtre explicite; MAIN vede/revocă doar telefoanele cimitirului propriu |
| revocare | `revoke_phone` (doar MAIN, doar legături MOBILE din cimitirul propriu, idempotent); trigger DB: o legătură revocată nu se reactivează și nu își schimbă cheia |
| fără secrete în telefon | telefonul primește doar cheia propriei legături; nu primește service-role / parole DB / `SUPABASE_SECRET_KEY` |
| rate limiting | `cp_pairing_attempts`: contor atomic per găleată (hash IP sursă + hash `client_id`), 10 încercări / 15 min, numărat ÎNAINTE de verificarea codului (plafon valabil și sub concurență); succes = refund; 429 + `Retry-After` |
| validare server-side | tot (cod, TTL, tenant, rol MAIN, statusuri) în server + DB; clientul nu e crezut |
| privilegii minime | legătura MOBILE: fără emitent de bootstrap (trigger DB), fără accept/reject/imported (018), fără emitere/listare/revocare; vede doar itemurile proprii |

## Migrarea 019 (`019_phone_pairing.sql`, 001–018 neschimbate; aditivă, idempotentă)
`cp_pairing_attempts` (+RLS pe `cp.pair_buckets`), `CHECK device_type IN ('MAIN','MOBILE')` (NOT VALID) + index parțial pe coduri MOBILE deschise, triggere `cp_mobile_pairing_guard`, `cp_binding_revocation_guard`, `cp_binding_issuers_no_mobile`.

## Modificări în cod existent (minime)
- `cimitir_cloud_control_pg.py`: `_CODE_SQL` filtrează `device_type='MAIN'` (înainte un cod MOBILE putea fi consumat de fluxul MAIN `redeem_code` ⇒ escaladare la MAIN); cheia de context `pair_buckets`.
- `cimitir_field_inbox_http.create_app`: montează routerul de pairing.

## Configurare operațională
- În spatele unui proxy invers setează `CIMITIR_PAIRING_TRUSTED_PROXIES=<nr. proxy-uri>` (se folosește a N-a intrare de la dreapta din `X-Forwarded-For`). Fără setare, `X-Forwarded-For` e ignorat (nu poate fi falsificat), dar toți clienții din spatele proxy-ului împart aceeași găleată IP.
- Pierderea răspunsului de redeem: codul e deja consumat ⇒ MAIN emite un cod nou; legătura orfană se revocă din `GET /phones` + `revoke`.

## Teste
- Local (PostgreSQL real, rol `cimitir_cp_app`): `CIMITIR_TEST_PG_ADMIN_URL=postgresql://<superuser>@127.0.0.1:<port>/postgres pytest -n 0 backend/tests/test_phone_pairing_pg.py`.
- Live Supabase TEST (sărit implicit): `backend/tests/test_phone_pairing_live.py` (aceleași variabile ca la Field Inbox live; `CIMITIR_APPLY_MIGRATION_019=1` aplică 019 doar după confirmarea ref-ului `nhhczdmcocbobptogvsw`).

## Backlog (nu e în v1)
Anularea unui cod emis dar neutilizat, expirarea automată a legăturilor MOBILE inactive, rotirea cheii legăturii, retenția/curățarea `cp_pairing_attempts`, UI MAIN/telefon (QR), notificare la pairing, plafon global anti-DDoS pe `/redeem`.
