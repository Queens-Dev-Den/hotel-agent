# Richard (T1): progress log

Branch `t1/richard-search` · Owner of WS1 (search, pipeline, CLI, UI) · Last updated 2026-10-17

## Status

| Piece | Status | Where |
|---|---|---|
| Search: `search_service.find_candidates` | Done, on `main` | PRs #8, #11, #17, #26 |
| Pipeline: `pipeline.run` (all calls at once, cap, memory, live events) | Done, on `main` | PRs #8, #11, #20 |
| CLI: `main.py` | Done, on `main` | PR #8 |
| UI: `app.py` (glass mission-control) | Done, on `main` | PRs #8, #17, #20, #32 |
| Real end-to-end call | Worked with teammates' test calls (see below) | — |

## What search does
- **Two searches at once:** SerpApi `google_hotels` for hotels and hostels, plus vacation rentals. Sorted by lowest price, CAD, total price for the stay.
- **Location rules:**
  - **Organizer:** within 20 km of the city border. The boundary is the city's OSM Nominatim polygon; of the places with that name, we use the one the listings are around (OSM lists Kingston, Jamaica first).
  - **Team (Zach):** within 15 km of downtown, ranked by price plus distance.
- **Phones:** come only from the per-hotel details lookup (search results never have them). Lookups run in parallel for the cheapest 5 hotels and 2 rentals. Phones are normalized to E.164.
- **Dropped:** no price, no phone, no address, duplicate, too far, no coordinates. Toll-free numbers rank last (they usually reach central reservations).
- **Extras:** each hotel gets a cheapest-booking-site link and its website.
- **Cost:** about 9 SerpApi credits per run. 0 LLM tokens.

## What the pipeline does
1. Parse the prompt (Zach), then search (us).
2. Recall Backboard notes on past calls to each hotel (in parallel), then build the call plans (Zach).
3. Call all hotels at once (Jordan's `place_call`), each with a cap.
4. Resolve answers: tool result, then the call's own answer, then transcript regex, then Backboard (Isaac).
5. Send one email (Isaac), then save memories (only for real calls to real hotels).

**Safety:**
- With `USE_MOCK_HOTEL` on, only the dialed number is swapped for our test numbers. With fewer numbers than hotels, only the cheapest hotels are called.
- `MOCK_MODULES` fakes search, brain, voice or email.

**Live events for the UI:** steps, candidates, dialing, each call as it ends, and running token totals.

## UI
- **Main page:** prompt + email + Go; a 5-step rail with timers; 3 live call cards (price, phone, test number rung, status, timer, quoted rate, reservation #, Book/Website); a verdict card with the booking button; a metrics strip (time, tokens, searches, email); transcripts and Zach's token breakdown in tabs.
- **Sidebar control panel:** a mode badge (Safe / rings test numbers / calls real hotels), fake toggles, and who to call. Calling real hotels needs an explicit confirmation.
- **Look:** frosted panels on navy, violet accent, official Lucide icons, Inter + Fira Code. Visible focus, reduced-motion respected, all hotel and transcript text escaped.

## Tests
`python -m pytest -q`: **231 passed, 1 skipped** (whole repo, offline, no keys).

Our files:
- **`tests/test_search.py`:** phone normalization, price basis, every drop reason, toll-free last, rentals not crowding out hotels, booking link, credit count, distance maths, picking the right city, radius filter, lookup failure.
- **`tests/test_pipeline.py`:** calls really in parallel, failed and stuck calls, the test-number switch and fewer-numbers mode, event order, memory order and hygiene, live token counting, API key not logged.

### Live tests
| Test | Result |
|---|---|
| SerpApi response shapes (Toronto) | No phones in search results (0/18), so phones come from details. Rentals have no phone. Prices on every listing |
| Full search, Toronto | 3 callable hotels in 6.0 s (Check Inn $46, Nomad Pod $58, Samesun $64) |
| City boundary, Toronto | Picked Toronto, Ontario (not Iowa or Kansas). Furthest kept listing 3.6 km outside the border |
| Real call run (teammates, 13:41) | No room / voicemail / available correctly classified. The quoted $2.00 was lost, which led to the extractor fix and the `record_availability` tool being attached |

## Bugs found and fixed
- **Rentals crowding out hotels:** cheap rentals with no phones used up all the phone lookups.
- **API key in logs:** the SerpApi key printed via the httpx request log.
- **Fake calls saved to memory:** fake and mock-hotel transcripts were saved to Backboard memory under real hotels' phones.
- **Quoted rate lost:** the rate was missed when it was in a different line from the "yes".
- **Sidebar arrow:** it showed as `keyboard_double_arrow_left` because a font rule overrode the icon font.

## Open
- The ngrok tunnel must be running on the laptop doing a real test (`ngrok http --url=aloha-derail-elixir.ngrok-free.dev 8000`), or the agent's tool answers can't reach us.
- Organizers: is an ElevenLabs dashboard link OK as "link to the call audio"? It needs a login to our workspace.
- Search takes ~6 s against a 5 s target (accepted; calls dominate).
- Live-test the radius rules on a small town.
