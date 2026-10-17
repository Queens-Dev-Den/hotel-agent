"""WS1 one-shot UI: streamlit run app.py

One input box, a live step list, and one live row per hotel call. The pipeline
runs on a background thread and reports progress through pipeline.run(on_event=...).
"""

from __future__ import annotations

import queue
import threading
import time

import streamlit as st

import config
import pipeline
from search_service import to_e164

STAGES = [
    ("brain", "Read prompt"),
    ("search", "Find hotels"),
    ("recall", "Check past calls"),
    ("call", "Call hotels"),
    ("email", "Send email"),
]
OUTCOMES = {
    "available": "✅ Available",
    "unavailable": "❌ No room",
    "voicemail": "📭 Voicemail",
    "no_answer": "📵 No answer",
    "error": "⚠️ Error",
}
POLL_S = 0.25


def _fmt_s(seconds: float) -> str:
    return f"{seconds:.1f} s" if seconds < 60 else f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def _start(prompt: str, email: str) -> tuple[threading.Thread, queue.Queue]:
    events: queue.Queue = queue.Queue()

    def work():
        try:
            pipeline.run(prompt, callback_email=email or None, on_event=lambda k, d: events.put((k, d)))
        except Exception as exc:
            events.put(("error", {"error": exc}))

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread, events


def _apply(state: dict, kind: str, data: dict) -> None:
    now = time.monotonic()
    if kind == "stage_start":
        state["stages"][data["name"]] = {"status": "running", "started": now}
    elif kind == "stage_done":
        state["stages"][data["name"]] = {"status": "done", "ms": data["ms"]}
    elif kind == "dialing":
        state["dialing"] = data["numbers"]
    elif kind in ("request", "candidates", "run", "error", "tokens"):
        state[kind] = data[kind]
    elif kind == "done":
        state["run"] = data["run"]
    elif kind == "call_start":
        state["calls"][data["rank"]] = {"started": now}
    elif kind == "call_done":
        call = data["call"]
        state["calls"][call.candidate_rank] = {"call": call}
    if kind == "error":
        for s in state["stages"].values():
            if s["status"] == "running":
                s["status"] = "failed"


def _render_steps(state: dict) -> None:
    now = time.monotonic()
    req, cands = state.get("request"), state.get("candidates")
    details = {
        "brain": f"{req.city} · {req.check_in:%b %d} → {req.check_out:%b %d} · {req.guests} guest(s)" if req else "",
        "search": f"{len(cands)} callable hotel(s)" if cands is not None else "",
        "recall": "Backboard memory" if "recall" in state["stages"] else "",
        "call": f"{sum('call' in c for c in state['calls'].values())}/{len(cands)} finished" if cands else "",
        "email": f"sent to {req.callback_email}" if req and state.get("run") else "",
    }
    for key, label in STAGES:
        s = state["stages"].get(key)
        if s is None:
            icon, took = "⬜", ""
        elif s["status"] == "running":
            icon, took = "⏳", _fmt_s(now - s["started"])
        elif s["status"] == "failed":
            icon, took = "❌", ""
        else:
            icon, took = "✅", _fmt_s(s["ms"] / 1000)
        a, b, c = st.columns([3, 1.2, 5])
        a.markdown(f"{icon} **{label}**")
        b.markdown(took)
        c.caption(details[key])


def _render_calls(state: dict) -> None:
    cands = state.get("candidates")
    if not cands:
        st.caption("Hotels appear here after the search.")
        return
    run = state.get("run")
    final = {a.candidate_rank: a for a in run.availability} if run else {}
    now = time.monotonic()
    for c in cands:
        live = state["calls"].get(c.rank)
        if live is None:
            status, took = "⬜ Waiting", ""
        elif "call" not in live:
            status, took = "📞 On call", _fmt_s(now - live["started"])
        else:
            call = live["call"]
            status, took = OUTCOMES.get(call.outcome, call.outcome), _fmt_s(call.duration_s)
            avail = final.get(c.rank) or call.availability
            if avail is not None and avail.available is None and call.outcome in ("available", "unavailable"):
                status = "❔ Unclear"
            if avail is not None and avail.quoted_rate:
                status += f" · quoted ${avail.quoted_rate:.0f}"
        a, b, c2, d, e = st.columns([0.4, 3.2, 1, 2, 2.6])
        a.markdown(f"**{c.rank}**")
        links = _links(c)
        b.markdown(f"**{c.name}**  \n<small>{c.address}</small>" + (f"  \n{links}" if links else ""),
                   unsafe_allow_html=True)
        c2.markdown(f"${c.total_price:.0f}")
        dialed = (state.get("dialing") or {}).get(c.rank)
        d.markdown(f"`{c.phone_e164}`" + (f"  \n<small>📲 ringing `{dialed}`</small>" if dialed and dialed != c.phone_e164 else ""),
                   unsafe_allow_html=True)
        e.markdown(f"{status}  \n<small>{took}</small>", unsafe_allow_html=True)


