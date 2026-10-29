"""cimitir_prov_store.py — registrul serviciului de provizionare (SQLite), jurnalul de audit înlănțuit, operatorii și seiful de secrete.

Serviciul de provizionare e controlat de OPERATOR (nu de parohii): are propriul registru, propriii operatori (tokenuri hash-uite) și propriul seif.
Licența `.lic` și cheia proprietarului instanței NU sunt acceptate aici ca autorizare.
Seiful: AES-256-GCM, cheia-master DOAR din mediu/fișier 0600 (niciodată în registru/Git); numele secretului este legat de text (AAD),
deci un fișier copiat de la o instanță la alta nu se decriptează.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import stat
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ENV_MASTER, ENV_MASTER_FILE = "CIMITIR_PROVISIONER_MASTER_KEY", "CIMITIR_PROVISIONER_MASTER_KEY_FILE"
ENVIRONMENTS = ("TEST", "PROD")
ROLES = ("operator", "owner")


class ProvisionError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


# ------------------------------------------------------------------ redactare
_URL_CRED = re.compile(r"(\w+://[^:/\s@]+:)([^@\s]+)(@)")
_SECRET_KEY = re.compile(r"(pass|secret|token|key|credential|grant|authorization|url)", re.I)


def redact_text(text: Any) -> str:
    s = str(text)
    s = _URL_CRED.sub(r"\1***\3", s)
    s = re.sub(r"(sb_secret_|eyJ)[A-Za-z0-9_\-\.]{8,}", r"\1***", s)
    s = re.sub(r"(cpv_)[A-Za-z0-9_\-]{8,}", r"\1***", s)
    return re.sub(r"(?i)(password|passwd|pwd|secret)=([^&\s]+)", r"\1=***", s)


def redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("***" if _SECRET_KEY.search(str(k)) and not str(k).endswith(("_public_key", "_pubkey", "_fingerprint")) and v not in (None, "", False, True)
                    else redact(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact(x) for x in obj]
    return redact_text(obj) if isinstance(obj, str) else obj


# ------------------------------------------------------------------ seif
class Vault:
    """Secrete pe fișiere, AES-256-GCM, nume legat ca AAD."""

    def __init__(self, root: Path, master_key: bytes):
        if len(master_key) != 32:
            raise ProvisionError("VAULT_KEY", "cheia-master a seifului trebuie să aibă 32 de octeți", 500)
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self.root, self._aes = Path(root), AESGCM(master_key)
        self.root.mkdir(parents=True, exist_ok=True)
        _chmod(self.root, 0o700)

    @staticmethod
    def master_from_env(env=None) -> bytes:
        env = os.environ if env is None else env
        raw = (env.get(ENV_MASTER) or "").strip()
        if not raw and env.get(ENV_MASTER_FILE):
            p = Path(env[ENV_MASTER_FILE])
            if not p.is_file():
                raise ProvisionError("VAULT_KEY", f"fișierul {ENV_MASTER_FILE} nu există", 500)
            if os.name == "posix" and p.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
                raise ProvisionError("VAULT_KEY", f"fișierul {ENV_MASTER_FILE} trebuie să fie 0600 (fără acces pentru grup/alții)", 500)
            raw = p.read_text().strip()
        if not raw:
            raise ProvisionError("VAULT_KEY", f"lipsește {ENV_MASTER} sau {ENV_MASTER_FILE}: seiful nu poate fi deschis", 500)
        try:
            key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        except Exception:  # noqa: BLE001
            raise ProvisionError("VAULT_KEY", "cheia-master nu este base64url valid", 500) from None
        if len(key) != 32:
            raise ProvisionError("VAULT_KEY", "cheia-master trebuie să aibă 32 de octeți (base64url)", 500)
        return key

    @staticmethod
    def new_master() -> str:
        return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")

    def _path(self, name: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_./-]{1,200}", name or "") or ".." in name:
            raise ProvisionError("VAULT_NAME", "nume de secret invalid", 500)
        return self.root / (name.replace("/", "__") + ".sec")

    def set(self, name: str, value: str) -> None:
        nonce = secrets.token_bytes(12)
        blob = nonce + self._aes.encrypt(nonce, value.encode("utf-8"), name.encode())
        p = self._path(name)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(blob)
        _chmod(tmp, 0o600)
        os.replace(tmp, p)

    def get(self, name: str) -> Optional[str]:
        p = self._path(name)
        if not p.is_file():
            return None
        raw = p.read_bytes()
        try:
            return self._aes.decrypt(raw[:12], raw[12:], name.encode()).decode("utf-8")
        except Exception:  # noqa: BLE001
            raise ProvisionError("VAULT_READ", f"secretul {name!r} nu poate fi decriptat (cheie-master greșită sau fișier modificat)", 500) from None

    def delete(self, name: str) -> None:
        self._path(name).unlink(missing_ok=True)

    def names(self, prefix: str = "") -> List[str]:
        pre = prefix.replace("/", "__")
        return sorted(p.stem.replace("__", "/") for p in self.root.glob("*.sec") if p.stem.startswith(pre))


def _chmod(p: Path, mode: int) -> None:
    try:
        os.chmod(p, mode)
    except OSError:
        pass


# ------------------------------------------------------------------ registru
_SCHEMA = """
CREATE TABLE IF NOT EXISTS instances (
  id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, domain TEXT NOT NULL UNIQUE, environment TEXT NOT NULL,
  status TEXT NOT NULL, project_ref TEXT UNIQUE, db_key TEXT UNIQUE, backend_port INTEGER UNIQUE, edge_port INTEGER UNIQUE,
  data TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS steps (id INTEGER PRIMARY KEY AUTOINCREMENT, instance_id TEXT NOT NULL, ts REAL NOT NULL, step TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS operators (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, role TEXT NOT NULL, environment TEXT NOT NULL,
  token_sha256 TEXT NOT NULL UNIQUE, disabled INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS confirmations (code_sha256 TEXT PRIMARY KEY, operator_id TEXT NOT NULL, action TEXT NOT NULL, instance_id TEXT NOT NULL, expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS backups (id TEXT PRIMARY KEY, instance_id TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL, kind TEXT NOT NULL, created_at REAL NOT NULL, manifest TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, operator TEXT NOT NULL, action TEXT NOT NULL, instance_id TEXT NOT NULL DEFAULT '',
  outcome TEXT NOT NULL, detail TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT, 'audit este append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT, 'audit este append-only'); END;
"""
GENESIS = "0" * 64


class Registry:
    def __init__(self, path: Path, clock=time.time):
        self.path, self.clock, self._lock = Path(path), clock, threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)
        _chmod(self.path, 0o600)

    def _conn(self):
        c = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        return c

    # ---- instanțe
    def create_instance(self, row: Dict[str, Any]) -> None:
        now = self.clock()
        data = json.dumps(row.get("data") or {})
        try:
            with self._lock, self._conn() as c:
                c.execute("INSERT INTO instances (id,slug,domain,environment,status,backend_port,edge_port,data,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (row["id"], row["slug"], row["domain"], row["environment"], row["status"], row.get("backend_port"), row.get("edge_port"), data, now, now))
        except sqlite3.IntegrityError as exc:
            raise ProvisionError("DUPLICATE", "slug, domeniu sau port deja folosit de altă instanță", 409) from exc

    def get_instance(self, ident: str) -> Optional[Dict[str, Any]]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM instances WHERE id=? OR slug=?", (ident, ident)).fetchone()
        return self._inst(r) if r else None

    def list_instances(self, environment: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM instances" + (" WHERE environment=?" if environment else "") + " ORDER BY created_at", (environment,) if environment else ()).fetchall()
        return [self._inst(r) for r in rows]

    @staticmethod
    def _inst(r) -> Dict[str, Any]:
        d = dict(r)
        d["data"] = json.loads(d["data"] or "{}")
        return d

    def update_instance(self, ident: str, status: Optional[str] = None, data: Optional[Dict[str, Any]] = None, **cols) -> Dict[str, Any]:
        with self._lock, self._conn() as c:
            r = c.execute("SELECT * FROM instances WHERE id=? OR slug=?", (ident, ident)).fetchone()
            if not r:
                raise ProvisionError("NOT_FOUND", "instanță necunoscută", 404)
            merged = json.loads(r["data"] or "{}")
            merged.update(data or {})
            sets, vals = ["data=?", "updated_at=?"], [json.dumps(merged), self.clock()]
            if status:
                sets.append("status=?")
                vals.append(status)
            for k, v in cols.items():
                if k not in ("project_ref", "db_key", "backend_port", "edge_port", "domain"):
                    raise ValueError(k)
                sets.append(f"{k}=?")
                vals.append(v)
            try:
                c.execute(f"UPDATE instances SET {','.join(sets)} WHERE id=?", (*vals, r["id"]))
            except sqlite3.IntegrityError as exc:
                raise ProvisionError("DUPLICATE", "proiectul Supabase/baza de date este deja asociat(ă) altei instanțe (credențiale nepartajabile)", 409) from exc
        return self.get_instance(r["id"])

    def used_ports(self) -> set:
        with self._conn() as c:
            return {p for r in c.execute("SELECT backend_port,edge_port FROM instances").fetchall() for p in r if p}

    def log_step(self, instance_id: str, step: str, status: str, detail: str = "") -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO steps (instance_id,ts,step,status,detail) VALUES (?,?,?,?,?)", (instance_id, self.clock(), step, status, redact_text(detail)[:600]))

    def steps(self, instance_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT ts,step,status,detail FROM steps WHERE instance_id=? ORDER BY id DESC LIMIT ?", (instance_id, limit)).fetchall()]

    # ---- operatori
    @staticmethod
    def _h(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def create_operator(self, name: str, role: str, environment: str) -> str:
        if role not in ROLES or environment not in ENVIRONMENTS:
            raise ProvisionError("BAD_REQUEST", "rol sau mediu invalid")
        token = "cpv_" + secrets.token_urlsafe(32)
        try:
            with self._lock, self._conn() as c:
                c.execute("INSERT INTO operators (id,name,role,environment,token_sha256,created_at) VALUES (?,?,?,?,?,?)",
                          (secrets.token_hex(8), name, role, environment, self._h(token), self.clock()))
        except sqlite3.IntegrityError:
            raise ProvisionError("DUPLICATE", "operator existent", 409) from None
        return token

    def authenticate(self, token: str) -> Optional[Dict[str, Any]]:
        if not isinstance(token, str) or not token.startswith("cpv_"):
            return None
        with self._conn() as c:
            r = c.execute("SELECT id,name,role,environment,disabled FROM operators WHERE token_sha256=?", (self._h(token),)).fetchone()
        return dict(r) if r and not r["disabled"] else None

    def disable_operator(self, name: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("UPDATE operators SET disabled=1 WHERE name=?", (name,))

    def operators(self) -> List[Dict[str, Any]]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT id,name,role,environment,disabled,created_at FROM operators").fetchall()]

    # ---- confirmări (pas suplimentar pentru acțiuni distructive)
    def new_confirmation(self, operator_id: str, action: str, instance_id: str, ttl: int = 300) -> str:
        code = secrets.token_urlsafe(12)
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO confirmations VALUES (?,?,?,?,?,0)", (self._h(code), operator_id, action, instance_id, self.clock() + ttl))
        return code

    def consume_confirmation(self, code: str, operator_id: str, action: str, instance_id: str) -> bool:
        with self._lock, self._conn() as c:
            r = c.execute("SELECT * FROM confirmations WHERE code_sha256=?", (self._h(code or ""),)).fetchone()
            if not r or r["used"] or r["expires"] < self.clock() or r["operator_id"] != operator_id or r["action"] != action or r["instance_id"] != instance_id:
                return False
            c.execute("UPDATE confirmations SET used=1 WHERE code_sha256=?", (r["code_sha256"],))
        return True

    # ---- backup-uri
    def add_backup(self, row: Dict[str, Any]) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO backups VALUES (?,?,?,?,?,?,?,?)", (row["id"], row["instance_id"], row["path"], row["sha256"], row["size"], row["kind"], self.clock(), json.dumps(row.get("manifest") or {})))

    def backups(self, instance_id: str) -> List[Dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM backups WHERE instance_id=? ORDER BY created_at DESC", (instance_id,)).fetchall()
        return [{**dict(r), "manifest": json.loads(r["manifest"])} for r in rows]

    def get_backup(self, instance_id: str, backup_id: str) -> Optional[Dict[str, Any]]:
        return next((b for b in self.backups(instance_id) if b["id"] == backup_id), None)

    # ---- audit înlănțuit
    def audit(self, operator: str, action: str, instance_id: str = "", outcome: str = "ok", detail: Optional[Dict[str, Any]] = None) -> None:
        d = json.dumps(redact(detail or {}), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        with self._lock, self._conn() as c:
            row = c.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
            prev = row["hash"] if row else GENESIS
            ts = round(self.clock(), 3)
            h = hashlib.sha256("|".join([prev, str(ts), operator, action, instance_id, outcome, d]).encode()).hexdigest()
            c.execute("INSERT INTO audit (ts,operator,action,instance_id,outcome,detail,prev_hash,hash) VALUES (?,?,?,?,?,?,?,?)", (ts, operator, action, instance_id, outcome, d, prev, h))

    def audit_list(self, instance_id: str = "", limit: int = 200) -> List[Dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT id,ts,operator,action,instance_id,outcome,detail FROM audit" + (" WHERE instance_id=?" if instance_id else "") + " ORDER BY id DESC LIMIT ?",
                             ((instance_id, limit) if instance_id else (limit,))).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]

    def audit_verify(self) -> Dict[str, Any]:
        prev, n = GENESIS, 0
        with self._conn() as c:
            for r in c.execute("SELECT * FROM audit ORDER BY id").fetchall():
                h = hashlib.sha256("|".join([prev, str(r["ts"]), r["operator"], r["action"], r["instance_id"], r["outcome"], r["detail"]]).encode()).hexdigest()
                if r["prev_hash"] != prev or not hmac.compare_digest(h, r["hash"]):
                    return {"ok": False, "broken_at": r["id"], "entries": n}
                prev, n = r["hash"], n + 1
        return {"ok": True, "entries": n}
