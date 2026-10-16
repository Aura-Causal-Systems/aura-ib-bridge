"""Regression tests for the 'HTTP handler hangs forever on an IB request' class of bug.

ib_async keys some futures by a fixed name (currentTime, openOrders): overlapping callers
orphan the first future. Seen live 2026-09-12 (second /snapshot hung 60s while /book polled).
"""

import asyncio

import pytest

from ib_bridge.service import BridgeService
from tests.conftest import seed_es_position


@pytest.mark.asyncio
async def test_req_returns_none_on_timeout_and_records_error(ib_client):
    async def never():
        await asyncio.sleep(10)

    result = await ib_client._req(never(), 0.05, "probe")
    assert result is None
    assert ib_client.last_error == "timeout: probe"


@pytest.mark.asyncio
async def test_concurrent_health_calls_share_one_clock_request(fake_ib, ib_client):
    seed_es_position(fake_ib, ib_client)
    calls = {"n": 0}
    original = fake_ib.reqCurrentTimeAsync

    async def counted():
        calls["n"] += 1
        await asyncio.sleep(0.01)
        return await original()

    fake_ib.reqCurrentTimeAsync = counted
    svc = BridgeService(ib_client)
    results = await asyncio.wait_for(asyncio.gather(*(svc.health() for _ in range(6))), timeout=2)
    assert all(h.connected for h in results)
    assert all(h.server_time_utc for h in results)
    assert calls["n"] == 1, "server time must be fetched once and served from the cached offset"


@pytest.mark.asyncio
async def test_open_orders_serialized_and_cached(fake_ib, ib_client):
    seed_es_position(fake_ib, ib_client)
    calls = {"n": 0}

    async def slow_orders():
        calls["n"] += 1
        await asyncio.sleep(0.02)
        return []

    fake_ib.reqAllOpenOrdersAsync = slow_orders
    svc = BridgeService(ib_client)
    await asyncio.wait_for(asyncio.gather(*(svc.open_orders() for _ in range(5))), timeout=2)
    assert calls["n"] == 1
