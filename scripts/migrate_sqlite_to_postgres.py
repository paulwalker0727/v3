#!/usr/bin/env python
"""migrate_sqlite_to_postgres.py — migrare SQLite -> PostgreSQL MULTI-TENANT, cu raport MIGRATION_REPORT.md.

Importul este limitat la UN singur tenant (organizație + cimitir), care trebuie să existe deja în PostgreSQL:
  --organization-uid UUID   (obligatoriu)  organizația țintă
  --cemetery-uid UUID       (obligatoriu)  cimitirul țintă; trebuie să aparțină organizației și devine settings.cemetery_id

Moduri:
  (implicit) / --dry-run   Analizează sursa SQLite, verifică tenantul țintă și scrie raportul (înainte / după, pe tabele). NU scrie nimic în PostgreSQL.
  --apply                  Încarcă datele într-o singură tranzacție (totul sau nimic), apoi verifică; abia apoi COMMIT.

Garanții:
  * Toate rândurile încărcate primesc organization_uid / cemetery_uid din argumente; nu se citește și nu se scrie în alt cimitir.
  * Tenantul țintă trebuie să fie gol (altfel --truncate-target șterge DOAR rândurile lui). Tabelele fără tenant (users, settings,
    cloud_sync_*) nu se suprascriu: dacă conțin deja date, importul se oprește.
  * settings.cemetery_id este aliniat cu cemeteries.uid; dacă indică alt cimitir care are date, importul se oprește.
  * Proiectul Supabase de producție este blocat (nici dry-run, nici apply).
  * SQLite se deschide DOAR read-only; nu se șterge, nu se modifică (dimensiune + mtime + SHA-256 comparate înainte/după).
  * Fișierele media nu se ating (se verifică doar dacă există).
  * Se păstrează: ID-urile, UID-urile existente, parolele hash (octet cu octet), relațiile, data/ora (text original) și auditul.
    UID-urile lipsă se derivă determinist (uuid5 din cemetery_uid:tabel:id) => rularea repetată dă aceleași UID-uri.
  * Raportul NU conține parole/hash-uri/valori din settings/date personale — doar numere, nume de tabele/coloane și ID-uri.

Utilizare:
  python scripts/migrate_sqlite_to_postgres.py --source shared_data/cimitir.db --organization-uid ORG --cemetery-uid CEM
  DATABASE_URL=... python scripts/migrate_sqlite_to_postgres.py --source cimitir.db --organization-uid ORG --cemetery-uid CEM --apply [--on-problem fix] [--truncate-target]

Coduri de ieșire: 0 OK | 1 eroare fatală (sursă/țintă/tenant inaccesibil, confirmare lipsă, producție) | 2 argumente
                  | 3 probleme BLOCANTE (nu s-a scris nimic) | 4 verificarea a eșuat (ROLLBACK) sau sursa s-a schimbat.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
import time
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "shared", ROOT / "backend" / "cimitir"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import cimitir_config  # noqa: E402
import cimitir_test_guard  # noqa: E402
import pg_compat  # noqa: E402

# Părinți înaintea copiilor (cerut de cheile străine la încărcare).
TABLE_ORDER = [
    "settings", "concesionari", "citizens", "locuri_de_veci", "sectoare", "guides", "rows_ghid", "loc_extra", "loc_holders",
    "contracte", "inhumati", "photos", "amenajari_avize", "plati", "documents", "cereri_concesionare", "citizen_resets",
    "users", "audit_log", "sync_ops", "email_outbox", "ai_scan_history", "qr_generations",
    "cloud_sync_map", "cloud_sync_outbox", "cloud_sync_docs",
    "sync_tombstones", "sync_conflicts",   # Etapa 6: doar pe server (nu există în SQLite)
    "media_objects",                       # Etapa 7: metadate fișiere (octeții sunt în storage); doar pe server
]
UID_TABLES = {"locuri_de_veci", "concesionari", "contracte", "inhumati", "sectoare", "photos", "amenajari_avize", "guides",
              "rows_ghid", "plati", "documents", "citizens", "cereri_concesionare", "users", "ai_scan_history", "qr_generations",
              "sync_tombstones", "sync_conflicts"}
# (copil, coloană, părinte, coloană părinte, nullable) — cheile străine din PostgreSQL (001 + 002).
LOGICAL_FKS = [
    ("contracte", "concesionar_id", "concesionari", "id", True), ("contracte", "id_loc", "locuri_de_veci", "id", True),
    ("inhumati", "id_loc", "locuri_de_veci", "id", True), ("inhumati", "contract_id", "contracte", "id", True),
    ("photos", "cod_loc", "locuri_de_veci", "cod_loc", False), ("amenajari_avize", "cod_loc", "locuri_de_veci", "cod_loc", False),
    ("cereri_concesionare", "concesionar_id", "concesionari", "id", True), ("cereri_concesionare", "citizen_id", "citizens", "id", True),
    ("cereri_concesionare", "contract_id", "contracte", "id", True), ("citizen_resets", "citizen_id", "citizens", "id", False),
    ("plati", "contract_id", "contracte", "id", True), ("qr_generations", "loc_id", "locuri_de_veci", "id", True),
]
# documents.entity_type -> (tabel, coloană)
DOC_ENTITY = {"loc": ("locuri_de_veci", "cod_loc"), "inhumat": ("inhumati", "id"), "concesionar": ("concesionari", "id"),
              "contract": ("contracte", "id"), "plata": ("plati", "id"), "aviz": ("amenajari_avize", "id"),
              "cerere": ("cereri_concesionare", "id")}
GEOMETRY_MIN_POINTS = {("locuri_de_veci", "points"): 3, ("sectoare", "points"): 3, ("guides", "points"): 2, ("rows_ghid", "baseline"): 2}
JSON_COLUMNS = set(GEOMETRY_MIN_POINTS) | {("loc_extra", "extra_json")}
UNIQUE_RULES = {"locuri_de_veci": ["cod_loc", "uid"], "users": ["username", "uid"], "citizens": ["email", "uid"],
                "cereri_concesionare": ["cod_urmarire"]}
CI_WARN = {"locuri_de_veci": "cod_loc", "users": "username", "citizens": "email"}   # duplicate ignorând majusculele
NATURAL_WARN = {"contracte": "numar_contract", "concesionari": "cnp"}             # nu sunt unice în aplicație
# stare de sincronizare a calculatorului MAIN (cache SQLite), fără tenant: nu se importă într-un server multi-tenant
NOT_IMPORTED_GLOBAL = ("cloud_sync_map", "cloud_sync_outbox", "cloud_sync_docs")
UID_NAMESPACE = uuid.UUID("6f1c0c3e-5d36-4c1e-9a55-2d5b8a0c7e11")
SAMPLE = 20  # câte ID-uri se listează în raport per problemă


# ------------------------------------------------------------------------------------------------ utilitare
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_state(path: Path) -> Dict[str, Any]:
    st = path.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": sha256_file(path)}


def digest(rows: Any) -> str:
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, default=str, sort_keys=True).encode("utf-8")).hexdigest()


def empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def count_by(items) -> Dict[str, int]:
    out: Dict[str, int] = defaultdict(int)
    for i in items:
        out[str(i)] += 1
    return dict(out)


class Problems:
    """Colectează constatările: BLOCK (oprește --apply), WARN, INFO."""

    def __init__(self):
        self.items: List[Dict[str, Any]] = []

    def add(self, level: str, code: str, table: str, message: str, ids: Optional[List[Any]] = None, count: Optional[int] = None):
        ids = list(ids or [])
        self.items.append({"level": level, "code": code, "table": table, "message": message,
                           "count": count if count is not None else len(ids), "ids": ids[:SAMPLE]})

    def blocking(self) -> List[Dict[str, Any]]:
        return [p for p in self.items if p["level"] == "BLOCK"]


class Plan:
    """Rezultatul analizei: date convertite gata de încărcat + constatări."""

    def __init__(self):
        self.problems = Problems()
        self.report: Dict[str, Any] = {}
        self.rows: Dict[str, List[Dict[str, Any]]] = {}
        self.fixes: List[Dict[str, Any]] = []
        self.skipped: Dict[str, List[Any]] = defaultdict(list)


# ------------------------------------------------------------------------------------------------ sursa SQLite
class Source:
    def __init__(self, path: Path):
        self.decode_fallbacks = 0
        wal = Path(str(path) + "-wal")
        # immutable=1: SQLite nu creează/atinge fișiere auxiliare (-shm/-wal). Dacă există WAL cu date, folosim doar mode=ro
        # (altfel tranzacțiile din WAL nu ar fi vizibile) — atunci raportul avertizează.
        self.immutable = not (wal.exists() and wal.stat().st_size > 0)
        uri = ("file:" + str(path.resolve()).replace("?", "%3f").replace("#", "%23") + "?mode=ro"
               + ("&immutable=1" if self.immutable else ""))
        self.conn = sqlite3.connect(uri, uri=True)
        self.conn.row_factory = sqlite3.Row
        self.conn.text_factory = self._decode
        self.conn.execute("PRAGMA query_only = ON")

    def _decode(self, raw: bytes) -> str:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            self.decode_fallbacks += 1
            return raw.decode("utf-8", errors="replace")

    def tables(self) -> List[str]:
        return [r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]

    def columns(self, table: str) -> List[Dict[str, Any]]:
        return [{"name": r["name"], "type": (r["type"] or "").upper()} for r in self.conn.execute(f'PRAGMA table_info("{table}")')]

    def rows(self, table: str) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(f'SELECT * FROM "{table}"')]

    def close(self):
        self.conn.close()


# ------------------------------------------------------------------------------------------------ schema țintă (PostgreSQL)
def target_meta(url: str) -> Dict[str, Any]:
    """{table: {"cols": {col: {kind, nullable, default}}, "pk": [..]}, "_db": nume} din information_schema (doar citire)."""
    import psycopg
    kinds = {"smallint": "int", "integer": "int", "bigint": "int", "double precision": "float", "real": "float",
             "numeric": "float", "jsonb": "json", "json": "json", "timestamp with time zone": "ts"}
    meta: Dict[str, Any] = {}
    with psycopg.connect(url, connect_timeout=10, autocommit=True, application_name="cimitir-migrate") as c:
        c.read_only = True
        for t, col, typ, nullable, default, ident in c.execute(
                "SELECT table_name, column_name, data_type, is_nullable, column_default, is_identity "
                "FROM information_schema.columns WHERE table_schema=current_schema() ORDER BY table_name, ordinal_position"):
            meta.setdefault(t, {"cols": {}, "pk": []})["cols"][col] = {
                "kind": kinds.get(typ, "text"), "nullable": nullable == "YES", "default": default is not None or ident == "YES"}
        for m in meta.values():
            m["scope"] = "cem" if "cemetery_uid" in m["cols"] else ("org" if "organization_uid" in m["cols"] else "global")
        for t, col in c.execute(
                "SELECT tc.table_name, kcu.column_name FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu USING (constraint_name, table_schema, table_name) "
                "WHERE tc.table_schema=current_schema() AND tc.constraint_type='PRIMARY KEY' ORDER BY kcu.ordinal_position"):
            meta[t]["pk"].append(col)
        meta["_db"] = c.execute("SELECT current_database()").fetchone()[0]
    return meta


# ------------------------------------------------------------------------------------------------ analiză
def derive_uid(cemetery: str, table: str, pk: Any) -> str:
    return str(uuid.uuid5(UID_NAMESPACE, f"{cemetery}:{table}:{pk}"))


def check_geometry(value: Any, min_points: int) -> Tuple[str, Optional[str]]:
    """('empty'|'valid'|'invalid', json_text)."""
    if empty(value):
        return "empty", "[]"
    try:
        data = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        return "invalid", None
    if not isinstance(data, list):
        return "invalid", None
    if not data:
        return "empty", "[]"
    for p in data:
        if (not isinstance(p, (list, tuple))) or len(p) != 2 or any(
                isinstance(c, bool) or not isinstance(c, (int, float)) or not math.isfinite(c) for c in p):
            return "invalid", None
    return ("valid" if len(data) >= min_points else "invalid"), json.dumps(data)


def hash_scheme(h: Any) -> str:
    if empty(h):
        return "gol"
    h = str(h)
    for prefix, name in (("pbkdf2", "pbkdf2"), ("$pbkdf2", "pbkdf2"), ("$2a$", "bcrypt"), ("$2b$", "bcrypt"), ("$2y$", "bcrypt"),
                         ("scrypt", "scrypt"), ("$argon2", "argon2"), ("argon2", "argon2"), ("sha256$", "sha256")):
        if h.startswith(prefix):
            return name
    return "necunoscut"


def coerce(kind: str, value: Any) -> Tuple[Any, Optional[str]]:
    """(valoare, stare) cu stare in {None, 'empty', 'bad'} — SQLite acceptă orice tip în orice coloană."""
    if value is None:
        return None, None
    if kind in ("int", "float"):
        if isinstance(value, bool):
            return int(value), None
        if isinstance(value, (int, float)):
            if kind == "int" and isinstance(value, float):
                return (int(value), None) if value == int(value) else (None, "bad")
            return value, None
        if isinstance(value, bytes):
            return None, "bad"
        s = str(value).strip()
        if s == "":
            return None, "empty"
        try:
            return (int(s) if kind == "int" else float(s)), None
        except ValueError:
            return None, "bad"
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace"), None
    if not isinstance(value, str):
        return str(value), None
    return value, None


def analyze(src: Source, meta: Optional[Dict[str, Any]], fix: bool, media_base: Path, cemetery: str) -> Plan:
    plan = Plan()
    P, rep = plan.problems, plan.report
    src_tables = src.tables()
    raw = {t: src.rows(t) for t in src_tables}
    cols = {t: src.columns(t) for t in src_tables}
    settings = {r["key"]: r["value"] for r in raw.get("settings", [])}
    rep["cemetery_id_present"] = bool(settings.get("cemetery_id"))

    # ---- inventar + mapare coloane
    inv = {}
    for t in src_tables:
        known = t in TABLE_ORDER and (meta is None or t in meta)
        names = [c["name"] for c in cols[t]]
        entry = {"rows": len(raw[t]), "known": known, "unmapped": [], "target_only": []}
        if meta is not None and t in meta:
            entry["unmapped"] = [n for n in names if n not in meta[t]["cols"]]
            entry["target_only"] = [n for n in meta[t]["cols"] if n not in names]
        inv[t] = entry
        if not known:
            P.add("BLOCK", "unknown_table", t, f"Tabela '{t}' din SQLite nu are corespondent în schema PostgreSQL ({len(raw[t])} rânduri nemigrate).",
                  count=len(raw[t]))
        if entry["unmapped"]:
            P.add("BLOCK", "unmapped_columns", t, f"Coloane SQLite fără corespondent în PostgreSQL: {', '.join(entry['unmapped'])} (date pierdute).",
                  count=len(entry["unmapped"]))
    rep["inventory"] = inv
    rep["tables_missing_in_source"] = [t for t in TABLE_ORDER if t not in src_tables]

    def kind_of(t: str, c: str, decl: str) -> str:
        if meta is not None and t in meta and c in meta[t]["cols"]:
            return meta[t]["cols"][c]["kind"]
        if (t, c) in JSON_COLUMNS:
            return "json"
        return "int" if "INT" in decl else ("float" if any(k in decl for k in ("REAL", "FLOA", "DOUB", "NUMERIC")) else "text")

    def pk_cols(t: str) -> List[str]:
        if meta and t in meta and meta[t]["pk"]:
            return meta[t]["pk"]
        return ["id"] if any(c["name"] == "id" for c in cols.get(t, [])) else []

    geometry_rep: Dict[str, Dict[str, Any]] = {}
    value_rep: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    numeric_bad: Dict[str, int] = defaultdict(int)
    nul_chars: Dict[str, int] = defaultdict(int)
    uid_rep: Dict[str, Dict[str, int]] = {}
    converted: Dict[str, List[Dict[str, Any]]] = {}

    for t in [x for x in TABLE_ORDER if x in src_tables]:
        decl = {c["name"]: c["type"] for c in cols[t]}
        tmeta = meta.get(t) if meta else None
        has_uid = t in UID_TABLES
        out_rows: List[Dict[str, Any]] = []
        uid_stat = {"existing": 0, "derived": 0, "non_uuid": 0}
        geo_stat: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"total": 0, "empty": 0, "valid": 0, "invalid": 0})
        for r in raw[t]:
            pk = ",".join(str(r.get(k)) for k in pk_cols(t)) or str(r.get("id", ""))
            row: Dict[str, Any] = {}
            skip_reason = None
            for c in decl:
                if (tmeta is not None and c not in tmeta["cols"]) or (c == "uid" and has_uid):
                    continue
                value, kind = r[c], kind_of(t, c, decl[c])
                if kind == "json":
                    minp = GEOMETRY_MIN_POINTS.get((t, c))
                    if minp:
                        state, norm = check_geometry(value, minp)
                        geo_stat[c]["total"] += 1
                        geo_stat[c][state] += 1
                        if state == "invalid":
                            if fix:
                                plan.fixes.append({"table": t, "id": pk, "column": c, "action": "poligon invalid -> '[]'"})
                                norm = "[]"
                            else:
                                P.add("BLOCK", "invalid_polygon", t, f"Poligon invalid în {t}.{c} (JSON/coordonate/număr de puncte).", [pk])
                        row[c] = norm
                    elif empty(value):
                        row[c] = None
                    else:
                        try:
                            json.loads(value)
                            row[c] = value
                        except (ValueError, TypeError):
                            row[c] = None
                            if fix:
                                plan.fixes.append({"table": t, "id": pk, "column": c, "action": "JSON invalid -> NULL"})
                            else:
                                P.add("BLOCK", "invalid_json", t, f"JSON invalid în {t}.{c}.", [pk])
                    continue
                v, state = coerce(kind, value)
                if state == "empty":
                    value_rep[t][f"{c}: '' -> NULL"] += 1
                elif state == "bad":
                    numeric_bad[f"{t}.{c}"] += 1
                    if fix:
                        plan.fixes.append({"table": t, "id": pk, "column": c, "action": "valoare non-numerică -> NULL"})
                    else:
                        P.add("BLOCK", "bad_numeric", t, f"Valoare non-numerică în coloana numerică {t}.{c}.", [pk])
                if isinstance(v, str) and "\x00" in v:
                    nul_chars[f"{t}.{c}"] += 1
                    if fix:
                        v = v.replace("\x00", "")
                        plan.fixes.append({"table": t, "id": pk, "column": c, "action": "caractere NUL eliminate"})
                    else:
                        P.add("BLOCK", "nul_char", t, f"Caracter NUL în {t}.{c} (PostgreSQL îl respinge).", [pk])
                row[c] = v
            if has_uid:
                existing = r.get("uid")
                if empty(existing):
                    row["uid"] = derive_uid(cemetery, t, pk)
                    uid_stat["derived"] += 1
                else:
                    row["uid"] = str(existing).strip()
                    uid_stat["existing"] += 1
                    if len(row["uid"]) != 36:
                        uid_stat["non_uuid"] += 1
            if tmeta is not None:       # coloane NOT NULL fără valoare
                for c, m in tmeta["cols"].items():
                    if c not in ("created_ts", "modified_at") and row.get(c) is None and not m["nullable"]:
                        if m["default"]:
                            row.pop(c, None)           # PostgreSQL folosește DEFAULT-ul
                        elif c in decl:
                            skip_reason = f"{c} NOT NULL lipsește"
            if skip_reason:
                if fix:
                    plan.skipped[t].append(pk)
                    plan.fixes.append({"table": t, "id": pk, "column": "-", "action": f"rând sărit: {skip_reason}"})
                    continue
                P.add("BLOCK", "not_null_missing", t, f"{t}: {skip_reason}.", [pk])
            out_rows.append(row)
        converted[t] = out_rows
        if geo_stat:
            geometry_rep[t] = {c: dict(g) for c, g in geo_stat.items()}
        if has_uid:
            uid_rep[t] = uid_stat

    rep.update({"geometry": geometry_rep, "uids": uid_rep, "numeric_bad": dict(numeric_bad), "nul_chars": dict(nul_chars),
                "value_conversions": {t: dict(v) for t, v in value_rep.items()}})
    for t, g in geometry_rep.items():
        for c, s in g.items():
            if s["empty"] and t == "locuri_de_veci":
                P.add("WARN", "no_polygon", t, f"Locuri fără poligon pe hartă ({t}.{c} gol).", count=s["empty"])

    # ---- unicități + duplicate
    dup_rep = []
    for t, rules in UNIQUE_RULES.items():
        for col in rules:
            seen: Dict[Any, List[Any]] = defaultdict(list)
            for r in converted.get(t, []):
                if not empty(r.get(col)):
                    seen[r[col]].append(r.get("id", r.get(col)))
            dups = {k: v for k, v in seen.items() if len(v) > 1}
            if not dups:
                continue
            ids = [i for v in dups.values() for i in v]
            dup_rep.append({"table": t, "column": col, "groups": len(dups), "rows": len(ids)})
            if col == "uid" and fix:
                later = {pk for v in dups.values() for pk in v[1:]}
                for r in converted[t]:
                    if r.get("id") in later:
                        r["uid"] = derive_uid(cemetery, t, f"{r['id']}:dup")
                        plan.fixes.append({"table": t, "id": r["id"], "column": "uid", "action": "UID duplicat -> regenerat"})
            else:
                P.add("BLOCK", "duplicate", t, f"Valori duplicate în {t}.{col} ({len(dups)} grupuri) — constrângere UNIQUE în PostgreSQL.", ids)
    for rules, code, label, lower in ((CI_WARN, "duplicate_ci", "{col} (diferă doar prin majuscule)", True),
                                      (NATURAL_WARN, "duplicate_natural", "{col} (neunică în aplicație)", False)):
        for t, col in rules.items():
            groups_: Dict[str, List[Tuple[Any, Any]]] = defaultdict(list)
            for r in converted.get(t, []):
                if not empty(r.get(col)):
                    groups_[str(r[col]).lower() if lower else str(r[col])].append((r.get("id", r.get(col)), r[col]))
            # CI: doar grupurile cu valori DIFERITE (duplicatele exacte sunt deja blocante mai sus)
            bad = [v for v in groups_.values() if len(v) > 1 and (not lower or len({x[1] for x in v}) > 1)]
            if bad:
                ids = [i for v in bad for i, _ in v]
                dup_rep.append({"table": t, "column": label.format(col=col), "groups": len(bad), "rows": len(ids)})
                P.add("WARN", code, t, f"Valori repetate în {t}.{col} ({label.format(col=col)}).", ids)
    rep["duplicates"] = dup_rep

    # ---- ID-uri
    id_rep = {}
    for t in [x for x in TABLE_ORDER if x in src_tables]:
        if meta and t in meta and meta[t]["cols"].get("id", {}).get("kind") == "int" and "id" in decl_names(cols[t]):
            ids = [r["id"] for r in raw[t] if isinstance(r.get("id"), int)]
            nonint = [r.get("id") for r in raw[t] if not isinstance(r.get("id"), int)]
            id_rep[t] = {"count": len(ids), "min": min(ids) if ids else None, "max": max(ids) if ids else None,
                         "gaps": (max(ids) - len(ids)) if ids else 0, "non_integer": len(nonint)}
            if nonint:
                P.add("BLOCK", "non_integer_id", t, f"ID neîntreg în {t}.", nonint)
    rep["ids"] = id_rep

    # ---- chei străine + orfani (față de rândurile care CHIAR se vor încărca)
    fk_rep = []
    for child, col, parent, pcol, nullable in LOGICAL_FKS:
        if child not in converted or col not in decl_names(cols[child]):
            continue
        valid = {r.get(pcol) for r in converted.get(parent, []) if r.get(pcol) is not None}
        total, n_null, orphans = len(converted[child]), 0, []
        for r in converted[child]:
            v = r.get(col)
            if v is None or v == "":
                n_null += 1
            elif v not in valid:
                orphans.append(r.get("id", v))
        fk_rep.append({"fk": f"{child}.{col} -> {parent}.{pcol}", "rows": total, "null": n_null, "orphans": len(orphans)})
        if not orphans:
            continue
        dead = set(orphans)
        if nullable and fix:
            for r in converted[child]:
                if r.get("id", r.get(col)) in dead:
                    plan.fixes.append({"table": child, "id": r.get("id"), "column": col, "action": f"referință orfană către {parent} -> NULL"})
                    r[col] = None
        elif fix:
            converted[child] = [r for r in converted[child] if r.get("id", r.get(col)) not in dead]
            plan.skipped[child] += orphans
            plan.fixes += [{"table": child, "id": i, "column": col, "action": f"rând sărit: părinte inexistent în {parent}"} for i in orphans]
        else:
            P.add("BLOCK", "orphan_fk", child, f"Referințe orfane {child}.{col} -> {parent}.{pcol}.", orphans)
    rep["foreign_keys"] = fk_rep
    try:
        rep["sqlite_fk_check"] = len(src.conn.execute("PRAGMA foreign_key_check").fetchall())
        rep["sqlite_integrity"] = src.conn.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.Error as exc:
        rep["sqlite_fk_check"], rep["sqlite_integrity"] = None, f"eroare: {type(exc).__name__}"
    if rep["sqlite_integrity"] != "ok":
        P.add("WARN", "sqlite_integrity", "-", f"PRAGMA integrity_check: {rep['sqlite_integrity']}")

    # ---- relații lipsă (semantic)
    contracte, inh, docs = converted.get("contracte", []), converted.get("inhumati", []), converted.get("documents", [])
    no_conc = [r.get("id") for r in contracte if r.get("concesionar_id") is None]
    no_loc = [r.get("id") for r in contracte if r.get("id_loc") is None]
    inh_no_loc = [r.get("id") for r in inh if r.get("id_loc") is None]
    doc_no_entity, doc_unknown = [], []
    for d in docs:
        mapping = DOC_ENTITY.get(str(d.get("entity_type")))
        if not mapping:
            doc_unknown.append(d.get("id"))
            continue
        t, c = mapping
        key: Any = d.get("entity_id")
        if c == "id":
            try:
                key = int(key)
            except (TypeError, ValueError):
                doc_no_entity.append(d.get("id"))
                continue
        if key not in {r.get(c) for r in converted.get(t, [])}:
            doc_no_entity.append(d.get("id"))
    rep["relations"] = {"contracts_total": len(contracte), "contracts_without_concesionar": len(no_conc), "contracts_without_loc": len(no_loc),
                        "burials_total": len(inh), "burials_without_valid_loc": len(inh_no_loc), "documents_total": len(docs),
                        "documents_without_entity": len(doc_no_entity), "documents_unknown_entity_type": len(doc_unknown)}
    for ids, code, table, msg in ((no_conc, "contract_no_concesionar", "contracte", "Contracte fără concesionar (concesionar_id gol sau orfan)."),
                                  (no_loc, "contract_no_loc", "contracte", "Contracte fără loc de veci valid (id_loc gol sau orfan)."),
                                  (inh_no_loc, "burial_no_loc", "inhumati", "Înhumări fără loc valid (id_loc gol sau orfan)."),
                                  (doc_no_entity, "document_no_entity", "documents", "Documente a căror entitate (entity_type/entity_id) nu există."),
                                  (doc_unknown, "document_unknown_type", "documents", "Documente cu entity_type necunoscut (entitatea nu poate fi verificată).")):
        if ids:
            P.add("WARN", code, table, msg, ids)

    # ---- utilizatori + parole
    users, cits = raw.get("users", []), raw.get("citizens", [])
    schemes = count_by(hash_scheme(u.get("password_hash")) for u in users)
    rep["users"] = {"count": len(users), "roles": count_by(u.get("role") for u in users),
                    "inactive": sum(1 for u in users if u.get("active") == 0), "hash_schemes": schemes,
                    "citizens": len(cits), "citizen_hash_schemes": count_by(hash_scheme(u.get("password_hash")) for u in cits),
                    "password_checksum": digest(sorted((u["id"], u["password_hash"]) for u in users)),
                    "citizen_password_checksum": digest(sorted((u["id"], u["password_hash"]) for u in cits))}
    if schemes.get("gol"):
        P.add("BLOCK", "empty_password_hash", "users", "Utilizatori cu hash de parolă gol.", [u["id"] for u in users if empty(u.get("password_hash"))])
    if schemes.get("necunoscut"):
        P.add("WARN", "unknown_hash_scheme", "users", "Hash-uri de parolă cu schemă nerecunoscută (se copiază neschimbate).",
              [u["id"] for u in users if hash_scheme(u.get("password_hash")) == "necunoscut"])

    # ---- audit
    audit = raw.get("audit_log", [])
    ts = [a["ts"] for a in audit if not empty(a.get("ts"))]
    rep["audit"] = {"count": len(audit), "min_ts": min(ts) if ts else None, "max_ts": max(ts) if ts else None, "null_ts": len(audit) - len(ts),
                    "checksum": digest([[a.get(c) for c in ("id", "ts", "actor_type", "actor", "action", "target", "detail")]
                                        for a in sorted(audit, key=lambda x: x["id"])])}
    if rep["audit"]["null_ts"]:
        P.add("WARN", "audit_null_ts", "audit_log", "Intrări de audit fără timestamp.", count=rep["audit"]["null_ts"])

    # ---- media (doar citire)
    media = {}
    for t in ("documents", "photos"):
        missing = []
        for r in raw.get(t, []):
            p = r.get("file_path")
            if empty(p) or not (Path(p) if Path(p).is_absolute() else media_base / p).exists():
                missing.append(r["id"])
        media[t] = {"total": len(raw.get(t, [])), "missing_on_disk": len(missing)}
        if missing:
            P.add("WARN", "media_missing", t, f"Fișiere referite de {t}.file_path care nu există pe disc aici (calea se copiază neschimbată).", missing)
    rep["media"] = media
    if "web_jwt_secret" in settings:
        P.add("INFO", "secret_in_settings", "settings", "settings conține 'web_jwt_secret' — se migrează neschimbat (valoarea NU e în raport); "
              "restricționează accesul la baza PostgreSQL.")
    if src.decode_fallbacks:
        P.add("WARN", "encoding", "-", "Text non-UTF-8 în SQLite, decodat cu înlocuire.", count=src.decode_fallbacks)
    plan.rows = converted
    return plan


def decl_names(columns: List[Dict[str, Any]]) -> set:
    return {c["name"] for c in columns}


# ------------------------------------------------------------------------------------------------ încărcare + verificare
def canon(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True, ensure_ascii=False)
    if isinstance(v, str) and v[:1] in "[{":
        try:
            return json.dumps(json.loads(v), sort_keys=True, ensure_ascii=False)
        except ValueError:
            return v
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        return float(v)
    return v


def table_checksum(rows: List[Dict[str, Any]], cols: List[str], order: List[str]) -> str:
    items = [[canon(r.get(c)) for c in cols] for r in rows]
    idx = [cols.index(o) for o in order if o in cols] or [0]
    return digest(sorted(items, key=lambda x: json.dumps([x[i] for i in idx], default=str)))


def tenant_where(meta: Dict[str, Any], t: str, org: str, cem: str) -> Tuple[str, Tuple]:
    sc = meta[t].get("scope", "global")
    if sc == "cem":
        return " WHERE cemetery_uid = %s", (cem,)
    if sc == "org":
        return " WHERE organization_uid = %s", (org,)
    return "", ()


def tenant_counts(conn, meta: Dict[str, Any], org: str, cem: str) -> Dict[str, int]:
    """Rânduri per tabel în tenantul țintă (tabelele fără tenant: totalul)."""
    out = {}
    for t in TABLE_ORDER:
        if t in meta:
            where, params = tenant_where(meta, t, org, cem)
            out[t] = conn.execute(f'SELECT count(*) FROM "{t}"{where}', params).fetchone()[0]
    return out


def other_tenant_rows(conn, meta: Dict[str, Any], cem: str) -> int:
    """Rânduri în alte cimitire decât cel țintă (cimitir-scoped)."""
    n = 0
    for t in TABLE_ORDER:
        if t in meta and meta[t].get("scope") == "cem":
            n += conn.execute(f'SELECT count(*) FROM "{t}" WHERE cemetery_uid IS DISTINCT FROM %s', (cem,)).fetchone()[0]
    return n


def check_tenant(url: str, meta: Dict[str, Any], org: str, cem: str) -> Tuple[Optional[str], Dict[str, Any]]:
    """(eroare | None, info). Tenantul trebuie să existe, iar cimitirul să aparțină organizației."""
    import psycopg
    info: Dict[str, Any] = {"organization_uid": org, "cemetery_uid": cem}
    if "cemeteries" not in meta or "organizations" not in meta:
        return "schema multi-tenant (migrările 008–013) nu este aplicată în baza țintă", info
    with psycopg.connect(url, connect_timeout=10, autocommit=True, application_name="cimitir-migrate") as c:
        c.read_only = True
        if not c.execute("SELECT 1 FROM organizations WHERE uid = %s", (org,)).fetchone():
            return f"organizația {org} nu există în baza țintă", info
        row = c.execute("SELECT organization_uid::text, name FROM cemeteries WHERE uid = %s", (cem,)).fetchone()
        if not row:
            return f"cimitirul {cem} nu există în baza țintă", info
        if row[0] != org:
            return f"cimitirul {cem} aparține altei organizații decât {org}", info
        info["cemetery_name"] = row[1]
        s = c.execute("SELECT value FROM settings WHERE key = 'cemetery_id'").fetchone() if "settings" in meta else None
        info["settings_cemetery_id"] = s[0] if s else None
        info["before"] = tenant_counts(c, meta, org, cem)
        info["other_tenant_rows"] = other_tenant_rows(c, meta, cem)
    return None, info


def execute(url: str, plan: Plan, meta: Dict[str, Any], tenant: Tuple[str, str], truncate: bool, confirm_db: Optional[str], assume_yes: bool) -> Dict[str, Any]:
    import psycopg
    org, cem = tenant
    result: Dict[str, Any] = {"tables": {}, "verified": False, "committed": False}
    db_name = meta["_db"]
    if not assume_yes:
        if not sys.stdin.isatty():
            raise SystemExit("--apply fără --yes necesită terminal interactiv pentru confirmare.")
        if input(f"Se va SCRIE în baza PostgreSQL '{db_name}', cimitirul {cem}. Scrie numele bazei pentru confirmare: ").strip() != db_name:
            raise SystemExit("Confirmare greșită. Nu s-a scris nimic.")
    elif confirm_db != db_name:
        raise SystemExit(f"--confirm-db={confirm_db} diferă de baza țintă. Nu s-a scris nimic.")

    err, tinfo = check_tenant(url, meta, org, cem)
    if err:
        raise SystemExit(f"Tenant invalid: {err}. Nu s-a scris nimic.")
    data_tables = [t for t in TABLE_ORDER if t in meta]
    conn = psycopg.connect(url, connect_timeout=10, autocommit=True, application_name="cimitir-migrate")
    q = lambda name: '"' + name + '"'  # noqa: E731
    try:
        before = tenant_counts(conn, meta, org, cem)
        # `users` nu are coloană de tenant: aparțin cimitirului țintă doar utilizatorii legați prin identities -> membri -> permisiuni
        owned_users = [r[0] for r in conn.execute(
            "SELECT DISTINCT i.legacy_user_id FROM identities i JOIN organization_members m ON m.identity_uid = i.uid "
            "JOIN cemetery_permissions p ON p.member_uid = m.member_uid WHERE p.cemetery_uid = %s AND i.legacy_user_id IS NOT NULL", (cem,))]
        before["users"] = len(owned_users)
        foreign_users = conn.execute("SELECT count(*) FROM users").fetchone()[0] - len(owned_users)
        result["before"] = before
        glob_busy = [t for t in data_tables if t not in ("settings", "users") and meta[t]["scope"] == "global" and before[t] and plan.rows.get(t)]
        if foreign_users and plan.rows.get("users"):
            glob_busy.append("users")
        if glob_busy:
            raise SystemExit("Tabele fără tenant deja pline în țintă (" + ", ".join(f"{t}={foreign_users if t == 'users' else before[t]}" for t in glob_busy)
                             + "): importul nu le suprascrie. Nu s-a scris nimic.")
        tenant_busy = {t: n for t, n in before.items() if n and (meta[t]["scope"] != "global" or t == "users")}
        if tenant_busy and not truncate:
            raise SystemExit("Cimitirul țintă are deja date (" + ", ".join(f"{t}={n}" for t, n in tenant_busy.items())
                             + "). Folosește --truncate-target (șterge DOAR acest cimitir) sau alt cimitir gol. Nu s-a scris nimic.")
        cur_cem = conn.execute("SELECT value FROM settings WHERE key = 'cemetery_id'").fetchone()
        if cur_cem and cur_cem[0] != cem and other_tenant_rows(conn, meta, cem):
            raise SystemExit("settings.cemetery_id indică alt cimitir, care are date: importul ar muta tenantul activ. Nu s-a scris nimic.")
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.org_uid', %s, true), set_config('app.cemetery_uids', %s, true)", (org, cem))
            if tenant_busy:
                if owned_users:
                    cur.execute("DELETE FROM cemetery_permissions WHERE cemetery_uid = %s AND member_uid IN (SELECT m.member_uid FROM organization_members m "
                                "JOIN identities i ON i.uid = m.identity_uid WHERE i.legacy_user_id = ANY(%s))", (cem, owned_users))
                    cur.execute("DELETE FROM organization_members m USING identities i WHERE i.uid = m.identity_uid AND i.legacy_user_id = ANY(%s) "
                                "AND NOT EXISTS (SELECT 1 FROM cemetery_permissions p WHERE p.member_uid = m.member_uid)", (owned_users,))
                    cur.execute("DELETE FROM identities i WHERE i.legacy_user_id = ANY(%s) "
                                "AND NOT EXISTS (SELECT 1 FROM organization_members m WHERE m.identity_uid = i.uid)", (owned_users,))
                    cur.execute("DELETE FROM users WHERE id = ANY(%s)", (owned_users,))
                for t in reversed(data_tables):
                    if meta[t]["scope"] == "global" or not before[t]:
                        continue
                    where, params = tenant_where(meta, t, org, cem)
                    cur.execute(f"ALTER TABLE {q(t)} DISABLE TRIGGER USER")    # fără tombstone-uri de sincronizare pentru ștergerea de curățare
                    cur.execute(f"DELETE FROM {q(t)}{where}", params)
                    cur.execute(f"ALTER TABLE {q(t)} ENABLE TRIGGER USER")
            for t in data_tables:
                sc = meta[t]["scope"]
                rows = [dict(r) for r in plan.rows.get(t, [])]
                if t == "settings":
                    rows = [r for r in rows if r.get("key") != "cemetery_id"]
                else:
                    for r in rows:
                        if sc in ("cem", "org") and "organization_uid" in meta[t]["cols"]:
                            r["organization_uid"] = org
                        if sc == "cem":
                            r["cemetery_uid"] = cem
                groups: Dict[Tuple[str, ...], List[Tuple]] = defaultdict(list)
                for r in rows:
                    cols = tuple(c for c in r if c in meta[t]["cols"])
                    groups[cols].append(tuple(r[c] for c in cols))
                conflict = " ON CONFLICT (key) DO NOTHING" if t == "settings" else ""
                for cols, vals in groups.items():
                    cur.executemany(f'INSERT INTO {q(t)} ({", ".join(q(c) for c in cols)}) VALUES ({", ".join(["%s"] * len(cols))}){conflict}', vals)
                result["tables"][t] = {"loaded": len(rows), "loaded_rows": rows}
            cur.execute("INSERT INTO settings (key, value) VALUES ('cemetery_id', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (cem,))
            if plan.rows.get("users"):
                for u in cur.execute("SELECT id, username, password_hash, role, rights, COALESCE(active, 1) AS active FROM users").fetchall():
                    uid_, uname, phash, role, rights, active = u
                    ident = cur.execute(
                        "INSERT INTO identities (display_name, password_hash, active, legacy_user_id) VALUES (%s,%s,%s,%s) "
                        "ON CONFLICT (legacy_user_id) DO UPDATE SET active = EXCLUDED.active RETURNING uid", (uname, phash, active == 1, uid_)).fetchone()[0]
                    memb = cur.execute(
                        "INSERT INTO organization_members (organization_uid, identity_uid, username, org_role, active) VALUES (%s,%s,%s,%s,%s) "
                        "ON CONFLICT (organization_uid, identity_uid) DO UPDATE SET active = EXCLUDED.active RETURNING member_uid",
                        (org, ident, uname, "ORG_ADMIN" if role == "admin" else "STAFF", active == 1)).fetchone()[0]
                    cur.execute(
                        "INSERT INTO cemetery_permissions (member_uid, organization_uid, cemetery_uid, role, rights, active) "
                        "VALUES (%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT (member_uid, cemetery_uid) DO NOTHING",
                        (memb, org, cem, role, json.dumps({"legacy_rights": rights}), active == 1))
            cur.execute("SELECT cimitir_reset_sequences()")
            failed, seq_bad = [], []
            for t in data_tables:
                entry = result["tables"][t]
                rows = entry.pop("loaded_rows")
                where, params = tenant_where(meta, t, org, cem)
                entry["before"] = before[t]
                if t == "settings":
                    keys = {r[0] for r in cur.execute("SELECT key FROM settings").fetchall()}
                    entry["postgres_rows"] = len(keys)
                    entry["ok"] = {r["key"] for r in plan.rows.get("settings", [])} <= keys | {"cemetery_id"} and \
                        cur.execute("SELECT value FROM settings WHERE key = 'cemetery_id'").fetchone()[0] == cem
                    continue
                entry["postgres_rows"] = cur.execute(f"SELECT count(*) FROM {q(t)}{where}", params).fetchone()[0]
                entry["ok"] = entry["postgres_rows"] == len(rows)
                comparable = sorted({c for r in rows for c in r if c in meta[t]["cols"] and all(c in x for x in rows)})
                if rows and comparable:
                    cur.execute(f'SELECT {", ".join(q(c) for c in comparable)} FROM {q(t)}{where}', params)
                    back = [dict(zip(comparable, rec)) for rec in cur.fetchall()]
                    order = meta[t]["pk"] or comparable
                    entry["checksum_ok"] = table_checksum(rows, comparable, order) == table_checksum(back, comparable, order)
                    entry["ok"] = entry["ok"] and entry["checksum_ok"]
                if not entry["ok"]:
                    failed.append(t)
                if rows and meta[t]["cols"].get("id", {}).get("kind") == "int" and meta[t]["cols"]["id"]["default"]:
                    mx = max(r["id"] for r in rows if "id" in r)
                    seq = cur.execute(f"SELECT pg_get_serial_sequence('{t}', 'id')").fetchone()[0]
                    last, called = cur.execute(f"SELECT last_value, is_called FROM {seq}").fetchone()
                    if not (called and last >= mx):
                        seq_bad.append(t)
            leak = other_tenant_rows(cur, meta, cem) - tinfo["other_tenant_rows"]
            result["other_tenants_untouched"] = leak == 0
            result["sequences_ok"] = not seq_bad
            if failed or seq_bad or leak:
                conn.rollback()
                result["failed_tables"], result["failed_sequences"] = failed, seq_bad
                return result
        conn.commit()
        result["committed"] = True
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    with psycopg.connect(url, connect_timeout=10, autocommit=True) as c2:         # re-verificare după COMMIT
        after = tenant_counts(c2, meta, org, cem)
        for t, entry in result["tables"].items():
            entry["postgres_rows_after_commit"] = after[t]
        c2.execute("SELECT set_config('app.org_uid', %s, false), set_config('app.cemetery_uids', %s, false)", (org, cem))
        for key, table, expected in (("password_checksum_ok", "users", "password_checksum"),
                                     ("citizen_password_checksum_ok", "citizens", "citizen_password_checksum")):
            where, params = tenant_where(meta, table, org, cem)
            result[key] = digest(sorted((i, h) for i, h in c2.execute(f"SELECT id, password_hash FROM {table}{where}", params))) == plan.report["users"][expected]
        where, params = tenant_where(meta, "audit_log", org, cem)
        audit_pg = c2.execute(f"SELECT id, ts, actor_type, actor, action, target, detail FROM audit_log{where} ORDER BY id", params).fetchall()
        result["audit_checksum_ok"] = digest([list(a) for a in audit_pg]) == plan.report["audit"]["checksum"]
        result["fk_not_validated"] = c2.execute("SELECT count(*) FROM pg_constraint WHERE contype='f' AND NOT convalidated").fetchone()[0]
        result["settings_cemetery_id_aligned"] = c2.execute("SELECT value FROM settings WHERE key='cemetery_id'").fetchone()[0] == cem
    result["verified"] = (all(e.get("ok") and (e["loaded"] == e.get("postgres_rows_after_commit") or t == "settings") for t, e in result["tables"].items())
                          and result["password_checksum_ok"] and result["citizen_password_checksum_ok"]
                          and result["audit_checksum_ok"] and result["sequences_ok"]
                          and result["other_tenants_untouched"] and result["settings_cemetery_id_aligned"])
    return result


# ------------------------------------------------------------------------------------------------ raport
def ids_text(ids: List[Any], count: int) -> str:
    if not ids:
        return ""
    return " ID: " + ", ".join(str(i) for i in ids) + (f" … (+{count - len(ids)})" if count > len(ids) else "")


def render_report(args, plan: Plan, info: Dict[str, Any], target_db: Optional[str], note: str, result: Optional[Dict[str, Any]], tinfo: Dict[str, Any],
                  elapsed: float, verdict: str, unchanged: bool) -> str:
    rep, P = plan.report, plan.problems
    L: List[str] = []
    w = L.append
    w("# MIGRATION_REPORT — SQLite → PostgreSQL\n")
    w(f"- **Mod:** {'APPLY' if args.apply else 'DRY-RUN (nu s-a scris nimic în PostgreSQL)'}")
    w(f"- **Tenant țintă:** organizație `{tinfo['organization_uid']}` · cimitir `{tinfo['cemetery_uid']}`"
      + (f" ({tinfo['cemetery_name']})" if tinfo.get('cemetery_name') else ' (neverificat)')
      + f" · settings.cemetery_id: {'aliniat' if tinfo.get('settings_cemetery_id') == tinfo['cemetery_uid'] else 'se aliniază la --apply'}")
    w(f"- **Generat:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (durată {elapsed:.1f} s)")
    w(f"- **Verdict:** **{verdict}**")
    w(f"- **Politică probleme:** `--on-problem {args.on_problem}`")
    w(f"- **Bază țintă PostgreSQL:** {target_db or 'neverificată'}{(' — ' + note) if note else ''}")
    w(f"- **Probleme:** {len(P.blocking())} blocante, {sum(1 for p in P.items if p['level'] == 'WARN')} avertismente, "
      f"{sum(1 for p in P.items if p['level'] == 'INFO')} informări\n")

    w("## 1. Sursa SQLite (read-only)\n")
    w(f"- Fișier: `{info['path']}`  \n- Dimensiune: {info['size']} octeți  \n- SHA-256: `{info['sha256']}`")
    w(f"- `PRAGMA integrity_check`: {rep.get('sqlite_integrity')} · `PRAGMA foreign_key_check`: {rep.get('sqlite_fk_check')} încălcări")
    w(f"- Mod deschidere: {'`immutable` (nu creează și nu atinge fișiere auxiliare -shm/-wal)' if info.get('immutable') else '`mode=ro` cu WAL'}")
    w(f"- Fișier WAL nevidat: {'DA — închide aplicațiile/desktopul înainte de migrarea finală' if info['wal'] else 'nu'}")
    w(f"- **Neschimbat după rulare:** {'DA (dimensiune, mtime și SHA-256 identice)' if unchanged else 'NU — VERIFICĂ IMEDIAT'}")
    w("- Fișiere media: **neatinse** (doar verificare de existență).\n")

    w("## 2. Inventar tabele\n")
    w("Înainte = rânduri existente în cimitirul țintă (tabelele fără tenant: totalul); După = rânduri în cimitirul țintă după import "
      "(la dry-run: estimare = înainte + se încarcă).\n")
    w("| Tabel | Rânduri SQLite | Se încarcă | Înainte | După | Verificare | Coloane SQLite nemapate |")
    w("|---|---:|---:|---:|---:|---|---|")
    before = tinfo.get("before") or {}
    for t, e in rep["inventory"].items():
        r = (result or {}).get("tables", {}).get(t)
        loaded = len(plan.rows.get(t, []))
        b = before.get(t, "—")
        pg_n = (r.get("postgres_rows_after_commit", r.get("postgres_rows")) if r else (b + loaded if isinstance(b, int) else "—"))
        w(f"| {t} | {e['rows']} | {loaded} | {b} | {pg_n}{'' if r else ' (estimat)'} | {('OK' if r.get('ok') else 'EȘUAT') if r else '—'} | "
          f"{', '.join(e['unmapped']) or '—'}{'' if e['known'] else ' (TABEL NECUNOSCUT)'} |")
    if rep["tables_missing_in_source"]:
        w(f"\nTabele din schema PostgreSQL absente în sursă (rămân goale): {', '.join(rep['tables_missing_in_source'])}")

    w("\n## 3. ID-uri (păstrate; secvențele se aliniază după încărcare)\n")
    w("| Tabel | Rânduri | ID min | ID max | Goluri | ID-uri non-întregi |")
    w("|---|---:|---:|---:|---:|---:|")
    for t, i in rep["ids"].items():
        w(f"| {t} | {i['count']} | {i['min']} | {i['max']} | {i['gaps']} | {i['non_integer']} |")
    if not rep["ids"]:
        w("| (verificare disponibilă doar cu PostgreSQL accesibil) | | | | | |")

    w("\n## 4. UID-uri\n")
    w(f"UID lipsă => derivat determinist `uuid5(cemetery_uid:tabel:id)` (cemetery_uid = cimitirul țintă; cemetery_id din sursă: "
      f"{'prezent' if rep['cemetery_id_present'] else 'absent'}, ignorat — settings.cemetery_id devine cimitirul țintă).\n")
    w("| Tabel | UID existente păstrate | UID derivate | Format ne-UUID (păstrate) |")
    w("|---|---:|---:|---:|")
    for t, u in rep["uids"].items():
        w(f"| {t} | {u['existing']} | {u['derived']} | {u['non_uuid']} |")

    w("\n## 5. Chei străine și referințe orfane\n")
    w("| Cheie străină | Rânduri | Fără referință (NULL) | Orfani |")
    w("|---|---:|---:|---:|")
    for f in rep["foreign_keys"]:
        w(f"| {f['fk']} | {f['rows']} | {f['null']} | {f['orphans']} |")

    w("\n## 6. Duplicate\n")
    if rep["duplicates"]:
        w("| Tabel | Coloană | Grupuri | Rânduri |")
        w("|---|---|---:|---:|")
        for d in rep["duplicates"]:
            w(f"| {d['table']} | {d['column']} | {d['groups']} | {d['rows']} |")
    else:
        w("Niciun duplicat găsit.")

    r = rep["relations"]
    w("\n## 7. Relații lipsă\n")
    w(f"- Contracte: {r['contracts_total']} · **fără concesionar: {r['contracts_without_concesionar']}** · fără loc valid: {r['contracts_without_loc']}")
    w(f"- Înhumări: {r['burials_total']} · **fără loc valid: {r['burials_without_valid_loc']}**")
    w(f"- Documente: {r['documents_total']} · **fără entitate: {r['documents_without_entity']}** · tip de entitate necunoscut: {r['documents_unknown_entity_type']}")

    w("\n## 8. Poligoane (JSONB)\n")
    w("| Tabel.coloană | Total | Valide | Goale | Invalide |")
    w("|---|---:|---:|---:|---:|")
    for t, g in rep["geometry"].items():
        for c, s in g.items():
            w(f"| {t}.{c} | {s['total']} | {s['valid']} | {s['empty']} | {s['invalid']} |")

    u = rep["users"]
    w("\n## 9. Utilizatori și parole\n")
    w(f"- Utilizatori personal: **{u['count']}** (roluri: {u['roles']}; dezactivați: {u['inactive']}) · hash-uri: {u['hash_schemes']}")
    w(f"- Conturi cetățeni: **{u['citizens']}** · hash-uri: {u['citizen_hash_schemes']}")
    w("- Hash-urile se copiază **octet cu octet**; după încărcare se compară un checksum SHA-256 al perechilor (id, hash). Valorile NU apar aici.")
    if result and result.get("committed"):
        w(f"- Checksum parole personal: **{'OK' if result.get('password_checksum_ok') else 'EȘUAT'}** · cetățeni: **{'OK' if result.get('citizen_password_checksum_ok') else 'EȘUAT'}**")

    a = rep["audit"]
    w("\n## 10. audit_log\n")
    w(f"- Intrări: **{a['count']}** · primul: {a['min_ts']} · ultimul: {a['max_ts']} · fără timestamp: {a['null_ts']}")
    w("- Se păstrează ID-urile și textul original `ts` (data/ora nemodificate).")
    if result and result.get("committed"):
        w(f"- Checksum audit: **{'OK' if result.get('audit_checksum_ok') else 'EȘUAT'}**")

    w("\n## 11. Conversii de valori\n")
    if rep["value_conversions"] or rep["numeric_bad"] or rep["nul_chars"]:
        for t, d in rep["value_conversions"].items():
            for k, n in d.items():
                w(f"- {t}.{k}: {n}")
        for k, n in rep["numeric_bad"].items():
            w(f"- {k}: {n} valori non-numerice")
        for k, n in rep["nul_chars"].items():
            w(f"- {k}: {n} valori cu caractere NUL")
    else:
        w("Nicio conversie necesară.")
    w("\nData și ora: coloanele de dată/oră existente rămân **text original, neschimbat**. Coloanele noi `created_ts`/`modified_at` primesc momentul încărcării.")

    w("\n## 12. Fișiere media (doar verificare)\n")
    for t, m in rep["media"].items():
        w(f"- {t}: {m['total']} înregistrări · lipsă pe disc aici: {m['missing_on_disk']}")

    w("\n## 13. Corecții aplicate / rânduri sărite (`--on-problem fix`)\n")
    if plan.fixes:
        w("| Tabel | ID | Coloană | Acțiune |")
        w("|---|---|---|---|")
        for f in plan.fixes[:200]:
            w(f"| {f['table']} | {f['id']} | {f['column']} | {f['action']} |")
        if len(plan.fixes) > 200:
            w(f"\n… și încă {len(plan.fixes) - 200} corecții.")
    else:
        w("Nicio corecție aplicată.")

    if result:
        w("\n## 14. Verificare după încărcare\n")
        w(f"- Tranzacție: **{'COMMIT' if result.get('committed') else 'ROLLBACK (nu s-a scris nimic)'}**")
        w(f"- Secvențe ID aliniate (last_value ≥ MAX(id)): {'OK' if result.get('sequences_ok') else 'EȘUAT'}")
        w(f"- Chei străine nevalidate: {result.get('fk_not_validated', '—')}")
        w(f"- Alte cimitiruri neatinse: {'DA' if result.get('other_tenants_untouched') else 'NU'} · settings.cemetery_id aliniat: {'DA' if result.get('settings_cemetery_id_aligned', True) else 'NU'}")
        if result.get("failed_tables"):
            w(f"- Tabele cu diferențe: {', '.join(result['failed_tables'])}")

    w("\n## 15. Constatări\n")
    titles = {"BLOCK": "BLOCANTE", "WARN": "AVERTISMENTE", "INFO": "INFORMĂRI"}
    if P.items:
        for lvl in ("BLOCK", "WARN", "INFO"):
            items = [p for p in P.items if p["level"] == lvl]
            if items:
                w(f"**{titles[lvl]}**\n")
                for p in items:
                    w(f"- `{p['code']}` · {p['table']} · {p['message']} ({p['count']}){ids_text(p['ids'], p['count'])}")
                w("")
    else:
        w("Nicio problemă.\n")

    w("## 16. Garanții și pașii următori\n")
    w("- Baza SQLite **nu a fost ștearsă sau modificată**; fișierele media nu au fost atinse.")
    w("- Reia cu `--dry-run` (implicit) după orice corecție; `--apply` doar după verdict `GATA` (sau după ce accepți `--on-problem fix`).")
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------------------------------------ main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Migrare SQLite -> PostgreSQL (dry-run sau execute) cu MIGRATION_REPORT.md.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="doar analiză + raport; nu scrie în PostgreSQL (modul implicit)")
    mode.add_argument("--apply", action="store_true", help="încarcă datele (tranzacție unică) și verifică")
    ap.add_argument("--source", required=True, help="calea către baza SQLite (se deschide doar read-only)")
    ap.add_argument("--organization-uid", required=True, help="UUID-ul organizației țintă (trebuie să existe)")
    ap.add_argument("--cemetery-uid", required=True, help="UUID-ul cimitirului țintă (trebuie să existe și să aparțină organizației)")
    ap.add_argument("--report", default=str(ROOT / "MIGRATION_REPORT.md"), help="fișierul raport (implicit MIGRATION_REPORT.md)")
    ap.add_argument("--on-problem", choices=("abort", "fix"), default="abort",
                    help="abort (implicit): probleme blocante => nu se scrie nimic; fix: aplică corecțiile documentate în raport")
    ap.add_argument("--truncate-target", action="store_true", help="--apply: șterge rândurile cimitirului țintă înainte de import (DOAR acest cimitir; alte cimitire rămân neatinse)")
    ap.add_argument("--yes", action="store_true", help="--apply: fără confirmare interactivă (cere --confirm-db)")
    ap.add_argument("--confirm-db", help="numele bazei țintă așteptate (cu --yes)")
    ap.add_argument("--media-base", help="folder față de care se rezolvă căile media relative (implicit folderul sursei)")
    args = ap.parse_args(argv)
    if args.apply and args.yes and not args.confirm_db:
        ap.error("--yes necesită --confirm-db NUME_BAZA")
    try:
        org_uid, cem_uid = str(uuid.UUID(args.organization_uid)), str(uuid.UUID(args.cemetery_uid))
    except ValueError:
        ap.error("--organization-uid și --cemetery-uid trebuie să fie UUID-uri valide")
    args.tenant = (org_uid, cem_uid)

    started = time.perf_counter()
    source_path = Path(args.source)
    if not source_path.is_file():
        print(f"Sursa nu există: {source_path}", file=sys.stderr)
        return 1
    before = file_state(source_path)
    wal = Path(str(source_path) + "-wal")
    info = {"path": str(source_path), "size": before["size"], "sha256": before["sha256"], "wal": wal.exists() and wal.stat().st_size > 0}

    cimitir_config.load_env()
    url, meta, target_db, note = "", None, None, ""
    try:
        url = pg_compat.database_url()
        if cimitir_test_guard.has_protected_ref(url):
            print("DATABASE_URL indică proiectul de producție: BLOCAT (nici dry-run, nici --apply).", file=sys.stderr)
            return 1
        if args.apply:
            import postgres_store
            postgres_store.migrate()                     # --apply: schema (idempotentă) trebuie aplicată înainte de validarea tenantului
        meta = target_meta(url)
        target_db = meta["_db"]
        if "settings" not in meta:                       # schemă neaplicată: dry-run nu scrie, deci omite verificările de schemă țintă
            meta, note = None, "schema încă neaplicată (verificările de schemă țintă sunt omise; se aplică la --apply)"
    except pg_compat.PgConfigError:
        if args.apply:
            print("DATABASE_URL nu este setat (necesar pentru --apply).", file=sys.stderr)
            return 1
        note = "DATABASE_URL lipsă — verificările de schemă țintă sunt omise"
    except Exception as exc:
        print(f"PostgreSQL inaccesibil: {type(exc).__name__}: {pg_compat.redact(str(exc).splitlines()[0] if str(exc) else '', url)}", file=sys.stderr)
        return 1
    tinfo: Dict[str, Any] = {"organization_uid": org_uid, "cemetery_uid": cem_uid}
    tenant_err: Optional[str] = None
    if meta is not None:
        tenant_err, tinfo = check_tenant(url, meta, org_uid, cem_uid)
        if tenant_err and args.apply:
            print(f"Tenant invalid: {tenant_err}. Nu s-a scris nimic.", file=sys.stderr)
            return 1

    try:
        src = Source(source_path)
    except sqlite3.Error as exc:
        print(f"Nu pot deschide sursa read-only: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    info["immutable"] = src.immutable
    try:
        plan = analyze(src, meta, args.on_problem == "fix", Path(args.media_base) if args.media_base else source_path.parent, cem_uid)
    finally:
        src.close()

    for t in NOT_IMPORTED_GLOBAL:
        if plan.rows.get(t):
            plan.problems.add("INFO", "not_imported_global", t, f"{t}: stare de sincronizare MAIN, fără tenant — nu se importă ({len(plan.rows[t])} rânduri ignorate).", count=len(plan.rows[t]))
            plan.rows[t] = []
    if tenant_err:
        plan.problems.add("BLOCK", "tenant_invalid", "-", f"Tenant țintă invalid: {tenant_err}.", count=1)
    elif meta is not None and tinfo.get("other_tenant_rows") is not None:
        plan.problems.add("INFO", "tenant_scope", "-", f"Import limitat la cimitirul {cem_uid}; alte cimitire din bază: {tinfo['other_tenant_rows']} rânduri (neatinse).", count=1)
    result = None
    blocked = bool(plan.problems.blocking())
    verdict = "BLOCAT — probleme blocante" if blocked else ("GATA de migrare" if not plan.problems.items else "GATA (cu avertismente)")
    code = 3 if blocked else 0
    if args.apply and not blocked:
        try:
            result = execute(url, plan, meta, args.tenant, args.truncate_target, args.confirm_db, args.yes)
        except SystemExit as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if not result.get("committed"):
            verdict, code = "EȘUAT la verificare — ROLLBACK, nu s-a scris nimic", 4
        elif not result.get("verified"):
            verdict, code = "COMMIT efectuat dar verificarea finală a găsit diferențe — INVESTIGHEAZĂ", 4
        else:
            verdict = "MIGRARE REUȘITĂ și verificată"
    elif args.apply:
        verdict = "BLOCAT — nu s-a scris nimic în PostgreSQL"

    unchanged = file_state(source_path) == before
    if not unchanged:
        code = 4
    Path(args.report).write_text(render_report(args, plan, info, target_db, note, result, tinfo, time.perf_counter() - started, verdict, unchanged),
                                 encoding="utf-8")
    print(f"{verdict}. Raport: {args.report}")
    for p in plan.problems.blocking():
        print(f"  BLOCK {p['code']} {p['table']}: {p['message']} ({p['count']})")
    return code


if __name__ == "__main__":
    sys.exit(main())
