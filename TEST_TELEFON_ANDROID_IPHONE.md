# Proceduri de test pe telefon real — Android și iPhone

**Starea la livrare: toate pașii de mai jos sunt NETESTAT pe telefon real.** Nu marca PASS decât după execuție. Mediul de dezvoltare (Linux) a verificat doar logica și PWA-ul în Chromium headless, fără cameră reală.

## Cerințe înainte de test
1. Cloud pornit conform `TEST_LAPTOP_WINDOWS_DOCKER.md`, cu registrul MAIN asociat (BOUND) și cel puțin un loc de veci sincronizat.
2. **HTTPS este obligatoriu pentru cameră** (`getUserMedia`) pe iPhone și Android. Pe `http://192.168.x.x` scanarea din PWA nu va funcționa. Soluții:
   - tunel (ex. Cloudflare Tunnel `cloudflared tunnel --url http://localhost:8084`) ⇒ adresă `https://…trycloudflare.com`; setează `CIMITIR_PUBLIC_URL` la acea adresă și repornește containerul;
   - sau un reverse proxy cu certificat valid; pentru proxy: `CIMITIR_PAIRING_TRUSTED_PROXIES=<IP proxy>` ca QR-ul să conțină adresa publică, nu IP-ul intern.
3. În `/admin/`: codul pentru telefon e emis doar de Owner; telefonul trebuie să aibă dreptul **Încărcare documente** (și `view_pii` doar dacă testezi explicit acest drept).
4. Browsere: Android → Chrome; iPhone → Safari (iOS 16.4+ pentru PWA instalat).

## A. Asociere prin QR
| # | Pas | Așteptat | Android | iPhone |
|---|---|---|---|---|
| A1 | /admin/ → „Generează cod pentru telefon” | cod + QR + numărătoare inversă | | |
| A2 | Camera NATIVĂ scanează QR | se deschide `…/mobile/#pair=…`, asocierea se face automat | | |
| A3 | Alternativ: deschide `/mobile/` → „Scanează codul QR” (cameră din PWA, acordă permisiunea) | asociere reușită | | |
| A4 | Reutilizezi același cod | respins (cod consumat) | | |
| A5 | Cod expirat (așteaptă expirarea) | mesaj clar, fără asociere | | |
| A6 | Refuzi permisiunea camerei în PWA | mesaj + posibilitate de a introduce codul manual | | |
| A7 | Introducere manuală a codului de 20 caractere | asociere reușită | | |
| A8 | PWA instalat pe ecranul principal („Adaugă pe ecranul principal”), repornit | rămâne asociat | | |

## B. Acces la cameră și fotografii ONLINE
| # | Pas | Așteptat | Android | iPhone |
|---|---|---|---|---|
| B1 | Alegi un loc de veci → „Fotografiază” → acorzi camera | se deschide camera | | |
| B2 | Faci o fotografie și o încarci (online) | apare „încărcat”; în MAIN apare la următoarea sincronizare, asociată locului corect | | |
| B3 | Verificare în MAIN: fișierul și metadatele (loc, dată, autor/dispozitiv) | corecte, fără duplicate | | |
| B4 | Încarci aceeași fotografie de două ori | un singur document | | |

## C. Fotografii OFFLINE și reluare
| # | Pas | Așteptat | Android | iPhone |
|---|---|---|---|---|
| C1 | Modul avion ON; fă 3 fotografii | intră în coada locală („în așteptare”: 3) | | |
| C2 | Închide și redeschide aplicația (offline) | coada se păstrează | | |
| C3 | Modul avion OFF | încărcarea reîncepe automat; coada ajunge la 0 | | |
| C4 | Întrerupe rețeaua în mijlocul încărcării | încărcarea se reia fără duplicate | | |
| C5 | Verificare în MAIN | exact 3 fotografii, fiecare o singură dată | | |
| C6 | Conflict: modifică același loc în MAIN și (offline) pe telefon, apoi reconectează | conflict semnalat/rezolvat conform regulilor sync v2, fără pierdere de date | | |

## D. Permisiuni și izolare
| # | Pas | Așteptat | Android | iPhone |
|---|---|---|---|---|
| D1 | Telefon FĂRĂ `Încărcare documente` | butonul de fotografiere/încărcare indisponibil; serverul respinge (403) | | |
| D2 | Telefon fără `view_pii` | CNP și date din acte de identitate NU apar (filtrate pe server) | | |
| D3 | Cu `view_pii` acordat explicit | datele apar doar acum | | |
| D4 | Al doilea cimitir/organizație în Cloud | NU apar pe telefonul asociat la primul | | |
| D5 | Revoci telefonul în /admin/ | următoarea sincronizare e respinsă; datele locale nu mai pot fi sincronizate | | |
| D6 | După revocare, reasociere cu cod nou | funcționează cu un dispozitiv nou, nu reactivează cel vechi | | |
| D7 | Documente: doar cele permise (documentele existente nu se modifică/șterg de pe telefon) | confirmat | | |

## Ce să raportezi
Pentru fiecare rând eșuat: model telefon + versiune OS + browser, ora aproximativă (UTC+3) și o captură de ecran; din `/admin/` → dispozitive, starea dispozitivului. Nu trimite coduri de asociere active.
