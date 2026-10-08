"""Test-time network guard: blocks DNS lookups and socket connections to anything but loopback.

Guarantees the suite is fully offline (no Microsoft 365 / Graph / login.microsoftonline.com calls).
"""
from __future__ import annotations

import socket
import threading

_LOCK = threading.Lock()
_DEPTH = 0
_ORIGINAL = {}


class NetworkBlocked(OSError):
    pass


def _is_loopback(host) -> bool:
    if host is None:
        return True
    host = host.decode() if isinstance(host, bytes) else str(host)
    return host in ("localhost", "::1", "") or host.startswith("127.")


def _guarded_getaddrinfo(host, *args, **kwargs):
    if not _is_loopback(host):
        raise NetworkBlocked(f"network access blocked in offline prototype: {host}")
    return _ORIGINAL["getaddrinfo"](host, *args, **kwargs)


def _guarded_connect(self, address):
    if isinstance(address, tuple) and not _is_loopback(address[0]):
        raise NetworkBlocked(f"network access blocked in offline prototype: {address[0]}")
    return _ORIGINAL["connect"](self, address)


def install() -> None:
    global _DEPTH
    with _LOCK:
        if _DEPTH == 0:
            _ORIGINAL["getaddrinfo"] = socket.getaddrinfo
            _ORIGINAL["connect"] = socket.socket.connect
            socket.getaddrinfo = _guarded_getaddrinfo
            socket.socket.connect = _guarded_connect
        _DEPTH += 1


def uninstall() -> None:
    global _DEPTH
    with _LOCK:
        if _DEPTH == 0:
            return
        _DEPTH -= 1
        if _DEPTH == 0:
            socket.getaddrinfo = _ORIGINAL.pop("getaddrinfo")
            socket.socket.connect = _ORIGINAL.pop("connect")
