"""WS1: cheapest callable listings (hotels, hostels and rentals) for a HotelRequest.

One SerpApi google_hotels search for hotels and one for vacation rentals run in
parallel, then a parallel property-details lookup for the cheapest few, because
phone numbers are only reliable in the details response. 0 LLM tokens.
Listings without a dialable phone and street address are dropped.
See docs/ws1-discovery.md.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

import config
from contracts import HotelCandidate, HotelRequest

log = logging.getLogger(__name__)

SERPAPI_URL = "https://serpapi.com/search.json"
CACHE_DIR = Path(".cache/serpapi")
TOLL_FREE_PREFIXES = ("800", "833", "844", "855", "866", "877", "888")


# --- HTTP -----------------------------------------------------------------------

def _serpapi(params: dict[str, str]) -> dict:
    """One SerpApi request. Never logs the URL or params: the key is in the query string."""
    cache_file = CACHE_DIR / (hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest() + ".json")
    if config.SEARCH_CACHE and cache_file.exists():
        return json.loads(cache_file.read_text())

    started = time.monotonic()
    resp = httpx.get(
        SERPAPI_URL,
        params={**params, "api_key": config.SERPAPI_API_KEY},
        timeout=config.HTTP_TIMEOUT_S,
    )
    log.info("serpapi %s -> %s in %.2fs", params.get("engine"), resp.status_code, time.monotonic() - started)
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        raise RuntimeError(f"SerpApi error: {data['error']}")

    if config.SEARCH_CACHE:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data))
    return data


def _base_params(request: HotelRequest) -> dict[str, str]:
    return {
        "engine": "google_hotels",
        "q": f"{request.city}",
        "check_in_date": request.check_in.isoformat(),
        "check_out_date": request.check_out.isoformat(),
        "adults": str(request.guests),
        "currency": config.SEARCH_CURRENCY,
        "gl": "ca",
        "hl": "en",
    }


# --- parsing --------------------------------------------------------------------

def to_e164(raw: str | None) -> str | None:
    """North American numbers -> +1XXXXXXXXXX; other +-prefixed numbers kept as digits."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if raw.strip().startswith("+") and not digits.startswith("1"):
        return f"+{digits}" if 8 <= len(digits) <= 15 else None
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    # NANP: area code and exchange can't start with 0 or 1
    if len(digits) == 10 and digits[0] not in "01" and digits[3] not in "01":
        return f"+1{digits}"
    return None


def total_price(prop: dict, nights: int) -> float | None:
    """Total stay price before fees, in the search currency. One basis for every listing."""
    total = (prop.get("total_rate") or {}).get("extracted_lowest")
    if isinstance(total, (int, float)) and total > 0:
        return float(total)
    nightly = (prop.get("rate_per_night") or {}).get("extracted_lowest")
    if isinstance(nightly, (int, float)) and nightly > 0:
        return float(nightly) * nights
    return None


def _is_toll_free(phone_e164: str) -> bool:
    return phone_e164.startswith("+1") and phone_e164[2:5] in TOLL_FREE_PREFIXES


# --- pipeline -------------------------------------------------------------------

def _search(request: HotelRequest, rentals: bool) -> list[dict]:
    params = {**_base_params(request), "sort_by": "3"}  # 3 = lowest price
    if rentals:
        params["vacation_rentals"] = "true"
    else:
        params["q"] = f"hotels in {request.city}"
    try:
        return _serpapi(params).get("properties") or []
    except Exception as exc:  # one search failing must not sink the other
        log.warning("search (rentals=%s) failed: %s", rentals, type(exc).__name__)
        return []


def _details(request: HotelRequest, prop: dict, source: str) -> dict | None:
    """Phone and address for one listing, or None if the lookup fails."""
    if prop.get("phone") and prop.get("address"):
        return prop
    token = prop.get("property_token")
    if not token:
        return None
    try:
        params = {**_base_params(request), "property_token": token}
        if source == "serpapi_rental":
            params["vacation_rentals"] = "true"
        return _serpapi(params)
    except Exception as exc:
        log.warning("details failed for %r: %s", prop.get("name"), type(exc).__name__)
        return None


def find_candidates(request: HotelRequest, *, stats: dict[str, int] | None = None) -> list[HotelCandidate]:
    """Up to 3 cheapest listings with a dialable phone, sorted by total price.

    `stats` gets drop counts added in place (for the Learnings slide).
    Raises RuntimeError if nothing callable is found.
    """
    stats = stats if stats is not None else {}
    nights = max((request.check_out - request.check_in).days, 1)

    kinds = (False, True) if config.SEARCH_RENTALS else (False,)
    with ThreadPoolExecutor(max_workers=config.SEARCH_TOP_N + config.RENTAL_FANOUT) as pool:
        results = list(pool.map(lambda r: _search(request, r), kinds))

        # Rentals get their own small budget: they are usually the cheapest but
        # rarely list a phone, so a shared budget would crowd out callable hotels.
        shortlist: list[tuple[float, dict, str]] = []
        for props, source, budget in zip(
            results, ("serpapi_hotel", "serpapi_rental"), (config.SEARCH_TOP_N, config.RENTAL_FANOUT)
        ):
            priced = []
            for prop in props:
                price = total_price(prop, nights)
                if price is None:
                    stats["no_price"] = stats.get("no_price", 0) + 1
                    continue
                priced.append((price, prop, source))
            priced.sort(key=lambda p: p[0])
            shortlist += priced[:budget]

        details = list(pool.map(lambda p: _details(request, p[1], p[2]), shortlist))

    seen: set[str] = set()
    candidates: list[tuple[bool, float, HotelCandidate]] = []
    for (price, prop, source), detail in zip(shortlist, details):
        if detail is None:
            stats["details_failed"] = stats.get("details_failed", 0) + 1
            continue
        phone = to_e164(detail.get("phone"))
        address = (detail.get("address") or "").strip()
        if not phone:
            stats["no_phone"] = stats.get("no_phone", 0) + 1
            continue
        if not address:
            stats["no_address"] = stats.get("no_address", 0) + 1
            continue
        if phone in seen:
            stats["duplicate"] = stats.get("duplicate", 0) + 1
            continue
        seen.add(phone)
        candidates.append((
            _is_toll_free(phone),  # central reservation lines rank after front desks
            price,
            HotelCandidate(
                rank=0,
                name=prop.get("name") or detail.get("name") or "Unknown",
                address=address,
                phone_e164=phone,
                total_price=round(price, 2),
                currency=config.SEARCH_CURRENCY,
                source=source,
            ),
        ))

    candidates.sort(key=lambda c: (c[0], c[1]))
    top = [c[2] for c in candidates[: config.MAX_CANDIDATES]]
    if not top:
        raise RuntimeError(f"No callable listings (with phone and address) found in {request.city}")
    # Rank by price among the chosen ones, 1 = cheapest
    top.sort(key=lambda c: c.total_price)
    return [c.model_copy(update={"rank": i}) for i, c in enumerate(top, start=1)]
