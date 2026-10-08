# Clonare și verificare v3: Etapa 4 (Pasul 5)

Repo-ul `github.com/paulwalker0727/v3` se copiază integral în spațiul de lucru și devine baza curentă și sursa de adevăr pentru Etapa 4.
Înainte de orice altă acțiune se verifică dacă v3 conține commitul și codul Pasului 5. Dacă lipsesc, lucrul se oprește și se raportează starea. Altfel se rulează suita de teste și se raportează rezultatul.

## Pentru cine
Proprietarul proiectului de gestiune a cimitirelor (MAIN local, cloud, bootstrap Flux A/B). Are nevoie de o copie verificată a stării actuale, pe care să construiască pașii următori.

## Ce s-a constatat deja (analiză publică pe GitHub, înainte de clonare)
- **Commitul Pasului 5 există în v3:** `13c6dee` („Etapa 4 – Pasul 5: FastAPI local transport pentru Flux A/B — COMPLET”). Ultimul commit este `e8939c4` (modificări auto-generate).
- **Commitul Pasului 5 schimbă în v3 doar 3 lucruri:** `memory/PRD.md`, `test_reports/iteration_3.json` și pointerul `v2`. Acesta din urmă a trecut de la `4515d99` la `862d8a8`.
- **`v2` din v3 nu este un folder cu cod.** Este o referință (submodul) către repo-ul `paulwalker0727/v2` la commitul `862d8a8`. Acel repo întoarce 404: e privat sau a fost șters.
- **Codul Etapei 4 nu apare nicăieri în conținutul public al v3.** Lipsesc `shared/cimitir_*.py` (inclusiv `cimitir_cloud_http.py`, `cimitir_cloud_client.py`, `cimitir_cloud_wire.py`, `cimitir_remote.py`), testele `test_etapa4_pas*.py` și `ETAPA4_BOOTSTRAP_DESIGN.md`.
- **În rădăcina v3 este doar șablonul inițial:** `backend/server.py` (exemplul „Hello World”), `frontend/`, `tests/__init__.py`, `memory/PRD.md`, `test_reports/`, `README.md` și `yarn.lock`.

**Concluzie preliminară:** commitul de raport al Pasului 5 există, dar codul lui nu e în v3. Conform instrucțiunii („dacă lipsește, oprește-te și raportează”), cel mai probabil rezultat al fazei 1 este oprirea, cu raport.

## Ce se face (faza 1)
1. Clonare completă a v3, cu tot istoricul git, ca bază de lucru în `/app`.
2. Verificare în istoricul local:
   - commitul `13c6dee` este prezent;
   - ce conține efectiv `v2` după clonare (submodul gol sau fișiere);
   - dacă există fișierele cheie ale Pasului 5 și testele Etapei 4.
3. Decizie:
   - **Codul Pasului 5 lipsește:** oprire. Nu se rulează teste, nu se modifică nimic și nu se reconstruiește nimic din memorie. Raportul spune exact ce există și ce lipsește.
   - **Codul Pasului 5 există** (de exemplu, dacă submodulul se poate totuși aduce): rularea suitei complete, comparată cu ultima stare raportată (521 passed, 3 legacy failed, 154 skipped).
4. Raport final, în același format PASS/FAIL folosit în etapele anterioare.

## Fluxul utilizatorului
Aprobă planul → primește raportul de verificare → decide sursa codului (de exemplu, încarcă codul v2 în v3) sau pasul următor.

## UI/UX
Nu se aplică. Nu există interfață nouă; rezultatul este un raport text în română.

## Faze de implementare
- **Faza 1 (acum):** clonare v3, verificare commit și cod, apoi oprire cu raport sau rularea testelor cu raport.
- **Faza 2:** după ce codul Etapei 4 ajunge în v3 (decizia proprietarului), restabilirea suitei complet funcționale și închiderea celor 3 teste legacy.
- **Faza 3:** continuarea Etapei 4 (Flux C și următorii pași) doar pe v3.

## Presupuneri
- „Commitul relevant” înseamnă codul Pasului 5, nu doar mesajul de commit. Un commit care conține doar raportul și pointerul de submodul este tratat ca „cod lipsă”.
- Nu se încearcă accesul la repo-ul `paulwalker0727/v2`, conform deciziei „lucrăm doar pe v3”.
- Nu se face commit, push sau vreo modificare pe GitHub.
- Șablonul existent din `/app` este înlocuit cu conținutul v3 (clonare integrală ca bază).
- Nu se atinge producția (Supabase, Render, Banovița).
