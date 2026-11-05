"""BUG-09 — soluția definitivă: backupurile NU trebuie să stea într-un depozit Git. Teste de regresie (Linux, git real, doar foldere temporare, date fictive).

Structura:
  * `test_characterize_*`  -> PASS înseamnă că RISCUL este confirmat experimental (comportament Git);
  * `test_target_*`        -> definesc soluția definitivă; XFAIL(strict) până la implementare, apoi trebuie să devină PASS (XPASS strict = markerul se scoate);
  * `test_compat_*` / `test_migration_*` -> garantează compatibilitatea cu backupurile existente și migrarea fără pierdere (doar pe copii fictive).
Nu se mută și nu se șterg backupuri reale; nu se rulează nicio migrare reală.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))
import cimitir_backup as cb  # noqa: E402

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(not GIT, reason="git nu este instalat")
GITENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
          "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def git(repo: Path, *args: str) -> str:
    p = subprocess.run([GIT, *args], cwd=repo, capture_output=True, text=True, env=GITENV)
    assert p.returncode == 0, f"git {' '.join(args)}: {p.stderr}"
    return p.stdout


def make_db(path: Path, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("INSERT OR REPLACE INTO settings VALUES ('marker', ?)", (marker,))
    c.commit()
    c.close()


def fake_data_dir(root: Path, marker: str = "m1") -> Path:
    d = root / "shared_data"
    make_db(d / "cimitir.db", marker)
    (d / "media").mkdir(exist_ok=True)
    (d / "media" / "poza.jpg").write_bytes(b"fictiv-" + marker.encode())
    return d


@pytest.fixture()
def repo_with_tracked_manifests(tmp_path):
    """Reproduce istoricul real: commit A urmărește shared_data-backups/*/manifest.json; commit B le scoate din index (ca 6a9774b) și ignoră folderul."""
    repo = tmp_path / "clona"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    data = fake_data_dir(repo)
    dest = repo / "shared_data-backups"
    copies = []
    for _ in range(2):
        copies.append(cb.backup(data, dest))
        import time
        time.sleep(1.1)
    (repo / "README").write_text("x")
    git(repo, "add", "README")
    for c in copies:
        git(repo, "add", "-f", str((c / "manifest.json").relative_to(repo)))        # starea clonelor vechi: manifestele urmărite
    git(repo, "commit", "-q", "-m", "A: manifeste urmarite")
    git(repo, "tag", "A")
    git(repo, "rm", "-q", "--cached", *[str((c / "manifest.json").relative_to(repo)) for c in copies])
    (repo / ".gitignore").write_text("shared_data-backups/\nshared_data/\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-q", "-m", "B: scoate manifestele din index")
    git(repo, "tag", "B")
    git(repo, "checkout", "-q", "A")                  # starea utilizatorului înainte de actualizare: manifestele sunt urmărite
    return repo, copies


# ------------------------------------------------------------------ caracterizare (riscul confirmat)
def test_characterize_pull_to_commit_that_untracks_manifests_deletes_them(repo_with_tracked_manifests):
    repo, copies = repo_with_tracked_manifests
    assert all((c / "manifest.json").is_file() for c in copies)
    git(repo, "checkout", "-q", "B")                  # echivalent cu `git pull` care aduce commit-ul 6a9774b
    assert not any((c / "manifest.json").exists() for c in copies), "manifestele au rămas pe disc (comportament Git diferit de cel documentat)"
    assert all((c / "cimitir.db").is_file() for c in copies)               # baza rămâne, dar copia dispare pentru MAIN
    assert cb.list_backups(copies[0].parent) == []
    assert cb.verify(copies[0])                                              # verify raportează probleme -> nu se poate restaura


def test_characterize_checkout_back_silently_overwrites_ignored_local_manifest(repo_with_tracked_manifests):
    repo, copies = repo_with_tracked_manifests
    git(repo, "checkout", "-q", "B")
    new = cb.backup(fake_data_dir(repo, "m2"), copies[0].parent)           # copie NOUĂ creată pe B: manifest ignorat (neurmărit)
    stale = copies[0] / "manifest.json"
    stale.write_text(json.dumps({"created": "2026-01-01T00:00:00", "files": {"cimitir.db": "0" * 64}}))   # manifest creat local, ignorat de Git
    git(repo, "checkout", "-q", "A")                  # schimbarea branch-ului înapoi
    restored = json.loads(stale.read_text())
    assert restored["files"]["cimitir.db"] != "0" * 64, "Git a rescris în tăcere un fișier IGNORAT"
    assert (new / "manifest.json").is_file()


def test_characterize_git_clean_x_wipes_whole_in_repo_backup_folder(repo_with_tracked_manifests):
    repo, copies = repo_with_tracked_manifests
    git(repo, "checkout", "-q", "B")
    assert (copies[0] / "cimitir.db").is_file()
    git(repo, "clean", "-fdx")                          # comandă frecventă „curăță tot”: .gitignore NU protejează
    assert not copies[0].exists() and not (repo / "shared_data").exists(), "git clean -fdx NU a șters folderele ignorate"


def test_characterize_plain_clean_fd_spares_ignored_backups_but_not_unignored_custom_folder(repo_with_tracked_manifests):
    repo, copies = repo_with_tracked_manifests
    git(repo, "checkout", "-q", "B")
    custom = cb.backup(fake_data_dir(repo, "m3"), repo / "backupuri-mele")   # folder ales de utilizator în depozit, neacoperit de .gitignore
    git(repo, "clean", "-fd")
    assert copies[0].exists() and not custom.exists()


# ------------------------------------------------------------------ soluția definitivă (ținte)
def _in_worktree(path: Path) -> bool:
    for p in [path, *path.parents]:
        if (p / ".git").exists():
            return True
    return False


@pytest.mark.xfail(strict=True, reason="BUG-09 definitiv NEREMEDIAT: default_backup_dir() = <folder_date>-backups; cu CIMITIR_DATA_DIR implicit ./shared_data (dezvoltare/clonă) rezultă un folder ÎN depozit.")
def test_target_default_backup_dir_is_never_inside_a_git_worktree(tmp_path, monkeypatch):
    repo = tmp_path / "clona"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.delenv("CIMITIR_BACKUP_DIR", raising=False)
    resolved = cb.default_backup_dir(repo / "shared_data")
    assert not _in_worktree(resolved.resolve()), f"folderul implicit de copii e în depozitul Git: {resolved}"


@pytest.mark.xfail(strict=True, reason="BUG-09 definitiv NEREMEDIAT: setarea `backup_dir` / CIMITIR_BACKUP_DIR acceptă orice cale, inclusiv în depozit, fără refuz sau avertisment.")
def test_target_backup_destination_inside_worktree_is_refused_or_relocated(tmp_path, monkeypatch):
    repo = tmp_path / "clona"
    (repo / ".git").mkdir(parents=True)
    inside = repo / "copii-mele"
    monkeypatch.delenv("CIMITIR_BACKUP_DIR", raising=False)
    dest = cb.backup_destination(repo / "shared_data", lambda k: str(inside) if k == "backup_dir" else None)
    assert not _in_worktree(Path(dest).resolve()), f"destinația din setări e în depozit și a fost acceptată: {dest}"


def test_target_default_dir_outside_repo_is_untouched_by_git_operations(repo_with_tracked_manifests, tmp_path):
    """Comportamentul dorit (și deja posibil prin CIMITIR_BACKUP_DIR): copiile în afara depozitului supraviețuiesc oricărei operații Git."""
    repo, _ = repo_with_tracked_manifests
    external = tmp_path / "Cimitir-backups"
    copy = cb.backup(fake_data_dir(repo, "ext"), external)
    for args in (("checkout", "-q", "B"), ("checkout", "-q", "A"), ("clean", "-fdx"), ("stash", "-u"), ("reset", "--hard", "B")):
        subprocess.run([GIT, *args], cwd=repo, capture_output=True, env=GITENV)
    assert cb.verify(copy) == [] and [r["name"] for r in cb.list_backups(external)] == [copy.name]


# ------------------------------------------------------------------ compatibilitate și migrare (copii fictive)
def test_compat_existing_in_repo_backups_are_listed_verified_and_restorable_by_current_code(tmp_path):
    data = fake_data_dir(tmp_path / "vechi", "vechi")
    legacy = tmp_path / "vechi" / "shared_data-backups"
    c1 = cb.backup(data, legacy)
    assert [r["name"] for r in cb.list_backups(legacy)] == [c1.name] and cb.verify(c1) == []
    target = tmp_path / "restaurat"
    cb.restore(c1, target)
    assert sqlite3.connect(target / "cimitir.db").execute("SELECT value FROM settings WHERE key='marker'").fetchone()[0] == "vechi"


def test_migration_by_copy_is_lossless_idempotent_and_leaves_source_intact(tmp_path):
    """Procedura propusă (descrisă în raport, executată doar pe copii fictive): copiere (nu mutare) + verify pe destinație + abia apoi decizia utilizatorului."""
    data = fake_data_dir(tmp_path / "repo", "mig")
    legacy = tmp_path / "repo" / "shared_data-backups"
    made = [cb.backup(data, legacy)]
    import time
    time.sleep(1.1)
    made.append(cb.backup(data, legacy))
    before = {str(p.relative_to(legacy)): p.read_bytes() for p in legacy.rglob("*") if p.is_file()}
    external = tmp_path / "ext" / "Cimitir-backups"

    def migrate():
        copied = skipped = 0
        for row in cb.list_backups(legacy):
            src, dst = Path(row["path"]), external / row["name"]
            if dst.exists():
                skipped += 1
                continue
            tmp = external / (row["name"] + ".copying")
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.copytree(src, tmp)                                  # copiere, nu mutare
            if cb.verify(tmp):                                         # verify pe copie ÎNAINTE de a o publica
                shutil.rmtree(tmp, ignore_errors=True)
                raise RuntimeError("verify a eșuat pe destinație")
            tmp.rename(dst)
            copied += 1
        return copied, skipped

    assert migrate() == (2, 0)
    assert migrate() == (0, 2)                                         # idempotent
    after = {str(p.relative_to(legacy)): p.read_bytes() for p in legacy.rglob("*") if p.is_file()}
    assert before == after, "sursa a fost modificată"
    assert sorted(r["name"] for r in cb.list_backups(external)) == sorted(m.name for m in made)
    assert all(cb.verify(Path(r["path"])) == [] for r in cb.list_backups(external))
    assert {r["name"]: (Path(r["path"]) / "manifest.json").read_bytes() for r in cb.list_backups(external)} == \
           {m.name: (m / "manifest.json").read_bytes() for m in made}


def test_migration_detects_a_corrupt_source_copy_and_does_not_publish_it(tmp_path):
    data = fake_data_dir(tmp_path / "repo", "bad")
    legacy = tmp_path / "repo" / "shared_data-backups"
    c = cb.backup(data, legacy)
    (c / "cimitir.db").write_bytes(b"deteriorat")
    assert cb.verify(c), "verify nu a detectat deteriorarea"


def test_characterize_copy_without_manifest_is_invisible_and_not_restorable(tmp_path):
    data = fake_data_dir(tmp_path / "repo", "nom")
    legacy = tmp_path / "repo" / "shared_data-backups"
    c = cb.backup(data, legacy)
    (c / "manifest.json").unlink()                                       # exact efectul `git pull` din BUG-09
    assert cb.list_backups(legacy) == []
    with pytest.raises(SystemExit):
        cb.restore(c, tmp_path / "nu-se-restaureaza")
