"""Proces REAL pentru testele Etapa 4 / Pasul 1 (cross-process lock, kill -9, offline). Se rulează cu `python etapa4_worker.py '<json>'`.

Argumente JSON: action (execute|abort|status), data_dir, cloud_db, cemetery_uid, plan_file, marker_dir, tag, crash_at (se blochează în fază ca
părintele să-l omoare cu SIGKILL), pause_at + release_file (ține lock-ul până apare fișierul), sync_at + peer_marker (așteaptă un marker al altui
proces), go_file (barieră de pornire), no_network (instalează filtrul seccomp înainte de orice), sleep_at/sleep_s.
Ultima linie de stdout: `RESULT {json}`. Cod de ieșire: 0 ok, 3 refuz controlat (lock/blocat/plan), 1 eroare neașteptată.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "shared"))


def wait_for(path: str, timeout: float = 60.0) -> None:
    end = time.time() + timeout
    while not Path(path).exists():
        if time.time() > end:
            raise TimeoutError(path)
        time.sleep(0.02)


def main() -> int:
    a = json.loads(sys.argv[1])
    net = {}
    if a.get("no_network"):
        import netblock
        net["installed"] = netblock.install()
        net["probe"] = netblock.probe()
    marker_dir = Path(a["marker_dir"])
    marker_dir.mkdir(parents=True, exist_ok=True)
    tag = a["tag"]

    import cimitir_bootstrap as bs
    import cimitir_cloud_control as cc
    import cimitir_registry as reg
    import cimitir_secretstore as ss

    cat = reg.RegistryCatalog(Path(a["data_dir"]))
    binding = cat.binding_for(a["cemetery_uid"])
    if a.get("cloud_url"):
        import cimitir_cloud_client as ccl
        pf0 = json.loads(Path(a["plan_file"]).read_text(encoding="utf-8")) if a.get("plan_file") else {}
        cp = ccl.RemoteControlPlane(a["cloud_url"], pf0.get("code"), timeout=a.get("http_timeout", 3.0), retries=a.get("http_retries", 1), backoff=0.05)
    else:
        cp = cc.LocalControlPlane(a["cloud_db"])
    store = ss.default_store(a["data_dir"])
    inst = bs.installation_uid(cat)
    result = {"tag": tag, "pid": os.getpid(), "net": net}
    if a.get("go_file"):
        wait_for(a["go_file"])
    try:
        if a["action"] == "status":
            result.update(ok=True, incomplete=bs.incomplete_bootstrap(binding), lock_held=bs.lock_held(binding), lock_info=bs.lock_info(binding))
        elif a["action"] == "abort":
            bs.abort_flow(binding, cp, store, a["bootstrap_id"], inst)
            result.update(ok=True)
        elif a["action"] in ("flow_b", "flow_b_abort"):
            import cimitir_flow_b as fb

            def bhook(phase: str) -> None:
                (marker_dir / f"{tag}.{phase.replace(':', '_')}").write_text(str(os.getpid()))
                if a.get("crash_at") == phase:
                    time.sleep(3600)

            pf = json.loads(Path(a["plan_file"]).read_text(encoding="utf-8"))
            if a["action"] == "flow_b_abort":
                fb.abort_flow_b(binding, cp, store, a["actor"], a["bootstrap_id"], inst)
                result.update(ok=True)
            else:
                rep = fb.execute_flow_b(binding, cp, store, a["actor"], pf["plan"], pf["plan"]["plan_hash"], pf["code"], inst, hook=bhook,
                                        accept_missing_media=pf.get("accept_missing", False), batch_size=pf["batch_size"], chunk_size=pf["chunk_size"])
                result.update(ok=True, report=rep)
        elif a["action"] in ("transfer", "resume_transfer", "promote"):
            import cimitir_identity as ident
            import cimitir_transfer as tr

            def thook(phase: str) -> None:
                (marker_dir / f"{tag}.{phase.replace(':', '_')}").write_text(str(os.getpid()))
                if a.get("crash_at") == phase:
                    time.sleep(3600)

            if a["action"] == "promote":
                result.update(ok=True, result=tr.promote_transfer(binding, a["bootstrap_id"], True, hook=thook))
            else:
                pf = json.loads(Path(a["plan_file"]).read_text(encoding="utf-8"))
                if a["action"] == "transfer":
                    import cimitir_preflight as pre
                    plan = pf["plan"]
                    t = tr.start_transfer(binding, cp, a["actor"], plan, plan["plan_hash"], a["bootstrap_id"], batch_size=pf["batch_size"], chunk_size=pf["chunk_size"])
                else:
                    t = tr.resume_transfer(binding, cp, a["actor"], a["bootstrap_id"], pf["plan"]["plan_hash"])
                rep = t.run(hook=thook)
                result.update(ok=True, report={k: rep[k] for k in ("stored", "skipped_already_staged", "verified", "staging_content_digest")})
        else:
            pf = json.loads(Path(a["plan_file"]).read_text(encoding="utf-8"))
            plan, code = pf["plan"], pf["code"]

            def hook(phase: str) -> None:
                (marker_dir / f"{tag}.{phase}").write_text(str(os.getpid()))
                if a.get("sync_at") == phase:
                    wait_for(a["peer_marker"])
                if a.get("pause_at") == phase:
                    wait_for(a["release_file"])
                if a.get("sleep_at") == phase:
                    time.sleep(float(a.get("sleep_s", 1)))
                if a.get("crash_at") == phase:
                    time.sleep(3600)

            rep = bs.execute_flow_a(binding, cp, store, plan, plan["plan_hash"], code, inst, hook=hook)
            result.update(ok=True, report=rep)
    except cc.BootstrapLocked as exc:
        result.update(ok=False, kind="locked", error=str(exc))
    except (bs.BlockedFlow, bs.PlanError, bs.BootstrapError) as exc:
        result.update(ok=False, kind="blocked", decision=getattr(exc, "decision", ""), error=str(exc))
    except Exception as exc:
        result.update(ok=False, kind="error", error=f"{type(exc).__name__}: {exc}", tb=traceback.format_exc())
    print("RESULT " + json.dumps(result, default=str))
    sys.stdout.flush()
    return 0 if result.get("ok") else (3 if result.get("kind") in ("locked", "blocked") else 1)


if __name__ == "__main__":
    sys.exit(main())
