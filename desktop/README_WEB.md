# Server LAN pentru scanarea de pe telefon

`web_server.py` mai conține doar scanarea documentelor de pe telefon (`/mobile-scan`, `/api/ai-scan-upload`,
`/api/ai-scan-result/{session}`). Portalul public, administrarea web și sincronizarea sunt în `../backend/server.py`
(vezi `../MODURI_RULARE.md`). API-ul operator vechi și interfața React (`web_build/`) au fost eliminate.

* `launcher.py` → „Server local": portal + administrare pe portul ales (implicit 8000) și scanarea de pe telefon pe portul următor (8001).
* Rulare manuală: `python web_server.py` (portul 8001).
* Telefonul și calculatorul trebuie să fie în aceeași rețea; pagina nu are autentificare, așa că serverul se pornește doar în rețele de încredere.
