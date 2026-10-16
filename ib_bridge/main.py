"""FastAPI entry point.

Run with the uvicorn factory flag so importing this module has no side effects
(tests build the app with a fake IB client):

    uvicorn ib_bridge.main:create_app --factory --host 127.0.0.1 --port 7801
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .auth import require_token
from .config import BridgeConfig, load_config
from .events_discovery import EventsService
from .ib_client import IBClient
from .routes import router
from .service import BridgeService


def create_app(cfg: BridgeConfig | None = None, client: IBClient | None = None, connect: bool = True) -> FastAPI:
    cfg = cfg or load_config()
    logging.basicConfig(level=getattr(logging, cfg.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # ib_async is chatty at INFO about every tick; keep it to warnings.
    logging.getLogger("ib_async").setLevel(logging.WARNING)

    ib_client = client or IBClient(cfg)
    events = EventsService(ib_client)
    service = BridgeService(ib_client, event_venues=[])

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if connect:
            await ib_client.start()
        yield
        if connect:
            await ib_client.stop()

    app = FastAPI(title="ib-bridge", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.cfg = cfg
    app.state.client = ib_client
    app.state.service = service
    app.state.events = events

    # CORS is the outermost layer so preflights never hit auth. allow_private_network
    # answers Chrome's Private/Local Network Access preflight for a public HTTPS
    # origin reaching this loopback server.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.effective_allowed_origins(),
        allow_credentials=False,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        allow_private_network=True,
        max_age=600,
    )
    app.include_router(router, dependencies=[Depends(require_token(cfg))])
    return app
