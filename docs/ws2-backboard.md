# WS2 — Backboard Brain, Memory & Token Telemetry

**Owner:** Zach (T2) · **Module:** `agent_brain.py` (plus the token counters in `telemetry.py`)

Backboard is **not** in the live call loop (see [architecture decision #1](architecture.md#key-decisions-changes-from-the-original-brief)). It runs before and after the calls, and it is the main source of our token-efficiency story.

## Deliverables

- `parse_prompt(prompt) -> Request`: regex/date parsing first (0 tokens). Fall back to Backboard only when the regex parse fails (≤ 150 tokens).
- `build_call_plan(request, candidate) -> CallPlan`: a template fill, 0 tokens, **called once per candidate (3 times per run)**. The agent system prompt is ≤ 300 tokens, and per-call facts go in `dynamic_vars`. The plan is for an **availability check only**: the agent never books.
- `fallback_extract(transcript) -> Availability | None`: Backboard extraction **only for a call whose `record_availability` tool never fired and whose regex fallback failed** (≤ 300 tokens per call).
- Token counters: `backboard_in`, `backboard_out` and `agent_llm` (read from each ElevenLabs conversation's details if they are exposed, **summed across the 3 calls**) written to the `Run` object.
- Backboard memory: store each hotel's availability outcome, IVR path and hold time per run. That's a talking point for "learns across runs".

## Defaults when the prompt is silent

| Field | Default |
|---|---|
| `check_in` | tomorrow |
| `check_out` | check_in + 1 night |
| `guests` | 1 |
| `callback_email` | from `config.py` |

There is no guest name any more, since nothing is booked.

## Phase tasks

### Phase 0 (09:45–10:30)
- [x] Claim the Backboard hackathon tier with promo code `TECHTO7327`. The free credit alone only covers Memory & RAG, not chat.
- [x] Auth works from the terminal.
- [x] Measure latency for 2–3 cheap models and record the numbers here (see below).
- [x] Check whether Backboard exposes an OpenAI-compatible endpoint. **No.** It has its own `POST /threads/messages` API (`X-API-Key` header, `llm_provider` + `model_name` per request, `system_prompt` per run, `input_tokens`/`output_tokens` in the reply). There's no max-output-tokens parameter, and errors such as billing or a bad model name come back as HTTP 200 with `status: FAILED`. Model list: `GET /models` (paginated, ~16k entries).
- [x] Gate: one Backboard completion from the terminal, with its latency recorded.

#### Latency results (2026-10-17, `python agent_brain.py <provider/model ...>`)

Full round trip for a tiny JSON reply (non-streaming), two runs each:

| Model | Run 1 | Run 2 | Tokens in / out | Notes |
|---|---|---|---|---|
| `google/gemini-2.5-flash-lite` | 1,277 ms | 1,324 ms | 36 / 5 | **Default.** Fewest tokens, clean JSON |
| `openai/gpt-4o-mini` | 1,070 ms | 1,529 ms | 41 / 6 | Clean JSON |
| `openai/gpt-4.1-nano` | 1,172 ms | 1,470 ms | 41 / 6 | Clean JSON |
| `anthropic/claude-haiku-4-5-20251001` | 1,185 ms | 1,330 ms | 81 / 13 | Wraps JSON in a code fence, 2× the tokens |
| `cerebras/meta-llama/llama-3.1-8b-instruct` | failed | failed | — | No routable endpoint |

Every model takes 1.1–1.5 s through Backboard, far above the 400 ms bar in [architecture decision #1](architecture.md#key-decisions-changes-from-the-original-brief). That confirms keeping Backboard out of the live call loop. It's a good number for the deck.

### Phase 1 (10:30–13:00)
- [ ] `parse_prompt` covers: "cheapest hotel in Toronto", "...for tomorrow", "...on Nov 2 for 2 people".
- [ ] `build_call_plan`: hand T3 the availability-check system prompt template and the variable names by 11:00.
- [ ] Count prompt tokens. The system prompt must be ≤ 300.
- [ ] Gate: a mock run (3 calls) uses < 1,200 Backboard tokens in total.

### Phase 2 (13:30–15:30)
- [ ] Search output feeds `build_call_plan` for each of the 3 candidates, which feeds the outbound call variables (with T1 and T3).
- [ ] Wire `fallback_extract` to the post-call transcript or webhook payload for any call without a tool result (with T4).

### Phase 3 (15:30–16:45)
- [ ] Final token table for the deck: per stage and per run, average of 3 runs. Include the agent LLM tokens summed over the 3 parallel calls.
- [ ] Write the "what we'd change" notes for the deck: model choice, prompt size, caching.

## Done when

- A full run logs exact token counts per stage.
- Backboard tokens stay under 1,200 per session (150 parse + at most 3 × 300 fallback), and there's a number to show the judges.
