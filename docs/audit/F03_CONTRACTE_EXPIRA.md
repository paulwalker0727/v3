# F-03 — `expiring_contracts()` nu vedea contractele create în MAIN

## Formatul real al datelor
* MAIN (dialogul contractelor, `contracts_window.py`) scrie `data_sfarsit` ca **`zz.ll.aaaa`** (ex. `10.03.2026`); contractele sosite din Cloud/Mobile pot fi **`aaaa-ll-zz`**; unele valori istorice au spații sau oră (`2026-03-05 10:00:00`).
* Funcția SQLite `date('10.03.2026')` întoarce **NULL** (înțelege doar ISO) ⇒ condiția `date(data_sfarsit) <= date('now', '+N days')` era falsă pentru toate contractele create în MAIN: nici expirate, nici „expiră curând” nu apăreau (alerte, PDF „Contracte care expiră”, colorarea hărții).
* În PostgreSQL (panoul web/Cloud) regexul `^[0-9]{4}-…` excludea aceleași contracte, iar `::date` ar fi putut pica pe tot setul la o valoare neinterpretabilă.

## Corecția
* `shared/cimitir_database.py::expiring_contracts(days)` și `backend/cimitir/postgres_store.py::expiring_contracts(days)`: se citesc rândurile cu `data_sfarsit` nevidă, data se interpretă în Python cu `cimitir_dates.parse_date(…, allow_time=True)` (`zz.ll.aaaa`, `z.l.aaaa`, `zz/ll/aaaa`, `zz-ll-aaaa`, `zzllaaaa`, `aaaa-ll-zz`, ISO cu oră), se compară cu `azi + days` (azi = data locală; **limita este inclusă**, ziua curentă este inclusă — „expiră azi”), rezultatul este sortat după **ziua reală** (nu după text) și apoi după `id`.
* Fiecare rând returnat primește `_expira` = ziua ISO (`aaaa-ll-zz`) și `_expira_raw` = textul original; `_date_col` neschimbat. Astfel consumatorii (PDF, alerte) nu mai depind de formatul din bază.
* Valorile **goale sau neinterpretabile nu se ghicesc**: nu apar în rezultat (nici ca expirate, nici ca viitoare); ele **nu se modifică în bază**. (Verificările de integritate DAT00x din Pasul B le raportează separat.)
* `desktop/alerts_window.py`: starea EXPIRAT / „expiră curând” se calcula din `date.fromisoformat(coloană)` ⇒ pentru `zz.ll.aaaa` rămânea „activ”; acum folosește `_expira` (afișarea coloanei rămâne neschimbată).
* PDF-ul „Contracte care expiră” folosește `_expira` (ISO) — fără modificări de cod.
* `date_col` este tot `data_sfarsit` (nu se mai caută altă coloană; comportament neschimbat).

## Teste
`desktop/tests/test_f03_expiring_contracts.py` (SQLite izolat, ziua fixată 2026-03-10): `DD.MM.YYYY` vs `YYYY-MM-DD`, expirat / azi / +5 / limita +30 / limita +31, ordonare, an nou, 29 februarie valid/invalid, date invalide (31.02, text, an cu 2 cifre, lună 13, gol, spații), ISO cu oră, datele din bază nemodificate, fereastra de alerte și PDF-ul pe date MAIN.
`backend/tests/test_f03_expiring_contracts_pg.py` (PostgreSQL local, `CIMITIR_TEST_DATABASE_URL`): paritate PostgreSQL ↔ SQLite pe același set.

## Riscuri / lacune rămase
* Ziua curentă este cea a mașinii care rulează (MAIN: PC-ul; portal: serverul) — fusul orar al serverului poate muta granița cu o zi la miezul nopții.
* `qr_public_data.py` și `qr_custom_generator.py` parsează încă `data_sfarsit` doar ca ISO (statusul public al contractelor MAIN nu se calculează) — **nu au fost modificate** (în afara F-03; propus pentru ETAPA următoare).
* Contractele cu dată neinterpretabilă nu generează alertă; recomandat să fie corectate manual după raportul Pasului B.
