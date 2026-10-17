import pytest

import extractor
from contracts import ToolAvailabilityRequest


def tool(**kw) -> ToolAvailabilityRequest:
    base = dict(run_id="r", candidate_rank=2, available=True)
    return ToolAvailabilityRequest(**{**base, **kw})


# --- parse_rate ---------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("$189", 189.0),
    ("189.00", 189.0),
    ("CAD 189/night", 189.0),
    ("about $1,189.50 a night", 1189.5),
    ("  210 dollars ", 210.0),
])
def test_parse_rate_ok(raw, expected):
    assert extractor.parse_rate(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "free", "call us", "$0", "0"])
def test_parse_rate_none(raw):
    assert extractor.parse_rate(raw) is None


# --- validate -----------------------------------------------------------------

def test_validate_full():
    a = extractor.validate(tool(available=True, quoted_rate="$189 a night", notes="  king bed  "))
    assert (a.candidate_rank, a.available, a.quoted_rate, a.notes, a.source) == (2, True, 189.0, "king bed", "tool")


def test_validate_unavailable_no_rate():
    a = extractor.validate(tool(available=False))
    assert a.available is False and a.quoted_rate is None and a.notes == ""


def test_validate_bad_rate_does_not_raise():
    assert extractor.validate(tool(quoted_rate="ask at desk")).quoted_rate is None


def test_validate_notes_capped():
    assert len(extractor.validate(tool(notes="x" * 2000)).notes) == extractor.MAX_NOTES


# --- from_transcript ----------------------------------------------------------

def t(*hotel_lines: str) -> str:
    out = ["Agent: Hi, I'm calling to check room availability please."]
    for line in hotel_lines:
        out += [f"Hotel: {line}", "Agent: Thank you."]
    return "\n".join(out)


def check(text):
    return extractor.from_transcript(text, 3)


def test_yes_with_rate():
    a = check(t("Yes, we have a room at $189 a night."))
    assert a.available is True and a.quoted_rate == 189.0
    assert a.source == "transcript" and a.candidate_rank == 3


def test_sold_out():
    a = check(t("Sorry, we're sold out for those dates."))
    assert a.available is False and a.quoted_rate is None


@pytest.mark.parametrize("line", [
    "We're fully booked that weekend.",
    "There's no availability, I'm afraid.",
    "No vacancy, sorry.",
    "I'm sorry, we don't have any rooms.",
    "We do not have a room for those nights.",
    "That date is not available.",
    "Unfortunately nothing is available.",
    "We're completely full.",
])
def test_negative_phrases(line):
    assert check(t(line)).available is False


@pytest.mark.parametrize("line", [
    "We do have a room for you.",
    "Yes, we have rooms available.",
    "A king room is available for those dates.",
    "We can accommodate two guests, that's 210 dollars per night.",
])
def test_positive_phrases(line):
    assert check(t(line)).available is True


def test_not_available_is_not_positive():
    assert check(t("That's not available.")).available is False


def test_unclear_answer_is_unknown():
    a = check(t("Hmm, I'd have to ask my manager about that.", "Can you call back later?"))
    assert a.available is None and a.source == "transcript"


def test_mixed_in_one_utterance_is_unknown():
    assert check(t("We're sold out Friday but we do have a room Saturday.")).available is None


def test_last_answer_wins():
    text = t("Let me check... yes we have a room.", "Actually sorry, that was the wrong date, we're sold out.")
    assert check(text).available is False


def test_hold_chatter_ignored_before_answer():
    text = t("Thank you for calling, please hold.", "Thanks for holding, we have a room at $159.")
    a = check(text)
    assert a.available is True and a.quoted_rate == 159.0


def test_agent_words_do_not_count():
    text = "Agent: Is a room available?\nHotel: Pardon?\nAgent: Is a room available for Nov 1?"
    assert check(text).available is None


def test_voicemail_is_unknown():
    assert check("Hotel: Please leave a message after the tone.").available is None


def test_continuation_lines_join_speaker():
    text = "Agent: Hi.\nHotel: Let me look.\nWe're sold\nout, sorry."
    assert check(text).available is False


