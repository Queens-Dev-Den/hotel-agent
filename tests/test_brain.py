from datetime import date

import httpx
import pytest

import agent_brain
import config
from agent_brain import build_call_plan, estimate_tokens, fallback_extract, parse_prompt
from contracts import HotelCandidate, HotelRequest

TODAY = date(2026, 10, 17)  # a Saturday


@pytest.fixture(autouse=True)
def no_real_backboard(monkeypatch):
    """config loads .env on import; never let a real key make network calls in tests."""
    monkeypatch.setattr(config, "BACKBOARD_API_KEY", "")
    monkeypatch.setattr(config, "BACKBOARD_ASSISTANT_ID", "")
    monkeypatch.setattr(agent_brain, "_assistant_id", None)


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
    assert usage == {"backboard_in": 60, "backboard_out": 25, "agent_llm": 0, "backboard_parse": 85}


@pytest.mark.parametrize("prompt", ["a hotel in the capital of Canada", "somewhere in a big city near the lake"])
def test_vague_city_goes_to_backboard(prompt):
    with pytest.raises(ValueError):  # no Backboard in tests, so the fallback can't resolve it
        parse(prompt)


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
        "hotel_notes": "none",
    }
    assert plan.candidate == CANDIDATE


def test_call_plan_passes_hotel_notes():
    plan = build_call_plan(REQUEST, CANDIDATE, run_id="run-1", hotel_notes="menu: press 1 for reservations")
    assert plan.dynamic_vars["hotel_notes"] == "menu: press 1 for reservations"


def test_prompt_with_longest_notes_within_budget():
    longest = "x" * agent_brain.HOTEL_NOTES_MAX_CHARS
    assert estimate_tokens(agent_brain.SYSTEM_PROMPT + longest) <= agent_brain.MAX_PROMPT_TOKENS


def test_every_template_var_is_supplied():
    plan = build_call_plan(REQUEST, CANDIDATE, run_id="run-1")
    used = set(__import__("re").findall(r"\{\{(\w+)\}\}", plan.system_prompt + plan.first_message))
    assert used <= set(plan.dynamic_vars)


def test_first_message_states_the_dates():
    plan = build_call_plan(REQUEST, CANDIDATE, run_id="run-1")
    assert plan.first_message == (
        "Hi, I'm just calling to ask what's the cheapest room you have available from {{check_in}} to {{check_out}}?"
    )


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
    assert usage == {"backboard_in": 120, "backboard_out": 20, "backboard_extract": 140}


def test_fallback_extract_unknown(monkeypatch):
    monkeypatch.setattr(agent_brain, "_backboard_complete", lambda s, u, m: ('{"available": null}', 100, 5))
    assert fallback_extract(TRANSCRIPT, 3).available is None


def test_fallback_extract_without_key_returns_none():
    assert fallback_extract(TRANSCRIPT, 1) is None


def test_fallback_extract_bad_json(monkeypatch):
    monkeypatch.setattr(agent_brain, "_backboard_complete", lambda s, u, m: ("sorry, no idea", 100, 5))
    assert fallback_extract(TRANSCRIPT, 1) is None


def test_backboard_failed_status_is_an_error(monkeypatch):
    monkeypatch.setattr(config, "BACKBOARD_API_KEY", "test")
    request = httpx.Request("POST", "https://app.backboard.io/api/threads/messages")
    monkeypatch.setattr(
        agent_brain.httpx, "post",
        lambda *a, **k: httpx.Response(200, json={"status": "FAILED", "content": "Your free credit is reserved"}, request=request),
    )
    with pytest.raises(httpx.HTTPError):
        agent_brain._backboard_complete("sys", "user", 10)
    assert fallback_extract(TRANSCRIPT, 1) is None


