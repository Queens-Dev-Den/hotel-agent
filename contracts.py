"""Shared data contracts. Frozen at G0; changes go through a contract/* PR.

Flow: HotelRequest -> SearchResult -> CallPlan x3 -> CallResult x3 (parallel)
-> EmailPayload -> EmailReceipt, all collected in one Run.
See docs/git-workflow.md section 3.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class HotelRequest(BaseModel):
    city: str
    check_in: date
    check_out: date
    guests: int = 1
    callback_email: str = ""


class HotelCandidate(BaseModel):
    rank: int  # 1 = cheapest
    name: str
    address: str
    phone_e164: str = Field(pattern=r"^\+[1-9]\d{6,14}$")
    total_price: float
    currency: str = "CAD"
    source: str
    booking_url: str | None = None  # cheapest booking site's link, from search (never used by the call)
    booking_source: str | None = None  # e.g. "Booking.com", or the hotel's own name if official
    website: str | None = None  # the hotel's own site
    distance_km: float | None = None  # from downtown (the city's centre point), None if unknown


class SearchResult(BaseModel):
    request: HotelRequest
    candidates: list[HotelCandidate]  # sorted by price, max 3


class CallPlan(BaseModel):
    run_id: str
    candidate: HotelCandidate
    system_prompt: str  # <= 300 tokens
    first_message: str
    dynamic_vars: dict[str, str]  # must include run_id and candidate_rank
    prompt_tokens: int


class ToolAvailabilityRequest(BaseModel):
    run_id: str
    candidate_rank: int
    available: bool
    quoted_rate: str | None = None  # raw, as spoken
    notes: str = ""
    reservation_number: str | None = None  # only if the hotel volunteers one; we never book


class Availability(BaseModel):
    candidate_rank: int
    available: bool | None  # None = unknown
    quoted_rate: float | None = None
    notes: str = ""
    source: Literal["tool", "transcript", "backboard"]
    reservation_number: str | None = None  # only if the hotel volunteers one; we never book


class CallResult(BaseModel):
    candidate_rank: int
    conversation_id: str
    outcome: Literal["available", "unavailable", "voicemail", "no_answer", "error"]
    duration_s: float
    availability: Availability | None = None
    transcript: str | None = None  # full, speaker-labelled


class HotelSection(BaseModel):
    name: str
    address: str
    phone: str
    availability: bool | None
    quoted_rate: float | None = None  # per night, from the call
    transcript: str | None = None
    reservation_number: str | None = None  # optional for the organizers; only if the hotel gives one
    search_price: float | None = None  # total stay from search, used when the call quoted no rate
    audio_url: str | None = None  # ElevenLabs link to the call recording (organizers' proof)


class EmailPayload(BaseModel):
    to: str
    subject: str
    request: HotelRequest
    hotels: list[HotelSection]


class EmailReceipt(BaseModel):
    message_id: str
    sent_at: datetime


class Telemetry(BaseModel):
    stage_ms: dict[str, int] = Field(default_factory=dict)  # search, brain, dial, call, email
    tokens: dict[str, int] = Field(
        default_factory=lambda: {"backboard_in": 0, "backboard_out": 0, "agent_llm": 0}
    )


class Run(BaseModel):
    run_id: str
    prompt: str
    started_at: datetime
    request: HotelRequest | None = None
    candidates: list[HotelCandidate] = Field(default_factory=list)
    calls: list[CallResult] = Field(default_factory=list)
    availability: list[Availability] = Field(default_factory=list)
    email: EmailReceipt | None = None
    telemetry: Telemetry = Field(default_factory=Telemetry)
