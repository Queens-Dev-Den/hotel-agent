"""WS1 CLI: python main.py "cheapest hotel in Toronto tonight" [--email me@example.com]"""

from __future__ import annotations

import argparse
import logging

import pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="One prompt in, one availability email out.")
    parser.add_argument("prompt", help='e.g. "cheapest hotel in Toronto tonight"')
    parser.add_argument("--email", help="where the email goes (default: RECEIPT_TO_EMAIL)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    run = pipeline.run(args.prompt, callback_email=args.email)

    req = run.request
    print(f"\n{req.city}  {req.check_in} -> {req.check_out}  {req.guests} guest(s)")
    for cand, call, avail in zip(run.candidates, run.calls, run.availability):
        status = {True: "AVAILABLE", False: "no room", None: "unknown"}[avail.available]
        print(f"  {cand.rank}. {cand.name}  ${cand.total_price:.0f} {cand.currency}  {cand.phone_e164}"
              f"  -> {call.outcome} / {status}")
    print(f"stage ms: {run.telemetry.stage_ms}  tokens: {run.telemetry.tokens}")
    print(f"email: {run.email.message_id if run.email else 'not sent'}")


if __name__ == "__main__":
    main()
