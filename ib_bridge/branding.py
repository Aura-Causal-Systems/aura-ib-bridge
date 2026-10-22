"""Product constants baked into the packaged tray app.

DESKS lists the web desks this helper serves. Their origins form the CORS
allow-list (together with `allowed_origins` in bridge.json), so only these
sites can talk to the helper at all - and still only with the bearer token.
A desk advertises that it can pair by publishing
<origin>/.well-known/aura-ib-bridge.json  {"pair_path": "/setup"}
so a new desk can adopt the helper without a new installer release.
"""

APP_NAME = "Aura IB Bridge"
APP_ID = "AuraIBBridge"  # registry Run value / install folder identity
LEGACY_APP_IDS = ("IBCopilotBridge",)  # Run values written by the pre-0.3 "IB Copilot Bridge"

# Bumped when the HTTP API changes incompatibly; desks refuse to run against an older helper.
# 2 (0.3.1): explicit instrument keys + roles; spot FX (EURUSD/USDJPY/GBPUSD keys now resolve to
#            IDEALPRO spot; FX futures are keys 6E/6J/6B).
API_VERSION = 2

DESKS: list[dict[str, str]] = [
    {"id": "ib-copilot", "name": "IB Copilot", "origin": "https://causalcopilot.netlify.app"},
    {"id": "bond-copilot", "name": "Bond Copilot", "origin": "https://causalbond.netlify.app"},
]

WELL_KNOWN_PATH = "/.well-known/aura-ib-bridge.json"
