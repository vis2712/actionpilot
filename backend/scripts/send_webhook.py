"""Simulates a meeting tool (Zoom, Teams…) posting a finished meeting to ActionPilot.

    uv run python -m scripts.send_webhook --owner you@example.com --url http://localhost:8001

Uses WEBHOOK_SECRET from the environment / .env. Run it twice to see idempotency (the 2nd says duplicate).
"""

import argparse
import json
import time
from datetime import date

import httpx

from app.config import settings
from app.routers.webhooks import sign

TRANSCRIPT = """Meera: Quick ops review. The vendor contract renewal is due this month.
Arjun: We agreed to renew with Acme for one year at the current price.
Arjun: I'll send the signed contract to Acme by Thursday.
Meera: I'll update the budget sheet with the new contract by Friday.
Meera: Thanks, that's all for today."""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--owner", required=True, help="email of an existing ActionPilot user")
    p.add_argument("--url", default="http://localhost:8001", help="API base URL (or http://<host>:3000/api)")
    p.add_argument("--external-id", default="demo-meeting-001")
    args = p.parse_args()
    if not settings.webhook_secret:
        raise SystemExit("Set WEBHOOK_SECRET in .env first (the API needs the same value).")

    body = json.dumps(
        {
            "external_id": args.external_id,
            "owner_email": args.owner,
            "title": "Ops review (from webhook)",
            "transcript": TRANSCRIPT,
            "meeting_date": date.today().isoformat(),
            "participants": [],
        }
    ).encode()
    ts = str(int(time.time()))
    r = httpx.post(
        f"{args.url.rstrip('/')}/webhooks/meetings",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Timestamp": ts,
            "X-Signature": sign(body, ts, settings.webhook_secret),
        },
    )
    print(r.status_code, r.text)


if __name__ == "__main__":
    main()
