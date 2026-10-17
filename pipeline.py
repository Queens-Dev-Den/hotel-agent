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
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
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
# Seconds the fake call takes per rank (0 in tests; the UI sets it so mock runs look live)
MOCK_CALL_DELAY_S = 0.0

# Progress callback: on_event(kind, data). Kinds: stage_start {name}, stage_done {name, ms},
# request {request}, candidates {candidates}, call_start {rank}, call_done {call}, done {run}
Emit = Callable[[str, dict], None]


def _no_emit(kind: str, data: dict) -> None:
    pass


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
    time.sleep(MOCK_CALL_DELAY_S * rank)
    return CallResult(
        candidate_rank=rank,
        conversation_id=f"mock-{plan.run_id}-{rank}",
        outcome="available",
        duration_s=MOCK_CALL_DELAY_S * rank,
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


def _call_all(plans: list[CallPlan], emit: Emit = _no_emit) -> list[CallResult]:
    """Every call at once. Each is capped; one slow or failing call never blocks the rest.
    Results are reported as each call ends, and returned in candidate order."""
    place_call = _place_call()
    cap = config.MAX_CALL_DURATION_S + JOIN_GRACE_S
    pool = ThreadPoolExecutor(max_workers=len(plans))
    started = time.monotonic()
    pending = {pool.submit(place_call, plan): plan.candidate.rank for plan in plans}
    for plan in plans:
        emit("call_start", {"rank": plan.candidate.rank})

    results: dict[int, CallResult] = {}
    while pending:
        done, _ = wait(pending, timeout=max(started + cap - time.monotonic(), 0), return_when=FIRST_COMPLETED)
        if not done:
            break
        for future in done:
            rank = pending.pop(future)
            try:
                results[rank] = future.result()
            except Exception as exc:
                log.warning("call %d failed: %s", rank, exc)
                results[rank] = CallResult(candidate_rank=rank, conversation_id="", outcome="error",
                                           duration_s=round(time.monotonic() - started, 1))
            emit("call_done", {"call": results[rank]})
    for rank in pending.values():
        log.warning("call %d hit the %ds cap", rank, cap)
        results[rank] = CallResult(candidate_rank=rank, conversation_id="", outcome="error", duration_s=float(cap))
        emit("call_done", {"call": results[rank]})
    pool.shutdown(wait=False, cancel_futures=True)  # don't wait on a hung call thread
    return [results[plan.candidate.rank] for plan in plans]


@contextmanager
def _stage(r: Run, name: str, emit: Emit):
    emit("stage_start", {"name": name})
    with telemetry.stage(r, name):
        yield
    emit("stage_done", {"name": name, "ms": r.telemetry.stage_ms[name]})


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


def run(prompt: str, *, callback_email: str | None = None, on_event: Emit | None = None) -> Run:
    """One prompt in, one email out. Returns the finished Run (also in run_telemetry.json).

    `on_event` gets live progress (see Emit) for the UI.
    """
    emit = on_event or _no_emit
    r = Run(run_id=uuid.uuid4().hex[:12], prompt=prompt, started_at=datetime.now(timezone.utc))
    telemetry.save(r)

    if not _mocked("voice"):
        import tool_server  # must be up before any call can fire record_availability
        tool_server.start_in_background(config.TOOL_SERVER_PORT)

    with _stage(r, "brain", emit):
        r.request = _parse_prompt()(prompt, callback_email=callback_email or config.RECEIPT_TO_EMAIL,
                                    usage=r.telemetry.tokens)
    emit("request", {"request": r.request})

    search_stats: dict[str, int] = {}
    with _stage(r, "search", emit):
        r.candidates = _find_candidates()(r.request, stats=search_stats)
    log.info("search dropped: %s", search_stats)
    emit("candidates", {"candidates": r.candidates})

    from agent_brain import build_call_plan
    plans = [build_call_plan(r.request, c, r.run_id) for c in r.candidates]
    if not _mocked("voice"):
        for c in r.candidates:
            tool_server.register_run(r, c)

    with _stage(r, "call", emit):
        r.calls = _call_all(_dial_targets(plans), emit)

    tool_results = {} if _mocked("voice") else tool_server.results_for(r.run_id)
    r.availability = [_availability(r, call, tool_results) for call in r.calls]

    with _stage(r, "email", emit):
        _send()(r)
    emit("done", {"run": r})
    return r
