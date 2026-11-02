"""Pasul B – diagnostic de integritate a evidențelor CIMITIR (MAIN/SQLite). STRICT CITIRE: nu modifică nimic, nu corectează automat.

    from cimitir_integrity import run_on_file
    rep = run_on_file(r"C:\\...\\cimitir.db")
"""
from .model import CATALOG, CATEGORIES, SEVERITIES, Issue, Report, redact
from .context import open_readonly
from .runner import STEPS, TOOL_VERSION, fingerprint, run_checks, run_on_file
from .report import to_csv, to_json, to_markdown, write_all

__all__ = ["CATALOG", "CATEGORIES", "SEVERITIES", "Issue", "Report", "redact", "open_readonly", "STEPS", "TOOL_VERSION", "fingerprint",
           "run_checks", "run_on_file", "to_csv", "to_json", "to_markdown", "write_all"]
