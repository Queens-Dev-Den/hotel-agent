"""All settings, loaded once from .env. The only module that reads env vars.

Each owner adds their own section below; don't create a second config file.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    return value in ("1", "true", "yes") if value else default


# --- WS1: Search (search_service.py) -------------------------------------------
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "").strip()
SEARCH_CURRENCY = os.getenv("SEARCH_CURRENCY", "CAD").strip() or "CAD"
# Cheapest hotels that get a parallel phone/address lookup (1 SerpApi credit each)
SEARCH_TOP_N = _int("SEARCH_TOP_N", 5)
# Callable hotels kept after filtering (contract: max 3)
MAX_CANDIDATES = min(_int("MAX_CANDIDATES", 3), 3)
# Airbnb-style rentals: searched alongside hotels, but they rarely list a phone
SEARCH_RENTALS = _bool("SEARCH_RENTALS", True)
RENTAL_FANOUT = _int("RENTAL_FANOUT", 2)
HTTP_TIMEOUT_S = _int("HTTP_TIMEOUT_S", 10)
# Replay raw API responses from .cache/ in dev so test loops don't burn quota
SEARCH_CACHE = _bool("SEARCH_CACHE", False)

# --- WS1: Pipeline (pipeline.py) -----------------------------------------------
# Where the one email goes
RECEIPT_TO_EMAIL = os.getenv("RECEIPT_TO_EMAIL", "").strip()
# Safety switch: dial MOCK_HOTEL_PHONES instead of the real hotels. Defaults ON.
USE_MOCK_HOTEL = _bool("USE_MOCK_HOTEL", True)
MOCK_HOTEL_PHONES = [p.strip() for p in os.getenv("MOCK_HOTEL_PHONES", "").split(",") if p.strip()]
MAX_CALL_DURATION_S = _int("MAX_CALL_DURATION_S", 180)
TOOL_SERVER_PORT = _int("TOOL_SERVER_PORT", 8000)
# Modules replaced by pipeline fakes, e.g. "voice,email" (must be empty at integration)
MOCK_MODULES = {m.strip() for m in os.getenv("MOCK_MODULES", "").split(",") if m.strip()}