@pytest.mark.parametrize("text", [None, "", "   ", "Agent: hello\n"])
def test_empty_inputs(text):
    assert check(text).available is None


def test_rate_only_taken_when_available():
    assert check(t("Sold out, last night it was $250.")).quoted_rate is None


def test_rate_forms():
    assert check(t("We have a room, 175 dollars.")).quoted_rate == 175.0
    assert check(t("We have a room for CAD 220 a night.")).quoted_rate == 220.0


def test_check_chatter_is_not_an_answer():
    assert check(t("Let me check what's available for you.")).available is None
    assert check(t("One moment, I'll see what rooms are available.")).available is None


def test_check_then_answer_in_one_breath():
    assert check(t("Let me check... yes, we have a room at $99.")).available is True


def test_cant_see_availability():
    assert check(t("I can't see any availability for those dates.")).available is False


def test_price_bounds_are_not_the_rate():
    a = check(t("We don't have anything under $200 but we do have a suite at $320."))
    assert a.available is True and a.quoted_rate == 320.0


def test_no_problem_is_not_negative():
    assert check(t("No problem, we have a room at $99.")).available is True


# --- reservation numbers (only if the hotel volunteers one) --------------------

@pytest.mark.parametrize("raw,expected", [
    ("HX4729", "HX4729"), ("hx 4729", "HX4729"), (" h-x-4729 ", "H-X-4729"),
    ("A1", None), ("", None), (None, None), ("x" * 25, None), ("ab#123", None),
])
def test_normalize_reservation_number(raw, expected):
    assert extractor.normalize_reservation_number(raw) == expected


def test_validate_passes_reservation_number_through():
    a = extractor.validate(tool(reservation_number="hx 4729"))
    assert a.reservation_number == "HX4729"
    assert extractor.validate(tool()).reservation_number is None
    assert extractor.validate(tool(reservation_number="??")).reservation_number is None


@pytest.mark.parametrize("line", [
    "Your reservation number is HX4729.",
    "Your reservation number is H X four seven two nine.",
    "The confirmation number is hotel x-ray 4-7-2-9, thanks.",
    "Reservation number: H X 4 7 2 9",
])
def test_volunteered_number_from_transcript(line):
    a = check(t("Yes we have a room at $189.", line))
    assert a.reservation_number == "HX4729" and a.available is True


def test_number_captured_even_when_availability_unclear():
    a = check(t("Your reservation number is AB1234, anything else?"))
    assert a.reservation_number == "AB1234"


def test_no_number_unless_volunteered():
    assert check(t("Yes, we have a room at $189.")).reservation_number is None
    assert check(t("Sorry, sold out.")).reservation_number is None
    # the agent's own words never count
    text = "Agent: reservation number is ZZ9999\nHotel: Sold out."
    assert check(text).reservation_number is None


def test_rate_in_a_separate_line_from_the_yes():
    # Condensed from a real call (conv_3201m55f…, 2026-10-17): the rate was lost
    text = (
        "Agent: Is there a room available for Monday, November second?\n"
        "Hotel: Uh, sorry, I have nothing available. Yeah, I have a room available.\n"
        "Agent: Okay, so you have a room available for Monday?\n"
        "Hotel: Yep, yep, yep. I have that room available.\n"
        "Agent: Great, and what's the lowest nightly rate you have for that night?\n"
        "Hotel: Lowest nightly rate I have is $2.00.\n"
        "Agent: Just to confirm, you have a room available at two dollars?\n"
        "Hotel: For two dollars. Yep, yep, yep. Confirmed. Yep.\n"
    )
    a = extractor.from_transcript(text, 3)
    assert a.available is True
    assert a.quoted_rate == 2.0


def test_separate_line_rate_latest_wins_and_bounds_skipped():
    text = (
        "Hotel: Yes, we have a room.\n"
        "Hotel: It's $150 a night.\n"
        "Hotel: Sorry, I misread, it's $129 a night.\n"
        "Hotel: Everything here is under $200.\n"
    )
    assert extractor.from_transcript(text, 1).quoted_rate == 129.0
