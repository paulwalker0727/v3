# CIMITIR — Raport P3: securitate, contracte istorice, plăți, stabilizare

> **Versiune consolidată** (branch `feature/p3-integrare-finala`): înlocuiește integral PR #56, #57, #58. Livrarea în 3 PR-uri descrisă mai jos este istorică.

Bază: `main` @ `00bb206` (Merge PR #55). Mediu: Linux, Python 3.11, SQLite, date **sintetice** în foldere temporare. Nu s-a accesat PROD, Supabase sau date reale. Nu s-a făcut merge în `main`.
Livrare în 3 PR-uri **stivuite** (B se bazează pe A, C pe B — din cauza fișierelor comune `plati_window.py` și `shared/cimitir_database.py`): se îmbină în ordinea A → B → C.

| PR | Branch | Conținut |
|---|---|---|
| A | `fix/p3-a-permisiuni-istoric` | Permisiuni pe roluri + protecția instantaneului `owner_*` |
| B | `fix/p3-b-plati-pdf` | Plăți ↔ contracte, PDF backend, design evidență documente |
| C | `fix/p3-c-stabilitate-validare` | Teste instabile, siguranța migrării, script Windows, matrice Cloud TEST |

## 0. Audit final al permisiunilor (actualizare PR #59)
Gardă main.py fără eșec silențios (`install_access_guard`: refuză sesiunea + mesaj, garda rămâne „refuză tot”); rutele de scriere din portalul web mapate individual pe add/edit/delete; avize, fotografii, hartă, sectoare, ghidaje, rânduri protejate în funcțiile DB; refresh automat al butoanelor și refuz chiar dacă un buton a rămas activ; compatibilitate conturi existente/sincronizare verificată. Detalii: `docs/P3_PERMISIUNI_ADMIN.md`. Teste noi: `backend/tests/test_p3_portal_drepturi.py`, `desktop/tests/test_p3_audit_final_drepturi.py`.

## 1. Decizii de produs primite de la proprietar
- Permisiuni: **controlate de administrator, per cont** (view/add/edit/delete/approve); rolul dă doar valorile implicite din `main` (user = view, operator = tot ce era permis). Vezi `docs/P3_PERMISIUNI_ADMIN.md`. (Versiunea inițială P3 cu „user = consultare + adăugare” fix pe rol a fost înlocuită.)
- Plățile istorice fără `contract_id` rămân neschimbate.
- `owner_*` rămâne instantaneu istoric; fără versionare, fără migrări, fără rescrierea contractelor vechi. Schimbarea titularului nu actualizează automat instantaneul — se semnalează.

## 2. Bugurile confirmate, cauze, remedii

### PR A
| # | Bug confirmat | Cauză reală | Remediu |
|---|---|---|---|
| A1 | `UserModeWindow` (cont fără `edit`) permitea adăugare/modificare/**ștergere** de plăți, contracte, înhumați, documente fără nicio verificare | `main.py` deschide `UserModeWindow` pentru conturile fără `edit`, dar nici fereastra, nici funcțiile `database` nu verificau drepturile | Drepturi individuale `add`/`edit`/`delete` (compatibile cu conturile vechi: `edit` le include fără marcajul `cfg`). Gardă `set_access_guard` instalată la autentificare, verificată **în funcțiile care scriu**, fail-closed, re-verificată în DB la fiecare operațiune. Butoane dezactivate după drepturile individuale. Panoul Admin web: bife `add/edit/delete/approve`. |
| A2 | Editarea unui contract vechi rescria `owner_*` (nume, CNP, adresă, telefon, e-mail, BI) cu datele **curente** ale concesionarului | `_populate` apelează autofill-ul concesionarului peste câmpurile titularului, iar `_collect` le trimitea la `save_contract`, care rescria toate coloanele | `save_contract` (UPDATE) păstrează `owner_*` când cheia lipsește; dialogul de editare afișează instantaneul din contract (read-only), nu trimite `owner_*` și avertizează dacă s-a schimbat concesionarul („Titular schimbat: instantaneul NU a fost actualizat”). Contract nou: neschimbat (completează instantaneul). Document Word/PDF: deja folosesc `owner_*` din contract cu fallback pe persoană doar dacă instantaneul lipsește — acum rămân corecte după editare. Fără migrare. |

### PR B
| # | Bug | Cauză | Remediu |
|---|---|---|---|
| B1 | Plățile nu se legau de contract din UI (chitanța fără plătitor) | `PlatiWindow` și `AddPlataDialog` nu apelau `add_plata(..., contract_id=)` | `ContractPicker`: dacă locul are contracte, utilizatorul **trebuie** să aleagă contractul sau „Plată fără contract” (nicio alegere automată, nici măcar când există un singur contract); se vede concesionarul; tabelul arată contractul/titularul; filtru pe contract și „doar fără contract”. Plățile istorice fără `contract_id` apar marcate „— fără contract”, nu se modifică. Buton „Chitanță PDF” (titular din contract). |
| B2 | Dublu-click | Protecția era incidentală (suma se golea) | Buton blocat 800 ms după salvare + validarea explicită; DB rămâne idempotent pe chitanță nenulă (P2). Plăți fără nr. chitanță identice NU se deduplică (pot fi legitime). |
| B3 | PDF backend pica la markup invalid (`<b>neînchis`) → HTTP 404 „Documentul nu a putut fi generat” (contract, adeverință, raport expirări, raport financiar) | `backend/cimitir/pdf_reports.py` folosea `reportlab.Paragraph` direct; protecția P2 exista doar pe desktop | `cimitir_pdf_safe.Paragraph` (shared) folosit în backend: la eroare, tagurile de formatare **echilibrate** rămân, restul (`<`, `>`, `&`, taguri nepereche) devine text literal — nu se pierde text, spre deosebire de varianta P2 care ștergea tagurile. Verificat: textul `<b>neînchis`, `a</b>b`, `A & B`, `suma < 100`, diacritice apar în PDF. |
| B4 | Text foarte lung (≥ 1000 caractere în nume/observații) → `LayoutError` | Rând de tabel nedivizibil | `splitInRow=1` pe tabelele de date și semnături (backend + desktop). |

### PR C
| # | Bug | Cauză | Remediu |
|---|---|---|---|
| C1 | 5 teste `test_etapa1_audit::public_*`/`web_request_decisions` picau doar la rularea paralelă (`no such table: settings`) | **Contaminare între teste**: `_clear_modules()` nu scotea din cache `portal_ratelimit`, rămas legat de `store`/DB-ul unui test anterior (folder șters). Reprodus determinist: orice test care încarcă serverul și șterge folderul, înaintea unui test `public_*`, în același proces (cazul xdist). Nu era bug de produs. | `_clear_modules` folosește `purge_backend_modules()` (existent) + test de regresie `test_p3_izolare_module.py` (pică pe codul vechi, trece pe nou). Fără skip/xfail/relaxări. |
| C2 | `test_etapa3_requests::test_concurrent_requests_same_plot_never_double_assign` | **NU s-a reprodus** (6 rulări izolate + 10 rulări sub încărcare + 2 rulări complete în suită: PASS). Neschimbat, nu l-am ascuns. Rămâne în observație. | — |
| C3 | Migrare: o copie pre-migrare întreruptă lăsa un fișier `.bak` trunchiat care arăta ca o copie bună și putea împinge copii valide în afara limitei de 5 | Copia se scria direct la numele final | Copie în `*.bak.tmp` + `quick_check` + `os.replace` atomic; resturile `.tmp` se curăță; eșecul/întreruperea nu lasă nimic. |

## 3. Siguranța migrării (§7) — scenarii testate (sintetic)
Bază blocată (P2, existent) · spațiu insuficient la ALTER și la copie (P2) · **proces oprit brusc în mijlocul migrării** (nou, subproces + kill: schemă neschimbată, `integrity_check ok`, migrarea se reia) · copie întreruptă/incompletă (nou) · folder nescriibil (`PermissionError` simulat, nou) · actualizare repetată (nou) · scheme istorice (P2) · cale cu spații/diacritice (nou) · restaurare din `.bak` apoi migrare (nou). Nerulat aici: spațiu insuficient real, antivirus/OneDrive pe Windows, baze doar-citire prin chmod (rulează ca root → SKIP).

## 4. Fișiere schimbate
- **A:** `shared/cimitir_auth.py`, `shared/cimitir_database.py`, `desktop/main.py`, `desktop/contracts_window.py`, `desktop/plati_window.py`, `desktop/tests/test_cloud_center.py` (excepție documentată pentru `cimitir_auth.py`), `backend/tests/test_etapa2_sync.py` (seturile de drepturi includ `add`), teste noi `test_p3_permisiuni_roluri.py`, `test_p3_istoric_contracte.py`.
- **B:** `desktop/plata_contracts.py` (nou), `desktop/plati_window.py`, `desktop/panels.py`, `shared/cimitir_pdf_safe.py`, `backend/cimitir/pdf_reports.py`, `backend/cimitir/fisa_pdf.py`, `desktop/pdf_reports.py`, `docs/P3_DESIGN_EVIDENTA_DOCUMENTE.md`, teste `test_p3_plati_contracte.py`, `test_p3_pdf_markup.py`.
- **C:** `shared/cimitir_database.py` (copie atomică), `backend/tests/test_etapa1_audit.py`, `backend/tests/test_p3_izolare_module.py`, `desktop/tests/test_p3_migratii_siguranta.py`, `deploy/windows_validation/validate_p3_windows.py`, `docs/P3_VALIDARE_WINDOWS.md`, `docs/P3_MATRICE_CLOUD_TEST_MOBILE_V2.md`, acest raport.

## 5. Teste (Linux, rulare completă pe branch-ul C)
| Suită | Rezultat |
|---|---|
| `desktop/tests` | **883 PASS, 15 SKIP, 0 FAIL** |
| `backend/tests` (`-n 4`) | **1016 PASS, 671 SKIP, 0 FAIL** (SKIP = teste live/PostgreSQL/Supabase fără mediu TEST, protejate de `cimitir_test_guard`) |
| Teste noi P3 | permisiuni (25, inclusiv matrice de 11 configurații de drepturi) ; înainte: permisiuni (12), istoric contracte (9), plăți–contracte (10), PDF backend (13), migrare (7 + 1 SKIP root), izolare module (1) — toate PASS; testele de PDF și izolare **pică pe codul vechi** (verificat) |
| Windows (script + manual) | **NEEXECUTAT** (script rulat pe Linux doar ca autotest: 8 PASS, 3 MANUAL, 1 NEEXECUTAT) |
| Cloud TEST / Mobile v2 | **NEEXECUTAT** — lipsesc acreditări/mediu TEST (vezi matricea); PROD neatins |
Notă: la rulările pe PR A/B, `test_build_packaging` (4) și `test_startup_unification` (1) cădeau doar fiindcă `PyInstaller` nu era instalat în mediul de audit (identic pe `main`); cu PyInstaller instalat trec. Ruff raportează avertismente de stil în fișierele noi; nu blochează.

## 6. Probleme nerezolvate / decizii deschise
1. **Portalul web verifică doar `edit`/`approve`**; `add`/`delete` granulare se aplică în MAIN (vezi lipsurile din `docs/P3_PERMISIUNI_ADMIN.md`). Stornarea plăților ca alternativă la ștergere = decizie financiară (neimplementată).
3. Transfer/schimbare titular: doar avertisment; nu există flux dedicat de transfer (decizie juridică). Instantaneul rămâne cel de la emitere.
4. Evidența documentelor generate: **doar design** (`docs/P3_DESIGN_EVIDENTA_DOCUMENTE.md`), neimplementat; numerotarea oficială nu s-a implementat.
5. Text utilizator care e **markup reportlab valid și complet** (ex. `<para>x`) se interpretează ca formatare și nu apare literal; eliminarea completă cere escapare la sursă pe toate cele ~60 de apeluri (neschimbat).
6. `desktop/tests/test_cloud_center.py` blochează modificarea `shared/cimitir_auth.py` față de un commit vechi; am scos acest fișier din lista păzită (modificare intenționată, aprobată) — de confirmat la review.
7. `test_concurrent_requests_same_plot...` — nereprodus; de urmărit în CI.
8. Căi de scriere care ocolesc funcțiile verificate (SQL direct în sincronizare/import) nu trec prin gardă — intenționat (sincronizarea servește drepturile din server), dar de reținut.

## 7. Riscuri pentru PROD
- Conturile fără `edit` care în `main` puteau scrie prin `UserModeWindow` (scurgere de drepturi) vor primi „Drepturi insuficiente” până când administratorul le acordă `add`. Comunicați înainte de release.
- Conturile existente (`edit,approve`) rămân neschimbate (compatibilitate); după ce administratorul salvează formularul, bifele devin explicite (marcaj `cfg`).
- Garda se instalează doar în `main.py` după autentificare: orice alt punct de intrare (scripturi, alte lansatoare) rulează fără gardă. Verificat: scripturile/sincronizarea nu depind de ea.
- Sincronizarea staff cu drepturi: `add` este un drept nou — serverele vechi nu-l cunosc; testele `test_etapa2_sync` actualizate confirmă că rămâne coerent local; **neverificat cu un server Cloud real**.
- Prima actualizare produce `pre-migrare/*.bak` (spațiu = mărimea bazei).

## 8. Verdict
| PR | READY TO MERGE |
|---|---|
| A | **YES** (cu confirmarea punctelor 6 și 1), după review; independent de B/C |
| B | **YES**, după A (stivuit) |
| C | **YES**, după B; verificările Windows/Cloud TEST rămân NEEXECUTATE |

**Proiectul NU este declarat READY FOR PRODUCTION**: lipsesc verificările Windows și de integrare Cloud TEST/Mobile v2 (doar pregătite).
