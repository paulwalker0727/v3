"""BUG-09 — remediere procedurală: scriptul `scripts/salveaza_manifeste_backup.py` protejează manifestele peste commit-ul 6a9774b.

Istoricul Git nu poate fi modificat (fără force-push), deci comportamentul Git din `test_bug09_git_update_deletes_backup_manifests.py` rămâne;
aici se verifică procedura documentată: save -> actualizare -> restore, pe o clonă locală temporară (fără push)."""
import shutil
import subprocess
import sys

import pytest

import cimitir_backup as cb
from conftest import ROOT

COMMIT = "6a9774b"
SCRIPT = ROOT / "scripts" / "salveaza_manifeste_backup.py"


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


@pytest.mark.skipif(not shutil.which("git"), reason="git lipsește")
def test_save_update_restore_keeps_all_manifests_and_copies_listed(tmp_path):
    if subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", COMMIT], capture_output=True).returncode != 0:
        pytest.skip("commit-ul 6a9774b nu există în acest depozit")
    clone, saved = tmp_path / "clona", tmp_path / "salvate"
    subprocess.run(["git", "clone", "-q", "--no-checkout", str(ROOT), str(clone)], check=True)
    subprocess.run(["git", "-C", str(clone), "checkout", "-q", COMMIT + "^"], check=True)
    root = clone / "shared_data-backups"
    before = {p.relative_to(clone).as_posix(): p.read_bytes() for p in root.glob("*/manifest.json")}
    assert len(before) == 4
    for m in root.glob("*/manifest.json"):
        (m.parent / "cimitir.db").write_bytes(b"baza locala (nu este in Git)")
    r = _run("save", "--repo", str(clone), "--dest", str(saved))
    assert r.returncode == 0 and "salvate: 4" in r.stdout, r.stdout + r.stderr
    subprocess.run(["git", "-C", str(clone), "checkout", "-q", COMMIT], check=True)
    assert not list(root.glob("*/manifest.json")), "Git a șters manifestele (comportamentul istoric rămâne)"
    assert cb.list_backups(root) == []
    r = _run("restore", "--repo", str(clone), "--dest", str(saved))
    assert r.returncode == 0 and "readuse: 4" in r.stdout, r.stdout + r.stderr
    after = {p.relative_to(clone).as_posix(): p.read_bytes() for p in root.glob("*/manifest.json")}
    assert after == before, "manifestele revin identice (octet cu octet)"
    assert len(cb.list_backups(root)) == 4
    assert all((m.parent / "cimitir.db").read_bytes() == b"baza locala (nu este in Git)" for m in root.glob("*/manifest.json"))


def test_restore_never_overwrites_and_never_recreates_missing_backup_folders(tmp_path):
    repo = tmp_path / "repo"
    (repo / "shared_data-backups" / "cimitir-20260101-000000").mkdir(parents=True)
    (repo / "shared_data-backups" / "cimitir-20260101-000000" / "manifest.json").write_text("{\"v\": 1}", encoding="utf-8")
    (repo / "shared_data-backups" / "cimitir-20260202-000000").mkdir()
    (repo / "shared_data-backups" / "cimitir-20260202-000000" / "manifest.json").write_text("{\"v\": 2}", encoding="utf-8")
    saved = tmp_path / "salvate"
    assert _run("save", "--repo", str(repo), "--dest", str(saved)).returncode == 0
    (repo / "shared_data-backups" / "cimitir-20260101-000000" / "manifest.json").write_text("{\"v\": \"modificat\"}", encoding="utf-8")
    shutil.rmtree(repo / "shared_data-backups" / "cimitir-20260202-000000")                  # folderul copiei a dispărut de tot
    r = _run("restore", "--repo", str(repo), "--dest", str(saved))
    assert "readuse: 0" in r.stdout, r.stdout
    assert (repo / "shared_data-backups" / "cimitir-20260101-000000" / "manifest.json").read_text(encoding="utf-8") == "{\"v\": \"modificat\"}"
    assert not (repo / "shared_data-backups" / "cimitir-20260202-000000").exists()


def test_save_refuses_a_destination_inside_the_repository(tmp_path):
    repo = tmp_path / "repo"
    (repo / "shared_data-backups" / "cimitir-20260101-000000").mkdir(parents=True)
    (repo / "shared_data-backups" / "cimitir-20260101-000000" / "manifest.json").write_text("{}", encoding="utf-8")
    r = _run("save", "--repo", str(repo), "--dest", str(repo / "salvate"))
    assert r.returncode != 0 and "ÎN AFARA depozitului" in (r.stdout + r.stderr)
    assert not (repo / "salvate").exists()


def test_save_is_idempotent_and_keeps_a_differing_earlier_version(tmp_path):
    repo = tmp_path / "repo"
    folder = repo / "x-backups" / "cimitir-20260101-000000"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text("{\"v\": 1}", encoding="utf-8")
    saved = tmp_path / "salvate"
    assert "salvate: 1, deja salvate identic: 0" in _run("save", "--repo", str(repo), "--dest", str(saved)).stdout
    assert "salvate: 0, deja salvate identic: 1" in _run("save", "--repo", str(repo), "--dest", str(saved)).stdout
    (folder / "manifest.json").write_text("{\"v\": 2}", encoding="utf-8")
    assert "salvate: 1" in _run("save", "--repo", str(repo), "--dest", str(saved)).stdout
    assert (saved / "x-backups" / "cimitir-20260101-000000" / "manifest.json").read_text(encoding="utf-8") == "{\"v\": 1}"


def test_status_reports_counts(tmp_path):
    repo = tmp_path / "repo"
    (repo / "shared_data-backups" / "cimitir-20260101-000000").mkdir(parents=True)
    (repo / "shared_data-backups" / "cimitir-20260101-000000" / "manifest.json").write_text("{}", encoding="utf-8")
    r = _run("status", "--repo", str(repo), "--dest", str(tmp_path / "salvate"))
    assert "pe disc în depozit: 1" in r.stdout and "salvate" in r.stdout
