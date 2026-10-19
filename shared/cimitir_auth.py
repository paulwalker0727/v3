"""Autentificare + drepturi personal (unic pentru desktop și backend).

Roluri: admin (toate drepturile), operator (consultare+editare+aprobare, ajustabile),
user (doar consultare). `users.rights` (CSV) restrânge/extinde explicit drepturile unui cont.
Conturile sincronizate de pe server au `lease_until`: fără reconectare la server, accesul local expiră.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import cimitir_config
import cimitir_db_router as db

ROLE_ADMIN, ROLE_OPERATOR, ROLE_USER = "admin", "operator", "user"
ROLES = (ROLE_ADMIN, ROLE_OPERATOR, ROLE_USER)
RIGHT_VIEW, RIGHT_EDIT, RIGHT_APPROVE = "view", "edit", "approve"
RIGHT_MANAGE_USERS, RIGHT_MANAGE_CONFIG = "manage_users", "manage_config"
RIGHTS = (RIGHT_VIEW, RIGHT_EDIT, RIGHT_APPROVE, RIGHT_MANAGE_USERS, RIGHT_MANAGE_CONFIG)
ROLE_RIGHTS = {
    ROLE_ADMIN: RIGHTS,
    ROLE_OPERATOR: (RIGHT_VIEW, RIGHT_EDIT, RIGHT_APPROVE),
    ROLE_USER: (RIGHT_VIEW,),
}
DEFAULT_LEASE_HOURS = 72
DEFAULT_USERS = [("admin", "admin123", ROLE_ADMIN), ("user", "user123", ROLE_USER)]


MAX_FAILS, LOCK_SECONDS = 5, 300
MIN_PASSWORD_LEN = 8
DEFAULT_PASSWORDS = frozenset(p for _, p, _ in DEFAULT_USERS)


# ------------------------------------------------------------------ blocare după încercări eșuate (persistată în DB)
def _fail_key(identifier: str) -> str:
    return "loginfail:" + (identifier or "").strip().lower()


def _fail_state(identifier: str) -> Tuple[int, float]:
    tries, _, until = (db.get_setting(_fail_key(identifier)) or "0,0").partition(",")
    try:
        return int(tries), float(until)
    except ValueError:
        return 0, 0.0


def lock_remaining(identifier: str) -> int:
    """Secunde rămase până la deblocare (0 = neblocat)."""
    return max(0, int(_fail_state(identifier)[1] - _now().timestamp()))


def record_failure(identifier: str) -> int:
    tries = _fail_state(identifier)[0] + 1
    until = _now().timestamp() + LOCK_SECONDS if tries >= MAX_FAILS else 0
    db.set_setting(_fail_key(identifier), f"{0 if until else tries},{until}")
    return tries


def clear_failures(identifier: str) -> None:
    if db.get_setting(_fail_key(identifier)):
        db.set_setting(_fail_key(identifier), "0,0")


def is_default_password(password: str) -> bool:
    return password in DEFAULT_PASSWORDS


# ------------------------------------------------------------------ parolă de schimbat la primul login (flag persistat în DB)
def _must_change_key(user_id: int) -> str:
    return f"mustchange:{int(user_id)}"


def must_change_flag(user_id: int) -> bool:
    return db.get_setting(_must_change_key(user_id)) == "1"


def set_must_change(user_id: int, required: bool) -> None:
    if required:
        db.set_setting(_must_change_key(user_id), "1")
    elif db.get_setting(_must_change_key(user_id)):
        db.set_setting(_must_change_key(user_id), "0")


def password_change_required(user_id: int) -> bool:
    """Regula comună web + MAIN: contul încă folosește o parolă implicită (verificat pe hash-ul din DB)."""
    conn = db.get_connection()
    try:
        row = conn.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return False
    return must_change_flag(user_id) or any(verify_password(p, row["password_hash"]) for p in DEFAULT_PASSWORDS)


def default_password_accounts() -> List[str]:
    """Conturile active care încă folosesc o parolă implicită (verificat pe hash) — înainte de accesul din rețea."""
    out = []
    for u in list_users():
        row = _row("id=?", int(u["id"]))
        if row and u.get("active", 1) and any(verify_password(p, row["password_hash"]) for p in DEFAULT_PASSWORDS):
            out.append(u["username"])
    return out


def validate_new_password(new_password: str, old_password: str = "") -> str:
    """Întoarce mesajul de eroare sau '' dacă parola nouă e acceptabilă."""
    if len(new_password or "") < MIN_PASSWORD_LEN:
        return f"Parola nouă trebuie să aibă cel puțin {MIN_PASSWORD_LEN} caractere."
    if is_default_password(new_password) or new_password == old_password:
        return "Alege o parolă diferită de cea curentă și de parolele implicite."
    return ""


# ------------------------------------------------------------------ parole
def hash_password(password: str, iterations: int = 200_000) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=32)
    return f"pbkdf2${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored or not password:
        return False
    try:
        if stored.startswith("pbkdf2$"):
            _, iters, salt_hex, hash_hex = stored.split("$", 3)
            dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters), dklen=32)
            return hmac.compare_digest(dk.hex(), hash_hex)
        if "$" in stored:
            salt_hex, sha_hex = stored.split("$", 1)
            h = hashlib.sha256(bytes.fromhex(salt_hex) + password.encode("utf-8"))
            return hmac.compare_digest(h.hexdigest(), sha_hex)
    except Exception:
        return False
    return False


# ------------------------------------------------------------------ drepturi
def effective_rights(role: str, rights_csv: Optional[str] = None) -> Tuple[str, ...]:
    if role == ROLE_ADMIN:
        return RIGHTS
    if rights_csv and rights_csv.strip():
        chosen = {r.strip() for r in rights_csv.split(",")} & set(RIGHTS)
        chosen.discard(RIGHT_MANAGE_USERS)
        chosen.discard(RIGHT_MANAGE_CONFIG)
        return tuple(r for r in RIGHTS if r in chosen | {RIGHT_VIEW})
    return ROLE_RIGHTS.get(role, (RIGHT_VIEW,))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(ts)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def lease_expired(lease_until: Optional[str], now: Optional[datetime] = None) -> bool:
    if not lease_until:
        return False
    until = _parse(lease_until)
    return until is None or (now or _now()) >= until


def _public(row: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": row["id"], "uid": row.get("uid") or "", "username": row["username"], "role": row["role"],
            "rights": list(effective_rights(row["role"], row.get("rights"))), "origin": row.get("origin") or "local",
            "lease_until": row.get("lease_until") or "", "cemetery_id": row.get("cemetery_id") or ""}


def _row(where: str, arg: Any) -> Optional[Dict[str, Any]]:
    conn = db.get_connection()
    try:
        r = conn.execute(f"SELECT * FROM users WHERE {where}", (arg,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def access_state(row: Optional[Dict[str, Any]], cemetery_id: Optional[str] = None) -> str:
    """'ok' sau motivul refuzului: missing | inactive | lease_expired | wrong_cemetery."""
    if not row:
        return "missing"
    if not int(row.get("active") if row.get("active") is not None else 1):
        return "inactive"
    if lease_expired(row.get("lease_until")):
        return "lease_expired"
    if cemetery_id and row.get("cemetery_id") and row["cemetery_id"] != cemetery_id:
        return "wrong_cemetery"
    return "ok"


def get_session_user(user_id: int, cemetery_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Reîncarcă contul din DB (revocări/expirări se aplică imediat); None dacă accesul nu mai e valid."""
    row = _row("id=?", int(user_id))
    return _public(row) if access_state(row, cemetery_id) == "ok" else None


