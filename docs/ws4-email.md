# WS4 — Extraction, Email & Pitch Deck

**Owner:** Isaac (T4) · **Modules:** `extractor.py`, `email_service.py`, `tool_server.py` (the `record_availability` endpoint), the deck

The email **is** the verification. A wrong subject or a missing field means the run doesn't count. We don't book, so the email highlights the **cheapest hotel with a room available**, with **that call's full transcript**, and lists the other hotels we called in one line each.

## Required email format

- **Subject (exact):** `Team # X - Hotel Quotes Confirmed` (X comes from `config.TEAM_NO`). Open question for organizers: does this string still apply when we only check availability? See [integration-and-demo.md](integration-and-demo.md#open-questions-for-organizers-ask-before-1030).
- **Body:** a header with the search, a one-line summary, the highlighted hotel in full with its transcript, then one line per other hotel. If no hotel said yes, the cheapest hotel called is shown with "no room confirmed".

```
Check-in: <request.check_in>
Check-out: <request.check_out>
Guests: <request.guests>

We called the 3 cheapest hotels we found for these dates. Here is the cheapest one with a room available.

=== Cheapest available hotel ===
Hotel Name: <candidates[rank].name>
Address: <candidates[rank].address>
Phone: <candidates[rank].phone_e164>
Availability: Available
Quoted Rate: <availability.quoted_rate or "not given">

Transcript:
Agent: ...
Hotel: ...

=== Other hotels we called ===
2. <name>, <phone>: Not available, rate not given
3. <name>, <phone>: Unknown (voicemail / no answer / error), rate not given
```

Hotel name, address and phone come from search data, not the call (see [contract rules](architecture.md#contract-rules)). Availability and the quoted rate come from the call. Only the highlighted hotel's transcript is included, verbatim from `calls[].transcript`; if it has none, it says "No transcript: <outcome>". The `WARNING ... please cancel it` line still appears under any hotel that issued a reservation number.

## Deliverables

- **`record_availability` endpoint (`tool_server.py`):** receives the server-tool call from the ElevenLabs agent for each of the 3 concurrent calls, validates it, and **stores it keyed by `run_id` + `candidate_rank`**. It returns a short success message so the agent can close the call. **It does not send the email.** It must be safe for 3 simultaneous requests.
- **`extractor.py` (zero tokens):**
  - `validate(payload)`: `available` is a boolean, `quoted_rate` parses to a number if present, `notes` is trimmed.
  - `from_transcript(text)`: regex fallback over the transcript for phrases like "we have a room", "available", "sold out", "no availability", "fully booked". Backboard (WS2) is the last fallback.
- **`email_service.send(run) -> message_id`:** builds the subject from the constant, asserts every hotel has its fields, picks the hotel to highlight (`best_index`), sends **one** email through Resend, and writes `email.sent_at`.
- **Public URL** for the endpoint: cloudflared or ngrok, or a small deploy if the tunnel is flaky.
- `tests/`: unit tests for the extractor and the subject/body format, including a body with mixed outcomes.

## Phase tasks

### Phase 0 (09:45–10:30)
- [ ] Resend account. Domain verification can be slow, so **also set up a Gmail SMTP app password as the fallback.**
- [ ] Gate: a test email with the exact subject format arrives in the inbox (not spam).

### Phase 1 (10:30–13:00)
- [ ] `email_service.send` with format assertions and a long transcript in the body. Check that a 3-transcript email isn't clipped or spam-flagged.
- [ ] `record_availability` endpoint up behind a public URL. Send the URL to T3 by 11:30.
- [ ] Extractor with unit tests for "yes we have a room at $189", "sorry, we're sold out", and an unclear answer (→ unknown).
- [ ] Help T3 build the mock hotel.
- [ ] Gate: a correctly formatted test email triggered from a stubbed `Run` with three results.

### Phase 2 (13:30–15:30)
- [ ] Live mock-hotel calls store their tool results, and one email goes out after the last call ends.
- [ ] Transcript fallback runs for any call whose tool didn't fire, before the email is built.

### Phase 3 (15:30–16:45) — the deck
- [ ] 3-minute deck for the judges (Jatin Mehta, David Li, Zeeshan Ahmed). About 6 slides:
  1. Problem and the one-shot demo flow
  2. Architecture diagram (from [architecture.md](architecture.md)), with the three parallel calls
  3. Token economy: per-stage table from T2, and what made each stage 0 tokens
  4. Latency post-mortem: per-stage `stage_ms`, and why parallel calls beat sequential ones
  5. Call handling: IVR, hold and availability extraction, tested against the mock hotel, plus the "never books" guardrail
  6. Learnings: the architecture decisions and what we'd do next
- [ ] Rehearse to 2:45 so there's buffer.

## Done when

- One email arrives within ≤ 2 s of the last call's transcript being ready, highlighting the cheapest available hotel with its transcript and listing the others, 3 times out of 3.
- Deck rehearsed and under 3 minutes.
