# Cum faci un `.exe` și un instalator pentru Cimitir Desktop

Există **două căi**, amândouă deja configurate în proiect. Poți folosi oricare.

> **De ce nu pot fi construite pe Emergent?** Un executabil Windows se poate
> compila **doar pe Windows**. Scripturile sunt pregătite, compilarea se face
> fie pe GitHub (mașină Windows gratuită), fie pe calculatorul tău.

---

## Ce vei obține

| Fișier | Ce e | Când îl folosești |
|---|---|---|
| `CimitirSetup-1.0.0.exe` | Instalator adevărat | Instalare la client: scurtătură pe Desktop + Start Menu, dezinstalare din Panou de Control |
| `Cimitir-portabil.exe` | Un singur fișier | Îl pui pe stick, pornește de oriunde (5-15 sec la pornire) |
| `dist\Cimitir\Cimitir.exe` | Folder cu exe + fișiere | Testare rapidă, pornire instantă |

**Datele nu se pierd niciodată la update sau reinstalare** — aplicația le ține separat:
- `%LOCALAPPDATA%\Cimitir` → baza de date `cimitir.db` + documentele scanate (`media`)
- `%APPDATA%\CimitirDesktop` → licența + cheia AI criptată (`ai_config.json`)

---

## Calea 1 — Automat pe GitHub (recomandat, nu instalezi nimic)

