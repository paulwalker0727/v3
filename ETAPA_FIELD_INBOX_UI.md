# MAIN — Field Inbox + Phone Pairing UI (PySide6)

Leagă în MAIN profilul Cloud activ (`cloud_profiles.py`) cu Field Inbox v1 și Phone Pairing v1 existente. Fără backend nou, fără migrare 020, fără API nou.

## Componente (desktop/)
- `field_inbox_client.py` — client HTTPS fără Qt: `Authorization: Binding <cheie>` din profilul activ (SecretStore); list/get/accept/reject, URL semnat, pairing code/phones/revoke; logică de prezentare (rânduri, preview, expirare cod).
- `field_worker.py` — `QThread` pentru cereri HTTP (UI nu se blochează; fereastra închisă = rezultat ignorat).
- `field_inbox_window.py` — „Elemente din teren”: filtre PENDING (implicit) / ACCEPTED / REJECTED / IMPORTED, detalii, preview JPEG/PNG/PDF (QtPdf dacă există), WORK_PHOTO BEFORE/DURING/AFTER, Acceptă / Respinge (motiv opțional; gol => „Respins de operator”, serverul cere motiv).
- `field_devices_window.py` — „Telefoane / Dispozitive teren”: cod MOBILE (afișat doar în memorie, cu expirare), telefoane asociate + cimitir, revocare.
- `ui_mainwindow.py` — acțiuni în toolbar: `📱 Elemente din teren [N]` (badge, verificare la pornire + la 60 s, în thread; mesaj „Ai N elemente noi din teren”) și `📶 Cloud / Telefoane`.
- `cloud_sync.validate_key` acceptă și forma cheii de legătură `<id>.<uuid>.<secret>` (profilul trebuie să poată stoca cheia MAIN folosită de Field Inbox); forma veche rămâne validă.

## Comportament
- Fără profil activ / URL / cheie de legătură: „Cloud neconfigurat” + buton „Configurează Cloud/Server” (dialogul existent).
- API offline / 5xx / redirect: „Cloud indisponibil”; MAIN pornește și lucrează local normal.
- UI permite doar PENDING -> ACCEPTED | REJECTED (verificare locală + serverul rămâne arbitrul). Nu există apel `imported`; ACCEPTED = aprobat pentru import local ulterior (importul în SQLite nu e implementat aici).
- Accept/Respinge cer dreptul `approve`; generarea codului și revocarea cer `manage_config`.
- URL-ul semnat: cerut la momentul preview-ului, descărcat fără autentificare, verificat (mărime, SHA-256, semnătură MIME), nu se salvează/loghează.
- Mesajele de eroare sunt texte fixe; MAIN nu primește parola PostgreSQL / SUPABASE_SECRET_KEY / service_role.

## Teste
`python -m pytest desktop/tests/test_field_inbox_main.py` — rulează routerele reale `cimitir_field_inbox_http` / `cimitir_phone_pairing_http` in-process peste un serviciu in-memory (aceleași tranziții). PostgreSQL/Storage LIVE: nerulat. PRODUCTION blocat în teste.
