"""Bearer-token auth.

The desk is served from a public HTTPS origin and reaches this loopback
server, so Chrome gates the request behind the Local Network Access
permission; the CORS middleware in main.py answers that preflight
(allow_private_network=True). Every non-preflight request must carry the
bridge token, compared in constant time.
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request

from .config import BridgeConfig


def require_token(cfg: BridgeConfig):
    async def _dep(request: Request) -> None:
        header = request.headers.get("authorization", "")
        scheme, _, presented = header.partition(" ")
        if scheme.lower() != "bearer" or not presented or not secrets.compare_digest(presented, cfg.token):
            raise HTTPException(status_code=401, detail="missing_or_invalid_bridge_token")

    return _dep
