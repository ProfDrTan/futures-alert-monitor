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
import io
import time
import urllib.request
import datetime
import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
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

SGT = datetime.timezone(datetime.timedelta(hours=8))


def to_sgt(ts):
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).astimezone(SGT)


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


def vwap_arrow(distance):
    """Color-coded arrow for at-a-glance above/below VWAP reading.
    Two intensity levels so a big move away from VWAP visually stands out
    from a small one, since that's the whole point of glancing at this.
    """
    if distance > 0:
        return "\U0001F7E2\u2B06\uFE0F\u2B06\uFE0F" if distance > 20 else "\U0001F7E2\u2B06\uFE0F"
    if distance < 0:
        return "\U0001F534\u2B07\uFE0F\u2B07\uFE0F" if distance < -20 else "\U0001F534\u2B07\uFE0F"
    return "\u26AA"


def make_chart(label, bars, vwap, session_high, session_low):
    """Renders a small PNG: session price line, VWAP as a dashed reference
    line, and the area between them shaded green when price is above VWAP
    and red when below -- so "how far off VWAP" is a visual read, not a
    number you have to parse. High/low points are marked directly on the
    line since those are the other two numbers being tracked.
    """
    times = [to_sgt(b["ts"]) for b in bars]
    closes = [b["close"] for b in bars]

    fig, ax = plt.subplots(figsize=(7, 3.5), dpi=130)
    ax.plot(times, closes, color="#1f77b4", linewidth=1.6, zorder=3)
    ax.axhline(vwap, color="#888888", linestyle="--", linewidth=1.2, label=f"VWAP {vwap}")

    above = [c if c >= vwap else vwap for c in closes]
    below = [c if c <= vwap else vwap for c in closes]
    ax.fill_between(times, closes, vwap, where=[c >= vwap for c in closes],
                     color="#2ca02c", alpha=0.25, interpolate=True)
    ax.fill_between(times, closes, vwap, where=[c <= vwap for c in closes],
                     color="#d62728", alpha=0.25, interpolate=True)

    high_bar = max(bars, key=lambda b: b["high"])
    low_bar = min(bars, key=lambda b: b["low"])
    ax.scatter([to_sgt(high_bar["ts"])], [high_bar["high"]], color="#2ca02c", zorder=4, s=30)
    ax.scatter([to_sgt(low_bar["ts"])], [low_bar["low"]], color="#d62728", zorder=4, s=30)

    ax.set_title(f"{label} -- session price vs VWAP (times in SGT)", fontsize=11)
    ax.set_xlabel("Time (SGT, UTC+8)", fontsize=8)
    ax.legend(loc="upper left", fontsize=8, frameon=False)
    ax.tick_params(axis="x", rotation=30, labelsize=7)
    ax.tick_params(axis="y", labelsize=8)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf


def send_telegram_photo(photo_buf, caption):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Missing Telegram credentials, skipping send.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    files = {"photo": ("chart.png", photo_buf, "image/png")}
    data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption}
    resp = requests.post(url, data=data, files=files, timeout=20)
    if not resp.ok:
        print(f"sendPhoto failed ({resp.status_code}): {resp.text}")
        # Fall back to a plain text message so the alert still gets through
        # even if the image upload itself has a problem.
        send_telegram(caption)


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
    return to_sgt(ts).strftime("%Y-%m-%d %H:%M SGT")


def check_symbol(label, yahoo_symbol, force_snapshot=False):
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
    arrow = vwap_arrow(vwap_distance)
    vwap_line = f"{arrow} VWAP: {vwap}{vwap_note} | price is {abs(vwap_distance)} pts {vwap_side} VWAP"

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
        chart = make_chart(label, bars, vwap, session_high, session_low)
        send_telegram_photo(chart, msg)

    if new_low and running_low is not None:
        msg = (
            f"\U0001F53B NEW LOW -- {label} {session_low}\n"
            f"(previous session low: {running_low})\n"
            f"{vwap_line}\n"
            f"Last price: {latest_price} | {fmt_ts(latest_ts)}\n"
            f"Session covers regular + extended/overnight hours."
        )
        print(msg)
        chart = make_chart(label, bars, vwap, session_high, session_low)
        send_telegram_photo(chart, msg)

    if running_high is None and running_low is None:
        # First run of a fresh session -- establish the baseline silently,
        # nothing to alert on yet since there's no "previous" extreme.
        print(f"[{label}] Establishing session baseline: high={session_high}, low={session_low}.")

    if force_snapshot:
        msg = (
            f"\U0001F4CA SNAPSHOT -- {label}\n"
            f"Session high: {session_high} | Session low: {session_low}\n"
            f"{vwap_line}\n"
            f"Last price: {latest_price} | {fmt_ts(latest_ts)}\n"
            f"Session covers regular + extended/overnight hours."
        )
        print(msg)
        chart = make_chart(label, bars, vwap, session_high, session_low)
        send_telegram_photo(chart, msg)

    state["running_high"] = session_high
    state["running_low"] = session_low
    save_json(state_file, state)


def send_test_alert():
    msg = ("\U0001F53A TEST -- new-high/new-low alert pipeline connectivity check. "
           "If you're seeing this, Telegram delivery is working correctly.")
    print(msg)
    send_telegram(msg)


def run_all_symbols(force_snapshot=False):
    for label, yahoo_symbol in SYMBOLS.items():
        check_symbol(label, yahoo_symbol, force_snapshot=force_snapshot)


def main():
    if os.environ.get("TEST_ALERT") == "1":
        send_test_alert()
        return

    force_snapshot = os.environ.get("SEND_SNAPSHOT") == "1"

    if os.environ.get("CONTINUOUS_LOOP") != "1":
        # Manual/dispatch runs (test, snapshot, or a one-off check) just do a
        # single pass and exit -- no reason to make a quick manual test wait
        # around inside a multi-hour loop.
        run_all_symbols(force_snapshot=force_snapshot)
        return

    # Scheduled runs: GitHub's native cron trigger has proven unreliable at
    # 5-minute granularity (it can go silent for hours under load, which is
    # exactly what happened and caused a real missed alert). Rather than
    # depending on GitHub firing this workflow every 5 minutes, the
    # scheduled trigger instead starts ONE long-lived job that polls every
    # 5 minutes internally via a sleep loop, for as long as the runner's
    # time budget allows. This needs GitHub's scheduler to fire reliably
    # only once every ~5 hours to restart the loop -- a far easier bar for
    # it to clear than every 5 minutes, since the failure mode we hit was
    # specific to short-interval scheduling.
    loop_budget_seconds = int(os.environ.get("LOOP_BUDGET_SECONDS", 290 * 60))
    poll_interval_seconds = 300
    start = time.time()
    iteration = 0

    while time.time() - start < loop_budget_seconds:
        iteration += 1
        print(f"--- poll iteration {iteration} at {datetime.datetime.utcnow().isoformat()} UTC ---")
        try:
            run_all_symbols(force_snapshot=False)
        except Exception as e:
            # A single bad iteration (e.g. a transient Yahoo fetch error)
            # should not kill hours of remaining coverage -- log and
            # continue polling.
            print(f"Iteration {iteration} failed: {e}")

        elapsed = time.time() - start
        remaining = loop_budget_seconds - elapsed
        if remaining <= 0:
            break
        time.sleep(min(poll_interval_seconds, remaining))

    print(f"Loop budget exhausted after {iteration} iterations, exiting cleanly for restart.")


if __name__ == "__main__":
    main()