def can(user: Optional[Dict[str, Any]], right: str) -> bool:
    if not user:
        return False
    fresh = get_session_user(user["id"], user.get("cemetery_id") or None)
    return bool(fresh and right in fresh["rights"])


# ------------------------------------------------------------------ conturi
ENV_ALLOW_DEFAULT = "CIMITIR_ALLOW_DEFAULT_USERS"
ENV_BOOT_USER, ENV_BOOT_PASSWORD = "CIMITIR_BOOTSTRAP_ADMIN_USER", "CIMITIR_BOOTSTRAP_ADMIN_PASSWORD"
MIN_ADMIN_PASSWORD_LEN = 12
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,40}$")


def default_users_allowed() -> bool:
    """Conturile implicite admin/admin123 + user/user123 NU se mai creează în instalările comerciale (nici local/SQLite): primul administrator se
    creează prin asistentul de configurare inițială. Excepție explicită, doar pentru dezvoltare/teste: CIMITIR_ALLOW_DEFAULT_USERS=1."""
    cimitir_config.load_env()
    return os.environ.get(ENV_ALLOW_DEFAULT, "").strip() == "1"


def first_run_required() -> bool:
    """True dacă registrul activ nu are niciun administrator local: trebuie rulat asistentul de configurare inițială."""
    return not admin_exists()


