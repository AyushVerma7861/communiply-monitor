#!/usr/bin/env python3
"""
ProductClank Communiply Monitor
Checks every 1 minute, alerts via Telegram on new feed tasks + campaigns.
"""

import os, json, time, re, logging, requests
from datetime import datetime

# CONFIG
TELEGRAM_BOT_TOKEN = "8745180952:AAFSPC552Aqo8AEmIEnw1nrH3kuPXCe_9pA"
TELEGRAM_CHAT_IDS  = ["1364114058", "5561181442", "961643873", "2027508985"]
# RAVENxSPARK, Vasu Bhai, Aman Bhai, Rahul Bhai
PC_USER_ID         = "13f58f1f-ef9e-4a9e-93d8-6222e362739f"
FALLBACK_HASH      = "40daa03a714ea234a127d03cf72a9651981175a966"
CHECK_INTERVAL     = 1 * 60
STATE_FILE         = "seen_items.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

BASE_URL = "https://miniapp.productclank.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/147 Safari/537.36",
    "Referer": f"{BASE_URL}/frame/feed",
    "Origin": BASE_URL,
    "Accept": "*/*",
}

state = {"hash_fail_count": 0, "hash_alert_sent": False}


def send_telegram(text):
    for chat_id in TELEGRAM_CHAT_IDS:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text,
                      "parse_mode": "HTML", "disable_web_page_preview": False},
                timeout=10
            )
            r.raise_for_status()
            log.info(f"Telegram sent to {chat_id} OK")
        except Exception as e:
            log.error(f"Telegram error for {chat_id}: {e}")


def verify_hash(h):
    """Returns True if this hash gives a response > 200 bytes."""
    try:
        test = requests.post(
            f"{BASE_URL}/frame/feed",
            headers={
                **HEADERS,
                "Accept": "text/x-component",
                "Content-Type": "text/plain;charset=UTF-8",
                "next-action": h,
            },
            data=json.dumps([{"offset": 0, "limit": 1, "userId": PC_USER_ID}]),
            timeout=15
        )
        return len(test.text) > 200
    except Exception:
        return False


def get_next_action_hash():
    # Step 1: verify FALLBACK_HASH still works
    if verify_hash(FALLBACK_HASH):
        log.info(f"Using verified fallback hash: {FALLBACK_HASH}")
        state["hash_fail_count"] = 0
        state["hash_alert_sent"] = False
        return FALLBACK_HASH

    log.warning("Fallback hash not working, trying auto-detect")

    # Step 2: auto-detect from JS chunk
    try:
        r = requests.get(f"{BASE_URL}/frame/feed",
                         headers={**HEADERS, "Accept": "text/html"}, timeout=15)
        chunk_match = re.search(
            r"/_next/static/chunks/app/[(]frame[)]/frame/feed/page-([a-f0-9]+[.]js)", r.text)
        if chunk_match:
            chunk_url = f"{BASE_URL}/_next/static/chunks/app/(frame)/frame/feed/page-{chunk_match.group(1)}"
            js = requests.get(chunk_url, headers=HEADERS, timeout=15)
            hash_match = re.search(r'createServerReference[(]["](40[a-f0-9]{38,42})["]', js.text)
            if hash_match:
                h = hash_match.group(1)
                if verify_hash(h):
                    log.info(f"Auto-detected working hash: {h}")
                    state["hash_fail_count"] = 0
                    state["hash_alert_sent"] = False
                    return h
                else:
                    log.warning(f"Auto-detected hash {h} also not working")
        log.warning("Auto-detect failed")
        state["hash_fail_count"] += 1
    except Exception as e:
        log.error(f"Hash auto-detect error: {e}")
        state["hash_fail_count"] += 1

    if state["hash_fail_count"] >= 3 and not state["hash_alert_sent"]:
        state["hash_alert_sent"] = True
        send_telegram(
            "WARNING: Communiply Monitor\n\n"
            "Both hash methods failed 3 times.\n"
            "Please export a new HAR file and send it to update the script."
        )
    return FALLBACK_HASH


