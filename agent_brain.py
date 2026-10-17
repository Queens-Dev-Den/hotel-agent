"""WS2: prompt parsing, call plans and Backboard fallback extraction.

Backboard is never in the live call loop. Deterministic work (parsing, the
call plan template) costs 0 tokens; Backboard is only hit when the regex
parse fails or a call's record_availability tool never fired.
See docs/ws2-backboard.md.
"""

from __future__ import annotations

import json
import math
import re
import sys
import time
from datetime import date, timedelta

import httpx

import config
from contracts import Availability, CallPlan, CallResult, HotelCandidate, HotelRequest, Run

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


def _usage_add(usage: dict[str, int] | None, tokens_in: int, tokens_out: int, stage: str) -> None:
    """Add to the run totals plus a per-stage total (backboard_parse / backboard_extract) for the deck."""
    if usage is not None:
        usage["backboard_in"] = usage.get("backboard_in", 0) + tokens_in
        usage["backboard_out"] = usage.get("backboard_out", 0) + tokens_out
        usage[f"backboard_{stage}"] = usage.get(f"backboard_{stage}", 0) + tokens_in + tokens_out


def _budget_left(usage: dict[str, int] | None) -> int:
    if usage is None:
        return config.BACKBOARD_TOKEN_BUDGET
    return config.BACKBOARD_TOKEN_BUDGET - usage.get("backboard_in", 0) - usage.get("backboard_out", 0)


def estimate_tokens(text: str) -> int:
    """Rough token count (~4 chars per token). Swap for the model's tokenizer once chosen."""
    return math.ceil(len(text) / 4)


# --- Backboard -----------------------------------------------------------------

