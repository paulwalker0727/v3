# Raport de securitate — Livrările 2, 4 și „Token automat” (completează `RAPORT_SECURITATE_LIVRARE1.md`)

Teste LOCALE (PostgreSQL local + HTTP real, SQLite, Qt offscreen). Nimic pe Supabase/Windows-DPAPI/Docker real.

## 1. Inițializarea administratorului Cloud (grant semnat)
| Amenințare | Control | Verificat |
|---|---|---|
| Licența `.lic` creează admin | fluxul nu citește licența; autorizarea cere cheia privată a proprietarului instanței (Ed25519, distinctă de cheia de licențiere) | cod + `no_key` refuzat local fără cerere către server |
| Grant falsificat / altă cheie / altă instanță / expirat / modificat | verificare semnătură + audiență (`CIMITIR_INSTANCE_ID`) + scop + expirare + durată maximă (≤ 15 min, clamp la emitere) | test: 5 variante ⇒ 403, instanța rămâne neinițializată |
| Reutilizarea grantului după inițializare | `bootstrap_completed` persistent în PostgreSQL ⇒ 409 `BOOTSTRAP_CLOSED` pentru orice grant nou sau vechi | test |
| Cheie de proprietar a altei instanțe în MAIN | comparare amprentă cheie ↔ server înainte de orice trimitere (`key_mismatch`) | test |
| Scurgeri | grantul/cheia privată/parola nu apar în rezultate, UI sau `caplog`; parola nu se stochează; cheia privată doar în SecretStore | test |
| Forță brută | limitatoarele existente (`_boot_ip`, `_boot_all`) se aplică și grantului | cod (neschimbat) |
| Token clasic | rămâne separat; `/status` avertizează `legacy_token_configured` după inițializare | test |
Limite: serverul nu își poate șterge singur `BOOTSTRAP_TOKEN` din configurația containerului (pas de operator; inofensiv după inițializare). Cine deține cheia privată poate inițializa instanța **cât timp e goală**. `/status` (neautentificat) expune `instance_id` și amprenta cheii publice (nu sunt secrete). Imaginea Cloud trebuie reconstruită o dată pentru dependența `cryptography`.

## 2. Backup portabil
| Risc | Control | Verificat |
|---|---|---|
| Arhivă cu cheie/legătură Cloud sau parole | sanitizare la export: legătura Cloud, `cloud_secret_ref`, `users` (hash-uri), coada de sync ⇒ eliminate; stare `UNBOUND` | test (octeți arhivă fără secretul/hash-ul) |
| Arhivă modificată / coruptă / versiune nouă | sha256 + dimensiune per fișier, `integrity_check`, versiune maximă acceptată | test |
| Zip-slip / căi nesigure | `_safe_name`, extragere doar în staging cu căi controlate | test |
| Suprascrierea datelor | import refuză cimitir existent (EXISTS), folder destinație nevid, necesită confirmare UID-uri; duplicate de organizație ⇒ avertisment explicit | teste |
| Eșec la mijloc | staging + mutare atomică + rollback catalog/foldere; `.import-*` curățate | test |
| Copie care moștenește identitatea de sincronizare | registrul importat e `UNBOUND` fără cheie; sincronizarea doar prin Flux B (plan, aprobare, verificare) | test |
Limite: arhiva **nu este criptată** (conține date personale); `users` nu se exportă; nu există semnătură a emitentului arhivei (verifică integritatea, nu autenticitatea sursei).

## 3. RLS / izolare (Livrarea 2)
Rolul de runtime nu este superuser și nu ocolește RLS; fără context ⇒ 0 rânduri; altă organizație ⇒ 0 rânduri; context corect ⇒ exact datele încărcate; INSERT în cimitirul altei organizații respins de politică; UPDATE/DELETE din alt context afectează 0 rânduri (test `test_rls_isolation_pg`, PostgreSQL local — nu Supabase).

## 4. Nesolicitat/neabordat
Provisionare per parohie (Livrarea 3), criptare cu chei ale parohiei, audit independent, teste de penetrare.