def fetch_feed_items(next_action_hash):
    try:
        r = requests.post(
            f"{BASE_URL}/frame/feed",
            headers={
                **HEADERS,
                "Accept": "text/x-component",
                "Content-Type": "text/plain;charset=UTF-8",
                "next-action": next_action_hash,
            },
            data=json.dumps([{"offset": 0, "limit": 20, "userId": PC_USER_ID}]),
            timeout=20
        )
        r.raise_for_status()
        log.info(f"Feed response length: {len(r.text)}")

        match = re.search(r'1:(\[)', r.text)
        if not match:
            log.warning("1:[ not found in feed response")
            return []

        json_str = r.text[match.start() + 2:]
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            end = json_str.rfind(']')
            if end == -1:
                log.error("Could not find end of JSON array")
                return []
            try:
                data = json.loads(json_str[:end + 1])
            except json.JSONDecodeError as ex:
                log.error(f"Feed JSON parse error: {ex}")
                return []

        if isinstance(data, list):
            log.info(f"Feed items fetched: {len(data)}")
            return data

        return []

    except requests.RequestException as e:
        log.error(f"Feed request error: {e}")
    except Exception as e:
        log.error(f"Feed fetch unexpected error: {e}")
    return []


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


def load_seen():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            d = json.load(f)
            return set(d.get("feed", [])), set(d.get("campaigns", []))
    return set(), set()


def save_seen(feed_ids, campaign_ids):
    with open(STATE_FILE, "w") as f:
        json.dump({"feed": list(feed_ids), "campaigns": list(campaign_ids)}, f)


def format_feed_item(item):
    post           = item.get("post", {})
    reply          = item.get("reply", {})
    campaign       = item.get("campaign", {})
    author         = post.get("author_username", "unknown")
    tweet_text     = post.get("tweet_text", "")[:200]
    tweet_url      = post.get("tweet_url", "")
    action_type    = reply.get("action_type", "like").lower()
    campaign_title = campaign.get("title", "")
    platform       = post.get("platform", "twitter")
    emoji        = {"like": "❤️", "retweet": "🔁", "reply": "💬"}.get(action_type, "✅")
    action_label = {"like": "Like", "retweet": "Repost", "reply": "Reply to"}.get(action_type, action_type.title())
    lines = [
        f"{emoji} <b>New Communiply Feed Task! (+30 pts)</b>",
        f"👤 @{author}",
        f"📝 {tweet_text}",
        f"✅ Action: {action_label} this post",
    ]
    if campaign_title:
        lines.append(f"📢 Campaign: {campaign_title[:80]}")
    if tweet_url:
        lines.append(f"👉 <a href='{tweet_url}'>Open on {platform.title()}</a>")
    lines.append(f"➡️ <a href='{BASE_URL}/frame/feed'>Open Communiply Feed</a>")
    return "\n".join(lines)


def format_campaign(c):
    title  = c.get("title", "Untitled")
    ctype  = c.get("campaign_type", "").replace("_", " ").title()
    reward = c.get("reward_type", "").replace("_", " ").title()
    action = c.get("action_cta", "")
    url    = c.get("action_url", "")
    end    = c.get("end_date", "")
    lines  = [f"📢 <b>New Campaign Task!</b>", f"<b>{title}</b>"]
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


def main():
    log.info("=== Communiply Monitor starting ===")
    send_telegram(
        "🤖 <b>Communiply Monitor is live!</b>\n"
        "Watching:\n"
        "❤️ Communiply Feed (like/repost/reply tasks)\n"
        "📢 Campaigns (action tasks)\n\n"
        "Checking every 1 minute 🔔"
    )

    seen_feed, seen_campaigns = load_seen()
    log.info(f"Loaded state: {len(seen_feed)} feed items, {len(seen_campaigns)} campaigns")

    while True:
        try:
            next_action_hash = get_next_action_hash()

            feed_items = fetch_feed_items(next_action_hash)
            seen_urls_this_batch = set()

            for item in feed_items:
                reply_id  = item.get("reply", {}).get("id", "")
                tweet_url = item.get("post", {}).get("tweet_url", "") or item.get("post", {}).get("id", "")

                if not reply_id or reply_id in seen_feed:
                    continue

                if tweet_url and tweet_url in seen_urls_this_batch:
                    seen_feed.add(reply_id)
                    continue

                log.info(f"NEW feed task: {reply_id}")
                send_telegram(format_feed_item(item))
                seen_feed.add(reply_id)
                if tweet_url:
                    seen_urls_this_batch.add(tweet_url)

            campaigns = fetch_campaigns()
            for c in campaigns:
                if c["id"] not in seen_campaigns:
                    log.info(f"NEW campaign: {c['id']}")
                    send_telegram(format_campaign(c))
                    seen_campaigns.add(c["id"])

            save_seen(seen_feed, seen_campaigns)

        except Exception as e:
            log.error(f"Loop error: {e}")

        log.info(f"Sleeping {CHECK_INTERVAL} sec...")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
