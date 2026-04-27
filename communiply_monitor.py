#!/usr/bin/env python3
"""
ProductClank Communiply Task Monitor
─────────────────────────────────────
Polls the public ProductClank campaigns API every 10 minutes.
Sends a Telegram message whenever a NEW campaign/task appears.

No Farcaster login needed — the API is fully public.

Setup:
  pip install requests
  python communiply_monitor.py
"""

import os
import json
import time
import logging
import requests
from datetime import datetime

# ─── CONFIG ───────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8745180952:AAFSPC552Aqo8AEmIEnw1nrH3kuPXCe_9pA")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "1364114058")

CHECK_INTERVAL_SEC = 10 * 60   # check every 10 minutes
STATE_FILE         = "seen_campaigns.json"

# Public API endpoints (no auth needed!)
API_URLS = [
    "https://miniapp.productclank.com/api/campaigns/list?status=active&sort_by_usd=true",
    "https://miniapp.productclank.com/api/campaigns/list?isFeatured=true&status=active&limit=10&sort_by_usd=true",
]
# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Referer": "https://miniapp.productclank.com/frame/home",
    "Accept": "*/*",
}


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }, timeout=10)
        r.raise_for_status()
        log.info("Telegram message sent")
        return True
    except Exception as e:
        log.error(f"Telegram failed: {e}")
        return False


def fetch_campaigns():
    seen_ids = set()
    all_campaigns = []
    for url in API_URLS:
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            for c in r.json().get("campaigns", []):
                if c["id"] not in seen_ids:
                    seen_ids.add(c["id"])
                    all_campaigns.append(c)
        except Exception as e:
            log.error(f"Fetch error: {e}")
    log.info(f"Fetched {len(all_campaigns)} active campaign(s)")
    return all_campaigns


def load_seen():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen(ids):
    with open(STATE_FILE, "w") as f:
        json.dump(list(ids), f)


def format_campaign(c):
    title   = c.get("title", "Untitled")
    ctype   = c.get("campaign_type", "").replace("_", " ").title()
    reward  = c.get("reward_type", "").replace("_", " ").title()
    action  = c.get("action_cta", "")
    url     = c.get("action_url", "")
    end     = c.get("end_date", "")

    lines = [f"<b>{title}</b>"]
    if ctype:   lines.append(f"📋 {ctype}")
    if reward:  lines.append(f"🎁 {reward}")
    if end:
        try:
            dt = datetime.fromisoformat(end)
            lines.append(f"⏳ Ends {dt.strftime('%b %d')}")
        except Exception:
            pass
    if action and url:
        lines.append(f"👉 <a href='{url}'>{action}</a>")
    return "\n".join(lines)


def main():
    log.info("=== Communiply Monitor starting ===")

    if "YOUR_BOT_TOKEN" in TELEGRAM_BOT_TOKEN or "YOUR_CHAT_ID" in TELEGRAM_CHAT_ID:
        print("\n⚠️  Fill in TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID first!")
        print("   Edit lines 21-22 in this file, or set them as env variables.\n")
        return

    send_telegram(
        "🤖 <b>Communiply Monitor is live!</b>\n"
        "Checking ProductClank every 10 min.\n"
        "I'll ping you when new tasks appear 🔔"
    )

    seen_ids = load_seen()
    log.info(f"Loaded {len(seen_ids)} previously seen IDs")

    while True:
        try:
            campaigns = fetch_campaigns()
            new = [c for c in campaigns if c["id"] not in seen_ids]

            if new:
                log.info(f"NEW: {len(new)} campaign(s)!")
                parts = [f"🔔 <b>{len(new)} new Communiply task(s) available!</b>\n"]
                for c in new:
                    parts.append(format_campaign(c))
                    parts.append("")
                parts.append("➡️ <a href='https://miniapp.productclank.com/frame/feed'>Open ProductClank</a>")
                send_telegram("\n".join(parts))
                seen_ids.update(c["id"] for c in campaigns)
                save_seen(seen_ids)
            else:
                log.info("No new campaigns.")

        except Exception as e:
            log.error(f"Loop error: {e}")

        log.info(f"Sleeping {CHECK_INTERVAL_SEC // 60} min...\n")
        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()