def _links(c) -> str:
    links = []
    if c.booking_url:
        links.append(f"[Book on {c.booking_source or 'booking site'} →]({c.booking_url})")
    if c.website:
        links.append(f"[Website]({c.website})")
    return " · ".join(links)


def _verdict(run) -> str:
    """Plain-English answer built from the results (0 tokens): what to book and why."""
    req = run.request
    nights = (req.check_out - req.check_in).days
    stay = f"{nights} night{'s' * (nights != 1)}, {req.check_in:%b %d} → {req.check_out:%b %d}, {req.guests} guest(s)"
    answers = {a.candidate_rank: a for a in run.availability}
    calls = {c.candidate_rank: c for c in run.calls}
    hotels = sorted(run.candidates, key=lambda c: c.total_price)
    confirmed = [c for c in hotels if answers.get(c.rank) and answers[c.rank].available]

    lines = []
    if confirmed:
        best = confirmed[0]
        a = answers[best.rank]
        lines.append(f"### ✅ Book **{best.name}**: ${best.total_price:.0f} {best.currency} for {stay}")
        said = "They confirmed a room is available on the phone"
        if a.quoted_rate:
            online = best.total_price / max(nights, 1)
            said += f", quoted **${a.quoted_rate:.0f}/night** (online: ${online:.0f}/night)"
        lines.append(said + ".")
        if a.reservation_number:
            lines.append(f"🧾 Reservation **#{a.reservation_number}**")
        links = _links(best)
        lines.append(f"{links} · 📞 `{best.phone_e164}` · {best.address}" if links else
                     f"📞 `{best.phone_e164}` · {best.address}")
        if best is not hotels[0]:
            lines.append(f"Cheaper online, but not confirmed: {hotels[0].name} (${hotels[0].total_price:.0f}).")
    else:
        lines.append(f"### ❔ No hotel confirmed a room for {stay}")
        lines.append("Cheapest options found online are below. Try calling them directly or book online.")

    others = []
    for c in hotels:
        if confirmed and c is confirmed[0]:
            continue
        call, a = calls.get(c.rank), answers.get(c.rank)
        status = OUTCOMES.get(call.outcome, call.outcome) if call else "not called"
        if a and a.available is None and call and call.outcome in ("available", "unavailable"):
            status = "❔ Unclear"
        link = f" · {_links(c)}" if _links(c) else ""
        others.append(f"- **{c.name}** ${c.total_price:.0f}: {status}{link}")
    if others:
        lines.append("**Other hotels called:**\n" + "\n".join(others))
    if all(c.conversation_id.startswith("mock-") for c in run.calls):
        lines.append("_Fake calls: these answers are simulated, no hotel was phoned._")
    return "\n\n".join(lines)


