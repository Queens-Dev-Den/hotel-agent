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

    kinds = [k for k, _ in events]
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
