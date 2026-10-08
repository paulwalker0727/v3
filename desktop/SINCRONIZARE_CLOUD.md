# Sincronizare cloud — aplicația desktop existentă ↔ portalul public

Acest modul a fost **adăugat** peste aplicația ta (`cimitir-desktop`, PySide6 + SQLite).
Nu a fost rescrisă nicio funcționalitate existentă: fereastra „Cereri online”, harta,
contractele, înhumații, licențierea și modulul AI rămân exact cum erau.

## Fișiere adăugate
| Fișier | Rol |
|---|---|
| `cloud_sync.py` | Motorul de sincronizare (offline-first). Nu are interfață. |
| `cloud_sync_window.py` | Fereastra „☁ Sincronizare cloud” (configurare + rulare + jurnal). |
| `cloud_sync_auto.py` | Sincronizare automată în fundal: la pornirea programului (după 5s) + la fiecare 5 minute. |
| `ui_mainwindow.py` | *Modificat minim*: un buton nou în bara de sus + metoda `open_cloud_sync_window()`. |

Tabele create automat în baza locală (nu ating schema existentă):
`cloud_sync_map` (legătura cerere locală ↔ cod de urmărire) și
`cloud_sync_outbox` (coada offline pentru deciziile care nu au putut fi trimise).
În `cereri_concesionare` se adaugă doar coloana `tracking_code`.

## Ce se sincronizează
**Desktop → cloud (portal public)**
- **opțional, cu bifă explicită**: numele persoanelor înhumate (doar nume + anii naștere/deces +
  codul locului) pentru **căutarea publică** din portal (`/cauta`). Debifat = nimic public,
  iar la debifare datele deja publicate se șterg automat. CNP-ul și adresele nu pleacă niciodată.
- **fotografia de fundal a hărții** din `settings.map_path` — se urcă automat la prima sincronizare
  și apoi doar când o schimbi (comparare SHA-256). Portalul o afișează sub poligoane, la scara
  reală în pixeli, deci arată exact ca în editorul tău de hartă.
- poligoanele locurilor de veci (`locuri_de_veci.points`), sectoarele (`sectoare`), `px_per_meter`
- pentru fiecare loc: cod, sector, parcelă, rând, număr, tip, capacitate, suprafață, stare
- **fără date personale** — nume, CNP, contracte și înhumați NU pleacă niciodată din program (GDPR)

**Cloud → desktop**
- cererile de concesionare depuse de cetățeni în portal → scrise în `cereri_concesionare`
  cu status `nou`, deci apar direct în fereastra ta „📥 Cereri online”
- în observații se adaugă codul de urmărire și lista documentelor încărcate în portal

**Desktop → cloud (înapoi)**
- decizia operatorului (`validat` / `respins` + nota) urcă în cloud, iar cetățeanul o vede
  cu codul de urmărire pe pagina „Verifică status”

## Configurare (o singură dată)
1. Pornește aplicația → butonul **☁ Sincronizare cloud**
2. Completează:
   - **Adresă server**: URL-ul backendului CimiPlus (ex. `https://ai-play-8.preview.emergentagent.com`)
   - **Cheie sincronizare**: cheia cimitirului (valoarea `SYNC_API_KEY` de pe server)
3. **Testează conexiunea** → **Salvează**
4. Opțional: bifează sincronizarea automată, apoi **Sincronizează acum**

## Offline-first
- Fără internet: sincronizarea eșuează „silențios”, aplicația funcționează normal pe SQLite local.

## Sincronizare automată (fundal)
- Dacă bifa „Sincronizează automat” e activă, `cloud_sync_auto.AutoSync` pornește odată cu
  fereastra principală: prima rulare la 5 secunde după deschidere, apoi la fiecare 5 minute.
- Rulează pe thread separat → interfața nu îngheață niciodată.
- După fiecare rulare se reîmprospătează **badge-ul „📥 Cereri online (N)”** și apare un mesaj
  în bara de status: câte cereri noi au intrat sau, dacă nu e internet, că sincronizarea se reia.

- Deciziile (validat / respins) rămân în `cloud_sync_outbox` și se trimit automat la următoarea
  sincronizare reușită.
- Importul cererilor este deduplicat pe `tracking_code` — o cerere nu poate intra de două ori.

## Endpointuri folosite (backend `/app/backend/routes_sync.py`)
| Metodă | Rută | Rol |
|---|---|---|
| GET | `/api/sync/ping` | test conexiune + cheie |
| POST | `/api/sync/push` | hartă: locuri + sectoare + px/metru |
| POST | `/api/sync/map-image` | fotografia de fundal a hărții (JPG/PNG, max 40 MB) |
| DELETE | `/api/sync/demo-data` | șterge locurile/sectoarele demo din portal |
| GET | `/api/sync/documents/{id}` | descarcă documentul scanat al unei cereri |
| GET | `/api/sync/cereri` | cereri noi din portal (format legacy) |
| POST | `/api/sync/cereri/ack` | confirmare preluare |
| POST | `/api/sync/cereri/{cod}/status` | validat / respins |

Autentificare: header `X-Sync-Key`. Fiecare cimitir are propria cheie și propria bază cloud.

## Test automat (fără interfață grafică)
```bash
python3 /app/tests/test_cloud_sync_e2e.py
```
Verifică: ping, push hartă, cerere depusă în portal → importată local, lipsa duplicatelor,
push status `validat` → `aprobata` în cloud.
