"""Verificare PRE-BUILD (rulează pe orice sistem, inclusiv Linux/CI): resursele și modulele de care depinde `Cimitir.exe` există în repository.

    python scripts/windows/verify_build_inputs.py

NU construiește și NU validează executabilul: confirmă doar intrările build-ului (spec, resurse, migrări, scripturi, dependențe).
Cod de ieșire: 0 = fără FAIL (WARN-urile sunt informative), 1 = cel puțin un FAIL.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESKTOP, SHARED, BACKEND = ROOT / "desktop", ROOT / "shared", ROOT / "backend"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "", warn: bool = False) -> None:
    results.append(("PASS" if ok else ("WARN" if warn else "FAIL"), name, detail))


def local_module(name: str) -> bool:
    top = name.split(".")[0]
    for base in (DESKTOP, SHARED, BACKEND, BACKEND / "cimitir"):
        if (base / f"{top}.py").is_file() or (base / top / "__init__.py").is_file():
            return True
    return False


def installed(name: str) -> bool:
    try:
        return importlib.util.find_spec(name.split(".")[0]) is not None
    except (ImportError, ValueError):
        return False


def main() -> int:
    spec = (DESKTOP / "cimitir.spec").read_text(encoding="utf-8")

    for kind, base in (("_data", DESKTOP), ("_backend_data", BACKEND), ("_root_data", ROOT)):
        for rel in re.findall(rf'\b{kind}\(\s*"([^"]+)"', spec):
            check(f"resursă spec {kind}({rel})", (base / rel).exists(), str(base / rel))

    hidden = re.search(r"hiddenimports = \[(.*?)\n\]", spec, re.S)
    names = re.findall(r'^\s*"([A-Za-z0-9_.]+)",', hidden.group(1), re.M) if hidden else []
    check("hiddenimports citite din spec", bool(names), f"{len(names)} module")
    unresolved = [n for n in names if not (local_module(n) or installed(n))]
    check("hiddenimports rezolvabile (local sau instalate)", not unresolved, ", ".join(unresolved) or "toate", warn=True)

    for must in ("cimitir_backup", "backup_window", "integrity_window", "duplicate_people_window", "cimitir_integrity"):
        present = must in names or local_module(must)
        check(f"modul nou/curent livrabil în build: {must}", present and local_module(must), "în spec sau analizat static din ui_mainwindow")
    ui = (DESKTOP / "ui_mainwindow.py").read_text(encoding="utf-8")
    for window in ("backup_window", "integrity_window", "duplicate_people_window"):
        check(f"MAIN importă {window}", f"from {window} import" in ui or f"import {window}" in ui)

    migrations = sorted((BACKEND / "cimitir" / "pg_migrations").glob("[0-9][0-9][0-9]_*.sql"))
    numbers = [int(m.name[:3]) for m in migrations]
    check("migrări incluse în build: secvență continuă", numbers == list(range(numbers[0], numbers[-1] + 1)) if numbers else False,
          f"{numbers[0]:03d}..{numbers[-1]:03d} ({len(numbers)})" if numbers else "lipsesc")

    for rel in ("desktop/fonts/LiberationSans-Regular.ttf", "desktop/templates", "desktop/icon.ico", "desktop/installer/cimitir_setup.iss",
                "desktop/installer/file_version_info.txt", "backend/templates", "backend/static", "tools/setup_manager",
                "scripts/windows/Register-CimitirBackupTask.ps1", "scripts/windows/Verify-CimitirBuild.ps1"):
        check(f"fișier/folder necesar: {rel}", (ROOT / rel).exists())

    launcher = (DESKTOP / "launcher.py").read_text(encoding="utf-8")
    for flag in ("--portal", "--backup-run"):
        check(f"launcher.py tratează {flag}", flag in launcher)

    reqs = [ln.strip() for ln in (DESKTOP / "requirements.txt").read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    vcs = [r for r in reqs if "git+" in r or " @ " in r]
    bat = (DESKTOP / "build_exe.bat").read_bytes().decode("utf-8", "replace")
    filtered = "emergentintegrations" in bat
    check("dependențe din Git (necesită `git` pe Windows) sunt excluse din build_exe.bat", not vcs or filtered,
          ("dependențe VCS: " + ", ".join(v.split()[0] for v in vcs)) if vcs else "nu există", warn=False)
    check("requirements-build.txt are PyInstaller", "pyinstaller" in (DESKTOP / "requirements-build.txt").read_text(encoding="utf-8").lower())
    check("build_exe.bat are CRLF", b"\r\n" in (DESKTOP / "build_exe.bat").read_bytes())

    width = max(len(n) for _, n, _ in results)
    for status, name, detail in results:
        print(f"[{status}] {name.ljust(width)}  {detail}")
    counts = {s: sum(1 for r in results if r[0] == s) for s in ("PASS", "WARN", "FAIL")}
    print(f"\nTOTAL: {counts['PASS']} PASS, {counts['WARN']} WARN, {counts['FAIL']} FAIL   (verificare statică; nu înlocuiește validarea manuală pe Windows)")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
