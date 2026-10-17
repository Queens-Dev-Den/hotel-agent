import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
import pytest

import config
import tool_server
from contracts import HotelCandidate, Run

SECRET = "test-secret"


@pytest.fixture()
def base(monkeypatch):
    monkeypatch.setattr(config, "TOOL_SHARED_SECRET", SECRET)
    tool_server.reset()
    port = tool_server.start_in_background(0)
    yield f"http://127.0.0.1:{port}"
    tool_server.stop()


def post(base, body, secret=SECRET, raw=None):
    headers = {"Content-Type": "application/json"}
    if secret is not None:
        headers["X-Tool-Secret"] = secret
    return httpx.post(f"{base}/record_availability", headers=headers,
                      content=raw if raw is not None else json.dumps(body), timeout=5)


def payload(rank=1, **kw):
    return {"run_id": "r1", "candidate_rank": rank, "available": True, **kw}


def test_health(base):
    r = httpx.get(f"{base}/health")
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_record_and_read_back(base):
    r = post(base, payload(1, quoted_rate="$189 a night", notes=" king "))
    assert r.status_code == 200 and "Recorded" in r.json()["message"]
    got = tool_server.results_for("r1")
    assert got[1].available is True and got[1].quoted_rate == 189.0
    assert got[1].notes == "king" and got[1].source == "tool"


def test_string_values_from_elevenlabs_are_coerced(base):
    r = post(base, {"run_id": "r1", "candidate_rank": "2", "available": "false"})
    assert r.status_code == 200
    assert tool_server.results_for("r1")[2].available is False


def test_bad_secret_rejected(base):
    assert post(base, payload(), secret="nope").status_code == 401
    assert post(base, payload(), secret=None).status_code == 401
    assert tool_server.results_for("r1") == {}


def test_no_configured_secret_fails_closed(base, monkeypatch):
    monkeypatch.setattr(config, "TOOL_SHARED_SECRET", "")
    assert post(base, payload(), secret="").status_code == 401


def test_malformed_payload_422(base):
    assert post(base, {"run_id": "r1"}).status_code == 422
    assert post(base, payload(available="maybe")).status_code == 422


def test_invalid_json_400(base):
    assert post(base, None, raw="{not json").status_code == 400


def test_empty_and_oversized_body_400(base):
    assert post(base, None, raw="").status_code == 400
    assert post(base, None, raw="x" * (tool_server.MAX_BODY + 1)).status_code == 400


def test_unknown_paths_404(base):
    assert httpx.get(f"{base}/nope").status_code == 404
    assert httpx.post(f"{base}/nope", content="{}").status_code == 404


def test_last_write_wins(base):
    post(base, payload(1, available=True))
    post(base, payload(1, available=False))
    assert tool_server.results_for("r1")[1].available is False


def test_runs_are_isolated(base):
    post(base, {**payload(1), "run_id": "a"})
    post(base, {**payload(1), "run_id": "b", "available": False})
    assert tool_server.results_for("a")[1].available is True
    assert tool_server.results_for("b")[1].available is False
    assert tool_server.results_for("c") == {}


def test_three_simultaneous_calls_no_collision(base):
    barrier = threading.Barrier(3)

    def fire(rank):
        barrier.wait()
        return post(base, payload(rank, available=rank != 2, quoted_rate=f"${100 * rank}")).status_code

    with ThreadPoolExecutor(3) as pool:
        assert list(pool.map(fire, [1, 2, 3])) == [200, 200, 200]
    got = tool_server.results_for("r1")
    assert {r: (a.available, a.quoted_rate) for r, a in got.items()} == {
        1: (True, 100.0), 2: (False, 200.0), 3: (True, 300.0)}


def test_many_concurrent_requests(base):
    def fire(i):
        return post(base, {"run_id": f"run{i}", "candidate_rank": 1, "available": True}).status_code

    with ThreadPoolExecutor(20) as pool:
        assert set(pool.map(fire, range(60))) == {200}
    assert all(tool_server.results_for(f"run{i}") for i in range(60))


def test_register_run_and_unregistered_results_still_stored(base):
    cand = HotelCandidate(rank=1, name="A", address="x", phone_e164="+14165550001", total_price=1.0, source="t")
    run = Run(run_id="r1", prompt="p", started_at=datetime.now(timezone.utc))
    tool_server.register_run(run, cand)
    post(base, payload(1))
    post(base, payload(2))  # never registered
    assert set(tool_server.results_for("r1")) == {1, 2}


