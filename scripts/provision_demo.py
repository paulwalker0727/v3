#!/usr/bin/env python3
"""Demonstrație LOCALĂ și IZOLATĂ a provizionării (Faza A): două parohii, fiecare cu „proiect Supabase” propriu (PostgreSQL + Storage emulat),
backend izolat, HTTPS local, administrator inițial prin grant Ed25519, apoi backup și actualizare.

    python scripts/provision_demo.py [--keep]

Nu atinge PROD, Docker, proiectele Supabase reale sau licențele. Necesită PostgreSQL server local (apt install postgresql) și `pip install -r backend/requirements-cloud.txt`.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))

import cimitir_instance_grant as ig  # noqa: E402
import cimitir_prov_demo as demo  # noqa: E402
import cimitir_provisioner as pv  # noqa: E402
import cimitir_prov_runtime as rt  # noqa: E402
from cimitir_prov_store import Vault  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="nu opri instanțele la final")
    a = ap.parse_args()
    base = Path(tempfile.mkdtemp(prefix="cimitir-prov-demo-", dir="/tmp"))
    os.chmod(base, 0o755)
    projects = {s: demo.LocalSupabaseProject(base, s).start() for s in ("demo-alfa", "demo-beta")}
    prov = pv.Provisioner(base / "home", "TEST", Vault(base / "home" / "vault", Vault.master_from_env({"CIMITIR_PROVISIONER_MASTER_KEY": Vault.new_master()})))
    owner = prov.reg.authenticate(prov.reg.create_operator("demo-owner", "owner", "TEST"))
    try:
        print("release:", prov.import_release(owner, str(ROOT), "demo-1"))
        prov.set_default_release(owner, "demo-1")
        keys = {}
        for slug, proj in projects.items():
            r = prov.register(owner, f"parohia-{slug}", f"Parohia {slug}", f"{slug}.parohii.example", generate_owner_key=True)
            keys[slug] = r["owner_private_key_ONCE"]
            prov.link(owner, r["slug"], proj.link_params())
            out = prov.provision(owner, r["slug"], proj.admin_url())
            h = out["steps"]["health"]
            print(f"[{slug}] {out['instance']['status']}  https://{r['domain']}:{r['edge_port']}  ready={h['ready']} db={h['database']} storage={h['storage']} cert={h['tls_days_left']}z")
            g = ig.issue_grant(keys[slug], r["slug"])
            ca = prov.ca.ca_pem
            import ssl, socket
            ctx = ssl.create_default_context(cafile=str(ca))
            body = json.dumps({"token": g, "email": f"admin@{slug}.example", "password": "Parola-Demo-12345!", "organization_name": f"Parohia {slug}", "cemetery_name": "Cimitir"}).encode()
            with socket.create_connection(("127.0.0.1", r["edge_port"])) as raw, ctx.wrap_socket(raw, server_hostname=r["domain"]) as s:
                s.sendall(f"POST /api/v1/admin/bootstrap HTTP/1.1\r\nHost: {r['domain']}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
                print(f"[{slug}] inițializare administrator:", s.recv(64).split(b"\r\n")[0].decode())
            print(f"[{slug}] admin_initialized =", prov.health(owner, r["slug"])["admin_initialized"])
            print(f"[{slug}] backup:", prov.backup(owner, r["slug"], proj.admin_url()))
        print("audit:", prov.reg.audit_verify(), "înregistrări:", len(prov.reg.audit_list(limit=1000)))
        print("Instanțe pornite. Directorul demonstrației:", base)
        if a.keep:
            input("Enter pentru oprire…")
        return 0
    finally:
        for i in prov.reg.list_instances():
            prov._stop_procs(i)
        for p in projects.values():
            p.destroy()
        if not a.keep:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
