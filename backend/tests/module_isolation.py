"""Izolarea modulelor cu același nume în backend/ și desktop/ (ex. `mobile_upload`, `database`) pentru testele care încarcă `server`."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _stems(directory: Path) -> set:
    return {f.stem for f in directory.glob("*.py")}


def purge_colliding_modules() -> None:
    """Scoate din cache modulele omonime și pune backend/ primul în sys.path: `import server` rezolvă mereu modulele backend."""
    colliding = (_stems(_ROOT / "backend") | _stems(_ROOT / "backend" / "cimitir")) & _stems(_ROOT / "desktop")
    for name in colliding:
        sys.modules.pop(name, None)
    for p in (str(_ROOT / "backend" / "cimitir"), str(_ROOT / "backend")):
        if p in sys.path:
            sys.path.remove(p)
        sys.path.insert(0, p)
