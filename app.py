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
    elif kind in ("request", "candidates", "run", "error"):
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
        b.markdown(f"**{c.name}**  \n<small>{c.address}</small>", unsafe_allow_html=True)
        c2.markdown(f"${c.total_price:.0f}")
        d.markdown(f"`{c.phone_e164}`")
        e.markdown(f"{status}  \n<small>{took}</small>", unsafe_allow_html=True)


def _render_summary(state: dict, started: float) -> None:
    if state.get("error"):
        st.error(f"Run failed: {state['error']}")
        return
    run = state.get("run")
    if run is None:
        st.caption(f"Running… {_fmt_s(time.monotonic() - started)}")
        return
    tokens = run.telemetry.tokens
    m1, m2, m3 = st.columns(3)
    m1.metric("Total time", _fmt_s(time.monotonic() - started if not state.get("finished") else state["finished"]))
    m2.metric("Backboard tokens", tokens.get("backboard_in", 0) + tokens.get("backboard_out", 0))
    m3.metric("Email", "Sent ✓" if run.email else "Not sent")
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
    if not fake_calls:
        if config.USE_MOCK_HOTEL:
            st.info(f"Calls go to the mock hotel numbers ({len(config.MOCK_HOTEL_PHONES)} set).")
        else:
            st.warning("USE_MOCK_HOTEL is off: this will phone REAL hotels.")
    config.MOCK_MODULES = {m for m, on in (("voice", fake_calls), ("email", fake_email), ("search", fake_search)) if on}

with st.form("prompt"):
    prompt = st.text_input("What do you need?", placeholder="cheapest hotel in Toronto tonight")
    email = st.text_input("Send the results to", value=config.RECEIPT_TO_EMAIL, placeholder="you@example.com")
    go = st.form_submit_button("Go", type="primary", use_container_width=True)

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
        if not alive:
            break
        time.sleep(POLL_S)
elif go:
    st.warning("Type a prompt first.")
