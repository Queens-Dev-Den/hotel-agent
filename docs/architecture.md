# Architecture

**Owner:** Lead · **Status:** locked at 10:30, changes need all four teammates to agree

**Target:** one prompt in → one email out, in **under 4 minutes**. The email highlights the **cheapest hotel that has a room** for the requested dates, with the **full transcript of that call**, and lists the other hotels we called in one line each. We **never book**: the agent only asks whether a room is available.

Backboard runs before and after the calls. ElevenLabs runs the live calls, **all three at the same time**. The email goes out once, when the last call has ended and its transcript is in.

## Pipeline

```
Prompt ──► search_service.py ──► agent_brain.py ──► voice_agent.py  ×3 IN PARALLEL
 (WS1)     prices + phone lookup   parse prompt,      ┌ call hotel #1 ─┐
           top 3 candidates        build 3 call       ├ call hotel #2 ─┼─► availability + transcript each
                                   plans (≤300 tok)   └ call hotel #3 ─┘
                                                              │
                      record_availability (server tool, per call) ──► tool_server.py stores the result
                                                              │
              pipeline.py waits for all 3 calls (or the hard cap)
                                                              │
              extractor.py + agent_brain.py fallback if a tool never fired
                                                              │
                                  email_service.py ──► ONE email: cheapest available + its transcript, others in 1 line
                                                              │
                                      telemetry.py tokens + stage times
```

`pipeline.py` owns the fan-out, the join and the timer. Every module reads and writes the shared `Run` object in `telemetry.py`.

## Key decisions (changes from the original brief)

Each one protects scoring points and is a story for the Learnings section of the pitch.

| # | Original brief | Decision | Why | Protects |
|---|---|---|---|---|
| 1 | Backboard is the conversational brain during the call | Backboard runs **before** the calls (parse prompt, build call plans) and **after** them (fallback extraction from a transcript). The ElevenLabs agent's own LLM handles live turns. | A Backboard hop on every turn adds delay to each reply. Only revisit if a sponsor rule requires it **and** time to first token is under 400 ms. | Turn-taking, Time |
| 2 | Regex the answer out of the transcript | The agent states what it heard, then calls the `record_availability` server tool with structured fields (`available`, optional `quoted_rate`, `notes`). `extractor.py` validates it. Transcript regex is the fallback only. | Free-form speech is messy; a tool call gives structured data. | Extraction |
| 3 | Book a room and email the reservation number | **Availability check only. The agent never books.** It asks whether a room is free for the dates, notes the rate if offered, thanks them and hangs up. It gives no card, no payment promise and no name. | We must not create real reservations at real hotels. | Safety |
| 4 | Dial candidates one at a time, fail over on a bad outcome | **Dial all 3 at once.** There is no failover loop: a voicemail or no-answer is simply recorded as that hotel's result. | Total time is the slowest call, not the sum of three. One slow hotel doesn't block the others. | Time |
| 5 | Email fires mid-call | The email fires **once, after the last call ends**, because it must include every transcript. The `record_availability` handler only stores the result. Transcripts are fetched from the ElevenLabs conversation API (post-call webhook as the fallback). | The email can't include a transcript that doesn't exist yet. | Time, Completeness |
| 6 | Turn-taking pause of 2.5 s or more for holds | Normal turn detection. Silence/end-call timeout of 90–120 s. The prompt says to stay silent during hold music. | Hold tolerance is a separate setting. A long end-of-turn pause makes every reply slow. | Turn-taking, Hold |
| 7 | Hang up after 20 s on voicemail or no answer | End the call **on detection** (voicemail detection plus phrases like "leave a message" / "after the tone"). Hard cap of 45 s with no human speech. | 20 s kills calls during long IVR greetings. | IVR navigation |
| 8 | Integrate everything at 13:30 | Stubbed end-to-end walking skeleton by **12:30** | Contract mismatches surface by lunch. | Everything |

## Data contract — the `Run` object

Defined in `telemetry.py` and serialized to `run_telemetry.json`. Each module writes only its own block.

| Block | Fields | Written by | Read by |
|---|---|---|---|
| `run` | `run_id`, `prompt`, `started_at` (epoch ms) | pipeline (WS1) | all |
| `request` | `city`, `check_in`, `check_out` (ISO `YYYY-MM-DD`), `guests`, `callback_email` | agent_brain (WS2) | WS1, WS3 |
| `candidates[]` | `rank`, `name`, `address`, `phone_e164`, `total_price`, `currency`, `source` — top 3, sorted by `total_price` ascending | search_service (WS1) | WS2, WS3, WS4 |
| `call_plans[]` | one per candidate: `candidate_rank`, `system_prompt`, `dynamic_vars`, `prompt_tokens` | agent_brain (WS2) | WS3 |
| `calls[]` | one per candidate: `candidate_rank`, `conversation_id`, `outcome` (`available` / `unavailable` / `voicemail` / `no_answer` / `error`), `duration_s`, `transcript` | voice_agent (WS3) | WS1, WS2, WS4 |
| `availability[]` | one per candidate: `candidate_rank`, `available` (true / false / null if unknown), `quoted_rate` (optional), `notes`, `source` (`tool` / `transcript` / `backboard`) | tool_server + extractor (WS4) | WS4 |
| `email` | `subject`, `message_id`, `sent_at` (epoch ms) | email_service (WS4) | WS1 |
| `telemetry` | `stage_ms{search, brain, dial, call, email}`, `tokens{backboard_in, backboard_out, agent_llm}` | all → telemetry.py | WS4 (deck) |

### Contract rules

- **Hotel name, address and phone in the email come from `candidates[]`**, not from the call.
- **Availability comes from the call** (`availability[]`). If a call produced no answer (voicemail, no answer, error), the email says so for that hotel instead of guessing.
- **The email highlights the cheapest available hotel** (lowest quoted rate among hotels that said yes; search-price order when there's no quote; the cheapest hotel overall if none said yes). Only **its** transcript is included, verbatim. Every transcript is still saved in `calls[].transcript` and `run_telemetry.json`. The other hotels get one line each: status and rate.
- `stage_ms.call` is the wall-clock time of the slowest call, since the calls overlap.
- `tokens.agent_llm` is the sum across the 3 calls.
- The subject is built from one constant in `config.py`, `Team # {TEAM_NO} - Hotel Reservation Confirmed!`, and checked against the exact required string before sending. See the open question about whether that string still applies now that we don't book.
- Every stage appends to `run_telemetry.json`. The UI log and the pitch metrics both read that one file.

## Token strategy

| Stage | Time target | Token target |
|---|---|---|
| Search + phone lookup | ≤ 5 s | 0 (no LLM) |
| Prompt parsing | ≤ 1 s | 0 if a regex parse succeeds; Backboard fallback ≤ 150 |
| Build 3 call plans | ≤ 1 s | 0 (template fill) |
| Dial + answer (3 in parallel) | ≤ 15 s | — |
| Live calls (3 in parallel) | ≤ 180 s (slowest call) | Agent prompt ≤ 300; replies ≤ 25 tokens/turn, per call |
| Fetch transcripts | ≤ 5 s | 0 |
| Email dispatch | ≤ 2 s | 0 |
| Fallback extraction | only for calls whose tool did not fire | ≤ 300 per call |
| **Total** | **< 4 min** | **Backboard < 1,200 per session** (150 parse + at most 3 × 300 fallback = 1,050 worst case) |

Never spend LLM tokens on deterministic work: parsing, sorting, formatting and validation.
