# Inițializarea primului administrator Cloud din MAIN (fără BOOTSTRAP_TOKEN manual și fără recrearea containerului)

## Problema
Până acum primul administrator Cloud cerea un `BOOTSTRAP_TOKEN` generat manual și **recrearea containerului Docker** (token în mediul containerului, deci în configurația persistentă).

## Mecanismul (autorizare = dreptul de administrare al instanței, NU licența)
- **Cheia proprietarului instanței**: pereche Ed25519, **distinctă** de cheia de semnare a licențelor. Cheia privată rămâne doar în SecretStore-ul MAIN (DPAPI pe Windows) sau într-un fișier 0600 al operatorului; **serverul cunoaște doar cheia publică**.
- **Provisionare, o singură dată, la crearea instanței** (nu la fiecare inițializare): serverul primește `CIMITIR_INSTANCE_ID` (identificator ales de operator) și `CIMITIR_INSTANCE_ADMIN_PUBKEY` (cheia publică; nu e secret). Imaginea Cloud are nevoie de `cryptography` (adăugat în `backend/requirements-cloud.txt`) ⇒ **o singură reconstruire a imaginii/recreare a containerului la trecerea pe această versiune**; după aceea nu mai e nevoie de recreări pentru inițializare.
- **În MAIN**: `Cloud → Asociază/Coduri → „Inițializare administrator Cloud”`: *Verifică instanța* (afișează `instance_id`, dacă există administrator, dacă cheia locală corespunde serverului după amprentă) → *Generează cheia proprietarului* (o singură dată; afișează doar cheia publică + amprenta, de pus la provisionare) → email + parolă + confirmare → *Inițializează*.
- MAIN semnează un **grant** (`grant.<payload>.<semnătură>`): scop `cloud-admin-bootstrap`, audiență = `instance_id`, `iat/exp`, durată ≤ 15 min (MAIN folosește 5 min), `jti` aleatoriu. Serverul îl verifică (semnătură, audiență, scop, expirare, durată maximă) și îl acceptă **doar cât timp instanța nu are administrator**. Tot `POST /api/v1/admin/bootstrap` rămâne aceeași rută (câmpul `token` acceptă fie tokenul clasic, fie grantul) ⇒ nu există un al treilea sistem de autentificare.
- **Invalidare / ne-reutilizare**: după primul administrator starea `bootstrap_completed` (persistentă în PostgreSQL, neschimbată) închide inițializarea ⇒ orice grant, inclusiv unul interceptat, primește 409 `BOOTSTRAP_CLOSED`. Grantul nu se stochează nicăieri (nici în SQLite, nici în loguri); parola nu se păstrează în MAIN.
- **Tokenul clasic** (`BOOTSTRAP_TOKEN`) rămâne o cale separată, neschimbată (compatibilitate). Dacă instanța e deja inițializată dar tokenul e încă în mediul containerului, `/status` raportează `legacy_token_configured: true`, iar MAIN avertizează să-l elimini din configurația containerului. *Serverul nu poate modifica mediul propriului container*: eliminarea efectivă din configurația persistentă este un pas al operatorului (inofensiv între timp: inițializarea e închisă).
- Nu am modificat nimic din licență: fluxul de grant nu citește și nu verifică `.lic`; fără cheia proprietarului, MAIN refuză local (`no_key`) fără să trimită nimic spre server, iar cheia unei alte instanțe este respinsă prin amprentă (`key_mismatch`).

## Unealtă de operator (opțional)
`python scripts/cloud_instance_owner.py keygen --out owner.key` → afișează `CIMITIR_INSTANCE_ADMIN_PUBKEY=…` + amprenta. `grant --key owner.key --instance-id ID` → emite un grant (stdout). Normal, MAIN face asta singur.

## Exemplu de provisionare (Docker, o singură dată)
```
-e CIMITIR_INSTANCE_ID=parohia-banovita-test -e CIMITIR_INSTANCE_ADMIN_PUBKEY=<cheia publică afișată de MAIN>
```
(nu mai e nevoie de `BOOTSTRAP_TOKEN` / `BOOTSTRAP_TOKEN_EXPIRES_AT`). **Nu modifica containerele 8084/8085/8086**: se aplică doar la o instanță nouă sau la o recreare pe care o hotărăști tu.

## Ce NU este acoperit
- Test pe containerul real 8086, pe Windows/DPAPI sau Supabase real: **netestat** (teste LOCAL-PG+HTTP).
- Cine deține cheia privată a proprietarului instanței poate crea administratorul **cât timp instanța e neinițializată**; protejarea acelei chei (backup, parolă SecretStore Windows) este responsabilitatea proprietarului. Pierderea ei înainte de inițializare = reprovisionare (sau tokenul clasic).
- Recuperarea parolei administratorului rămâne prin `scripts/cloud_admin_recovery.py` (neschimbat).
