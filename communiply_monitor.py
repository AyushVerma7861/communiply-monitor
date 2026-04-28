#!/usr/bin/env python3
"""
ProductClank Communiply Monitor
────────────────────────────────
Monitors BOTH:
  1. Communiply FEED  (like/reply tasks) — via Next.js Server Action
  2. Campaigns        (action tasks)     — via public API

Sends Telegram alerts when anything new appears.

Setup:
  pip install requests
  python communiply_monitor.py
"""

import os, json, time, logging, requests
from datetime import datetime

# ─── CONFIG ───────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN  = "8745180952:AAFSPC552Aqo8AEmIEnw1nrH3kuPXCe_9pA"
TELEGRAM_CHAT_ID    = "1364114058"
PC_USER_ID          = "61b9ad05-ab10-46c3-ae39-8d16931f5452"   # your ProductClank user ID
NEXT_ACTION_HASH    = "40a7b53e562648f22187e1fad072f056fc9dac8158"  # from HAR
CHECK_INTERVAL_SEC  = 10 * 60
STATE_FILE          = "seen_items.json"
# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

BASE_URL = "https://miniapp.productclank.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/147 Safari/537.36",
    "Referer": f"{BASE_URL}/frame/feed",
    "Origin": BASE_URL,
    "Accept": "*/*",
    "sec-fetch-site": "same-origin",
    "sec-fetch-mode": "cors",
}


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(text):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": False},
            timeout=10
        )
        r.raise_for_status()
        log.info("Telegram sent ✓")
    except Exception as e:
        log.error(f"Telegram error: {e}")


# ── Fetch Communiply Feed ─────────────────────────────────────────────────────

def fetch_feed_items():
    """
    POSTs to /frame/feed using your userId + next-action hash.
    Returns list of {reply, post, campaign} dicts.
    """
    try:
        r = requests.post(
            f"{BASE_URL}/frame/feed",
            headers={
                **HEADERS,
                "Accept": "text/x-component",
                "Content-Type": "text/plain;charset=UTF-8",
                "next-action": NEXT_ACTION_HASH,
            },
            data=json.dumps([{"offset": 0, "limit": 20, "userId": PC_USER_ID}]),
            timeout=20
        )
        r.raise_for_status()

        # Response is Next.js streaming format — feed items are on the "1:[...]" line
        for line in r.text.splitlines():
            if line.startswith("1:"):
                data = json.loads(line[2:])
                if isinstance(data, list):
                    log.info(f"Feed items fetched: {len(data)}")
                    return data
    except Exception as e:
        log.error(f"Feed fetch error: {e}")

    return []


# ── Fetch Campaigns ───────────────────────────────────────────────────────────

def fetch_campaigns():
    seen, results = set(), []
    for url in [
        f"{BASE_URL}/api/campaigns/list?status=active&sort_by_usd=true",
        f"{BASE_URL}/api/campaigns/list?isFeatured=true&status=active&limit=10&sort_by_usd=true",
    ]:
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            for c in r.json().get("campaigns", []):
                if c["id"] not in seen:
                    seen.add(c["id"])
                    results.append(c)
        except Exception as e:
            log.error(f"Campaign fetch error: {e}")
    log.info(f"Campaigns fetched: {len(results)}")
    return results


# ── State ─────────────────────────────────────────────────────────────────────

def load_seen():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            d = json.load(f)
            return set(d.get("feed", [])), set(d.get("campaigns", []))
    return set(), set()


def save_seen(feed_ids, campaign_ids):
    with open(STATE_FILE, "w") as f:
        json.dump({"feed": list(feed_ids), "campaigns": list(campaign_ids)}, f)


# ── Formatters ────────────────────────────────────────────────────────────────

def format_feed_item(item):
    post          = item.get("post", {})
    reply         = item.get("reply", {})
    campaign      = item.get("campaign", {})

    author        = post.get("author_username", "unknown")
    tweet_text    = post.get("tweet_text", "")[:200]
    tweet_url     = post.get("tweet_url", "")
    action_type   = reply.get("action_type", "like").title()   # "Like" or "Reply"
    campaign_title = campaign.get("title", "")
    platform      = post.get("platform", "twitter")

    emoji = "❤️" if action_type.lower() == "like" else "💬"

    lines = [
        f"{emoji} <b>New Communiply Feed Task! (+30 pts)</b>",
        f"👤 @{author}",
        f"📝 {tweet_text}",
        f"✅ Action: {action_type} this post",
    ]
    if campaign_title:
        lines.append(f"📢 Campaign: {campaign_title[:80]}")
    if tweet_url:
        lines.append(f"👉 <a href='{tweet_url}'>Open on {platform.title()}</a>")
    lines.append(f"➡️ <a href='{BASE_URL}/frame/feed'>Open Communiply Feed</a>")
    return "\n".join(lines)


def format_campaign(c):
    title   = c.get("title", "Untitled")
    ctype   = c.get("campaign_type", "").replace("_", " ").title()
    reward  = c.get("reward_type", "").replace("_", " ").title()
    action  = c.get("action_cta", "")
    url     = c.get("action_url", "")
    end     = c.get("end_date", "")

    lines = [f"📢 <b>New Campaign Task!</b>", f"<b>{title}</b>"]
    if ctype:  lines.append(f"📋 {ctype}")
    if reward: lines.append(f"🎁 {reward}")
    if end:
        try:
            dt = datetime.fromisoformat(end)
            lines.append(f"⏳ Ends {dt.strftime('%b %d')}")
        except Exception:
            pass
    if action and url:
        lines.append(f"👉 <a href='{url}'>{action}</a>")
    lines.append(f"➡️ <a href='{BASE_URL}/frame/feed'>Open Communiply Feed</a>")
    return "\n".join(lines)


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    log.info("=== Communiply Monitor starting ===")

    send_telegram(
        "🤖 <b>Communiply Monitor is live!</b>\n"
        "Watching:\n"
        "❤️ Communiply Feed (like/reply tasks)\n"
        "📢 Campaigns (action tasks)\n\n"
        "Checking every 10 minutes 🔔"
    )

    seen_feed, seen_campaigns = load_seen()
    log.info(f"Loaded state: {len(seen_feed)} feed items, {len(seen_campaigns)} campaigns")

    while True:
        try:
            # ── Communiply Feed ───────────────────────────────────────────────
            feed_items = fetch_feed_items()
            for item in feed_items:
                item_id = item.get("reply", {}).get("id", "")
                if item_id and item_id not in seen_feed:
                    log.info(f"NEW feed item: {item_id}")
                    send_telegram(format_feed_item(item))
                    seen_feed.add(item_id)

            # ── Campaigns ─────────────────────────────────────────────────────
            campaigns = fetch_campaigns()
            for c in campaigns:
                if c["id"] not in seen_campaigns:
                    log.info(f"NEW campaign: {c['id']}")
                    send_telegram(format_campaign(c))
                    seen_campaigns.add(c["id"])

            save_seen(seen_feed, seen_campaigns)

        except Exception as e:
            log.error(f"Loop error: {e}")

        log.info(f"Sleeping {CHECK_INTERVAL_SEC // 60} min...\n")
        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()
