"""WS1 one-shot UI: streamlit run app.py

Glass "mission control" layout for the demo: prompt + Go, a 5-step progress rail, three
live call cards side by side, a verdict hero and a metrics strip. The pipeline runs
on a background thread and reports progress through pipeline.run(on_event=...).
"""

from __future__ import annotations

import html
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
POLL_S = 0.25

# Lucide icons (stroke, 24x24). SVG, not emoji: consistent across OSes and themeable.
ICONS = {
    "clock": '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    "phone": '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>',
    "check": '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>',
    "x": '<circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>',
    "voicemail": '<circle cx="5.5" cy="11.5" r="4.5"/><circle cx="18.5" cy="11.5" r="4.5"/><line x1="5.5" y1="16" x2="18.5" y2="16"/>',
    "missed": '<line x1="23" y1="1" x2="17" y2="7"/><line x1="17" y1="1" x2="23" y2="7"/><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>',
    "alert": '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "help": '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "link": '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>',
    "hotel": '<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M9 22v-4h6v4"/><path d="M8 6h.01M12 6h.01M16 6h.01M8 10h.01M12 10h.01M16 10h.01M8 14h.01M12 14h.01M16 14h.01"/>',
    "search": '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    "mail": '<path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/>',
    "cpu": '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3"/>',
    "receipt": '<path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z"/><path d="M16 8h-6M16 12h-6M13 16h-3"/>',
}

# status key -> (label, icon). Every status has an icon AND a label: never colour alone.
STATUS = {
    "waiting": ("Waiting", "clock"),
    "oncall": ("On call", "phone"),
    "available": ("Available", "check"),
    "unavailable": ("No room", "x"),
    "voicemail": ("Voicemail", "voicemail"),
    "no_answer": ("No answer", "missed"),
    "error": ("Error", "alert"),
    "unclear": ("Unclear", "help"),
}

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Fira+Code:wght@500;600&display=swap');
:root {
  --glass: rgba(255,255,255,0.07); --glass-2: rgba(255,255,255,0.11); --glass-border: rgba(255,255,255,0.16);
  --text: #F8FAFC; --muted: #B4BED3; --primary: #8B5CF6; --primary-2: #EC4899; --on-primary: #FFFFFF;
  --live: #22D3EE; --ok: #34D399; --bad: #FB7185; --warn: #FBBF24; --idle: #94A3B8;
  --blur: blur(18px) saturate(150%); --shadow: 0 8px 32px rgba(8, 6, 30, 0.45);
}
html, body, [class*="st-"] { font-family: 'Inter', system-ui, sans-serif; }

