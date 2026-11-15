# CIMITIR — macOS: ferestre, hartă și interfață adaptivă (`fix/macos-ui-responsiveness`)

Pornit din `origin/main` (include PR #61 macOS). Nu s-au modificat: baza reală, licențierea, sincronizarea Cloud, infrastructura PROD, schema datelor.
Toate testele au rulat offscreen (`QT_QPA_PLATFORM=offscreen`) pe baze SQLite fictive, fără Cloud.

> **Verificarea vizuală pe un Mac real NU a fost făcută** (nu a existat acces la un MacBook). Comportamentul macOS a fost verificat doar prin
> simulare (platformă simulată + ecran 1280×775 logic + randare offscreen). Vezi „Verificări manuale necesare”.

## 1. Probleme identificate și cauze

| # | Problemă | Cauză reală (măsurată) | Corecție |
|---|----------|------------------------|----------|
| 1 | Harta nu se putea extinde; panoul Overview „mânca” spațiul; separatorul nu se putea muta | `RightPanel` avea lățime **minimă 912 px** (măsurată): tab-ul „Date juridice” punea 6 butoane pe un singur rând (688 px) + meniul lateral fix 200 px. `QSplitter.setSizes([1100, 400])` era ignorat (panoul nu putea scădea sub 912) → la 1500 px harta avea 566 px. Panoul nu era colapsabil. | `FlowLayout` (butoanele trec pe mai multe rânduri când nu încap), tab-urile din panou în `QScrollArea`, meniu 150 px pe macOS → minim panou **544 px (Windows) / 494 px (macOS)**. Splitter nou (`OverviewSplitter`): harta nu se colapsează, panoul da; min. hartă 240 px. |
| 2 | Fereastra principală: `setMinimumSize(1500, 850)` nu încape pe ecrane compacte/„scaled” și blochează micșorarea | Minim fix, independent de ecran | macOS: minim adaptiv **1000×640**, limitat la `QScreen.availableGeometry()`; dimensiune inițială 92% din ecranul disponibil (max 1440×900). **Windows: 1500×850 neschimbat.** |
| 3 | Overview nu se putea ascunde | Nu exista funcție | Buton „◧ Overview” (bara „Instrumente hartă”), scurtătură `Cmd/Ctrl+\`, dublu-click pe separator. Panoul se ascunde (nu se distruge): selecția și datele rămân. La restaurare se revine la lățimea anterioară (limitată la ecran). Harta încadrată se reîncadrează automat. |
| 4 | Separator greu de prins cu trackpad-ul | Mâner de 1 px | macOS: 8 px. Windows neschimbat. |
| 5 | La redimensionare harta nu rămânea încadrată | Fit-ul se aplica doar la încărcare | `refit_if_fitted()`: reîncadrează doar dacă utilizatorul n-a făcut zoom/pan de la ultimul „Fit” (nu pierde zoom-ul ales). Automat la redimensionare doar pe macOS; la ascunderea/restaurarea panoului pe toate platformele. |
| 6 | Trackpad: pan imposibil, zoom necontrolat | `wheelEvent` făcea zoom la **fiecare** eveniment de derulare (derularea cu 2 degete = zoom haotic); pan doar cu butonul din mijloc (inexistent pe trackpad) | macOS: derulare cu 2 degete = **pan** (în orice mod, inclusiv desen), `Cmd`+derulare = zoom, **pinch** = zoom pe poziția degetelor, dublu-tap cu 2 degete = Fit; `Cmd/Ctrl+0` = Fit, `Cmd/Ctrl +/-` = zoom. Rotița unui mouse clasic = zoom, ca înainte. **Windows: rotița = zoom, neschimbat.** Etichetă: „două degete = navighezi oricând”. |
| 7 | Ferestre mai mari decât ecranul | Minime fixe: 1200×700 (Locuri, Înhumați), 1240×800 (AI Scanare), 1300×780 (UserMode), QR Personalizat 1372 px lățime minimă (rând de opțiuni nesfârșit), etc. | `ScreenGuard` (doar macOS): la fiecare afișare reduce minimul/dimensiunea la ce încape pe ecran, aduce fereastra complet în zona vizibilă; reacționează la schimbarea ecranului/rezoluției; nu atinge ferestrele maximizate/fullscreen. QR Personalizat: rând de opțiuni cu `FlowLayout`. |
| 8 | Text tăiat / lățime minimă enormă | Etichete lungi fără word-wrap (ex. „Telefoane mobile”: 1435 px minim) | `ScreenGuard` activează word-wrap pe etichetele ≥ 70 caractere (doar macOS). |
| 9 | Bare de instrumente: 2 bare pe același rând, fiecare cu 2–3 butoane vizibile; meniul de depășire „»” invisibil | Qt le așază pe același rând; QSS `QToolButton{padding:6px 12px}` strivește butonul de extensie la 12 px (săgeata dispare) | macOS: rând separat pentru fiecare bară (`addToolBarBreak`), buton „»” redesenat vizibil. |
| 10 | Etichetă „mijloc = navighezi” fără sens pe Mac | — | Text adaptat pe macOS. |

Verificate și **fără modificări necesare**: HiDPI/Retina (aplicația nu setează `QT_SCALE_FACTOR`/`AA_*HighDpi*` — Qt 6 scalează singur; un test blochează introducerea lor, deci nu există dublă scalare), fonturi/QSS (`theme.py` convertește deja pt→px pe macOS), dialoguri de fișiere (native, `QFileDialog` standard), deschidere fișiere (`open` pe macOS; toate `os.startfile` sunt protejate), scurtături (singura `Ctrl+Delete` din `optional_date_edit`; Qt mapează Ctrl→Cmd pe macOS, la fel `snap` cu Shift/Ctrl în editorul hărții).

## 2. Fișiere modificate / adăugate

Noi: `desktop/screen_utils.py`, `desktop/flow_layout.py`, `desktop/overview_splitter.py`, `desktop/tests/test_ui_responsive.py`, acest document.
Modificate: `desktop/ui_mainwindow.py`, `desktop/map_view.py`, `desktop/panels.py`, `desktop/qr_custom_dialog.py`, `desktop/main.py` (instalare `ScreenGuard`, no-op pe Windows).

## 3. Teste (rulate în sandbox Linux, offscreen, SQLite fictiv)

* Suita `desktop/tests` completă, înainte (origin/main) și după:
  * `origin/main`: 956 passed, 15 skipped, **7 failed + 2 errors**
  * ramura: 983 passed, 15 skipped, **aceleași 7 failed + 2 errors** (+27 teste noi, 0 regresii; lista eșecurilor identică)
  * Cele 9 eșecuri preexistente sunt de mediu (portalul local/serverul nu pornește în sandbox: `test_local_first_launcher`, `test_startup_unification`, `test_windows_guide_flow`) și apar identic pe `origin/main`.
* Teste noi (`tests/test_ui_responsive.py`): dimensiuni min/inițiale Windows vs macOS, ecran 1280×775 și 1100×700, încadrare pe ecran + schimbare ecran, word-wrap, `FlowLayout`, `OverviewSplitter` (ascundere/restaurare/colapsare prin tragere/limite), Fit pe viewport compact, reîncadrare doar când e încadrată, pan/zoom trackpad, pinch, Cmd+scroll, Windows neschimbat, Cmd/Ctrl+0/±, limite zoom, lipsa factorilor manuali de scalare, fereastra principală pe ecran compact (min 1000×640, redimensionare, ascundere Overview păstrând selecția, hartă încadrată), fereastra principală „Windows” (1500×850, toggle), 33 de ferestre/dialoguri încadrate pe ecran compact.
* Testele existente nu au fost modificate.

## 4. Regresii Windows

Nu a existat acces la un Windows real; verificarea a fost făcută pe cod/test, cu platforma ne-macOS:
* Fereastra principală: minim **1500×850**, fără redimensionare inițială, mâner implicit al splitter-ului, `splitter.setSizes([1100, 400])` păstrat (test dedicat).
* `ScreenGuard`, `addToolBarBreak`, pan cu 2 degete, auto-refit la redimensionare, 8 px pe separator, meniu 150 px: **doar macOS**.
* Schimbări comune tuturor platformelor (benefice, dar modifică aspectul): panoul Overview poate fi acum mai îngust (min 544 față de 912 px; tab-uri cu scroll, butoanele „Date juridice” trec pe 2 rânduri când nu încap), panoul se poate ascunde, `Ctrl+0/+/-` pe hartă, rândul de opțiuni din „QR Personalizat” se împarte pe rânduri la lățimi mici. La lățimi mari aspectul e același.
* Rotița mouse-ului pe Windows face zoom exact ca înainte (test).

## 5. Instrucțiuni de testare pe MacBook (Air M5)

```bash
git fetch && git checkout fix/macos-ui-responsiveness
./start-macos.sh --demo     # doar prima dată, bază FICTIVĂ
./start-macos.sh
```
1. Autentificare în baza DEMO. Fereastra trebuie să se deschidă ~90% din ecran; trage de colț/maximizează (butonul verde) și intră în fullscreen (Ctrl+Cmd+F): harta se extinde.
2. Micșorează fereastra până ≈1000×640: nu apare conținut tăiat; barele au „»” pentru comenzile suplimentare.
3. Bara de jos „Instrumente hartă” → **◧ Overview** (sau `Cmd+\`, sau dublu-click pe separator): panoul dispare, harta ocupă tot; apasă din nou: revine cu aceeași selecție/date.
4. Trage separatorul cu trackpad-ul (mâner de 8 px). Panoul nu coboară sub ≈494 px; harta nu sub 240 px.
5. **Fit hartă** (bara de jos, `F`, `Home` sau `Cmd+0`): harta se încadrează; redimensionează fereastra → rămâne încadrată; după zoom/pan manual NU se mai reîncadrează.
6. Trackpad: 2 degete = pan; `Cmd`+2 degete (sau pinch) = zoom; dublu-tap cu 2 degete = Fit. Verifică și în modul „Desenează poligon” (pan fără a adăuga puncte). Click = selectare loc; click dreapta/„2 degete tap” = meniu loc.
7. Deschide pe rând: Locuri, Înhumați, Concesionari, Contracte (+Istoric), Plăți, Amenajări, Copii de rezervă, Centru Cloud, QR/Etichete, QR Personalizat, AI Scanare, Rapoarte: fiecare trebuie să încapă pe ecran, să se poată muta/redimensiona, fără butoane/text tăiate.
8. Mută fereastra pe un monitor extern / schimbă rezoluția (Setări sistem → Ecrane): fereastra rămâne în zona vizibilă.

## 6. Necesită verificare manuală pe Mac

* Aspectul real (Retina, fonturi, QSS, culoarea/vizibilitatea butonului „»”, dimensiunea mânerului de 8 px).
* Evenimentele reale de trackpad: faza derulării, pinch (`QNativeGestureEvent`), dublu-tap cu 2 degete; testele folosesc evenimente sintetice.
* Fullscreen macOS, Spaces, monitor extern, schimbarea rezoluției în timpul rulării, zona sub „notch”.
* Dialoguri native de fișiere și deschiderea fișierelor cu `open`.
* Ferestre care au ajuns pe `ScreenGuard` cu minim redus: la ecrane foarte mici (<1100 px) unele ferestre mari (Locuri, Înhumați, AI Scanare) pot avea conținut parțial tăiat, fără scroll propriu — de verificat pe ecranul real.
* Conținutul din ferestre cu constructori ce cer argumente (Editare loc/sector/poligon, Asociere Cloud etc.) nu a fost randat individual (verificate doar cele din test).

## 7. Alte observații (nemodificate)

* `UserModeWindow` (mod operator) are încă minim 1300×780 setat în cod; pe macOS `ScreenGuard` îl reduce la ecran, dar conținutul (≈1272 px lățime minimă) merită refăcut separat.
* Bara principală are ~30 de comenzi; pe ferestre înguste o parte intră în meniul „»”. O regrupare (meniuri) ar fi o schimbare de interfață mai mare, neinclusă.
* Header-ul panoului Overview are minim ~320 px; sub ≈494 px panoul se ascunde, nu se mai micșorează.
