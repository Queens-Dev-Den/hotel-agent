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


REAL_DOWNLOAD = email_service.download_recordings  # kept before the autouse stub replaces it


@pytest.fixture(autouse=True)
def no_real_downloads(monkeypatch):
    """Never fetch real recordings from ElevenLabs in tests; tests that want files set them."""
    monkeypatch.setattr(email_service, "download_recordings", lambda run, ranks=None: {})


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(config, "TEAM_NO", "15")
    monkeypatch.setattr(config, "RECEIPT_TO_EMAIL", "me@example.com")
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "resend")
    monkeypatch.setattr(config, "ELEVENLABS_CONVERSATION_URL", "https://el.test/c/{id}")


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
    assert email_service.subject() == "Team # 15 - Hotel Quotes Confirmed"


def test_hotels_sorted_cheapest_first():
    payload = email_service.build_payload(mixed_run())
    assert [h.name for h in payload.hotels] == ["Alpha", "Beta", "Gamma"]


def test_body_mixed_outcomes():
    body = email_service.render_body(email_service.build_payload(mixed_run()))
    assert body.startswith("Check-in: 2026-11-01\nCheck-out: 2026-11-03\nGuests: 2\n")
    assert "We called the 3 cheapest hotels we found for these dates. Here is the cheapest one with a room available." in body
    assert (
        "=== Cheapest available hotel ===\nHotel Name: Alpha\nAddress: 1 King St, Toronto\nPhone: +14165550001\n"
        "Reservation Date: 2026-11-01 to 2026-11-03 (2 nights)\n"
        "Price for Stay: $378.00 total ($189.00/night x 2 nights, quoted on the call)\n"
        "Call Recording: https://el.test/c/c1\n"
        "Availability: Available\n"
    ) in body
    assert "Hotel: Yes, $189 a night." in body
    assert "=== Other hotels we called ===" in body
    assert "2. Beta, +14165550002: Not available, rate not given" in body
    assert "3. Gamma, +14165550003: Unknown (voicemail / no answer / error), rate not given" in body


def test_only_highlighted_transcript_included():
    body = email_service.render_body(email_service.build_payload(mixed_run()))
    assert body.count("Transcript:") == 1
    assert "Sorry, sold out." not in body
    assert "No transcript: voicemail" not in body


def test_highlights_cheapest_quote_among_available():
    run = mixed_run()
    run.calls[1].transcript = "Agent: Hi.\nHotel: Yes, $120 a night."
    run.availability[1] = Availability(candidate_rank=2, available=True, quoted_rate=120.0, source="tool")
    body = email_service.render_body(email_service.build_payload(run))
    assert "=== Cheapest available hotel ===\nHotel Name: Beta" in body
    assert "Hotel: Yes, $120 a night." in body and "$189 a night" not in body
    assert "1. Alpha, +14165550001: Available, rate $189.00" in body


def test_none_available_highlights_cheapest_called():
    run = mixed_run()
    run.availability[0] = Availability(candidate_rank=1, available=False, source="tool")
    body = email_service.render_body(email_service.build_payload(run))
    assert "None of them confirmed a room, so here is the cheapest one we called." in body
    assert "=== Cheapest hotel (no room confirmed) ===\nHotel Name: Alpha" in body


def test_missing_call_still_reported():
    run = mixed_run()
    run.calls = run.calls[:1]
    body = email_service.render_body(email_service.build_payload(run))
    assert "3. Gamma, +14165550003: Unknown (voicemail / no answer / error)" in body


def test_availability_falls_back_to_call_result():
    run = mixed_run()
    run.availability = []
    run.calls[0].availability = Availability(candidate_rank=1, available=True, quoted_rate=150.0, source="transcript")
    payload = email_service.build_payload(run)
    assert payload.hotels[0].availability is True and payload.hotels[0].quoted_rate == 150.0


def test_check_rejects_bad_subject():
    payload = email_service.build_payload(mixed_run())
    payload.subject = "Team # 15 - Hotel Reservation Confirmed!"
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
    monkeypatch.setattr(email_service, "_send_resend", lambda to, s, b, files=(): sent.update(to=to, s=s, b=b) or "rid-1")
    run = mixed_run()
    receipt = email_service.send(run)
    assert receipt.message_id == "rid-1" and run.email is receipt
    assert sent["to"] == "me@example.com" and sent["s"] == "Team # 15 - Hotel Quotes Confirmed"


def test_send_falls_back_to_smtp(monkeypatch):
    def boom(*a):
        raise RuntimeError("resend down")

    monkeypatch.setattr(email_service, "_send_resend", boom)
    monkeypatch.setattr(email_service, "_send_smtp", lambda to, s, b, files=(): "<smtp-id>")
    assert email_service.send(mixed_run()).message_id == "<smtp-id>"


def test_send_smtp_provider_skips_resend(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "smtp")
    monkeypatch.setattr(email_service, "_send_resend", lambda *a: pytest.fail("should not be called"))
    monkeypatch.setattr(email_service, "_send_smtp", lambda to, s, b, files=(): "<smtp-id>")
    assert email_service.send(mixed_run()).message_id == "<smtp-id>"


def test_long_transcripts_not_truncated(monkeypatch):
    run = mixed_run()
    run.calls[0].transcript = "\n".join(f"Hotel: line {i}" for i in range(500))
    body = email_service.render_body(email_service.build_payload(run))
    assert "Hotel: line 499" in body


