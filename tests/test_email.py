from datetime import date, datetime, timezone

import pytest

import config
import email_service
from contracts import (
    Availability,
    CallResult,
    HotelCandidate,
    HotelRequest,
    Run,
)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(config, "TEAM_NO", "15")
    monkeypatch.setattr(config, "RECEIPT_TO_EMAIL", "me@example.com")
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "resend")


def cand(rank, name):
    return HotelCandidate(
        rank=rank, name=name, address=f"{rank} King St, Toronto",
        phone_e164=f"+1416555000{rank}", total_price=100.0 * rank, source="test",
    )


def mixed_run() -> Run:
    """Available / sold out / voicemail, deliberately out of rank order."""
    return Run(
        run_id="r1",
        prompt="p",
        started_at=datetime.now(timezone.utc),
        request=HotelRequest(city="Toronto", check_in=date(2026, 11, 1), check_out=date(2026, 11, 3), guests=2),
        candidates=[cand(3, "Gamma"), cand(1, "Alpha"), cand(2, "Beta")],
        calls=[
            CallResult(candidate_rank=1, conversation_id="c1", outcome="available", duration_s=40,
                       transcript="Agent: Hi.\nHotel: Yes, $189 a night."),
            CallResult(candidate_rank=2, conversation_id="c2", outcome="unavailable", duration_s=35,
                       transcript="Agent: Hi.\nHotel: Sorry, sold out."),
            CallResult(candidate_rank=3, conversation_id="c3", outcome="voicemail", duration_s=20, transcript=None),
        ],
        availability=[
            Availability(candidate_rank=1, available=True, quoted_rate=189.0, source="tool"),
            Availability(candidate_rank=2, available=False, source="tool"),
        ],
    )


def test_subject_is_exact():
    assert email_service.subject() == "Team # 15 - Hotel Reservation Confirmed!"


def test_hotels_sorted_cheapest_first():
    payload = email_service.build_payload(mixed_run())
    assert [h.name for h in payload.hotels] == ["Alpha", "Beta", "Gamma"]


def test_body_mixed_outcomes():
    body = email_service.render_body(email_service.build_payload(mixed_run()))
    assert body.startswith("Check-in: 2026-11-01\nCheck-out: 2026-11-03\nGuests: 2\n")
    assert "--- Hotel 1 of 3 ---\nHotel Name: Alpha\nAddress: 1 King St, Toronto\nPhone: +14165550001\n" in body
    assert "Availability: Available\nQuoted Rate: $189.00" in body
    assert "Availability: Not available\nQuoted Rate: not given" in body
    assert "Availability: Unknown (voicemail / no answer / error)" in body
    assert "Hotel: Yes, $189 a night." in body
    assert "No transcript: voicemail" in body


def test_missing_call_still_reported():
    run = mixed_run()
    run.calls = run.calls[:1]
    body = email_service.render_body(email_service.build_payload(run))
    assert "No transcript: not called" in body
    assert "Hotel Name: Gamma" in body


def test_availability_falls_back_to_call_result():
    run = mixed_run()
    run.availability = []
    run.calls[0].availability = Availability(candidate_rank=1, available=True, quoted_rate=150.0, source="transcript")
    payload = email_service.build_payload(run)
    assert payload.hotels[0].availability is True and payload.hotels[0].quoted_rate == 150.0


def test_check_rejects_bad_subject():
    payload = email_service.build_payload(mixed_run())
    payload.subject = "Team # 15 - Hotel Reservation Confirmed"
    with pytest.raises(ValueError, match="subject"):
        email_service.check_payload(payload)


def test_check_rejects_empty_field():
    payload = email_service.build_payload(mixed_run())
    payload.hotels[1].address = " "
    with pytest.raises(ValueError, match="hotel 2: address"):
        email_service.check_payload(payload)


def test_check_rejects_missing_team_no(monkeypatch):
    payload = email_service.build_payload(mixed_run())
    monkeypatch.setattr(config, "TEAM_NO", "")
    with pytest.raises(ValueError):
        email_service.check_payload(payload)


def test_send_uses_resend_and_records_receipt(monkeypatch):
    sent = {}
    monkeypatch.setattr(email_service, "_send_resend", lambda to, s, b: sent.update(to=to, s=s, b=b) or "rid-1")
    run = mixed_run()
    receipt = email_service.send(run)
    assert receipt.message_id == "rid-1" and run.email is receipt
    assert sent["to"] == "me@example.com" and sent["s"] == "Team # 15 - Hotel Reservation Confirmed!"


def test_send_falls_back_to_smtp(monkeypatch):
    def boom(*a):
        raise RuntimeError("resend down")

    monkeypatch.setattr(email_service, "_send_resend", boom)
    monkeypatch.setattr(email_service, "_send_smtp", lambda to, s, b: "<smtp-id>")
    assert email_service.send(mixed_run()).message_id == "<smtp-id>"


def test_send_smtp_provider_skips_resend(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "smtp")
    monkeypatch.setattr(email_service, "_send_resend", lambda *a: pytest.fail("should not be called"))
    monkeypatch.setattr(email_service, "_send_smtp", lambda to, s, b: "<smtp-id>")
    assert email_service.send(mixed_run()).message_id == "<smtp-id>"


def test_long_transcripts_not_truncated(monkeypatch):
    run = mixed_run()
    run.calls[0].transcript = "\n".join(f"Hotel: line {i}" for i in range(500))
    body = email_service.render_body(email_service.build_payload(run))
    assert "Hotel: line 499" in body


def test_reservation_number_triggers_cancel_warning():
    run = mixed_run()
    run.availability[0].reservation_number = "HX4729"
    body = email_service.render_body(email_service.build_payload(run))
    assert "WARNING: Hotel issued reservation # HX4729. No booking was intended, please cancel it." in body
    assert body.count("WARNING") == 1


def test_no_warning_without_reservation_number():
    assert "WARNING" not in email_service.render_body(email_service.build_payload(mixed_run()))
