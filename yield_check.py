import urllib.request
import json
import os
import datetime

# CBOE yield indices, quoted as yield*10 (e.g. 47.35 = 4.735%).
# 2-year omitted: no verified free real-time 2yr yield index exists (only
# ZT futures price, which needs a duration approximation -- not a clean
# yield number, so it's left out rather than faked).
SYMBOLS = {"10Y": "^TNX", "5Y": "^FVX"}

ALERT_THRESHOLD_BPS = 5.0
STATE_FILE = "last_yield_state.json"

TELEGRAM_BOT_TOKEN = os.environ.get("YIELD_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("YIELD_TELEGRAM_CHAT_ID")


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)


def fetch_yield(yahoo_symbol):
    """Returns (current_pct, prior_close_pct)."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}?interval=1m&range=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    result = data["chart"]["result"][0]
    meta = result["meta"]
    current = meta["regularMarketPrice"] / 10.0
    prior_close = meta["previousClose"] / 10.0 if meta.get("previousClose") else current
    return current, prior_close


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Missing Telegram credentials, skipping send.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": message}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10)


def send_test_alert():
    msg = ("TEST ALERT: yield-monitor connectivity check. If you're seeing "
           "this, the 10Y/5Y Treasury yield alert pipeline is wired up correctly. "
           "Note: this is delayed data (Yahoo Finance, ~15-20min lag), not a live feed.")
    print(msg)
    send_telegram(msg)


def check_symbol(label, yahoo_symbol, state):
    today = datetime.date.today().isoformat()
    try:
        current, prior_close = fetch_yield(yahoo_symbol)
    except Exception as e:
        print(f"[{label}] fetch failed: {e}")
        return

    sym_state = state.get(label, {})
    if sym_state.get("date") != today:
        # New session -- reset the alert baseline to today's prior close.
        sym_state = {"date": today, "last_alert_yield": prior_close}

    last_alert_yield = sym_state.get("last_alert_yield", prior_close)
    diff_bps = round((current - last_alert_yield) * 100, 1)
    intraday_bps = round((current - prior_close) * 100, 1)

    if abs(diff_bps) >= ALERT_THRESHOLD_BPS:
        direction = "up" if diff_bps > 0 else "down"
        msg = (f"{label} Treasury yield {direction} {abs(diff_bps)}bps to {current:.3f}% "
               f"(intraday: {'+' if intraday_bps >= 0 else ''}{intraday_bps}bps vs prior close {prior_close:.3f}%). "
               f"[delayed ~15-20min, Yahoo Finance]")
        print(msg)
        send_telegram(msg)
        sym_state["last_alert_yield"] = current
    else:
        print(f"[{label}] {current:.3f}% -- {diff_bps}bps since last alert, below {ALERT_THRESHOLD_BPS}bps threshold.")

    state[label] = sym_state


def main():
    if os.environ.get("TEST_ALERT") == "1":
        send_test_alert()
        return
    state = load_json(STATE_FILE, default={})
    for label, yahoo_symbol in SYMBOLS.items():
        check_symbol(label, yahoo_symbol, state)
    save_json(STATE_FILE, state)


if __name__ == "__main__":
    main()
