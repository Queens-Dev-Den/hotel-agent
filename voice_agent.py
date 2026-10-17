"""WS3: ElevenLabs outbound call lifecycle. See docs/voice-handoff.md.

place_call(plan) dials one hotel, blocks until the call has ended and the transcript
is fetched, and returns a CallResult. pipeline.py runs three of these at once, so
nothing here is shared between calls except read-only config.
"""
from __future__ import annotations

import logging
import time

import httpx

import config
import extractor
from contracts import Availability, CallPlan, CallResult

log = logging.getLogger(__name__)

OUTBOUND_PATH = "/v1/convai/twilio/outbound-call"
CONVERSATION_PATH = "/v1/convai/conversations/{id}"
FINAL_STATUSES = {"done", "failed"}
SPEAKERS = {"agent": "Agent", "user": "Hotel"}


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=config.ELEVENLABS_BASE_URL,
        headers={"xi-api-key": config.ELEVENLABS_API_KEY},
        timeout=config.ELEVENLABS_HTTP_TIMEOUT_S,
    )


def place_call(plan: CallPlan) -> CallResult:
    """Dial plan.candidate, wait for the call to end, return outcome + transcript.

    Raises if the call can't be started; the pipeline records that as outcome="error".
    """
    started = time.monotonic()
    with _client() as client:
        conversation_id = _start_call(client, plan)
        log.info("rank %d: call started %s", plan.candidate.rank, conversation_id)
        conversation = _wait_for_end(client, conversation_id)
    return _to_result(plan, conversation_id, conversation, time.monotonic() - started)


def conversation_details(conversation_id: str) -> dict:
    """Raw ElevenLabs conversation (transcript, metadata, LLM usage) for token counting."""
    with _client() as client:
        return _get_conversation(client, conversation_id)


# --- ElevenLabs requests --------------------------------------------------------

LISTEN_FIRST_LINE = (
    '\n- Wait for them to speak first. Once a person greets you, start with: "{opener}" '
    "If it's a phone menu, follow it first and say this to the person who answers."
)


def _agent_override(plan: CallPlan) -> dict:
    """Prompt + first message for this call.

    With VOICE_LISTEN_FIRST the agent stays silent on pickup (hotels and phone menus
    always speak first) and says the plan's opener as its first reply instead.
    """
    if not (config.VOICE_LISTEN_FIRST and plan.first_message):
        return {"prompt": {"prompt": plan.system_prompt}, "first_message": plan.first_message}
    prompt = plan.system_prompt + LISTEN_FIRST_LINE.format(opener=plan.first_message)
    return {"prompt": {"prompt": prompt}, "first_message": ""}


def _start_call(client: httpx.Client, plan: CallPlan) -> str:
    body = {
        "agent_id": config.ELEVENLABS_AGENT_ID,
        "agent_phone_number_id": config.ELEVENLABS_AGENT_PHONE_NUMBER_ID,
        "to_number": plan.candidate.phone_e164,
        "conversation_initiation_client_data": {
            "dynamic_variables": plan.dynamic_vars,
            "conversation_config_override": {"agent": _agent_override(plan)},
        },
    }
    resp = client.post(OUTBOUND_PATH, json=body)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success") or not data.get("conversation_id"):
        raise RuntimeError(f"outbound call to rank {plan.candidate.rank} failed: {data.get('message')}")
    return data["conversation_id"]


def _get_conversation(client: httpx.Client, conversation_id: str) -> dict:
    resp = client.get(CONVERSATION_PATH.format(id=conversation_id))
    resp.raise_for_status()
    return resp.json()


def _wait_for_end(client: httpx.Client, conversation_id: str) -> dict:
    """Poll until the call is over, then retry briefly while the transcript catches up."""
    deadline = time.monotonic() + config.MAX_CALL_DURATION_S + config.TRANSCRIPT_WAIT_S
    conversation = _get_conversation(client, conversation_id)
    while conversation.get("status") not in FINAL_STATUSES and time.monotonic() < deadline:
        time.sleep(config.VOICE_POLL_INTERVAL_S)
        conversation = _get_conversation(client, conversation_id)

    for _ in range(config.TRANSCRIPT_RETRIES):
        if _turns(conversation) or conversation.get("status") != "done":
            break
        time.sleep(config.VOICE_POLL_INTERVAL_S)
        conversation = _get_conversation(client, conversation_id)
    return conversation


# --- Conversation -> CallResult -------------------------------------------------

def _turns(conversation: dict) -> list[dict]:
    return [t for t in conversation.get("transcript") or [] if (t.get("message") or "").strip()]


def format_transcript(conversation: dict) -> str | None:
    """One line per turn: 'Agent: ...' for us, 'Hotel: ...' for the other side."""
    lines = []
    for turn in _turns(conversation):
        speaker = SPEAKERS.get(turn.get("role"), "Hotel")
        text = " ".join(turn["message"].split())
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines) or None


def _hit_voicemail(conversation: dict) -> bool:
    reason = str((conversation.get("metadata") or {}).get("termination_reason") or "")
    if "voicemail" in reason.lower():
        return True
    for turn in conversation.get("transcript") or []:
        for call in turn.get("tool_calls") or []:
            if "voicemail" in str(call.get("tool_name") or call.get("name") or "").lower():
                return True
    return False


def _hotel_spoke(conversation: dict) -> bool:
    return any(t.get("role") == "user" for t in _turns(conversation))


def _tool_result(plan: CallPlan) -> Availability | None:
    """What record_availability stored for this call, if the tool server is running here."""
    try:
        import tool_server
    except ImportError:
        return None
    return tool_server.results_for(plan.run_id).get(plan.candidate.rank)


def _to_result(plan: CallPlan, conversation_id: str, conversation: dict, elapsed_s: float) -> CallResult:
    rank = plan.candidate.rank
    transcript = format_transcript(conversation)
    duration = float((conversation.get("metadata") or {}).get("call_duration_secs") or elapsed_s)
    availability: Availability | None = None

    if conversation.get("status") not in FINAL_STATUSES:
        outcome = "error"  # still running at our deadline
    elif _hit_voicemail(conversation):
        outcome = "voicemail"
    elif not _hotel_spoke(conversation):
        outcome = "no_answer"
    else:
        availability = _tool_result(plan)
        if availability is None or availability.available is None:
            availability = extractor.from_transcript(transcript, rank)
        # A person answered: anything short of a clear "yes" is reported as unavailable,
        # with availability=None so the email shows it as unknown rather than sold out.
        outcome = "available" if availability.available else "unavailable"
        if availability.available is None:
            availability = None

    log.info("rank %d: %s in %.0fs", rank, outcome, duration)
    return CallResult(
        candidate_rank=rank,
        conversation_id=conversation_id,
        outcome=outcome,
        duration_s=duration,
        availability=availability,
        transcript=transcript,
    )
