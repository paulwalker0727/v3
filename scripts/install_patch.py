"""install_patch.py — instalare SIGURĂ a unui patch git peste o copie de lucru existentă (Windows / Linux), fără suprascrieri.

Garanții:
  * verifică că directorul este repo git, ramura curentă este cea așteptată și că HEAD conține commitul de bază al patch-ului;
  * oprește (fără nicio modificare) dacă un fișier atins de patch are modificări locale necomise sau dacă patch-ul nu se aplică curat (`git apply --check`);
  * face backup al fiecărui fișier existent atins de patch (+ manifest cu SHA-256) înainte de aplicare;
  * idempotent: dacă patch-ul este deja aplicat (marker + `git apply --reverse --check`), nu face nimic;
  * nu rulează niciun `git commit/push/reset/checkout`; modificările locale ale altor fișiere rămân neatinse;
  * `--rollback` restaurează din backup DOAR dacă fișierele nu au mai fost modificate după instalare.
Exit: 0 ok / deja aplicat, 2 verificare eșuată (nimic modificat), 3 eroare la aplicare (backup păstrat).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

BACKUP_DIR = ".patch_backups"
MARKER_DIR = ".patch_applied"


def git(repo: Path, *args: str, check: bool = False, inp: Optional[bytes] = None) -> subprocess.CompletedProcess:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, input=inp)
    if check and p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", "replace").strip())
    return p


def out(p: subprocess.CompletedProcess) -> str:
    return p.stdout.decode("utf-8", "replace").strip()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def patch_files(patch: bytes) -> List[str]:
    """Căile fișierelor atinse (b/...), inclusiv fișiere noi/șterse/redenumite."""
    paths: List[str] = []
    for m in re.finditer(rb"^diff --git a/(.+?) b/(.+?)$", patch, re.M):
        for g in (m.group(1), m.group(2)):
            s = g.decode("utf-8", "replace").strip('"')
            if s.startswith(("/", "\\")) or ".." in Path(s).parts or ":" in s.split("/")[0]:
                raise ValueError(f"Cale nepermisă în patch: {s}")
            if s not in paths:
                paths.append(s)
    return paths


def dirty_paths(repo: Path) -> dict:
    res = {}
    p = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    for ent in p.stdout.decode("utf-8", "replace").split("\0"):
        if len(ent) > 3:
            res[ent[3:]] = ent[:2]
    return res


def verify(repo: Path, patch: bytes, branch: str, base: Optional[str]) -> Tuple[List[str], List[str]]:
    errs: List[str] = []
    files = patch_files(patch)
    if git(repo, "rev-parse", "--is-inside-work-tree").returncode != 0:
        return ["Directorul nu este un repository git."], files
    cur = out(git(repo, "rev-parse", "--abbrev-ref", "HEAD"))
    if branch and cur != branch:
        errs.append(f"Ramura curentă este '{cur}', dar patch-ul cere '{branch}'. Treci pe ramura corectă (git switch {branch}) — nu am modificat nimic.")
    if base:
        if git(repo, "cat-file", "-e", f"{base}^{{commit}}").returncode != 0:
            errs.append(f"Commitul de bază {base} nu există în repository. Rulează: git fetch origin")
        elif git(repo, "merge-base", "--is-ancestor", base, "HEAD").returncode != 0:
            errs.append(f"HEAD nu conține commitul de bază {base}; patch-ul nu este pentru această istorie.")
    dirty = dirty_paths(repo)
    clash = sorted(f for f in files if f in dirty)
    if clash:
        errs.append("Fișiere atinse de patch au modificări locale (necomise) — NU le suprascriu:\n    " + "\n    ".join(f"{dirty[f].strip() or '?'} {f}" for f in clash)
                    + "\n  Comite sau salvează (git stash) modificările acestor fișiere, apoi reia instalarea.")
    if not errs and git(repo, "apply", "--check", "-", inp=patch).returncode != 0:
        chk = git(repo, "apply", "--check", "-", inp=patch)
        errs.append("Patch-ul nu se aplică curat pe acest cod (conflict cu modificările din commit-uri):\n    " + chk.stderr.decode("utf-8", "replace").strip().replace("\n", "\n    "))
    return errs, files


def install(repo: Path, patch_path: Path, branch: str, base: Optional[str], dry_run: bool = False) -> int:
    patch = patch_path.read_bytes()
    try:
        patch_files(patch)
    except ValueError as exc:
        print(f"OPRIT — {exc}")
        return 2
    pid = hashlib.sha256(patch).hexdigest()[:16]
    if not patch.strip():
        print("EȘEC: patch gol.")
        return 2
    if git(repo, "rev-parse", "--is-inside-work-tree").returncode == 0 and git(repo, "apply", "--reverse", "--check", "-", inp=patch).returncode == 0:
        print(f"Patch-ul {pid} este DEJA aplicat. Nu s-a modificat nimic.")
        return 0
    errs, files = verify(repo, patch, branch, base)
    if errs:
        print("OPRIT — nu s-a modificat niciun fișier:")
        for e in errs:
            print("  - " + e)
        return 2
    print(f"Verificări OK: ramura '{branch}', {len(files)} fișiere în patch, fără conflicte cu modificările locale.")
    if dry_run:
        print("Dry-run: nimic aplicat.")
        return 0
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bdir = repo / BACKUP_DIR / f"{stamp}_{pid}"
    manifest = {"patch_id": pid, "created": stamp, "branch": branch, "head": out(git(repo, "rev-parse", "HEAD")), "files": {}}
    for rel in files:
        src = repo / rel
        if src.is_file():
            dst = bdir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            manifest["files"][rel] = {"existed": True, "sha_before": sha256(src)}
        else:
            manifest["files"][rel] = {"existed": False}
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    p = git(repo, "apply", "--whitespace=nowarn", "-", inp=patch)
    if p.returncode != 0:
        print("EROARE la aplicare: " + p.stderr.decode("utf-8", "replace").strip())
        print(f"Backup păstrat în {bdir}. Verifică `git status`; restaurează manual din backup dacă este nevoie.")
        return 3
    for rel, info in manifest["files"].items():
        f = repo / rel
        info["sha_after"] = sha256(f) if f.is_file() else None
    (bdir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    (repo / MARKER_DIR).mkdir(exist_ok=True)
    (repo / MARKER_DIR / f"{pid}.json").write_text(json.dumps({"patch_id": pid, "backup": str(bdir.relative_to(repo)), "applied": stamp}), encoding="utf-8")
    print(f"Patch {pid} aplicat. Backup: {bdir}. Fișiere: {len(files)}. Nu s-a făcut commit/push — verifică `git status` / `git diff`.")
    return 0


def rollback(repo: Path, pid: Optional[str]) -> int:
    markers = sorted((repo / MARKER_DIR).glob("*.json")) if (repo / MARKER_DIR).exists() else []
    if pid:
        markers = [m for m in markers if m.stem == pid]
    if not markers:
        print("Nu există instalare de anulat.")
        return 2
    marker = markers[-1]
    info = json.loads(marker.read_text(encoding="utf-8"))
    bdir = repo / info["backup"]
    manifest = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
    problems = []
    for rel, i in manifest["files"].items():
        f = repo / rel
        cur = sha256(f) if f.is_file() else None
        if cur != i.get("sha_after"):
            problems.append(rel)
    if problems:
        print("OPRIT — fișiere modificate după instalare (nu le suprascriu):\n    " + "\n    ".join(problems))
        return 2
    for rel, i in manifest["files"].items():
        f = repo / rel
        if i["existed"]:
            f.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bdir / rel, f)
        elif f.exists():
            f.unlink()
    marker.unlink()
    print(f"Rollback făcut din {bdir}.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="directorul repository-ului (ex. C:\\Users\\xboxw\\Desktop\\v3-main)")
    ap.add_argument("--patch", help="fișierul .patch (git diff de la commitul de bază)")
    ap.add_argument("--branch", default="", help="ramura așteptată (obligatoriu la instalare)")
    ap.add_argument("--base", default="", help="commitul de bază al patch-ului")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--patch-id", default="")
    a = ap.parse_args(argv)
    repo = Path(a.repo).resolve()
    if a.rollback:
        return rollback(repo, a.patch_id or None)
    if not a.patch or not a.branch:
        print("Cer --patch și --branch.")
        return 2
    return install(repo, Path(a.patch), a.branch, a.base or None, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
