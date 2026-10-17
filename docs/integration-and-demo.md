# Integration, Timeline & Demo Runbook

**Owner:** Everyone · **T1** Richard · **T2** Zach · **T3** Jordan · **T4** Isaac

## Timeline

| Time | Phase | Exit gate |
|---|---|---|
| 09:45–10:30 | **0 — Provisioning & contract** | Every teammate shows a working API call from their terminal. The `Run` contract is frozen. Open questions are sent to the organizers. |
| 10:30–13:00 | **1 — Modules + walking skeleton** | **12:30:** hard-coded hotel → real call to the mock hotel → tool → email arrives, with no one touching anything. |
| 13:00–13:30 | Lunch | — |
| 13:30–15:30 | **2 — Real integration** | 3 passes in a row against the mock hotel, all < 4 min, with all 5 email fields correct. |
| 15:30–16:45 | **3 — Hardening + UI** | A full run with no manual steps, including one forced failover to candidate #2. |
| 16:45–17:15 | **4 — Freeze & bake-off** | Code freeze at 16:45. Two dry runs. System ready for the 17:15 trigger. |

Per-teammate tasks are in each `ws*.md` doc.

## Integration order

Each step is tested before the next one is wired.

1. **WS4 endpoint ← curl.** A fake tool payload sends an email.
2. **WS3 call → WS4 endpoint.** The mock-hotel call fires the tool, and the email arrives. *(This is the 12:30 skeleton, with a hard-coded hotel.)*
3. **WS2 call_plan → WS3.** Dynamic variables reach the agent.
4. **WS1 search → WS2.** A live candidate replaces the hard-coded hotel.
5. **pipeline.py failover loop + timers.**
6. **app.py UI** on top of `pipeline.run`.

## Test matrix (against the mock hotel)

| Scenario | Expected | Owner | Pass |
|---|---|---|---|
| Happy path, human answers | Booked, email in < 4 min | T3/T4 | [ ] |
| IVR "press 1 for reservations" | Agent sends DTMF or says it, and reaches a human | T3 | [ ] |
| 60 s hold | Agent stays silent and books after the hold | T3 | [ ] |
| Code spoken fast or garbled | Readback corrects it, and the email has the right code | T3/T4 | [ ] |
| Voicemail | Detected ≤ 45 s, fails over to #2 | T3/T1 | [ ] |
| No answer | Fails over to #2 | T3/T1 | [ ] |
| Receptionist asks for a card | "Pay at check-in" line. If refused, fail over | T3 | [ ] |
| Tool never fires | Transcript fallback, then the email still goes out | T4/T2 | [ ] |
| Search returns hotel without phone | Skipped, next candidate used | T1 | [ ] |

## Risk register

| Risk | Fallback | Owner |
|---|---|---|
| Outbound number not ready by 10:30 | Escalate to sponsor support on Discord right away. Test with web calls meanwhile. | T3 |
| Search results lack phone numbers | Second lookup through Maps/Places. Skip candidates without a phone. | T1 |
| Agent garbles the code | Readback with phonetic spelling, at most 2 tries | T3 |
| Tunnel drops during the demo | Static/paid tunnel, or deploy the endpoint | T4 |
| Email lands in spam / domain unverified | Gmail SMTP fallback, tested in Phase 0 | T4 |
| Backboard slow or down | Prompt parsing and the call plan are already 0-token. Skip the fallback | T2 |

## Open questions for organizers (ask before 10:30)

- [ ] Our **team number** for the subject line → set `TEAM_NO` in `.env`.
- [ ] On judging day, do we dial an organizer-run mock hotel or real hotels? We don't make real bookings without permission.
- [ ] "Reservation Date": the date we booked, or the check-in date?
- [ ] Which email address should the receipt go to?
- [ ] Does the ElevenLabs agent's built-in LLM count as a "ready-made agent"? We're assuming no, because it's sponsor stack.
- [ ] What stops the timer: the email being sent, or the email arriving?

## Demo runbook (bake-off)

1. 16:45: code freeze. Tag the commit.
2. Check `.env`: `TEAM_NO`, recipient email, Twilio number, all API keys.
3. Start the tunnel and confirm that `record_confirmation` returns 200 for a curl.
4. Open `app.py`, plus the email inbox on a second screen.
5. Dry run #1 and #2 against the mock hotel. Clear `run_telemetry.json` after.
6. At 17:15: paste the prompt, submit, and don't touch anything.
7. After the run: screenshot the UI timings and the token table for the deck.
