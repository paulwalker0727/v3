"""6B — driverul SupabaseStorage extins (put_bytes fără upsert, stat, signed_url, list_prefix) față de un Supabase simulat, + reguli pure ale stratului Field Inbox."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import cimitir_field_storage as fs
import cimitir_storage as st

KEY = "sb_secret_fake_for_tests"


class _Fake(BaseHTTPRequestHandler):
    store: dict = {}

    def log_message(self, *a):
        pass

    def _ok(self):
        if self.headers.get("apikey") != KEY:
            self.send_response(401); self.end_headers(); return False
        return True

    def _send(self, code, body=b"{}", headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _obj(self):
        parts = self.path.split("?")[0].split("/")             # /storage/v1/object/[authenticated|sign/]bucket/key...
        i = 4 + (1 if parts[4] in ("authenticated", "sign") else 0)
        return "/".join(parts[i + 1:])

    def do_POST(self):
        if not self._ok():
            return
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        if "/object/list/" in self.path:
            prefix = json.loads(raw)["prefix"].rstrip("/") + "/"
            items = [{"name": k[len(prefix):], "id": "x", "metadata": {"size": len(v)}, "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"}
                     for k, v in self.store.items() if k.startswith(prefix) and "/" not in k[len(prefix):]]
            return self._send(200, json.dumps(items).encode())
        if "/object/sign/" in self.path:
            return self._send(200, json.dumps({"signedURL": f"/object/sign/b/{self._obj()}?token=t.o.k"}).encode())
        key = self._obj()
        if key in self.store and self.headers.get("x-upsert") != "true":
            return self._send(400, b'{"statusCode":"409","error":"Duplicate","message":"The resource already exists"}')
        self.store[key] = raw
        self._send(200, b'{"Key":"ok"}')

    def do_HEAD(self):
        if not self._ok():
            return
        v = self.store.get(self._obj())
        self.send_response(200 if v is not None else 400)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(v or b"")))
        self.end_headers()

    def do_GET(self):
        if not self._ok():
            return
        v = self.store.get(self._obj())
        self._send(200, v) if v is not None else self._send(400, b'{"error":"not_found"}')

    def do_DELETE(self):
        if self._ok():
            self.store.pop(self._obj(), None)
            self._send(200)


@pytest.fixture()
def sup():
    _Fake.store = {}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield st.SupabaseStorage(f"http://127.0.0.1:{httpd.server_address[1]}", KEY, "b")
    httpd.shutdown()


def test_put_bytes_no_upsert_stat_sign_list(sup):
    sup.put_bytes("field/o/c/u1", b"%PDF-1", "application/pdf")
    with pytest.raises(st.AlreadyExists):
        sup.put_bytes("field/o/c/u1", b"%PDF-2", "application/pdf")
    assert _Fake.store["field/o/c/u1"] == b"%PDF-1"
    sup.put_bytes("field/o/c/u1", b"%PDF-3", "application/pdf", upsert=True)
    assert sup.stat("field/o/c/u1")["size"] == 6 and sup.stat("field/o/c/nu") is None
    url = sup.signed_url("field/o/c/u1", 60)
    assert url.endswith("token=t.o.k") and KEY not in url
    assert [o["name"] for o in sup.list_prefix("field/o/c")] == ["u1"]
    sup.delete("field/o/c/u1")
    assert sup.list_prefix("field/o/c") == []


def test_wrong_key_is_rejected_without_leaking_it(sup):
    bad = st.SupabaseStorage(sup.base.replace("/storage/v1", ""), "sb_secret_wrong", "b")
    with pytest.raises(st.StorageError) as e:
        bad.put_bytes("field/x", b"x")
    assert "sb_secret_wrong" not in str(e.value) and KEY not in str(e.value)


def test_path_is_server_built_and_formats_are_closed():
    assert fs.storage_path("O", "C", "U") == "field/O/C/U"
    assert set(fs.MAGIC) == {"application/pdf", "image/jpeg", "image/png"} and fs.MAX_SIGNED_TTL <= 300
    with pytest.raises(fs.FieldStorageError):
        fs._norm_uuid("../../etc/passwd", "uid")
