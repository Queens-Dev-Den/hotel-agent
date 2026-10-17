from datetime import date

import pytest

import agent_brain
from agent_brain import build_call_plan, estimate_tokens, fallback_extract, parse_prompt
from contracts import HotelCandidate, HotelRequest

TODAY = date(2026, 10, 17)  # a Saturday


def parse(prompt, **kw):
    return parse_prompt(prompt, today=TODAY, **kw)


# --- parse_prompt: the three Phase 1 prompts -----------------------------------

def test_cheapest_hotel_defaults():
    r = parse("cheapest hotel in Toronto", callback_email="me@example.com")
    assert r.city == "Toronto"
    assert r.check_in == date(2026, 10, 18)
    assert r.check_out == date(2026, 10, 19)
    assert r.guests == 1
    assert r.callback_email == "me@example.com"


def test_for_tomorrow():
    r = parse("cheapest hotel in Toronto for tomorrow")
    assert r.city == "Toronto"
    assert (r.check_in, r.check_out) == (date(2026, 10, 18), date(2026, 10, 19))


def test_date_and_guests():
    r = parse("cheapest hotel in Toronto on Nov 2 for 2 people")
    assert r.city == "Toronto"
    assert (r.check_in, r.check_out) == (date(2026, 11, 2), date(2026, 11, 3))
    assert r.guests == 2


# --- parse_prompt: other shapes ------------------------------------------------

@pytest.mark.parametrize(
    "prompt, check_in, check_out",
    [
        ("hotel in Toronto tonight", date(2026, 10, 17), date(2026, 10, 18)),
        ("hotel in Toronto from Nov 2 to Nov 5", date(2026, 11, 2), date(2026, 11, 5)),
        ("hotel in Toronto Nov 2-4", date(2026, 11, 2), date(2026, 11, 4)),
        ("hotel in Toronto on 2nd of November for 3 nights", date(2026, 11, 2), date(2026, 11, 5)),
        ("hotel in Toronto on 2026-12-24", date(2026, 12, 24), date(2026, 12, 25)),
        ("hotel in Toronto on Jan 3", date(2027, 1, 3), date(2027, 1, 4)),  # rolls to next year
        ("hotel in Toronto tomorrow for two nights", date(2026, 10, 18), date(2026, 10, 20)),
    ],
)
def test_dates(prompt, check_in, check_out):
    r = parse(prompt)
    assert (r.check_in, r.check_out) == (check_in, check_out)


def test_multi_word_lowercase_city_and_word_guests():
    r = parse("cheapest hotel in new york for three guests")
    assert r.city == "New York"
    assert r.guests == 3


def test_range_does_not_eat_guest_count():
    r = parse("hotel in Toronto on Nov 2 to 3 people")
    assert r.check_in == date(2026, 11, 2)
    assert r.guests == 3


def test_no_city_without_backboard_raises():
    with pytest.raises(ValueError):
        parse("find me something cheap")


def test_no_city_uses_backboard(monkeypatch):
    monkeypatch.setattr(
        agent_brain, "_backboard_complete",
        lambda system, user, max_tokens: ('{"city": "Ottawa", "check_in": "2026-11-02", "check_out": null, "guests": 2}', 60, 25),
    )
    usage = {"backboard_in": 0, "backboard_out": 0, "agent_llm": 0}
    r = parse("somewhere cheap at the capital", usage=usage)
    assert (r.city, r.check_in, r.check_out, r.guests) == ("Ottawa", date(2026, 11, 2), date(2026, 11, 3), 2)
    assert usage == {"backboard_in": 60, "backboard_out": 25, "agent_llm": 0}


def test_regex_path_spends_no_tokens(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("Backboard should not be called")
    monkeypatch.setattr(agent_brain, "_backboard_complete", boom)
    usage = {"backboard_in": 0, "backboard_out": 0}
    parse("cheapest hotel in Toronto on Nov 2 for 2 people", usage=usage)
    assert usage == {"backboard_in": 0, "backboard_out": 0}


# --- build_call_plan -----------------------------------------------------------

CANDIDATE = HotelCandidate(
    rank=2, name="Hotel Ocho", address="195 Spadina Ave, Toronto",
    phone_e164="+14165550123", total_price=149.0, source="serpapi",
)
REQUEST = HotelRequest(city="Toronto", check_in=date(2026, 11, 2), check_out=date(2026, 11, 4), guests=2)


def test_call_plan_vars():
    plan = build_call_plan(REQUEST, CANDIDATE, run_id="run-1")
    assert plan.dynamic_vars == {
        "run_id": "run-1",
        "candidate_rank": "2",
        "hotel_name": "Hotel Ocho",
        "check_in": "Monday, November 2",
        "check_out": "Wednesday, November 4",
        "nights": "2",
        "guests": "2",
    }
    assert plan.candidate == CANDIDATE


def test_every_template_var_is_supplied():
    plan = build_call_plan(REQUEST, CANDIDATE, run_id="run-1")
    used = set(__import__("re").findall(r"\{\{(\w+)\}\}", plan.system_prompt))
    assert used <= set(plan.dynamic_vars)


def test_system_prompt_within_budget():
    plan = build_call_plan(REQUEST, CANDIDATE, run_id="run-1")
    assert plan.prompt_tokens == estimate_tokens(plan.system_prompt)
    assert plan.prompt_tokens <= agent_brain.MAX_PROMPT_TOKENS


def test_prompt_forbids_booking():
    assert "NOT booking" in agent_brain.SYSTEM_PROMPT
    assert "Never book" in agent_brain.SYSTEM_PROMPT


# --- fallback_extract ----------------------------------------------------------

TRANSCRIPT = "Agent: Hi, do you have a room for November 2?\nHotel: Yes, we have one at 189 a night."


def test_fallback_extract(monkeypatch):
    monkeypatch.setattr(
        agent_brain, "_backboard_complete",
        lambda s, u, m: ('{"available": true, "quoted_rate": 189, "notes": "one room left"}', 120, 20),
    )
    usage = {}
    a = fallback_extract(TRANSCRIPT, 1, usage=usage)
    assert (a.candidate_rank, a.available, a.quoted_rate, a.source) == (1, True, 189.0, "backboard")
    assert usage == {"backboard_in": 120, "backboard_out": 20}


def test_fallback_extract_unknown(monkeypatch):
    monkeypatch.setattr(agent_brain, "_backboard_complete", lambda s, u, m: ('{"available": null}', 100, 5))
    assert fallback_extract(TRANSCRIPT, 3).available is None


def test_fallback_extract_unwired_returns_none():
    assert fallback_extract(TRANSCRIPT, 1) is None


def test_fallback_extract_bad_json(monkeypatch):
    monkeypatch.setattr(agent_brain, "_backboard_complete", lambda s, u, m: ("sorry, no idea", 100, 5))
    assert fallback_extract(TRANSCRIPT, 1) is None


def test_fallback_extract_respects_budget(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("over budget, should not call")
    monkeypatch.setattr(agent_brain, "_backboard_complete", boom)
    assert fallback_extract(TRANSCRIPT, 1, usage={"backboard_in": 1000, "backboard_out": 0}) is None
