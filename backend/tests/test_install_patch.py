"""Instalatorul de patch (scripts/install_patch.py): ramură greșită, conflicte cu modificări locale, backup, idempotență, rollback."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "install_patch.py"


def g(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture()
def world(tmp_path):
    repo = tmp_path / "v3"
    repo.mkdir()
    g(repo, "init", "-q", "-b", "sync-branch")
    g(repo, "config", "user.email", "t@t"); g(repo, "config", "user.name", "t")
    (repo / "a.py").write_text("one\ntwo\nthree\n"); (repo / "b.py").write_text("x\ny\n"); (repo / "local_only.txt").write_text("keep\n")
    g(repo, "add", "-A"); g(repo, "commit", "-qm", "base")
    base = g(repo, "rev-parse", "HEAD")
    (repo / "a.py").write_text("one\nTWO\nthree\n"); (repo / "new.py").write_text("new\n")
    g(repo, "add", "-A"); g(repo, "commit", "-qm", "feature")
    patch = tmp_path / "p.patch"
    patch.write_text(g(repo, "diff", base, "HEAD") + "\n")
    g(repo, "reset", "-q", "--hard", base)
    return {"repo": repo, "patch": patch, "base": base}


def run(w, *extra, branch="sync-branch"):
    return subprocess.run([sys.executable, str(SCRIPT), "--repo", str(w["repo"]), "--patch", str(w["patch"]), "--branch", branch, "--base", w["base"], *extra],
                          capture_output=True, text=True)


def test_wrong_branch_changes_nothing(world):
    r = run(world, branch="main")
    assert r.returncode == 2 and "Ramura curentă" in r.stdout
    assert (world["repo"] / "a.py").read_text() == "one\ntwo\nthree\n" and not (world["repo"] / "new.py").exists()


def test_local_modification_on_patched_file_stops_without_overwrite(world):
    (world["repo"] / "a.py").write_text("one\ntwo\nthree\nMY LOCAL EDIT\n")
    r = run(world)
    assert r.returncode == 2 and "modificări locale" in r.stdout and "a.py" in r.stdout
    assert "MY LOCAL EDIT" in (world["repo"] / "a.py").read_text() and not (world["repo"] / "new.py").exists()
    assert not (world["repo"] / ".patch_backups").exists()


def test_clean_apply_with_unrelated_local_changes_backup_and_idempotent_and_rollback(world):
    repo = world["repo"]
    (repo / "local_only.txt").write_text("my edit\n"); (repo / "untracked.txt").write_text("u\n")
    r = run(world)
    assert r.returncode == 0, r.stdout
    assert (repo / "a.py").read_text() == "one\nTWO\nthree\n" and (repo / "new.py").exists()
    assert (repo / "local_only.txt").read_text() == "my edit\n" and (repo / "untracked.txt").exists()      # modificările locale neatinse
    backups = list((repo / ".patch_backups").glob("*/a.py"))
    assert backups and backups[0].read_text() == "one\ntwo\nthree\n"
    again = run(world)
    assert again.returncode == 0 and "DEJA aplicat" in again.stdout and (repo / "a.py").read_text() == "one\nTWO\nthree\n"
    rb = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(repo), "--rollback"], capture_output=True, text=True)
    assert rb.returncode == 0, rb.stdout
    assert (repo / "a.py").read_text() == "one\ntwo\nthree\n" and not (repo / "new.py").exists() and (repo / "local_only.txt").read_text() == "my edit\n"


def test_rollback_refuses_if_file_changed_after_install(world):
    repo = world["repo"]
    assert run(world).returncode == 0
    (repo / "a.py").write_text("changed after\n")
    rb = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(repo), "--rollback"], capture_output=True, text=True)
    assert rb.returncode == 2 and (repo / "a.py").read_text() == "changed after\n"


def test_patch_with_path_traversal_is_refused(world, tmp_path):
    bad = tmp_path / "bad.patch"
    bad.write_text("diff --git a/../evil.txt b/../evil.txt\nnew file mode 100644\n--- /dev/null\n+++ b/../evil.txt\n@@ -0,0 +1 @@\n+x\n")
    w = {**world, "patch": bad}
    r = run(w)
    assert r.returncode == 2 and "Cale nepermisă" in r.stdout and not (tmp_path / "evil.txt").exists()
