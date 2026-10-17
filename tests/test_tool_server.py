import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
import pytest

import tool_server
from contracts import HotelCandidate, Run

SECRET = "test-secret"


@pytest.fixture()
def base(monkeypatch):
    monkeypatch.setenv("TOOL_SHARED_SECRET", SECRET)
    monkeypatch.setitem(__import__("sys").modules, "config", None)
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
    monkeypatch.setenv("TOOL_SHARED_SECRET", "")
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
