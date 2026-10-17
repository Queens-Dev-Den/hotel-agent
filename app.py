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

STAGES = [
    ("brain", "Read prompt"),
    ("search", "Find hotels"),
    ("recall", "Past calls"),
    ("call", "Call hotels"),
    ("retry", "Call 3 more"),  # only shown when no first-round hotel confirmed a room with a rate
    ("email", "Send email"),
]
POLL_S = 0.25
DEFAULT_RECIPIENT = "hackathon@techto.org"  # pre-filled in the email box; whatever is typed there wins

# Lucide icons (lucide.dev, ISC licence): official SVG bodies, 24x24 stroke. SVG, not emoji.
ICONS = {
    "clock": "<circle cx=\"12\" cy=\"12\" r=\"10\"/><path d=\"M12 6v6l4 2\"/>",
    "phone": "<path d=\"M13.832 16.568a1 1 0 0 0 1.213-.303l.355-.465A2 2 0 0 1 17 15h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2A18 18 0 0 1 2 4a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v3a2 2 0 0 1-.8 1.6l-.468.351a1 1 0 0 0-.292 1.233 14 14 0 0 0 6.392 6.384\"/>",
    "check": "<circle cx=\"12\" cy=\"12\" r=\"10\"/><path d=\"m16 9-5.5 5.5L8 12\"/>",
    "x": "<circle cx=\"12\" cy=\"12\" r=\"10\"/><path d=\"m15 9-6 6\"/><path d=\"m9 9 6 6\"/>",
    "voicemail": "<circle cx=\"6\" cy=\"12\" r=\"4\"/><circle cx=\"18\" cy=\"12\" r=\"4\"/><line x1=\"6\" x2=\"18\" y1=\"16\" y2=\"16\"/>",
    "missed": "<path d=\"m16 2 6 6\"/><path d=\"m22 2-6 6\"/><path d=\"M13.832 16.568a1 1 0 0 0 1.213-.303l.355-.465A2 2 0 0 1 17 15h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2A18 18 0 0 1 2 4a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v3a2 2 0 0 1-.8 1.6l-.468.351a1 1 0 0 0-.292 1.233 14 14 0 0 0 6.392 6.384\"/>",
    "alert": "<path d=\"m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3\"/><path d=\"M12 9v4\"/><path d=\"M12 17h.01\"/>",
    "help": "<circle cx=\"12\" cy=\"12\" r=\"10\"/><path d=\"M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3\"/><path d=\"M12 17h.01\"/>",
    "link": "<path d=\"M15 3h6v6\"/><path d=\"M10 14 21 3\"/><path d=\"M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6\"/>",
    "hotel": "<path d=\"M10 22v-6.57\"/><path d=\"M12 11h.01\"/><path d=\"M12 7h.01\"/><path d=\"M14 15.43V22\"/><path d=\"M15 16a5 5 0 0 0-6 0\"/><path d=\"M16 11h.01\"/><path d=\"M16 7h.01\"/><path d=\"M8 11h.01\"/><path d=\"M8 7h.01\"/><rect x=\"4\" y=\"2\" width=\"16\" height=\"20\" rx=\"2\"/>",
    "search": "<path d=\"m21 21-4.34-4.34\"/><circle cx=\"11\" cy=\"11\" r=\"8\"/>",
    "mail": "<path d=\"m22 7-8.991 5.727a2 2 0 0 1-2.009 0L2 7\"/><rect x=\"2\" y=\"4\" width=\"20\" height=\"16\" rx=\"2\"/>",
    "cpu": "<path d=\"M12 20v2\"/><path d=\"M12 2v2\"/><path d=\"M17 20v2\"/><path d=\"M17 2v2\"/><path d=\"M2 12h2\"/><path d=\"M2 17h2\"/><path d=\"M2 7h2\"/><path d=\"M20 12h2\"/><path d=\"M20 17h2\"/><path d=\"M20 7h2\"/><path d=\"M7 20v2\"/><path d=\"M7 2v2\"/><rect x=\"4\" y=\"4\" width=\"16\" height=\"16\" rx=\"2\"/><rect x=\"8\" y=\"8\" width=\"8\" height=\"8\" rx=\"1\"/>",
    "receipt": "<path d=\"M12 17V7\"/><path d=\"M16 8h-6a2 2 0 0 0 0 4h4a2 2 0 0 1 0 4H8\"/><path d=\"M4 3a1 1 0 0 1 1-1 1.3 1.3 0 0 1 .7.2l.933.6a1.3 1.3 0 0 0 1.4 0l.934-.6a1.3 1.3 0 0 1 1.4 0l.933.6a1.3 1.3 0 0 0 1.4 0l.933-.6a1.3 1.3 0 0 1 1.4 0l.934.6a1.3 1.3 0 0 0 1.4 0l.933-.6A1.3 1.3 0 0 1 19 2a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1 1.3 1.3 0 0 1-.7-.2l-.933-.6a1.3 1.3 0 0 0-1.4 0l-.934.6a1.3 1.3 0 0 1-1.4 0l-.933-.6a1.3 1.3 0 0 0-1.4 0l-.933.6a1.3 1.3 0 0 1-1.4 0l-.934-.6a1.3 1.3 0 0 0-1.4 0l-.933.6a1.3 1.3 0 0 1-.7.2 1 1 0 0 1-1-1z\"/>",
    "sparkles": "<path d=\"M11.017 2.814a1 1 0 0 1 1.966 0l1.051 5.558a2 2 0 0 0 1.594 1.594l5.558 1.051a1 1 0 0 1 0 1.966l-5.558 1.051a2 2 0 0 0-1.594 1.594l-1.051 5.558a1 1 0 0 1-1.966 0l-1.051-5.558a2 2 0 0 0-1.594-1.594l-5.558-1.051a1 1 0 0 1 0-1.966l5.558-1.051a2 2 0 0 0 1.594-1.594z\"/><path d=\"M20 2v4\"/><path d=\"M22 4h-4\"/><circle cx=\"4\" cy=\"20\" r=\"2\"/>",
    "pin": "<path d=\"M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0\"/><circle cx=\"12\" cy=\"10\" r=\"3\"/>",
    "flask": "<path d=\"M14 2v6a2 2 0 0 0 .245.96l5.51 10.08A2 2 0 0 1 18 22H6a2 2 0 0 1-1.755-2.96l5.51-10.08A2 2 0 0 0 10 8V2\"/><path d=\"M6.453 15h11.094\"/><path d=\"M8.5 2h7\"/>",
    "phonecall": "<path d=\"M13 2a9 9 0 0 1 9 9\"/><path d=\"M13 6a5 5 0 0 1 5 5\"/><path d=\"M13.832 16.568a1 1 0 0 0 1.213-.303l.355-.465A2 2 0 0 1 17 15h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2A18 18 0 0 1 2 4a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v3a2 2 0 0 1-.8 1.6l-.468.351a1 1 0 0 0-.292 1.233 14 14 0 0 0 6.392 6.384\"/>",
    "settings": "<path d=\"M14 17H5\"/><path d=\"M19 7h-9\"/><circle cx=\"17\" cy=\"17\" r=\"3\"/><circle cx=\"7\" cy=\"7\" r=\"3\"/>",
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
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Fira+Code:wght@500;600&display=swap');
:root {
  /* type scale (1.25) and radii: one set for the whole page */
  --fs-xs: .8125rem; --fs-sm: .875rem; --fs-md: 1rem; --fs-lg: 1.125rem; --fs-xl: 1.375rem; --fs-2xl: 1.75rem; --fs-3xl: 2.375rem;
  --r-sm: 10px; --r-md: 16px; --r-lg: 22px;
  --text: #FFFFFF; --muted: #C9D1E4; --faint: #9AA6C2;
  --primary: #7C3AED; --primary-hover: #6D28D9; --live: #22D3EE; --ok: #34D399; --bad: #FB7185; --warn: #FBBF24; --idle: #A5B0C9;
  /* frosted glass, kept quiet: flat translucent fill, blur, thin rim */
  --glass-bg: rgba(255,255,255,.06);
  --glass-rim: rgba(255,255,255,.14);
  --glass-blur: blur(20px) saturate(140%);
  --glass-shadow: 0 6px 24px rgba(2,4,16,.35), inset 0 1px 0 rgba(255,255,255,.08);
}

/* one font everywhere, except Streamlit's icon font (it draws the sidebar arrows) and code */
.stApp, .stApp *:not([data-testid="stIconMaterial"]):not(.mono):not(code):not(pre):not(pre *) {
  font-family: 'Inter', system-ui, sans-serif;
}
[data-testid="stIconMaterial"] { font-family: 'Material Symbols Rounded' !important; }
.mono, .stApp code, .stApp pre, .stApp pre * { font-family: 'Fira Code', ui-monospace, monospace !important;
  font-variant-numeric: tabular-nums; }
.ic { width: 1.15em; height: 1.15em; vertical-align: -0.2em; fill: none; stroke: currentColor;
      stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; flex: none; }

/* backdrop: calm navy with two faint glows for the frosted panels to pick up */
.stApp { background: #0B1020; }
.stApp::before {
  content: ""; position: fixed; inset: -20%; z-index: 0; pointer-events: none;
  background:
    radial-gradient(35% 40% at 15% 10%, rgba(124,58,237,.22), transparent 70%),
    radial-gradient(35% 40% at 90% 85%, rgba(34,211,238,.10), transparent 70%),
    #0B1020;
}
[data-testid="stAppViewContainer"], [data-testid="stMain"], [data-testid="stBottomBlockContainer"] { background: transparent !important; }
[data-testid="stAppViewContainer"] > * { position: relative; z-index: 1; }
/* hide Streamlit's own chrome: top bar, toolbar, status widget, footer */
header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"],
[data-testid="stStatusWidget"], footer { display: none !important; }
.block-container { max-width: 1080px; margin: 0 auto; padding: 3.5rem 1.5rem 4rem; }
/* no sidebar: hide it and its open/close arrow */
section[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="collapsedControl"] { display: none !important; }

/* shared glass surface (+ a soft sheen across the top-left) */
.glass, .step, .card, .hero, .metric, .empty {
  position: relative; background: var(--glass-bg); border: 1px solid var(--glass-rim);
  backdrop-filter: var(--glass-blur); -webkit-backdrop-filter: var(--glass-blur); box-shadow: var(--glass-shadow);
  overflow: hidden;
}

/* header */
.hdr { display: flex; align-items: center; justify-content: center; gap: .75rem; }
.hdr h1 { font-size: 2.75rem; font-weight: 800; margin: 0; letter-spacing: -0.035em; line-height: 1.05;
  background: linear-gradient(135deg, #FFFFFF 30%, #C4B5FD 100%); -webkit-background-clip: text; background-clip: text;
  color: transparent; }
.hdr .ic { width: 2.5rem; height: 2.5rem; color: #A78BFA; filter: drop-shadow(0 4px 14px rgba(124,58,237,.55)); }
.sub { color: var(--muted); font-size: var(--fs-lg); line-height: 1.55; margin: .7rem auto 1.4rem; text-align: center;
  max-width: 600px; text-wrap: balance; }
.how { display: flex; flex-wrap: wrap; justify-content: center; align-items: center; gap: .35rem .4rem; margin-bottom: 2.2rem; }
.chip { display: inline-flex; align-items: center; gap: .4rem; padding: .35rem .75rem; border-radius: 999px;
  background: rgba(255,255,255,.06); border: 1px solid rgba(255,255,255,.10); color: var(--muted); font-size: var(--fs-xs);
  font-weight: 500; white-space: nowrap; }
.chip .ic { color: #A78BFA; }
.sep { color: rgba(255,255,255,.28); font-size: var(--fs-md); }

/* section headings between the result blocks */
.sec { display: flex; align-items: center; gap: .75rem; margin: 2rem 0 .9rem; color: var(--faint); font-size: var(--fs-xs);
  font-weight: 700; letter-spacing: .1em; text-transform: uppercase; }
.sec::after { content: ""; flex: 1; height: 1px; background: linear-gradient(90deg, rgba(255,255,255,.14), transparent); }

/* Streamlit widgets: labels, inputs, the Go button */
.stApp label, .stApp [data-testid="stWidgetLabel"] p { font-size: var(--fs-sm) !important; font-weight: 600 !important; color: var(--muted) !important; }
/* the search bar: one glass panel holding both inputs and Go */
div[data-testid="stForm"] { background: rgba(255,255,255,.05); border: 1px solid var(--glass-rim) !important;
  border-radius: var(--r-lg); padding: 1.1rem 1.25rem 1.25rem; box-shadow: var(--glass-shadow);
  backdrop-filter: var(--glass-blur); -webkit-backdrop-filter: var(--glass-blur); margin-bottom: 1.6rem; }
/* text inputs: a clearly defined dark well with a visible rim (Streamlit's own border matches its fill) */
div[data-testid="stTextInputRootElement"] { background: rgba(4,6,18,.78) !important;
  border: 1.5px solid rgba(255,255,255,.26) !important; border-radius: var(--r-sm) !important; min-height: 3rem;
  box-shadow: inset 0 2px 6px rgba(0,0,0,.45); transition: border-color .15s ease-out, box-shadow .15s ease-out; }
div[data-testid="stTextInputRootElement"] > div { background: transparent !important; }
div[data-testid="stTextInputRootElement"]:hover { border-color: rgba(255,255,255,.42) !important; }
div[data-testid="stTextInputRootElement"]:focus-within { border-color: #A78BFA !important;
  box-shadow: inset 0 2px 6px rgba(0,0,0,.45), 0 0 0 3px rgba(167,139,250,.35) !important; }
div[data-testid="stTextInputRootElement"] input { background: transparent !important; color: var(--text) !important;
  font-size: var(--fs-md) !important; padding-left: .95rem !important; caret-color: #C4B5FD; }
div[data-testid="stTextInputRootElement"] input::placeholder { color: var(--faint) !important; opacity: 1; }
/* Streamlit's "Press Enter to submit form" hint (Enter still submits) */
[data-testid="InputInstructions"] { display: none !important; }
.stApp [data-testid="stTextInput"] label p { color: var(--text) !important; }
div[data-testid="stFormSubmitButton"] button {
  background: var(--primary) !important; border: 0 !important;
  color: #fff !important; min-height: 3.05rem; border-radius: var(--r-sm) !important; cursor: pointer;
  background-image: linear-gradient(135deg, #8B5CF6, #6D28D9) !important;
  box-shadow: 0 6px 20px rgba(124,58,237,.45); transition: transform .15s ease-out, box-shadow .15s ease-out; }
div[data-testid="stFormSubmitButton"] button p { font-size: var(--fs-lg) !important; font-weight: 700 !important; color: #fff !important; }
div[data-testid="stFormSubmitButton"] button:hover { transform: translateY(-1px); box-shadow: 0 10px 26px rgba(124,58,237,.55); }
div[data-testid="stFormSubmitButton"] button:disabled { opacity: .45; box-shadow: none; transform: none; }

/* progress rail */
.rail { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: .6rem; margin: 0 0 1.6rem; }
.step { border-radius: var(--r-md); padding: .8rem .9rem; display: flex; gap: .65rem; align-items: center; min-height: 68px; }
.step > div:last-child { min-width: 0; }
.step .dot { width: 30px; height: 30px; border-radius: 50%; display: grid; place-items: center;
             border: 2px solid rgba(255,255,255,.4); color: var(--idle); font-weight: 700; font-size: var(--fs-sm); flex: none;
             background: rgba(255,255,255,.06); }
.step .lbl { font-weight: 700; font-size: var(--fs-sm); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.step .meta { color: var(--faint); font-size: .75rem; line-height: 1.35; margin-top: .15rem;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; }
.step.done .dot { background: var(--primary); border-color: var(--primary); color: #fff; }
.step.running { border-color: rgba(34,211,238,.6); }
.step.running .dot { border-color: var(--live); color: var(--live); }
.step.failed .dot { border-color: var(--bad); color: var(--bad); }

/* call cards */
.card { border-radius: var(--r-lg); padding: 1.25rem 1.35rem; display: flex; flex-direction: column; gap: .6rem;
        height: 100%; min-height: 340px; transition: border-color .2s ease-out, box-shadow .2s ease-out, transform .2s ease-out; }
.card:hover { transform: translateY(-2px); box-shadow: 0 14px 34px rgba(2,4,16,.5), inset 0 1px 0 rgba(255,255,255,.08); }
div[data-testid="stColumn"]:has(.card) > div, div[data-testid="stColumn"]:has(.card) [data-testid="stVerticalBlock"],
div[data-testid="stColumn"]:has(.card) [data-testid="stElementContainer"],
div[data-testid="stColumn"]:has(.card) [data-testid="stMarkdown"],
div[data-testid="stColumn"]:has(.card) [data-testid="stMarkdownContainer"] { height: 100%; }
.card .top { display: flex; justify-content: space-between; align-items: center; }
.rank { background: rgba(255,255,255,.14); color: var(--text); border-radius: 999px; padding: .2rem .7rem;
        font-size: var(--fs-xs); font-weight: 700; letter-spacing: .02em; }
.card h3 { font-size: var(--fs-xl); font-weight: 700; margin: .1rem 0 0; line-height: 1.25; color: var(--text); }
.addr { color: var(--muted); font-size: var(--fs-sm); line-height: 1.45; }
.price { font-size: var(--fs-3xl); font-weight: 600; line-height: 1; color: var(--text); margin-top: .2rem; }
.price small { font-family: 'Inter', sans-serif !important; font-size: var(--fs-sm); color: var(--muted); font-weight: 500; margin-left: .4rem; }
.phone { color: var(--muted); font-size: var(--fs-sm); }
.ring { color: var(--live); font-size: var(--fs-sm); font-weight: 600; }
.pill { display: inline-flex; align-items: center; gap: .45rem; border-radius: 999px; padding: .4rem .9rem;
        font-weight: 700; font-size: var(--fs-md); width: fit-content; background: rgba(0,0,0,.22); border: 1px solid currentColor; }
.timer { font-size: var(--fs-xl); font-weight: 600; }
.quote { font-size: var(--fs-md); color: var(--text); }
.links { margin-top: auto; display: flex; flex-wrap: wrap; gap: .5rem; padding-top: .4rem; }
.btn { display: inline-flex; align-items: center; gap: .4rem; border-radius: var(--r-sm); padding: .6rem .95rem; min-height: 44px;
       font-weight: 600; font-size: var(--fs-sm); text-decoration: none !important; cursor: pointer; text-shadow: none;
       border: 1px solid var(--glass-rim); color: var(--text) !important; background: rgba(255,255,255,.12);
       transition: background .15s ease-out, transform .15s ease-out; }
.btn:hover { background: rgba(255,255,255,.18); }
.btn:focus-visible, .stApp a:focus-visible { outline: 3px solid #A78BFA; outline-offset: 2px; }
.btn.primary { background: var(--primary); color: #fff !important; border: 0; font-size: var(--fs-md); }
.btn.primary:hover { background: var(--primary-hover); }

.s-waiting { color: var(--idle); } .s-oncall { color: var(--live); } .s-available { color: var(--ok); }
.s-unavailable, .s-error { color: var(--bad); } .s-voicemail, .s-no_answer { color: var(--warn); } .s-unclear { color: var(--muted); }
.card.s-oncall { border-color: rgba(34,211,238,.6); }
.card.s-available { border-color: rgba(52,211,153,.6); }
.card .pill, .card .timer { color: inherit; }

/* verdict hero */
.hero { border-radius: var(--r-lg); padding: 1.6rem 1.8rem; margin-bottom: 1.4rem; }
.hero.ok { border-color: rgba(52,211,153,.55); }
.hero .kicker { font-size: var(--fs-sm); font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
.hero h2 { font-size: var(--fs-2xl); font-weight: 700; margin: .35rem 0 .45rem; line-height: 1.25; color: var(--text); }
.hero p { color: var(--muted); margin: .25rem 0; font-size: var(--fs-md); line-height: 1.5; }
.hero .row { display: flex; flex-wrap: wrap; gap: .7rem; margin-top: 1rem; align-items: center; }
.note { color: var(--faint); font-size: var(--fs-sm); font-style: italic; margin-top: .7rem; }

/* metrics strip */
.metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: .7rem; margin: 0 0 .4rem; }
.metric { border-radius: var(--r-md); padding: .95rem 1.1rem; }
.metric .k { color: var(--muted); font-size: var(--fs-xs); font-weight: 600; display: flex; gap: .45rem; align-items: center;
  text-transform: uppercase; letter-spacing: .06em; }
.metric .v { font-size: var(--fs-xl); font-weight: 600; margin-top: .3rem; color: var(--text); }

.empty { border-style: dashed; border-radius: var(--r-lg); padding: 2.75rem; text-align: center; color: var(--muted); font-size: var(--fs-md); }

/* tabs, code and tables on glass */
div[data-testid="stTabs"] button p { font-size: var(--fs-md) !important; font-weight: 600 !important; }
div[data-testid="stCode"] pre, div[data-testid="stDataFrame"] { background: rgba(6,4,24,.55) !important; border-radius: var(--r-sm); }

@media (max-width: 640px) {
  .block-container { padding: 2rem 1rem 3rem; }
  .hdr h1 { font-size: 2.1rem; } .hdr .ic { width: 2rem; height: 2rem; }
  .sub { font-size: var(--fs-md); } .sep { display: none; }
  div[data-testid="stForm"] { padding: .9rem; }
}
@media (prefers-reduced-motion: reduce) { .card, .card:hover, div[data-testid="stFormSubmitButton"] button { transition: none; transform: none; } }

@media (prefers-reduced-motion: no-preference) {
  .step.running .dot, .card.s-oncall .pill { animation: pulse 1.6s ease-in-out infinite; }
  @keyframes pulse { 0%, 100% { box-shadow: 0 0 0 0 rgba(34,211,238,.6); } 50% { box-shadow: 0 0 0 9px rgba(34,211,238,0); } }
}
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
        "search": f"{len(cands)} callable, ≤{config.SEARCH_DOWNTOWN_KM:g} km from downtown" if cands is not None else "",
        "recall": "Backboard memory" if "recall" in state["stages"] else "",
        "call": f"{sum('call' in c for k, c in state['calls'].items() if k <= 3)}/{min(len(cands), 3)} finished"
                if cands else "",
        "retry": "no room confirmed, calling the next cheapest" if "retry" in state["stages"] else "",
        "email": f"to {_e(req.callback_email)}" if req and state.get("run") and req.callback_email else "",
    }
    steps = []
    stages = [(k, lbl) for k, lbl in STAGES if k != "retry" or "retry" in state["stages"]]
    for i, (key, label) in enumerate(stages, start=1):
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
    _html('<div class="sec">Hotels we called</div>')
    for row in range(0, len(cands), 3):  # a retry round adds a second row
        for col, c in zip(st.columns(3, gap="medium"), cands[row:row + 3]):
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
    _html('<div class="sec">This run</div><div class="metrics">' + "".join(
        f'<div class="metric"><div class="k">{_icon(i)} {k}</div><div class="v mono">{_e(v)}</div></div>'
        for i, k, v in tiles) + "</div>")


def _render_token_breakdown(run, backboard: int, agent: int) -> None:
    """Where every token went, by pipeline step."""
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


def _render_details(run) -> None:
    t = run.telemetry.tokens
    backboard = t.get("backboard_in", 0) + t.get("backboard_out", 0)
    _html('<div class="sec">Token breakdown</div>')
    _render_token_breakdown(run, backboard, t.get("agent_llm", 0))


# --- page -----------------------------------------------------------------------------

st.set_page_config(page_title="Hotel Agent", page_icon="🏨", layout="wide", initial_sidebar_state="collapsed")
_html(CSS)
_html(
    f'<div class="hdr">{_icon("hotel")}<h1>Hotel Agent</h1></div>'
    '<div class="sub">Type what you need. An AI voice agent phones the 3 cheapest hotels at the same time '
    'and emails you who has a room.</div>'
    '<div class="how">'
    f'<span class="chip">{_icon("search")} Finds hotels within {config.SEARCH_DOWNTOWN_KM:g} km of downtown</span>'
    f'<span class="sep">›</span><span class="chip">{_icon("phonecall")} Calls the 3 cheapest at once</span>'
    f'<span class="sep">›</span><span class="chip">{_icon("check")} Confirms a room and rate</span>'
    f'<span class="sep">›</span><span class="chip">{_icon("mail")} Emails you the results</span></div>'
)


# Live only: every run searches the real hotels, phones them and sends a real email.
config.MOCK_MODULES = set()
config.USE_MOCK_HOTEL, config.MOCK_HOTEL_PHONES = False, []

with st.form("prompt", border=False):
    c1, c2, c3 = st.columns([5, 3, 1.2], vertical_alignment="bottom")
    prompt = c1.text_input("What do you need?", placeholder="cheapest hotel in Toronto tonight")
    email = c2.text_input("Send the results to", value=DEFAULT_RECIPIENT, placeholder="you@example.com")
    go = c3.form_submit_button("Go", type="primary", use_container_width=True)

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
