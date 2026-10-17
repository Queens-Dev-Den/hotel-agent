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
    (?:can['’]?t|cannot|unable\s+to)\s+(?:see|find)\s+any |
    (?:have|got)\s+nothing(?:\s+(?:is\s+)?(?:available|left))?
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

# An answering machine's greeting is not an answer ("the person you're trying to reach is not available").
_VOICEMAIL = re.compile(
    r"leave\s+(?:a|your)\s+message|at\s+the\s+(?:tone|beep)|after\s+the\s+(?:tone|beep)|record\s+your\s+message|"
    r"voice\s*mail|(?:person|party|number)\s+you(?:['’]re|\s+are)\s+(?:trying\s+to\s+reach|calling)",
    re.IGNORECASE,
)
# A quoted price is a yes when it's offered: "the cheapest one I have is $50", "we've got a king for $120",
# "Yes, $149 a night".
_OFFER = re.compile(
    r"\b(?:yes|yeah|yep|sure|cheapest|lowest|best\s+(?:rate|price)|(?:i|we)(?:['’]ve|\s+have)?\s+got|(?:i|we)\s+(?:do\s+)?have|there['’]s\s+(?:one|a))\b",
    re.IGNORECASE,
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
    has_positive = bool(_POSITIVE.search(remainder)) or (
        bool(_OFFER.search(remainder)) and _rate_in(remainder) is not None
    )
    if has_negative and has_positive:
        return None, "ambiguous"
    if has_negative:
        return False, None
    if has_positive:
        return True, None
    return None, None


_CEILING = re.compile(r"(?:under|below|less\s+than|up\s+to|over|above|more\s+than)\s*$", re.IGNORECASE)

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
_NUMBER_WORD = "|".join(sorted([*_UNITS, *_TENS, "hundred"], key=len, reverse=True))
# 'two dollars', 'a hundred and fifty bucks', 'one eighty-nine a night'
_SPOKEN_RATE = re.compile(
    rf"\b((?:a\s+)?(?:(?:{_NUMBER_WORD}|and)[\s-]+)*(?:{_NUMBER_WORD}))\s*(?:dollars?|bucks|cad|a\s+night|per\s+night|/\s*night)",
    re.IGNORECASE,
)


def _words_to_number(phrase: str) -> int | None:
    """'one eighty nine' -> 189, 'a hundred and fifty' -> 150, 'two' -> 2, 'twenty one' -> 21."""
    tokens = [t for t in re.split(r"[\s-]+", phrase.lower()) if t and t not in ("a", "and")]
    if not tokens:
        return None
    if "hundred" not in tokens:
        rest = _sum_tokens(tokens[1:])
        if tokens[0] in _UNITS and 1 <= _UNITS[tokens[0]] <= 9 and rest is not None and rest >= 10:
            return _UNITS[tokens[0]] * 100 + rest  # colloquial: 'one eighty nine'
        return _sum_tokens(tokens)
    total = current = 0
    for t in tokens:
        if t == "hundred":
            current = (current or 1) * 100
        elif t in _UNITS or t in _TENS:
            current += _UNITS.get(t, _TENS.get(t, 0))
        else:
            return None
    return total + current


def _sum_tokens(tokens: list[str]) -> int | None:
    if not tokens:
        return 0
    if any(t not in _UNITS and t not in _TENS for t in tokens):
        return None
    return sum(_UNITS.get(t, _TENS.get(t, 0)) for t in tokens)


def _rate_in(utterance: str) -> float | None:
    """A nightly rate in one utterance: digits ('$2.00', '189 a night') or words ('two dollars')."""
    for m in _RATE.finditer(utterance):
        if _CEILING.search(utterance[: m.start()]):
            continue
        value = parse_rate(m.group(1) or m.group(2))
        if value is not None:
            return value
    for m in _SPOKEN_RATE.finditer(utterance):
        if _CEILING.search(utterance[: m.start()]):
            continue
        value = _words_to_number(m.group(1))
        if value:
            return float(value)
    return None


def _find_rate(utterances: list[str]) -> float | None:
    """The last rate the hotel mentioned: the price is often a different sentence from 'yes'."""
    for utterance in reversed(utterances):
        rate = _rate_in(utterance)
        if rate is not None:
            return rate
    return None


def from_transcript(text: str | None, candidate_rank: int) -> Availability:
    """Scan Hotel: lines. The last utterance that answers wins; unclear -> available=None."""
    unknown = Availability(candidate_rank=candidate_rank, available=None, source="transcript")
    if not text:
        return unknown

    utterances = _hotel_utterances(text)
    if any(_VOICEMAIL.search(u) for u in utterances):
        return unknown
    reservation_number = _volunteered_reservation_number(utterances)
    unknown.reservation_number = reservation_number

    for utterance in reversed(utterances):
        signal, flag = _classify(utterance)
        if flag == "ambiguous":
            return unknown
        if signal is None:
            continue
        return Availability(
            candidate_rank=candidate_rank,
            available=signal,
            quoted_rate=_find_rate(utterances) if signal else None,
            notes="",
            source="transcript",
            reservation_number=reservation_number,
        )
    return unknown