1. Încarcă codul pe GitHub (butonul **„Save to Github"** din chat).
2. Pe GitHub, intră în tab-ul **Actions** → în stânga alege
   **„Construiește Cimitir pentru Windows"** → butonul **„Run workflow"**.
3. Aștepți ~8-12 minute. Când apare bifa verde, deschide rularea și descarcă
   din secțiunea **Artifacts**:
   - `CimitirSetup-1.0.0` → instalatorul
   - `Cimitir-portabil-1.0.0` → varianta portabilă

### Ca să publici automat o versiune (Release)
```bash
git tag v1.0.0
git push --tags
```
GitHub construiește singur și creează un **Release** cu ambele fișiere atașate,
de unde clienții pot descărca direct.

Workflow-ul e la `.github/workflows/build-windows.yml` și găsește automat folderul
aplicației, indiferent dacă e `scanareqr1/cimitir-desktop` sau `cimitir-desktop`.

---

## Calea 2 — Local, pe calculatorul tău Windows

**Ai nevoie o singură dată de:**
- [Python 3.11 sau 3.12](https://www.python.org/downloads/windows/) — bifează
  **„Add Python to PATH"** la instalare
- [Inno Setup 6](https://jrsoftware.org/isdl.php) (gratuit) — doar dacă vrei și instalatorul

**Apoi:**
1. Deschide folderul `cimitir-desktop`
2. Dublu-click pe **`build_exe.bat`**
3. Aștepți 3-8 minute (prima rulare descarcă dependințele)

Rezultatele apar în `dist\` și `dist_installer\`.

---

## Verificarea automată de update

La pornire, aplicația întreabă o singură dată GitHub Releases dacă există o versiune
mai nouă (fără cheie API, cerere publică). Dacă da, apare o bară verde discretă sus:
„⬆ Versiunea 1.1.0 este disponibilă — **Descarcă**". Fără internet sau fără release-uri,
nu se întâmplă nimic — pornirea nu e afectată niciodată.

Repository-ul verificat este `paulwalker0727/qrwork`. Îl poți schimba fără recompilare:
```bat
set CIMITIR_UPDATE_REPO=alt-utilizator/alt-repo
```

Ca să apară notificarea la clienți, publică versiunile cu tag-uri semantice
(`v1.1.0`) — vezi secțiunea cu Release de mai sus. Versiunea locală se citește din
`security/config.py` → `APP_VERSION`.

---

## Ghidul PDF pentru client

Regenerează ghidul de instalare oricând:
```bat
python tools\make_client_guide.py
```
Rezultat: `docs\Ghid_Instalare_Cimitir.pdf` (instalare, unde stau datele, activare
licență, conturi implicite, obținerea și configurarea cheii Gemini, probleme frecvente).
Folderul `docs/` intră automat în pachet, iar instalatorul creează o scurtătură către
ghid în meniul Start.

---

## Semnare digitală (elimină avertismentul „Editor necunoscut")

Fără semnare, Windows SmartScreen afișează un avertisment la prima instalare.
Ai nevoie de un certificat **code-signing** (OV sau EV) de la Sectigo, DigiCert,
Certum etc.

### Local (`build_exe.bat`)
Deschide `cmd` în folderul `cimitir-desktop` și rulează:
```bat
set CIMITIR_CERT=C:\certificate\firma.pfx
set CIMITIR_CERT_PASS=parola_ta
build_exe.bat
```
Dacă certificatul e deja instalat în magazia Windows (sau e pe token USB):
```bat
set CIMITIR_CERT_SUBJECT=Numele Firmei SRL
build_exe.bat
```
Scriptul găsește singur `signtool.exe` din Windows SDK. Dacă nu e instalat,
build-ul continuă nesemnat și te anunță.

### Pe GitHub
În repo: **Settings → Secrets and variables → Actions → New repository secret**
| Nume secret | Valoare |
|---|---|
| `WINDOWS_CERT_BASE64` | certificatul `.pfx` codificat base64 |
| `WINDOWS_CERT_PASSWORD` | parola certificatului |

Ca să obții base64-ul certificatului, în PowerShell:
```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\certificate\firma.pfx")) | Set-Clipboard
```
Dacă secretele lipsesc, workflow-ul sare peste semnare și construiește normal.

> Certificatele pe **token USB / HSM** (obligatoriu pentru OV din 2023) nu pot fi
> folosite pe GitHub Actions — semnează local, cu `CIMITIR_CERT_SUBJECT`.

---

## Ce se include în pachet

`cimitir.spec` împachetează: `fonts/` (PDF-uri și etichete), `templates/`
(contract Word), `portal/` (portal + administrare web din `backend/`),
`icon.ico`, `LICENSE.txt` și `docs/`.

**Intenționat NU se includ:**
- `emergentintegrations` / `litellm` — rezerva internă de AI; clientul folosește
  cheia lui Gemini din **⚙️ Setări AI** (ar fi adăugat ~200 MB degeaba)
- **Tesseract OCR** — modul offline are nevoie de el instalat separat
  ([descărcare](https://github.com/UB-Mannheim/tesseract/wiki), bifează limba
  română). Fără Tesseract, aplicația funcționează normal cu AI Gemini.
- Module Qt nefolosite (WebEngine, Qml, Quick, 3D, Charts...) — scad pachetul
  de la ~450 MB la ~150 MB

Punctul de intrare este `launcher.py` (fereastra care întreabă „doar local" sau
„server în rețea"), pentru că e singurul modul deja pregătit pentru `sys.frozen`.

**Portalul + administrarea web** (`backend/server.py`) sunt incluse sub `portal/` și pornesc ca proces propriu:
`Cimitir.exe --portal --host 127.0.0.1 --port 8000` (lansat automat de launcher). Instalatorul Inno Setup copiază
tot folderul `dist\Cimitir\`, deci include și portalul. Detalii: `../MODURI_RULARE.md`.

### Dimensiuni așteptate

| Livrabil | Mărime |
|---|---|
| `dist\Cimitir\` (folder) | ~370-400 MB |
| `Cimitir-portabil.exe` | ~150-180 MB |
| `CimitirSetup-1.0.0.exe` | ~140-170 MB |

Cei mai mari contributori sunt PySide6 (~120 MB), PyMuPDF (~50 MB),
OpenCV (~85 MB) și NumPy (~40 MB).

**Variantă slabă (~85 MB mai puțin)** — scoate OpenCV, folosit doar la
preprocesarea imaginilor pentru OCR-ul offline (care oricum are nevoie de
Tesseract instalat separat):
```bat
set CIMITIR_SLIM=1
build_exe.bat
```
Aplicația funcționează normal cu AI Gemini; dacă lipsește OpenCV, OCR-ul offline
folosește imaginea neprelucrată în loc să crape.

---

## Schimbarea versiunii

Versiunea se citește automat din `security/config.py`:
```python
APP_VERSION = "1.0.0"
```
Modifică acolo și actualizează și `installer/file_version_info.txt`
(`filevers`, `prodvers`, `FileVersion`, `ProductVersion`) ca să apară corect în
proprietățile fișierului `.exe`.

---

## Probleme frecvente

| Simptom | Cauză / rezolvare |
|---|---|
| `ModuleNotFoundError` la pornirea exe-ului | Modul importat târziu, lipsă din pachet. Adaugă-l în lista `hiddenimports` din `cimitir.spec` și reconstruiește |
| Exe-ul pornește și se închide instant | Construiește temporar cu consolă: în `cimitir.spec` pune `console=True`, rulează din `cmd` și citește eroarea |
| Antivirusul șterge exe-ul | Fals pozitiv tipic PyInstaller. Semnarea digitală rezolvă; alternativ, adaugă o excepție |
| Fonturile lipsesc din PDF-uri | Verifică că folderul `fonts/` a ajuns în `dist\Cimitir\_internal\fonts` |
| Baza de date pare goală după instalare | Normal — exe-ul folosește `%LOCALAPPDATA%\Cimitir\cimitir.db`. Copiază acolo `cimitir.db` din surse dacă vrei datele existente |
