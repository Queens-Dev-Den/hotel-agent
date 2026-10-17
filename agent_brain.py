"""WS2: prompt parsing, call plans and Backboard fallback extraction.

Backboard is never in the live call loop. Deterministic work (parsing, the
call plan template) costs 0 tokens; Backboard is only hit when the regex
parse fails or a call's record_availability tool never fired.
See docs/ws2-backboard.md.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from datetime import date, timedelta

import httpx

from contracts import Availability, CallPlan, HotelCandidate, HotelRequest

BACKBOARD_TOKEN_BUDGET = 1200  # per session, see docs/architecture.md
PARSE_MAX_TOKENS = 150
EXTRACT_MAX_TOKENS = 300
MAX_PROMPT_TOKENS = 300

MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"),
            ("apr", "april"), ("may",), ("jun", "june"), ("jul", "july"),
            ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"),
            ("nov", "november"), ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

_MONTH = r"(?P<{p}month>" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?"
_DAY = r"(?P<{p}day>\d{{1,2}})(?:st|nd|rd|th)?"
_NUM = r"(\d+|" + "|".join(NUMBER_WORDS) + r")"

# "Nov 2", "November 2nd", "2 Nov", "2nd of November"
_MONTH_DAY = (_MONTH + r"\s+" + _DAY).format(p="a")
_DAY_MONTH = (_DAY + r"\s+(?:of\s+)?" + _MONTH).format(p="b")
DATE_RE = re.compile(
    rf"\b(?P<iso>\d{{4}}-\d{{2}}-\d{{2}})\b|\b{_MONTH_DAY}\b|\b{_DAY_MONTH}\b", re.I
)
# "Nov 2-4" / "Nov 2 to 4": same-month range, end day only
SAME_MONTH_RANGE_RE = re.compile(
    rf"\b{_MONTH_DAY.replace('?P<a', '?P<r')}\s*(?:-|–|to|until|through)\s*(?P<end>\d{{1,2}})(?:st|nd|rd|th)?\b(?!\s*(?:people|guests?|adults?|persons?|nights?))",
    re.I,
)
CITY_RE = re.compile(
    r"\b(?:in|near|around)\s+(?P<city>[a-z][a-z .'-]*?)"
    r"(?=\s+(?:for|on|from|tonight|today|tomorrow|this|next|between|starting|with|checking|"
    + "|".join(MONTHS)
    + r")\b|\s+\d|[,.!?]|$)",
    re.I,
)
GUESTS_RE = re.compile(rf"\b{_NUM}\s+(?:people|persons?|guests?|adults?|of us)\b", re.I)
NIGHTS_RE = re.compile(rf"\b{_NUM}\s+nights?\b", re.I)


def _to_int(token: str) -> int:
    return int(token) if token.isdigit() else NUMBER_WORDS[token.lower()]


def _resolve(month: int, day: int, today: date) -> date:
    """Month/day with no year -> the next occurrence on or after today."""
    d = date(today.year, month, day)
    return d if d >= today else date(today.year + 1, month, day)


def _find_dates(text: str, today: date) -> list[date]:
    found = []
    for m in DATE_RE.finditer(text):
        if m["iso"]:
            found.append(date.fromisoformat(m["iso"]))
        else:
            month = MONTHS[(m["amonth"] or m["bmonth"]).lower()]
            found.append(_resolve(month, int(m["aday"] or m["bday"]), today))
    return found


def _usage_add(usage: dict[str, int] | None, tokens_in: int, tokens_out: int) -> None:
    if usage is not None:
        usage["backboard_in"] = usage.get("backboard_in", 0) + tokens_in
        usage["backboard_out"] = usage.get("backboard_out", 0) + tokens_out


def _budget_left(usage: dict[str, int] | None) -> int:
    if usage is None:
        return BACKBOARD_TOKEN_BUDGET
    return BACKBOARD_TOKEN_BUDGET - usage.get("backboard_in", 0) - usage.get("backboard_out", 0)


def estimate_tokens(text: str) -> int:
    """Rough token count (~4 chars per token). Swap for the model's tokenizer once chosen."""
    return math.ceil(len(text) / 4)


# --- Backboard -----------------------------------------------------------------

BACKBOARD_DEFAULT_URL = "https://app.backboard.io/api"
BACKBOARD_DEFAULT_MODEL = "openai/gpt-4o-mini"  # "<llm_provider>/<model_name>"


