"""WS3: ElevenLabs outbound call lifecycle. See docs/voice-handoff.md.

place_call(plan) dials one hotel, blocks until the call has ended and the transcript
is fetched, and returns a CallResult. pipeline.py runs three of these at once, so
nothing here is shared between calls except read-only config.
"""
from __future__ import annotations

import json
import logging
import time

import httpx

import config
import extractor
from contracts import Availability, CallPlan, CallResult, ToolAvailabilityRequest

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


# From live tests with real hotels: receptionists hang up on stiff AI-sounding callers,
# "one moment" got an "Are you still there?" after 10 s, brand names read as "wrong
# number", half sentences and misheard greetings ("hi" -> "bye") made the agent ask to
# repeat or hang up, and a PBX "enter your access code" got a spoken explanation.
CALL_RULES = (
    "\n- Sound like a relaxed, friendly person: short casual sentences with contractions. Never write bracketed tags like [slow]."
    '\n- If they say "one moment", "let me check" or put you on hold, stay silent until they speak again, however long it takes. '
    'Never ask "are you still there?" while they are checking.'
    "\n- If they answer with a different or brand name, or a central reservations line, carry on: ask about {{hotel_name}}."
    "\n- If speech is cut off or unclear, let them finish. Ask them to repeat at most once. If a menu is unclear, press 0."
    "\n- If a system asks for a code, extension or PIN, press 0; if nothing changes, end the call."
    '\n- If asked if you are a robot or AI, answer honestly in one friendly line: "Yes, I\'m an automated assistant '
    "checking prices for a traveller, it's just one quick question.\" Then ask again."
    "\n- If they don't want to talk to an AI, thank them and end the call politely."
    '\n- A single "bye" at the very start is probably misheard, so ask your question once.'
)


def _agent_override(plan: CallPlan) -> dict:
    """Prompt + first message for this call.

    CALL_RULES are always appended. With VOICE_LISTEN_FIRST the agent stays silent on
    pickup (hotels and phone menus always speak first) and says the plan's opener as
    its first reply instead.
    """
    prompt = plan.system_prompt + CALL_RULES
    if not (config.VOICE_LISTEN_FIRST and plan.first_message):
        return {"prompt": {"prompt": prompt}, "first_message": plan.first_message}
    prompt += LISTEN_FIRST_LINE.format(opener=plan.first_message)
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


def _tool_call_answer(plan: CallPlan, conversation: dict) -> Availability | None:
    """The agent's last record_availability call, read from the conversation record itself.
    Works even when the tool's HTTP request went to someone else's tunnel."""
    for turn in reversed(conversation.get("transcript") or []):
        for call in reversed(turn.get("tool_calls") or []):
            if call.get("tool_name") != "record_availability":
                continue
            try:
                params = json.loads(call.get("params_as_json") or "{}")
                if params.get("available") is None:
                    continue
                return extractor.validate(ToolAvailabilityRequest(
                    run_id=plan.run_id, candidate_rank=plan.candidate.rank,
                    available=params["available"], quoted_rate=params.get("quoted_rate"),
                    reservation_number=params.get("reservation_number"), notes=params.get("notes") or "",
                ))
            except (ValueError, TypeError):
                continue
    return None


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
        # tool server (if the tunnel points here) -> the tool call in the conversation -> transcript regex
        availability = _tool_result(plan) or _tool_call_answer(plan, conversation)
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
