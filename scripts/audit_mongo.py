#!/usr/bin/env python
"""audit_mongo.py — raportează (doar citire) unde mai depinde codul de MongoDB. Nu modifică nimic, nu se conectează la Mongo.

Cod de ieșire: 0 = Mongo dezactivat și fără import la rulare; 1 = Mongo activ sau importat (nu se poate elimina încă).
Utilizare: python scripts/audit_mongo.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "shared", ROOT / "backend" / "cimitir"):
    sys.path.insert(0, str(_p))

import cimitir_config  # noqa: E402

PATTERN = re.compile(r"mongo_store|MONGO_URL|ENABLE_MONGO|mongo_enabled|pymongo|motor==|(?:from|import) motor\b|MONGO_SOURCE|engine\W+==\W+.mongo|mongodb", re.I)
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pgdata", ".ruff_cache", "test_reports", "shared_data", "shared_data-backups", ".emergent"}
SKIP_FILES = {"scripts/audit_mongo.py", "ETAPA10_ELIMINARE_MONGO.md", "memory/PRD.md", "test_result.md"}
EXT = {".py", ".html", ".css", ".txt", ".md", ".yml", ".yaml", ".bat", ".sh", ".env", ".example", ".json", ".spec"}


def category(rel: str) -> str:
    if rel == "backend/cimitir/mongo_store.py":
        return "1-modul-legacy"
    if rel.endswith(".md"):
        return "5-documentatie"
    if "/tests/" in rel or rel.startswith("tests/"):
        return "4-teste"
    if rel.endswith((".txt", ".env", ".yml", ".yaml", ".example")) or rel == "backend/.env":
        return "3-config/dependinte"
    return "2-cod-aplicatie"


def scan() -> dict:
    found: dict = {}
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT).as_posix()
        if not path.is_file() or SKIP_DIRS & set(path.parts) or rel in SKIP_FILES:
            continue
        if path.suffix not in EXT and path.name != ".env":
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        hits = [(i, ln.strip()[:110]) for i, ln in enumerate(lines, 1) if PATTERN.search(ln)]
        if hits:
            found[rel] = hits
    return found


def main() -> int:
    enabled = cimitir_config.mongo_enabled()
    print(f"CIMITIR_ENABLE_MONGO activ: {'DA' if enabled else 'nu'} | motor configurat: {cimitir_config.db_engine()}")
    found = scan()
    for cat in sorted({category(r) for r in found}):
        print(f"\n== {cat}")
        for rel in sorted(r for r in found if category(r) == cat):
            print(f"  {rel}: {len(found[rel])} linii (ex. l.{found[rel][0][0]}: {found[rel][0][1]})")
    print(f"\nTotal fișiere cu referințe: {len(found)}")
    print("Verdict: " + ("MONGO ACTIV, nu se poate elimina" if enabled else "Mongo dezactivat; ștergerea cere criteriile din ETAPA10_ELIMINARE_MONGO.md (secțiunea 8)"))
    return 1 if enabled else 0


if __name__ == "__main__":
    sys.exit(main())
