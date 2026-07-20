"""
Daily job: computes RSI(14), 8/21 EMA cross state, and last-candle pattern
from daily bars. Runs alongside calculate_levels.py, writes indicators.json.
"""
import urllib.request
import json

SYMBOL = "MES=F"
RANGE = "3mo"
OUT_FILE = "indicators.json"

def fetch_daily_bars():
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{SYMBOL}?interval=1d&range={RANGE}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
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

def main():
    bars = fetch_daily_bars()
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
        "rsi14": rsi,
        "rsi_read": rsi_read,
        "ema8": round(ema8[-1], 2),
        "ema21": round(ema21[-1], 2),
        "ema_cross_state": cross_state,
        "last_candle_pattern": pattern,
    }
    with open(OUT_FILE, "w") as f:
        json.dump(indicators, f, indent=2)
    print(json.dumps(indicators, indent=2))

if __name__ == "__main__":
    main()
