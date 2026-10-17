# Team [X] — One-Shot Hotel Availability Agent (TechTO Fast & Frugal)

Planning docs only. No code has been written yet. Each teammate owns one workstream doc.

**What it does:** one prompt in, then the 3 cheapest hotels are called **at the same time** by an ElevenLabs voice agent that asks whether a room is available for the dates. **It never books.** One email goes out with each hotel's availability and the **full transcript of every call**.

| Doc | Owner | Covers |
|---|---|---|
| [docs/architecture.md](docs/architecture.md) | Lead | System design, data flow, token strategy, corrections to the original plan |
| [docs/ws1-discovery.md](docs/ws1-discovery.md) | Richard (T1) | SerpApi search, phone resolution, one-shot UI |
| [docs/ws2-backboard.md](docs/ws2-backboard.md) | Zach (T2) | Backboard prompt parsing + call plans, fallback extraction, token telemetry |
| [docs/ws3-voice.md](docs/ws3-voice.md) | Jordan (T3) | ElevenLabs agent, 3 parallel outbound calls, IVR/hold/voicemail, transcripts |
| [docs/ws4-email.md](docs/ws4-email.md) | Isaac (T4) | Resend email (availability + transcripts), extraction, deck |
| [docs/integration-and-demo.md](docs/integration-and-demo.md) | Everyone | Phase timeline, integration order, test matrix, demo runbook, open questions |

## Target repo layout (to be built)

```
config.py          # all env settings, single source of truth
telemetry.py       # Run object: stage timers + token counters -> run_telemetry.json
search_service.py  # WS1
agent_brain.py     # WS2 (prompt parsing + Backboard fallback)
voice_agent.py     # WS3 (agent setup + call lifecycle)
extractor.py       # WS4 (zero-token availability extraction)
email_service.py   # WS4
pipeline.py        # orchestration: parallel call fan-out/join + timer
main.py            # CLI: python main.py --city "Toronto"
app.py             # Streamlit one-shot UI
tests/             # extractor + prompt parsing unit tests
.env.example
```
