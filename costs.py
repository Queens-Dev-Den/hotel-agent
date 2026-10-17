"""WS1: estimated dollar cost of one run, from Run.telemetry and the rates in config.py.

List prices, not our bill: we're on free tiers. Shown in the UI and usable in the deck.
"""

from __future__ import annotations

from dataclasses import dataclass

import config
from contracts import Run


@dataclass
class Line:
    item: str
    usage: str
    usd: float


def estimate(run: Run) -> list[Line]:
    """One line per cost source. Call minutes are billed per call, so they add up across
    the 3 parallel calls even though the wall-clock time doesn't."""
    tokens = run.telemetry.tokens
    bb_in, bb_out = tokens.get("backboard_in", 0), tokens.get("backboard_out", 0)
    agent = tokens.get("agent_llm", 0)
    searches = tokens.get("serpapi_searches", 0)
    minutes = sum(c.duration_s for c in run.calls) / 60
    return [
        Line("Backboard (prompt, fallback, memory)", f"{bb_in:,} in + {bb_out:,} out tokens",
             bb_in / 1e6 * config.BACKBOARD_USD_PER_M_IN + bb_out / 1e6 * config.BACKBOARD_USD_PER_M_OUT),
        Line("Voice agent LLM", f"{agent:,} tokens", agent / 1e6 * config.AGENT_LLM_USD_PER_M),
        Line("ElevenLabs call time", f"{minutes:.1f} call-min", minutes * config.ELEVENLABS_USD_PER_MIN),
        Line("Phone line", f"{minutes:.1f} call-min", minutes * config.TELEPHONY_USD_PER_MIN),
        Line("SerpApi search", f"{searches} searches", searches * config.SERPAPI_USD_PER_SEARCH),
        Line("Email (Resend)", "1 email" if run.email else "not sent",
             config.EMAIL_USD_PER_SEND if run.email else 0.0),
    ]


def total_tokens(run: Run) -> int:
    t = run.telemetry.tokens
    return t.get("backboard_in", 0) + t.get("backboard_out", 0) + t.get("agent_llm", 0)
