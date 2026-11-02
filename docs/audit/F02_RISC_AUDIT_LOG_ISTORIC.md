# F-02 — Datele personale (CNP / CI) în `audit_log`: ce s-a corectat, riscul istoric rămas și procedura propusă de remediere

**Stare:** documentul descrie (1) ce face patch-ul F-02, (2) riscul datelor DEJA scrise în `audit_log` și în alte jurnale, (3) o procedură de remediere **propusă**, care **nu se execută fără aprobarea explicită** a utilizatorului. Patch-ul F-02 nu șterge, nu rescrie și nu migrează nimic din jurnalul istoric și nu introduce criptare.

## 1. Unde se scriau informații personale în jurnal (identificat)
| # | Punct de scriere | Ce putea conține | Acoperit de F-02 |
|---|---|---|---|
| 1 | `backend/audit_entities.record()` — apelat din `entity_sync_api.py` (sincronizare legacy `/entity-sync`) și din funcțiile panoului web (`audit_entities.install()`) | valorile **vechi/noi pe câmp**: `cnp`, `bi_ci`, `owner_cnp`, `owner_id_card_serie_number` (lista `_SENSITIVE` nu le conținea), plus `extra_data`/`observatii` cu CNP în text | **Da** — mascare după nume de câmp și după conținut, **după** calculul diferenței (o modificare doar a CNP rămâne vizibilă ca „câmp modificat”) |
| 2 | `shared/cimitir_requests.audit()` — ~40 de apeluri (server web, cetățeni, sincronizare, mobile, profile cloud) | text liber / JSON în `detail` și `target` (mesaje de eroare, `str(exc)`, valori interpolate) | **Da** — redactare după conținut și JSON recursiv, înainte de trunchiere |
| 3 | `shared/cimitir_sync_v2.py::_audit()` (tabela `sync_v2_audit`, Cloud) | la ștergere: `{"before": <snapshot complet al rândului>}` → conține `cnp`/`bi_ci`/`owner_*` | **Da** — redactare structurală înainte de INSERT |
| 4 | `desktop/security/audit_log.py` (jurnal criptat AES local, GDPR) | doar metadate (cheie de licență, erori de integritate) | Nu necesită |
| 5 | `cp_audit_log` / `audit_log` din planul de control (`cimitir_cloud_control*.py`) | doar uid-uri, roluri, ttl | Nu necesită (nu primesc date de persoane) |

Ce face redactarea (modul `shared/cimitir_redact.py`):
* câmpuri cu nume sensibil (`cnp`, `owner_cnp`, `bi_ci`, `owner_id_card*`, `carte_identitate`, `serie_nr`, `ci`…) → `[REDACTAT]` (golul rămâne gol);
* orice text: 13 cifre în formă de CNP → `[CNP REDACTAT]`; `cheie=valoare` / `"cheie": "valoare"` / `Key (cnp)=(…)` din mesaje de eroare cu cheie sensibilă; seria/nr. CI precedate de „CI/BI/serie/act de identitate”;
* JSON (inclusiv JSON imbricat în text, ex. `extra_data`) se parcurge recursiv;
* se păstrează: utilizator (`actor`), tip actor, acțiune, entitate/țintă, moment (`ts`), cheile câmpurilor modificate, valorile nesensibile.
* **Datele operaționale nu se modifică** (concesionari/contracte/înhumați rămân neschimbate).

**Limite cunoscute (rămân deschise, nu sunt rezolvate prin F-02):**
1. O serie/nr. de CI scrisă în text liber fără nume de câmp și fără cuvânt-cheie (de ex. „XY123456” singur) nu se poate deosebi de un număr de contract și nu este redactată. Câmpurile structurate sunt acoperite întotdeauna.
2. Alte tabele operaționale cu snapshot-uri ale rândurilor: `sync_v2_conflicts` (`incoming`/`server`), `sync_conflicts`, `cloud_sync_outbox.payload`, `sync_ops`. Nu sunt „jurnal de audit”, au rol funcțional (rezolvarea conflictelor) și **nu au fost modificate**; riscul de confidențialitate există și rămâne o decizie separată.
3. Nu s-a făcut un audit exhaustiv al tuturor mesajelor `logger.*`/fișierelor de log din aplicație (nu s-a găsit niciun apel care să scrie CNP; verificare prin căutare în cod, nu prin execuție completă).
4. Exporturile/PDF-urile conțin intenționat CNP (contracte, adeverințe) — în afara F-02.

