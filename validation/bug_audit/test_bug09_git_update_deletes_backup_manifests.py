"""BUG-09 (A2) — actualizarea depozitului (pull/checkout peste commit-ul 6a9774b) șterge de pe disc manifestele copiilor din
`shared_data-backups/`, iar copiile devin invizibile și nerestaurabile.

Cauză: commit 6a9774b a făcut `git rm --cached` pe cele 4 `shared_data-backups/*/manifest.json`. Pentru un depozit care le are
URMĂRITE (orice clonă dinainte), trecerea la commit-ul nou le ȘTERGE din working tree. Fără manifest, shared/cimitir_backup.py:198
`list_backups` nu mai listează copia, iar `verify`/`restore` o refuză (:82-86, :143-145).
Reproducere locală: clonă temporară din depozitul local (nu atinge istoricul, nu face push), checkout pe părintele lui 6a9774b,
apoi checkout pe 6a9774b; se verifică fișierele de pe disc.
AȘTEPTAT: manifestele rămân pe disc. OBȚINUT: sunt șterse. Datele (cimitir.db) nu erau în Git, deci copia rămâne fără manifest.
"""
import shutil
import subprocess

import pytest

import cimitir_backup as cb
from conftest import ROOT

COMMIT = "6a9774b"


@pytest.mark.xfail(strict=True, reason="Comportament Git al unui commit existent în istoric (6a9774b), imposibil de schimbat prin cod fără rescrierea istoricului "
                   "(interzis: fără force-push). Atenuat procedural: scripts/salveaza_manifeste_backup.py (vezi test_bug09_mitigation.py). "
                   "strict=True: dacă situația se schimbă (XPASS), testul eșuează și trebuie reanalizat.")
@pytest.mark.skipif(not shutil.which("git"), reason="git lipsește")
def test_update_over_6a9774b_keeps_backup_manifests_on_disk(tmp_path):
    if subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", COMMIT], capture_output=True).returncode != 0:
        pytest.skip("commit-ul 6a9774b nu există în acest depozit")
    clone = tmp_path / "clona"
    subprocess.run(["git", "clone", "-q", "--no-checkout", str(ROOT), str(clone)], check=True)
    subprocess.run(["git", "-C", str(clone), "checkout", "-q", COMMIT + "^"], check=True)
    before = sorted(p.relative_to(clone).as_posix() for p in (clone / "shared_data-backups").glob("*/manifest.json"))
    assert len(before) == 4, before
    for m in (clone / "shared_data-backups").glob("*/manifest.json"):
        (m.parent / "cimitir.db").write_bytes(b"baza locala (nu este in Git)")      # copia reală de pe calculator
    listed_before = len(cb.list_backups(clone / "shared_data-backups"))
    subprocess.run(["git", "-C", str(clone), "checkout", "-q", COMMIT], check=True)
    after = sorted(p.relative_to(clone).as_posix() for p in (clone / "shared_data-backups").glob("*/manifest.json"))
    listed_after = len(cb.list_backups(clone / "shared_data-backups"))
    assert after == before, (f"manifestele au fost șterse de pe disc la actualizare: înainte {len(before)}, după {len(after)}; "
                             f"copii listate în MAIN: {listed_before} -> {listed_after}")
