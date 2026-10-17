# WS1 — Discovery, Orchestration & UI

**Owner:** Teammate 1 · **Modules:** `search_service.py`, `pipeline.py`, `main.py`, `app.py`, `config.py`, `telemetry.py`

You own the start and end of the run: the prompt comes in, the candidates go out, the failover loop runs, and the timer stops.

## Deliverables

- `search_service.find_candidates(city, check_in, check_out) -> list[Candidate]`: top 3 by total price, each with a valid E.164 phone and a street address.
- `pipeline.run(prompt) -> Run`: calls every stage in order, loops through candidates on failure, and records `stage_ms`.
- `main.py`: CLI, e.g. `python main.py --city "Toronto"`.
- `app.py`: Streamlit page with one text input and a live stage log read from `run_telemetry.json`.
- `config.py` and `.env.example`: the only place settings are defined.

## Search design

1. **Prices:** SerpApi `google_hotels` engine, sorted by lowest price, filtered to the city.
2. **Phone numbers:** Google Hotels results often have **no phone**. Plan a second lookup per candidate:
   - Option A: SerpApi `google_maps` engine, search `"<hotel name> <city>"`.
   - Option B: Google Places Text Search with a field mask for `nationalPhoneNumber`/`internationalPhoneNumber`.
   - Run the lookups for the top 5 **in parallel**. Keep the first 3 that have a phone.
3. **Validation:** parse phones with `phonenumbers` into E.164. Drop any candidate without a phone or a street address.
4. **Amadeus:** confirm in Phase 0 that the self-service API is still available before writing any code against it. Treat it as optional.

## Phase tasks

### Phase 0 (09:45–10:30)
- [ ] Create the repo, `.env.example` and `config.py` skeleton. Share them with the team.
- [ ] Define the `Run` dataclass in `telemetry.py` exactly as in [architecture.md](architecture.md#data-contract--the-run-object).
- [ ] SerpApi key works. **Confirm whether hotel results include phone numbers.**
- [ ] Gate: a terminal call returns hotel JSON.

### Phase 1 (10:30–13:00)
- [ ] `find_candidates` returns 3 valid candidates in ≤ 5 s.
- [ ] `pipeline.py` stub: a hard-coded candidate goes through stubbed WS2, WS3 and WS4 functions.
- [ ] **12:30:** walking skeleton runs end to end with the real call and email from WS3/WS4.

### Phase 2 (13:30–15:30)
- [ ] Wire live search into the pipeline.
- [ ] Per-stage timers. The timer starts on prompt submit and stops at `email.sent_at`.
- [ ] Failover loop: when `outcome != booked`, dial the next candidate, up to 3.

### Phase 3 (15:30–16:45)
- [ ] Streamlit UI: one input box, plus a live stage list with times as they complete.
- [ ] Show the final summary: hotel, price, reservation number, total time and tokens.

## Done when

- Toronto (or any test city) returns 3 candidates with phones in ≤ 5 s, 3 times in a row.
- The UI shows every stage live during a full run.
