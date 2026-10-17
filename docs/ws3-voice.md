# WS3 — ElevenLabs Voice Agent & Telephony

**Owner:** Jordan (T3) · **Module:** `voice_agent.py`

Call handling is 10 of the 30 points: IVR navigation, hold tolerance, natural turn-taking and confirmation extraction.

> **First 10 minutes:** start getting the outbound phone number. It has the longest lead time and blocks almost everything else.

## Deliverables

- An ElevenLabs Conversational AI agent, configured as described below.
- `start_call(candidate, call_plan) -> conversation_id`: outbound call with `dynamic_vars` injected.
- `await_outcome(conversation_id) -> CallOutcome`: `booked` / `voicemail` / `no_answer` / `refused` / `error`.
- The `record_confirmation` **server tool**, defined on the agent and pointing at WS4's endpoint.
- A **mock hotel** agent for testing, built together with T4.

## Agent configuration

| Setting | Value | Why |
|---|---|---|
| LLM | Fastest cheap model available in ElevenLabs | Turn latency matters more than reasoning depth |
| Turn detection | Default/responsive (**not** a 2.5 s pause) | Natural turn-taking is scored |
| Silence / end-call timeout | 90–120 s | Survives hold music |
| Max call duration | 240 s | Stops a runaway call |
| Voicemail detection | On, if available (verify in the console) | Triggers failover |
| DTMF / keypad tool | On, if available (verify) | IVR navigation |
| First message | Short: "Hi, I'd like to make a reservation please." | Gets straight past a human greeting |

## Agent prompt outline (≤ 300 tokens, T2 owns the token count)

1. **Role:** booking a room for `{{guest_name}}`, `{{check_in}}` to `{{check_out}}`, `{{guests}}` guest(s).
2. **IVR:** if a menu plays, choose reservations or the front desk. If unclear, press 0 or say "front desk".
3. **Hold:** if you hear hold music or "please hold", stay silent until a person speaks.
4. **Booking:** ask for the cheapest available room for those dates. Accept it. Don't negotiate.
5. **Payment:** if they ask for a card, say "The guest will pay at check-in." If they refuse, politely end the call.
6. **Readback:** repeat the reservation number back letter by letter using phonetic words ("A as in Alpha"). Wait for a yes.
7. **Tool:** after a confirmed readback, call `record_confirmation` with `reservation_number` and `reservation_date`.
8. **Close:** thank them and end the call.
9. **Style:** one short sentence per turn.

## Phase tasks

### Phase 0 (09:45–10:30)
- [ ] Claim ElevenLabs credits through the hackathon Discord bot.
- [ ] **Outbound number:** import a Twilio number into ElevenLabs, or set up a SIP trunk.
- [ ] Gate: an outbound test call rings a teammate's phone.

### Phase 1 (10:30–13:00)
- [ ] Build the agent with the settings above, using T2's prompt template.
- [ ] Define the `record_confirmation` server tool. URL from T4.
- [ ] **Mock hotel** (with T4): a second ElevenLabs agent on a teammate's number that plays a fake IVR ("press 1 for reservations"), puts the caller on hold for 15 s, then gives a code like `HX4729`.
- [ ] **12:30:** walking skeleton, where the call to the mock hotel fires the tool and the email arrives.
- [ ] Gate: one call to the mock hotel produces a correct tool payload.

### Phase 2 (13:30–15:30)
- [ ] `start_call` takes `dynamic_vars` from `call_plan`.
- [ ] `await_outcome` classifies the call from the webhook or by polling the conversation status.

### Phase 3 (15:30–16:45)
- [ ] Failover tests: voicemail, no answer, refusal. Each should move to candidate #2 within ≤ 45 s.
- [ ] Hold tests at 30, 60 and 90 s. The agent must stay silent and not hang up.
- [ ] Garbled-code test: the mock hotel spells the code fast. The readback must catch it.
- [ ] Record dial-to-answer and per-turn latency for the deck (PSTN vs SIP if both were tried).

## Done when

- The mock-hotel call navigates the IVR, survives the hold, reads back the code and fires the tool, 3 times out of 3.
- Voicemail is detected and fails over in ≤ 45 s.
