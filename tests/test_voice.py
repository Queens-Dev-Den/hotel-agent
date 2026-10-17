"""WS3: voice_agent against a fake ElevenLabs API (no real calls)."""

import json

import httpx
import pytest

import config
import tool_server
import voice_agent
from contracts import ToolAvailabilityRequest
from test_voice_contract import PLAN, assert_valid_call_result

CONV_ID = "conv_test"


def _conversation(status="done", turns=(), tool_calls=(), duration=42, reason=None):
    transcript = [{"role": role, "message": msg, "time_in_call_secs": i} for i, (role, msg) in enumerate(turns)]
    if tool_calls:
        transcript.append({"role": "agent", "message": "", "tool_calls": [{"tool_name": n} for n in tool_calls]})
    return {
        "conversation_id": CONV_ID,
        "status": status,
        "transcript": transcript,
        "metadata": {"call_duration_secs": duration, "termination_reason": reason},
    }


@pytest.fixture
def fake_api(monkeypatch):
    """Serve the outbound-call POST, then each queued conversation in turn on GET."""
    state = {"outbound": {"success": True, "message": "ok", "conversation_id": CONV_ID}, "gets": [], "posts": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            state["posts"].append(json.loads(request.content))
            return httpx.Response(200, json=state["outbound"])
        convo = state["gets"].pop(0) if len(state["gets"]) > 1 else state["gets"][0]
        return httpx.Response(200, json=convo)

    def client():
        return httpx.Client(base_url="https://fake.elevenlabs", transport=httpx.MockTransport(handler))

    monkeypatch.setattr(voice_agent, "_client", client)
    monkeypatch.setattr(config, "VOICE_POLL_INTERVAL_S", 0)
    tool_server.reset()
    yield state
    tool_server.reset()


HOTEL_YES = [("agent", "Hi, what's your cheapest room?"), ("user", "Yes, we have a room at $189 a night.")]
HOTEL_NO = [("agent", "Hi, what's your cheapest room?"), ("user", "Sorry, we're sold out those nights.")]


def test_sends_plan_overrides_and_variables(fake_api):
    fake_api["gets"] = [_conversation(turns=HOTEL_YES)]
    voice_agent.place_call(PLAN)
    body = fake_api["posts"][0]
    assert body["to_number"] == PLAN.candidate.phone_e164
    client_data = body["conversation_initiation_client_data"]
    assert client_data["dynamic_variables"] == PLAN.dynamic_vars
    agent = client_data["conversation_config_override"]["agent"]
    assert agent["prompt"]["prompt"] == PLAN.system_prompt
    assert agent["first_message"] == PLAN.first_message


def test_polls_until_done_then_available(fake_api):
    fake_api["gets"] = [_conversation(status="in-progress"), _conversation(status="processing"),
                        _conversation(turns=HOTEL_YES)]
    result = voice_agent.place_call(PLAN)
    assert_valid_call_result(PLAN, result)
    assert result.outcome == "available"
    assert result.availability.quoted_rate == 189.0
    assert result.duration_s == 42
    assert result.transcript.splitlines() == ["Agent: Hi, what's your cheapest room?",
                                              "Hotel: Yes, we have a room at $189 a night."]


def test_tool_result_wins_over_transcript(fake_api):
    tool_server.record(ToolAvailabilityRequest(run_id=PLAN.run_id, candidate_rank=PLAN.candidate.rank,
                                               available=False, notes="sold out"))
    fake_api["gets"] = [_conversation(turns=HOTEL_YES)]
    result = voice_agent.place_call(PLAN)
    assert_valid_call_result(PLAN, result)
    assert result.outcome == "unavailable"
    assert result.availability.source == "tool"


def test_sold_out(fake_api):
    fake_api["gets"] = [_conversation(turns=HOTEL_NO)]
    result = voice_agent.place_call(PLAN)
    assert_valid_call_result(PLAN, result)
    assert result.outcome == "unavailable"
    assert result.availability.available is False


def test_unclear_answer_is_unavailable_with_unknown_availability(fake_api):
    fake_api["gets"] = [_conversation(turns=[("agent", "Hi"), ("user", "Let me check, one moment.")])]
    result = voice_agent.place_call(PLAN)
    assert_valid_call_result(PLAN, result)
    assert result.outcome == "unavailable"
    assert result.availability is None


def test_voicemail(fake_api):
    fake_api["gets"] = [_conversation(turns=[("user", "Please leave a message after the tone.")],
                                      tool_calls=["voicemail_detection"])]
    result = voice_agent.place_call(PLAN)
    assert_valid_call_result(PLAN, result)
    assert result.outcome == "voicemail"


def test_no_answer(fake_api):
    fake_api["gets"] = [_conversation(status="failed", turns=[("agent", "Hi, what's your cheapest room?")])]
    result = voice_agent.place_call(PLAN)
    assert_valid_call_result(PLAN, result)
    assert result.outcome == "no_answer"


def test_retries_until_transcript_arrives(fake_api):
    fake_api["gets"] = [_conversation(turns=()), _conversation(turns=()), _conversation(turns=HOTEL_YES)]
    result = voice_agent.place_call(PLAN)
    assert result.outcome == "available"


def test_still_running_at_deadline_is_error(fake_api, monkeypatch):
    monkeypatch.setattr(config, "MAX_CALL_DURATION_S", 0)
    monkeypatch.setattr(config, "TRANSCRIPT_WAIT_S", 0)
    fake_api["gets"] = [_conversation(status="in-progress", turns=HOTEL_YES)]
    result = voice_agent.place_call(PLAN)
    assert_valid_call_result(PLAN, result)
    assert result.outcome == "error"


def test_failed_start_raises(fake_api):
    fake_api["outbound"] = {"success": False, "message": "not authorized to call"}
    with pytest.raises(RuntimeError, match="not authorized"):
        voice_agent.place_call(PLAN)


def test_multiline_message_stays_on_one_line():
    convo = _conversation(turns=[("agent", "Hi"), ("user", "Yes.\nWe have one room.")])
    assert voice_agent.format_transcript(convo) == "Agent: Hi\nHotel: Yes. We have one room."


def test_conversation_details(fake_api):
    fake_api["gets"] = [_conversation(turns=HOTEL_YES)]
    assert voice_agent.conversation_details(CONV_ID)["conversation_id"] == CONV_ID
