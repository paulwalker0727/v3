"""Simulatoare pentru Phone Capture: serviciu Field Inbox in-memory cu ACELEAȘI validări ca cel real (validate_item, request_hash, idempotență,
reguli de fișiere) și pairing in-memory, montate sub routerele REALE FastAPI, servite pe un port local (HTTP real) pentru codul JS al telefonului."""
from __future__ import annotations

import hashlib
import socket
import threading
import time
import uuid

import uvicorn
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

import cimitir_cloud_control as cc
import cimitir_field_inbox as fi
import cimitir_field_inbox_http as fih
import cimitir_field_storage as fs
import cimitir_phone_pairing as pp
import cimitir_phone_pairing_http as pph

ORG = str(uuid.uuid4())
CEM = str(uuid.uuid4())
OTHER_CEM = str(uuid.uuid4())


class PhoneInbox:
    cp = None

    def __init__(self):
        self.items, self.idem, self.files, self.blobs = {}, {}, {}, {}
        self.bindings = {}
        self.codes = {}
        self.log = []

    def add_binding(self, device_type="MOBILE", cemetery=CEM, active=True, expired=False):
        key = f"cimitir.{uuid.uuid4()}.{uuid.uuid4().hex}{uuid.uuid4().hex}"
        self.bindings[key] = {"binding_uid": str(uuid.uuid4()), "organization_uid": ORG, "cemetery_uid": cemetery, "device_type": device_type, "active": active, "expired": expired}
        return key

    def authenticate(self, key, cemetery_uid=None):
        b = self.bindings.get(key)
        if not b or not b["active"] or b["expired"]:
            raise cc.ScopeError("legătură invalidă")
        if cemetery_uid is not None and str(cemetery_uid) != b["cemetery_uid"]:
            raise cc.ScopeError("cimitir invalid")
        return {**{k: b[k] for k in ("binding_uid", "organization_uid", "cemetery_uid", "device_type")}, "main": b["device_type"] == "MAIN"}

    def _need_main(self, a):
        if not a["main"]:
            raise cc.PermissionDenied("operația cere un dispozitiv MAIN")

    def create_item(self, key, cem, idem, item_type, payload, grave_uid=None, target_uid=None, work_stage=None):
        self.log.append(("create", idem))
        if not isinstance(idem, str) or not fi._IDEM_RE.match(idem):
            raise fi.InvalidItem("idempotency_key invalid")
        clean = fi.validate_item(item_type, payload, grave_uid, target_uid, work_stage)
        h = fi.request_hash(item_type, work_stage, target_uid, grave_uid, clean)
        a = self.authenticate(key, cem)
        k = (a["cemetery_uid"], idem)
        if k in self.idem:
            uid, h0, by = self.idem[k]
            if by != a["binding_uid"] or h0 != h:
                raise fi.IdempotencyConflict("idempotency_key deja folosit")
            return {"item": dict(self.items[uid]), "replayed": True}
        uid = str(uuid.uuid4())
        self.items[uid] = {"uid": uid, "organization_uid": ORG, "cemetery_uid": a["cemetery_uid"], "item_type": item_type, "work_stage": work_stage, "target_uid": target_uid,
                           "grave_uid": grave_uid, "payload": clean, "status": "PENDING", "idempotency_key": idem, "created_by": a["binding_uid"]}
        self.files[uid] = []
        self.idem[k] = (uid, h, a["binding_uid"])
        return {"item": dict(self.items[uid]), "replayed": False}

    def attach_file(self, key, cem, item_uid, file_uid, filename, mime, data):
        self.log.append(("attach", item_uid, file_uid))
        a = self.authenticate(key, cem)
        it = self.items.get(item_uid)
        if not it or it["created_by"] != a["binding_uid"]:
            raise cc.ScopeError("item inexistent")
        if mime not in fs.MAGIC or not any(data.startswith(m) for m in fs.MAGIC[mime]):
            raise fs.BadContent("tip neacceptat (PDF/JPEG/PNG)")
        if it["item_type"] in fi.PHOTO_TYPES and mime == "application/pdf":
            raise fs.BadContent("un item foto acceptă doar JPEG/PNG")
        known = next((f for f in self.files[item_uid] if f["uid"] == file_uid), None)
        if known:
            return {"item_uid": item_uid, "file": known, "replayed": True}
        f = {"uid": file_uid, "filename": filename, "mime": mime, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "status": "READY",
             "kind": "document" if mime == "application/pdf" else "photo"}
        self.files[item_uid].append(f)
        self.blobs[file_uid] = data
        return {"item_uid": item_uid, "file": f, "replayed": False}

    def list_items(self, key, cem, status=None, item_type=None, limit=50, offset=0):
        a = self.authenticate(key, cem)
        return [dict(i) for i in self.items.values() if i["created_by"] == a["binding_uid"]][offset:offset + limit]

    def get_item(self, key, cem, uid):
        a = self.authenticate(key, cem)
        it = self.items.get(uid)
        if not it or it["created_by"] != a["binding_uid"]:
            raise cc.ScopeError("item inexistent")
        return {**it, "files": list(self.files[uid])}


class PhonePairing:
    def __init__(self, inbox: PhoneInbox):
        self.inbox = inbox

    def new_code(self, cemetery=CEM):
        code = pp.new_code()
        self.inbox.codes[code] = cemetery
        return pp.display_code(code)

    def redeem(self, code, device_name=None, client_id=None, source=""):
        norm = pp.normalize_code(code)
        cem = self.inbox.codes.pop(norm, None) if norm else None
        if not cem:
            raise pp.PairingRejected("codul nu a putut fi folosit")
        key = self.inbox.add_binding("MOBILE", cem)
        b = self.inbox.bindings[key]
        return {"binding_key": key, "binding_uid": b["binding_uid"], "device_uid": str(uuid.uuid4()), "organization_uid": ORG, "cemetery_uid": cem, "device_type": "MOBILE"}


def make_app(inbox: PhoneInbox, pairing: PhonePairing, origins=()):
    app = FastAPI()
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(origins), allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=fih._CORS_HEADERS)
    app.include_router(fih.create_router(inbox))
    app.include_router(pph.create_router(pairing))
    return app


class LiveServer:
    def __init__(self, app):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="critical"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        self.thread.start()
        for _ in range(200):
            if self.server.started:
                return self
            time.sleep(0.02)
        raise RuntimeError("server nepornit")

    def __exit__(self, *a):
        self.server.should_exit = True
        self.thread.join(5)
