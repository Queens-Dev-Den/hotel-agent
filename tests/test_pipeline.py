import time

import pytest

import agent_brain
import config
import pipeline
import telemetry


ALL_MOCKED = {"brain", "search", "voice", "email"}


@pytest.fixture(autouse=True)
def mocked(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MOCK_MODULES", set(ALL_MOCKED))
    monkeypatch.setattr(telemetry, "RUN_FILE", tmp_path / "run_telemetry.json")


def test_full_mocked_run():
    r = pipeline.run("cheapest hotel in Toronto tonight", callback_email="me@example.com")

    assert [c.rank for c in r.candidates] == [1, 2, 3]
    assert [c.outcome for c in r.calls] == ["available"] * 3
    assert [a.available for a in r.availability] == [True] * 3
    assert r.email is not None
    assert set(r.telemetry.stage_ms) == {"brain", "search", "recall", "call", "email"}


def test_calls_run_in_parallel(monkeypatch):
    real = pipeline._mock_place_call

    def slow(plan):
        time.sleep(0.3)
        return real(plan)

    monkeypatch.setattr(pipeline, "_mock_place_call", slow)
    r = pipeline.run("x")

    assert r.telemetry.stage_ms["call"] < 600  # 3 x 0.3 s would be 900 ms in series


def test_failed_call_is_recorded_and_others_continue(monkeypatch):
    real = pipeline._mock_place_call

    def flaky(plan):
        if plan.candidate.rank == 2:
            raise ConnectionError("boom")
        return real(plan)

    monkeypatch.setattr(pipeline, "_mock_place_call", flaky)
    r = pipeline.run("x")

    assert [c.outcome for c in r.calls] == ["available", "error", "available"]
    assert r.availability[1].available is None
    assert r.email is not None


def test_hung_call_hits_cap(monkeypatch):
    real = pipeline._mock_place_call

    def hang(plan):
        if plan.candidate.rank == 1:
            time.sleep(2)
        return real(plan)

    monkeypatch.setattr(pipeline, "_mock_place_call", hang)
    monkeypatch.setattr(config, "MAX_CALL_DURATION_S", 0)
    monkeypatch.setattr(pipeline, "JOIN_GRACE_S", 0.3)
    started = time.monotonic()
    r = pipeline.run("x")

    assert time.monotonic() - started < 1.5
    assert [c.outcome for c in r.calls] == ["error", "available", "available"]


def test_mock_hotel_swaps_dialed_numbers_only(monkeypatch):
    monkeypatch.setattr(config, "MOCK_MODULES", ALL_MOCKED - {"voice"})
    monkeypatch.setattr(config, "USE_MOCK_HOTEL", True)
    monkeypatch.setattr(config, "MOCK_HOTEL_PHONES", ["+14165559991", "+14165559992", "+14165559993"])
    req = pipeline._mock_parse_prompt("x")
    plans = [agent_brain.build_call_plan(req, c, "run1") for c in pipeline._mock_find_candidates(req)]

    dialed = pipeline._dial_targets(plans)

    assert [p.candidate.phone_e164 for p in dialed] == config.MOCK_HOTEL_PHONES
    assert [p.candidate.phone_e164 for p in plans] == ["+14165550101", "+14165550102", "+14165550103"]


def test_mock_hotel_without_numbers_refuses_to_dial(monkeypatch):
    monkeypatch.setattr(config, "MOCK_MODULES", ALL_MOCKED - {"voice"})
    monkeypatch.setattr(config, "USE_MOCK_HOTEL", True)
    monkeypatch.setattr(config, "MOCK_HOTEL_PHONES", [])
    req = pipeline._mock_parse_prompt("x")
    plans = [agent_brain.build_call_plan(req, c, "run1") for c in pipeline._mock_find_candidates(req)]

    with pytest.raises(RuntimeError, match="Refusing to dial real hotels"):
        pipeline._dial_targets(plans)


def test_progress_events_in_order(monkeypatch):
    real = pipeline._mock_place_call

    def staggered(plan):  # hotel 3 finishes first
        time.sleep(0.05 * (4 - plan.candidate.rank))
        return real(plan)

    monkeypatch.setattr(pipeline, "_mock_place_call", staggered)
    events = []
    pipeline.run("x", on_event=lambda kind, data: events.append((kind, data)))

    assert any(k == "tokens" for k, _ in events)
    kinds = [k for k, _ in events if k != "tokens"]
    assert kinds[:4] == ["stage_start", "stage_done", "request", "stage_start"]
    assert kinds[-3:] == ["stage_start", "stage_done", "done"]
    assert [d["call"].candidate_rank for k, d in events if k == "call_done"] == [3, 2, 1]


def test_memory_recalled_in_parallel_and_saved_after_email(monkeypatch):
    monkeypatch.setattr(config, "MOCK_MODULES", ALL_MOCKED - {"brain"})
    monkeypatch.setattr(pipeline, "_real_calls", lambda: True)
    monkeypatch.setattr(pipeline, "_parse_prompt", lambda: pipeline._mock_parse_prompt)
    order = []

    def recall(candidate):
        time.sleep(0.2)
        return f"notes for {candidate.rank}"

    real_build = pipeline.build_call_plan
    monkeypatch.setattr(pipeline, "recall_hotel_notes", recall)
    monkeypatch.setattr(pipeline, "build_call_plan",
                        lambda req, c, run_id, hotel_notes="": order.append(hotel_notes) or real_build(req, c, run_id, hotel_notes))
    monkeypatch.setattr(pipeline, "remember_run", lambda run: order.append("remember" if run.email else "too early"))

    r = pipeline.run("x")

    assert r.telemetry.stage_ms["recall"] < 500  # 3 x 0.2 s would be 600 ms in series
    assert order == ["notes for 1", "notes for 2", "notes for 3", "remember"]


@pytest.mark.parametrize("mocked,use_mock_hotel", [
    ({"voice"}, False),   # fake calls: fake transcripts
    (set(), True),        # real calls, but to the mock hotel numbers
])
def test_memory_never_saved_from_fake_or_mock_hotel_calls(monkeypatch, mocked, use_mock_hotel):
    monkeypatch.setattr(config, "MOCK_MODULES", mocked)
    monkeypatch.setattr(config, "USE_MOCK_HOTEL", use_mock_hotel)
    assert pipeline._real_calls() is False


def test_serpapi_key_not_logged_by_httpx():
    import logging

    import search_service  # noqa: F401  (sets the httpx logger level on import)
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)


