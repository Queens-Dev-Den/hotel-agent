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

## Live setup (as built, Oct 17)

This is what is actually configured in ElevenLabs and Twilio. If the agent breaks or another laptop runs the demo, rebuild from here.

### Code
- `voice_agent.place_call(plan)`: Twilio outbound call (`POST /v1/convai/twilio/outbound-call`) with the plan's prompt and first message as overrides and `dynamic_vars`, then polls `GET /v1/convai/conversations/{id}` every 2 s until `done`/`failed`, retrying up to 4 times while the transcript is empty.
- Outcome: `voicemail` (voicemail tool or termination reason) → `no_answer` (hotel never spoke) → `available` / `unavailable` from the tool result, falling back to `extractor.from_transcript`. A person who answered without a clear yes is `unavailable` with `availability=None`. Still running after `MAX_CALL_DURATION_S + TRANSCRIPT_WAIT_S` is `error`.
- `voice_agent.conversation_details(id)` returns the raw conversation for the agent LLM token count.
- Settings live in the WS3 section of `config.py`. Tests: `tests/test_voice.py` (fake API) and `tests/test_voice_contract.py`.

### Agent: `Hotel Availability Caller`
| Setting | Value |
|---|---|
| LLM | `deepseek-v41-flash`, temperature 0.25, max 150 tokens, backup LLM default |
| Prompt / first message | From `agent_brain.SYSTEM_PROMPT` / `FIRST_MESSAGE`. **Overrides are on for both**, so code sends them on every call; the dashboard copy only matters for dashboard test calls |
| Listen first | `VOICE_LISTEN_FIRST` (default on): `voice_agent` sends an empty first message and adds a prompt line to say `FIRST_MESSAGE` after the hotel speaks. Dashboard first message is empty to match |
| Call rules | `voice_agent.CALL_RULES` appended to every prompt: brand or central-reservations names aren't a wrong number; wait on cut-off or unclear speech and ask to repeat only once; a lone "bye" at pickup is probably misheard. Prompt is ~412 tokens in total |
| Dynamic variables | `hotel_name`, `guests`, `check_in`, `check_out`, `nights`, `hotel_notes`, `run_id`, `candidate_rank` (test values set in the dashboard) |
| Built-in tools | `end_call`, `skip_turn` (silent on hold), `play_keypad_touch_tone` (IVR), `voicemail_detection` (hang up, no message) |
| Custom tool | `record_availability`, see below |
| Voice | `eleven_v4_turbo`, streaming latency 3, stability 0.5, speed 1.0 |
| Turn | Eagerness **patient** (normal jumped in on half sentences), turn timeout 10 s, silence end-call 90 s, soft timeout off |
| ASR keywords | `press, reservations, front desk, available, sold out, per night, hold, extension, hello, hi` ("press" was being heard as "trust") |
| Max call | 180 s |
| Limits | Concurrency 4 (bursting on), 300 calls/day |
| Privacy | Transcripts and audio kept 7 days, zero-retention **off** (transcripts must be fetchable) |

### `record_availability` tool
- Definition: [`agents/booking_tool.json`](../agents/booking_tool.json). `run_id` and `candidate_rank` come from dynamic variables, so the tool server can tell the 3 parallel calls apart; the other fields come from the LLM.
- URL `https://aloha-derail-elixir.ngrok-free.dev/record_availability`, header `X-Tool-Secret` = `TOOL_SHARED_SECRET`.
- **The laptop running the app must run the tunnel** on that domain with the domain owner's ngrok authtoken: `ngrok http --domain=aloha-derail-elixir.ngrok-free.dev 8000` (older ngrok: `--domain`; newer: `--url`). Without it the tool call fails and results come from the transcript fallback only.
- To recreate it (e.g. new domain or secret), fill the placeholders from `.env`, create it, then attach the returned id to the agent:

```bash
python -c "import json,config,httpx; b=open('agents/booking_tool.json').read().replace('{PUBLIC_BASE_URL}',config.PUBLIC_BASE_URL).replace('{TOOL_SHARED_SECRET}',config.TOOL_SHARED_SECRET); r=httpx.post(config.ELEVENLABS_BASE_URL+'/v1/convai/tools',headers={'xi-api-key':config.ELEVENLABS_API_KEY},json=json.loads(b)); print(r.status_code, r.json().get('id'))"
```
Then in the dashboard (agent → Tools) or via `PATCH /v1/convai/agents/{id}` with `conversation_config.agent.prompt.tool_ids`, attach it and publish to **main**.

### Telephony
- Twilio paid account, number imported into ElevenLabs and assigned to the agent (`ELEVENLABS_AGENT_PHONE_NUMBER_ID`).
- **Twilio Geo permissions: Canada must be enabled** (Voice → Settings → Geo permissions). Without it every call to a Canadian number fails with "Account not authorized to call".
- Test phones are entered in the UI's test-phones box; `USE_MOCK_HOTEL` keeps real runs on our own phones.
