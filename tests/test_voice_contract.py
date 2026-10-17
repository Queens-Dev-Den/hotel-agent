"""Contract for WS3 voice_agent.place_call(plan) -> CallResult. See docs/voice-handoff.md.

Offline: checks the signature once voice_agent.py exists (skipped until then),
and checks the validator itself against the pipeline's fake call.

Live (opt-in, dials MOCK_HOTEL_PHONES[0] only, never a real hotel):
    VOICE_LIVE_TEST=1 python -m pytest -q tests/test_voice_contract.py
"""

import inspect
import os
import re
from datetime import date

import pytest

import config
import pipeline
from agent_brain import build_call_plan
from contracts import CallPlan, CallResult, HotelCandidate, HotelRequest

OUTCOMES = {"available", "unavailable", "voicemail", "no_answer", "error"}
SPEAKER_RE = re.compile(r"^(Agent|Hotel): ")


def assert_valid_call_result(plan: CallPlan, result: CallResult) -> None:
    """Everything the pipeline, tool server and email rely on. Use it in your own tests too."""
    assert isinstance(result, CallResult), f"place_call must return contracts.CallResult, got {type(result)}"
    rank = plan.candidate.rank
    assert result.candidate_rank == rank, "candidate_rank must be plan.candidate.rank"
    assert result.outcome in OUTCOMES, f"outcome {result.outcome!r} not in {sorted(OUTCOMES)}"
    assert result.duration_s >= 0
    if result.outcome != "error":
        assert result.conversation_id, "conversation_id is needed to fetch transcripts and token usage"
    if result.outcome in ("available", "unavailable"):
        assert result.transcript, "a call that reached a person must return its transcript"
    if result.transcript:
        lines = [line for line in result.transcript.splitlines() if line.strip()]
        bad = [line for line in lines if not SPEAKER_RE.match(line)]
        assert not bad, f"every transcript line must start with 'Agent: ' or 'Hotel: ', got {bad[:2]}"
    if result.availability is not None:
        assert result.availability.candidate_rank == rank
        if result.outcome == "available":
            assert result.availability.available is not False
        if result.outcome == "unavailable":
            assert result.availability.available is not True


PLAN = build_call_plan(
    HotelRequest(city="Toronto", check_in=date(2026, 11, 2), check_out=date(2026, 11, 3), guests=2),
    HotelCandidate(rank=2, name="Mock Hotel", address="1 King St W, Toronto", phone_e164="+14165550102",
                   total_price=99.0, source="mock"),
    run_id="contract-test",
)


def test_validator_accepts_pipeline_fake():
    assert_valid_call_result(PLAN, pipeline._mock_place_call(PLAN))


@pytest.mark.parametrize("update, message", [
    ({"candidate_rank": 3}, "candidate_rank"),
    ({"outcome": "booked"}, "outcome"),
    ({"transcript": "agent: hi\nuser: hello"}, "Agent: "),
    ({"transcript": None}, "transcript"),
    ({"conversation_id": ""}, "conversation_id"),
])
def test_validator_rejects(update, message):
    bad = pipeline._mock_place_call(PLAN).model_copy(update=update)
    with pytest.raises(AssertionError, match=message):
        assert_valid_call_result(PLAN, bad)


def test_place_call_signature():
    voice_agent = pytest.importorskip("voice_agent", reason="voice_agent.py not on main yet (WS3)")
    assert callable(getattr(voice_agent, "place_call", None)), "voice_agent.place_call is missing"
    params = list(inspect.signature(voice_agent.place_call).parameters.values())
    required = [p for p in params if p.default is p.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    assert len(required) == 1, "place_call must take exactly one required argument: the CallPlan"


@pytest.mark.skipif(os.getenv("VOICE_LIVE_TEST") != "1", reason="set VOICE_LIVE_TEST=1 to place a real call")
def test_live_call_to_mock_hotel():
    voice_agent = pytest.importorskip("voice_agent")
    assert config.MOCK_HOTEL_PHONES, "set MOCK_HOTEL_PHONES in .env (the live test only dials the mock hotel)"
    plan = PLAN.model_copy(update={
        "candidate": PLAN.candidate.model_copy(update={"phone_e164": config.MOCK_HOTEL_PHONES[0]}),
    })
    assert_valid_call_result(plan, voice_agent.place_call(plan))
