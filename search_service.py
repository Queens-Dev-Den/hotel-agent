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
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from statistics import median
from pathlib import Path

import httpx

import config
from contracts import HotelCandidate, HotelRequest

log = logging.getLogger(__name__)
# httpx logs every request URL at INFO, and SerpApi's api_key is in the query string
logging.getLogger("httpx").setLevel(logging.WARNING)

SERPAPI_URL = "https://serpapi.com/search.json"
CACHE_DIR = Path(".cache/serpapi")
# City boundaries for the organizer's "within 20 km of the city border" rule. Free, no key;
# usage policy: identify the app and stay under 1 request/s (we make 1 per run, cached).
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_UA = "hotel-agent-hackathon/1.0 (github.com/Queens-Dev-Den/hotel-agent)"
# A city that OSM only knows as a point (no boundary) is treated as this wide around the point
POINT_CITY_RADIUS_KM = 3.0
_city_cache: dict[str, list[dict]] = {}
TOLL_FREE_PREFIXES = ("800", "833", "844", "855", "866", "877", "888")
_stats_lock = threading.Lock()


def _count(stats: dict[str, int] | None, key: str) -> None:
    if stats is not None:
        with _stats_lock:
            stats[key] = stats.get(key, 0) + 1


# --- HTTP -----------------------------------------------------------------------

def _serpapi(params: dict[str, str], stats: dict[str, int] | None = None) -> dict:
    """One SerpApi request. Never logs the URL or params: the key is in the query string.
    Adds 1 to stats["serpapi_searches"] per real request (local cache hits are free)."""
    cache_file = CACHE_DIR / (hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest() + ".json")
    if config.SEARCH_CACHE and cache_file.exists():
        return json.loads(cache_file.read_text())
    _count(stats, "serpapi_searches")

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


def best_offer(detail: dict) -> tuple[str | None, str | None]:
    """(link, site) of the cheapest booking option in a property-details response.
    Ties go to the hotel's own (official) site."""
    offers = []
    for p in detail.get("prices") or []:
        price = (p.get("total_rate") or {}).get("extracted_lowest") or (p.get("rate_per_night") or {}).get("extracted_lowest")
        if p.get("link") and isinstance(price, (int, float)) and price > 0:
            offers.append((price, not p.get("official"), p["link"], p.get("source")))
    if not offers:
        return None, None
    _, _, link, site = min(offers, key=lambda o: (o[0], o[1]))
    return link, site


# --- 20 km of the city border ------------------------------------------------------

def _radius_km() -> float:
    return float(getattr(config, "SEARCH_RADIUS_KM", 20.0))  # setting added on the contract branch


def _city_places(city: str) -> list[dict]:
    """Up to 5 OSM settlements named `city`, with boundary polygons. [] on any error."""
    key = city.strip().lower()
    if key in _city_cache:
        return _city_cache[key]
    try:
        resp = httpx.get(NOMINATIM_URL, headers={"User-Agent": NOMINATIM_UA}, timeout=config.HTTP_TIMEOUT_S, params={
            "q": city, "format": "jsonv2", "featureType": "settlement", "limit": 5,
            "polygon_geojson": 1, "polygon_threshold": 0.001,  # simplified outline is plenty for 20 km
        })
        resp.raise_for_status()
        places = resp.json()
    except Exception as exc:
        log.warning("city boundary lookup failed for %r: %s", city, type(exc).__name__)
        return []
    _city_cache[key] = places
    return places


def _rings(place: dict) -> list[list[tuple[float, float]]]:
    """Outer boundary rings as (lon, lat). A point-only place becomes [] (handled by distance)."""
    geo = place.get("geojson") or {}
    if geo.get("type") == "Polygon":
        return [[tuple(p) for p in geo["coordinates"][0]]]
    if geo.get("type") == "MultiPolygon":
        return [[tuple(p) for p in poly[0]] for poly in geo["coordinates"]]
    return []


