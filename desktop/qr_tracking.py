"""
qr_tracking.py — Persistență pentru istoricul QR-urilor generate.

Tabelă `qr_generations`:
    - loc_id (FK) — locul pentru care s-a generat QR
    - cod_loc — dublurat pentru query rapid
    - generated_at — timestamp ISO
    - generated_by — utilizator (username, opțional)
    - format — 'png' / 'batch_png' / 'pdf_labels'
    - path — calea fișierului (informativ; poate fi șters)
    - printed_at — timestamp când operatorul a bifat "printat"
    - printed_by — cine a bifat
    - notes — text liber
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

import database as db
from app_logging import get_logger

logger = get_logger(__name__)


def _ensure_table() -> None:
    """Creează tabela dacă nu există (rulează la primul acces)."""
    conn = db.get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS qr_generations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                loc_id INTEGER,
                cod_loc TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                generated_by TEXT,
                format TEXT NOT NULL,
                path TEXT,
                printed_at TEXT,
                printed_by TEXT,
                notes TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_qr_cod_loc ON qr_generations(cod_loc)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_qr_generated_at ON qr_generations(generated_at)")
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def log_generation(cod_loc: str,
                   format: str,
                   path: Optional[str] = None,
                   generated_by: Optional[str] = None,
                   notes: Optional[str] = None) -> int:
    """Înregistrează o generare QR. Returnează id-ul rândului."""
    _ensure_table()
    loc_id = db.get_loc_id_by_cod(cod_loc)
    conn = db.get_connection()
    try:
        cur = conn.execute("""
            INSERT INTO qr_generations
              (loc_id, cod_loc, generated_at, generated_by, format, path, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (loc_id, cod_loc, _now(), generated_by, format, path, notes))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def mark_printed(cod_loc: str, printed_by: Optional[str] = None) -> bool:
    """Marchează ultima generare pentru acest loc ca „printat"."""
    _ensure_table()
    conn = db.get_connection()
    try:
        row = conn.execute("""
            SELECT id FROM qr_generations
            WHERE cod_loc = ? AND printed_at IS NULL
            ORDER BY generated_at DESC LIMIT 1
        """, (cod_loc,)).fetchone()
        if not row:
            # nu există intrare fără printed_at — creăm una nouă cu ambele
            conn.execute("""
                INSERT INTO qr_generations
                    (cod_loc, generated_at, format, printed_at, printed_by, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (cod_loc, _now(), "manual", _now(), printed_by,
                  "Marcat printat manual, fără generare prealabilă"))
        else:
            conn.execute("""
                UPDATE qr_generations
                SET printed_at = ?, printed_by = ?
                WHERE id = ?
            """, (_now(), printed_by, row[0]))
        conn.commit()
        return True
    except Exception:
        logger.exception("Nu am putut marca printat pentru %s", cod_loc)
        return False
    finally:
        conn.close()


def unmark_printed(cod_loc: str) -> bool:
    """Șterge marcajul „printat" de la ultima generare printată."""
    _ensure_table()
    conn = db.get_connection()
    try:
        row = conn.execute("""
            SELECT id FROM qr_generations
            WHERE cod_loc = ? AND printed_at IS NOT NULL
            ORDER BY printed_at DESC LIMIT 1
        """, (cod_loc,)).fetchone()
        if row:
            conn.execute("UPDATE qr_generations SET printed_at = NULL, printed_by = NULL WHERE id = ?",
                         (row[0],))
            conn.commit()
        return True
    except Exception:
        logger.exception("Nu am putut demarca printat pentru %s", cod_loc)
        return False
    finally:
        conn.close()


def get_status_map() -> Dict[str, Dict[str, Any]]:
    """
    Returnează un map { cod_loc: {last_generated_at, last_format, printed_at, generations_count} }
    pentru afișare rapidă în tabelul de management.
    """
    _ensure_table()
    conn = db.get_connection()
    try:
        rows = conn.execute("""
            SELECT
                cod_loc,
                MAX(generated_at) AS last_generated_at,
                MAX(printed_at) AS last_printed_at,
                COUNT(*) AS gen_count
            FROM qr_generations
            GROUP BY cod_loc
        """).fetchall()
        result: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            result[r[0]] = {
                "last_generated_at": r[1],
                "last_printed_at": r[2],
                "gen_count": int(r[3] or 0),
            }
        return result
    finally:
        conn.close()


def list_history(cod_loc: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    """Istoricul complet (sau filtrat pe un loc)."""
    _ensure_table()
    conn = db.get_connection()
    try:
        if cod_loc:
            rows = conn.execute("""
                SELECT id, cod_loc, generated_at, generated_by, format, path,
                       printed_at, printed_by, notes
                FROM qr_generations
                WHERE cod_loc = ?
                ORDER BY generated_at DESC LIMIT ?
            """, (cod_loc, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT id, cod_loc, generated_at, generated_by, format, path,
                       printed_at, printed_by, notes
                FROM qr_generations
                ORDER BY generated_at DESC LIMIT ?
            """, (limit,)).fetchall()
        cols = ["id", "cod_loc", "generated_at", "generated_by", "format",
                "path", "printed_at", "printed_by", "notes"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def stats() -> Dict[str, int]:
    """Statistici sumare pentru dashboard."""
    _ensure_table()
    conn = db.get_connection()
    try:
        total_locs = conn.execute("SELECT COUNT(*) FROM locuri_de_veci").fetchone()[0]
        with_qr = conn.execute(
            "SELECT COUNT(DISTINCT cod_loc) FROM qr_generations").fetchone()[0]
        printed = conn.execute(
            "SELECT COUNT(DISTINCT cod_loc) FROM qr_generations WHERE printed_at IS NOT NULL"
        ).fetchone()[0]
        return {
            "total_locuri": int(total_locs or 0),
            "with_qr_generated": int(with_qr or 0),
            "with_qr_printed": int(printed or 0),
            "without_qr": max(0, int(total_locs or 0) - int(with_qr or 0)),
        }
    finally:
        conn.close()
