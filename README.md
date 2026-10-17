# Team [X] — One-Shot Hotel Booking Agent (TechTO Fast & Frugal)

Planning docs only. No code has been written yet. Each teammate owns one workstream doc.

| Doc | Owner | Covers |
|---|---|---|
| [docs/architecture.md](docs/architecture.md) | Lead | System design, data flow, token strategy, corrections to the original plan |
| [docs/ws1-discovery.md](docs/ws1-discovery.md) | Teammate 1 | SerpApi search, phone resolution, one-shot UI |
| [docs/ws2-backboard.md](docs/ws2-backboard.md) | Teammate 2 | Backboard extraction fallback, token telemetry |
| [docs/ws3-voice.md](docs/ws3-voice.md) | Teammate 3 | ElevenLabs agent, outbound call, IVR/hold/voicemail, failover |
| [docs/ws4-email.md](docs/ws4-email.md) | Teammate 4 | Resend email, confirmation extraction, deck |
| [docs/integration-and-demo.md](docs/integration-and-demo.md) | Everyone | Phase timeline, integration order, test matrix, demo runbook |

## Target repo layout (to be built)

```
config.py          # all env settings, single source of truth
telemetry.py       # Run object: stage timers + token counters -> run_telemetry.json
search_service.py  # WS1
agent_brain.py     # WS2 (prompt parsing + Backboard fallback)
voice_agent.py     # WS3 (agent setup + call lifecycle)
extractor.py       # WS4 (zero-token confirmation extraction)
email_service.py   # WS4
pipeline.py        # orchestration + failover loop
main.py            # CLI: python main.py --city "Toronto"
app.py             # Streamlit one-shot UI
tests/             # extractor + prompt parsing unit tests
.env.example
```