def setup_first_admin(username: str, password: str, confirm: str) -> Tuple[bool, str]:
    """Asistentul de configurare inițială: parola o alege utilizatorul, deci nu mai e nevoie de schimbare forțată la primul login."""
    if (password or "") != (confirm or ""):
        return False, "Parolele nu coincid."
    return create_first_admin(username, password, must_change=False)


def admin_exists() -> bool:
    """True dacă tabela users conține cel puțin un cont cu rol admin (activ sau nu)."""
    conn = db.get_connection()
    try:
        return int(conn.execute("SELECT COUNT(*) AS c FROM users WHERE role=?", (ROLE_ADMIN,)).fetchone()["c"]) > 0
    finally:
        conn.close()


def validate_first_admin(username: str, password: str) -> str:
    """Mesaj de eroare sau ''. Parola nu este niciodată inclusă în mesaj."""
    if not _USERNAME_RE.match(username or ""):
        return "Numele de utilizator: 3-40 caractere (litere, cifre, punct, minus, underscore)."
    if (username or "").lower() in ("admin", "administrator", "root", "user"):
        return "Alege un nume de utilizator diferit de cele implicite (admin, administrator, root, user)."
    if len(password or "") < MIN_ADMIN_PASSWORD_LEN:
        return f"Parola primului administrator trebuie să aibă cel puțin {MIN_ADMIN_PASSWORD_LEN} caractere."
    if is_default_password(password) or password.lower() == username.lower():
        return "Parola este prea previzibilă (parolă implicită sau egală cu numele de utilizator)."
    return ""


def create_first_admin(username: str, password: str, must_change: bool = True) -> Tuple[bool, str]:
    """Creează primul administrator. Refuză dacă există deja un admin. Contul trebuie să-și schimbe parola la primul login."""
    if admin_exists():
        return False, "Există deja un administrator; bootstrap-ul este dezactivat."
    error = validate_first_admin(username, password)
    if error:
        return False, error
    uid = create_user(username.strip(), password, ROLE_ADMIN)
    if not uid or not admin_exists():
        return False, "Contul nu a putut fi creat (nume deja folosit?)."
    if must_change:
        set_must_change(uid, True)
    return True, ("Primul administrator a fost creat; la primul login i se cere schimbarea parolei." if must_change else "Primul administrator local a fost creat.")


def bootstrap_from_env() -> str:
    """Bootstrap automat la pornire: DOAR dacă nu există niciun admin ȘI sunt setate ambele variabile de mediu.
    Întoarce un mesaj de stare fără parolă ('' dacă nu s-a făcut nimic). Se dezactivează singur după crearea primului admin."""
    cimitir_config.load_env()
    user = os.environ.get(ENV_BOOT_USER, "").strip()
    password = os.environ.get(ENV_BOOT_PASSWORD, "")
    if not user or not password or admin_exists():
        return ""
    ok, msg = create_first_admin(user, password)
    return ("[bootstrap] " + msg + (" Șterge CIMITIR_BOOTSTRAP_ADMIN_PASSWORD din configurație." if ok else ""))


