# Portal mobil local (LAN): telefon + MAIN pe aceeași bază

```
MAIN (PySide6) ─┐
                ├─ același SQLite (registrul activ) + aceleași fișiere media
Telefon → portal local FastAPI (/admin) ─┘
```

Fără bază separată, fără Supabase, fără cloud. Ce faci din telefon apare în MAIN la reîncărcarea dosarului.

## Pornire
1. În MAIN/launcher alege **Acces telefon în rețea** (opt-in, cu confirmare). Implicit rămâne `127.0.0.1`.
   CLI: `python desktop/start_local.py --host 0.0.0.0 --port 8000`.
2. Fereastra afișează adresa `http://<IP-PC>:8000/admin/harta` și codul QR. Telefonul și PC-ul trebuie să fie în aceeași rețea Wi-Fi / hotspot.
3. Pe telefon: autentificare cu un cont al personalului (drepturile existente: citire = vizualizare; `edit` = modificări).
   **Schimbă parolele implicite înainte** (launcher-ul avertizează; `scripts/reset_admin_password.py`).
4. Nu face port forwarding și nu expune portul pe internet.

## Flux pe telefon
Hartă → atingi locul → dosarul locului (file: General, Înhumați, Contracte, Plăți, **Lucrări**, Foto/Docs).
- **Fă poză locului** → `poza_loc` (aceeași listă de fotografii pe care o citește MAIN).
- **Înhumați**: adaugă / editează datele / Fă poză / Certificat înhumare / Documente / Adeverință PDF.
- **Lucrări** (avizele de amenajare din MAIN): descriere, stare (Planificată / În lucru / Finalizată), observații,
  fotografii ÎNAINTE / ÎN TIMPUL / DUPĂ, „Marchează finalizată”.
- Fotografiile se aleg din cameră (`capture`) sau galerie/PDF, cu previzualizare, mai multe fișiere, progres și mesajul „Fotografie salvată”.

## Unde se salvează
Metadatele în tabela `documents` (+ `amenajari_avize` cu coloanele aditive `stare`, `observatii`, `data_finalizare`, doar SQLite local);
fișierele în `media/` al registrului activ (`registries/<cemetery_uid>/media/`). Portalul urmează registrul activ din MAIN; fără scurgeri între registre.

Teste: `backend/tests/test_mobile_local_portal.py`.
