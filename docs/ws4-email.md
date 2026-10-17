# WS4 — Confirmation Extraction, Email & Pitch Deck

**Owner:** Teammate 4 · **Modules:** `extractor.py`, `email_service.py`, the `record_confirmation` endpoint, the deck

The email **is** the verification. A wrong subject or a missing field means the run doesn't count.

## Required email format

- **Subject (exact):** `Team # X - Hotel Reservation Confirmed!` (X comes from `config.TEAM_NO`)
- **Body:** all five fields, each on its own labelled line:

```
Hotel Name: <candidates[rank].name>
Address: <candidates[rank].address>
Phone: <candidates[rank].phone_e164>
Reservation #: <confirmation.reservation_number>
Reservation Date: <confirmation.reservation_date>
```

Hotel name, address and phone come from search data, not the call (see [contract rules](architecture.md#contract-rules)).

## Deliverables

- **`record_confirmation` endpoint:** receives the server-tool call from the ElevenLabs agent, validates it, and **sends the email immediately, during the call**. It returns a short success message so the agent can close the call.
- **`extractor.py` (zero tokens):**
  - `validate(payload)`: the reservation number matches `^[A-Z0-9-]{4,20}$` after uppercasing and stripping spaces. The date parses.
  - `from_transcript(text)`: regex fallback over the transcript, including spoken-digit normalisation ("one two three" → `123`, "Alpha" → `A`). Backboard (WS2) is the last fallback.
- **`email_service.send(run) -> message_id`:** builds the subject from the constant, asserts all 5 fields are non-empty, sends through Resend, and writes `email.sent_at`.
- **Public URL** for the endpoint: cloudflared or ngrok, or a small deploy if the tunnel is flaky.
- `tests/`: unit tests for the extractor and the subject/body format.

## Phase tasks

### Phase 0 (09:45–10:30)
- [ ] Resend account. Domain verification can be slow, so **also set up a Gmail SMTP app password as the fallback.**
- [ ] Gate: a test email with the exact subject format arrives in the inbox (not spam).

### Phase 1 (10:30–13:00)
- [ ] `email_service.send` with format assertions.
- [ ] `record_confirmation` endpoint up behind a public URL. Send the URL to T3 by 11:30.
- [ ] Extractor with unit tests for the codes `HX4729`, "H X four seven two nine" and "hotel x-ray 4-7-2-9".
- [ ] Help T3 build the mock hotel.
- [ ] Gate: a correctly formatted test email triggered by a curl to the endpoint.

### Phase 2 (13:30–15:30)
- [ ] The tool call from a live mock-hotel call sends the email.
- [ ] The post-call webhook backfills telemetry and runs the transcript fallback when the tool didn't fire.

### Phase 3 (15:30–16:45) — the deck
- [ ] 3-minute deck for the judges (Jatin Mehta, David Li, Zeeshan Ahmed). About 6 slides:
  1. Problem and the one-shot demo flow
  2. Architecture diagram (from [architecture.md](architecture.md))
  3. Token economy: per-stage table from T2, and what made each stage 0 tokens
  4. Latency post-mortem: per-stage `stage_ms`, and where the time actually went
  5. Call handling: IVR, hold, readback and failover, with how we tested each one against the mock hotel
  6. Learnings: the 6 architecture decisions and what we'd do next
- [ ] Rehearse to 2:45 so there's buffer.

## Done when

- The email arrives within ≤ 2 s of the tool call, with all 5 fields, 3 times out of 3.
- Deck rehearsed and under 3 minutes.
