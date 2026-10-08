"""cimitir_remote.py — dispatch transparent: același serviciu rulează local (LocalControlPlane) sau prin transport HTTP (RemoteControlPlane)."""
from __future__ import annotations

import functools


def remotable(fn):
    @functools.wraps(fn)
    def wrapper(cp, *args, **kwargs):
        remote = getattr(cp, "remote_call", None)
        if remote is not None:
            return remote(fn.__name__, args, kwargs)
        return fn(cp, *args, **kwargs)
    return wrapper
