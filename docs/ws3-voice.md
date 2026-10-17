# WS3 — ElevenLabs Voice Agent & Telephony

**Owner:** Jordan (T3) · **Module:** `voice_agent.py`

Call handling is 10 of the 30 points: IVR navigation, hold tolerance, natural turn-taking and extraction. This workstream now runs **three calls at the same time**, and the agent **only checks availability. It never books.**

> **First 10 minutes:** start getting the outbound phone number, and find out how many **concurrent** calls it and the ElevenLabs plan allow. We need 3 at once. This has the longest lead time and blocks almost everything else.

## Deliverables

- An ElevenLabs Conversational AI agent, configured as described below. One agent config, used for all 3 calls.
- `place_call(plan) -> CallResult`: places the outbound call with `dynamic_vars` injected, waits for it to end, and returns the outcome **with the full transcript**. `pipeline.py` runs three of these concurrently.
- Outcome classes: `available` / `unavailable` / `voicemail` / `no_answer` / `error`.
- Transcript retrieval from the ElevenLabs conversation API once the call ends, formatted as plain text with `Agent:` and `Hotel:` speaker labels. The post-call webhook is the fallback.
- The `record_availability` **server tool**, defined on the agent and pointing at WS4's endpoint.
- **Mock hotel** agent(s) for testing, built together with T4.

## Agent configuration

| Setting | Value | Why |
|---|---|---|
| LLM | Fastest cheap model available in ElevenLabs | Turn latency matters more than reasoning depth |
| Turn detection | Default/responsive (**not** a 2.5 s pause) | Natural turn-taking is scored |
| Silence / end-call timeout | 90–120 s | Survives hold music |
| Max call duration | 180 s | Stops a runaway call and keeps the run under 4 minutes |
| Voicemail detection | On, if available (verify in the console) | Ends the call early, recorded as `voicemail` |
| DTMF / keypad tool | On, if available (verify) | IVR navigation |
| First message | Short: "Hi, I'm calling to check room availability please." | Gets straight past a human greeting |
| Concurrency | At least 3 simultaneous calls | All hotels are dialled at once |

## Agent prompt outline (≤ 300 tokens, T2 owns the token count)

1. **Role:** checking whether a room is **available** for `{{check_in}}` to `{{check_out}}`, `{{guests}}` guest(s). Not booking.
2. **IVR:** if a menu plays, choose reservations or the front desk. If unclear, press 0 or say "front desk".
3. **Hold:** if you hear hold music or "please hold", stay silent until a person speaks.
4. **Question:** ask whether they have a room available for those dates, and the nightly rate if they'll say. Don't negotiate.
5. **No booking:** never reserve. If they ask for a name, card or to confirm a booking, say "I'm only checking availability today, thank you." Don't offer payment details.
6. **Honesty:** if asked, say you're an automated assistant calling on someone's behalf.
7. **Repeat back:** briefly repeat the answer ("So a room is available at $X a night") and wait for a yes.
8. **Tool:** after the answer, call `record_availability` with `available` (true/false), optional `quoted_rate`, and a short `notes`.
9. **Close:** thank them and end the call.
10. **Style:** one short sentence per turn.

## Phase tasks

### Phase 0 (09:45–10:30)
- [ ] Claim ElevenLabs credits through the hackathon Discord bot.
- [ ] **Outbound number:** import a Twilio number into ElevenLabs, or set up a SIP trunk. **Confirm it supports 3 concurrent calls.**
- [ ] Gate: an outbound test call rings a teammate's phone.

### Phase 1 (10:30–13:00)
- [ ] Build the agent with the settings above, using T2's prompt template.
- [ ] Define the `record_availability` server tool. URL from T4.
- [ ] **Mock hotel** (with T4): an ElevenLabs agent that plays a fake IVR ("press 1 for reservations"), puts the caller on hold for 15 s, then answers "yes, we have a room at $189" or "sorry, we're sold out". It must take **3 calls at once**, using either three numbers or one agent with enough concurrency.
- [ ] **12:30:** walking skeleton, where calls to the mock hotel fire the tool and one email arrives with the results and transcripts.
- [ ] Gate: one call to the mock hotel produces a correct tool payload and a retrievable transcript.

### Phase 2 (13:30–15:30)
- [ ] `place_call` takes `dynamic_vars` from `call_plan` and is safe to run in parallel (no shared mutable state).
- [ ] Outcome classification from the webhook or by polling the conversation status.
- [ ] Transcript fetch, with a retry because the transcript can lag the call end by a few seconds.

### Phase 3 (15:30–16:45)
- [ ] Mixed-outcome test: three simultaneous calls where one is available, one sold out and one voicemail.
- [ ] Voicemail and no-answer tests: each is recorded as such within ≤ 45 s without blocking the other two calls.
- [ ] Hold tests at 30, 60 and 90 s. The agent must stay silent and not hang up.
- [ ] Booking-pressure test: the mock hotel asks for a card or a name, and the agent declines and doesn't book.
- [ ] Record dial-to-answer and per-turn latency for the deck (PSTN vs SIP if both were tried).

## Done when

- Three simultaneous calls to the mock hotel each navigate the IVR, survive the hold, get an availability answer, fire the tool and return a full transcript, 3 times out of 3.
- Voicemail is detected and ends the call in ≤ 45 s.
- The agent never asks for or offers payment details, and never books.
