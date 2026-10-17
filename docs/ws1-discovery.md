# WS1 — Discovery, Orchestration & UI

**Owner:** Richard (T1) · **Modules:** `search_service.py`, `pipeline.py`, `main.py`, `app.py`, `telemetry.py` (+ the WS1 sections of `config.py`)

You own the start and end of the run: the prompt comes in, the candidates go out, the three calls fan out and join, and the timer stops.

## Deliverables (built)

- `search_service.find_candidates(request: HotelRequest, *, stats=None) -> list[HotelCandidate]`: up to 3 by total stay price, each with a valid E.164 phone and a street address. `stats` collects drop counts. Raises `RuntimeError` if nothing callable is found.
- `pipeline.run(prompt, *, callback_email=None, on_event=None) -> Run`: calls every stage in order, **dials all candidates at once**, waits for every call to finish or hit the cap, and records `stage_ms`. `on_event(kind, data)` streams live progress to the UI.
- `main.py`: CLI, `python main.py "cheapest hotel in Toronto tonight" [--email you@x.com]`.
- `app.py`: Streamlit page (`streamlit run app.py`): one input, a live step list, one live row per hotel call, a summary and transcripts. A sidebar has fake calls/email/search toggles.
- `telemetry.py`: `stage(run, name)` timer + `save(run)` → `run_telemetry.json` after every stage.

## Search design (as built, verified against live SerpApi responses)

1. **Prices:** SerpApi `google_hotels`, `sort_by=3` (lowest price), `gl=ca`, currency from `SEARCH_CURRENCY` (CAD). Two searches run in parallel: hotels (hostels included) and vacation rentals (`vacation_rentals=true`, toggled by `SEARCH_RENTALS`).
   - Price basis: `total_rate.extracted_lowest` (whole stay), falling back to `rate_per_night × nights`. Listings with neither are dropped.
2. **Phone numbers:** search results **never** include a phone (0 of 18 hotels, 0 of 18 rentals in the Toronto probe). Phones come from the per-property details call (`property_token`), which returns `phone` and `address`.
   - The cheapest `SEARCH_TOP_N` (5) hotels and `RENTAL_FANOUT` (2) rentals are looked up **in parallel**. Rentals get their own budget because they're cheapest but have had no phone so far, and would otherwise crowd out callable hotels.
   - We chose SerpApi details over Google Maps or Places: same key, same Google data, no second account.
3. **Validation:** small built-in normalizer to E.164 (North American numbers; other `+` numbers kept). Drops: no price, no valid phone, no street address, duplicate phone, failed lookup (drops that listing only, never the run).
4. **Ranking:** cheapest first, but **toll-free numbers (8xx) rank last**, because they usually reach central reservations, not the front desk. Keeps `MAX_CANDIDATES` (3).
5. **Cost:** about 9 SerpApi credits per run (2 searches + 7 detail lookups), about 6 with rentals off. `SEARCH_CACHE=1` replays responses from `.cache/` in dev.
6. **Secrets:** the SerpApi key is in the query string, so we never log URLs, and the `httpx` request logger is silenced.
7. **Amadeus:** not used.

## Orchestration design (as built)

- Order: start `tool_server` → `parse_prompt` (WS2) → `find_candidates` → `recall_hotel_notes` for every candidate **in parallel** (WS2 Backboard memory) → `build_call_plan(..., hotel_notes=...)` → `place_call` for **all candidates at once** (WS3) → `tool_server.resolve_availability` (tool → call → transcript regex → Backboard, WS4) → `email_service.send` **once** (WS4) → `remember_run` (WS2).
- There is **no failover loop**. Each call returns a result even if it's a voicemail or no answer.
- The join waits with a hard cap of `MAX_CALL_DURATION_S` + 30 s. A call that errors or hits the cap is recorded as `error`. The others are never held up, and each is reported to the UI the moment it ends.
- If search returns fewer than 3 candidates, call however many there are.
- **Safety:** with `USE_MOCK_HOTEL=true` (the default) only the *dialed* number is swapped for `MOCK_HOTEL_PHONES`. The email still shows the real hotel. If there aren't enough mock numbers, the run stops instead of dialing real hotels.
- **Memory hygiene:** `remember_run` only runs for real calls to real hotels, so fake or mock-hotel transcripts are never saved under a real hotel's phone.
- `MOCK_MODULES` (`search`, `brain`, `voice`, `email`) swaps in fakes from `pipeline.py`. The UI fakes calls and email by default.
- `stage_ms`: `brain`, `search`, `recall`, `call` (wall-clock of the slowest call), `email`.

## Phase tasks

### Phase 0
- [x] `.env.example` and `config.py` skeleton (PR #3).
- [x] `Run` model (in `contracts.py`), timers in `telemetry.py`.
- [x] SerpApi key works. Hotel results do **not** include phones, so we use a details lookup.
- [x] A terminal call returns hotel JSON.

### Phase 1
- [x] `find_candidates` returns 3 valid candidates (Toronto: 6.0 s, against a 5 s target; accepted for now since calls dominate).
- [x] `pipeline.py` with fakes for WS2/WS3/WS4, calls in parallel.
- [ ] Walking skeleton end to end with real calls and email. **Blocked on WS3 `voice_agent.place_call`.**

### Phase 2
- [x] Live search wired into the pipeline.
- [x] Per-stage timers; `stage_ms.call` is the slowest call.
- [x] Parallel fan-out and join with a cap; a failed call never blocks the rest (tested).
- [x] Backboard memory: recall before call plans, remember after the email.

### Phase 3
- [x] Streamlit UI: input box, live step list, one live row per hotel call.
- [x] Final summary: each hotel with price and answer, total time, tokens, transcripts.

## Tests

`python -m pytest -q tests/test_search.py tests/test_pipeline.py` runs offline, with no keys. It covers phone normalization, price basis, every drop reason, toll-free ranking, rental crowd-out, a failed search, parallel calls, failed and hung calls, the mock-hotel safety switch, progress events, memory order and hygiene, and that the API key isn't logged.

## Done when

- [ ] Toronto (or any test city) returns 3 candidates with phones in ≤ 5 s, 3 times in a row (6.0 s today).
- [x] All 3 calls run concurrently, and the total is faster than the sum (tested with fakes).
- [x] The UI shows every stage live during a full run (with fakes; real calls pending WS3).