def km_outside(lat: float, lon: float, place: dict) -> float:
    """0 if (lat, lon) is inside the place's boundary, else km to its nearest edge.
    Equirectangular projection around the point: well under 1% error at 20 km."""
    kx, ky = 111.320 * math.cos(math.radians(lat)), 110.574

    def xy(p_lon: float, p_lat: float) -> tuple[float, float]:
        return (p_lon - lon) * kx, (p_lat - lat) * ky  # the listing sits at the origin

    rings = _rings(place)
    if not rings:
        cx, cy = xy(float(place["lon"]), float(place["lat"]))
        return max(math.hypot(cx, cy) - POINT_CITY_RADIUS_KM, 0.0)
    best = math.inf
    for ring in rings:
        pts = [xy(*p) for p in ring]
        inside = False
        for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
            if (y1 > 0) != (y2 > 0) and 0 < x1 + (0 - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside  # ray cast along +x from the origin
            dx, dy = x2 - x1, y2 - y1
            t = max(0.0, min(1.0, -(x1 * dx + y1 * dy) / (dx * dx + dy * dy))) if dx or dy else 0.0
            best = min(best, math.hypot(x1 + t * dx, y1 + t * dy))
        if inside:
            return 0.0
    return best


def _coords(prop: dict) -> tuple[float, float] | None:
    gps = prop.get("gps_coordinates") or {}
    lat, lon = gps.get("latitude"), gps.get("longitude")
    return (float(lat), float(lon)) if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) else None


def pick_city(places: list[dict], props: list[dict]) -> dict | None:
    """The OSM place the listings are actually around ("Kingston" is Jamaica first in OSM).
    Prefers a real boundary over a point when both fit."""
    points = [c for c in map(_coords, props) if c]
    if not places:
        return None
    if not points:
        return places[0]
    mid = (median(p[0] for p in points), median(p[1] for p in points))
    return min(places, key=lambda pl: (round(km_outside(*mid, pl)), not _rings(pl)))


def _within_radius(props: list[dict], place: dict | None, stats: dict[str, int]) -> list[dict]:
    """Keep listings inside the city or within SEARCH_RADIUS_KM of its border."""
    if place is None:
        stats["radius_unchecked"] = 1  # lookup failed: keep everything rather than fail the run
        return props
    kept = []
    for prop in props:
        c = _coords(prop)
        if c is None:
            stats["no_coords"] = stats.get("no_coords", 0) + 1
        elif km_outside(*c, place) > _radius_km():
            stats["outside_radius"] = stats.get("outside_radius", 0) + 1
        else:
            kept.append(prop)
    return kept


def _is_toll_free(phone_e164: str) -> bool:
    return phone_e164.startswith("+1") and phone_e164[2:5] in TOLL_FREE_PREFIXES


# --- pipeline -------------------------------------------------------------------

def _search(request: HotelRequest, rentals: bool, stats: dict[str, int] | None = None) -> list[dict]:
    params = {**_base_params(request), "sort_by": "3"}  # 3 = lowest price
    if rentals:
        params["vacation_rentals"] = "true"
    else:
        params["q"] = f"hotels in {request.city}"
    try:
        return _serpapi(params, stats).get("properties") or []
    except Exception as exc:  # one search failing must not sink the other
        log.warning("search (rentals=%s) failed: %s", rentals, type(exc).__name__)
        return []


def _details(request: HotelRequest, prop: dict, source: str, stats: dict[str, int] | None = None) -> dict | None:
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
        return _serpapi(params, stats)
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
        places = pool.submit(_city_places, request.city)  # in parallel with the searches
        results = list(pool.map(lambda r: _search(request, r, stats), kinds))
        place = pick_city(places.result(), [p for props in results for p in props])
        results = [_within_radius(props, place, stats) for props in results]

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

        details = list(pool.map(lambda p: _details(request, p[1], p[2], stats), shortlist))

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
        booking_url, booking_source = best_offer(detail)
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
                booking_url=booking_url,
                booking_source=booking_source,
                website=detail.get("link") or None,
            ),
        ))

    candidates.sort(key=lambda c: (c[0], c[1]))
    top = [c[2] for c in candidates[: config.MAX_CANDIDATES]]
    if not top:
        raise RuntimeError(f"No callable listings (with phone and address) found in {request.city}")
    # Rank by price among the chosen ones, 1 = cheapest
    top.sort(key=lambda c: c.total_price)
    return [c.model_copy(update={"rank": i}) for i, c in enumerate(top, start=1)]
