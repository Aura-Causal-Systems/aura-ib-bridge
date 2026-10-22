"""Aura IB Bridge: read-only local bridge between a trader's TWS and Aura's web desks.

Rules: no fake or fallback data - missing is None; every quote carries its
market-data type; the bridge exposes no order-writing path at all (enforced by
tests/test_routes.py::test_no_write_paths_exist).
"""

__version__ = "0.3.1"
