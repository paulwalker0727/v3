"""Conexiune STRICT read-only la SQLite și contextul de rulare al verificărilor."""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .model import CATALOG, Issue, SEV_RANK

_OK, _DENY = sqlite3.SQLITE_OK, sqlite3.SQLITE_DENY
_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, getattr(sqlite3, "SQLITE_RECURSIVE", 33)}
_ALLOWED_PRAGMAS = {"table_info", "table_xinfo", "foreign_key_list", "index_list", "database_list"}


def _authorizer(action, arg1, arg2, dbname, source):
    """Orice altceva decât SELECT/citire/funcții și PRAGMA-uri de inspecție este refuzat de motorul SQLite însuși."""
    if action in _ALLOWED_ACTIONS:
        return _OK
    if action == sqlite3.SQLITE_PRAGMA and str(arg1).lower() in _ALLOWED_PRAGMAS:
        return _OK
    return _DENY


def open_readonly(path: str) -> sqlite3.Connection:
    """Deschide fișierul fără a-l putea modifica: mode=ro (nu creează fișierul), query_only și autorizator care refuză orice scriere/DDL.

    Dacă baza nu are fișier -wal (nicio modificare necontrolată în jurnal), se folosește immutable=1: SQLite nu mai creează fișierele
    auxiliare -wal/-shm lângă bază (zero efecte pe disc). Dacă -wal există, se citește normal (mode=ro) ca să se vadă și datele din jurnal."""
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"Baza de date nu există: {p}")
    has_wal = Path(str(p) + "-wal").exists()
    conn = sqlite3.connect(f"{p.as_uri()}?mode=ro" + ("" if has_wal else "&immutable=1"), uri=True, timeout=15.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    conn.set_authorizer(_authorizer)
    return conn


class Ctx:
    """Context comun: acces SELECT-only, adăugare probleme cu plafon per verificare, schemă tolerantă la tabele/coloane lipsă."""

    def __init__(self, conn: sqlite3.Connection, today: Optional[date] = None, max_per_code: int = 5000,
                 deep: bool = False, media_root: Optional[str] = None, include_info: bool = True):
        self.conn, self.today, self.max_per_code = conn, today or date.today(), max_per_code
        self.deep, self.media_root, self.include_info = deep, media_root, include_info
        self.issues: List[Issue] = []
        self.found: Dict[str, int] = {}
        self._n: Dict[str, int] = {}
        self.truncated: Dict[str, int] = {}
        self.skipped: List[str] = []
        self._cols: Dict[str, List[str]] = {}
        self._seen = set()

    # --- schemă
    def columns(self, table: str) -> List[str]:
        if table not in self._cols:
            try:
                self._cols[table] = [r[1] for r in self.conn.execute(f"PRAGMA table_info({table})").fetchall()]
            except sqlite3.DatabaseError:
                self._cols[table] = []
        return self._cols[table]

    def has(self, table: str, *cols: str) -> bool:
        have = set(self.columns(table))
        return bool(have) and all(c in have for c in cols)

    def need(self, table: str, *cols: str) -> bool:
        if self.has(table, *cols):
            return True
        self.skipped.append(f"{table}({', '.join(cols)})")
        return False

    def q(self, sql: str, args: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(sql, tuple(args)).fetchall()]

    # --- probleme
    def add(self, code: str, entity: str, record_id: Any, description: str, evidence: Optional[Dict[str, Any]] = None,
            severity: Optional[str] = None, uid: Optional[str] = None, cod_loc: Optional[str] = None) -> None:
        sev0, cat, title, rec, fixable = CATALOG[code]
        sev = severity or sev0
        if sev == "info" and not self.include_info:
            return
        rid = None if record_id is None else str(record_id)
        key = (code, entity, rid, description)
        if key in self._seen:
            return
        self._seen.add(key)
        fk = f"{code}|{sev}"
        self.found[fk] = self.found.get(fk, 0) + 1
        n = self._n[code] = self._n.get(code, 0) + 1
        if n > self.max_per_code and not code.startswith("META"):
            self.truncated[code] = n
            return
        self.issues.append(Issue(code=code, severity=sev, category=cat, entity=entity, record_id=rid, description=description,
                                 evidence=evidence or {}, recommendation=rec, auto_fixable=fixable, title=title, uid=uid, cod_loc=cod_loc))

    def sorted_issues(self) -> List[Issue]:
        return sorted(self.issues, key=lambda i: (SEV_RANK[i.severity], i.category, i.code, _num(i.record_id)))


def _num(v: Optional[str]):
    try:
        return (0, int(v), "")
    except (TypeError, ValueError):
        return (1, 0, v or "")
