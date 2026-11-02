# F-01 — Normalizarea formatelor de dată (MAIN · Cloud · Mobile)

## 1. Decizie de format
| Strat | Format | Motiv |
|---|---|---|
| Transport/sincronizare (fir `/entity-sync`, `/sync-v2`) și stocarea **nouă** în Cloud | **ISO `YYYY-MM-DD`** | singurul format înțeles de `<input type="date">` (Mobile) și de PostgreSQL/SQLite |
| Afișare și stocare locală în MAIN | **`zz.ll.aaaa`** (neschimbat) | nu se schimbă nejustificat formatul de afișare din MAIN |
| Valori istorice | rămân cum sunt (zz.ll.aaaa sau ISO), **nu se rescriu** | compatibilitate cu datele vechi |

Regulă generală (modul `shared/cimitir_dates.py`, identic pe toate straturile): o valoare **validă** în orice format cunoscut (`zz.ll.aaaa`, `z.l.aaaa`, `zz/ll/aaaa`, `zz-ll-aaaa`, `zzllaaaa`, `aaaa-ll-zz`) se convertește doar ca text; **goală rămâne goală**; **neinterpretabilă (ex. `31.02.2026`, `circa 1940`) rămâne EXACT cum este** — nu devine data curentă, nu devine goală. Valorile cu oră (`2026-03-05 10:00`) nu se convertesc (ora s-ar pierde).

## 2. Câmpuri de tip dată identificate
| Entitate | Câmpuri | MAIN (SQLite) | Cloud (PG) | Mobile (formular) |
|---|---|---|---|---|
| `contracte` | `data_contract`, `data_inceput`, `data_sfarsit` | text | text | `type=date` |
| `inhumati` | `data_nasterii`, `data_deces`, `data_inhumare`, `data_deshumare` | text | text | `type=date` (fără `data_deshumare`) |
| `plati` | `data_plata` | text (`plati_window` scrie ISO, `panels` scrie zz.ll.aaaa) | text | `type=date` |
| `amenajari_avize` | `data_aviz`, `data_inceput`, `data_finalizare` | text | text | `type=date` |
| Nu sunt „date”, nu s-au atins | `created_at`, `updated_at`, `modified_at` (marcaje de timp) | | | |
Lista este în `cimitir_entities.DATE_COLS`. Alte locuri cu date (nemodificate): filtrele de raport `De la/Până la` (ISO din `<input type=date>`, citite deja tolerant de `pdf_reports`), portalul de teren `teren.js` (convertea deja ISO→zz.ll.aaaa, fără a goli) și `field_import`.

## 3. Defectele reproduse (pe `origin/main`) și corecțiile
| Defect | Cauză | Corecție |
|---|---|---|
| Mobile golește datele MAIN la editare | `<input type=date value="05.03.2026">` e gol în Chromium; `{...cur, ...vals}` trimite `''` | (a) Cloud servește ISO pe fir (`_wire` / `_row_to_wire` → `canon_dates`); (b) formularul Mobile convertește orice format cunoscut în ISO la afișare; (c) o valoare neinterpretabilă apare ca **câmp doar-citire cu mențiune** și nu se trimite (rămâne neatinsă); (d) validare calendaristică reală la salvare |
| Dialogul contractelor MAIN ignoră ISO/gol și salvează **01.01.2000** (nu „data curentă”, cum notase auditul) | `QDateEdit` implicit 2000-01-01; `_populate` înțelegea doar `dd.MM.yyyy` | `OptionalDateEdit` (și în dialogul înhumaților): acceptă toate formatele, gol rămâne gol, text nerecunoscut e păstrat, ziua neschimbată își păstrează textul original; contract nou: aceleași valori implicite ca înainte (azi) |
| Cloud păstra formate amestecate | `clean_data` nu normaliza | `clean_data` convertește în ISO datele valide; `same_data`/`changed_fields` compară pe zi calendaristică (nu apar conflicte/versiuni false din format) |
| MAIN primea ISO și îl scria ca atare | `_wire_to_local` nu converteai | ISO din Cloud → `zz.ll.aaaa` doar dacă ziua e nouă; aceeași zi ⇒ textul local rămâne; gol/nerecunoscut ⇒ neschimbat |
| `TODAY` din Mobile folosea UTC (`toISOString`) | după miezul nopții local dădea ziua precedentă | dată locală (`todayLocal`) |

SW Mobile: cache `cimitir-mobile-shell-v9` → `v10` (telefoanele își reîncarcă codul). Nu s-au adăugat fișiere statice noi (Dockerfile și lista `cimitir_cloud_app._MOBILE_STATIC` rămân neschimbate; funcțiile noi sunt în `mobile_ext.js`).

## 4. Comportament la sincronizare (matrice)
| Scenariu | Rezultat |
|---|---|
| MAIN creează cu `05.03.2026` → Cloud | stocat `2026-03-05`; telefonul vede `2026-03-05` |
| Rând istoric în Cloud cu `05.03.2026` → telefon | servit `2026-03-05`; **stocarea nu se rescrie** la citire; la editare cu aceeași zi nu se scrie nimic pentru acel câmp |
| Telefon editează alt câmp | toate datele existente se păstrează (verificat în Chromium) |
| Telefon șterge explicit o dată | devine goală (decizia utilizatorului) |
| Valoare nerecunoscută (`circa 2036`) | vizibilă, doar-citire în Mobile; păstrată în Cloud și în MAIN |
| Offline → coadă → reconectare | operațiunea din coadă conține ISO validat; aceleași reguli la reconectare |
| Cloud → MAIN, zi schimbată | MAIN primește `zz.ll.aaaa` |
| Client vechi (v9) fără câmpul în payload | neschimbat (cheie lipsă ≠ gol); clientul vechi care trimite `''` rămâne o ștergere explicită (nu se poate distinge server-side) — **de aceea SW v10 forțează actualizarea** |

## 5. Riscuri rămase / ce NU s-a făcut
* Telefon real (iOS Safari/Android Chrome) și Windows real: nevalidate de mine (doar Chromium headless + Qt offscreen).
* O PWA veche (v9) rămasă în cache poate încă goli date până la reîncărcare (necesită o reîncărcare cu internet). Server-side nu se poate distinge golirea intenționată de cea accidentală.
* `plati_window` continuă să scrie ISO în MAIN (format amestecat intern; tratat corect la sincronizare). Nu l-am schimbat (ar fi modificare de afișare nesolicitată).
* `AddAvizDialog` (parametrul `existing`, nefolosit de niciun apelant) a fost doar făcut tolerant la formate; o dată invalidă ar fi încă înlocuită cu azi dacă dialogul ar fi folosit pentru editare — se rezolvă în ETAPA 2 (editare aviz completă).
* `manifest`/`transfer` (Flux B) calculează hash-uri pe valori brute; nu s-au modificat (ar schimba amprentele).
* `qr_public_data.py` / `qr_custom_generator.py` parsează `data_sfarsit` doar ca ISO ⇒ pentru contracte MAIN statusul public nu se calculează (aparține F-03 / urmărire separată).

## 6. Teste
`backend/tests/test_f01_dates_unit.py` · `test_f01_mobile_dates_js.py` (Node) · `test_f01_dates_sync_pg.py` și `test_f01_dates_pwa.py` (PG local + Chromium) · `desktop/tests/test_f01_main_dates.py` (SQLite izolat + Qt offscreen). Un singur test existent a fost actualizat conștient (`test_avize_complete_028.py`: ISO sosit din Cloud cu ziua schimbată se stochează acum zz.ll.aaaa în MAIN) — comentat în cod.
