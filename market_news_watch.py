"""
market_news_watch.py
======================
Free equivalent of a CNBC push notification: polls CNBC's own public RSS
feeds (Markets + Top News) every 5 minutes and fires a Telegram message
the moment a new headline appears -- no paid API, no daily digest, no
paraphrasing. Just "this landed, here's the headline and the link."

Uses the same self-restarting polling-loop pattern as
new_high_low_check.py, for the same reason: GitHub's native `schedule:`
cron is unreliable at 5-minute granularity, so the scheduled trigger
only has to restart this loop every ~5 hours; the actual 5-min polling
happens inside one long-lived job.

Also journals every headline into docs/news-feed.md so there's a
permanent record on the site, same as the high/low alerts.
"""

import os
import json
import time
import datetime
import requests
import xml.etree.ElementTree as ET

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

FEEDS = {
    "CNBC Markets": "https://www.cnbc.com/id/20409666/device/rss/rss.html",
    "CNBC Economy": "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "CNBC Finance": "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "Fed Press Releases": "https://www.federalreserve.gov/feeds/press_all.xml",
    "EIA Today in Energy": "https://www.eia.gov/rss/todayinenergy.xml",
}

STATE_FILE = "seen_news_state.json"
SGT = datetime.timezone(datetime.timedelta(hours=8))

# On the very first run ever, don't blast the entire existing feed history
# to Telegram -- just record what's already there as the baseline, same
# as the high/low monitor does for session extremes.
MAX_ALERTS_ON_FIRST_RUN = 0


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Missing Telegram credentials, skipping send.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                              "disable_web_page_preview": False}, timeout=10)


def journal_headline(source, title, link, pub_date):
    entry = f"- **{pub_date}** [{source}] [{title}]({link})\n"
    path = "docs/news-feed.md"
    if os.path.exists(path):
        with open(path) as f:
            existing = f.read()
    else:
        existing = "# Live Market News Feed\n\nHeadlines as they land, newest first.\n\n"
    header, _, rest = existing.partition("\n\n")
    updated = header + "\n\n" + entry + rest[1:] if rest.startswith("\n") else header + "\n\n" + entry + rest
    with open(path, "w") as f:
        f.write(updated)


def fetch_feed_items(url):
    resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    items = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or link).strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        if title and link:
            items.append({"guid": guid, "title": title, "link": link, "pub_date": pub_date})
    return items


def check_feeds():
    state = load_state()

    for source, url in FEEDS.items():
        source_is_new = source not in state
        seen = set(state.get(source, []))
        try:
            items = fetch_feed_items(url)
        except Exception as e:
            print(f"[{source}] fetch failed: {e}")
            continue

        new_items = [i for i in items if i["guid"] not in seen]
        # Feed order is newest-first; alert oldest-of-the-new first so
        # Telegram message order matches chronological order.
        new_items.reverse()

        if source_is_new:
            print(f"[{source}] First run for this feed -- baselining {len(items)} existing items, no alerts.")
        else:
            for item in new_items:
                msg = f"\U0001F4F0 {source}\n{item['title']}\n{item['link']}"
                print(msg)
                send_telegram(msg)
                journal_headline(source, item["title"], item["link"], item["pub_date"])

        state[source] = list({i["guid"] for i in items} | seen)
        # Keep the seen-set bounded so the state file doesn't grow forever.
        state[source] = state[source][-300:]

    save_state(state)


def send_sample():
    """Sends the single latest real headline from each feed, exactly as a
    live alert would look -- for verification purposes only. Doesn't
    touch seen-state, so it has no effect on what counts as "new" later.
    """
    for source, url in FEEDS.items():
        try:
            items = fetch_feed_items(url)
        except Exception as e:
            print(f"[{source}] fetch failed: {e}")
            continue
        if not items:
            continue
        latest = items[0]
        msg = f"\U0001F4F0 {source} (SAMPLE)\n{latest['title']}\n{latest['link']}"
        print(msg)
        send_telegram(msg)


def main():
    if os.environ.get("SEND_SAMPLE") == "1":
        send_sample()
        return

    if os.environ.get("CONTINUOUS_LOOP") != "1":
        check_feeds()
        return

    loop_budget_seconds = int(os.environ.get("LOOP_BUDGET_SECONDS", 290 * 60))
    poll_interval_seconds = 300
    start = time.time()
    iteration = 0

    while time.time() - start < loop_budget_seconds:
        iteration += 1
        print(f"--- news poll iteration {iteration} at {datetime.datetime.utcnow().isoformat()} UTC ---")
        try:
            check_feeds()
        except Exception as e:
            print(f"Iteration {iteration} failed: {e}")

        elapsed = time.time() - start
        remaining = loop_budget_seconds - elapsed
        if remaining <= 0:
            break
        time.sleep(min(poll_interval_seconds, remaining))

    print(f"Loop budget exhausted after {iteration} iterations, exiting cleanly for restart.")


if __name__ == "__main__":
    main()
