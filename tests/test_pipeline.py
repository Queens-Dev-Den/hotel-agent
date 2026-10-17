import time

import pytest

import agent_brain
import config
import pipeline
import telemetry
from contracts import Availability, CallResult

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
    assert set(r.telemetry.stage_ms) == {"brain", "search", "call", "email"}


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


def test_availability_falls_back_to_transcript_then_backboard(monkeypatch):
    r = pipeline.Run(run_id="r", prompt="x", started_at=pipeline.datetime.now(pipeline.timezone.utc))
    call = CallResult(candidate_rank=1, conversation_id="c", outcome="unavailable", duration_s=30,
                      transcript="Agent: Any rooms?\nHotel: Hmm, let me think.")
    tool = Availability(candidate_rank=1, available=False, source="tool")
    backboard = Availability(candidate_rank=1, available=False, source="backboard")
    monkeypatch.setattr(agent_brain, "fallback_extract", lambda *a, **k: backboard)

    assert pipeline._availability(r, call, {1: tool}) is tool
    assert pipeline._availability(r, call, {}) is backboard  # regex can't tell, so Backboard
    no_transcript = call.model_copy(update={"transcript": None})
    assert pipeline._availability(r, no_transcript, {}).available is None
