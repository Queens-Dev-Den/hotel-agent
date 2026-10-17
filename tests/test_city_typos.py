"""A misspelled city must never search 'anywhere in Canada' (real bug: 'Calgalary' and 'Tornto'
both returned the same cheap hostels across the country, and the radius check was skipped)."""
from datetime import date

import pytest

import search_service
from contracts import HotelRequest
from search_service import UnknownCityError, find_candidates, suggest_city

CALGARY = {"lat": "51.05", "lon": "-114.07", "geojson": {"type": "Polygon", "coordinates": [[
    [-114.30, 50.85], [-113.85, 50.85], [-113.85, 51.21], [-114.30, 51.21], [-114.30, 50.85]]]}}
IN_CALGARY = {"latitude": 51.045, "longitude": -114.06}
GIBSONS_BC = {"latitude": 49.43, "longitude": -123.65}


@pytest.mark.parametrize("typed,expected", [
    ("Calgalary", "Calgary"), ("Tornto", "Toronto"), ("tornoto", "Toronto"), ("Vancover", "Vancouver"),
    ("Montrael", "Montreal"), ("Ottowa", "Ottawa"), ("Niagra Falls", "Niagara Falls"), ("Calgary", "Calgary"),
])
def test_suggest_city(typed, expected):
    assert suggest_city(typed) == expected


@pytest.mark.parametrize("typed", ["Zzxqv", "Blorptown", ""])
def test_no_suggestion_for_nonsense(typed):
    assert suggest_city(typed) is None


@pytest.fixture
def api(monkeypatch):
    """OSM knows only Calgary; SerpApi returns a Calgary hotel for 'Calgary' and a far-away
    hostel for anything else (what Google does with an unknown city)."""
    state = {"searched": [], "osm": {"calgary": [CALGARY]}}

    def serpapi(params, stats=None):
        if "property_token" in params:
            return {"phone": "403-555-0100", "address": "1 Stephen Ave, Calgary"}
        state["searched"].append(params["q"])
        if "Calgary" in params["q"]:
            return {"properties": [{"name": "Calgary Inn", "property_token": "c", "gps_coordinates": IN_CALGARY,
                                    "rate_per_night": {"extracted_lowest": 120}}]}
        return {"properties": [{"name": "Far Hostel", "property_token": "f", "gps_coordinates": GIBSONS_BC,
                                "rate_per_night": {"extracted_lowest": 30}}]}

    monkeypatch.setattr(search_service, "_serpapi", serpapi)
    monkeypatch.setattr(search_service, "_city_places", lambda city: state["osm"].get(city.strip().lower(), []))
    monkeypatch.setattr(search_service.config, "SEARCH_RENTALS", False)
    return state


def req(city):
    return HotelRequest(city=city, check_in=date(2026, 11, 13), check_out=date(2026, 11, 14))


def test_typo_is_corrected_and_searched_again(api):
    r = req("Calgalary")
    stats = {}
    out = find_candidates(r, stats=stats)
    assert [c.name for c in out] == ["Calgary Inn"]       # never the far-away hostel
    assert r.city == "Calgary" and stats["city_corrected"] == 1
    assert api["searched"][-1] == "hotels in Calgary"


def test_unknown_city_with_no_close_match_refuses(api):
    with pytest.raises(UnknownCityError, match="Blorptown"):
        find_candidates(req("Blorptown"))


def test_correct_city_does_not_search_twice(api):
    stats = {}
    find_candidates(req("Calgary"), stats=stats)
    assert api["searched"] == ["hotels in Calgary"] and "city_corrected" not in stats
