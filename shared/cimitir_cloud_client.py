"""cimitir_cloud_client.py — RemoteControlPlane: clientul HTTP al MAIN (Etapa 4, Pasul 5). Aceeași interfață ca LocalControlPlane pentru ce folosesc fluxurile A/B,
deci logica de bootstrap NU este rescrisă: `execute_flow_a/b` rulează neschimbate peste HTTP. Secretele pleacă DOAR în headerul Authorization.

Retry sigur: timeout/conexiune întreruptă => aceeași cerere se retrimite (serviciile sunt idempotente). Dacă tot nu răspunde: `TransportError` (derivat din `Crash`):
starea cloud e necunoscută, deci fluxul NU face rollback automat și rămâne reluabil din MAIN (execute_* cu același plan).
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

import httpx

import cimitir_bootstrap as bs
import cimitir_cloud_wire as wire


class TransportError(bs.Crash):
    """Conexiune/timeout epuizat: rezultatul cererii este necunoscut."""


class RemoteControlPlane:
    def __init__(self, base_url: str, pairing_code: Optional[str] = None, timeout: float = 5.0, retries: int = 3, backoff: float = 0.2):
        self.base, self.pairing_code, self.key = base_url.rstrip("/"), pairing_code, None
        self.timeout, self.retries, self.backoff = timeout, retries, backoff
        self.clock = time.time
        self._http = httpx.Client(timeout=timeout)

    def use_binding_key(self, key: str) -> None:
        self.key = key

    def close(self) -> None:
        self._http.close()

    # ---- transport
    def _auth(self, op: str, explicit_key: Optional[str] = None) -> str:
        kind = wire.OPS[op][1]
        key = explicit_key or self.key
        if kind == "binding" or (kind == "either" and key):
            if not key:
                raise wire.Unauthenticated("lipsește cheia legăturii")
            return f"Binding {key}"
        if not self.pairing_code:
            raise wire.Unauthenticated("lipsește codul de pairing")
        return f"Pairing {self.pairing_code}"

    def _call(self, op: str, args: list, kwargs: Optional[dict] = None, key: Optional[str] = None) -> Any:
        url = self.base + wire.API + wire.OPS[op][0]
        headers = {"Authorization": self._auth(op, key), "Content-Type": "application/json"}
        payload = json.dumps(wire.encode({"args": args, "kwargs": kwargs or {}}))
        last: Optional[BaseException] = None
        for attempt in range(self.retries + 1):
            try:
                r = self._http.post(url, content=payload, headers=headers)
                break
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
                time.sleep(self.backoff * (attempt + 1))
        else:
            raise TransportError(f"{op}: fără răspuns după {self.retries + 1} încercări ({type(last).__name__})")
        try:
            body = r.json()
        except ValueError:
            raise TransportError(f"{op}: răspuns ne-JSON (HTTP {r.status_code})") from None
        if body.get("ok"):
            return wire.decode(body["result"])
        cls = wire.CODE_TO_CLASS.get(body.get("code"))
        if cls is None:
            raise TransportError(f"{op}: eroare server {r.status_code} {body.get('code')}")
        raise cls(body.get("message", ""))

    def remote_call(self, name: str, args: tuple, kwargs: dict) -> Any:
        res = self._call(name, list(args), {k: v for k, v in kwargs.items() if k != "hook"})
        if name == "publish_flow_b" and kwargs.get("hook"):
            kwargs["hook"]("publish:after_commit")  # `publish:in_txn` există doar local (în tranzacția serverului)
        return res

    def raw_post(self, path: str, headers: Optional[dict] = None, content: Any = None) -> httpx.Response:
        return self._http.post(self.base + path, headers=headers or {}, content=content)

    # ---- interfața LocalControlPlane folosită de fluxuri
    def can(self, principal, action, organization_uid) -> bool:
        return self._call("can", [principal, action, organization_uid])

    def export_snapshot(self, actor, organization_uid, cemetery_uid) -> Dict[str, Any]:
        return self._call("export_snapshot", [actor, organization_uid, cemetery_uid])

    def preflight_for_code(self, code, installation_uid, cemetery_uid) -> Dict[str, Any]:
        self.pairing_code = code or self.pairing_code
        return self._call("preflight_for_code", [None, installation_uid, cemetery_uid])

    def begin_run(self, code, installation_uid, bootstrap_id, kind, cemetery_uid, plan_hash, plan=None):
        self.pairing_code = code or self.pairing_code
        return self._call("begin_run", [None, installation_uid, bootstrap_id, kind, cemetery_uid, plan_hash], {"plan": plan})

    def redeem_code(self, code, installation_uid, device_name, cemetery_uid, cemetery_name="", bootstrap_id=None, pending=False):
        self.pairing_code = code or self.pairing_code
        res = self._call("redeem_code", [None, installation_uid, device_name, cemetery_uid, cemetery_name], {"bootstrap_id": bootstrap_id, "pending": pending})
        self.key = res["binding_key"]
        return res

    def mark_failed_unauthenticated(self, bootstrap_id, installation_uid, error):
        return self._call("mark_failed_unauthenticated", [bootstrap_id, installation_uid, error])

    def get_run(self, bootstrap_id) -> Dict[str, Any]:
        return self._call("get_run", [bootstrap_id])

    def resume_run(self, binding_key, bootstrap_id, plan_hash):
        self.key = binding_key
        return self._call("resume_run", [None, bootstrap_id, plan_hash])

    def mark_verifying(self, binding_key, bootstrap_id):
        self.key = binding_key
        return self._call("mark_verifying", [None, bootstrap_id])

    def verify_empty(self, binding_key, bootstrap_id):
        self.key = binding_key
        return self._call("verify_empty", [None, bootstrap_id])

    def complete_run(self, binding_key, bootstrap_id):
        self.key = binding_key
        return self._call("complete_run", [None, bootstrap_id])

    def rollback_run(self, binding_key, bootstrap_id):
        self.key = binding_key
        return self._call("rollback_run", [None, bootstrap_id])

    def revoke_by_key(self, binding_key):
        self.key = binding_key
        return self._call("revoke_by_key", [None])

    def authenticate(self, binding_key, cemetery_uid=None, allow_pending=False):
        self.key = binding_key
        return self._call("authenticate", [None, cemetery_uid])