def _backboard_post(path: str, payload: dict, timeout: float = 15) -> dict:
    """POST to Backboard -> JSON body. NotImplementedError if no key, httpx.HTTPError on failure."""
    if not config.BACKBOARD_API_KEY:
        raise NotImplementedError("BACKBOARD_API_KEY not set")
    resp = httpx.post(
        f"{config.BACKBOARD_BASE_URL}{path}",
        headers={"X-API-Key": config.BACKBOARD_API_KEY},
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def _backboard_complete(
    system: str, user: str, max_tokens: int, model: str | None = None
) -> tuple[str, int, int]:
    """One Backboard completion -> (text, tokens_in, tokens_out).

    POST /threads/messages auto-creates a thread, so each call is stateless.
    Backboard has no output-token cap param; prompts ask for short JSON instead.
    `model` ("<provider>/<model>") overrides config.BACKBOARD_MODEL.
    """
    provider, _, model = (model or config.BACKBOARD_MODEL).partition("/")
    body = _backboard_post(
        "/threads/messages",
        {
            "content": user,
            "system_prompt": system,
            "llm_provider": provider,
            "model_name": model,
            "json_output": True,
            "memory": "off",
            "stream": False,
        },
    )
    # Billing/model errors come back as HTTP 200 with status FAILED and the reason in content
    if body.get("status") == "FAILED":
        raise httpx.HTTPError(f"Backboard run failed: {body.get('content')}")
    return body.get("content") or "", body.get("input_tokens") or 0, body.get("output_tokens") or 0


def _backboard_json(
    system: str, user: str, max_tokens: int, usage: dict[str, int] | None, stage: str
) -> dict | None:
    if _budget_left(usage) < max_tokens:
        return None
    try:
        text, tokens_in, tokens_out = _backboard_complete(system, user, max_tokens)
    except (NotImplementedError, httpx.HTTPError):
        return None
    _usage_add(usage, tokens_in, tokens_out, stage)
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
    # Descriptions like "the capital of Canada" aren't city names; let Backboard resolve them
    if not city or len(city.split()) > 3 or re.match(r"(?:the|a|an|my|our|some)\b", city, re.I):
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
        raw = _backboard_json(PARSE_SYSTEM.replace("{today}", today.isoformat()), prompt, PARSE_MAX_TOKENS, usage, "parse")
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
- Notes from past calls to this hotel: {{hotel_notes}}
- Phone menu: choose reservations or front desk. If unclear, press 0 or say "front desk".
- Hold music or "please hold": stay silent until a person speaks.
- Ask if a room is available for those dates, and the lowest nightly rate they have. Don't negotiate.
- Never book. If asked for a name, card or to reserve, say: "I'm only checking availability today, thank you."
- If asked, say you are an automated assistant calling on someone's behalf.
- Voicemail or recording: hang up without leaving a message.
- Briefly repeat their answer back and wait for a yes.
- Then call record_availability with available (true/false), quoted_rate if given, and short notes.
- Thank them and end the call.
- One short sentence per turn."""

# Plain string: ElevenLabs fills {{check_in}} / {{check_out}} from dynamic_vars
FIRST_MESSAGE = "Hi, I'm just calling to check if you have a room available from {{check_in}} to {{check_out}}."


def _spoken_date(d: date) -> str:
    return f"{d:%A}, {d:%B} {d.day}"


def build_call_plan(
    request: HotelRequest, candidate: HotelCandidate, run_id: str, hotel_notes: str = ""
) -> CallPlan:
    """Template fill, 0 tokens. Called once per candidate (3 per run).

    `hotel_notes` comes from recall_hotel_notes(); pass "" when there is none.
    """
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
            "hotel_notes": hotel_notes or "none",
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
    raw = _backboard_json(EXTRACT_SYSTEM, transcript[-TRANSCRIPT_TAIL_CHARS:], EXTRACT_MAX_TOKENS, usage, "extract")
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


# --- Agent LLM tokens (ElevenLabs) ---------------------------------------------

def _sum_llm_usage(llm_usage: dict | None) -> tuple[int, int]:
    """LLMCategoryUsage or LLMUsage -> (input incl. cache, output) summed over every model."""
    tokens_in = tokens_out = 0
    if not llm_usage:
        return 0, 0
    # LLMCategoryUsage splits generations into categories; a bare LLMUsage has model_usage directly
    categories = [llm_usage] if "model_usage" in llm_usage else [v for v in llm_usage.values() if isinstance(v, dict)]
    for category in categories:
        for model in (category.get("model_usage") or {}).values():
            for key in ("input", "input_cache_read", "input_cache_write"):
                tokens_in += (model.get(key) or {}).get("tokens", 0)
            tokens_out += (model.get("output_total") or {}).get("tokens", 0)
    return tokens_in, tokens_out


def agent_llm_tokens(conversation: dict) -> int:
    """Total LLM tokens for one call, from GET /v1/convai/conversations/{id}.

    Prefers metadata.charging.llm_usage; falls back to summing transcript[].llm_usage.
    """
    charging = ((conversation.get("metadata") or {}).get("charging") or {}).get("llm_usage")
    tokens_in, tokens_out = _sum_llm_usage(charging)
    if tokens_in + tokens_out == 0:
        for turn in conversation.get("transcript") or []:
            t_in, t_out = _sum_llm_usage(turn.get("llm_usage"))
            tokens_in, tokens_out = tokens_in + t_in, tokens_out + t_out
    return tokens_in + tokens_out


def add_agent_llm_tokens(conversation: dict, usage: dict[str, int] | None) -> int:
    """Add one call's agent LLM tokens to Run.telemetry.tokens["agent_llm"]. Call once per call."""
    tokens = agent_llm_tokens(conversation)
    if usage is not None:
        usage["agent_llm"] = usage.get("agent_llm", 0) + tokens
    return tokens


# --- Memory: learn across runs -------------------------------------------------
# One Backboard assistant holds a memory per hotel call. Memories are written
# by us (0 LLM tokens) and read back before the next call as {{hotel_notes}}.

MEMORY_ASSISTANT_NAME = "hotel-agent-memory"
HOTEL_NOTES_MAX_CHARS = 160  # keeps the agent prompt within budget
_KEY = rf"(?P<key>\d|{'|'.join(NUMBER_WORDS)})"
_DEST = r"(?P<dest>reservations?|front desk|booking)"
# "press 1 for reservations" or "for reservations, press 1"
IVR_PRESS_RES = [
    re.compile(rf"\bpress\s+{_KEY}\b[^.]*?\b{_DEST}", re.I),
    re.compile(rf"\b{_DEST}\b[^.]*?\bpress\s+{_KEY}\b", re.I),
]

_assistant_id: str | None = None


def memory_assistant_id() -> str | None:
    """config.BACKBOARD_ASSISTANT_ID, or create the assistant once and print its id for .env."""
    global _assistant_id
    if _assistant_id is None:
        _assistant_id = config.BACKBOARD_ASSISTANT_ID or None
    if _assistant_id is None:
        try:
            body = _backboard_post("/assistants", {"name": MEMORY_ASSISTANT_NAME})
        except (NotImplementedError, httpx.HTTPError):
            return None
        _assistant_id = body.get("assistant_id")
        print(f"Created Backboard memory assistant. Add to .env: BACKBOARD_ASSISTANT_ID={_assistant_id}")
    return _assistant_id


def _hotel_lines(transcript: str) -> str:
    return "\n".join(line for line in transcript.splitlines() if not line.startswith("Agent:"))


def summarize_call(candidate: HotelCandidate, call: CallResult, on: date) -> str:
    """One-line, deterministic memory for a call. Starts with the phone number so recall can match it."""
    parts = [f"{call.outcome.replace('_', ' ')}"]
    a = call.availability
    if a and a.quoted_rate is not None:
        parts.append(f"quoted ${a.quoted_rate:g}/night")
    hotel_said = _hotel_lines(call.transcript or "")
    ivr = min((m for rx in IVR_PRESS_RES if (m := rx.search(hotel_said))), key=lambda m: m.start(), default=None)
    if ivr:
        parts.append(f"menu: press {_to_int(ivr['key'])} for {ivr['dest'].lower()}")
    if re.search(r"\b(?:please hold|on hold|hold on|hold please)\b", hotel_said, re.I):
        parts.append("put on hold")
    parts.append(f"call {call.duration_s:.0f}s")
    return f"[{candidate.phone_e164}] {candidate.name}, {on.isoformat()}: " + "; ".join(parts)


def remember_run(run: Run) -> int:
    """Store one memory per call in the run. Returns how many were saved; never raises."""
    assistant_id = memory_assistant_id()
    if not assistant_id:
        return 0
    by_rank = {c.rank: c for c in run.candidates}
    saved = 0
    for call in run.calls:
        candidate = by_rank.get(call.candidate_rank)
        if candidate is None:
            continue
        try:
            _backboard_post(
                f"/assistants/{assistant_id}/memories",
                {
                    "content": summarize_call(candidate, call, run.started_at.date()),
                    "metadata": {"phone": candidate.phone_e164, "run_id": run.run_id, "outcome": call.outcome},
                },
            )
            saved += 1
        except (NotImplementedError, httpx.HTTPError):
            continue
    return saved


def recall_hotel_notes(candidate: HotelCandidate, limit: int = 2) -> str:
    """Past-call notes for this hotel, newest first, for {{hotel_notes}}. "" if none or on any error."""
    assistant_id = memory_assistant_id()
    if not assistant_id:
        return ""
    try:
        body = _backboard_post(
            f"/assistants/{assistant_id}/memories/search",
            {"query": f"{candidate.phone_e164} {candidate.name}", "limit": 10},
            timeout=3,
        )
    except (NotImplementedError, httpx.HTTPError):
        return ""
    # Search is semantic, so keep only memories for this exact phone number
    tag = f"[{candidate.phone_e164}]"
    hits = [m for m in body.get("memories", []) if m.get("content", "").startswith(tag)]
    hits.sort(key=lambda m: m.get("created_at") or "", reverse=True)
    notes = " | ".join(m["content"].split(": ", 1)[-1] for m in hits[:limit])
    return notes[:HOTEL_NOTES_MAX_CHARS]


# --- Phase 0 latency check -----------------------------------------------------

def _latency_check(models: list[str]) -> None:
    """python agent_brain.py [provider/model ...]: time one tiny completion per model."""
    for model in models or [config.BACKBOARD_MODEL]:
        start = time.perf_counter()
        try:
            text, tokens_in, tokens_out = _backboard_complete(
                'Reply with JSON only: {"ok": true}', "ping", 20, model=model
            )
        except (NotImplementedError, httpx.HTTPError) as e:
            print(f"{model}: FAILED {e}")
            continue
        ms = (time.perf_counter() - start) * 1000
        print(f"{model}: {ms:.0f} ms, {tokens_in} in / {tokens_out} out -> {text.strip()[:60]}")


if __name__ == "__main__":
    _latency_check(sys.argv[1:])