def test_reservation_number_not_in_email():
    run = mixed_run()
    run.availability[0].reservation_number = "HX4729"
    run.availability[1].reservation_number = "ZZ1"
    body = email_service.render_body(email_service.build_payload(run))
    assert "Reservation #" not in body and "reservation #" not in body
    assert "HX4729" not in body and "ZZ1" not in body
    assert "2. Beta, +14165550002: Not available, rate not given" in body


def test_stay_price_falls_back_to_search_total():
    run = mixed_run()
    run.availability[0].quoted_rate = None
    body = email_service.render_body(email_service.build_payload(run))
    assert "Price for Stay: $100.00 total (online search price; no rate quoted on the call)\n" in body


def test_one_night_stay_price():
    run = mixed_run()
    run.request.check_out = date(2026, 11, 2)
    body = email_service.render_body(email_service.build_payload(run))
    assert "(1 night)" in body and "$189.00 total ($189.00/night x 1 night, quoted on the call)" in body


def test_no_recording_link_for_fake_or_missing_calls():
    run = mixed_run()
    run.calls[0].conversation_id = "mock-r1-1"
    body = email_service.render_body(email_service.build_payload(run))
    assert "Call Recording: Not available\n" in body
    assert email_service.audio_url("") is None
    run.calls = []
    assert email_service.build_payload(run).hotels[0].audio_url is None


def test_recipient_from_request_beats_env(monkeypatch):
    monkeypatch.setattr(config, "RECEIPT_TO_EMAIL", "env@example.com")
    run = mixed_run()
    run.request.callback_email = "typed@example.com"
    assert email_service.build_payload(run).to == "typed@example.com"


def test_recipient_falls_back_to_env(monkeypatch):
    monkeypatch.setattr(config, "RECEIPT_TO_EMAIL", "env@example.com")
    assert email_service.build_payload(mixed_run()).to == "env@example.com"


def test_body_ends_with_team_signature():
    body = email_service.render_body(email_service.build_payload(mixed_run()))
    assert body.endswith(
        "\n--\nTeam 15\n"
        "Isaac Ouellette - isaacoue4@gmail.com\n"
        "Richard Guo - richardguo64@gmail.com\n"
        "Zachary Gazaille - zacharygazaille@gmail.com\n"
        "Jordan Bouckley - jordanbouckley@gmail.com\n"
    )


# --- call recordings attached as files ---------------------------------------------

def test_only_the_winning_hotels_recording_is_attached(monkeypatch):
    asked = []

    def download(run, ranks=None):
        asked.append(ranks)
        return {1: ("call1-alpha.mp3", b"ID3a")}

    monkeypatch.setattr(email_service, "download_recordings", download)
    sent = {}
    monkeypatch.setattr(email_service, "_send_resend",
                        lambda to, s, b, files=(): sent.update(b=b, files=list(files)) or "rid")
    email_service.send(mixed_run())

    assert asked == [{1}]  # Alpha (rank 1) is the cheapest available hotel
    assert sent["files"] == [("call1-alpha.mp3", b"ID3a")]
    assert "Call Recording: attached (call1-alpha.mp3)\n" in sent["b"]
    assert "recording" not in sent["b"].split("=== Other hotels we called ===")[1]


def test_winner_recording_follows_the_highlighted_hotel(monkeypatch):
    run = mixed_run()
    run.availability[0] = Availability(candidate_rank=1, available=False, source="tool")
    run.availability[1] = Availability(candidate_rank=2, available=True, quoted_rate=120.0, source="tool")
    asked = []
    monkeypatch.setattr(email_service, "download_recordings", lambda run, ranks=None: asked.append(ranks) or {})
    monkeypatch.setattr(email_service, "_send_resend", lambda to, s, b, files=(): "rid")
    email_service.send(run)
    assert asked == [{2}]


def test_resend_payload_has_base64_attachments(monkeypatch):
    monkeypatch.setattr(config, "RESEND_API_KEY", "k")
    captured = {}

    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"id": "rid"}

    monkeypatch.setattr(email_service.httpx, "post", lambda url, headers, json, timeout: captured.update(json) or Resp())
    email_service._send_resend("me@example.com", "subj", "body", [("call1-alpha.mp3", b"ID3")])
    assert captured["attachments"] == [{"filename": "call1-alpha.mp3", "content": "SUQz"}]


def test_smtp_message_has_audio_attachment(monkeypatch):
    monkeypatch.setattr(config, "SMTP_USER", "u@example.com")
    monkeypatch.setattr(config, "SMTP_APP_PASSWORD", "p")
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def starttls(self): pass
        def login(self, *a): pass
        def send_message(self, msg): sent.append(msg)

    monkeypatch.setattr(email_service.smtplib, "SMTP", FakeSMTP)
    email_service._send_smtp("me@example.com", "subj", "body", [("call1-alpha.mp3", b"ID3")])
    parts = [p for p in sent[0].iter_attachments()]
    assert [(p.get_filename(), p.get_content_type(), p.get_content()) for p in parts] == [
        ("call1-alpha.mp3", "audio/mpeg", b"ID3")]


def test_download_recordings_skips_fake_failed_and_unrequested(monkeypatch):
    import sys
    import types

    run = mixed_run()
    run.calls[1].conversation_id = "mock-r1-2"  # fake call: no recording

    def audio(cid):
        if cid == "c3":
            raise ConnectionError("boom")
        return b"ID3" + cid.encode()

    monkeypatch.setitem(sys.modules, "voice_agent", types.SimpleNamespace(conversation_audio=audio))
    assert REAL_DOWNLOAD(run) == {1: ("call1-alpha.mp3", b"ID3c1")}
    assert REAL_DOWNLOAD(run, {3}) == {}  # requested, but the download failed
    assert REAL_DOWNLOAD(run, {2}) == {}  # fake call
