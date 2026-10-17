"""WS4: zero-token availability extraction and validation.

validate() turns a record_availability tool payload into an Availability.
from_transcript() is the regex fallback for calls whose tool never fired.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contracts import Availability, ToolAvailabilityRequest


def parse_rate(raw: str | None) -> float | None:
    """'$189', '189.00', 'CAD 189/night' -> 189.0; anything unparseable -> None."""
    raise NotImplementedError


def validate(payload: ToolAvailabilityRequest) -> Availability:
    """Coerce available to bool, parse quoted_rate, trim notes; source='tool'."""
    raise NotImplementedError


def from_transcript(text: str | None, candidate_rank: int) -> Availability:
    """Scan Hotel: lines. Negatives win over positives; both or neither -> available=None."""
    raise NotImplementedError