def _backboard_complete(system: str, user: str, max_tokens: int) -> tuple[str, int, int]:
    """One Backboard completion -> (text, tokens_in, tokens_out).

    POST /threads/messages auto-creates a thread, so each call is stateless.
    Backboard has no output-token cap param; prompts ask for short JSON instead.
    TODO: read BACKBOARD_* from config.py once it exists on main.
    """
    api_key = os.environ.get("BACKBOARD_API_KEY")
    if not api_key:
        raise NotImplementedError("BACKBOARD_API_KEY not set")
    base_url = os.environ.get("BACKBOARD_BASE_URL") or BACKBOARD_DEFAULT_URL
    provider, _, model = (os.environ.get("BACKBOARD_MODEL") or BACKBOARD_DEFAULT_MODEL).partition("/")

    resp = httpx.post(
        f"{base_url.rstrip('/')}/threads/messages",
        headers={"X-API-Key": api_key},
        json={
            "content": user,
            "system_prompt": system,
            "llm_provider": provider,
            "model_name": model,
            "json_output": True,
            "memory": "off",
            "stream": False,
        },
        timeout=15,
    )
    resp.raise_for_status()
    body = resp.json()
    # Billing/model errors come back as HTTP 200 with status FAILED and the reason in content
    if body.get("status") == "FAILED":
        raise httpx.HTTPError(f"Backboard run failed: {body.get('content')}")
    return body.get("content") or "", body.get("input_tokens") or 0, body.get("output_tokens") or 0


def _backboard_json(system: str, user: str, max_tokens: int, usage: dict[str, int] | None) -> dict | None:
    if _budget_left(usage) < max_tokens:
        return None
    try:
        text, tokens_in, tokens_out = _backboard_complete(system, user, max_tokens)
    except (NotImplementedError, httpx.HTTPError):
        return None
    _usage_add(usage, tokens_in, tokens_out)
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None


# --- parse_prompt --------------------------------------------------------------

PARSE_SYSTEM = (
    "Extract a hotel search from the user's text. Reply with JSON only: "
    '{"city": str, "check_in": "YYYY-MM-DD"|null, "check_out": "YYYY-MM-DD"|null, "guests": int|null}. '
    "Today is {today}."
)


def _regex_parse(prompt: str, today: date) -> dict | None:
    city_m = CITY_RE.search(prompt)
    if not city_m:
        return None
    city = city_m["city"].strip(" .'-")
    if not city:
        return None
    # Rest of the prompt minus the city, so a city like "Mayfair" can't match a month
    rest = prompt[: city_m.start("city")] + " " + prompt[city_m.end("city"):]
    lower = rest.lower()

    check_in = check_out = None
    range_m = SAME_MONTH_RANGE_RE.search(rest)
    if range_m:
        month = MONTHS[range_m["rmonth"].lower()]
        check_in = _resolve(month, int(range_m["rday"]), today)
        check_out = date(check_in.year, month, int(range_m["end"]))
    else:
        dates = _find_dates(rest, today)
        if dates:
            check_in = dates[0]
            if len(dates) > 1 and dates[1] > check_in:
                check_out = dates[1]
    if check_in is None:
        if re.search(r"\b(?:tonight|today)\b", lower):
            check_in = today
        elif re.search(r"\btomorrow\b", lower):
            check_in = today + timedelta(days=1)
    if check_out is None and check_in is not None:
        nights_m = NIGHTS_RE.search(rest)
        if nights_m:
            check_out = check_in + timedelta(days=_to_int(nights_m.group(1)))

    guests_m = GUESTS_RE.search(rest)
    return {
        "city": city if city != city.lower() else city.title(),
        "check_in": check_in,
        "check_out": check_out,
        "guests": _to_int(guests_m.group(1)) if guests_m else None,
    }


def parse_prompt(
    prompt: str,
    *,
    today: date | None = None,
    callback_email: str = "",
    usage: dict[str, int] | None = None,
) -> HotelRequest:
    """Prompt -> HotelRequest. Regex first (0 tokens), Backboard only if no city is found.

    `usage` is Run.telemetry.tokens; Backboard tokens are added to it in place.
    Defaults: check-in tomorrow, 1 night, 1 guest.
    """
    today = today or date.today()
    fields = _regex_parse(prompt, today)
    if fields is None:
        raw = _backboard_json(PARSE_SYSTEM.replace("{today}", today.isoformat()), prompt, PARSE_MAX_TOKENS, usage)
        if not raw or not raw.get("city"):
            raise ValueError(f"Could not find a city in prompt: {prompt!r}")
        fields = {
            "city": str(raw["city"]).strip(),
            "check_in": date.fromisoformat(raw["check_in"]) if raw.get("check_in") else None,
            "check_out": date.fromisoformat(raw["check_out"]) if raw.get("check_out") else None,
            "guests": int(raw["guests"]) if raw.get("guests") else None,
        }

    check_in = fields["check_in"] or today + timedelta(days=1)
    check_out = fields["check_out"]
    if check_out is None or check_out <= check_in:
        check_out = check_in + timedelta(days=1)
    return HotelRequest(
        city=fields["city"],
        check_in=check_in,
        check_out=check_out,
        guests=fields["guests"] or 1,
        callback_email=callback_email,
    )