def _render_token_breakdown(run, backboard: int, agent: int) -> None:
    """Where every token went: counted tokens by step, the cached context we don't count, per call."""
    t = run.telemetry.tokens
    st.markdown(f"**{backboard + agent:,} tokens this run** = Backboard {backboard:,} + voice agent {agent:,}. "
                "The voice agent number matches the ElevenLabs dashboard.")
    st.dataframe([
        {"Step": "1. Understand the prompt", "Service": "Backboard",
         "Tokens": t.get("backboard_parse", 0), "What it is": "0 when our regex reads the prompt; LLM only as a fallback"},
        {"Step": "2. Search hotels", "Service": "SerpApi",
         "Tokens": 0, "What it is": f"No LLM. {t.get('serpapi_searches', 0)} searches"},
        {"Step": "3. Past-call notes", "Service": "Backboard memory",
         "Tokens": 0, "What it is": "Memory search and our own one-line summaries, no LLM"},
        {"Step": "4. Calls: what the agent heard", "Service": "ElevenLabs",
         "Tokens": t.get("agent_llm_input", 0), "What it is": "New input sent to the LLM each turn (mostly what the hotel said)"},
        {"Step": "4. Calls: what the agent said", "Service": "ElevenLabs",
         "Tokens": t.get("agent_llm_output", 0), "What it is": "The agent's replies and tool calls"},
        {"Step": "5. Read the answer", "Service": "Backboard",
         "Tokens": t.get("backboard_extract", 0), "What it is": "0 when the agent's tool call or our regex has the answer"},
        {"Step": "Total", "Service": "", "Tokens": backboard + agent, "What it is": ""},
    ], hide_index=True, width="stretch")

    cached = t.get("agent_llm_cache_read", 0) + t.get("agent_llm_cache_write", 0)
    st.markdown("**Not in the total: cached context**")
    st.caption("On every turn the agent re-reads ElevenLabs' built-in instructions, our prompt and the "
               "conversation so far (~3.5k tokens). That comes from cache and is billed at a fraction, so "
               "ElevenLabs doesn't count it as tokens used.")
    st.dataframe([
        {"Item": "Cache reads (context re-read each turn)", "Value": f"{t.get('agent_llm_cache_read', 0):,} tokens"},
        {"Item": "Cache writes (context stored, first turn of each call)", "Value": f"{t.get('agent_llm_cache_write', 0):,} tokens"},
        {"Item": "Everything the LLM processed (incl. cache and interrupted replies)", "Value": f"{t.get('agent_llm_context', 0):,} tokens"},
        {"Item": "ElevenLabs LLM credits charged", "Value": f"{t.get('agent_llm_credits', 0):,}"},
        {"Item": "Call time (all calls added up)", "Value": f"{sum(c.duration_s for c in run.calls) / 60:.1f} min"},
    ], hide_index=True, width="stretch")

    rows = []
    names = {c.rank: c.name for c in run.candidates}
    for call in sorted(run.calls, key=lambda c: c.candidate_rank):
        k = f"call{call.candidate_rank}_"
        rows.append({
            "Hotel": f"{call.candidate_rank}. {names.get(call.candidate_rank, '?')}",
            "Outcome": call.outcome,
            "Call length": f"{call.duration_s:.0f}s",
            "Heard (in)": t.get(k + "agent_llm_input", 0),
            "Said (out)": t.get(k + "agent_llm_output", 0),
            "Tokens": t.get(k + "agent_llm", 0),
            "Cached reads": t.get(k + "agent_llm_cache_read", 0),
            "Credits": t.get(k + "agent_llm_credits", 0),
        })
    if rows and cached:
        st.markdown("**Per call**")
        st.dataframe(rows, hide_index=True, width="stretch")


def _render_summary(state: dict, started: float) -> None:
    if state.get("error"):
        st.error(f"Run failed: {state['error']}")
        return
    run = state.get("run")
    if run is None:
        t = state.get("tokens") or {}
        backboard = t.get("backboard_in", 0) + t.get("backboard_out", 0)
        agent = t.get("agent_llm", 0)
        st.caption(f"Running… {_fmt_s(time.monotonic() - started)} · tokens so far: **{backboard + agent:,}** "
                   f"(Backboard {backboard:,} · voice agent {agent:,}) · SerpApi searches: "
                   f"{t.get('serpapi_searches', 0)}")
        return
    t = run.telemetry.tokens
    backboard = t.get("backboard_in", 0) + t.get("backboard_out", 0)
    agent = t.get("agent_llm", 0)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total time", _fmt_s(time.monotonic() - started if not state.get("finished") else state["finished"]))
    m2.metric("Tokens", f"{backboard + agent:,}")
    m3.metric("SerpApi searches", t.get("serpapi_searches", 0))
    m4.metric("Email", "Sent ✓" if run.email else "Not sent")
    with st.expander("Token and usage breakdown", expanded=True):
        _render_token_breakdown(run, backboard, agent)
    with st.expander("Transcripts"):
        for call in run.calls:
            st.markdown(f"**Hotel {call.candidate_rank}** · {call.outcome}")
            st.text(call.transcript or "(no transcript)")


# --- page ----------------------------------------------------------------------------

