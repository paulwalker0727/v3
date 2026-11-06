# P3 — Permisiuni controlate de administrator

## Model
Drepturi: `view`, `add`, `edit`, `delete`, `approve` (+ `manage_users`, `manage_config`, doar admin). Rolul dă **doar valori implicite**, identice cu `main`:
- `admin`: toate; `operator`: view, add, edit, delete, approve (în `main` „edit” acoperea orice operațiune de date); `user`: view.
- `users.rights` (CSV) suprascrie implicitele **per cont**. Marcajul `cfg` = „configurat explicit de administrator”: drepturile sunt exact cele bifate, fără implicații ascunse; `cfg` singur = doar `view` (administratorul poate retrage tot, inclusiv unui operator).
- Format vechi (fără `cfg`, ex. `edit,approve`): **compatibilitate** — `edit` include `add` și `delete`, ca în `main`. Nicio migrare; conturile existente se comportă la fel până când administratorul salvează formularul.
- Clienții/MAIN mai vechi ignoră `cfg`, `add`, `delete` (nu sunt în `RIGHTS` lor): o configurare nouă apare acolo cel mult mai restrictivă, niciodată mai largă. Sincronizarea Cloud/Mobile v2 transportă CSV-ul neschimbat.

## Unde se aplică
| Operațiune | Drept | Funcții (verificare reală, fail-closed) | UI (dezactivare butoane) |
|---|---|---|---|
| Adăugare înhumat / concesionar / contract / plată / document | `add` | `save_inhumat`, `save_concesionar`, `save_contract` (fără id), `add_plata`, `add_document` | `Înhumați`, `Concesionari`, `Contracte`, `Plăți`, panou plăți, `Adaugă înhumat` din modul utilizator |
| Modificare | `edit` | `save_*` cu id | butoane „Modifică” |
| Ștergere | `delete` | `delete_inhumat/concesionar/contract/plata/document/plot` | butoane „Șterge” |
| Aprobare cereri, administrare | `approve`, `manage_*` | neschimbat | neschimbat |
Garda (`set_access_guard`) se instalează la autentificarea în MAIN și re-verifică drepturile în DB la fiecare operațiune (revocarea/acordarea se aplică în sesiune deschisă). Scripturile, sincronizarea și importurile (fără gardă instalată) nu sunt afectate.

## Panoul Admin (web, Utilizatori)
Acum are bife individuale `add / edit / delete / approve` pe cont; salvarea scrie `cfg,<bife>`. Bifele afișează drepturile **efective** ale contului. `manage_users`/`manage_config` nu se pot acorda din panou.

## Lipsuri rămase (documentate, neschimbate implicit)
1. Portalul web verifică doar `edit` (`can_write`) și `approve`; `add`/`delete` granulare sunt aplicate în MAIN. Pentru granularitate și pe web, rutele de scriere din `backend/server.py` trebuie mapate pe `add`/`edit`/`delete` (modificare separată, de aprobat).
2. Nu există panou de utilizatori în aplicația desktop; configurarea se face din panoul web Admin.
3. Operațiuni neacoperite de gardă (nu au fost cerute în P3): avize, fotografii, metadate de loc/geometrie hartă, sectoare. Hărțile sunt protejate de modul de afișare (read-only pentru cine nu are `edit`).
4. Drepturile telefoanelor (Mobile v2, perechi) sunt un sistem separat și nu s-au modificat.
5. Implicitul rolului `user` poate deveni „consultare + adăugare” printr-o singură modificare în `ROLE_RIGHTS` (decizie de politică, nu s-a schimbat).
