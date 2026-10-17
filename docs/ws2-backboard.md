# WS2 — Backboard Brain, Memory & Token Telemetry

**Owner:** Zach (T2) · **Module:** `agent_brain.py` (plus the token counters in `telemetry.py`)

Backboard is **not** in the live call loop (see [architecture decision #1](architecture.md#key-decisions-changes-from-the-original-brief)). It runs before and after the calls, and it is the main source of our token-efficiency story.

## Deliverables

- `parse_prompt(prompt) -> Request`: regex/date parsing first (0 tokens). Fall back to Backboard only when the regex parse fails (≤ 150 tokens).
- `build_call_plan(request, candidate) -> CallPlan`: a template fill, 0 tokens, **called once per candidate (3 times per run)**. The agent system prompt is ≤ 300 tokens, and per-call facts go in `dynamic_vars`. The plan is for an **availability check only**: the agent never books.
- `fallback_extract(transcript) -> Availability | None`: Backboard extraction **only for a call whose `record_availability` tool never fired and whose regex fallback failed** (≤ 300 tokens per call).
- Token counters: `backboard_in`, `backboard_out` and `agent_llm` (read from each ElevenLabs conversation's details if they are exposed, **summed across the 3 calls**) written to the `Run` object.
- Backboard memory: store each hotel's availability outcome, IVR path and hold time per run. That's a talking point for "learns across runs".
  - **Built.** `remember_run(run)` runs after the email and saves one line per call to the `hotel-agent-memory` assistant (`BACKBOARD_ASSISTANT_ID`), e.g. `[+14165550123] Hotel Ocho, 2026-10-17: available; quoted $179/night; menu: press 1 for reservations; put on hold; call 74s`. Our code writes it, so it costs 0 LLM tokens.
  - `recall_hotel_notes(candidate)` runs before the calls (one per candidate, in parallel, about 0.9 s, 3 s timeout). It returns the 2 newest notes for that exact phone number, capped at 160 characters, and the pipeline passes them to `build_call_plan(..., hotel_notes=...)`. The agent sees them as `{{hotel_notes}}` ("none" when there's no history).
  - Any Backboard error makes both functions return empty, so memory can never block a run.

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
- [x] Write the "what we'd change" notes for the deck: model choice, prompt size, caching (see [Deck notes](#deck-notes-for-isaac)).

## Deck notes (for Isaac)

Measured 2026-10-17 unless marked *pending*. Run `python token_report.py` after each real run to fill in the pending numbers.

### Token economy: what we spend per run

| Stage | LLM? | Tokens | Measured |
|---|---|---|---|
| Prompt parsing | Regex first; Backboard only if no city is found | **0** for normal prompts; ~160 on the fallback | Live: "cheapest hotel in Toronto on Nov 2 for 2 people" → 0 tokens, 0 ms. "the capital of Canada next friday" → Backboard, 158 tokens |
| Search + phone lookup | No | **0** | 9.1 s for 9 SerpApi calls (target was 5 s) |
| Memory recall (3 hotels, parallel) | No (memory search) | **0** | ~1.0 s |
| Call plans ×3 | No (template fill) | **0** | System prompt ≈ 234 tokens (budget 300), 7 dynamic vars + `hotel_notes` |
| Live calls ×3 | ElevenLabs agent LLM | ~89k per run, **81% cached reads** | First real run (27 s / 12 s / 72 s calls): 88,993 total, 72,227 cache reads, 1,110 output |
| Availability extraction | Tool call first, then regex, then Backboard | **0** when the tool fires; ~200 per call on the Backboard fallback | Live: sample transcript → "available, $179", 197 tokens |
| Remember the run | No (our code writes the summary) | **0** | ~1.2 s per hotel, after the email so it never delays the result |
| **Backboard total** | | **0 on the happy path**; worst case ≈ 160 + 3 × 200 = 760 | Budget 1,200 |

The pitch line: **Backboard costs 0 tokens on the happy path.** Every deterministic step (parsing, sorting, the prompt, validation, the memory summary) is plain code. The LLM is only a fallback.

### What we learned / what we'd change

1. **Backboard latency ruled it out of the live call.** Every model took 1.1–1.5 s per round trip through Backboard (gemini-2.5-flash-lite, gpt-4o-mini, gpt-4.1-nano, claude-haiku-4-5). That's 3× over our 400 ms bar for a voice turn, so the ElevenLabs agent's own LLM handles the conversation and Backboard runs only before and after.
2. **Model choice by tokens, not just speed.** The latencies were within noise of each other, so we picked `gemini-2.5-flash-lite` for the fewest tokens (36 in / 5 out on the test prompt vs 81 / 13 for Haiku, which also wrapped its JSON in a code fence).
3. **Backboard isn't OpenAI-compatible.** It has its own `/threads/messages` API, with no max-output-tokens parameter, so we cap output by asking for short JSON. Errors such as billing or an unknown model come back as **HTTP 200 with `status: FAILED`**, which a naive client treats as a real answer. We caught that on day one.
4. **The free tier only covers Memory & RAG.** Chat completions needed the hackathon promo credit. We'd check billing scope first next time.
5. **Memory without tokens.** Instead of letting Backboard's LLM extract memories from transcripts (which costs tokens), we write one deterministic line per call (outcome, rate, IVR key, hold, duration) and read it back as `{{hotel_notes}}`. The agent learns "press 1 for reservations" for next time at 0 tokens.
6. **Agent tokens are mostly the platform's context, re-read every turn.** Our prompt is ~238 tokens, but each agent turn re-reads ~3,500 (ElevenLabs' built-in instructions and tool definitions + our prompt + the conversation so far). On the first real run that was 89k tokens for 3 calls, 81% of them cheap cache reads, and only ~1.1k output. Shorter calls (the opening line now asks for the cheapest room and dates up front) cut this more than trimming our prompt would. We also caught a double count: ElevenLabs' `initiated_generation` already includes `irreversible_generation`, so summing both showed 143k.
7. **What we'd do with more time:**
   - Stream Backboard replies and measure true time to first token. Today's numbers are full round trips.
   - Use the model's real tokenizer instead of the 4-chars-per-token estimate for the prompt budget.
   - Prompt caching on the ElevenLabs side: the system prompt is identical across all 3 calls, and only `dynamic_vars` change.
   - Search is the slowest deterministic stage (9 s). Caching hotel phone numbers between runs would cut most of the 9 SerpApi calls.

## Done when

- A full run logs exact token counts per stage.
- Backboard tokens stay under 1,200 per session (150 parse + at most 3 × 300 fallback), and there's a number to show the judges.
