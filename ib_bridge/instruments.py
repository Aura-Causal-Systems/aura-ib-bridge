"""Engine <-> IB instrument map, loaded from ../../instruments.json (single source)."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from ib_async import Contract, Future, Stock


@dataclass(frozen=True)
class IbSpec:
    symbol: str
    sec_type: str
    exchange: str
    currency: str
    trading_class: str | None
    primary_exchange: str | None = None


@dataclass(frozen=True)
class MicroSpec:
    symbol: str
    trading_class: str | None
    exchange: str
    multiplier: float
    tick_size: float


@dataclass(frozen=True)
class Instrument:
    engine_symbol: str | None
    engine_desk: str
    display_name: str
    ib: IbSpec
    multiplier: float
    tick_size: float
    notation: str
    direction_sign: int
    micro: MicroSpec | None
    data_bundle: str | None
    roll_window_days: int | None
    notes: str | None
    tick_fraction_of_32nd: float | None

    @property
    def key(self) -> str:
        return self.engine_symbol or self.ib.trading_class or self.ib.symbol


def instruments_file() -> Path:
    # Packaged (PyInstaller) build: the file is bundled next to the frozen modules.
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root) / "instruments.json"
    # Source checkout: instruments.json sits at the repository root (this repo owns the map).
    return Path(__file__).resolve().parents[1] / "instruments.json"


def _spec(d: dict[str, Any]) -> IbSpec:
    return IbSpec(
        symbol=d["symbol"],
        sec_type=d["sec_type"],
        exchange=d["exchange"],
        currency=d["currency"],
        trading_class=d.get("trading_class"),
        primary_exchange=d.get("primary_exchange"),
    )


def _micro(d: dict[str, Any] | None) -> MicroSpec | None:
    if not d:
        return None
    return MicroSpec(
        symbol=d["symbol"],
        trading_class=d.get("trading_class"),
        exchange=d["exchange"],
        multiplier=float(d["multiplier"]),
        tick_size=float(d["tick_size"]),
    )


@lru_cache(maxsize=1)
def load_instruments() -> tuple[Instrument, ...]:
    raw = json.loads(instruments_file().read_text(encoding="utf-8"))
    out: list[Instrument] = []
    for r in raw["instruments"]:
        out.append(
            Instrument(
                engine_symbol=r.get("engine_symbol"),
                engine_desk=r["engine_desk"],
                display_name=r["display_name"],
                ib=_spec(r["ib"]),
                multiplier=float(r["multiplier"]),
                tick_size=float(r["tick_size"]),
                notation=r.get("notation", "decimal"),
                direction_sign=int(r.get("direction_sign", 1)),
                micro=_micro(r.get("micro")),
                data_bundle=r.get("data_bundle"),
                roll_window_days=r.get("roll_window_days"),
                notes=r.get("notes"),
                tick_fraction_of_32nd=r.get("tick_fraction_of_32nd"),
            )
        )
    return tuple(out)


def find_instrument(key: str) -> Instrument | None:
    """Look up by engine symbol, IB trading class, or IB symbol (case-insensitive)."""
    k = key.upper()
    for inst in load_instruments():
        if (inst.engine_symbol or "").upper() == k:
            return inst
    for inst in load_instruments():
        if (inst.ib.trading_class or "").upper() == k or inst.ib.symbol.upper() == k:
            return inst
    return None


def contract_for(inst: Instrument, micro: bool = False, expiry: str = "") -> Contract:
    """Build the (unqualified) ib_async Contract for an instrument. Expiry blank = all months."""
    if inst.ib.sec_type == "STK":
        c = Stock(inst.ib.symbol, inst.ib.exchange, inst.ib.currency)
        if inst.ib.primary_exchange:
            c.primaryExchange = inst.ib.primary_exchange
        return c
    if micro:
        if not inst.micro:
            raise ValueError(f"{inst.key} has no verified micro contract")
        c = Future(inst.micro.symbol, expiry, inst.micro.exchange, currency=inst.ib.currency)
        if inst.micro.trading_class:
            c.tradingClass = inst.micro.trading_class
        return c
    c = Future(inst.ib.symbol, expiry, inst.ib.exchange, currency=inst.ib.currency)
    if inst.ib.trading_class:
        c.tradingClass = inst.ib.trading_class
    return c


def spec_dict(inst: Instrument, micro: bool = False) -> dict[str, Any]:
    if micro and inst.micro:
        return {
            "symbol": inst.micro.symbol,
            "sec_type": "FUT",
            "exchange": inst.micro.exchange,
            "currency": inst.ib.currency,
            "trading_class": inst.micro.trading_class,
            "multiplier": inst.micro.multiplier,
            "tick_size": inst.micro.tick_size,
        }
    return {
        "symbol": inst.ib.symbol,
        "sec_type": inst.ib.sec_type,
        "exchange": inst.ib.exchange,
        "currency": inst.ib.currency,
        "trading_class": inst.ib.trading_class,
        "multiplier": inst.multiplier,
        "tick_size": inst.tick_size,
        "notation": inst.notation,
        "direction_sign": inst.direction_sign,
    }
