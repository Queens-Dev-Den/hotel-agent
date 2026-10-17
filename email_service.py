"""WS4: build and send the single email: the cheapest available hotel with its price for
the stay, call-recording link and transcript.

Plain text only. Resend first, Gmail SMTP fallback.
"""
from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import make_msgid

import httpx
import config
from contracts import (
    Availability,
    CallResult,
    EmailPayload,
    EmailReceipt,
    HotelRequest,
    HotelSection,
    Run,
)

log = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
UNKNOWN_STATUS = "Unknown (voicemail / no answer / error)"


def subject() -> str:
    return f"Team # {config.TEAM_NO} - Hotel Reservation Confirmed!"


def _status_for(rank: int, run: Run, call: CallResult | None) -> Availability | None:
    for a in run.availability:
        if a.candidate_rank == rank:
            return a
    return call.availability if call else None


def audio_url(conversation_id: str) -> str | None:
    """Dashboard link to one call's recording; None for fake or failed calls."""
    if not conversation_id or conversation_id.startswith("mock-"):
        return None
    return config.ELEVENLABS_CONVERSATION_URL.format(id=conversation_id)


def build_payload(run: Run) -> EmailPayload:
    """Join candidates, availability and calls by rank, cheapest first."""
    if run.request is None:
        raise ValueError("run.request is missing")
    calls = {c.candidate_rank: c for c in run.calls}

    hotels: list[HotelSection] = []
    for cand in sorted(run.candidates, key=lambda c: c.rank):
        call = calls.get(cand.rank)
        status = _status_for(cand.rank, run, call)
        transcript = call.transcript.strip() if call and call.transcript else ""
        if not transcript:
            outcome = call.outcome if call else "not called"
            transcript = f"No transcript: {outcome}"
        hotels.append(
            HotelSection(
                name=cand.name,
                address=cand.address,
                phone=cand.phone_e164,
                availability=status.available if status else None,
                quoted_rate=status.quoted_rate if status else None,
                transcript=transcript,
                reservation_number=status.reservation_number if status else None,
                search_price=cand.total_price,
                audio_url=audio_url(call.conversation_id) if call else None,
            )
        )

    return EmailPayload(
        to=config.RECEIPT_TO_EMAIL or run.request.callback_email,
        subject=subject(),
        request=run.request,
        hotels=hotels,
    )


def _status_text(available: bool | None) -> str:
    if available is True:
        return "Available"
    if available is False:
        return "Not available"
    return UNKNOWN_STATUS


def best_index(hotels: list[HotelSection]) -> int:
    """The hotel to highlight: the cheapest one that said it has a room.

    Cheapest by quoted rate; hotels without a quote keep search-price order (hotels are
    already cheapest first). If no hotel confirmed a room, the cheapest hotel overall.
    """
    available = [i for i, h in enumerate(hotels) if h.availability is True]
    if not available:
        return 0
    return min(available, key=lambda i: (hotels[i].quoted_rate is None, hotels[i].quoted_rate or 0, i))


def nights(req: HotelRequest) -> int:
    return max((req.check_out - req.check_in).days, 1)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def _rate_text(h: HotelSection) -> str:
    return f"${h.quoted_rate:,.2f}/night" if h.quoted_rate is not None else "not given"


def stay_price_text(h: HotelSection, n: int) -> str:
    """Total for the stay: the call's nightly quote x nights, else the search total."""
    if h.quoted_rate is not None:
        return (f"${h.quoted_rate * n:,.2f} total "
                f"(${h.quoted_rate:,.2f}/night x {_plural(n, 'night')}, quoted on the call)")
    if h.search_price is not None:
        return f"${h.search_price:,.2f} total (online search price; no rate quoted on the call)"
    return "not given"


def _extras(h: HotelSection) -> str:
    """Recording link for the one-line summary of other hotels."""
    return f", recording {h.audio_url}" if h.audio_url else ""