def test_fallback_extract_respects_budget(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("over budget, should not call")
    monkeypatch.setattr(agent_brain, "_backboard_complete", boom)
    assert fallback_extract(TRANSCRIPT, 1, usage={"backboard_in": config.BACKBOARD_TOKEN_BUDGET - 100, "backboard_out": 0}) is None


# --- memory --------------------------------------------------------------------

from datetime import datetime  # noqa: E402

from contracts import Availability, CallResult, Run  # noqa: E402

IVR_TRANSCRIPT = (
    "Hotel: Thank you for calling Hotel Ocho. For reservations, press 1. For the front desk, press 2.\n"
    "Agent: (pressed 1)\n"
    "Hotel: Please hold while we connect you.\n"
    "Hotel: Reservations, how can I help?\n"
    "Agent: Do you have a room for November 2?\n"
    "Hotel: Yes, 179 a night."
)
CALL = CallResult(
    candidate_rank=2, conversation_id="conv-1", outcome="available", duration_s=74.4,
    availability=Availability(candidate_rank=2, available=True, quoted_rate=179.0, source="tool"),
    transcript=IVR_TRANSCRIPT,
)


def test_summarize_call():
    assert agent_brain.summarize_call(CANDIDATE, CALL, date(2026, 10, 17)) == (
        "[+14165550123] Hotel Ocho, 2026-10-17: available; quoted $179/night; "
        "menu: press 1 for reservations; put on hold; call 74s"
    )


@pytest.mark.parametrize("menu", [
    "Hotel: Press 2 for the front desk.",
    "Hotel: For the front desk, press two.",
])
def test_summarize_menu_either_order(menu):
    call = CALL.model_copy(update={"transcript": menu, "availability": None})
    assert "menu: press 2 for front desk" in agent_brain.summarize_call(CANDIDATE, call, date(2026, 10, 17))


def test_summarize_ignores_agent_lines():
    call = CALL.model_copy(update={"transcript": "Agent: please hold on, press 1 for reservations", "availability": None})
    assert agent_brain.summarize_call(CANDIDATE, call, date(2026, 10, 17)).endswith(": available; call 74s")


class FakeBackboard:
    def __init__(self, memories=()):
        self.posts = []
        self.memories = list(memories)

    def __call__(self, path, payload, timeout=15):
        self.posts.append((path, payload))
        if path == "/assistants":
            return {"assistant_id": "asst-1"}
        if path.endswith("/memories/search"):
            return {"memories": self.memories, "total_count": len(self.memories)}
        return {}


def test_remember_run_creates_assistant_once_and_stores_each_call(monkeypatch, capsys):
    fake = FakeBackboard()
    monkeypatch.setattr(agent_brain, "_backboard_post", fake)
    run = Run(
        run_id="run-1", prompt="p", started_at=datetime(2026, 10, 17, 10, 0),
        candidates=[CANDIDATE, CANDIDATE.model_copy(update={"rank": 1, "phone_e164": "+14165550100"})],
        calls=[CALL, CALL.model_copy(update={"candidate_rank": 1, "outcome": "voicemail", "transcript": None})],
    )
    assert agent_brain.remember_run(run) == 2
    assert agent_brain.remember_run(run) == 2
    paths = [p for p, _ in fake.posts]
    assert paths.count("/assistants") == 1
    assert paths.count("/assistants/asst-1/memories") == 4
    assert fake.posts[1][1]["metadata"] == {"phone": "+14165550123", "run_id": "run-1", "outcome": "available"}
    assert "BACKBOARD_ASSISTANT_ID=asst-1" in capsys.readouterr().out


def test_recall_filters_to_this_phone_newest_first(monkeypatch):
    monkeypatch.setattr(config, "BACKBOARD_ASSISTANT_ID", "asst-1")
    monkeypatch.setattr(agent_brain, "_backboard_post", FakeBackboard([
        {"content": "[+14165550123] Hotel Ocho, 2026-10-10: unavailable; call 40s", "created_at": "2026-10-10T10:00:00"},
        {"content": "[+19995550000] Other Hotel, 2026-10-16: available; call 30s", "created_at": "2026-10-16T10:00:00"},
        {"content": "[+14165550123] Hotel Ocho, 2026-10-15: available; menu: press 1 for reservations; call 60s", "created_at": "2026-10-15T10:00:00"},
    ]))
    assert agent_brain.recall_hotel_notes(CANDIDATE) == (
        "available; menu: press 1 for reservations; call 60s | unavailable; call 40s"
    )


def test_recall_truncates(monkeypatch):
    monkeypatch.setattr(config, "BACKBOARD_ASSISTANT_ID", "asst-1")
    long = {"content": "[+14165550123] Hotel Ocho, 2026-10-15: " + "y" * 500, "created_at": "2026-10-15"}
    monkeypatch.setattr(agent_brain, "_backboard_post", FakeBackboard([long]))
    assert len(agent_brain.recall_hotel_notes(CANDIDATE)) == agent_brain.HOTEL_NOTES_MAX_CHARS


def test_memory_without_key_is_a_noop():
    run = Run(run_id="r", prompt="p", started_at=datetime(2026, 10, 17), candidates=[CANDIDATE], calls=[CALL])
    assert agent_brain.remember_run(run) == 0
    assert agent_brain.recall_hotel_notes(CANDIDATE) == ""


# --- agent LLM tokens ----------------------------------------------------------

def _usage(inp, out, cache_read=0):
    return {"model_usage": {"gemini-2.5-flash": {
        "input": {"tokens": inp, "price": 0.0},
        "input_cache_read": {"tokens": cache_read, "price": 0.0},
        "output_total": {"tokens": out, "price": 0.0},
    }}}


def test_agent_llm_tokens_from_charging():
    conv = {"metadata": {"charging": {"llm_usage": {
        "irreversible_generation": _usage(900, 120, cache_read=300),
        "initiated_generation": _usage(50, 10),
    }}}}
    assert agent_brain.agent_llm_tokens(conv) == 900 + 120 + 300 + 50 + 10


def test_agent_llm_tokens_falls_back_to_transcript():
    conv = {"metadata": {}, "transcript": [
        {"role": "agent", "message": "Hi", "llm_usage": _usage(400, 15)},
        {"role": "user", "message": "Hello", "llm_usage": None},
        {"role": "agent", "message": "Thanks", "llm_usage": _usage(450, 10)},
    ]}
    assert agent_brain.agent_llm_tokens(conv) == 875


def test_agent_llm_tokens_missing_everything():
    assert agent_brain.agent_llm_tokens({}) == 0


def test_add_agent_llm_tokens_sums_across_calls():
    usage = {"backboard_in": 0, "backboard_out": 0, "agent_llm": 0}
    for conv in ({"transcript": [{"llm_usage": _usage(100, 10)}]}, {"transcript": [{"llm_usage": _usage(200, 20)}]}):
        agent_brain.add_agent_llm_tokens(conv, usage)
    assert usage["agent_llm"] == 330
