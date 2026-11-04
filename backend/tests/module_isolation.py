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


def purge_project_modules() -> None:
    """Scoate din cache TOATE modulele proiectului (backend/, backend/cimitir/, shared/): un `import server` nou nu mai folosește
    referințe vechi (ex. `citizen_api` legat de un `store` dintr-un test anterior)."""
    roots = tuple(str(_ROOT / d) + "/" for d in ("backend", "shared", "desktop"))
    tests_dir = str(_ROOT / "backend" / "tests") + "/"
    for name, mod in list(sys.modules.items()):
        f = getattr(mod, "__file__", None) or ""
        if f.startswith(roots) and not f.startswith(tests_dir) and "/desktop/tests/" not in f:
            sys.modules.pop(name, None)
    purge_colliding_modules()
