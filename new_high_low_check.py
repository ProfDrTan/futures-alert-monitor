"""
new_high_low_check.py
======================
Event-triggered alert: fires the moment ES, NQ, RTY, YM, GC, or BTC
futures print a NEW SESSION HIGH or NEW SESSION LOW -- across both
regular AND extended/overnight (Globex) hours, since these contracts
trade nearly 24/5 and the session doesn't reset at the RTH close.

How "session" is defined: Yahoo's intraday chart endpoint, when asked
for interval=5m/range=1d on a continuously-traded futures contract,
returns bars for the current Globex trading day (e.g. Sun 6pm ET
through Fri 5pm ET, rolling day-to-day in between) -- not just the
9:30-4:00 RTH window. We take the max/min of those bars as the
running session high/low, so extended-hours moves are already
included with no separate RTH/ETH split needed.

State (session_state_<LABEL>.json) persists the running high/low and
a session_id (the timestamp of the first bar) so a rollover into a
new trading day is detected and the high/low reset automatically --
otherwise yesterday's high would silently suppress today's real new
high.

Alert message puts the NEW HIGH / NEW LOW line FIRST, always -- that
is the one thing that must never get buried, since it's what's being
traded off directly.
"""

import json
import os
import urllib.request
import datetime
from yahoo_session import yahoo_json

SYMBOLS = {
    "ES": "ES=F",
    "NQ": "NQ=F",
    "RTY": "RTY=F",
    "YM": "YM=F",
    "GC": "GC=F",
    "BTC": "BTC=F",
}

INTRADAY_INTERVAL = "5m"
INTRADAY_RANGE = "1d"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Missing Telegram credentials, skipping send.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": message}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10)


def fetch_intraday_bars(yahoo_symbol):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
           f"?interval={INTRADAY_INTERVAL}&range={INTRADAY_RANGE}")
    data = yahoo_json(url)
    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    q = result["indicators"]["quote"][0]
    volumes = q.get("volume", [None] * len(timestamps))
    bars = []
    for i in range(len(timestamps)):
        if q["high"][i] is None or q["low"][i] is None:
            continue
        bars.append({"ts": timestamps[i], "high": q["high"][i], "low": q["low"][i],
                     "close": q["close"][i], "volume": volumes[i] or 0})
    return bars


def session_vwap(bars):
    """Volume-weighted average price over the session's bars so far, using
    each bar's typical price (H+L+C)/3. Falls back to a simple average of
    typical price if volume data is missing/zero for the session (this can
    happen for some futures on Yahoo, especially overnight) -- an
    unweighted average is still a far better reference than nothing, it's
    just flagged as such in the message.
    """
    total_vol = sum(b["volume"] for b in bars)
    if total_vol > 0:
        weighted_sum = sum(((b["high"] + b["low"] + b["close"]) / 3) * b["volume"] for b in bars)
        return round(weighted_sum / total_vol, 2), True
    typical_prices = [(b["high"] + b["low"] + b["close"]) / 3 for b in bars]
    return round(sum(typical_prices) / len(typical_prices), 2), False


def fmt_ts(ts):
    return datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M UTC")


def check_symbol(label, yahoo_symbol):
    state_file = f"session_state_{label}.json"

    try:
        bars = fetch_intraday_bars(yahoo_symbol)
    except Exception as e:
        print(f"[{label}] fetch failed: {e}")
        return
    if not bars:
        print(f"[{label}] no bars returned, skipping.")
        return

    session_id = bars[0]["ts"]
    session_high = max(b["high"] for b in bars)
    session_low = min(b["low"] for b in bars)
    latest_price = bars[-1]["close"]
    latest_ts = bars[-1]["ts"]
    vwap, vwap_is_volume_weighted = session_vwap(bars)
    vwap_distance = round(latest_price - vwap, 2)
    vwap_side = "above" if vwap_distance > 0 else ("below" if vwap_distance < 0 else "at")
    vwap_note = "" if vwap_is_volume_weighted else " (volume data unavailable -- unweighted avg used instead)"
    vwap_line = f"VWAP: {vwap}{vwap_note} | price is {abs(vwap_distance)} pts {vwap_side} VWAP"

    state = load_json(state_file, default={})

    # New trading day (Globex session) -- reset the running extremes so
    # yesterday's high/low can't suppress today's real new high/low.
    if state.get("session_id") != session_id:
        print(f"[{label}] New session detected (id={session_id}), resetting high/low tracking.")
        state = {"session_id": session_id, "running_high": None, "running_low": None}

    running_high = state.get("running_high")
    running_low = state.get("running_low")

    new_high = running_high is None or session_high > running_high
    new_low = running_low is None or session_low < running_low

    if new_high and running_high is not None:
        msg = (
            f"\U0001F53A NEW HIGH -- {label} {session_high}\n"
            f"(previous session high: {running_high})\n"
            f"{vwap_line}\n"
            f"Last price: {latest_price} | {fmt_ts(latest_ts)}\n"
            f"Session covers regular + extended/overnight hours."
        )
        print(msg)
        send_telegram(msg)

    if new_low and running_low is not None:
        msg = (
            f"\U0001F53B NEW LOW -- {label} {session_low}\n"
            f"(previous session low: {running_low})\n"
            f"{vwap_line}\n"
            f"Last price: {latest_price} | {fmt_ts(latest_ts)}\n"
            f"Session covers regular + extended/overnight hours."
        )
        print(msg)
        send_telegram(msg)

    if running_high is None and running_low is None:
        # First run of a fresh session -- establish the baseline silently,
        # nothing to alert on yet since there's no "previous" extreme.
        print(f"[{label}] Establishing session baseline: high={session_high}, low={session_low}.")

    state["running_high"] = session_high
    state["running_low"] = session_low
    save_json(state_file, state)


def send_test_alert():
    msg = ("\U0001F53A TEST -- new-high/new-low alert pipeline connectivity check. "
           "If you're seeing this, Telegram delivery is working correctly.")
    print(msg)
    send_telegram(msg)


def main():
    if os.environ.get("TEST_ALERT") == "1":
        send_test_alert()
        return
    for label, yahoo_symbol in SYMBOLS.items():
        check_symbol(label, yahoo_symbol)


if __name__ == "__main__":
    main()
