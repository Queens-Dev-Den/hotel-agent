"""WS4: build and send the single availability email (all hotels + transcripts).

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


def render_body(payload: EmailPayload) -> str:
    """Header (dates, guests), then one section per hotel with its transcript."""
    req = payload.request
    lines = [
        f"Check-in: {req.check_in.isoformat()}",
        f"Check-out: {req.check_out.isoformat()}",
        f"Guests: {req.guests}",
    ]
    total = len(payload.hotels)
    for i, h in enumerate(payload.hotels, start=1):
        rate = f"${h.quoted_rate:,.2f}" if h.quoted_rate is not None else "not given"
        lines += [
            "",
            f"--- Hotel {i} of {total} ---",
            f"Hotel Name: {h.name}",
            f"Address: {h.address}",
            f"Phone: {h.phone}",
            f"Availability: {_status_text(h.availability)}",
            f"Quoted Rate: {rate}",
        ]
        if h.reservation_number:
            lines.append(
                f"WARNING: Hotel issued reservation # {h.reservation_number}. "
                "No booking was intended, please cancel it."
            )
        lines += [
            "",
            "Transcript:",
            h.transcript or "",
        ]
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
