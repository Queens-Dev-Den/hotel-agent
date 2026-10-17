from datetime import date

import pytest

import search_service
from contracts import HotelRequest
from search_service import find_candidates, to_e164, total_price

REQ = HotelRequest(city="Toronto", check_in=date(2026, 10, 18), check_out=date(2026, 10, 20))


def prop(name, nightly=None, total=None, token=None):
    p = {"name": name, "property_token": token or name}
    if nightly is not None:
        p["rate_per_night"] = {"extracted_lowest": nightly}
    if total is not None:
        p["total_rate"] = {"extracted_lowest": total}
    return p


@pytest.fixture
def fake_api(monkeypatch):
    """Route _serpapi to in-memory search and details responses."""
    state = {"hotels": [], "rentals": [], "details": {}, "fail": set()}

    def fake(params, stats=None):
        if "property_token" in params:
            token = params["property_token"]
            if token in state["fail"]:
                raise TimeoutError
            return state["details"].get(token, {})
        return {"properties": state["rentals"] if params.get("vacation_rentals") else state["hotels"]}

    monkeypatch.setattr(search_service, "_serpapi", fake)
    return state


# --- helpers --------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("(416) 555-0199", "+14165550199"),
    ("+1 416-555-0199", "+14165550199"),
    ("1-416-555-0199", "+14165550199"),
    ("+44 20 7946 0958", "+442079460958"),
    ("555-0199", None),
    ("(016) 555-0199", None),
    ("", None),
    (None, None),
])
def test_to_e164(raw, expected):
    assert to_e164(raw) == expected


def test_total_price_prefers_total_rate():
    assert total_price(prop("a", nightly=100, total=230), 2) == 230
    assert total_price(prop("a", nightly=100), 2) == 200
    assert total_price(prop("a"), 2) is None


# --- find_candidates ------------------------------------------------------------

def test_cheapest_three_with_phones_ranked(fake_api):
    fake_api["hotels"] = [prop("H1", nightly=150), prop("H2", nightly=90), prop("H3", nightly=120)]
    fake_api["rentals"] = [prop("R1", nightly=60), prop("R2", nightly=200)]
    for i, name in enumerate(["H1", "H2", "H3", "R1", "R2"]):
        fake_api["details"][name] = {"phone": f"(416) 555-010{i}", "address": f"{i} King St"}

    out = find_candidates(REQ)

    assert [c.name for c in out] == ["R1", "H2", "H3"]
    assert [c.rank for c in out] == [1, 2, 3]
    assert out[0].total_price == 120  # 60 x 2 nights
    assert out[0].source == "serpapi_rental"
    assert out[1].source == "serpapi_hotel"


def test_drops_no_price_no_phone_no_address_dupes_and_failures(fake_api):
    fake_api["hotels"] = [
        prop("noprice"),
        prop("nophone", nightly=50),
        prop("noaddr", nightly=51),
        prop("fails", nightly=52),
        prop("ok", nightly=53),
        prop("dupe", nightly=54),
    ]
    fake_api["details"] = {
        "nophone": {"address": "1 A St"},
        "noaddr": {"phone": "416-555-0001"},
        "ok": {"phone": "416-555-0002", "address": "2 B St"},
        "dupe": {"phone": "+1 (416) 555-0002", "address": "3 C St"},
    }
    fake_api["fail"] = {"fails"}
    stats = {}

    out = find_candidates(REQ, stats=stats)

    assert [c.name for c in out] == ["ok"]
    assert stats == {"no_price": 1, "no_phone": 1, "no_address": 1, "details_failed": 1, "duplicate": 1}


def test_toll_free_ranked_after_local(fake_api):
    fake_api["hotels"] = [prop("chain", nightly=50), prop("a", nightly=80), prop("b", nightly=90), prop("c", nightly=95)]
    fake_api["details"] = {
        "chain": {"phone": "1-800-555-0100", "address": "1 A St"},
        "a": {"phone": "416-555-0101", "address": "2 A St"},
        "b": {"phone": "416-555-0102", "address": "3 A St"},
        "c": {"phone": "416-555-0103", "address": "4 A St"},
    }

    assert [c.name for c in find_candidates(REQ)] == ["a", "b", "c"]


def test_cheap_phoneless_rentals_dont_crowd_out_hotels(fake_api):
    fake_api["rentals"] = [prop(f"R{i}", nightly=10 + i) for i in range(10)]
    fake_api["details"] = {f"R{i}": {"address": "Somewhere, ON"} for i in range(10)}
    fake_api["hotels"] = [prop(f"H{i}", nightly=100 + i) for i in range(6)]
    for i in range(6):
        fake_api["details"][f"H{i}"] = {"phone": f"416-555-010{i}", "address": f"{i} King St"}
    stats = {}

    out = find_candidates(REQ, stats=stats)

    assert [c.name for c in out] == ["H0", "H1", "H2"]
    assert stats["no_phone"] == 2  # only RENTAL_FANOUT rentals were looked up


def test_one_search_failing_still_returns(fake_api, monkeypatch):
    real = search_service._serpapi

    def flaky(params, stats=None):
        if params.get("vacation_rentals"):
            raise TimeoutError
        return real(params, stats)

    monkeypatch.setattr(search_service, "_serpapi", flaky)
    fake_api["hotels"] = [prop("H1", nightly=100)]
    fake_api["details"]["H1"] = {"phone": "416-555-0100", "address": "1 King St"}

    assert [c.name for c in find_candidates(REQ)] == ["H1"]


def test_nothing_callable_raises(fake_api):
    fake_api["hotels"] = [prop("H1", nightly=100)]
    with pytest.raises(RuntimeError, match="Toronto"):
        find_candidates(REQ)


def test_best_offer_picks_cheapest_site_ties_go_official():
    detail = {"prices": [
        {"source": "Agoda", "link": "https://a", "total_rate": {"extracted_lowest": 84}},
        {"source": "Booking.com", "link": "https://b", "total_rate": {"extracted_lowest": 77}},
        {"source": "The Hotel", "link": "https://h", "official": True, "total_rate": {"extracted_lowest": 77}},
        {"source": "NoLink", "total_rate": {"extracted_lowest": 10}},
    ]}
    assert search_service.best_offer(detail) == ("https://h", "The Hotel")
    assert search_service.best_offer({}) == (None, None)


def test_candidate_carries_booking_link_and_website(fake_api):
    fake_api["hotels"] = [prop("H1", nightly=100)]
    fake_api["details"]["H1"] = {
        "phone": "416-555-0100", "address": "1 King St", "link": "https://h1.example",
        "prices": [{"source": "Booking.com", "link": "https://book/h1", "total_rate": {"extracted_lowest": 190}}],
    }

    [c] = find_candidates(REQ)

    assert (c.booking_url, c.booking_source, c.website) == ("https://book/h1", "Booking.com", "https://h1.example")


def test_serpapi_searches_counted_but_not_cache_hits(monkeypatch, tmp_path):
    monkeypatch.setattr(search_service, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(search_service.config, "SEARCH_CACHE", True)

    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"properties": []}

    monkeypatch.setattr(search_service.httpx, "get", lambda *a, **k: Resp())
    stats = {}
    search_service._serpapi({"q": "x"}, stats)
    search_service._serpapi({"q": "x"}, stats)  # replayed from .cache/
    assert stats == {"serpapi_searches": 1}