def render_body(payload: EmailPayload) -> str:
    """Dates, then the cheapest available hotel in full with its transcript, then a
    one-line summary of every other hotel we called."""
    req = payload.request
    hotels = payload.hotels
    best = best_index(hotels)
    h = hotels[best]
    found = h.availability is True
    total = len(hotels)
    n = nights(req)

    lines = [
        f"Check-in: {req.check_in.isoformat()}",
        f"Check-out: {req.check_out.isoformat()}",
        f"Guests: {req.guests}",
        "",
        f"We called the {total} cheapest hotel{'s' if total != 1 else ''} we found for these dates. "
        + ("Here is the cheapest one with a room available." if found
           else "None of them confirmed a room, so here is the cheapest one we called."),
        "",
        "=== Cheapest available hotel ===" if found else "=== Cheapest hotel (no room confirmed) ===",
        f"Hotel Name: {h.name}",
        f"Address: {h.address}",
        f"Phone: {h.phone}",
        f"Reservation Date: {req.check_in.isoformat()} to {req.check_out.isoformat()} ({_plural(n, 'night')})",
        f"Price for Stay: {stay_price_text(h, n)}",
        f"Call Recording: {h.audio_url or 'Not available'}",
        f"Availability: {_status_text(h.availability)}",
        "",
        "Transcript:",
        h.transcript or "",
    ]
    others = [(i, o) for i, o in enumerate(hotels) if i != best]
    if others:
        lines += ["", "=== Other hotels we called ==="]
        for i, o in others:
            lines.append(f"{i + 1}. {o.name}, {o.phone}: {_status_text(o.availability)}, "
                         f"rate {_rate_text(o)}{_extras(o)}")
    return "\n".join(lines) + "\n"


def check_payload(payload: EmailPayload) -> None:
    """Refuse to send anything that would not count. Raises ValueError."""
    if payload.subject != subject():
        raise ValueError(f"subject mismatch: {payload.subject!r}")
    if not config.TEAM_NO:
        raise ValueError("TEAM_NO is not set")
    if not payload.to:
        raise ValueError("no recipient (RECEIPT_TO_EMAIL / callback_email)")
    if not payload.hotels:
        raise ValueError("no hotels to report")
    for i, h in enumerate(payload.hotels, start=1):
        for field in ("name", "address", "phone", "transcript"):
            if not (getattr(h, field) or "").strip():
                raise ValueError(f"hotel {i}: {field} is empty")


def _send_resend(to: str, subj: str, body: str) -> str:
    key = config.RESEND_API_KEY
    if not key:
        raise RuntimeError("RESEND_API_KEY is not set")
    resp = httpx.post(
        RESEND_URL,
        headers={"Authorization": f"Bearer {key}"},
        json={
            "from": config.EMAIL_FROM,
            "to": [to],
            "subject": subj,
            "text": body,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def _send_smtp(to: str, subj: str, body: str) -> str:
    user = config.SMTP_USER
    password = config.SMTP_APP_PASSWORD
    if not (user and password):
        raise RuntimeError("SMTP_USER / SMTP_APP_PASSWORD are not set")
    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = to
    msg["Subject"] = subj
    msg["Message-ID"] = make_msgid()
    msg.set_content(body)
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as s:
        s.starttls()
        s.login(user, password)
        s.send_message(msg)
    return msg["Message-ID"]


def send(run: Run) -> EmailReceipt:
    """Check format, send one email (Resend, then SMTP on failure), set run.email."""
    payload = build_payload(run)
    check_payload(payload)
    body = render_body(payload)
    if not payload.hotels[best_index(payload.hotels)].audio_url:
        log.warning("reported hotel has no call-recording link; the organizers use it as proof")

    message_id = None
    if config.EMAIL_PROVIDER == "resend":
        try:
            message_id = _send_resend(payload.to, payload.subject, body)
        except Exception as exc:
            log.warning("Resend failed (%s); falling back to SMTP", exc)
    if message_id is None:
        message_id = _send_smtp(payload.to, payload.subject, body)

    receipt = EmailReceipt(message_id=message_id, sent_at=datetime.now(timezone.utc))
    run.email = receipt
    return receipt
