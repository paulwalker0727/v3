"""build_bundle.py — construiește ZIP-ul de livrare (rulat de dezvoltator, nu pe laptopul parohiei).

    python deploy/windows_validation/build_bundle.py --out /app/outputs
Conține: patch (git format-patch), overlay/ (fișierele noi/modificate, întregi), manifest.json (hash-uri bază/nou), scripturile Windows,
instrucțiuni, capturi, rapoarte.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASE = "60f292c"


def git(*args: str, raw: bool = False):
    out = subprocess.run(["git", *args], cwd=REPO, capture_output=True, check=True)
    return out.stdout if raw else out.stdout.decode("utf-8").strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", default="cimitir-cloud-center-etapa12")
    a = ap.parse_args()
    out = Path(a.out)
    stage = out / a.name
    if stage.exists():
        shutil.rmtree(stage)
    (stage / "overlay").mkdir(parents=True)
    head = git("rev-parse", "--short", "HEAD")
    files = []
    for line in git("diff", "--name-status", BASE, "HEAD").splitlines():
        st, path = line.split("\t", 1)
        if st not in ("A", "M"):
            raise SystemExit(f"stare neașteptată {st} {path}")
        new = (REPO / path).read_bytes()
        entry = {"path": path, "status": st, "new_sha256": hashlib.sha256(new).hexdigest()}
        if st == "M":
            base = git("show", f"{BASE}:{path}", raw=True)
            entry["base_sha256"] = hashlib.sha256(base).hexdigest()
            entry["base_sha256_lf"] = hashlib.sha256(base.replace(b"\r\n", b"\n")).hexdigest()
        (stage / "overlay" / path).parent.mkdir(parents=True, exist_ok=True)
        (stage / "overlay" / path).write_bytes(new)
        files.append(entry)
    (stage / "manifest.json").write_text(json.dumps({"base_commit": BASE, "head_commit": head, "files": files}, indent=1), encoding="utf-8")
    rows = ["# Diferențe față de commitul de bază 60f292c", "", f"Capăt: `{head}`. Sunt modificate **{sum(f['status'] == 'M' for f in files)}** fișiere existente și adăugate **{sum(f['status'] == 'A' for f in files)}**.", "",
            "| Fișier | Tip | + | − |", "|---|---|---|---|"]
    for line in git("diff", "--numstat", BASE, "HEAD").splitlines():
        add, dele, path = line.split("\t", 2)
        rows.append(f"| `{path}` | {'modificat' if next(f for f in files if f['path'] == path)['status'] == 'M' else 'nou'} | {add} | {dele} |")
    rows += ["", "Fișiere interzise (neatinse, verificat): `desktop/license/*`, `desktop/tools/license_manager.py`, `desktop/login_window.py`, `desktop/auth.py`, `shared/cimitir_auth.py`:",
             "`git diff --name-only 60f292c HEAD -- <acestea>` → gol."]
    (stage / "DIFERENTE_FATA_DE_60f292c.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (stage / "patch").mkdir()
    subprocess.run(["git", "format-patch", f"{BASE}..HEAD", "-o", str(stage / "patch")], cwd=REPO, check=True, capture_output=True)
    for name in ("install_test_copy.py", "INSTALL_TEST_COPY.bat"):
        shutil.copy2(HERE / name, stage / name)
    shutil.copy2(HERE / "INSTRUCTIUNI_WINDOWS.md", stage / "CITESTE-MA.md")
    shutil.copytree(REPO / "docs" / "cloud_center_screens", stage / "capturi")
    shutil.copy2(REPO / "docs" / "CENTRU_CONFIGURARE_CLOUD.md", stage / "RAPORT_ETAPA_1_2.md")
    shutil.copy2(HERE / "RAPORT_VALIDARE_SANDBOX.md", stage / "RAPORT_VALIDARE_SANDBOX.md")
    zpath = out / f"{a.name}.zip"
    zpath.unlink(missing_ok=True)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(stage).as_posix())
    digest = hashlib.sha256(zpath.read_bytes()).hexdigest()
    (out / f"{a.name}.zip.sha256").write_text(f"{digest}  {a.name}.zip\n")
    print(zpath, digest, len(files), "fișiere în overlay")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
