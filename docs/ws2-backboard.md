# WS2 — Backboard Brain, Memory & Token Telemetry

**Owner:** Isaac (T2) · **Module:** `agent_brain.py` (plus the token counters in `telemetry.py`)

Backboard is **not** in the live call loop (see [architecture decision #1](architecture.md#key-decisions-changes-from-the-original-brief)). It runs before and after the call, and it is the main source of our token-efficiency story.

## Deliverables

- `parse_prompt(prompt) -> Request`: regex/date parsing first (0 tokens). Fall back to Backboard only when the regex parse fails (≤ 150 tokens).
- `build_call_plan(request, candidate) -> CallPlan`: a template fill, 0 tokens. The agent system prompt is ≤ 300 tokens, and per-call facts go in `dynamic_vars`.
- `fallback_extract(transcript) -> Confirmation | None`: Backboard extraction **only when the `record_confirmation` tool never fired** (≤ 300 tokens).
- Token counters: `backboard_in`, `backboard_out` and `agent_llm` (read from the ElevenLabs conversation details if they are exposed) written to the `Run` object.
- Backboard memory: store each run's outcome, IVR path and hold time per hotel. That's a talking point for "learns across runs".

## Defaults when the prompt is silent

| Field | Default |
|---|---|
| `check_in` | tomorrow |
| `check_out` | check_in + 1 night |
| `guests` | 1 |
| `guest_name` | from `config.py` |
| `callback_email` | from `config.py` |

## Phase tasks

### Phase 0 (09:45–10:30)
- [ ] Claim the Backboard hackathon tier with promo code `TECHTO7327`.
- [ ] Auth works from the terminal.
- [ ] Measure time to first token for 2–3 cheap models and record the numbers here.
- [ ] Check whether Backboard exposes an OpenAI-compatible endpoint. Note the result for the architecture critique.
- [ ] Gate: one Backboard completion from the terminal, with its latency recorded.

### Phase 1 (10:30–13:00)
- [ ] `parse_prompt` covers: "cheapest hotel in Toronto", "...for tomorrow", "...on Nov 2 for 2 people".
- [ ] `build_call_plan`: hand T3 the system prompt template and the variable names by 11:00.
- [ ] Count prompt tokens. The system prompt must be ≤ 300.
- [ ] Gate: a mock run uses < 1,200 Backboard tokens in total.

### Phase 2 (13:30–15:30)
- [ ] Search output feeds `build_call_plan`, which feeds the outbound call variables (with T1 and T3).
- [ ] Wire `fallback_extract` to the post-call webhook payload (with T4).

### Phase 3 (15:30–16:45)
- [ ] Final token table for the deck: per stage and per run, average of 3 runs.
- [ ] Write the "what we'd change" notes for the deck: model choice, prompt size, caching.

## Done when

- A full run logs exact token counts per stage.
- Backboard tokens stay under 1,200 per session, and there's a number to show the judges.
