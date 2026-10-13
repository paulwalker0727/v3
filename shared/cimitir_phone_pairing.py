"""cimitir_phone_pairing.py — Phone Pairing v1 (service layer): asocierea explicită a unui telefon (device MOBILE) cu UN cimitir, peste Field Inbox v1.

Flux: operatorul MAIN (legătură activă) emite un cod aleator, cu expirare scurtă și folosibil o singură dată -> telefonul îl introduce -> primește o cheie de legătură
(`Binding <cheie>`, aceeași schemă ca Field Inbox) legată de cimitirul codului. Tenantul vine DOAR din rândul codului emis server-side, niciodată din cererea telefonului.

Garanții:
- codul (20 caractere, ~99 biți) și cheia legăturii NU se stochează în clar: doar sha256; apar o singură dată în răspuns (la emitere / la pairing).
- consum atomic: `UPDATE ... WHERE used_at IS NULL AND expires_at > now()` (arbitrul e rândul din DB) în aceeași tranzacție cu crearea device-ului și a legăturii.
- rate limiting: contor atomic per găleată (IP sursă, id client) numărat ÎNAINTE de verificarea codului; sub plafon concurența nu poate depăși limita.
- orice eșec de pairing (cod greșit/expirat/folosit/format) întoarce același mesaj generic: fără oracol despre existența codului.
- telefonul primește doar legătură MOBILE (fără emitent de bootstrap, fără drept de accept/reject/imported din 018, fără emitere/listare/revocare). Revocarea (doar MAIN, doar în cimitirul lui) e definitivă.
- fără service-role / parole DB / secrete în răspuns, loguri sau audit.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from typing import Any, Dict, List, Optional

import cimitir_cloud_control as cc

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"      # 31 simboluri, fără 0/O/1/I/L
CODE_LEN = 20
DEFAULT_TTL, MIN_TTL, MAX_TTL = 600, 60, 900
MAX_OPEN_CODES = 10
RATE_LIMIT, RATE_WINDOW = 10, 900                # încercări per găleată / fereastră (secunde)
_CODE_RE = re.compile(rf"^[{ALPHABET}]{{{CODE_LEN}}}$")
_NAME_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,64}$")
_BUCKET_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")
GENERIC_REJECTION = "cod de pairing invalid, expirat sau deja folosit"


class PairingError(Exception):
    pass


class PairingRejected(PairingError):
    pass


class RateLimited(PairingError):
    def __init__(self, retry_after: int):
        super().__init__("prea multe încercări de pairing; reîncearcă mai târziu")
        self.retry_after = max(1, int(retry_after))


class TooManyOpenCodes(PairingError):
    pass


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))


def display_code(code: str) -> str:
    return "-".join(code[i:i + 5] for i in range(0, CODE_LEN, 5))


def normalize_code(raw: Any) -> Optional[str]:
    if not isinstance(raw, str) or len(raw) > 64:
        return None
    s = re.sub(r"[\s-]", "", raw).upper()
    return s if _CODE_RE.match(s) else None


def _h(v: str) -> str:
    return hashlib.sha256(v.encode("utf-8")).hexdigest()


def _buckets(source: str, client_id: Optional[str]) -> List[str]:
    out = ["ip:" + _h("ip|" + (source or "unknown"))[:40]]
    if client_id and _BUCKET_ID_RE.match(client_id):
        out.append("cid:" + _h("cid|" + client_id)[:40])
    return out


class PhonePairingService:
    def __init__(self, inbox, rate_limit: int = RATE_LIMIT, rate_window: int = RATE_WINDOW):
        self.inbox, self.cp = inbox, inbox.cp
        self.rate_limit, self.rate_window = int(rate_limit), int(rate_window)

    def _main(self, main_key: str) -> Dict[str, Any]:
        a = self.inbox.authenticate(main_key)
        self.inbox._need_main(a)
        return a

    # ------------------------------------------------------------------ emitere (MAIN)
    def issue_code(self, main_key: str, ttl: int = DEFAULT_TTL) -> Dict[str, Any]:
        ttl = min(max(MIN_TTL, int(ttl)), MAX_TTL)
        a = self._main(main_key)
        code = new_code()
        c = self.cp._c()
        try:
            c.execute("BEGIN")
            try:
                c.execute("SELECT cp_lock_cemetery(?)", (a["cemetery_uid"],))
                n = c.execute("SELECT count(*) AS n FROM device_pairing_codes WHERE cemetery_uid=? AND device_type='MOBILE' AND used_at IS NULL AND expires_at > now()",
                              (a["cemetery_uid"],)).fetchone()["n"]
                if n >= MAX_OPEN_CODES:
                    raise TooManyOpenCodes(f"maximum {MAX_OPEN_CODES} coduri active per cimitir")
                r = c.execute("INSERT INTO device_pairing_codes (code_hash, organization_uid, cemetery_uid, device_type, created_by, expires_at) "
                              "VALUES (?,?,?,'MOBILE',?, now() + make_interval(secs => ?)) RETURNING EXTRACT(EPOCH FROM expires_at)::float8 AS exp",
                              (_h(code), a["organization_uid"], a["cemetery_uid"], a["binding_uid"], ttl)).fetchone()
                self.cp._audit(c, a["binding_uid"], "phone_code_issued", a["organization_uid"], a["cemetery_uid"], ttl=ttl)
                c.execute("COMMIT")
            except BaseException:
                if c.in_transaction:
                    c.execute("ROLLBACK")
                raise
        finally:
            c.close()
        return {"code": display_code(code), "expires_at": r["exp"], "ttl": ttl, "organization_uid": a["organization_uid"], "cemetery_uid": a["cemetery_uid"]}

    # ------------------------------------------------------------------ rate limiting
    def _count_attempt(self, buckets: List[str]) -> None:
        self.cp.begin_request()
        c = self.cp._c(pair_buckets=",".join(buckets))
        try:
            worst = 0
            for b in buckets:
                r = c.execute(
                    "INSERT INTO cp_pairing_attempts (bucket, window_start, attempts) VALUES (?, now(), 1) ON CONFLICT (bucket) DO UPDATE SET "
                    "attempts = CASE WHEN cp_pairing_attempts.window_start <= now() - make_interval(secs => ?) THEN 1 ELSE cp_pairing_attempts.attempts + 1 END, "
                    "window_start = CASE WHEN cp_pairing_attempts.window_start <= now() - make_interval(secs => ?) THEN now() ELSE cp_pairing_attempts.window_start END "
                    "RETURNING attempts, GREATEST(1, CEIL(EXTRACT(EPOCH FROM (window_start + make_interval(secs => ?) - now()))))::int AS retry",
                    (b, self.rate_window, self.rate_window, self.rate_window)).fetchone()
                if r["attempts"] > self.rate_limit:
                    worst = max(worst, r["retry"])
            if worst:
                raise RateLimited(worst)
        finally:
            c.close()

    def _refund(self, buckets: List[str]) -> None:
        self.cp.begin_request()
        c = self.cp._c(pair_buckets=",".join(buckets))
        try:
            for b in buckets:
                c.execute("UPDATE cp_pairing_attempts SET attempts = GREATEST(attempts - 1, 0) WHERE bucket=?", (b,))
        finally:
            c.close()

    # ------------------------------------------------------------------ pairing (telefon, fără autentificare prealabilă)
    def redeem(self, code: Any, device_name: Any = None, client_id: Optional[str] = None, source: str = "") -> Dict[str, Any]:
        buckets = _buckets(source, client_id)
        self._count_attempt(buckets)
        norm = normalize_code(code)
        name = device_name.strip() if isinstance(device_name, str) and _NAME_RE.match(device_name.strip() or "\x00") else "Telefon"
        if not norm:
            raise PairingRejected(GENERIC_REJECTION)
        h = _h(norm)
        self.cp.begin_request()
        c = self.cp._c(code_hash=h)
        try:
            row = c.execute("SELECT organization_uid, cemetery_uid FROM device_pairing_codes WHERE code_hash=? AND device_type='MOBILE' AND used_at IS NULL AND expires_at > now()",
                            (h,)).fetchone()
            if not row:
                raise PairingRejected(GENERIC_REJECTION)
            org, cem = row["organization_uid"], row["cemetery_uid"]
            c.set_ctx(org=org, cems=cem)
            binding_uid, device_uid = str(uuid.uuid4()), str(uuid.uuid4())
            key = f"cimitir.{binding_uid}.{secrets.token_urlsafe(32)}"
            c.execute("BEGIN")
            try:
                c.execute("SELECT cp_lock_cemetery(?)", (cem,))
                n = c.execute("UPDATE device_pairing_codes SET used_at = now(), used_by_binding_uid = ? WHERE code_hash=? AND device_type='MOBILE' AND used_at IS NULL "
                              "AND expires_at > now()", (binding_uid, h)).rowcount
                if n != 1:
                    raise PairingRejected(GENERIC_REJECTION)
                if not c.execute("SELECT 1 FROM cemeteries WHERE uid=? AND organization_uid=?", (cem, org)).fetchone():
                    raise PairingRejected(GENERIC_REJECTION)
                c.execute("INSERT INTO devices (device_uid, organization_uid, installation_uid, device_name, device_type, created_by) VALUES (?,?,?,?,'MOBILE','phone-pairing')",
                          (device_uid, org, str(uuid.uuid4()), name))
                c.execute("INSERT INTO device_cemetery_bindings (binding_uid, device_uid, organization_uid, cemetery_uid, sync_key_hash, key_prefix, active) VALUES (?,?,?,?,?,?,true)",
                          (binding_uid, device_uid, org, cem, cc._h(key), key[:16]))
                self.cp._audit(c, device_uid, "phone_paired", org, cem, binding_uid=binding_uid)
                c.execute("COMMIT")
            except BaseException:
                if c.in_transaction:
                    c.execute("ROLLBACK")
                raise
        finally:
            c.close()
        self._refund(buckets)
        return {"binding_key": key, "binding_uid": binding_uid, "device_uid": device_uid, "organization_uid": org, "cemetery_uid": cem, "device_type": "MOBILE"}

    # ------------------------------------------------------------------ administrare (MAIN, în cimitirul propriu)
    def list_phones(self, main_key: str) -> List[Dict[str, Any]]:
        a = self._main(main_key)
        c = self.cp._c()
        try:
            rows = c.execute("SELECT b.binding_uid, b.device_uid, d.device_name, b.created_at, b.last_seen_at, b.revoked_at, (b.active AND b.revoked_at IS NULL) AS active "
                             "FROM device_cemetery_bindings b JOIN devices d ON d.device_uid=b.device_uid AND d.organization_uid=b.organization_uid "
                             "WHERE b.cemetery_uid=? AND b.organization_uid=? AND d.device_type='MOBILE' ORDER BY b.created_at, b.binding_uid",
                             (a["cemetery_uid"], a["organization_uid"])).fetchall()
            iso = lambda v: v.isoformat() if v is not None else None            # noqa: E731
            return [{"binding_uid": r["binding_uid"], "device_uid": r["device_uid"], "device_name": r["device_name"], "created_at": iso(r["created_at"]),
                     "last_seen_at": iso(r["last_seen_at"]), "revoked_at": iso(r["revoked_at"]), "active": bool(r["active"])} for r in rows]
        finally:
            c.close()

    def revoke_phone(self, main_key: str, binding_uid: str) -> Dict[str, Any]:
        try:
            binding_uid = str(uuid.UUID(str(binding_uid)))
        except ValueError:
            raise cc.ScopeError("legătură inexistentă") from None
        a = self._main(main_key)
        c = self.cp._c()
        try:
            n = c.execute("UPDATE device_cemetery_bindings SET active=false, revoked_at=COALESCE(revoked_at, now()), revoked_by=COALESCE(revoked_by, ?) "
                          "WHERE binding_uid=? AND cemetery_uid=? AND organization_uid=? AND device_uid IN "
                          "(SELECT device_uid FROM devices WHERE organization_uid=? AND device_type='MOBILE')",
                          (a["binding_uid"], binding_uid, a["cemetery_uid"], a["organization_uid"], a["organization_uid"])).rowcount
            if n != 1:
                raise cc.ScopeError("legătură inexistentă")
            self.cp._audit(c, a["binding_uid"], "phone_revoked", a["organization_uid"], a["cemetery_uid"], binding_uid=binding_uid)
            return {"binding_uid": binding_uid, "revoked": True}
        finally:
            c.close()
