"""All settings, loaded once from .env. The only module that reads env vars.

Each owner adds their own section below; don't create a second config file.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


def _float(name: str, default: float) -> float:
    value = os.getenv(name, "").strip()
    return float(value) if value else default


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    return value in ("1", "true", "yes") if value else default


# --- WS1: Search (search_service.py) -------------------------------------------
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "").strip()
SEARCH_CURRENCY = os.getenv("SEARCH_CURRENCY", "CAD").strip() or "CAD"
# Cheapest hotels that get a parallel phone/address lookup (1 SerpApi credit each)
SEARCH_TOP_N = _int("SEARCH_TOP_N", 5)
# Callable hotels kept after filtering (contract: max 3)
MAX_CANDIDATES = min(_int("MAX_CANDIDATES", 3), 3)
# Airbnb-style rentals: searched alongside hotels, but they rarely list a phone
SEARCH_RENTALS = _bool("SEARCH_RENTALS", True)
RENTAL_FANOUT = _int("RENTAL_FANOUT", 2)
HTTP_TIMEOUT_S = _int("HTTP_TIMEOUT_S", 10)
# Replay raw API responses from .cache/ in dev so test loops don't burn quota
SEARCH_CACHE = _bool("SEARCH_CACHE", False)


# --- Competition / pipeline (shared) -------------------------------------------
# Team number from organizers -> subject "Team # {TEAM_NO} - Hotel Reservation Confirmed!"
TEAM_NO = os.getenv("TEAM_NO", "").strip()
# Where the availability email (with call transcripts) is sent
RECEIPT_TO_EMAIL = os.getenv("RECEIPT_TO_EMAIL", "").strip()
# Modules replaced by mocks.py fixtures, e.g. MOCK_MODULES=search,brain (see git-workflow.md)
MOCK_MODULES = frozenset(m.strip() for m in os.getenv("MOCK_MODULES", "").split(",") if m.strip())

# --- WS4: Email + tool endpoint (email_service.py, tool_server.py) -------------
# resend | smtp
EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "resend").strip().lower() or "resend"
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
EMAIL_FROM = os.getenv("EMAIL_FROM", "onboarding@resend.dev").strip() or "onboarding@resend.dev"
# Gmail SMTP fallback (an app password, not the account password)
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com").strip() or "smtp.gmail.com"
SMTP_PORT = _int("SMTP_PORT", 587)
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_APP_PASSWORD = os.getenv("SMTP_APP_PASSWORD", "").strip()
# Public URL of the record_availability endpoint (ngrok/cloudflared/deploy)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
# Shared secret the ElevenLabs server tool sends in X-Tool-Secret
TOOL_SHARED_SECRET = os.getenv("TOOL_SHARED_SECRET", "").strip()
TOOL_SERVER_PORT = _int("TOOL_SERVER_PORT", 8000)

# --- WS2: Backboard (agent_brain.py) -------------------------------------------
BACKBOARD_API_KEY = os.getenv("BACKBOARD_API_KEY", "").strip()
BACKBOARD_BASE_URL = (os.getenv("BACKBOARD_BASE_URL", "").strip() or "https://app.backboard.io/api").rstrip("/")
# "<llm_provider>/<model_name>"; gemini-2.5-flash-lite won the Phase 0 latency test
BACKBOARD_MODEL = os.getenv("BACKBOARD_MODEL", "").strip() or "google/gemini-2.5-flash-lite"
# Hard cap per session (see docs/architecture.md token strategy)
BACKBOARD_TOKEN_BUDGET = _int("BACKBOARD_TOKEN_BUDGET", 1200)
# Assistant that stores per-hotel call memories; created on first run if empty
BACKBOARD_ASSISTANT_ID = os.getenv("BACKBOARD_ASSISTANT_ID", "").strip()

# --- WS1: Pipeline (pipeline.py) -----------------------------------------------
# Safety switch: dial MOCK_HOTEL_PHONES instead of the real hotels. Defaults ON.
USE_MOCK_HOTEL = _bool("USE_MOCK_HOTEL", True)
MOCK_HOTEL_PHONES = [p.strip() for p in os.getenv("MOCK_HOTEL_PHONES", "").split(",") if p.strip()]
MAX_CALL_DURATION_S = _int("MAX_CALL_DURATION_S", 180)

# --- WS1: Cost estimate (costs.py), list prices in USD as of Oct 2026 ------------
# Gemini 2.5 Flash-Lite via Backboard: $0.10 / $0.40 per 1M input / output tokens
BACKBOARD_USD_PER_M_IN = _float("BACKBOARD_USD_PER_M_IN", 0.10)
BACKBOARD_USD_PER_M_OUT = _float("BACKBOARD_USD_PER_M_OUT", 0.40)
# Voice agent LLM tokens (one combined count; ~90% input at the same model's rates)
AGENT_LLM_USD_PER_M = _float("AGENT_LLM_USD_PER_M", 0.13)
# ElevenLabs Agents: $0.08 per call minute on every paid plan (LLM billed separately)
ELEVENLABS_USD_PER_MIN = _float("ELEVENLABS_USD_PER_MIN", 0.08)
# Outbound phone line per minute (Twilio/SIP), an estimate
TELEPHONY_USD_PER_MIN = _float("TELEPHONY_USD_PER_MIN", 0.014)
# SerpApi Starter plan: $25 / 1,000 searches (the free plan's 250/month cost $0)
SERPAPI_USD_PER_SEARCH = _float("SERPAPI_USD_PER_SEARCH", 0.025)
# Resend: free tier (3,000 emails/month)
EMAIL_USD_PER_SEND = _float("EMAIL_USD_PER_SEND", 0.0)

# --- WS3: Voice (voice_agent.py) -----------------------------------------------
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "").strip()
# The imported Twilio number's ElevenLabs id (phnum_...), not the +1 number
ELEVENLABS_AGENT_PHONE_NUMBER_ID = os.getenv("ELEVENLABS_AGENT_PHONE_NUMBER_ID", "").strip()
ELEVENLABS_BASE_URL = (os.getenv("ELEVENLABS_BASE_URL", "").strip() or "https://api.elevenlabs.io").rstrip("/")
ELEVENLABS_HTTP_TIMEOUT_S = _int("ELEVENLABS_HTTP_TIMEOUT_S", 15)
# How often place_call polls the conversation while the call is live
VOICE_POLL_INTERVAL_S = _float("VOICE_POLL_INTERVAL_S", 2.0)
# Extra wait past MAX_CALL_DURATION_S for the call to wrap up (pipeline allows +30)
TRANSCRIPT_WAIT_S = _int("TRANSCRIPT_WAIT_S", 20)
# Re-fetches when a finished call's transcript is still empty
TRANSCRIPT_RETRIES = _int("TRANSCRIPT_RETRIES", 4)
