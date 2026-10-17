"""WS1: the one orchestrator. Prompt -> search -> 3 parallel calls -> one email.

pipeline.run(prompt) is called by both main.py and app.py. There is no failover:
every candidate is called at once, and a voicemail, no-answer or error is simply
that hotel's result. See docs/git-workflow.md section 6.

Modules listed in config.MOCK_MODULES ("search", "brain", "voice", "email") are
replaced by the fakes below, so the run works before every branch has landed.
"""

from __future__ import annotations

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import date, datetime, timedelta, timezone

import config
import telemetry
from contracts import (
    Availability,
    CallPlan,
    CallResult,
    EmailReceipt,
    HotelCandidate,
    HotelRequest,
    Run,
)

log = logging.getLogger(__name__)

# Extra wait on top of MAX_CALL_DURATION_S for dial time and the transcript fetch
JOIN_GRACE_S = 30


# --- fakes (MOCK_MODULES) --------------------------------------------------------

def _mock_parse_prompt(prompt: str, *, callback_email: str = "", usage=None) -> HotelRequest:
    check_in = date.today() + timedelta(days=1)
    return HotelRequest(city="Toronto", check_in=check_in, check_out=check_in + timedelta(days=1),
                        callback_email=callback_email)


def _mock_find_candidates(request: HotelRequest, *, stats=None) -> list[HotelCandidate]:
    return [
        HotelCandidate(rank=i, name=f"Mock Hotel {i}", address=f"{i} King St W, Toronto, ON",
                       phone_e164=f"+1416555010{i}", total_price=50.0 * i, source="mock")
        for i in (1, 2, 3)
    ]


def _mock_place_call(plan: CallPlan) -> CallResult:
    rank = plan.candidate.rank
    return CallResult(
        candidate_rank=rank,
        conversation_id=f"mock-{plan.run_id}-{rank}",
        outcome="available",
        duration_s=0.0,
        availability=Availability(candidate_rank=rank, available=True, quoted_rate=99.0, source="tool"),
        transcript="Agent: Hi, I'm calling to check room availability please.\nHotel: Yes, we have a room.",
    )


def _mock_send(run: Run) -> EmailReceipt:
    log.info("MOCK email to %s: %d hotels", run.request.callback_email if run.request else "?", len(run.candidates))
    receipt = EmailReceipt(message_id=f"mock-{run.run_id}", sent_at=datetime.now(timezone.utc))
    run.email = receipt
    return receipt


# --- module selection ---------------------------------------------------------------

def _mocked(module: str) -> bool:
    return module in config.MOCK_MODULES


def _parse_prompt():
    if _mocked("brain"):
        return _mock_parse_prompt
    from agent_brain import parse_prompt
    return parse_prompt


def _find_candidates():
    if _mocked("search"):
        return _mock_find_candidates
    from search_service import find_candidates
    return find_candidates


def _place_call():
    if _mocked("voice"):
        return _mock_place_call
    from voice_agent import place_call
    return place_call


def _send():
    if _mocked("email"):
        return _mock_send
    from email_service import send
    return send


# --- stages ------------------------------------------------------------------------

def _dial_targets(plans: list[CallPlan]) -> list[CallPlan]:
    """Swap in the mock hotel numbers unless USE_MOCK_HOTEL is off. Only the plan's
    copy changes: run.candidates (and so the email) keep the real hotel's details."""
    if not config.USE_MOCK_HOTEL or _mocked("voice"):
        return plans
    if len(config.MOCK_HOTEL_PHONES) < len(plans):
        raise RuntimeError(
            f"USE_MOCK_HOTEL is on but MOCK_HOTEL_PHONES has {len(config.MOCK_HOTEL_PHONES)} "
            f"numbers for {len(plans)} calls. Refusing to dial real hotels."
        )
    return [
        plan.model_copy(update={"candidate": plan.candidate.model_copy(update={"phone_e164": phone})})
        for plan, phone in zip(plans, config.MOCK_HOTEL_PHONES)
    ]


def _call_all(plans: list[CallPlan]) -> list[CallResult]:
    """Every call at once. Each is capped; one slow or failing call never blocks the rest."""
    place_call = _place_call()
    cap = config.MAX_CALL_DURATION_S + JOIN_GRACE_S
    pool = ThreadPoolExecutor(max_workers=len(plans))
    started = time.monotonic()
    futures = [pool.submit(place_call, plan) for plan in plans]
    results = []
    for plan, future in zip(plans, futures):
        rank = plan.candidate.rank
        try:
            results.append(future.result(timeout=max(cap - (time.monotonic() - started), 0)))
        except FutureTimeout:
            log.warning("call %d hit the %ds cap", rank, cap)
            results.append(CallResult(candidate_rank=rank, conversation_id="", outcome="error",
                                      duration_s=float(cap)))
        except Exception as exc:
            log.warning("call %d failed: %s", rank, exc)
            results.append(CallResult(candidate_rank=rank, conversation_id="", outcome="error",
                                      duration_s=round(time.monotonic() - started, 1)))
    pool.shutdown(wait=False, cancel_futures=True)  # don't wait on a hung call thread
    return results


def _availability(run: Run, call: CallResult, tool_results: dict[int, Availability]) -> Availability:
    """Tool result first, then the call's own, then transcript regex, then Backboard."""
    rank = call.candidate_rank
    found = tool_results.get(rank) or call.availability
    if found is not None:
        return found
    unknown = Availability(candidate_rank=rank, available=None, source="transcript")
    if not call.transcript:
        return unknown
    try:
        from extractor import from_transcript
        found = from_transcript(call.transcript, rank)
        if found.available is not None:
            return found
    except ImportError:
        pass
    from agent_brain import fallback_extract
    return fallback_extract(call.transcript, rank, usage=run.telemetry.tokens) or unknown


def run(prompt: str, *, callback_email: str | None = None) -> Run:
    """One prompt in, one email out. Returns the finished Run (also in run_telemetry.json)."""
    r = Run(run_id=uuid.uuid4().hex[:12], prompt=prompt, started_at=datetime.now(timezone.utc))
    telemetry.save(r)

    if not _mocked("voice"):
        import tool_server  # must be up before any call can fire record_availability
        tool_server.start_in_background(config.TOOL_SERVER_PORT)

    with telemetry.stage(r, "brain"):
        r.request = _parse_prompt()(prompt, callback_email=callback_email or config.RECEIPT_TO_EMAIL,
                                    usage=r.telemetry.tokens)

    search_stats: dict[str, int] = {}
    with telemetry.stage(r, "search"):
        r.candidates = _find_candidates()(r.request, stats=search_stats)
    log.info("search dropped: %s", search_stats)

    from agent_brain import build_call_plan
    plans = [build_call_plan(r.request, c, r.run_id) for c in r.candidates]
    if not _mocked("voice"):
        for c in r.candidates:
            tool_server.register_run(r, c)

    with telemetry.stage(r, "call"):
        r.calls = _call_all(_dial_targets(plans))

    tool_results = {} if _mocked("voice") else tool_server.results_for(r.run_id)
    r.availability = [_availability(r, call, tool_results) for call in r.calls]

    with telemetry.stage(r, "email"):
        _send()(r)
    return r
