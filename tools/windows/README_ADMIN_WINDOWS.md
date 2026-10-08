# Scurtături Windows pentru administrator

Dublu-click pe fișier, în folderul `tools\windows\`. Nu trebuie să scrii nicio comandă.
Poți face „Trimite la → Desktop (creare comandă rapidă)" pe oricare dintre ele.

## Înainte de prima folosire (o singură dată)

1. Python 3.11 sau 3.12 instalat (bifează „Add python.exe to PATH").
2. Pachetele instalate (vezi `GHID_WINDOWS.md`, secțiunea 1). Dacă ai creat un mediu virtual `.venv` în rădăcina proiectului, scurtăturile îl găsesc singure.
3. Fișierul `.env` în rădăcina proiectului (model: `config\cimitir-online-postgres.env.example`).
   **Parola PostgreSQL stă doar în `.env`**, niciodată în fișierele `.bat` sau `.url`. Nu trimite și nu publica fișierul `.env`.

Portul implicit este **8000**. Dacă folosești altul, scrie în `.env` linia `CIMITIR_PORT=8001`; scurtăturile îl citesc de acolo.

## Ce face fiecare scurtătură

| Fișier | Ce face, pe scurt |
|---|---|
| `START_SERVER.bat` | **Pornește serverul.** Se deschide o fereastră neagră; cât timp e deschisă, serverul merge. Pentru oprire apasă `Ctrl+C` sau închide fereastra. Pornește-l primul. |
| `OPEN_ADMIN.bat` | Deschide în browser panoul de administrare (`/admin`). Te autentifici cu utilizatorul tău. Serverul trebuie să fie pornit. |
| `OPEN_PORTAL.bat` | Deschide portalul public pentru cetățeni (`/portal`). Serverul trebuie să fie pornit. |
| `SERVER_STATUS.bat` | Deschide pagina „Sistem" (`/admin/system`): starea serverului și a bazei de date, ultima sincronizare, operații în așteptare, backup, erori. Doar pentru administratori. |
| `CHECK_DATABASE.bat` | Verifică dacă se poate conecta la PostgreSQL și dacă schema e completă. **Doar citește**, nu modifică nimic și nu afișează parola. La final arată un rezultat: `0` = totul OK, `1` = nu se poate conecta sau configurarea e greșită, `2` = conectarea merge, dar lipsesc migrații. |
| `OPEN_LOGS.bat` | Deschide folderul cu jurnalele aplicației (`cimitir.log`). Parolele și cheile sunt mascate în jurnale. Utile când ceva nu merge și trebuie arătate unui specialist. |
| `CONFIGURE_CLOUD.bat` | Deschide `backend\.env` în Notepad (îl creează din `backend\.env.example` dacă lipsește). Completezi secțiunea „CONFIGURARE CLOUD - SUPABASE”, salvezi și închizi. |
| `CHECK_CLOUD_CONFIG.bat` | Verifică dacă variabilele cloud există și testează conexiunile. Afișează doar stări (`CONFIGURAT`/`NECONFIGURAT`, `ONLINE`/`OFFLINE`), niciodată parola, connection string-ul sau cheia secretă. Cu argumentul `--create-bucket` creează bucketul PRIVAT dacă lipsește. |
| `CREATE_FIRST_ADMIN.bat` | Creează PRIMUL administrator pe o bază nouă, goală. Cere numele și parola de la tastatură (nu se afișează, nu se salvează). Funcționează doar dacă în `users` nu există niciun admin; contul trebuie să-și schimbe parola la primul login. Pe PostgreSQL nu există `admin/admin123`. |
| `RESTART_BACKEND.bat` | Oprește serverul de pe portul configurat (doar dacă e `uvicorn server:app`) și îl pornește din nou într-o fereastră nouă. Necesar după ce modifici `backend\.env`. |
| `OPEN_DATABASE_DASHBOARD.url` | Deschide pagina Supabase în browser, unde vezi baza de date. Vezi secțiunea de mai jos. |

## Configurare cloud Supabase (o singură dată)

1. `CONFIGURE_CLOUD.bat` → completezi `DATABASE_URL` (conexiune directă sau Session Pooler, **port 5432**, cu `?sslmode=require`; NU Transaction Pooler 6543), `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `SUPABASE_BUCKET` (implicit `cimitir-private`). Salvezi.
2. `CHECK_CLOUD_CONFIG.bat` → trebuie să vezi `PostgreSQL: ONLINE`, `Storage: ONLINE`, `Bucket: PRIVAT`.
3. `RESTART_BACKEND.bat` → apoi `SERVER_STATUS.bat` și verifici cardul „Verificare producție”.
Alternativ, din panoul web: **Administrare online → Configurare Cloud** (`/admin/cloud`, doar administratori) scrie aceleași valori în `backend\.env`, cu câmpuri secrete mascate și butoane de test. Necesită autentificare, deci prima configurare (când baza e încă goală) se face cu `CONFIGURE_CLOUD.bat`.
`backend\.env` este ignorat de Git și nu trebuie trimis nimănui. Nu scrie parole în fișiere `.bat`.

## Ordinea obișnuită

1. `START_SERVER.bat` (lasă fereastra deschisă)
2. `SERVER_STATUS.bat` sau `OPEN_ADMIN.bat`
3. Dacă ceva pare în neregulă: `CHECK_DATABASE.bat`, apoi `OPEN_LOGS.bat`

## Problemă frecventă: „Pagina nu se încarcă"

Serverul nu e pornit. Pornește `START_SERVER.bat` și așteaptă câteva secunde, apoi reîncearcă.

## Supabase (`OPEN_DATABASE_DASHBOARD.url`)

- Fișierul conține doar adresa publică a consolei Supabase (`https://supabase.com/dashboard/projects`). Nu conține utilizator, parolă sau token.
- Pentru a sări direct la proiectul tău, deschide fișierul cu Notepad și înlocuiește adresa cu `https://supabase.com/dashboard/project/<ID-ul-proiectului>`. ID-ul se vede în adresa din browser când ai deschis proiectul. Nu pune acolo parole sau chei.
- Dacă nu folosești Supabase, poți șterge fișierul.
- Detalii despre configurarea PostgreSQL: `config\cimitir-online-postgres.env.example` și `ETAPA5_SERVER_POSTGRES.md`.

## Siguranță

- Niciun fișier de aici nu conține și nu cere parole. Conexiunea la baza de date se citește din `.env`.
- `CHECK_DATABASE.bat` rulează doar `scripts\check_postgres.py` (verificare în mod doar-citire).
- Scurtăturile deschid doar `127.0.0.1` (calculatorul curent). Nu expun serverul în rețea.
- `_common.bat` este un fișier ajutător folosit de celelalte; nu îl rula singur.
