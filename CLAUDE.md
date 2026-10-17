# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Hackathon project (TechTO "Fast & Frugal"): one prompt in → search the cheapest hotels in a city → call up to 3 of them **in parallel** with an ElevenLabs voice agent → send **one** email reporting the cheapest hotel with a room. The agent **never books**; it only asks about availability and price.

## Commands

- Setup: `python -m venv .venv && pip install -r requirements.txt`, then `cp .env.example .env` and fill in the keys. `config.py` is the only module that reads env vars.
- Tests: `python -m pytest -q`. A single test: `python -m pytest -q tests/test_email.py::test_subject_is_exact`. All tests are offline (HTTP is faked).
- CLI: `python main.py "cheapest hotel in Toronto tonight" [--email you@x.com]`
- UI: `streamlit run app.py`
- Token report for the pitch deck: `python token_report.py`
- No linter or formatter is configured.

## Architecture

`pipeline.run(prompt)` is the only orchestrator. `main.py` and `app.py` both call it. Its stages:

1. `tool_server` (FastAPI) starts on a background thread. It receives the agent's `record_availability` server-tool calls during the live calls, through `PUBLIC_BASE_URL` (an ngrok tunnel).
2. `agent_brain.parse_prompt` turns the prompt into a `HotelRequest`. It tries a regex parse first and falls back to Backboard.
3. `search_service.find_candidates`: SerpApi google_hotels plus vacation rentals, a parallel details lookup for phone numbers, and a 20 km city-border filter (OSM Nominatim). It returns up to 3 `HotelCandidate`s, cheapest first.
4. `agent_brain.build_call_plan` makes one plan per candidate, from a ≤300-token prompt template.
5. `voice_agent.place_call` runs for every plan **at once**, joined with a per-call cap (`MAX_CALL_DURATION_S`). There is no failover: a voicemail or no-answer is just that hotel's result.
6. `tool_server.resolve_availability` fills in each answer in this order: tool result → regex on the transcript (`extractor`) → Backboard (`agent_brain.fallback_extract`) → unknown.
7. `email_service.send` sends one email. `telemetry` writes `run_telemetry.json`, which both the UI and the pitch metrics read.

Key pieces:
- **Data shapes** are Pydantic models in `contracts.py`. The `Run` model collects everything for one run.
- **Mocks:** `MOCK_MODULES=search,brain,voice,email` swaps the `_mock_*` fixtures in `pipeline.py` in for the real modules.
- **Safety switch:** `USE_MOCK_HOTEL=true` (the default) dials `MOCK_HOTEL_PHONES` instead of the real hotels.

## Organizer rules the code must keep

- Listings must be within 20 km of the city border (`SEARCH_RADIUS_KM`).
- The email subject is exactly `Team # {TEAM_NO} - Hotel Quotes Confirmed`. `check_payload` refuses to send anything else.
- The email body must have: hotel name, address, the phone we called, the price for the stay, and the ElevenLabs call-recording link (`ELEVENLABS_CONVERSATION_URL` with `{id}` = `conversation_id`). The organizers use the recording link to check the agent didn't make anything up. The reservation # is left out on purpose (optional per the organizers; we never book).
- `quoted_rate` is **per night** everywhere. The price for the stay is `quoted_rate × nights`, falling back to the search's `total_price`.
- Token discipline: deterministic work (parsing, sorting, formatting, validation) uses 0 LLM tokens. Backboard is capped by `BACKBOARD_TOKEN_BUDGET`, and token counts are scored.

## Git workflow (docs/git-workflow.md)

- Each teammate owns specific files:
  - `t1/richard-search`: search, pipeline, UI, telemetry
  - `t2/zach-brain`: `agent_brain.py`
  - `t3/jordan-voice`: `voice_agent.py`
  - `t4/isaac-email`: `email_service.py`, `extractor.py`, `tool_server.py`
- Shared files (`contracts.py`, `config.py`, `requirements.txt`, `.env.example`, README, shared docs) change only in a small `contract/<what>` PR.
- Before a PR, check that only your files changed: `git fetch origin && git diff --name-only origin/main...HEAD`.
- Use merge commits only: no squash, no rebase, no force-push. Stage named files, never `git add -A`.
- Several Claude sessions may work in this repo at once. Use a separate `git worktree` for another branch rather than switching branches under them.
