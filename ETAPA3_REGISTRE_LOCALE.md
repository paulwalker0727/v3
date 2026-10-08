# Etapa 3 — Registre locale multiple în MAIN (local-first)

MAIN administrează mai multe **organizații** și mai multe **cimitire** pe același PC, fără internet. Fiecare cimitir are un
registru separat, identificat doar prin UUID (niciodată prin nume):

```
<DATA_DIR>/registry_catalog.sqlite          catalog: organizații, cimitire, registrul activ, "deschide automat"
<DATA_DIR>/registries/<cemetery_uid>/
    database.sqlite   media/   cache/   logs/   outbox/   registry.json
```

Outbox-ul de sincronizare (`cloud_sync_outbox`), setările locale și configurarea cloud sunt în `database.sqlite`, deci
sunt separate natural per registru. Baza își păstrează identitatea (`organization_uid`, `cemetery_uid` în `settings`);
o bază străină pusă într-un folder de registru este refuzată la deschidere.

## Fluxuri (nemodificate)

```
MAIN Desktop    -> SQLite + media locală, direct (nu prin localhost)
portal/admin    -> 127.0.0.1:8000 (0.0.0.0 doar LAN opt-in), pe registrul activ din catalog
sync background -> HTTPS -> cloud   (Etapele 4–6; nu face parte din Etapa 3)
```

## Module

| Fișier | Rol |
|---|---|
| `shared/cimitir_registry.py` | catalog, tipuri de organizații, `activate()` (MAIN), `use_binding()` / `bind_context_from_catalog()` (portal, per cerere), `pinned()` (operațiuni de sync nefragmentabile), `startup_plan()` |
| `shared/cimitir_registry_adopt.py` | detectare instalație veche, dry-run, adoptare cu confirmare, rollback |
| `desktop/registry_selector.py` | ecranul de pornire: Organizație / Cimitir; adaugă, deschide, editează, arhivează, configurare cloud |
| `scripts/adopt_legacy_registry.py` | adoptare din linia de comandă (dry-run implicit) |

Organizații: Parohie, Primărie / UAT, Operator privat, Mănăstire, Asociație, Altul (UID, tip, denumire oficială/afișată, CUI/CIF,
județ, localitate, adresă, telefon, email, logo, activ).

## Comutarea registrului

`activate(cemetery_uid)` face, în această ordine: validează (cimitir + organizație active) → pregătește/validează baza
(schemă, identitate) → așteaptă operațiunile fixate (`pinned()`: ciclurile de sync) sau refuză cu `RegistryBusyError` →
checkpoint WAL al registrului vechi → schimbă SQLite, media, cache, setări, outbox și configurarea sync deodată.
`set_active_db_path()` către altă bază decât a registrului activ → `RegistryBindingError`. Un `Binding` vechi nu mai
poate scrie (`assert_current` → `RegistrySwitchedError`).

Portalul local detectează schimbarea la fiecare cerere (catalogul e citit; fiecare cerere e legată de un singur registru).
Dacă registrul activ e arhivat/lipsește, portalul răspunde 503 (nu deschide o bază „la întâmplare”); instalațiile clasice
(`cimitir.db`, sau „Continuă cu baza veche”) merg ca înainte.

## Pornire MAIN

* un singur registru + „Deschide automat data viitoare” bifat → intră direct;
* altfel → selectorul. Meniu/buton „Schimbă registrul” în ferestrele MAIN.

## Instalații vechi (cimitir.db + media)

Nimic nu se mută automat. `adopt_legacy_registry.py` (sau butonul din selector) afișează întâi dry-run:

```
Am găsit registru existent
N înregistrări
M fișiere media
Va fi mutat în <cemetery_uid>
```

Cu `--apply --confirm`: copiere cu API-ul de backup SQLite → verificare (număr de rânduri pe tabel, integrity_check,
fișiere media) → căile din `documents`/`photos` rescrise → abia apoi originalele sunt mutate în
`<DATA_DIR>/legacy_adopted_<cemetery_uid>/` (nu se șterge nimic). La eșec: rollback, instalația veche rămâne neatinsă.

## Teste

`cd backend && python -m pytest tests/test_etapa3_registries.py -q -n 0` — numai date temporare: 2 organizații × 2 cimitire,
același `cod_loc`, aceeași denumire de fotografie, outbox/cache/setări separate, A→B→A repetat, restart (proces nou),
fără internet (socket blocat), portal local pe registrul activ, scrieri între registre imposibile, adoptare (dry-run,
confirmare, rollback, CLI).

## Nu face parte din Etapa 3

Bootstrap MAIN→cloud, cloud→MAIN, Sync V2, migrarea production, sincronizarea completă a fotografiilor (Etapele 4–6).
Backup/restaurare funcționează pe registrul activ (`cimitir_config.working_dir()`).
