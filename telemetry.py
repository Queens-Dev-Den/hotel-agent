"""WS1: per-stage timers and run_telemetry.json (read by the UI log and the pitch metrics)."""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path

from contracts import Run

RUN_FILE = Path("run_telemetry.json")


@contextmanager
def stage(run: Run, name: str):
    """Time a block into run.telemetry.stage_ms[name], then save so the UI sees it live."""
    started = time.monotonic()
    try:
        yield
    finally:
        run.telemetry.stage_ms[name] = int((time.monotonic() - started) * 1000)
        save(run)


def save(run: Run, path: Path | None = None) -> None:
    (path or RUN_FILE).write_text(run.model_dump_json(indent=2))
