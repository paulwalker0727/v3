"""Istoric scanări AI — tabela `ai_scan_history` în baza SQLite existentă."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

import database as db


def init_scan_history() -> None:
    """Creează tabela ai_scan_history dacă nu există."""
    conn = db.get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_scan_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                document_type TEXT NOT NULL,
                filename TEXT,
                file_path TEXT,
                status TEXT NOT NULL DEFAULT 'processed',
                entity_type TEXT,
                entity_id INTEGER,
                extracted_json TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_scan_history_created "
            "ON ai_scan_history(created_at DESC)"
        )
    finally:
        conn.close()


def save_scan(
    document_type: str,
    filename: str,
    file_path: Optional[str],
    extracted: dict[str, Any],
    status: str = "processed",
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
) -> Optional[int]:
    init_scan_history()
    conn = db.get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO ai_scan_history
               (created_at, document_type, filename, file_path, status,
                entity_type, entity_id, extracted_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
                document_type,
                filename or "",
                file_path or "",
                status,
                entity_type,
                entity_id,
                json.dumps(extracted, ensure_ascii=False),
            ),
        )
        return cur.lastrowid
    except Exception as e:
        print(f"[DB ERROR save_scan] {e}")
        return None
    finally:
        conn.close()


def list_scans(
    document_type: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    init_scan_history()
    conn = db.get_connection()
    try:
        clauses = []
        params: list[Any] = []
        if document_type:
            clauses.append("document_type = ?")
            params.append(document_type)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"""SELECT id, created_at, document_type, filename, file_path,
                       status, entity_type, entity_id, extracted_json
                FROM ai_scan_history {where}
                ORDER BY id DESC
                LIMIT {int(limit)}""",
            tuple(params),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["extracted"] = json.loads(d.pop("extracted_json") or "{}")
            except Exception:
                d["extracted"] = {}
            out.append(d)
        return out
    finally:
        conn.close()


def update_scan_status(
    scan_id: int,
    status: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
) -> bool:
    conn = db.get_connection()
    try:
        if entity_type is not None:
            cur = conn.execute(
                "UPDATE ai_scan_history SET status=?, entity_type=?, entity_id=? WHERE id=?",
                (status, entity_type, entity_id, scan_id),
            )
        else:
            cur = conn.execute(
                "UPDATE ai_scan_history SET status=? WHERE id=?",
                (status, scan_id),
            )
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_scan(scan_id: int) -> bool:
    conn = db.get_connection()
    try:
        cur = conn.execute("DELETE FROM ai_scan_history WHERE id=?", (scan_id,))
        return cur.rowcount > 0
    finally:
        conn.close()


def get_scan(scan_id: int) -> Optional[dict[str, Any]]:
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM ai_scan_history WHERE id=?", (scan_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["extracted"] = json.loads(d.pop("extracted_json") or "{}")
        except Exception:
            d["extracted"] = {}
        return d
    finally:
        conn.close()


# ============ STATISTICI ============

ENGINE_AI = "ai"
ENGINE_OFFLINE = "offline"
ENGINE_UNKNOWN = "unknown"


def _engine_bucket(extracted_json: Optional[str]) -> str:
    """Clasifică motorul folosit la extragere din payload-ul salvat."""
    try:
        engine = str((json.loads(extracted_json or "{}") or {}).get("engine") or "").lower()
    except Exception:
        return ENGINE_UNKNOWN
    if not engine:
        return ENGINE_UNKNOWN
    if "tesseract" in engine or "offline" in engine:
        return ENGINE_OFFLINE
    return ENGINE_AI


def _parse_created_at(value: str) -> Optional[datetime]:
    """created_at e text „DD.MM.YYYY HH:MM:SS" (formate mai vechi acceptate)."""
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime((value or "").strip(), fmt)
        except ValueError:
            continue
    return None


def _all_rows() -> list[tuple[str, str, str]]:
    init_scan_history()
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT created_at, document_type, extracted_json FROM ai_scan_history"
        ).fetchall()
        return [(r["created_at"], r["document_type"], r["extracted_json"]) for r in rows]
    finally:
        conn.close()


def scan_stats() -> dict[str, Any]:
    """Cifre pentru banda de statistici din fereastra AI Scanare."""
    now = datetime.now()
    total = month = ai = offline = unknown = 0
    for created_at, _doc_type, extracted_json in _all_rows():
        total += 1
        dt = _parse_created_at(created_at)
        if dt and dt.year == now.year and dt.month == now.month:
            month += 1
        bucket = _engine_bucket(extracted_json)
        if bucket == ENGINE_AI:
            ai += 1
        elif bucket == ENGINE_OFFLINE:
            offline += 1
        else:
            unknown += 1
    known = ai + offline
    return {
        "total": total,
        "this_month": month,
        "ai": ai,
        "offline": offline,
        "unknown": unknown,
        "ai_pct": round(ai * 100 / known, 1) if known else None,
        "offline_pct": round(offline * 100 / known, 1) if known else None,
    }


def monthly_scan_stats(months: int = 12) -> list[dict[str, Any]]:
    """Ultimele `months` luni calendaristice, de la cea mai veche la cea mai nouă."""
    now = datetime.now()
    buckets: dict[tuple[int, int], dict[str, Any]] = {}
    year, month = now.year, now.month
    keys: list[tuple[int, int]] = []
    for _ in range(max(1, months)):
        keys.append((year, month))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    keys.reverse()
    for key in keys:
        buckets[key] = {
            "year": key[0], "month": key[1],
            "label": f"{key[1]:02d}.{key[0] % 100:02d}",
            "total": 0, "ai": 0, "offline": 0, "unknown": 0,
        }

    for created_at, _doc_type, extracted_json in _all_rows():
        dt = _parse_created_at(created_at)
        if not dt:
            continue
        bucket = buckets.get((dt.year, dt.month))
        if bucket is None:
            continue
        bucket["total"] += 1
        bucket[_engine_bucket(extracted_json)] += 1

    return [buckets[k] for k in keys]


def doc_type_stats() -> dict[str, int]:
    """Câte scanări per tip de document."""
    out: dict[str, int] = {}
    for _created_at, doc_type, _extracted in _all_rows():
        out[doc_type or "necunoscut"] = out.get(doc_type or "necunoscut", 0) + 1
    return out