def test_start_is_idempotent(base):
    assert tool_server.start_in_background(0) == int(base.rsplit(":", 1)[1])


# --- resolve_availability -----------------------------------------------------

from contracts import Availability, CallResult, HotelRequest  # noqa: E402
from datetime import date  # noqa: E402


def make_run(transcripts: dict[int, str | None]) -> Run:
    cands = [HotelCandidate(rank=r, name=f"H{r}", address="a", phone_e164=f"+141655500{r}0", total_price=r * 100.0, source="t")
             for r in transcripts]
    calls = [CallResult(candidate_rank=r, conversation_id=f"c{r}", outcome="available", duration_s=1, transcript=t)
             for r, t in transcripts.items()]
    return Run(run_id="r1", prompt="p", started_at=datetime.now(timezone.utc),
               request=HotelRequest(city="T", check_in=date(2026, 11, 1), check_out=date(2026, 11, 2)),
               candidates=cands, calls=calls)


SOLD = "Agent: Hi\nHotel: Sorry, we're sold out."
YES = "Agent: Hi\nHotel: Yes, we have a room at $189."
FUZZY = "Agent: Hi\nHotel: Hmm, let me ask my manager."


def test_resolve_prefers_tool_over_transcript(base):
    post(base, payload(1, available=True, quoted_rate="$150"))
    run = make_run({1: SOLD})  # transcript disagrees; the tool result wins
    got = tool_server.resolve_availability(run)
    assert got[0].available is True and got[0].source == "tool" and run.availability == got


def test_resolve_uses_regex_when_tool_missing(base):
    run = make_run({1: YES, 2: SOLD})
    got = tool_server.resolve_availability(run)
    assert [(a.candidate_rank, a.available, a.source) for a in got] == [(1, True, "transcript"), (2, False, "transcript")]
    assert got[0].quoted_rate == 189.0


def test_resolve_calls_fallback_only_when_regex_unsure(base):
    calls = []

    def fallback(transcript, rank, *, usage=None):
        calls.append(rank)
        return Availability(candidate_rank=rank, available=True, quoted_rate=99.0, source="backboard")

    run = make_run({1: YES, 2: FUZZY})
    got = tool_server.resolve_availability(run, fallback)
    assert calls == [2] and got[1].source == "backboard"


def test_resolve_fallback_none_or_error_means_unknown(base):
    run = make_run({1: FUZZY, 2: FUZZY})

    def boom(*a, **k):
        raise RuntimeError("backboard down")

    assert tool_server.resolve_availability(run, lambda *a, **k: None)[0].available is None
    assert tool_server.resolve_availability(run, boom)[1].available is None


def test_resolve_no_transcript_never_calls_fallback(base):
    run = make_run({1: None})
    got = tool_server.resolve_availability(run, lambda *a, **k: pytest.fail("no tokens for empty transcripts"))
    assert got[0].available is None


def test_resolve_keeps_call_result_availability(base):
    run = make_run({1: FUZZY})
    run.calls[0].availability = Availability(candidate_rank=1, available=False, source="tool")
    assert tool_server.resolve_availability(run)[0].available is False


def test_resolve_fills_volunteered_reservation_number(base):
    post(base, payload(1))
    run = make_run({1: YES + "\nHotel: Your reservation number is HX4729."})
    assert tool_server.resolve_availability(run)[0].reservation_number == "HX4729"


def test_resolve_voicemail_call_is_unknown_even_if_greeting_says_not_available(base):
    greeting = "Agent: Hi\nHotel: The person you're trying to reach is not available. At the tone, please record your message."
    run = make_run({1: greeting})
    run.calls[0].outcome = "voicemail"
    got = tool_server.resolve_availability(run, lambda *a, **k: pytest.fail("no tokens for a voicemail"))
    assert got[0].available is None


def test_resolve_ignores_transcript_when_nobody_answered(base):
    run = make_run({1: YES})  # transcript says yes, but the call never connected to a person
    run.calls[0].outcome = "no_answer"
    assert tool_server.resolve_availability(run)[0].available is None


def test_resolve_still_honours_a_tool_result_on_odd_outcomes(base):
    post(base, payload(1, available=True, quoted_rate="$99"))
    run = make_run({1: None})
    run.calls[0].outcome = "error"
    assert tool_server.resolve_availability(run)[0].available is True
