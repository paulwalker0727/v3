# Etapa 3.5 — Validare reală MAIN înainte de Etapa 4

Nu schimbă arhitectura. Verifică integrarea reală: **launcher → portal (socket real) → MAIN → registre**.

## Cum se rulează

| Unde | Comandă |
|---|---|
| Windows (ferestrele se văd și se acționează singure) | `tools\windows\SMOKE_MAIN_REAL.bat` sau `python scripts\smoke_main_real\run.py` |
| Windows, internet oprit (Wi-Fi/Ethernet dezactivat) | `tools\windows\SMOKE_MAIN_REAL.bat --offline` (în plus, procesul MAIN nu are voie să iasă pe internet) |
| Linux / CI | `QT_QPA_PLATFORM=offscreen python scripts/smoke_main_real/run.py [--offline]` |

Folosește un folder de date NOU (temporar); refuză un folder nevid. Nu atinge Supabase, Render, migrările 008–013, datele Banovița.
Cod de ieșire 0 = PASS. Necesită `psutil` (verificarea porturilor/proceselor); bat-ul îl instalează.

## Ce face (cod real, nu simulare)

* `launcher.main()` real → butonul „Doar local” (`start_local`) → portal uvicorn real pe `127.0.0.1:8000` → `desktop/main.py` real.
* Un operator de test (`scripts/smoke_main_real/sitecustomize.py`, activ doar cu `CIMITIR_SMOKE=1`) acționează ferestrele reale prin
  QTimer: selector → 2 organizații → 2 cimitire → Deschide A → login normal (cont creat în registru, nu cel implicit) → loc + fotografie →
  „Schimbă registrul” (dialog real de confirmare) → B (verifică lipsa datelor din A, DB și portal) → același `cod_loc` + același nume de
  fotografie → înapoi în A (date intacte) → închidere. Apoi: repornire (selector, registrul activ rămâne A), autostart (un singur registru +
  „Deschide automat”), port ocupat.
* Verificări socket real: portul ascultă numai pe `127.0.0.1` (psutil/netstat), nu pe `0.0.0.0`/`::`; IP-ul LAN refuză conexiunea;
  `/portal` răspunde HTTP real; schimbarea registrului în MAIN se vede imediat în portal (`/api/public/search`).
* Port ocupat de alt program: launcherul avertizează, nu pornește portalul, MAIN pornește oricum (SQLite local).
* Închiderea launcherului: procesul portalului se oprește, portul se eliberează, fără procese-copil rămase.
* Offline: `socket.connect`/DNS ocolesc internetul (DNS întârzie 3 s ca în realitate), loopback permis; `AutoSync` eșuează controlat,
  UI-ul continuă să primească evenimente (tick la 250 ms).

Limite asumate: licența Ed25519 este simulată valid doar în procesul de test; „adaugă un loc / fotografie” se face prin aceleași funcții
de bază de date folosite de UI (`save_plot_to_db`, `add_document`), nu prin desenarea poligonului pe hartă. Testul pe Windows
rămâne de rulat pe un PC Windows (aici s-a rulat pe Linux, Qt offscreen).

## Defecte reale găsite de smoke-test și corectate (fără schimbări de arhitectură)

1. `launcher.main()` apela `init_db()` și citea `startup_mode` din `DATA_DIR/cimitir.db`, creând o bază „veche” goală (cu conturi
   implicite) lângă registre. Preferințele launcherului stau acum în catalogul registrelor (`launcher_*` în `app_state`).
2. Portalul pornit de launcher înainte de primul registru crea și el `cimitir.db`; acum, cu catalog fără bază veche, așteaptă
   (`/api/public/health` = 200, `/portal` = pagină explicativă 503 cu `Retry-After`, API = 503) și nu creează nimic.
3. Selectorul oferea „Am găsit registru existent: 2 înregistrări” pentru o bază goală (doar conturi implicite);
   `detect_legacy` ignoră acum bazele fără date (0 înregistrări, 0 media; conturile/setările nu contează).

## Cele 3 teste vechi (NU reparate; anterioare Etapei 3)

* `test_etapa1_audit.py::test_route_inventory_uses_only_clear_prefixes` — ruta `/api/m/{token}/info` are prefix neclar; eșuează identic pe codul de dinaintea Etapei 3.
* `test_etapa9b_cloud_config.py::test_env_example_has_the_cloud_section_without_secrets` — caută `backend/.env.example`, absent din repo.
* `test_etapa9b_cloud_config.py::test_real_env_has_the_cloud_section_and_is_not_tracked` — cere `backend/.env` real cu secțiunea „CONFIGURARE CLOUD - SUPABASE”; depinde de mașină.

Suita completă (Linux): 221 trecute, 151 sărite (PostgreSQL/live), 3 picate = cele de mai sus.
