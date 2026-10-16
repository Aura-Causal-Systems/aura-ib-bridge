from datetime import date, datetime, timezone
from types import SimpleNamespace

from ib_bridge.normalize import (
    avg_price_from_cost,
    choose_front,
    classify_event_contract,
    freshness_from_type,
    implied_probability,
    parse_ib_date,
    quote_from_ticker,
    to_float,
)
from tests.conftest import NAN, make_contract, make_ticker


def test_to_float_treats_nan_and_junk_as_missing():
    assert to_float(NAN) is None
    assert to_float(None) is None
    assert to_float("abc") is None
    assert to_float("12.5") == 12.5
    assert to_float(float("inf")) is None
    # IB UNSET_DOUBLE sentinel (seen live on PnLSingle.realizedPnL for FX virtual positions)
    assert to_float(1.7976931348623157e308) is None
    assert to_float(-1.7976931348623157e308) is None


def test_freshness_labels_follow_ib_market_data_type():
    assert freshness_from_type(1, True) == "live"
    assert freshness_from_type(2, True) == "frozen"
    assert freshness_from_type(3, True) == "delayed"
    assert freshness_from_type(4, True) == "delayed_frozen"
    assert freshness_from_type(None, True) == "n/a"
    assert freshness_from_type(1, False) == "n/a"  # type says live but nothing arrived yet


def test_quote_from_ticker_never_invents_values():
    c = make_contract(conId=1, symbol="ZN", secType="FUT")
    t = make_ticker(c)  # all NaN, no type
    q = quote_from_ticker(t)
    assert q.bid is q.ask is q.last is q.close is None
    assert q.freshness == "n/a"
    assert q.updated_utc is None


def test_quote_from_ticker_delayed_and_negative_bid_ask():
    c = make_contract(conId=2, symbol="DX", secType="FUT")
    t = make_ticker(c, bid=-1.0, ask=-1.0, last=99.31, marketDataType=3, time=datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc))
    q = quote_from_ticker(t, min_tick=0.005)
    assert q.bid is None and q.ask is None and q.last == 99.31
    assert q.freshness == "delayed"
    assert q.updated_utc == "2026-09-02T12:00:00Z"
    assert q.min_tick == 0.005


def test_avg_price_divides_by_multiplier_for_derivatives_only():
    assert avg_price_from_cost(50 * 5400.25, 50, "FUT") == (5400.25, "avg_cost_div_multiplier")
    assert avg_price_from_cost(78.12, 1, "STK") == (78.12, "avg_cost")
    assert avg_price_from_cost(1234.0, None, "FUT") == (None, "unknown")
    assert avg_price_from_cost(None, 50, "FUT") == (None, "unknown")


def test_classify_event_contracts():
    fx_yes = make_contract(secType="OPT", exchange="FORECASTX", right="C", tradingClass="FF")
    assert classify_event_contract(fx_yes) == (True, "FORECASTX", "YES", 1.0)
    fx_no = make_contract(secType="OPT", exchange="FORECASTX", right="P")
    assert classify_event_contract(fx_no) == (True, "FORECASTX", "NO", 1.0)
    cme = make_contract(secType="FOP", exchange="CME", tradingClass="ECES", right="C")
    assert classify_event_contract(cme) == (True, "CME_EVENT", "YES", 100.0)
    plain_fop = make_contract(secType="FOP", exchange="CME", tradingClass="ES", right="P")
    assert classify_event_contract(plain_fop) == (False, None, None, None)
    unknown_venue = make_contract(secType="OPT", exchange="KALSHI", right="C")
    assert classify_event_contract(unknown_venue) == (False, None, None, None)
    assert classify_event_contract(unknown_venue, extra_venues=["KALSHI"]) == (True, "KALSHI", "YES", None)


def test_implied_probability_rules():
    assert implied_probability(0.40, 0.44, None, 1.0) == (42.0, "mid")
    assert implied_probability(None, 0.44, None, 1.0) == (44.0, "ask")
    assert implied_probability(0.40, None, None, 1.0) == (40.0, "bid")
    assert implied_probability(None, None, 0.5, 1.0) == (50.0, "last")
    assert implied_probability(40.0, 44.0, None, 100.0) == (42.0, "mid")
    assert implied_probability(0.4, 0.44, None, None) == (None, "n/a")  # unknown payout -> no probability
    assert implied_probability(None, None, None, 1.0) == (None, "n/a")
    assert implied_probability(0.5, 0.4, None, 1.0) == (40.0, "ask")  # crossed book: don't mid it


def test_parse_ib_date():
    assert parse_ib_date("20261218") == date(2026, 12, 18)
    assert parse_ib_date("202612") == date(2026, 12, 1)
    assert parse_ib_date("20261218 15:00:00 US/Central") == date(2026, 12, 18)
    assert parse_ib_date("") is None
    assert parse_ib_date("garbage") is None


def test_choose_front_skips_expired_and_flags_roll_window():
    mk = lambda exp: SimpleNamespace(contract=make_contract(lastTradeDateOrContractMonth=exp))  # noqa: E731
    today = date(2026, 9, 14)
    front, nxt, note = choose_front([mk("20260918"), mk("20261218"), mk("20260619")], today, roll_window_days=8)
    assert front.contract.lastTradeDateOrContractMonth == "20260918"
    assert nxt.contract.lastTradeDateOrContractMonth == "20261218"
    assert note and "roll window" in note
    front2, nxt2, note2 = choose_front([mk("20261218"), mk("20270319")], today, roll_window_days=8)
    assert front2.contract.lastTradeDateOrContractMonth == "20261218" and note2 is None
    assert choose_front([], today, 8) == (None, None, None)