def init_auth() -> None:
    db.init_db()
    if db.users_count() == 0 and default_users_allowed():
        for username, password, role in DEFAULT_USERS:
            create_user(username, password, role)
        return
    if not admin_exists():
        message = bootstrap_from_env()
        if message:
            print(message)


def authenticate_ex(username: str, password: str, cemetery_id: Optional[str] = None) -> Tuple[Optional[Dict], str]:
    if not username or not password:
        return None, "invalid"
    username = username.strip()
    if lock_remaining(username):
        return None, "locked"
    row = _row("username=?", username)
    if not row or not verify_password(password, row["password_hash"]):
        record_failure(username)
        return None, "locked" if lock_remaining(username) else "invalid"
    state = access_state(row, cemetery_id)
    if state != "ok":
        return None, state
    clear_failures(username)
    db.user_touch_login(row["id"])
    user = _public(row)
    user["must_change_password"] = is_default_password(password) or must_change_flag(row["id"])
    return user, "ok"


def authenticate(username: str, password: str, cemetery_id: Optional[str] = None) -> Optional[Dict]:
    return authenticate_ex(username, password, cemetery_id)[0]


def list_users() -> List[Dict[str, Any]]:
    conn = db.get_connection()
    try:
        rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = _public(dict(r))
        d.update(created_at=r["created_at"], last_login=r["last_login"], active=int(r["active"] if r["active"] is not None else 1),
                 version=r["version"] or 1, rights_csv=r["rights"] or "")
        out.append(d)
    return out


def create_user(username: str, password: str, role: str = ROLE_USER, rights: Optional[List[str]] = None,
                cemetery_id: str = "") -> Optional[int]:
    if not username or not password:
        return None
    role = role if role in ROLES else ROLE_USER
    now = datetime.now().isoformat(timespec="seconds")
    conn = db.get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at, uid, rights, active, cemetery_id, version, "
            "updated_at, origin) VALUES (?,?,?,?,?,?,1,?,1,?, 'local')",
            (username.strip(), hash_password(password), role, now, str(uuid.uuid4()),
             ",".join(rights) if rights else "", cemetery_id or db.get_setting("cemetery_id") or "", now))
        return cur.lastrowid
    except Exception as exc:
        print(f"[create_user] {exc}")
        return None
    finally:
        conn.close()


def _bump(conn, user_id: int, sets: str, args: tuple) -> bool:
    cur = conn.execute(f"UPDATE users SET {sets}, version=COALESCE(version,1)+1, updated_at=? WHERE id=?",
                       (*args, datetime.now().isoformat(timespec="seconds"), int(user_id)))
    return cur.rowcount > 0


def update_user(user_id: int, role: Optional[str] = None, rights: Optional[List[str]] = None,
                active: Optional[bool] = None) -> bool:
    conn = db.get_connection()
    try:
        ok = False
        if role in ROLES:
            ok = _bump(conn, user_id, "role=?", (role,)) or ok
        if rights is not None:
            ok = _bump(conn, user_id, "rights=?", (",".join(r for r in rights if r in RIGHTS),)) or ok
        if active is not None:
            ok = _bump(conn, user_id, "active=?", (1 if active else 0,)) or ok
        return ok
    finally:
        conn.close()


def change_password(user_id: int, new_password: str) -> bool:
    conn = db.get_connection()
    try:
        done = _bump(conn, user_id, "password_hash=?", (hash_password(new_password),))
    finally:
        conn.close()
    if done:
        set_must_change(user_id, False)  # parola nouă a fost aleasă de utilizator/administrator: obligația de schimbare dispare
    return done