st.set_page_config(page_title="Hotel Agent", page_icon="🏨", layout="centered")
st.title("🏨 Hotel Agent")
st.caption("One prompt in. The 3 cheapest hotels get called at once. One email out.")

with st.sidebar:
    st.header("Test mode")
    # On by default until voice_agent and email_service are merged and tested
    fake_calls = st.checkbox("Fake calls (no phone calls)", value=True)
    fake_email = st.checkbox("Fake email (nothing sent)", value=True)
    fake_search = st.checkbox("Fake search (no SerpApi credits)", value="search" in config.MOCK_MODULES)
    pipeline.MOCK_CALL_DELAY_S = st.slider("Fake call length (s)", 0.0, 10.0, 3.0, 0.5)

    st.header("Who the agent calls")
    dial_block = None  # why Go is disabled, if it is
    if fake_calls:
        st.caption("Fake calls are on, so nobody is phoned. Turn them off to call a real phone.")
    else:
        target = st.radio("Call", ["Our own test number(s)", "The real hotels"], index=0,
                          help="Test numbers get the real call (same agent, same script), but ring "
                               "our phones instead of the hotels. The email still shows the real hotels.")
        if target == "Our own test number(s)":
            # Kept in a second key so the numbers survive switching to "real hotels" and back
            ss = st.session_state
            ss.setdefault("test_numbers_saved", "\n".join(config.MOCK_HOTEL_PHONES))
            ss["test_numbers"] = ss["test_numbers_saved"]
            raw = st.text_area("Test numbers, one per line (up to 3)", key="test_numbers",
                               placeholder="+16135550123\n(613) 555-0199",
                               on_change=lambda: ss.__setitem__("test_numbers_saved", ss["test_numbers"]))
            entered = [line.strip() for line in raw.splitlines() if line.strip()]
            numbers = [to_e164(n) for n in entered]
            bad = [e for e, n in zip(entered, numbers) if n is None]
            numbers = list(dict.fromkeys(n for n in numbers if n))[:3]
            if bad:
                dial_block = f"Not a valid phone number: {', '.join(bad)}"
            elif not numbers:
                dial_block = "Add at least one test number."
            else:
                st.success(f"Will call {len(numbers)} hotel(s), ringing: " + ", ".join(f"`{n}`" for n in numbers))
                if len(numbers) < 3:
                    st.caption("One number per call, so only the cheapest hotel(s) get called. "
                               "Add more numbers to run all 3 at once.")
            config.USE_MOCK_HOTEL, config.MOCK_HOTEL_PHONES = True, numbers
        else:
            st.warning("This phones the **real hotels** found by the search.")
            if not st.checkbox("Yes, I'm allowed to call real hotels"):
                dial_block = "Confirm you're allowed to call real hotels first."
            config.USE_MOCK_HOTEL, config.MOCK_HOTEL_PHONES = False, []
        if dial_block:
            st.error(dial_block)
    config.MOCK_MODULES = {m for m, on in (("voice", fake_calls), ("email", fake_email), ("search", fake_search)) if on}

with st.form("prompt"):
    prompt = st.text_input("What do you need?", placeholder="cheapest hotel in Toronto tonight")
    email = st.text_input("Send the results to", value=config.RECEIPT_TO_EMAIL, placeholder="you@example.com")
    go = st.form_submit_button("Go", type="primary", use_container_width=True, disabled=dial_block is not None)

verdict_box = st.empty()
st.subheader("Steps")
steps_box = st.empty()
st.subheader("Calls")
calls_box = st.empty()
summary_box = st.empty()

if go and prompt.strip():
    started = time.monotonic()
    state: dict = {"stages": {}, "calls": {}}
    thread, events = _start(prompt.strip(), email.strip())
    while True:
        alive = thread.is_alive()  # read before draining so no event is missed
        while not events.empty():
            _apply(state, *events.get())
        if not alive:
            state["finished"] = time.monotonic() - started
        with steps_box.container():
            _render_steps(state)
        with calls_box.container():
            _render_calls(state)
        with summary_box.container():
            _render_summary(state, started)
        if state.get("run") is not None:
            verdict_box.container(border=True).markdown(_verdict(state["run"]))
        if not alive:
            break
        time.sleep(POLL_S)
elif go:
    st.warning("Type a prompt first.")
