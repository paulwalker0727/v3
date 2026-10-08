"""Notificări e-mail către cetățeni (aprobare / respingere cerere): coadă + șabloane + trimitere.

Furnizorul implicit este SIMULAT (MOCK): mesajul se marchează „mock_sent" și NU pleacă niciun e-mail real.
Un furnizor real se adaugă în `PROVIDERS` și se activează cu setarea `email_provider`.
Destinatarul este emailul contului de cetățean (cererile anonime nu au email după preluarea de MAIN).
"""
from __future__ import annotations

import json
import os
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Any, Callable, Dict, List, Optional

import cimitir_db_router as db

NOTIFY_STATUSES = ("validat", "respins")
MAX_ATTEMPTS = 5
TEMPLATES = {
    "validat": ("Cererea {cod} a fost aprobată",
                "Bună ziua{nume},\n\nCererea dumneavoastră cu codul {cod} pentru locul {loc} a fost aprobată de {cimitir}.\n"
                "{mesaj}\nVă rugăm să urmăriți starea cererii în contul dumneavoastră de pe portal.\n\nCu respect,\n{cimitir}"),
    "respins": ("Cererea {cod} a fost respinsă",
                "Bună ziua{nume},\n\nCererea dumneavoastră cu codul {cod} pentru locul {loc} a fost respinsă de {cimitir}.\n"
                "{mesaj}\nPuteți depune o cerere nouă sau ne puteți contacta pentru clarificări.\n\nCu respect,\n{cimitir}"),
}