# --- build_call_plan -----------------------------------------------------------

# ElevenLabs fills {{var}} from dynamic_vars. The record_availability tool
# should take run_id and candidate_rank from the same dynamic variables.
SYSTEM_PROMPT = """\
You are calling {{hotel_name}} to check room availability for {{guests}} guest(s), \
checking in {{check_in}} and out {{check_out}} ({{nights}} night(s)). You are NOT booking.
- Phone menu: choose reservations or front desk. If unclear, press 0 or say "front desk".
- Hold music or "please hold": stay silent until a person speaks.
- Ask if a room is available for those dates, and the nightly rate. Don't negotiate.
- Never book. If asked for a name, card or to reserve, say: "I'm only checking availability today, thank you."
- If asked, say you are an automated assistant calling on someone's behalf.
- Voicemail or recording: hang up without leaving a message.
- Briefly repeat their answer back and wait for a yes.
- Then call record_availability with available (true/false), quoted_rate if given, and short notes.
- Thank them and end the call.
- One short sentence per turn."""

FIRST_MESSAGE = "Hi, I'm calling to check room availability please."


def _spoken_date(d: date) -> str:
    return f"{d:%A}, {d:%B} {d.day}"


def build_call_plan(request: HotelRequest, candidate: HotelCandidate, run_id: str) -> CallPlan:
    """Template fill, 0 tokens. Called once per candidate (3 per run)."""
    nights = (request.check_out - request.check_in).days
    return CallPlan(
        run_id=run_id,
        candidate=candidate,
        system_prompt=SYSTEM_PROMPT,
        first_message=FIRST_MESSAGE,
        dynamic_vars={
            "run_id": run_id,
            "candidate_rank": str(candidate.rank),
            "hotel_name": candidate.name,
            "check_in": _spoken_date(request.check_in),
            "check_out": _spoken_date(request.check_out),
            "nights": str(nights),
            "guests": str(request.guests),
        },
        prompt_tokens=estimate_tokens(SYSTEM_PROMPT),
    )


# --- fallback_extract ----------------------------------------------------------

EXTRACT_SYSTEM = (
    "From this phone call transcript, did the hotel say a room is available for the requested dates? "
    'Reply with JSON only: {"available": true|false|null, "quoted_rate": number|null, "notes": str}. '
    "Use null when the transcript doesn't say."
)
TRANSCRIPT_TAIL_CHARS = 800  # the answer is near the end; keeps input well under budget


def fallback_extract(
    transcript: str,
    candidate_rank: int,
    *,
    usage: dict[str, int] | None = None,
) -> Availability | None:
    """Backboard extraction for a call whose tool never fired and whose regex fallback failed.

    Returns None when Backboard is unavailable, over budget, or the reply is unusable.
    """
    if not transcript.strip():
        return None
    raw = _backboard_json(EXTRACT_SYSTEM, transcript[-TRANSCRIPT_TAIL_CHARS:], EXTRACT_MAX_TOKENS, usage)
    if raw is None or "available" not in raw:
        return None
    available = raw["available"]
    rate = raw.get("quoted_rate")
    return Availability(
        candidate_rank=candidate_rank,
        available=available if isinstance(available, bool) else None,
        quoted_rate=float(rate) if isinstance(rate, (int, float)) else None,
        notes=str(raw.get("notes") or "")[:200],
        source="backboard",
    )


# --- Phase 0 latency check -----------------------------------------------------

def _latency_check(models: list[str]) -> None:
    """python agent_brain.py [provider/model ...]: time one tiny completion per model."""
    from dotenv import load_dotenv

    load_dotenv(".env")
    for model in models or [os.environ.get("BACKBOARD_MODEL") or BACKBOARD_DEFAULT_MODEL]:
        os.environ["BACKBOARD_MODEL"] = model
        start = time.perf_counter()
        try:
            text, tokens_in, tokens_out = _backboard_complete(
                'Reply with JSON only: {"ok": true}', "ping", 20
            )
        except (NotImplementedError, httpx.HTTPError) as e:
            print(f"{model}: FAILED {e}")
            continue
        ms = (time.perf_counter() - start) * 1000
        print(f"{model}: {ms:.0f} ms, {tokens_in} in / {tokens_out} out -> {text.strip()[:60]}")


if __name__ == "__main__":
    _latency_check(sys.argv[1:])
