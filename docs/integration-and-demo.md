# Integration, Timeline & Demo Runbook

**Owner:** Everyone · **T1** Richard · **T2** Zach · **T3** Jordan · **T4** Isaac

## Timeline

| Time | Phase | Exit gate |
|---|---|---|
| 09:45–10:30 | **0 — Provisioning & contract** | Every teammate shows a working API call from their terminal. The `Run` contract is frozen. Open questions are sent to the organizers. |
| 10:30–13:00 | **1 — Modules + walking skeleton** | **12:30:** 3 hard-coded hotels → 3 parallel real calls to the mock hotel → tool results → one email with transcripts arrives, with no one touching anything. |
| 13:00–13:30 | Lunch | — |
| 13:30–15:30 | **2 — Real integration** | 3 passes in a row against the mock hotel, all < 4 min, with the email showing all 3 hotels' availability and transcripts. |
| 15:30–16:45 | **3 — Hardening + UI** | A full run with no manual steps, including a mixed outcome (one available, one sold out, one voicemail). |
| 16:45–17:15 | **4 — Freeze & bake-off** | Code freeze at 16:45. Two dry runs. System ready for the 17:15 trigger. |

Per-teammate tasks are in each `ws*.md` doc.

## Integration order

Each step is tested before the next one is wired.

1. **WS4 `email_service.send` ← stubbed `Run`.** A fake run with three results and transcripts sends one correctly formatted email.
2. **WS4 `record_availability` ← curl.** A fake tool payload is stored, and three at once don't collide.
3. **WS3 calls → WS4 endpoint.** Three simultaneous mock-hotel calls fire the tool and return transcripts, and the email arrives. *(This is the 12:30 skeleton, with hard-coded hotels.)*
4. **WS2 call_plans → WS3.** Dynamic variables reach the agent for each call.
5. **WS1 search → WS2.** Live candidates replace the hard-coded hotels.
6. **pipeline.py parallel fan-out/join + timers.**
7. **app.py UI** on top of `pipeline.run`.

## Test matrix (against the mock hotel, 3 calls at once)

| Scenario | Expected | Owner | Pass |
|---|---|---|---|
| Happy path, all 3 answer "available" | One email in < 4 min, 3 sections, 3 transcripts | T3/T4 | [ ] |
| Mixed: available / sold out / available | Each hotel shows its own correct status | T3/T4 | [ ] |
| IVR "press 1 for reservations" | Agent sends DTMF or says it, and reaches a human | T3 | [ ] |
| 60 s hold | Agent stays silent and gets an answer after the hold | T3 | [ ] |
| Voicemail on one hotel | Detected ≤ 45 s, recorded as `voicemail`, the other two unaffected | T3/T1 | [ ] |
| No answer on one hotel | Recorded as `no_answer`, email says "Unknown" for it | T3/T1 | [ ] |
| Receptionist asks for a card or a name | Agent declines ("only checking availability"), no booking made | T3 | [ ] |
| Tool never fires on one call | Transcript fallback fills the status, and the email still goes out | T4/T2 | [ ] |
| One call hits the hard cap | Recorded as `error`, the email still goes out with the other results | T1/T3 | [ ] |
| Search returns hotel without phone | Skipped, next candidate used | T1 | [ ] |
| Total time | < 4 min, with the call stage = slowest call, not the sum | T1 | [ ] |

## Risk register

| Risk | Fallback | Owner |
|---|---|---|
| Outbound number not ready by 10:30, or it can't do 3 concurrent calls | Escalate to sponsor support on Discord right away. Add a second number or trunk. Test with web calls meanwhile. | T3 |
| ElevenLabs plan caps concurrent conversations below 3 | Check the limit in Phase 0. Request a raise from the sponsor, or stagger the dials by a few seconds as a last resort. | T3 |
| Search results lack phone numbers | Second lookup through Maps/Places. Skip candidates without a phone. | T1 |
| Agent gets a muddled availability answer | Repeat-back before the tool call. The transcript fallback marks it `Unknown` rather than guessing. | T3/T4 |
| Agent drifts into booking | Hard "never book" line in the prompt, and a test where the mock hotel pushes for a booking | T3 |
| Transcript isn't ready when the call ends | Retry the fetch for a few seconds, then fall back to the webhook payload. Never block the email longer than the cap. | T3/T4 |
| Tunnel drops during the demo | Static/paid tunnel, or deploy the endpoint | T4 |
| Email lands in spam / domain unverified, or is clipped by long transcripts | Gmail SMTP fallback, tested in Phase 0 with a 3-transcript body | T4 |
| Backboard slow or down | Prompt parsing and the call plans are already 0-token. Skip the fallback | T2 |

## Open questions for organizers (ask before 10:30)

- [ ] Our **team number** for the subject line → set `TEAM_NO` in `.env`.
- [ ] **We don't book. Does the verification still accept the subject `Team # X - Hotel Reservation Confirmed!` with an availability result and transcripts in the body, or is there a different required subject/body?** This is the biggest open risk.
- [ ] On judging day, do we dial an organizer-run mock hotel or real hotels? We don't make real bookings. Is it OK to ring three real hotels at once just to ask about availability?
- [ ] Is it OK that the voice agent identifies itself as automated if asked?
- [ ] Which email address should the receipt go to?
- [ ] Does the ElevenLabs agent's built-in LLM count as a "ready-made agent"? We're assuming no, because it's sponsor stack.
- [ ] What stops the timer: the email being sent, or the email arriving?

## Demo runbook (bake-off)

1. 16:45: code freeze. Tag the commit.
2. Check `.env`: `TEAM_NO`, recipient email, Twilio number (3 concurrent calls confirmed), all API keys.
3. Start the tunnel and confirm that `record_availability` returns 200 for a curl.
4. Open `app.py`, plus the email inbox on a second screen.
5. Dry run #1 and #2 against the mock hotel. Clear `run_telemetry.json` after.
6. At 17:15: paste the prompt, submit, and don't touch anything.
7. After the run: screenshot the UI timings and the token table for the deck.