/* backdrop: deep violet -> navy with soft colour glows for the glass to blur */
.stApp {
  background:
    radial-gradient(900px 600px at 10% 5%, rgba(139,92,246,.35), transparent 60%),
    radial-gradient(800px 600px at 95% 15%, rgba(236,72,153,.22), transparent 60%),
    radial-gradient(900px 700px at 50% 110%, rgba(34,211,238,.18), transparent 60%),
    linear-gradient(160deg, #140B2E 0%, #0B1026 55%, #070B1A 100%);
  background-attachment: fixed;
}
header[data-testid="stHeader"] { background: transparent; }
section[data-testid="stSidebar"] > div { background: rgba(12, 10, 32, 0.55); backdrop-filter: var(--blur);
  -webkit-backdrop-filter: var(--blur); border-right: 1px solid var(--glass-border); }
.block-container { max-width: 1280px; padding-top: 2rem; }
.mono { font-family: 'Fira Code', ui-monospace, monospace; font-variant-numeric: tabular-nums; }
.ic { width: 1.1em; height: 1.1em; vertical-align: -0.18em; fill: none; stroke: currentColor;
      stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; flex: none; }

/* shared glass surface */
.glass, .step, .card, .hero, .metric, .empty {
  background: var(--glass); border: 1px solid var(--glass-border); box-shadow: var(--shadow), inset 0 1px 0 rgba(255,255,255,.08);
  backdrop-filter: var(--blur); -webkit-backdrop-filter: var(--blur);
}

.hdr { display: flex; align-items: center; gap: .75rem; margin-bottom: .25rem; }
.hdr h1 { font-size: 2rem; font-weight: 700; margin: 0; letter-spacing: -0.02em;
  background: linear-gradient(90deg, #FFFFFF 0%, #DDD6FE 60%, #FBCFE8 100%); -webkit-background-clip: text;
  background-clip: text; color: transparent; }
.hdr .ic { width: 2.1rem; height: 2.1rem; color: #C4B5FD; }
.sub { color: var(--muted); font-size: 1rem; margin-bottom: 1.25rem; }

/* inputs and the Go button */
div[data-baseweb="input"], div[data-baseweb="textarea"] { background: var(--glass-2) !important;
  border: 1px solid var(--glass-border) !important; border-radius: 12px !important; backdrop-filter: var(--blur); }
div[data-baseweb="input"] input { background: transparent !important; color: var(--text) !important; min-height: 2.6rem; }
div[data-testid="stFormSubmitButton"] button {
  background: linear-gradient(135deg, var(--primary) 0%, var(--primary-2) 100%) !important; border: 0 !important;
  color: var(--on-primary) !important; font-weight: 700; min-height: 2.75rem; font-size: 1.05rem; cursor: pointer;
  border-radius: 12px !important; box-shadow: 0 6px 24px rgba(139,92,246,.45); transition: transform .15s ease-out, box-shadow .15s ease-out; }
div[data-testid="stFormSubmitButton"] button:hover { transform: translateY(-1px); box-shadow: 0 10px 30px rgba(236,72,153,.45); }
div[data-testid="stFormSubmitButton"] button:disabled { opacity: .45; box-shadow: none; transform: none; }

/* progress rail */
.rail { display: flex; flex-wrap: wrap; gap: .6rem; margin: .75rem 0 1.25rem; }
.step { flex: 1 1 150px; border-radius: 14px; padding: .75rem .95rem; display: flex; gap: .6rem; align-items: center; min-height: 66px; }
.step .dot { width: 32px; height: 32px; border-radius: 50%; display: grid; place-items: center;
             border: 2px solid rgba(255,255,255,.28); color: var(--idle); font-weight: 700; font-size: .85rem; flex: none; }
.step .lbl { font-weight: 600; font-size: .95rem; }
.step .meta { color: var(--muted); font-size: .8rem; line-height: 1.3; }
.step.done .dot { background: linear-gradient(135deg, var(--primary), var(--primary-2)); border-color: transparent; color: #fff; }
.step.running { border-color: rgba(34,211,238,.55); }
.step.running .dot { border-color: var(--live); color: var(--live); }
.step.failed .dot { border-color: var(--bad); color: var(--bad); }

/* call cards */
.card { border-radius: 20px; padding: 1.15rem 1.25rem; display: flex; flex-direction: column; gap: .55rem;
        min-height: 330px; transition: border-color .2s ease-out, box-shadow .2s ease-out; }
.card .top { display: flex; justify-content: space-between; align-items: center; }
.rank { background: rgba(255,255,255,.1); color: var(--muted); border-radius: 999px; padding: .2rem .65rem; font-size: .8rem; font-weight: 600; }
.card h3 { font-size: 1.2rem; font-weight: 700; margin: 0; line-height: 1.3; }
.addr { color: var(--muted); font-size: .85rem; line-height: 1.4; }
.price { font-size: 2.1rem; font-weight: 600; line-height: 1; }
.price small { font-family: 'Inter', sans-serif; font-size: .8rem; color: var(--muted); font-weight: 500; margin-left: .35rem; }
.phone { color: var(--muted); font-size: .85rem; }
.ring { color: var(--live); font-size: .8rem; }
.pill { display: inline-flex; align-items: center; gap: .4rem; border-radius: 999px; padding: .35rem .85rem;
        font-weight: 700; font-size: .95rem; width: fit-content; background: rgba(255,255,255,.08);
        border: 1px solid currentColor; }
.timer { font-size: 1.5rem; }
.quote { font-size: .95rem; }
.links { margin-top: auto; display: flex; flex-wrap: wrap; gap: .5rem; }
.btn { display: inline-flex; align-items: center; gap: .35rem; border-radius: 12px; padding: .55rem .9rem; min-height: 44px;
       font-weight: 600; font-size: .9rem; text-decoration: none !important; cursor: pointer;
       border: 1px solid var(--glass-border); color: var(--text) !important; background: rgba(255,255,255,.08);
       transition: background .15s ease-out, transform .15s ease-out; }
.btn:hover { background: rgba(255,255,255,.16); transform: translateY(-1px); }
.btn:focus-visible, a:focus-visible { outline: 3px solid #C4B5FD; outline-offset: 2px; }
.btn.primary { background: linear-gradient(135deg, var(--primary), var(--primary-2)); color: #fff !important; border: 0;
               box-shadow: 0 6px 22px rgba(139,92,246,.45); }

.s-waiting { color: var(--idle); } .s-oncall { color: var(--live); } .s-available { color: var(--ok); }
.s-unavailable, .s-error { color: var(--bad); } .s-voicemail, .s-no_answer { color: var(--warn); } .s-unclear { color: var(--muted); }
.card.s-oncall { border-color: rgba(34,211,238,.6); box-shadow: var(--shadow), 0 0 28px rgba(34,211,238,.18); }
.card.s-available { border-color: rgba(52,211,153,.6); box-shadow: var(--shadow), 0 0 28px rgba(52,211,153,.16); }
.card .pill, .card .timer { color: inherit; }
.card h3, .card .price, .card .quote { color: var(--text); }

/* verdict hero */
.hero { border-radius: 22px; padding: 1.5rem 1.7rem; margin-bottom: 1.25rem; }
.hero.ok { border-color: rgba(52,211,153,.55); box-shadow: var(--shadow), 0 0 40px rgba(52,211,153,.14); }
.hero .kicker { font-size: .85rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; }
.hero h2 { font-size: 1.85rem; font-weight: 700; margin: .3rem 0 .4rem; line-height: 1.25; }
.hero p { color: var(--muted); margin: .2rem 0; font-size: 1rem; }
.hero .row { display: flex; flex-wrap: wrap; gap: .6rem; margin-top: .9rem; align-items: center; }
.note { color: var(--muted); font-size: .85rem; font-style: italic; margin-top: .6rem; }

/* metrics strip */
.metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: .6rem; margin: 1.25rem 0; }
.metric { border-radius: 14px; padding: .85rem 1rem; }
.metric .k { color: var(--muted); font-size: .8rem; display: flex; gap: .4rem; align-items: center; }
.metric .v { font-size: 1.6rem; font-weight: 600; margin-top: .15rem; }

.empty { border-style: dashed; border-radius: 20px; padding: 2.5rem; text-align: center; color: var(--muted); }

/* tabs, code and tables sit on glass too */
div[data-testid="stTabs"] [data-baseweb="tab-list"] { gap: .4rem; }
div[data-testid="stCode"] pre, div[data-testid="stDataFrame"] { background: rgba(10,8,30,.55) !important; border-radius: 12px; }

@media (prefers-reduced-motion: no-preference) {
  .step.running .dot, .card.s-oncall .pill { animation: pulse 1.6s ease-in-out infinite; }
  @keyframes pulse { 0%, 100% { box-shadow: 0 0 0 0 rgba(34,211,238,.5); } 50% { box-shadow: 0 0 0 8px rgba(34,211,238,0); } }
}
@media (prefers-reduced-motion: reduce) { .btn:hover, div[data-testid="stFormSubmitButton"] button:hover { transform: none; } }
</style>
"""


def _icon(name: str) -> str:
    return f'<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">{ICONS[name]}</svg>'


def _e(text) -> str:
    """Escape anything that came from the web, a hotel or a transcript."""
    return html.escape(str(text or ""))


def _url(url: str | None) -> str | None:
    return url if url and url.startswith(("https://", "http://")) else None


def _fmt_s(seconds: float) -> str:
    return f"{seconds:.1f}s" if seconds < 60 else f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def _html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


# --- run plumbing ---------------------------------------------------------------------

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


def _answers(state: dict) -> dict:
    run = state.get("run")
    return {a.candidate_rank: a for a in run.availability} if run else {}


def _call_status(c, state: dict) -> tuple[str, str, object]:
    """(status key, timer text, availability or None) for one hotel."""
    live = state["calls"].get(c.rank)
    if live is None:
        return "waiting", "", None
    if "call" not in live:
        return "oncall", _fmt_s(time.monotonic() - live["started"]), None
    call = live["call"]
    avail = _answers(state).get(c.rank) or call.availability
    key = call.outcome if call.outcome in STATUS else "error"
    if avail is not None and avail.available is None and call.outcome in ("available", "unavailable"):
        key = "unclear"
    return key, _fmt_s(call.duration_s), avail


# --- rendering ------------------------------------------------------------------------

def _render_rail(state: dict) -> None:
    now = time.monotonic()
    req, cands = state.get("request"), state.get("candidates")
    meta = {
        "brain": f"{_e(req.city)} · {req.check_in:%b %d}→{req.check_out:%b %d} · {req.guests} guest(s)" if req else "",
        "search": f"{len(cands)} callable within {getattr(config, 'SEARCH_RADIUS_KM', 20):g} km" if cands is not None else "",
        "recall": "Backboard memory" if "recall" in state["stages"] else "",
        "call": f"{sum('call' in c for c in state['calls'].values())}/{len(cands)} finished" if cands else "",
        "email": f"to {_e(req.callback_email)}" if req and state.get("run") and req.callback_email else "",
    }
    steps = []
    for i, (key, label) in enumerate(STAGES, start=1):
        s = state["stages"].get(key)
        if s is None:
            cls, dot, took = "", str(i), ""
        elif s["status"] == "running":
            cls, dot, took = "running", str(i), _fmt_s(now - s["started"])
        elif s["status"] == "failed":
            cls, dot, took = "failed", "!", "failed"
        else:
            cls, dot, took = "done", "✓", _fmt_s(s["ms"] / 1000)
        info = " · ".join(x for x in (f'<span class="mono">{took}</span>' if took else "", meta[key]) if x)
        steps.append(f'<div class="step {cls}" role="listitem" aria-label="Step {i}: {label}, {cls or "pending"}">'
                     f'<div class="dot">{dot}</div><div><div class="lbl">{label}</div>'
                     f'<div class="meta">{info}</div></div></div>')
    _html(f'<div class="rail" role="list">{"".join(steps)}</div>')


def _links(c, primary: bool = False) -> str:
    out = []
    if _url(c.booking_url):
        site = _e(c.booking_source or "booking site")
        out.append(f'<a class="btn{" primary" if primary else ""}" href="{_e(c.booking_url)}" target="_blank" '
                   f'rel="noopener noreferrer">Book on {site} {_icon("link")}</a>')
    if _url(c.website):
        out.append(f'<a class="btn" href="{_e(c.website)}" target="_blank" rel="noopener noreferrer">'
                   f'Website {_icon("link")}</a>')
    return "".join(out)


def _card(c, state: dict, nights: int) -> str:
    key, timer, avail = _call_status(c, state)
    label, icon = STATUS[key]
    dialed = (state.get("dialing") or {}).get(c.rank)
    ring = (f'<div class="ring">{_icon("phone")} ringing test number <span class="mono">{_e(dialed)}</span></div>'
            if dialed and dialed != c.phone_e164 else "")
    extra = ""
    if avail is not None and avail.quoted_rate:
        extra += f'<div class="quote">Quoted <span class="mono">${avail.quoted_rate:.0f}</span>/night on the phone</div>'
    if avail is not None and avail.reservation_number:
        extra += f'<div class="quote">{_icon("receipt")} Reservation <span class="mono">#{_e(avail.reservation_number)}</span></div>'
    stay = f"{c.currency} · {nights} night{'s' * (nights != 1)}"
    return (
        f'<div class="card s-{key}" role="group" aria-label="Hotel {c.rank}: {_e(c.name)}, {label}">'
        f'<div class="top"><span class="rank">#{c.rank} cheapest</span>'
        f'<span class="timer mono" aria-label="call time">{timer}</span></div>'
        f'<h3>{_e(c.name)}</h3><div class="addr">{_e(c.address)}</div>'
        f'<div class="price mono">${c.total_price:.0f}<small>{_e(stay)}</small></div>'
        f'<div class="phone">{_icon("phone")} <span class="mono">{_e(c.phone_e164)}</span></div>{ring}'
        f'<span class="pill" role="status">{_icon(icon)} {label}</span>{extra}'
        f'<div class="links">{_links(c)}</div></div>'
    )


def _render_cards(state: dict) -> None:
    cands, req = state.get("candidates"), state.get("request")
    if not cands:
        _html(f'<div class="empty">{_icon("hotel")}&nbsp; The cheapest callable hotels appear here after the search.</div>')
        return
    nights = max((req.check_out - req.check_in).days, 1) if req else 1
    for col, c in zip(st.columns(3, gap="medium"), cands):
        with col:
            _html(_card(c, state, nights))


def _render_hero(run) -> None:
    """The plain-English verdict (0 tokens): what to book and why."""
    req = run.request
    nights = max((req.check_out - req.check_in).days, 1)
    stay = f"{nights} night{'s' * (nights != 1)} · {req.check_in:%b %d} → {req.check_out:%b %d} · {req.guests} guest(s)"
    answers = {a.candidate_rank: a for a in run.availability}
    hotels = sorted(run.candidates, key=lambda c: c.total_price)
    confirmed = [c for c in hotels if answers.get(c.rank) and answers[c.rank].available]
    fake = all(c.conversation_id.startswith("mock-") for c in run.calls)
    note = '<div class="note">Fake calls: these answers are simulated, no hotel was phoned.</div>' if fake else ""

    if not confirmed:
        _html(f'<div class="hero"><div class="kicker s-unclear">{_icon("help")} No confirmation</div>'
              f'<h2>No hotel confirmed a room</h2><p>{_e(stay)}</p>'
              f'<p>The cheapest options found online are below. Book online or call them directly.</p>{note}</div>')
        return
    best = confirmed[0]
    a = answers[best.rank]
    said = "Confirmed available on the phone"
    if a.quoted_rate:
        said += f', quoted <span class="mono">${a.quoted_rate:.0f}</span>/night (online <span class="mono">${best.total_price / nights:.0f}</span>/night)'
    resv = (f'<p>{_icon("receipt")} Reservation <span class="mono">#{_e(a.reservation_number)}</span></p>'
            if a.reservation_number else "")
    cheaper = (f'<p>Cheaper online but not confirmed: {_e(hotels[0].name)} '
               f'(<span class="mono">${hotels[0].total_price:.0f}</span>).</p>' if best is not hotels[0] else "")
    _html(
        f'<div class="hero ok"><div class="kicker s-available">{_icon("check")} Recommended</div>'
        f'<h2>Book {_e(best.name)} · <span class="mono">${best.total_price:.0f}</span> {_e(best.currency)}</h2>'
        f'<p>{_e(stay)}</p><p>{said}.</p>{resv}{cheaper}'
        f'<div class="row">{_links(best, primary=True)}'
        f'<span class="phone">{_icon("phone")} <span class="mono">{_e(best.phone_e164)}</span> · {_e(best.address)}</span></div>'
        f'{note}</div>'
    )


def _render_metrics(state: dict, started: float) -> None:
    run = state.get("run")
    t = (run.telemetry.tokens if run else state.get("tokens")) or {}
    tokens = t.get("backboard_in", 0) + t.get("backboard_out", 0) + t.get("agent_llm", 0)
    elapsed = state.get("finished") or (time.monotonic() - started)
    email = "Sent" if run and run.email else ("Pending" if not state.get("error") else "Not sent")
    tiles = [("clock", "Total time", _fmt_s(elapsed)), ("cpu", "Tokens", f"{tokens:,}"),
             ("search", "SerpApi searches", str(t.get("serpapi_searches", 0))), ("mail", "Email", email)]
    _html('<div class="metrics">' + "".join(
        f'<div class="metric"><div class="k">{_icon(i)} {k}</div><div class="v mono">{_e(v)}</div></div>'
        for i, k, v in tiles) + "</div>")


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


def _render_details(run) -> None:
    t = run.telemetry.tokens
    backboard = t.get("backboard_in", 0) + t.get("backboard_out", 0)
    names = {c.rank: c.name for c in run.candidates}
    tabs = st.tabs(["Transcripts", "Token breakdown"])
    with tabs[0]:
        for call in sorted(run.calls, key=lambda c: c.candidate_rank):
            label = STATUS.get(call.outcome, (call.outcome, ""))[0]
            st.markdown(f"**#{call.candidate_rank} {names.get(call.candidate_rank, '')}** · {label} · {call.duration_s:.0f}s")
            st.code(call.transcript or "(no transcript)", language=None, wrap_lines=True)
    with tabs[1]:
        _render_token_breakdown(run, backboard, t.get("agent_llm", 0))


# --- page -----------------------------------------------------------------------------

st.set_page_config(page_title="Hotel Agent", page_icon="🏨", layout="wide")
_html(CSS)
_html(f'<div class="hdr">{_icon("hotel")}<h1>Hotel Agent</h1></div>'
      '<div class="sub">One prompt in. The 3 cheapest hotels get called at the same time. One email out.</div>')

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

with st.form("prompt", border=False):
    c1, c2, c3 = st.columns([5, 3, 1.2], vertical_alignment="bottom")
    prompt = c1.text_input("What do you need?", placeholder="cheapest hotel in Toronto tonight")
    email = c2.text_input("Send the results to", value=config.RECEIPT_TO_EMAIL, placeholder="you@example.com")
    go = c3.form_submit_button("Go", type="primary", use_container_width=True, disabled=dial_block is not None)
    if dial_block:
        st.caption(f"Go is disabled: {dial_block}")

rail_box = st.empty()
hero_box = st.empty()
cards_box = st.empty()
metrics_box = st.empty()
details_box = st.empty()

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
        with rail_box.container():
            _render_rail(state)
        with cards_box.container():
            _render_cards(state)
        with metrics_box.container():
            _render_metrics(state, started)
        if state.get("error"):
            hero_box.error(f"Run failed: {state['error']}")
        elif state.get("run") is not None:
            with hero_box.container():
                _render_hero(state["run"])
        if not alive:
            break
        time.sleep(POLL_S)
    if state.get("run") is not None:
        with details_box.container():
            _render_details(state["run"])
else:
    with rail_box.container():
        _render_rail({"stages": {}, "calls": {}})
    with cards_box.container():
        _render_cards({"stages": {}, "calls": {}})
    if go:
        st.warning("Type a prompt first.")
