"""WS4: build and send the single availability email (all hotels + transcripts).

Plain text only. Resend first, Gmail SMTP fallback.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contracts import EmailPayload, EmailReceipt, Run


def subject() -> str:
    """'Team # {TEAM_NO} - Hotel Reservation Confirmed!' from config."""
    raise NotImplementedError


def build_payload(run: Run) -> EmailPayload:
    """Join candidates, availability and calls by rank, cheapest first."""
    raise NotImplementedError


def render_body(payload: EmailPayload) -> str:
    """Header (dates, guests), one section per hotel, transcript per section."""
    raise NotImplementedError


def send(run: Run) -> EmailReceipt:
    """Assert format, send one email (Resend, then SMTP on failure), set email.sent_at."""
    raise NotImplementedError
