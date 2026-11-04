"""Latest JPEG sent by the phone."""

from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_jpeg: bytes | None = None
_at = 0.0
_url = ""


def update(jpeg: bytes) -> None:
    global _jpeg, _at
    with _lock:
        _jpeg = jpeg
        _at = time.monotonic()


def latest(max_age: float = 45.0) -> bytes | None:
    with _lock:
        if _jpeg is None or time.monotonic() - _at > max_age:
            return None
        return _jpeg


def age() -> float | None:
    with _lock:
        if _jpeg is None:
            return None
        return time.monotonic() - _at


def set_url(url: str) -> None:
    global _url
    with _lock:
        _url = url


def camera_url() -> str:
    with _lock:
        return _url
