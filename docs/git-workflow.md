# Git Workflow & Module Boundaries

**Owner:** Jordan (lead) · **Rule zero:** you only edit files your branch owns. If two people never touch the same file, merge conflicts are impossible.

## 1. Branches and file ownership

| Branch | Owner | May edit — nothing else |
|---|---|---|
| `t1/richard-search` | Richard (T1) | `search_service.py`, `pipeline.py`, `main.py`, `app.py`, `telemetry.py`, `tests/test_search.py`, `docs/ws1-discovery.md` |
| `t2/isaac-brain` | Isaac (T2) | `agent_brain.py`, `tests/test_brain.py`, `docs/ws2-backboard.md` |
| `t3/jordan-voice` | Jordan (T3) | `voice_agent.py`, `agents/` (booking + mock hotel prompts), `tests/test_voice.py`, `docs/ws3-voice.md` |
| `t4/zach-email` | Zach (T4) | `email_service.py`, `extractor.py`, `tool_server.py`, `tests/test_email.py`, `docs/ws4-email.md` |
| `contract/<what>` | anyone, all 4 agree in chat | **Shared files:** `contracts.py`, `mocks.py`, `config.py`, `requirements.txt`, `.env.example`, `.gitattributes`, `README.md`, `docs/architecture.md`, `docs/integration-and-demo.md`, `docs/git-workflow.md` |

- Shared files are written **once in Phase 0** and then frozen. A change to one is a tiny `contract/*` PR, merged straight away, and everyone syncs right after.
- `requirements.txt` is pre-filled with every dependency up front, so nobody edits it mid-build.
- `.gitattributes` forces LF line endings. Without it, Windows and macOS laptops create whole-file diffs that conflict.
- **Check before every PR**: this must list only files you own.

  ```bash
  git fetch origin && git diff --name-only origin/main...HEAD
  ```

## 2. Phase 0 tree (on `main` before 10:30)

Every module file starts as a stub: real function signatures plus `raise NotImplementedError`. Imports work on day one, and `pipeline.py` can swap any stub for its mock.

```
hotel-agent/
├── .env.example          shared   all settings (copy to .env)
├── .gitattributes        shared   LF line endings
├── .gitignore            shared
├── requirements.txt      shared   frozen, all deps listed up front
├── README.md             shared
├── config.py             shared   loads .env; MOCK_MODULES switch
├── contracts.py          shared   Pydantic models below
├── mocks.py              shared   fixture version of every module function
├── telemetry.py          T1       stage(), add_tokens(), save() — signatures frozen
├── search_service.py     T1       find_candidates()
├── pipeline.py           T1       run() + failover loop
├── main.py               T1       CLI
├── app.py                T1       Streamlit one-shot UI
├── agent_brain.py        T2       parse_prompt(), build_call_plan(), fallback_extract()
├── voice_agent.py        T3       place_call()
├── agents/
│   ├── booking_prompt.md     T3
│   └── mock_hotel_prompt.md  T3
├── extractor.py          T4       validate(), from_transcript()
├── email_service.py      T4       build_payload(), send()
├── tool_server.py        T4       /record_confirmation, register_run(), wait_for_confirmation()
├── tests/
│   ├── test_contracts.py shared   mock chain smoke test
│   ├── test_search.py    T1
│   ├── test_brain.py     T2
│   ├── test_voice.py     T3
│   └── test_email.py     T4
└── docs/                 one ws doc per owner; the rest shared
```

## 3. Data contracts (`contracts.py`, frozen at Gate 0)

The flow: **HotelRequest → SearchResult → CallPlan → CallResult → EmailPayload → EmailReceipt**, all collected in one `Run`.

| Model | Producer → consumer | Fields |
|---|---|---|
| `HotelRequest` | T2 `parse_prompt` → T1, T3 | `city`, `check_in: date`, `check_out: date`, `guests: int = 1`, `guest_name`, `callback_email` |
| `HotelCandidate` | T1 `find_candidates` → T2, T3, T4 | `rank` (1 = cheapest), `name`, `address`, `phone_e164` (validated `^\+[1-9]\d{6,14}$`), `total_price: float`, `currency = "CAD"`, `source` |
| `SearchResult` | T1 → T2 | `request: HotelRequest`, `candidates: list[HotelCandidate]` (sorted by price, max 3) |
| `CallPlan` | T2 `build_call_plan` → T3 | `run_id`, `candidate`, `system_prompt` (≤ 300 tokens), `first_message`, `dynamic_vars: dict[str, str]` (**must include `run_id` and `candidate_rank`**), `prompt_tokens` |
| `ToolConfirmationRequest` | ElevenLabs server tool → T4 `tool_server` | `run_id`, `candidate_rank`, `reservation_number` (raw), `reservation_date` (raw string) |
| `Confirmation` | T4 `extractor.validate` → T3, T4 | `reservation_number` (uppercase, no spaces), `reservation_date: date`, `readback_confirmed: bool`, `source: "tool" \| "transcript" \| "backboard"` |
| `CallResult` | T3 `place_call` → T1 | `candidate_rank`, `conversation_id`, `outcome: booked \| voicemail \| no_answer \| refused \| error`, `duration_s`, `confirmation: Confirmation \| None`, `transcript: str \| None` |
| `EmailPayload` | T4 `build_payload` → `send` | `to`, `subject` (exact competition string), `hotel_name`, `address`, `phone` (all from the candidate), `reservation_number`, `reservation_date` (from the confirmation) |
| `EmailReceipt` | T4 `send` → T1 | `message_id`, `sent_at: datetime` |
| `Run` | T1 `pipeline.run` → `run_telemetry.json` | `run_id`, `prompt`, `started_at`, `request`, `candidates`, `calls`, `confirmation`, `email`, `telemetry{stage_ms, tokens}` |

