"""cimitir_prov_edge.py — terminator TLS local (demonstrație/dezvoltare): ascultă pe HTTPS și retransmite TCP spre backendul instanței.

În producție rolul îl are Caddy (certificat Let's Encrypt automat + HSTS), vezi deploy/provisioner/. Certificatul se reîncarcă la conexiunile noi
dacă fișierele s-au schimbat (reînnoire fără repornire; prin callback SNI).
    python -m cimitir_prov_edge --listen 127.0.0.1:PORT --upstream 127.0.0.1:PORT --cert F --key F
"""
from __future__ import annotations

import argparse
import asyncio
import os
import ssl
import sys


def _load(cert: str, key: str) -> ssl.SSLContext:
    c = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    c.minimum_version = ssl.TLSVersion.TLSv1_2
    c.load_cert_chain(cert, key)
    return c


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", required=True)
    ap.add_argument("--upstream", required=True)
    ap.add_argument("--cert", required=True)
    ap.add_argument("--key", required=True)
    a = ap.parse_args(argv)
    lh, lp = a.listen.rsplit(":", 1)
    uh, up = a.upstream.rsplit(":", 1)
    state = {"mtime": None, "ctx": None}

    def current() -> ssl.SSLContext:
        m = (os.stat(a.cert).st_mtime_ns, os.stat(a.key).st_mtime_ns)
        if m != state["mtime"]:
            state.update(mtime=m, ctx=_load(a.cert, a.key))
        return state["ctx"]

    base = current()

    def sni(sock, name, _ctx):
        try:
            sock.context = current()
        except Exception:  # noqa: BLE001
            pass

    base.sni_callback = sni

    async def pipe(r, w):
        try:
            while data := await r.read(65536):
                w.write(data)
                await w.drain()
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                w.close()
            except Exception:  # noqa: BLE001
                pass

    async def handle(r, w):
        try:
            ur, uw = await asyncio.open_connection(uh, int(up))
        except OSError:
            w.close()
            return
        await asyncio.gather(pipe(r, uw), pipe(ur, w))

    async def run():
        server = await asyncio.start_server(handle, lh, int(lp), ssl=base)
        async with server:
            await server.serve_forever()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
