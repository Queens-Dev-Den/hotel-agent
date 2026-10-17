from datetime import datetime, timezone

import config
import costs
from contracts import CallResult, EmailReceipt, Run


def test_estimate_adds_every_source(monkeypatch):
    for name, value in [("BACKBOARD_USD_PER_M_IN", 0.10), ("BACKBOARD_USD_PER_M_OUT", 0.40),
                        ("AGENT_LLM_USD_PER_M", 0.13), ("ELEVENLABS_USD_PER_MIN", 0.08),
                        ("TELEPHONY_USD_PER_MIN", 0.014), ("SERPAPI_USD_PER_SEARCH", 0.025),
                        ("EMAIL_USD_PER_SEND", 0.0)]:
        monkeypatch.setattr(config, name, value)
    now = datetime.now(timezone.utc)
    run = Run(run_id="r", prompt="x", started_at=now,
              calls=[CallResult(candidate_rank=i, conversation_id="c", outcome="available", duration_s=60)
                     for i in (1, 2, 3)],
              email=EmailReceipt(message_id="m", sent_at=now))
    run.telemetry.tokens.update(backboard_in=1_000_000, backboard_out=500_000, agent_llm=2_000_000,
                                serpapi_searches=9)

    lines = {line.item: round(line.usd, 4) for line in costs.estimate(run)}

    assert lines["Backboard (prompt, fallback, memory)"] == 0.30  # 0.10 + 0.20
    assert lines["Voice agent LLM"] == 0.26
    assert lines["ElevenLabs call time"] == 0.24  # 3 calls x 1 min, billed per call
    assert lines["Phone line"] == 0.042
    assert lines["SerpApi search"] == 0.225
    assert costs.total_tokens(run) == 3_500_000
