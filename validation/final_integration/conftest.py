"""Audit final — infrastructură: reutilizează (fără a o modifica) conftest-ul din validation/bug_audit (clienți MAIN izolați, server clasic pe PG local temporar)."""
import importlib.util
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "bug_audit" / "conftest.py"
_spec = importlib.util.spec_from_file_location("bug_audit_conftest", _SRC)
_ba = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ba)

ROOT = _ba.ROOT
PY = _ba.PY
needs_pg = _ba.needs_pg
run_main = _ba.run_main
pg_rows = _ba.pg_rows
main_env = _ba.main_env
classic_server = _ba.classic_server
