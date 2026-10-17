"""G1 skeleton without telephony: HTTP tool calls -> resolve -> one email."""
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

import config
import email_service
import tool_server
from test_tool_server import FUZZY, SOLD, make_run  # noqa: F401  (fixtures reused)


@pytest.fixture()
def server(monkeypatch):
    monkeypatch.setattr(config, "TOOL_SHARED_SECRET", "s3")
    monkeypatch.setattr(config, "TEAM_NO", "15")
    monkeypatch.setattr(config, "RECEIPT_TO_EMAIL", "me@example.com")
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "resend")
    tool_server.reset()
    port = tool_server.start_in_background(0)
    yield f"http://127.0.0.1:{port}"
    tool_server.stop()


def test_three_parallel_tool_calls_then_one_email(server, monkeypatch):
    sent = []
    monkeypatch.setattr(email_service, "_send_resend", lambda to, s, b: sent.append((to, s, b)) or "mid-1")
    run = make_run({1: "Agent: Hi\nHotel: Yes, $189.", 2: SOLD, 3: None})

    def tool_call(rank):
        return httpx.post(f"{server}/record_availability", headers={"X-Tool-Secret": "s3"},
                          content=json.dumps({"run_id": "r1", "candidate_rank": rank, "available": rank == 1,
                                              "quoted_rate": "$189" if rank == 1 else None}))

    with ThreadPoolExecutor(2) as pool:  # rank 3 (voicemail) never fires the tool
        assert [r.status_code for r in pool.map(tool_call, [1, 2])] == [200, 200]

    tool_server.resolve_availability(run)
    receipt = email_service.send(run)

    assert len(sent) == 1 and receipt.message_id == "mid-1" and run.email is receipt
    to, subject, body = sent[0]
    assert to == "me@example.com" and subject == "Team # 15 - Hotel Reservation Confirmed!"
    assert "=== Cheapest available hotel ===" in body
    assert "Availability: Available\nQuoted Rate: $189.00" in body
    assert "Hotel: Yes, $189." in body and body.count("Transcript:") == 1
    assert ": Not available, rate not given" in body
    assert ": Unknown (voicemail / no answer / error), rate not given" in body