## 2. Riscul datelor deja existente în `audit_log`
Orice rând de audit scris de versiunile anterioare pentru modificări ale `concesionari`/`contracte`/`inhumati` (create/update/delete) poate conține CNP/CI în clar în coloana `detail` (JSON `old`/`new`), iar rândurile `delete` din `sync_v2_audit` pot conține snapshot-ul complet. **F-02 nu schimbă aceste rânduri.** Ele rămân vizibile pentru oricine are acces de citire la `audit_log` (panoul web „istoric entitate”, `recent_audit`, backup-uri, dump-uri PostgreSQL/Supabase). Backup-urile existente conțin și ele aceste valori.

## 3. Procedură de remediere propusă (NEEDS APPROVAL — nu se execută automat)
Responsabil: operatorul bazei de date, cu aprobarea scrisă a utilizatorului. Se face mai întâi pe o **copie / mediu de test**, apoi pe producție.

1. **Inventar read-only (fără modificări).** Numără rândurile afectate, fără a afișa valorile:
   * SQLite (MAIN/portal local), pe o **copie** a fișierului: 
     ```sql
     SELECT count(*) FROM audit_log WHERE detail GLOB '*[1-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]*'
        OR detail LIKE '%"cnp"%' OR detail LIKE '%bi_ci%' OR detail LIKE '%id_card%';
     ```
   * PostgreSQL/Supabase (sesiune doar-citire):
     ```sql
     SELECT count(*) FROM audit_log WHERE detail ~ '(^|[^0-9])[1-9][0-9]{12}([^0-9]|$)' OR detail ~* '"(cnp|owner_cnp|bi_ci|owner_id_card[a-z_]*)"';
     SELECT count(*) FROM sync_v2_audit WHERE detail::text ~* '"(cnp|owner_cnp|bi_ci|owner_id_card[a-z_]*)"';
     ```
2. **Backup verificat înainte de orice scriere**: copie a fișierului SQLite (cu `PRAGMA integrity_check`) / `pg_dump -t audit_log -t sync_v2_audit` + verificarea restaurării într-o bază temporară. Backup-ul conține valorile în clar → se păstrează criptat și cu termen de ștergere stabilit.
3. **Aprobare**: raportul de inventar (doar numere) și scriptul de redactare sunt prezentate utilizatorului; se aprobă explicit ținta (TEST sau PROD), fereastra de lucru și păstrarea/ștergerea backup-ului.
4. **Execuție în tranzacție, în loturi, idempotentă**: scriptul aplică **aceeași** funcție `cimitir_redact.redact_detail()` pe coloana `detail` (și `redact_obj()` pe JSON-ul din `sync_v2_audit`), doar `UPDATE … SET detail = <redactat>` pe rândurile cu modificări; nu atinge `ts`, `actor`, `action`, `target`; înregistrează numărul de rânduri modificate. Rulare `--dry-run` obligatorie înainte.
5. **Verificare după**: repetarea inventarului (așteptat 0), compararea numărului total de rânduri și a sumelor de control pe coloanele neatinse, testul aplicației (istoricul entității se afișează).
6. **Backup-uri și dump-uri vechi**: inventariate separat; se rotesc/șterg conform politicii aprobate (în continuare conțin datele vechi).
7. **Rollback**: se restaurează `audit_log` din backup-ul de la pasul 2 (valorile în clar revin; de aceea backup-ul se șterge doar după validare).

## 4. Teste
Vezi `backend/tests/test_f02_audit_redaction.py` (SQLite izolat, date sintetice; test PostgreSQL pentru `sync_v2_audit` rulat cu `CIMITIR_TEST_PG_ADMIN_URL`).