def ensure_table() -> None:
    conn = db.get_connection()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS email_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT, cod_urmarire TEXT, template TEXT, to_email TEXT, to_masked TEXT,
            subject TEXT, body TEXT, status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0, last_error TEXT,
            created_at TEXT, sent_at TEXT, UNIQUE(cod_urmarire, template))""")
    finally:
        conn.close()


def enabled() -> bool:
    return (db.get_setting("notify_email_enabled") or "1") != "0"


def provider_name() -> str:
    return (db.get_setting("email_provider") or "mock").strip().lower()


def mask_email(email: str) -> str:
    local, _, domain = (email or "").partition("@")
    return f"{local[:1]}***@{domain[:1]}***" if domain else ""


def render(template: str, cod: str, loc: str, nume: str, mesaj: str) -> tuple:
    subject, body = TEMPLATES[template]
    cimitir = db.get_setting("cimitir_nume") or "Cimitir"
    values = dict(cod=cod, loc=loc or "solicitat", nume=f" {nume}" if nume else "", cimitir=cimitir,
                  mesaj=f"\nMesaj de la operator: {mesaj}\n" if mesaj else "")
    return subject.format(**values), body.format(**values)


def _citizen_for(code: str) -> Optional[Dict[str, Any]]:
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT c.cod_loc AS cod_loc, z.email AS email, z.full_name AS full_name FROM cereri_concesionare c "
            "JOIN citizens z ON z.id=c.citizen_id WHERE c.cod_urmarire=? AND COALESCE(z.active,1)=1", (code,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def enqueue_status_email(code: str, status: str, message: str = "") -> bool:
    """Pune în coadă emailul pentru aprobare/respingere. Idempotent per (cerere, șablon). True dacă s-a adăugat."""
    if status not in NOTIFY_STATUSES or not enabled():
        return False
    ensure_table()
    who = _citizen_for(code)
    if not who or not who["email"]:
        return False
    subject, body = render(status, code, who["cod_loc"], who["full_name"] or "", message)
    conn = db.get_connection()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO email_outbox (cod_urmarire, template, to_email, to_masked, subject, body, created_at) "
            "VALUES (?,?,?,?,?,?,?)", (code, status, who["email"], mask_email(who["email"]), subject, body,
                                       datetime.now().isoformat(timespec="seconds")))
        return cur.rowcount > 0
    finally:
        conn.close()


def _send_mock(row: Dict[str, Any]) -> str:
    """Nu trimite nimic; mesajul (cu eventuale linkuri) se scrie în <DATA_DIR>/logs/mock_mail.log pentru dezvoltare."""
    try:
        import cimitir_config
        log_dir = os.path.join(str(cimitir_config.data_dir()), "logs")
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "mock_mail.log"), "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "to": row.get("to_email"),
                                     "subject": row.get("subject"), "body": row.get("body")}, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"[notify] mock log: {exc}")
    return "mock_sent"


def smtp_settings() -> Dict[str, str]:
    """Configurare SMTP din mediu (fără valori implicite pentru secrete): SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_SECURITY."""
    return {k: os.environ.get(f"SMTP_{k.upper()}", "").strip() for k in ("host", "port", "user", "password", "from", "security")}


def _send_smtp(row: Dict[str, Any]) -> str:
    cfg = smtp_settings()
    if not cfg["host"] or not cfg["from"]:
        raise RuntimeError("SMTP nu este configurat (SMTP_HOST, SMTP_FROM)")
    security = (cfg["security"] or "starttls").lower()
    port = int(cfg["port"] or (465 if security == "ssl" else 587))
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = cfg["from"], row["to_email"], row["subject"]
    message.set_content(row["body"])
    client = smtplib.SMTP_SSL(cfg["host"], port, timeout=20) if security == "ssl" else smtplib.SMTP(cfg["host"], port, timeout=20)
    try:
        if security == "starttls":
            client.starttls()
        if cfg["user"]:
            client.login(cfg["user"], cfg["password"])
        client.send_message(message)
    finally:
        client.quit()
    return "sent"


PROVIDERS: Dict[str, Callable[[Dict[str, Any]], str]] = {"mock": _send_mock, "smtp": _send_smtp}


def send_message(to_email: str, subject: str, body: str) -> str:
    """Trimite imediat un mesaj (reset parolă, alerte), fără a-l păstra în coadă. Întoarce starea sau ridică excepția furnizorului."""
    sender = PROVIDERS.get(provider_name())
    if sender is None:
        raise RuntimeError(f"Furnizor de email necunoscut: {provider_name()}")
    return sender({"to_email": to_email, "subject": subject, "body": body})


def send_pending() -> Dict[str, int]:
    """Trimite mesajele în așteptare. La succes adresa reală se șterge din coadă (rămâne doar cea mascată)."""
    ensure_table()
    sender = PROVIDERS.get(provider_name())
    conn = db.get_connection()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM email_outbox WHERE status IN ('pending','failed') AND attempts<? ORDER BY id", (MAX_ATTEMPTS,)).fetchall()]
    finally:
        conn.close()
    result = {"sent": 0, "failed": 0}
    for row in rows:
        try:
            if sender is None:
                raise RuntimeError(f"Furnizor de email necunoscut: {provider_name()}")
            status, error = sender(row), ""
        except Exception as exc:
            status, error = "failed", str(exc)[:200]
        conn = db.get_connection()
        try:
            if status == "failed":
                conn.execute("UPDATE email_outbox SET status='failed', attempts=attempts+1, last_error=? WHERE id=?", (error, row["id"]))
            else:
                conn.execute("UPDATE email_outbox SET status=?, attempts=attempts+1, last_error='', to_email='', sent_at=? WHERE id=?",
                             (status, datetime.now().isoformat(timespec="seconds"), row["id"]))
        finally:
            conn.close()
        result["failed" if status == "failed" else "sent"] += 1
    return result


def notify_status_change(code: str, status: str, message: str = "") -> None:
    """Punct de intrare din sincronizare: nu ridică niciodată excepții (nu blochează decizia din MAIN)."""
    try:
        if enqueue_status_email(code, status, message):
            send_pending()
    except Exception as exc:
        print(f"[notify] {code}: {exc}")


def recent(limit: int = 50) -> List[Dict[str, Any]]:
    ensure_table()
    conn = db.get_connection()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, cod_urmarire, template, to_masked, subject, status, attempts, last_error, created_at, sent_at "
            "FROM email_outbox ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    finally:
        conn.close()


def purge_sent(days: int, now: Optional[datetime] = None) -> int:
    ensure_table()
    limit = ((now or datetime.now()) - timedelta(days=days)).isoformat(timespec="seconds")
    conn = db.get_connection()
    try:
        return conn.execute("DELETE FROM email_outbox WHERE status<>'pending' AND status<>'failed' AND created_at<?", (limit,)).rowcount
    finally:
        conn.close()