def test_fake_call_quotes_online_nightly_price():
    from datetime import date

    from agent_brain import build_call_plan
    from contracts import HotelCandidate, HotelRequest

    request = HotelRequest(city="Toronto", check_in=date(2026, 11, 2), check_out=date(2026, 11, 4))
    candidate = HotelCandidate(rank=1, name="H", address="1 King St", phone_e164="+14165550101",
                               total_price=190.0, source="serpapi_hotel")
    result = pipeline._mock_place_call(build_call_plan(request, candidate, "r1"))
    assert result.availability.quoted_rate == 95.0  # $190 total / 2 nights
    assert "$95 a night" in result.transcript


def test_agent_tokens_counted_live_per_call(monkeypatch):
    import sys
    import types

    monkeypatch.setattr(config, "MOCK_MODULES", ALL_MOCKED - {"voice"})
    monkeypatch.setattr(config, "USE_MOCK_HOTEL", False)
    monkeypatch.setattr(pipeline.tool_server, "start_in_background", lambda port: port)
    fake_voice = types.SimpleNamespace(
        place_call=pipeline._mock_place_call,
        conversation_details=lambda cid: {"id": cid},
    )
    monkeypatch.setitem(sys.modules, "voice_agent", fake_voice)
    monkeypatch.setattr(pipeline, "add_agent_llm_tokens",
                        lambda conv, usage, rank=None: usage.__setitem__("agent_llm", usage.get("agent_llm", 0) + 100))
    events = []

    r = pipeline.run("x", on_event=lambda kind, data: events.append((kind, data)))

    assert r.telemetry.tokens["agent_llm"] == 300
    live = [d["tokens"]["agent_llm"] for k, d in events if k == "tokens" and "agent_llm" in d["tokens"]]
    assert [100, 200, 300] == [t for t in live if t][:3]  # grows as each call ends


def _real_voice(monkeypatch, numbers):
    import sys
    import types

    monkeypatch.setattr(config, "MOCK_MODULES", ALL_MOCKED - {"voice"})
    monkeypatch.setattr(config, "USE_MOCK_HOTEL", True)
    monkeypatch.setattr(config, "MOCK_HOTEL_PHONES", numbers)
    monkeypatch.setattr(pipeline.tool_server, "start_in_background", lambda port: port)
    dialed = []

    def place_call(plan):
        dialed.append(plan.candidate.phone_e164)
        return pipeline._mock_place_call(plan)

    monkeypatch.setitem(sys.modules, "voice_agent", types.SimpleNamespace(
        place_call=place_call, conversation_details=lambda cid: {}))
    return dialed


def test_one_test_number_calls_only_the_cheapest_hotel(monkeypatch):
    dialed = _real_voice(monkeypatch, ["+16135550000"])
    events = []

    r = pipeline.run("x", on_event=lambda kind, data: events.append((kind, data)))

    assert dialed == ["+16135550000"]
    assert [c.rank for c in r.candidates] == [1]  # email and UI only list the hotel we called
    assert r.candidates[0].phone_e164 == "+14165550101"  # real hotel details kept
    assert [d["numbers"] for k, d in events if k == "dialing"] == [{1: "+16135550000"}]


def test_test_mode_without_numbers_stops_before_calling(monkeypatch):
    dialed = _real_voice(monkeypatch, [])
    with pytest.raises(RuntimeError, match="Refusing to dial real hotels"):
        pipeline.run("x")
    assert dialed == []


