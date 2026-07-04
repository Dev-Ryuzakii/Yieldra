"""Local Telegram inbound simulator.

POSTs the simplified ``{chat, text, image_url}`` payload to the running API so you
can test agent routing without a real Telegram bot connection.

Usage:
    python scripts/test_telegram.py --chat 12345 --text "READY for harvest"
    python scripts/test_telegram.py --chat 12345 --text "Kini mo le se fun arun iresi mi?"
    python scripts/test_telegram.py --chat 12345 --image https://example.com/leaf.jpg --text "wetin dey my crop"
"""

from __future__ import annotations

import argparse
import json

import httpx

DEFAULT_URL = "http://127.0.0.1:8000/webhook/telegram"


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate an inbound Telegram message")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--chat", required=True, help="Telegram chat id (stored as User.phone)")
    parser.add_argument("--text", default="")
    parser.add_argument("--image", dest="image_url", default=None)
    parser.add_argument("--name", default=None, help="Sender name (used during onboarding)")
    args = parser.parse_args()

    payload: dict = {"chat": args.chat, "text": args.text}
    if args.image_url:
        payload["image_url"] = args.image_url
    if args.name:
        payload["name"] = args.name

    print(f"POST {args.url}\n{json.dumps(payload, indent=2)}\n")
    resp = httpx.post(args.url, json=payload, timeout=60)
    print(f"HTTP {resp.status_code}")
    try:
        print(json.dumps(resp.json(), indent=2, ensure_ascii=False))
    except ValueError:
        print(resp.text)


if __name__ == "__main__":
    main()
