# Architecture

**Owner:** Lead · **Status:** locked at 10:30, changes need all four teammates to agree

**Target:** one prompt in → confirmation email out in **under 4 minutes**. Backboard runs before and after the call. ElevenLabs runs the live call. The email fires **mid-call** the moment the reservation number is confirmed.

## Pipeline

```
Prompt ──► search_service.py ──► agent_brain.py ──────────► voice_agent.py
 (WS1)     prices + phone lookup   parse prompt, build        ElevenLabs outbound call
           top 3 candidates        call_plan (≤300 tokens)    IVR / hold / readback
                                                                    │
                                                    record_confirmation (server tool)
                                                                    │
                          email_service.py ◄── extractor.py ◄───────┘
                          (WS4, fires mid-call)  (zero-token validation)
                                                                    │
           pipeline.py failover loop ◄── voicemail / no answer / refused → dial candidate #2
                                                                    │
           post-call webhook ──► agent_brain.py fallback extraction + telemetry.py tokens
```

`pipeline.py` owns the loop and the timer. Every module reads and writes the shared `Run` object in `telemetry.py`.

## Key decisions (changes from the original brief)

Each one protects scoring points and is a story for the Learnings section of the pitch.

| # | Original brief | Decision | Why | Protects |
|---|---|---|---|---|
| 1 | Backboard is the conversational brain during the call | Backboard runs **before** the call (parse prompt, build call plan) and **after** it (fallback extraction from the transcript). The ElevenLabs agent's own LLM handles live turns. | A Backboard hop on every turn adds delay to each reply. Only revisit if a sponsor rule requires it **and** time to first token is under 400 ms. | Turn-taking, Time |
| 2 | Regex the spoken code out of the transcript | The agent reads the code back, gets a "yes", then calls the `record_confirmation` server tool with structured fields. `extractor.py` validates the format. Transcript regex is the fallback only. | Speech-to-text writes "AB123" as "ay bee one two three" or "A.B. 123". | Confirmation extraction |
| 3 | Send the email after the call-completion webhook | The `record_confirmation` handler sends the email immediately. The webhook only backfills telemetry. | The timer stops before the goodbye and the webhook delay, saving roughly 10–30 s. | Time |
| 4 | Turn-taking pause of 2.5 s or more for holds | Normal turn detection. Silence/end-call timeout of 90–120 s. The prompt says to stay silent during hold music. | Hold tolerance is a separate setting. A long end-of-turn pause makes every reply slow. | Turn-taking, Hold |
| 5 | Hang up after 20 s on voicemail or no answer | Fail over **on detection** (voicemail detection plus phrases like "leave a message" / "after the tone"). Hard cap of 45 s with no human speech. | 20 s kills calls during long IVR greetings. | IVR navigation |
| 6 | Integrate everything at 13:30 | Stubbed end-to-end walking skeleton by **12:30** | Contract mismatches surface by lunch. | Everything |

## Data contract — the `Run` object

Defined in `telemetry.py` and serialized to `run_telemetry.json`. Each module writes only its own block.

| Block | Fields | Written by | Read by |
|---|---|---|---|
| `run` | `run_id`, `prompt`, `started_at` (epoch ms) | pipeline (WS1) | all |
| `request` | `city`, `check_in`, `check_out` (ISO `YYYY-MM-DD`), `guests`, `guest_name`, `callback_email` | agent_brain (WS2) | WS1, WS3 |
| `candidates[]` | `rank`, `name`, `address`, `phone_e164`, `total_price`, `currency`, `source` — top 3, sorted by `total_price` ascending | search_service (WS1) | WS2, WS3, WS4 |
| `call_plan` | `system_prompt`, `dynamic_vars`, `prompt_tokens` | agent_brain (WS2) | WS3 |
| `calls[]` | `candidate_rank`, `conversation_id`, `outcome` (`booked` / `voicemail` / `no_answer` / `refused` / `error`), `duration_s` | voice_agent (WS3) | WS1, WS2 |
| `confirmation` | `reservation_number`, `reservation_date`, `readback_confirmed`, `source` (`tool` / `transcript`) | extractor (WS4) | WS4 |
| `email` | `subject`, `message_id`, `sent_at` (epoch ms) | email_service (WS4) | WS1 |
| `telemetry` | `stage_ms{search, brain, dial, call, email}`, `tokens{backboard_in, backboard_out, agent_llm}` | all → telemetry.py | WS4 (deck) |

### Contract rules

- **Hotel name, address and phone in the email come from `candidates[]`**, not from the call. Only `reservation_number` and `reservation_date` come from the call, which leaves less for speech-to-text to garble.
- The subject is built from one constant in `config.py`: `Team # {TEAM_NO} - Hotel Reservation Confirmed!`. It is checked against the exact required string before sending.
- Every stage appends to `run_telemetry.json`. The UI log and the pitch metrics both read that one file.

## Token strategy

| Stage | Time target | Token target |
|---|---|---|
| Search + phone lookup | ≤ 5 s | 0 (no LLM) |
| Prompt parsing | ≤ 1 s | 0 if a regex parse succeeds; Backboard fallback ≤ 150 |
| Build call plan | ≤ 1 s | 0 (template fill) |
| Dial + answer | ≤ 15 s | — |
| Live call | ≤ 150 s | Agent prompt ≤ 300; replies ≤ 25 tokens/turn |
| Email dispatch | ≤ 2 s | 0 |
| Post-call fallback extraction | async, untimed | ≤ 300, only when the tool did not fire |
| **Total** | **< 4 min** | **Backboard < 1,200 per session** |

Never spend LLM tokens on deterministic work: parsing, sorting, formatting and validation.
