"""
squeeze_check.py
================
TTM Squeeze detector for NQ futures — futures-alert-monitor
Mirrors the code style of check_price.py (pure stdlib, no pip installs).

Logic:
  Squeeze ON  = Bollinger Bands (20, 2.0) are INSIDE Keltner Channel (20, 1.5)
  Squeeze OFF = BB expands outside KC
  Alert fires only on TRANSITIONS (squeeze starts or fires), not every bar.

State is persisted to last_squeeze_state_{LABEL}.json and committed by the
workflow, so duplicate alerts are suppressed across runs.
"""

import urllib.request
import json
import os
import math
import datetime

# ── Symbols ──────────────────────────────────────────────────────────────────
SYMBOLS = {"NQ": "NQ=F", "ES": "ES=F"}

# ── Squeeze parameters (matches TOS TTM_Squeeze defaults) ────────────────────
BB_LENGTH = 20
BB_MULT   = 2.0
KC_LENGTH = 20
KC_MULT   = 1.5

# ── Data fetch (5-min bars, today's session) ─────────────────────────────────
INTERVAL = "5m"
RANGE    = "1d"

# ── Telegram (from repo secrets, same as check_price.py) ────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")


# ── Data helpers ─────────────────────────────────────────────────────────────

def fetch_bars(yahoo_symbol):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
           f"?interval={INTERVAL}&range={RANGE}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    q = result["indicators"]["quote"][0]
    bars = []
    for i in range(len(timestamps)):
        if None in (q["close"][i], q["high"][i], q["low"][i], q["open"][i]):
            continue
        bars.append({
            "ts":    timestamps[i],
            "open":  q["open"][i],
            "high":  q["high"][i],
            "low":   q["low"][i],
            "close": q["close"][i],
        })
    return bars


def load_json(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ── Math helpers (pure stdlib — no numpy/pandas) ─────────────────────────────

def _sma(values, period):
    return sum(values[-period:]) / period

def _std(values, period):
    mean = _sma(values, period)
    return math.sqrt(sum((x - mean) ** 2 for x in values[-period:]) / period)

def _ema(values, period):
    """EMA seeded with SMA of first `period` values."""
    k = 2 / (period + 1)
    val = sum(values[:period]) / period
    for v in values[period:]:
        val = v * k + val * (1 - k)
    return val

def _true_ranges(bars):
    tr = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    return tr

def _atr(bars, period):
    tr = _true_ranges(bars)
    if len(tr) < period:
        return None
    return sum(tr[-period:]) / period


# ── Squeeze calculation ───────────────────────────────────────────────────────

def calc_squeeze(bars):
    """
    Returns dict with squeeze state for the most recent completed bar, or None
    if there are not enough bars to calculate.
    """
    needed = max(BB_LENGTH, KC_LENGTH) + 2
    if len(bars) < needed:
        return None

    closes = [b["close"] for b in bars]

    # Bollinger Bands
    bb_mid   = _sma(closes, BB_LENGTH)
    bb_std   = _std(closes, BB_LENGTH)
    bb_upper = bb_mid + BB_MULT * bb_std
    bb_lower = bb_mid - BB_MULT * bb_std

    # Keltner Channel
    kc_mid   = _ema(closes, KC_LENGTH)
    atr_val  = _atr(bars, KC_LENGTH)
    if atr_val is None:
        return None
    kc_upper = kc_mid + KC_MULT * atr_val
    kc_lower = kc_mid - KC_MULT * atr_val

    # Squeeze = BB entirely inside KC
    squeeze_on = (bb_upper < kc_upper) and (bb_lower > kc_lower)

    # Momentum proxy: close vs. average of BB-mid and KC-mid
    momentum  = closes[-1] - (bb_mid + kc_mid) / 2
    direction = "LONG" if momentum > 0 else "SHORT"

    return {
        "squeeze_on": squeeze_on,
        "momentum":   round(momentum, 2),
        "direction":  direction,
        "close":      closes[-1],
        "ts":         bars[-1]["ts"],
        "bb_upper":   round(bb_upper, 2),
        "bb_lower":   round(bb_lower, 2),
        "kc_upper":   round(kc_upper, 2),
        "kc_lower":   round(kc_lower, 2),
    }


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Missing Telegram credentials — skipping send.")
        return
    url  = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": message}).encode()
    req  = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    urllib.request.urlopen(req, timeout=10)


# ── Per-symbol check ──────────────────────────────────────────────────────────

def check_symbol(label, yahoo_symbol):
    state_file = f"last_squeeze_state_{label}.json"
    state      = load_json(state_file, default={})
    now_str    = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    try:
        bars = fetch_bars(yahoo_symbol)
    except Exception as e:
        print(f"[{label}] Fetch failed: {e}")
        return

    needed = max(BB_LENGTH, KC_LENGTH) + 2
    if len(bars) < needed:
        print(f"[{label}] Only {len(bars)} bars available, need {needed}. Skipping.")
        return

    # Evaluate current bar and the bar before it to detect transitions
    current  = calc_squeeze(bars)
    previous = calc_squeeze(bars[:-1])

    if not current or not previous:
        print(f"[{label}] Insufficient data for squeeze. Skipping.")
        return

    current_ts    = str(current["ts"])
    last_alert_ts = state.get("last_alert_ts", "")

    just_started = current["squeeze_on"]  and not previous["squeeze_on"]
    just_fired   = not current["squeeze_on"] and previous["squeeze_on"]

    msg = None

    if just_started and current_ts != last_alert_ts:
        msg = (
            f"🔴 SQUEEZE ON — {label} (5-min)\n"
            f"Price: {current['close']:,.2f}\n"
            f"BB is now inside Keltner Channel — market coiling.\n"
            f"Prepare for a move. No direction confirmed yet.\n"
            f"BB: [{current['bb_lower']:,.2f} – {current['bb_upper']:,.2f}]\n"
            f"KC: [{current['kc_lower']:,.2f} – {current['kc_upper']:,.2f}]\n"
            f"⏰ {now_str}"
        )
        state["condition"]    = "on"
        state["last_alert_ts"] = current_ts

    elif just_fired and current_ts != last_alert_ts:
        emoji = "🟢🚀" if current["direction"] == "LONG" else "🔴📉"
        msg = (
            f"{emoji} SQUEEZE FIRED {current['direction']} — {label} (5-min)\n"
            f"Price: {current['close']:,.2f}\n"
            f"BB expanded outside Keltner Channel — breakout in progress.\n"
            f"Momentum: {current['momentum']:+.2f} → {current['direction']} bias\n"
            f"Check your chart NOW and confirm with Stochastic before entering.\n"
            f"⏰ {now_str}"
        )
        state["condition"]    = "fired"
        state["last_alert_ts"] = current_ts

    else:
        status = "ON 🔴" if current["squeeze_on"] else "OFF 🟢"
        print(
            f"[{label}] Squeeze {status} | "
            f"Price {current['close']:,.2f} | "
            f"Momentum {current['momentum']:+.2f} ({current['direction']}) | "
            f"No transition — no alert."
        )

    if msg:
        print(msg)
        send_telegram(msg)

    save_json(state_file, state)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    for label, yahoo_symbol in SYMBOLS.items():
        check_symbol(label, yahoo_symbol)

if __name__ == "__main__":
    main()
