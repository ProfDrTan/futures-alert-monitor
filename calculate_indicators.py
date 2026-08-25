"""
Daily job: computes RSI(14), 8/21 EMA cross state, and last-candle pattern
from daily bars for ES and NQ. Runs alongside calculate_levels.py,
writes indicators_<SYMBOL>.json.
"""
import urllib.request
import json
import os
from yahoo_session import yahoo_json

SYMBOLS = {"ES": "ES=F", "NQ": "NQ=F"}
RANGE = "3mo"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

NOTABLE_PATTERNS = {
    "bullish_engulfing": "bullish",
    "bearish_engulfing": "bearish",
    "hammer": "bullish",
    "shooting_star": "bearish",
}

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Missing Telegram credentials, skipping send.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": message}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10)

def fetch_daily_bars(yahoo_symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}?interval=1d&range={RANGE}"
    data = yahoo_json(url)
    result = data["chart"]["result"][0]
    q = result["indicators"]["quote"][0]
    bars = []
    for i in range(len(result["timestamp"])):
        if q["close"][i] is None:
            continue
        bars.append({"open": q["open"][i], "high": q["high"][i],
                     "low": q["low"][i], "close": q["close"][i],
                     "volume": q["volume"][i] or 0})
    return bars

def rsi14(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def ema_series(closes, period):
    k = 2 / (period + 1)
    ema = [closes[0]]
    for price in closes[1:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema

def detect_cross(ema_fast, ema_slow):
    # compare last two bars to catch a cross that just happened
    prev_diff = ema_fast[-2] - ema_slow[-2]
    curr_diff = ema_fast[-1] - ema_slow[-1]
    if prev_diff <= 0 and curr_diff > 0:
        return "golden_cross_just_occurred"
    if prev_diff >= 0 and curr_diff < 0:
        return "death_cross_just_occurred"
    return "bullish_bias" if curr_diff > 0 else "bearish_bias"

def detect_candle_pattern(bars):
    if len(bars) < 2:
        return None
    prev, last = bars[-2], bars[-1]
    body = abs(last["close"] - last["open"])
    range_ = last["high"] - last["low"]
    prev_body = abs(prev["close"] - prev["open"])

    # bullish engulfing
    if (prev["close"] < prev["open"] and last["close"] > last["open"]
            and last["close"] >= prev["open"] and last["open"] <= prev["close"]
            and body > prev_body):
        return "bullish_engulfing"
    # bearish engulfing
    if (prev["close"] > prev["open"] and last["close"] < last["open"]
            and last["open"] >= prev["close"] and last["close"] <= prev["open"]
            and body > prev_body):
        return "bearish_engulfing"
    # hammer (small body near top, long lower wick)
    if range_ > 0:
        upper_wick = last["high"] - max(last["open"], last["close"])
        lower_wick = min(last["open"], last["close"]) - last["low"]
        if lower_wick > body * 2 and upper_wick < body and body > 0:
            return "hammer"
        if upper_wick > body * 2 and lower_wick < body and body > 0:
            return "shooting_star"
    # doji (very small body relative to range)
    if range_ > 0 and body / range_ < 0.1:
        return "doji"
    return None

def calc_for_symbol(label, yahoo_symbol):
    bars = fetch_daily_bars(yahoo_symbol)
    closes = [b["close"] for b in bars]

    rsi = rsi14(closes)
    ema8 = ema_series(closes, 8)
    ema21 = ema_series(closes, 21)
    cross_state = detect_cross(ema8, ema21)
    pattern = detect_candle_pattern(bars)

    rsi_read = None
    if rsi is not None:
        if rsi >= 70:
            rsi_read = "overbought"
        elif rsi <= 30:
            rsi_read = "oversold"
        else:
            rsi_read = "neutral"

    indicators = {
        "symbol": label,
        "rsi14": rsi,
        "rsi_read": rsi_read,
        "ema8": round(ema8[-1], 2),
        "ema21": round(ema21[-1], 2),
        "ema_cross_state": cross_state,
        "last_candle_pattern": pattern,
    }
    out_file = f"indicators_{label}.json"
    with open(out_file, "w") as f:
        json.dump(indicators, f, indent=2)
    print(json.dumps(indicators, indent=2))

    # Standalone alerts -- fire independent of any price-level touch, since a
    # cross or a notable candle is a signal in its own right, not just context.
    alerts = []
    if cross_state == "golden_cross_just_occurred":
        alerts.append(f"{label} GOLDEN CROSS: 8-EMA just crossed above 21-EMA (bullish). "
                       f"RSI {rsi} ({rsi_read}).")
    elif cross_state == "death_cross_just_occurred":
        alerts.append(f"{label} DEATH CROSS: 8-EMA just crossed below 21-EMA (bearish). "
                       f"RSI {rsi} ({rsi_read}).")

    if pattern in NOTABLE_PATTERNS:
        bias = NOTABLE_PATTERNS[pattern]
        alerts.append(f"{label} CANDLE: yesterday's daily candle formed a "
                       f"{pattern.replace('_', ' ')} ({bias}). RSI {rsi} ({rsi_read}), "
                       f"EMA bias: {cross_state.replace('_', ' ')}.")

    for msg in alerts:
        print(msg)
        send_telegram(msg)

def main():
    for label, yahoo_symbol in SYMBOLS.items():
        calc_for_symbol(label, yahoo_symbol)

if __name__ == "__main__":
    main()

