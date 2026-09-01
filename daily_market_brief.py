"""
daily_market_brief.py
======================
Runs every weekday morning (SGT). Calls the Anthropic API directly, WITH
the web_search tool enabled, so the report is based on same-morning
research -- not something Prof has to fetch and paste in himself.

Sends the result to Telegram, and journals it into docs/index.md, which
GitHub Pages renders as a running public market journal (see README for
the Pages URL).

Env vars required:
  ANTHROPIC_API_KEY   -- Anthropic API key
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  -- existing Telegram bot creds
"""

import os
import json
import datetime
import requests

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

MODEL = "claude-sonnet-5"

SGT = datetime.timezone(datetime.timedelta(hours=8))

PROMPT = """You are producing a concise morning market brief for a futures
trader based in Singapore who holds positions in MNQ (short), MGC (long),
and trades ES, NQ, RTY, YM, GC, and BTC futures.

Research and report on:
1. How major US indices (Dow, S&P 500, Nasdaq) closed in the most recent
   session, with the actual point/percentage moves.
2. The specific drivers behind the move -- geopolitical events, Fed
   commentary, economic data releases, earnings -- named plainly, not
   vaguely ("stocks fell on inflation worries" is not enough; say what
   the actual data or event was).
3. Oil, gold, and Treasury yields if they moved meaningfully, and why.
4. Anything relevant to an ongoing Iran/Middle East conflict if it's
   affecting markets.
5. One or two notable individual stock movers if relevant.

Write it as a tight, scannable markdown brief -- headers and short
bullet points, not paragraphs of prose. No generic disclaimers, no
"consult a financial advisor" boilerplate. Assume the reader is
experienced and wants the facts and the causal chain, fast.
"""


def generate_brief():
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": MODEL,
            "max_tokens": 1500,
            "messages": [{"role": "user", "content": PROMPT}],
            "tools": [{"type": "web_search_20250305", "name": "web_search"}],
        },
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json()
    text_parts = [block["text"] for block in data.get("content", []) if block.get("type") == "text"]
    return "\n".join(text_parts).strip()


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Missing Telegram credentials, skipping send.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    # Telegram caps messages at 4096 chars -- split if the brief runs long
    # rather than silently truncating real content.
    chunks = [text[i:i + 3800] for i in range(0, len(text), 3800)] or [text]
    for chunk in chunks:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk}, timeout=10)


def journal_entry(brief_text):
    today = datetime.datetime.now(tz=SGT).strftime("%Y-%m-%d")
    entry = f"## {today}\n\n{brief_text}\n\n---\n\n"

    journal_path = "docs/index.md"
    if os.path.exists(journal_path):
        with open(journal_path) as f:
            existing = f.read()
    else:
        existing = "# Market Journal\n\nDaily research-backed market briefs, newest first.\n\n---\n\n"

    header, _, rest = existing.partition("---\n\n")
    updated = header + "---\n\n" + entry + rest
    with open(journal_path, "w") as f:
        f.write(updated)


def main():
    print("Generating daily market brief...")
    brief = generate_brief()
    if not brief:
        print("No content returned, aborting.")
        return
    print(brief)
    send_telegram(f"\U0001F4F0 MORNING MARKET BRIEF\n\n{brief}")
    journal_entry(brief)


if __name__ == "__main__":
    main()