def test_outcome_follows_the_resolved_answer():
    from datetime import datetime, timezone

    from contracts import Availability, CallResult, Run

    r = Run(run_id="r", prompt="p", started_at=datetime.now(timezone.utc),
            calls=[CallResult(candidate_rank=1, conversation_id="c1", outcome="unavailable", duration_s=30),
                   CallResult(candidate_rank=2, conversation_id="c2", outcome="voicemail", duration_s=10),
                   CallResult(candidate_rank=3, conversation_id="c3", outcome="unavailable", duration_s=20)],
            availability=[Availability(candidate_rank=1, available=True, quoted_rate=50.0, source="backboard"),
                          Availability(candidate_rank=2, available=True, source="backboard"),
                          Availability(candidate_rank=3, available=None, source="transcript")])
    pipeline._sync_outcomes(r)
    assert [c.outcome for c in r.calls] == ["available", "voicemail", "unavailable"]


# --- retry round: call 3 more only if nobody in round 1 confirmed a room with a rate ---

from contracts import Availability, CallResult  # noqa: E402


def _answer(available, rate=None):
    """A fake call where the hotel says `available` (True/False/None) at `rate`."""
    def place(plan):
        rank = plan.candidate.rank
        return CallResult(
            candidate_rank=rank, conversation_id=f"mock-{plan.run_id}-{rank}", duration_s=1.0,
            outcome="available" if available else "unavailable",
            availability=Availability(candidate_rank=rank, available=available, quoted_rate=rate, source="tool"),
            transcript="Agent: Hi.\nHotel: ...",
        )
    return place


def _by_rank(answers):
    """answers: {rank: place_fn}."""
    return lambda plan: answers[plan.candidate.rank](plan)


def test_retry_calls_three_new_hotels_when_round_one_finds_nothing(monkeypatch):
    sold_out = _answer(False)
    monkeypatch.setattr(pipeline, "_mock_place_call",
                        _by_rank({1: sold_out, 2: sold_out, 3: sold_out, 4: sold_out, 5: _answer(True, 120.0), 6: sold_out}))
    events = []
    r = pipeline.run("x", on_event=lambda kind, data: events.append(kind))

    assert [c.rank for c in r.candidates] == [1, 2, 3, 4, 5, 6]
    assert [c.name for c in r.candidates[3:]] == ["Mock Hotel 4", "Mock Hotel 5", "Mock Hotel 6"]  # not re-called
    assert [c.candidate_rank for c in r.calls] == [1, 2, 3, 4, 5, 6]
    assert [a.candidate_rank for a in r.availability] == [1, 2, 3, 4, 5, 6]
    assert r.availability[4].available is True and r.availability[4].quoted_rate == 120.0
    assert "retry" in r.telemetry.stage_ms
    assert r.email is not None and events.count("done") == 1


def test_no_retry_when_one_hotel_confirms_a_rate(monkeypatch):
    sold_out = _answer(False)
    monkeypatch.setattr(pipeline, "_mock_place_call",
                        _by_rank({1: sold_out, 2: _answer(True, 99.0), 3: sold_out}))
    r = pipeline.run("x")

    assert [c.rank for c in r.candidates] == [1, 2, 3]
    assert "retry" not in r.telemetry.stage_ms


def test_yes_without_a_rate_still_retries(monkeypatch):
    monkeypatch.setattr(pipeline, "_mock_place_call",
                        _by_rank({1: _answer(True), 2: _answer(None), 3: _answer(False),
                                  4: _answer(False), 5: _answer(False), 6: _answer(False)}))
    r = pipeline.run("x")
    assert len(r.calls) == 6


def test_retry_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(config, "RETRY_IF_NO_ROOM", False)
    monkeypatch.setattr(pipeline, "_mock_place_call", _answer(False))
    assert len(pipeline.run("x").calls) == 3


def test_retry_with_no_new_hotels_still_emails(monkeypatch):
    monkeypatch.setattr(pipeline, "_mock_place_call", _answer(False))
    real = pipeline._mock_find_candidates

    def nothing_new(request, *, stats=None, exclude=None):
        if exclude:
            raise RuntimeError("No callable listings")
        return real(request)

    monkeypatch.setattr(pipeline, "_mock_find_candidates", nothing_new)
    r = pipeline.run("x")
    assert len(r.calls) == 3 and r.email is not None and "retry" in r.telemetry.stage_ms


def test_retry_does_not_rerun_round_one_fallbacks(monkeypatch):
    unclear = _answer(None)
    monkeypatch.setattr(pipeline, "_mock_place_call", unclear)
    seen = []
    monkeypatch.setattr(pipeline, "fallback_extract", lambda t, rank, usage=None: seen.append(rank))
    pipeline.run("x")
    assert sorted(seen) == [1, 2, 3, 4, 5, 6]  # each hotel's fallback once, not round 1 twice
