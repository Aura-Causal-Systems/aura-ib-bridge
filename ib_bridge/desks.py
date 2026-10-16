"""Desk discovery: does a desk site know how to pair with this helper?

A desk publishes  <origin>/.well-known/aura-ib-bridge.json  containing
{"pair_path": "/setup"}. The tray only appends the token to URLs of desks that
advertise a pair path, so a desk that has not adopted the helper yet never
receives (or leaves in its address bar / history) the token. The desk list
itself is local configuration only: a remote file can never add origins to
the CORS allow-list.
"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Callable

from .branding import WELL_KNOWN_PATH

_PAIR_PATH = re.compile(r"^/[A-Za-z0-9/_\-.]{0,100}$")

Fetcher = Callable[[str, float], bytes]


def _http_get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "AuraIBBridge"})
    with urllib.request.urlopen(req, timeout=timeout) as res:  # noqa: S310 - https desk origins from local config
        return res.read(4096)


def valid_pair_path(value: object) -> str | None:
    if not isinstance(value, str) or not _PAIR_PATH.match(value) or ".." in value or "//" in value:
        return None
    return value


def discover_pair_path(origin: str, timeout: float = 4.0, fetch: Fetcher = _http_get) -> str | None:
    """The desk's pairing path, or None when the desk does not (yet) support the helper or is unreachable."""
    origin = origin.rstrip("/")
    if not origin.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        return None
    try:
        data = json.loads(fetch(origin + WELL_KNOWN_PATH, timeout).decode("utf-8-sig"))
    except Exception:  # noqa: BLE001 - network, 404, bad JSON: all mean "not supported"
        return None
    return valid_pair_path(data.get("pair_path")) if isinstance(data, dict) else None
