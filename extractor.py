"""WS4: zero-token availability extraction and validation.

validate() turns a record_availability tool payload into an Availability.
from_transcript() is the regex fallback for calls whose tool never fired.
"""
from __future__ import annotations

import re

from contracts import Availability, ToolAvailabilityRequest

MAX_NOTES = 500

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_SPEAKER = re.compile(r"^\s*(agent|hotel)\s*:\s*(.*)$", re.IGNORECASE)

# Negatives are matched first and cut out of the utterance, so "don't have a room"
# can't be read as the positive "have a room".
_NEGATIVE = re.compile(
    r"""
    sold[\s-]*out | fully[\s-]*booked | booked[\s-]*up | no[\s-]+vacanc\w* |
    no[\s-]+availability | nothing\s+(?:is\s+)?available |
    (?:do\s*n['’]?t|do\s+not|does\s*n['’]?t|did\s*n['’]?t)\s+have\s+(?:any\s+)?(?:a\s+|an\s+)?(?:rooms?|availability|vacanc\w*) |
    no\s+(?:rooms?|suites?)\b |
    (?:not|isn['’]?t|aren['’]?t|un)[\s-]*available |
    (?:we['’]?re|we\s+are|completely|totally)\s+(?:completely\s+|totally\s+)?full |
    (?:can['’]?t|cannot|unable\s+to)\s+(?:see|find)\s+any
    """,
    re.IGNORECASE | re.VERBOSE,
)
# Clauses that mention availability without answering ("let me check what's available").
_NOISE = re.compile(
    r"(?:let\s+me|i['’]?ll|i\s+will|one\s+moment|hold\s+on|give\s+me)[^.?!,;]*?(?:check|see|look)[^.?!,;]*"
    r"|what['’]?s\s+available|what\s+is\s+available",
    re.IGNORECASE,
)
_POSITIVE = re.compile(
    r"""
    (?:we|i)\s+(?:do\s+)?have\s+(?:a|an|some|one|two|any|rooms?|availability|vacanc\w*)\b |
    have\s+(?:a|an)\s+(?:room|suite) |
    (?:rooms?|suites?|vacanc\w*)\s+(?:is|are)?\s*available |
    available |
    can\s+accommodate | there['’]?s\s+(?:a|an)\s+(?:room|suite)
    """,
    re.IGNORECASE | re.VERBOSE,
)
_RATE = re.compile(
    r"""
    (?:\$|cad\s*|usd\s*)\s*(\d[\d,]*(?:\.\d{1,2})?) |
    (\d[\d,]*(?:\.\d{1,2})?)\s*(?:dollars?|bucks|cad|a\s+night|per\s+night|/\s*night)
    """,
    re.IGNORECASE | re.VERBOSE,
)


def parse_rate(raw: str | None) -> float | None:
    """'$189', '189.00', 'CAD 189/night' -> 189.0; anything unparseable -> None."""
    if not raw:
        return None
    match = _NUMBER.search(raw)
    if not match:
        return None
    try:
        value = float(match.group().replace(",", ""))
    except ValueError:
        return None
    return value if value > 0 else None


def validate(payload: ToolAvailabilityRequest) -> Availability:
    """Coerce available to bool, parse quoted_rate, trim notes; source='tool'."""
    return Availability(
        candidate_rank=payload.candidate_rank,
        available=bool(payload.available),
        quoted_rate=parse_rate(payload.quoted_rate),
        notes=(payload.notes or "").strip()[:MAX_NOTES],
        source="tool",
    )


def _hotel_utterances(text: str) -> list[str]:
    """Hotel-side utterances in order; unlabelled lines continue the previous speaker."""
    utterances: list[tuple[str, str]] = []
    for line in text.splitlines():
        m = _SPEAKER.match(line)
        if m:
            utterances.append((m.group(1).lower(), m.group(2).strip()))
        elif line.strip() and utterances:
            speaker, said = utterances[-1]
            utterances[-1] = (speaker, f"{said} {line.strip()}")
    return [said for speaker, said in utterances if speaker == "hotel" and said]


def _classify(utterance: str) -> tuple[bool | None, str | None]:
    """(signal, remainder): signal is True/False, or None if no signal; 'both' -> ambiguous."""
    utterance = _NOISE.sub(" ", utterance)
    has_negative = bool(_NEGATIVE.search(utterance))
    remainder = _NEGATIVE.sub(" ", utterance)
    has_positive = bool(_POSITIVE.search(remainder))
    if has_negative and has_positive:
        return None, "ambiguous"
    if has_negative:
        return False, None
    if has_positive:
        return True, None
    return None, None


_CEILING = re.compile(r"(?:under|below|less\s+than|up\s+to|over|above|more\s+than)\s*$", re.IGNORECASE)


def _find_rate(utterance: str) -> float | None:
    """First rate in the utterance, skipping bounds like 'under $200'."""
    for m in _RATE.finditer(utterance):
        if _CEILING.search(utterance[: m.start()]):
            continue
        return parse_rate(m.group(1) or m.group(2))
    return None


def from_transcript(text: str | None, candidate_rank: int) -> Availability:
    """Scan Hotel: lines. The last utterance that answers wins; unclear -> available=None."""
    unknown = Availability(candidate_rank=candidate_rank, available=None, source="transcript")
    if not text:
        return unknown

    for utterance in reversed(_hotel_utterances(text)):
        signal, flag = _classify(utterance)
        if flag == "ambiguous":
            return unknown
        if signal is None:
            continue
        return Availability(
            candidate_rank=candidate_rank,
            available=signal,
            quoted_rate=_find_rate(utterance) if signal else None,
            notes="",
            source="transcript",
        )
    return unknown
