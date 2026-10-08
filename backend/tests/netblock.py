"""Rețea INDISPONIBILĂ la nivel de kernel pentru procesul curent (Linux, fără root): filtru seccomp care refuză `socket()` pentru orice
familie în afară de AF_UNIX (deci nici AF_INET/AF_INET6, nici DNS, nici loopback TCP). Nu este un namespace de rețea (`unshare -n` nu e
permis în acest mediu), dar efectul pentru proces este același: orice încercare de conexiune eșuează cu ENETUNREACH. Ireversibil pe proces."""
from __future__ import annotations

import ctypes
import platform
import struct

_ARCH = {"x86_64": (0xC000003E, 41), "aarch64": (0xC00000B7, 198)}  # (AUDIT_ARCH, __NR_socket)
ENETUNREACH = 101


def install() -> bool:
    arch = _ARCH.get(platform.machine())
    if arch is None or not platform.system() == "Linux":
        return False
    audit, nr_socket = arch

    def ins(code, jt, jf, k):
        return struct.pack("HBBI", code, jt, jf, k)

    prog = [ins(0x20, 0, 0, 4), ins(0x15, 1, 0, audit), ins(0x06, 0, 0, 0x80000000),
            ins(0x20, 0, 0, 0), ins(0x15, 0, 3, nr_socket),
            ins(0x20, 0, 0, 16), ins(0x15, 1, 0, 1), ins(0x06, 0, 0, 0x00050000 | ENETUNREACH),
            ins(0x06, 0, 0, 0x7FFF0000)]
    buf = ctypes.create_string_buffer(b"".join(prog), 8 * len(prog))

    class SockFprog(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.c_void_p)]

    libc = ctypes.CDLL(None, use_errno=True)
    fprog = SockFprog(len(prog), ctypes.cast(buf, ctypes.c_void_p))
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        return False
    return libc.prctl(22, 2, ctypes.byref(fprog)) == 0  # PR_SET_SECCOMP, SECCOMP_MODE_FILTER


def probe() -> dict:
    """Încearcă conexiuni reale; întoarce rezultatul fiecăreia (pentru raport)."""
    import socket
    out = {}
    for name, fam, addr in (("internet_1.1.1.1:53", socket.AF_INET, ("1.1.1.1", 53)), ("loopback_tcp_127.0.0.1:9", socket.AF_INET, ("127.0.0.1", 9)),
                            ("ipv6_::1:9", socket.AF_INET6, ("::1", 9))):
        try:
            s = socket.socket(fam, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect(addr)
            out[name] = "CONNECTED"
        except OSError as exc:
            out[name] = f"blocked:{exc.errno}"
        finally:
            try:
                s.close()
            except Exception:
                pass
    return out
