"""WS2: per-stage token and time table for the deck, averaged over runs.

    python token_report.py              # archive run_telemetry.json, then report every archived run
    python token_report.py a.json b.json

pipeline.py overwrites run_telemetry.json each run, so run this after every
real run to keep a copy in telemetry_runs/. Prints a Markdown table.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import config
from contracts import Run

RUN_FILE = Path("run_telemetry.json")  # written by telemetry.py (WS1)
ARCHIVE = Path("telemetry_runs")
STAGES = ["brain", "search", "recall", "call", "email"]
TOKENS = ["backboard_parse", "backboard_extract", "backboard_total", "agent_llm"]


def archive_latest() -> None:
    if not RUN_FILE.exists():
        return
    run = Run.model_validate_json(RUN_FILE.read_text())
    ARCHIVE.mkdir(exist_ok=True)
    target = ARCHIVE / f"{run.run_id}.json"
    if not target.exists():
        shutil.copy(RUN_FILE, target)
        print(f"Archived {RUN_FILE} -> {target}\n")


def row(run: Run) -> dict[str, float]:
    t = run.telemetry
    total_ms = (
        (run.email.sent_at - run.started_at).total_seconds() * 1000
        if run.email else sum(t.stage_ms.values())
    )
    out = {"total_s": total_ms / 1000}
    out.update({f"{s}_s": t.stage_ms.get(s, 0) / 1000 for s in STAGES})
    out["backboard_parse"] = t.tokens.get("backboard_parse", 0)
    out["backboard_extract"] = t.tokens.get("backboard_extract", 0)
    out["backboard_total"] = t.tokens.get("backboard_in", 0) + t.tokens.get("backboard_out", 0)
    out["agent_llm"] = t.tokens.get("agent_llm", 0)
    return out


def report(runs: list[Run]) -> str:
    cols = ["total_s"] + [f"{s}_s" for s in STAGES] + TOKENS
    rows = [(r.run_id[:8], row(r)) for r in runs]
    avg = {c: sum(v[c] for _, v in rows) / len(rows) for c in cols}

    def fmt(c: str, v: float) -> str:
        return f"{v:.1f}" if c.endswith("_s") else f"{v:,.0f}"

    lines = [
        "| run | " + " | ".join(cols) + " |",
        "|---|" + "---:|" * len(cols),
        *(f"| {rid} | " + " | ".join(fmt(c, v[c]) for c in cols) + " |" for rid, v in rows),
        f"| **avg of {len(rows)}** | " + " | ".join(f"**{fmt(c, avg[c])}**" for c in cols) + " |",
    ]
    budget = config.BACKBOARD_TOKEN_BUDGET
    lines += [
        "",
        f"Backboard per session: avg {avg['backboard_total']:,.0f} / {budget:,} budget "
        f"(max {max(v['backboard_total'] for _, v in rows):,.0f}).",
        f"Agent LLM per run (3 parallel calls): avg {avg['agent_llm']:,.0f}.",
        f"Total time: avg {avg['total_s']:.1f} s (target < 240 s).",
    ]
    return "\n".join(lines)


def main(paths: list[str]) -> None:
    if not paths:
        archive_latest()
        files = sorted(ARCHIVE.glob("*.json"))
    else:
        files = [Path(p) for p in paths]
    runs = [Run.model_validate_json(f.read_text()) for f in files]
    if not runs:
        print("No runs found. Do a full run first (python main.py ... or the UI).")
        return
    runs.sort(key=lambda r: r.started_at)
    print(report(runs))


if __name__ == "__main__":
    main(sys.argv[1:])
