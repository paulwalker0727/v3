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

## Portalul web (rute de scriere, `backend/server.py`)
Fiecare rută cere exact dreptul ei (înainte: toate cereau `edit`): adăugare (înhumat, plată, aviz, document/atașament, concesionar nou, token „continuă pe telefon”, scanare) = `add`; modificare (loc, înhumat, aviz, aplicare scanare, concesionar existent) = `edit`; ștergere (înhumat, contract, plată, aviz, document, atașament, concesionar) = `delete`. Crearea de contract rămâne pe `approve` (flux existent) și, la conturile configurate explicit (`cfg`), cere și `add`; conturile în format vechi rămân neschimbate. Decizia cererilor (`approve`), utilizatori/setări/backup/purge (`manage_*`) neschimbate. Nicio rută nu primește acces mai larg decât înainte pentru conturile existente.

## Avize, fotografii, hartă (MAIN)
Protejate acum în funcțiile DB: avize (`add_aviz`=add, `update_aviz`=edit, `delete_aviz`=delete), fotografii (`save_photo`=add, `set_cover_photo`=edit, `delete_photo`=delete), geometrie loc (`save_plot_to_db`: loc nou=add, existent=edit), metadate/extra loc, sectoare, ghidaje, rânduri (add/edit/delete). Fluxurile derivate rămân funcționale prin `granted(...)`: metadatele unui loc **nou creat** (dialog atribuire cod, generare locuri), suprafața calculată din geometria salvată, arhivarea documentelor la aprobarea unei cereri (`approve`). Sincronizarea inițiată de sistem (documente de cerere din Cloud) folosește `system_operation()`.

## Sesiune deschisă
Garda DB re-verifică drepturile la fiecare operațiune: un buton rămas activ nu poate executa o operațiune interzisă (mesaj „Drepturi insuficiente”). Butoanele gate-uite se re-sincronizează automat la ~5 s (`refresh_gates`, revocare și acordare, cont dezactivat = tot retras), fără a reactiva controale dezactivate din alte motive. Dacă garda nu poate fi instalată/verificată la autentificare, sesiunea este refuzată cu mesaj de eroare și garda rămâne „refuză tot”.

## Lipsuri / riscuri rămase (documentate, neschimbate implicit)
1. Nu există panou de utilizatori în aplicația desktop; configurarea se face din panoul web Admin.
2. Operațiuni care scriu direct în SQL (importuri, sincronizare entități, aprobare cereri în `cimitir_requests`) nu trec prin gardă — intenționat; sunt autorizate de dreptul `approve` sau de sistem.
3. Hărțile: interacțiunile de desenare din UI se bazează pe modul de afișare (`edit`) și, în plus, pe garda DB la salvare.
4. Drepturile telefoanelor (Mobile v2, perechi) sunt un sistem separat și nu s-au modificat (netestat cu telefon real).
5. Un MAIN/serverul mai vechi ignoră `cfg/add/delete`: vede cel mult mai puține drepturi. Serverul nou + MAIN vechi cu cont `cfg,add` = fără scriere în MAIN vechi până la actualizare.
6. Implicitul rolului `user` poate deveni „consultare + adăugare” printr-o modificare în `ROLE_RIGHTS` (decizie de politică, nu s-a schimbat).