def change_own_password(user_id: int, old_password: str, new_password: str) -> str:
    """Schimbă parola contului după verificarea celei vechi. Întoarce mesajul de eroare sau '' la succes."""
    row = _row("id=?", int(user_id))
    if not row or lock_remaining(row["username"]):
        return "Cont blocat temporar. Reîncearcă mai târziu."
    if not verify_password(old_password, row["password_hash"]):
        record_failure(row["username"])
        return "Parola curentă este greșită."
    error = validate_new_password(new_password, old_password)
    if error:
        return error
    clear_failures(row["username"])
    change_password(row["id"], new_password)
    return ""


def delete_user(user_id: int) -> bool:
    return db.user_delete(int(user_id))


def active_admin_count() -> int:
    return sum(1 for u in list_users() if u["role"] == ROLE_ADMIN and u["active"])


# ------------------------------------------------- personal sincronizat (MAIN)
def export_staff() -> List[Dict[str, Any]]:
    """Directorul de personal pentru MAIN (server → MAIN). Include conturile dezactivate = revocări."""
    conn = db.get_connection()
    try:
        rows = conn.execute("SELECT * FROM users WHERE COALESCE(origin,'local')='local'").fetchall()
    finally:
        conn.close()
    return [{"uid": r["uid"], "username": r["username"], "password_hash": r["password_hash"], "role": r["role"],
             "rights": r["rights"] or "", "active": int(r["active"] if r["active"] is not None else 1),
             "version": r["version"] or 1} for r in rows]


def apply_staff_snapshot(staff: List[Dict[str, Any]], lease_hours: float, now: Optional[datetime] = None) -> Dict[str, int]:
    """MAIN: aplică personalul primit; reînnoiește lease-ul celor activi; dezactivează cei dispăruți/revocați."""
    now = now or _now()
    lease = (now + timedelta(hours=float(lease_hours))).isoformat(timespec="seconds")
    stats = {"created": 0, "updated": 0, "revoked": 0, "unchanged": 0}
    seen = set()
    cemetery = db.get_setting("cemetery_id") or ""
    conn = db.get_connection()
    try:
        for s in staff:
            seen.add(s["uid"])
            row = conn.execute("SELECT * FROM users WHERE uid=?", (s["uid"],)).fetchone()
            active = 1 if int(s.get("active", 1)) else 0
            stamp = now.isoformat(timespec="seconds")
            if not row:
                taken = conn.execute("SELECT id FROM users WHERE username=?", (s["username"],)).fetchone()
                if taken:
                    continue
                conn.execute(
                    "INSERT INTO users (username, password_hash, role, created_at, uid, rights, active, cemetery_id, version,"
                    " updated_at, origin, lease_until) VALUES (?,?,?,?,?,?,?,?,?,?, 'synced', ?)",
                    (s["username"], s["password_hash"], s["role"], stamp, s["uid"], s.get("rights", ""), active,
                     cemetery, s["version"], stamp, lease if active else None))
                stats["created"] += 1
                continue
            if row["origin"] != "synced":
                continue
            if int(s["version"]) > int(row["version"] or 0):
                conn.execute("UPDATE users SET username=?, password_hash=?, role=?, rights=?, active=?, version=?, updated_at=? "
                             "WHERE id=?", (s["username"], s["password_hash"], s["role"], s.get("rights", ""), active,
                                            s["version"], stamp, row["id"]))
                stats["revoked" if not active and row["active"] else "updated"] += 1
            else:
                stats["unchanged"] += 1
            conn.execute("UPDATE users SET lease_until=? WHERE id=?", (lease if active else None, row["id"]))
        for row in conn.execute("SELECT id, uid, active FROM users WHERE origin='synced'").fetchall():
            if row["uid"] not in seen and row["active"]:
                conn.execute("UPDATE users SET active=0, lease_until=NULL WHERE id=?", (row["id"],))
                stats["revoked"] += 1
    finally:
        conn.close()
    return stats
