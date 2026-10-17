"""WS4: record_availability endpoint for the ElevenLabs server tool.

Stores each call's result keyed by (run_id, candidate_rank). Never sends email.
Safe for 3 simultaneous requests.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contracts import Availability, HotelCandidate, Run


def register_run(run: Run, candidate: HotelCandidate) -> None:
    """Tell the server to expect a result for this run + candidate."""
    raise NotImplementedError


def results_for(run_id: str) -> dict[int, Availability]:
    """Stored results for a run, keyed by candidate_rank."""
    raise NotImplementedError


def start_in_background(port: int = 8000) -> None:
    """Run the HTTP server on a daemon thread (called first by pipeline.run)."""
    raise NotImplementedError
