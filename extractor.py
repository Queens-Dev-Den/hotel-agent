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
# A reservation number is only ever captured if the hotel volunteers one (we never book).
_RESNUM_VALID = re.compile(r"^[A-Z0-9-]{4,20}$")
_RESNUM_PHRASE = re.compile(
    r"(?:reservation|confirmation|booking)\s*(?:number|code|#|no\.?)\s*(?:is|:|would\s+be)?\s*(?P<code>[A-Za-z0-9][A-Za-z0-9\-, ]{2,60})",
    re.IGNORECASE,
)
_SPOKEN = {
    **{w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine".split())},
    "oh": "0",
}
_NATO = {w: w[0].upper() for w in (
    "alpha alfa bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike "
    "november oscar papa quebec romeo sierra tango uniform victor whiskey yankee zulu").split()}
_NATO["x-ray"] = _NATO["xray"] = "X"

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


def normalize_reservation_number(raw: str | None) -> str | None:
    """'hx 4729' -> 'HX4729'; anything not matching ^[A-Z0-9-]{4,20}$ -> None."""
    if not raw:
        return None
    code = re.sub(r"\s+", "", raw).upper()
    return code if _RESNUM_VALID.match(code) else None


def _spoken_code(fragment: str) -> str | None:
    """'H X four seven two nine' / 'hotel x-ray 4-7-2-9' / 'HX4729' -> 'HX4729'."""
    out = []
    for token in re.split(r"[\s,]+", fragment.strip()):
        token = token.strip(".").lower()
        if not token:
            continue
        pieces = ["x-ray"] if token == "x-ray" else token.split("-")
        for piece in pieces:
            if piece in _SPOKEN:
                out.append(_SPOKEN[piece])
            elif piece in _NATO:
                out.append(_NATO[piece])
            elif re.fullmatch(r"[a-z0-9]+", piece) and (len(piece) == 1 or any(c.isdigit() for c in piece)):
                out.append(piece.upper())
            else:
                return normalize_reservation_number("".join(out))  # a plain word ends the code
    return normalize_reservation_number("".join(out))


def _volunteered_reservation_number(utterances: list[str]) -> str | None:
    for utterance in reversed(utterances):
        m = _RESNUM_PHRASE.search(utterance)
        if m:
            code = _spoken_code(m.group("code"))
            if code:
                return code
    return None


def validate(payload: ToolAvailabilityRequest) -> Availability:
    """Coerce available to bool, parse quoted_rate, trim notes; source='tool'."""
    return Availability(
        candidate_rank=payload.candidate_rank,
        available=bool(payload.available),
        quoted_rate=parse_rate(payload.quoted_rate),
        notes=(payload.notes or "").strip()[:MAX_NOTES],
        source="tool",
        reservation_number=normalize_reservation_number(payload.reservation_number),
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

    utterances = _hotel_utterances(text)
    reservation_number = _volunteered_reservation_number(utterances)
    unknown.reservation_number = reservation_number

    for utterance in reversed(utterances):
        signal, flag = _classify(utterance)
        if flag == "ambiguous":
            return unknown
        if signal is None:
            continue
        rate = None
        if signal:
            # The rate often comes in its own line ("Yes, I have a room." ... "It's $129.").
            # Use the answer's own rate, else the hotel's latest one (corrections win).
            rate = _find_rate(utterance) or next(
                (r for u in reversed(utterances) if (r := _find_rate(u)) is not None), None)
        return Availability(
            candidate_rank=candidate_rank,
            available=signal,
            quoted_rate=rate,
            notes="",
            source="transcript",
            reservation_number=reservation_number,
        )
    return unknown
