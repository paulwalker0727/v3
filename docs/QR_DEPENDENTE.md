# QR — dependențe și verificarea mediului

- **Generarea** QR (aplicația, PDF-uri, etichete) folosește `qrcode[pil]` și `segno` din `desktop/requirements.txt`. Nu depinde de libzbar.
- **Decodarea** (doar în teste: `desktop/tests/test_qr_scanning.py`, `test_qr_custom_and_map.py::test_qr_decodes_back`) folosește `pyzbar`, care are nevoie de biblioteca nativă **libzbar**. Nu este cod de aplicație.

Cauza „testele QR nu rulează": mediu/instalare (nu cod). Lipsa lui `pyzbar` dădea SKIP; lipsa lui libzbar cu `pyzbar` instalat dădea `ImportError: Unable to find zbar shared library` la colectare. Acum ambele cazuri dau SKIP cu motiv explicit.

| Platformă | Instalare |
|---|---|
| Windows | `python -m pip install pyzbar` (roata include `libzbar-64.dll`; dacă se încarcă greu: Visual C++ Redistributable 2013 x64) |
| Linux (Debian/Ubuntu) | `sudo apt-get install libzbar0` + `python -m pip install pyzbar` |
| macOS | `brew install zbar` + `python -m pip install pyzbar` |

Verificare: `python scripts/check_qr_env.py` (exit 0 = OK). Windows real: NECONFIRMAT (verificat doar pe Linux).
URL-ul din QR pentru un loc este `<qr_base_url>/public/loc/<cod_loc>` (redirecționează spre portal); testele vechi așteptau `/loc/…` și erau învechite.
