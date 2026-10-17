"""WS4: record_availability endpoint for the ElevenLabs server tool.

Stores each call's result keyed by (run_id, candidate_rank). Never sends email.
Safe for 3 simultaneous requests: ThreadingHTTPServer + one lock around the store.

  POST /record_availability   header X-Tool-Secret, JSON ToolAvailabilityRequest
  GET  /health
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dotenv import load_dotenv
from pydantic import ValidationError

import extractor
from contracts import Availability, HotelCandidate, Run, ToolAvailabilityRequest

load_dotenv()
log = logging.getLogger(__name__)

MAX_BODY = 64 * 1024
SECRET_HEADER = "X-Tool-Secret"
OK_MESSAGE = "Recorded. Thank you, goodbye."

_lock = threading.Lock()
_results: dict[tuple[str, int], Availability] = {}
_expected: dict[str, set[int]] = {}
_server: ThreadingHTTPServer | None = None


def _secret() -> str:
    try:
        import config

        value = getattr(config, "TOOL_SHARED_SECRET", None)
        if value:
            return str(value)
    except ImportError:
        pass
    return os.environ.get("TOOL_SHARED_SECRET", "").strip()


def register_run(run: Run, candidate: HotelCandidate) -> None:
    """Tell the server to expect a result for this run + candidate."""
    with _lock:
        _expected.setdefault(run.run_id, set()).add(candidate.rank)


def results_for(run_id: str) -> dict[int, Availability]:
    """Stored results for a run, keyed by candidate_rank."""
    with _lock:
        return {rank: a for (rid, rank), a in _results.items() if rid == run_id}


def record(payload: ToolAvailabilityRequest) -> Availability:
    """Validate and store one tool result. Last write wins if the agent calls twice."""
    availability = extractor.validate(payload)
    with _lock:
        _results[(payload.run_id, payload.candidate_rank)] = availability
        known = payload.candidate_rank in _expected.get(payload.run_id, set())
    if not known:
        log.warning("result for unregistered run/candidate %s/%s stored anyway",
                    payload.run_id, payload.candidate_rank)
    return availability


def reset() -> None:
    """Forget everything (tests, and between runs if the process is long-lived)."""
    with _lock:
        _results.clear()
        _expected.clear()


class _Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/record_availability":
            return self._send(404, {"error": "not found"})

        expected = _secret()
        given = self.headers.get(SECRET_HEADER, "")
        # Fail closed: with no secret configured nothing is accepted.
        if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
            return self._send(401, {"error": "unauthorized"})

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._send(400, {"error": "bad content-length"})
        if length <= 0 or length > MAX_BODY:
            return self._send(400, {"error": "bad body size"})

        try:
            payload = ToolAvailabilityRequest.model_validate(json.loads(self.rfile.read(length)))
        except json.JSONDecodeError:
            return self._send(400, {"error": "invalid json"})
        except ValidationError as exc:
            return self._send(422, {"error": "invalid payload", "detail": exc.errors(include_url=False, include_input=False)})

        record(payload)
        self._send(200, {"message": OK_MESSAGE})

    def log_message(self, fmt: str, *args) -> None:
        log.info("%s %s", self.address_string(), fmt % args)


def start_in_background(port: int = 8000) -> int:
    """Run the HTTP server on a daemon thread (called first by pipeline.run).

    Idempotent. Returns the port actually bound (pass 0 for a free one).
    """
    global _server
    if _server is not None:
        return _server.server_address[1]
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    server.daemon_threads = True
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), name="tool_server", daemon=True).start()
    _server = server
    return server.server_address[1]


def stop() -> None:
    global _server
    if _server is not None:
        _server.shutdown()
        _server.server_close()
        _server = None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    bound = start_in_background(int(os.environ.get("TOOL_SERVER_PORT", "8000")))
    print(f"tool_server listening on 127.0.0.1:{bound}")
    threading.Event().wait()