## 4. Merge gates

Merge to `main` as soon as your tests pass. Small and often beats one big merge. The gates are the latest you can merge, not the only times.

| Gate | Time | What must be on `main` | Who merges |
|---|---|---|---|
| **G0** | 10:30 | Phase 0 tree, contracts, mocks. All 4 branches created from this commit. | Jordan |
| **G1 skeleton** | 12:30 | T4 `tool_server` + `email_service` real. T3 `place_call` real against the mock hotel. T1/T2 may still be mocks. | Zach, then Jordan |
| **G2 Phase 1 exit** | 13:00 | Every module's first real version, with its unit tests passing | all |
| **G3 integration** | 15:30 | `MOCK_MODULES` empty. 3 green runs in a row against the mock hotel. | Richard (owns `pipeline.py`) |
| **G4 freeze** | 16:45 | Tag `v-final`. Bug fixes after this only with the whole team's OK. | Jordan |

**Sync rule:** `git pull origin main` into your branch right after every gate, and at least once an hour.

## 5. Git commands

**Merge commits only: no squash, no rebase, no force-push.** Squash merges rewrite history and make the next sync conflict. With merge commits, syncing your branch is always a fast, conflict-free pull.

**Once, at G0 (each person):**

```bash
git clone git@github.com:Queens-Dev-Den/hotel-agent.git && cd hotel-agent
cp .env.example .env            # fill in your keys
git switch -c t3/jordan-voice   # your branch name from section 1
git push -u origin t3/jordan-voice
```

**While working:**

```bash
git add voice_agent.py agents/ tests/test_voice.py   # name your files; never `git add -A`
git commit -m "voice: outbound call + outcome classification"
git push
```

**Sync from main** (after each gate, and hourly):

```bash
git pull origin main      # merge, not rebase; no conflicts if you only touched your files
```

**Merge to main:**

```bash
git fetch origin && git diff --name-only origin/main...HEAD   # only your files?
python -m pytest -q                                            # green?
gh pr create --base main --fill
gh pr merge --merge                                            # merge commit; keep the branch
```

**Changing a shared file:**

```bash
git switch main && git pull
git switch -c contract/add-hold-timeout
# one small edit, announce it in chat
git commit -am "contract: add HOLD_TIMEOUT_S" && git push -u origin HEAD
gh pr create --base main --fill && gh pr merge --merge --delete-branch
# then everyone: git pull origin main
```

**If you do hit a conflict:** stop and post in chat. Don't resolve it inside someone else's file.

## 6. Integration protocol (Phase 2)

`main.py` parses the CLI argument and calls `pipeline.run(prompt)`. `app.py` calls the same function. `pipeline.run` is the only orchestrator:

1. Start `tool_server` on a background thread (T4). It has to be up before any call can fire the tool.
2. `request = parse_prompt(prompt)` (T2), timed as the `brain` stage.
3. `candidates = find_candidates(request)` (T1), timed as the `search` stage.
4. For each candidate, cheapest first:
   1. `plan = build_call_plan(request, candidate, run_id)` (T2)
   2. `tool_server.register_run(run, candidate)` (T4)
   3. `result = place_call(plan)` (T3), timed as the `call` stage. During the call, the agent's `record_confirmation` tool hits `tool_server`. That validates the code (`extractor`) and **sends the email right away** (`email_service`).
   4. If `result.outcome == booked`: stop. Otherwise go to the next candidate.
5. If the call was booked but the tool never fired: `extractor.from_transcript`, then `agent_brain.fallback_extract`, then send the email.
6. `telemetry.save(run)`. Total time = `email.sent_at − started_at`.

### Mock fallbacks for a late branch

`mocks.py` has one function with the same name and signature for every real module function. `config.MOCK_MODULES` comes from `.env` (for example `MOCK_MODULES=search,brain`). `pipeline.py` picks the implementation per module:

| `MOCK_MODULES` contains | Replaced with fixtures |
|---|---|
| `search` | `find_candidates` → 3 fake Toronto hotels |
| `brain` | `parse_prompt`, `build_call_plan` → fixed request and plan |
| `voice` | `place_call` → instant `booked` with code `MOCK1234` |
| `email` | `send` → prints instead of sending |

- If a branch misses G1 or G2, everyone else keeps integrating with that module mocked. The late owner merges once their tests pass, and the pipeline needs no code change.
- `tests/test_contracts.py` runs the whole chain on mocks. If it fails on `main`, a contract was broken, so fix that before anything else.
- At G3, `MOCK_MODULES` must be empty. For the demo, `USE_MOCK_HOTEL=false` only if the organizers confirm which numbers we're allowed to dial.
