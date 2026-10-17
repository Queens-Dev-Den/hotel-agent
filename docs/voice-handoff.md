# Voice handoff (WS3 → everyone)

**For:** Jordan · **From:** Zach · Everything except `voice_agent.py` is on `main` and runs end to end with voice faked (`MOCK_MODULES=voice`). This page is the full list of what the rest of the pipeline expects from the voice side.

## 1. The one function: `voice_agent.place_call(plan) -> CallResult`

`pipeline._call_all` calls it **3 times at once** from a thread pool, one per hotel, and caps each at `MAX_CALL_DURATION_S + 30` s. So:

- **Block until the call has ended** and the transcript is fetched, then return. Don't return early.
- **Thread-safe:** no shared mutable state between calls.
- **Dial `plan.candidate.phone_e164` as given.** The pipeline has already swapped in `MOCK_HOTEL_PHONES` when `USE_MOCK_HOTEL=true`.
- **Raising is OK.** The pipeline turns an exception into `outcome="error"` for that hotel and carries on.

Start the call with `ELEVENLABS_AGENT_ID` + `ELEVENLABS_AGENT_PHONE_NUMBER_ID`, sending:

| From the plan | Goes to |
|---|---|
| `plan.system_prompt` | Agent prompt (if overriding per call; otherwise set it once in the console from `agent_brain.SYSTEM_PROMPT`) |
| `plan.first_message` | First message |
| `plan.dynamic_vars` | Dynamic variables: `run_id`, `candidate_rank`, `hotel_name`, `check_in`, `check_out`, `nights`, `guests`, **`hotel_notes`** |

`hotel_notes` is new: the "Notes from past calls" line in the prompt. It's always set (`"none"` when there's no history), so add it to the agent's dynamic variables or ElevenLabs will reject the call.

## 2. What to return

| `CallResult` field | Rule |
|---|---|
| `candidate_rank` | `plan.candidate.rank` |
| `conversation_id` | The ElevenLabs conversation id (needed for transcripts and token usage) |
| `outcome` | `available` / `unavailable` (a person answered) · `voicemail` · `no_answer` · `error` |
| `duration_s` | Call length in seconds |
| `availability` | Optional. The tool result if you have it; Isaac's tool server already stores it, so `None` is fine |
| `transcript` | **Required whenever a person answered.** One line per turn, `Agent: ...` for our agent and `Hotel: ...` for the other side (ElevenLabs role `user`). Isaac's regex fallback only reads `Hotel:` lines, and the email prints it verbatim |

The transcript can lag the call end by a few seconds, so retry the fetch (`GET /v1/convai/conversations/{id}`) until it has turns.

## 3. Token count (one line)

`place_call` never sees the `Run`, so the pipeline adds the agent's LLM tokens after the calls end. Jordan exposes the conversation fetch, and Richard adds two lines after `_call_all` in `pipeline.run`. No contract change is needed:

```python
# voice_agent.py (Jordan): the same GET place_call already uses for the transcript
def conversation_details(conversation_id: str) -> dict: ...

# pipeline.py (Richard), after r.calls = _call_all(...)
for c in r.calls:
    if c.conversation_id:
        add_agent_llm_tokens(voice_agent.conversation_details(c.conversation_id), r.telemetry.tokens)
```

`add_agent_llm_tokens` (in `agent_brain`) reads `metadata.charging.llm_usage` and falls back to per-turn `llm_usage`.

## 4. The `record_availability` server tool (ElevenLabs console)

- **URL:** `{PUBLIC_BASE_URL}/record_availability`, method `POST`, header `X-Tool-Secret: {TOOL_SHARED_SECRET}`. The tool server starts automatically inside `pipeline.run` on `TOOL_SERVER_PORT` (8000); point ngrok/cloudflared at it.
- **Body (JSON, `contracts.ToolAvailabilityRequest`):**

| Param | Type | Source |
|---|---|---|
| `run_id` | string | Dynamic variable `{{run_id}}` |
| `candidate_rank` | integer | Dynamic variable `{{candidate_rank}}` |
| `available` | boolean | LLM: did they say a room is free |
| `quoted_rate` | string, optional | LLM: nightly rate if given |
| `reservation_number` | string, optional | LLM: **only if the hotel volunteers one.** We never book, so describe it that way |
| `notes` | string | LLM: one short sentence |

`run_id` and `candidate_rank` are how the tool server tells the 3 parallel calls apart, so they must come from the dynamic variables, not the LLM.

## 5. Settings

`ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`, `ELEVENLABS_AGENT_PHONE_NUMBER_ID` and `ELEVENLABS_WEBHOOK_SECRET` are in `.env.example` but **not yet in `config.py`**. Add a `# --- WS3: Voice` section at the end of `config.py` (`config.py` is the only module that reads env vars).

## 6. Check it before you merge

```bash
python -m pytest -q tests/test_voice_contract.py              # signature + validator (offline)
VOICE_LIVE_TEST=1 python -m pytest -q tests/test_voice_contract.py   # one real call to MOCK_HOTEL_PHONES[0]
python main.py "cheapest hotel in Toronto tonight"             # full run (MOCK_MODULES empty)
```

`assert_valid_call_result(plan, result)` in `tests/test_voice_contract.py` checks every rule in section 2. Use it in `tests/test_voice.py` too. The live test only ever dials `MOCK_HOTEL_PHONES[0]`.
