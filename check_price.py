import urllib.request
import json
import os
import sys
import statistics
import datetime

SYMBOLS = {"ES": "ES=F", "NQ": "NQ=F"}

# Distance (in points) from a level that counts as "approaching" it.
APPROACH_DISTANCE = {"ES": 10.0, "NQ": 40.0}

# US regular session, ET converted to UTC (handles standard offset; DST-aware
# enough for our purposes since futures trade nearly 24/5 anyway).
US_MARKET_OPEN_UTC_HOUR = 13.5   # 9:30am ET
US_MARKET_CLOSE_UTC_HOUR = 20.0  # 4:00pm ET

INTRADAY_INTERVAL = "5m"
INTRADAY_RANGE = "1d"

def fetch_intraday_bars(yahoo_symbol):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
           f"?interval={INTRADAY_INTERVAL}&range={INTRADAY_RANGE}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    q = result["indicators"]["quote"][0]
    bars = []
    for i in range(len(timestamps)):
        if q["close"][i] is None:
            continue
        bars.append({"ts": timestamps[i], "open": q["open"][i], "high": q["high"][i],
                     "low": q["low"][i], "close": q["close"][i]})
    return bars

def ema_series(closes, period):
    k = 2 / (period + 1)
    ema = [closes[0]]
    for price in closes[1:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema

def detect_cross(ema_fast, ema_slow):
    prev_diff = ema_fast[-2] - ema_slow[-2]
    curr_diff = ema_fast[-1] - ema_slow[-1]
    if prev_diff <= 0 and curr_diff > 0:
        return "golden_cross_just_occurred"
    if prev_diff >= 0 and curr_diff < 0:
        return "death_cross_just_occurred"
    return "bullish_bias" if curr_diff > 0 else "bearish_bias"

NOTABLE_INTRADAY_PATTERNS = {
    "bullish_engulfing": "bullish",
    "bearish_engulfing": "bearish",
    "hammer": "bullish",
    "shooting_star": "bearish",
}

def detect_candle_pattern(bars):
    if len(bars) < 2:
        return None
    prev, last = bars[-2], bars[-1]
    body = abs(last["close"] - last["open"])
    range_ = last["high"] - last["low"]
    prev_body = abs(prev["close"] - prev["open"])
    if (prev["close"] < prev["open"] and last["close"] > last["open"]
            and last["close"] >= prev["open"] and last["open"] <= prev["close"]
            and body > prev_body):
        return "bullish_engulfing"
    if (prev["close"] > prev["open"] and last["close"] < last["open"]
            and last["open"] >= prev["close"] and last["close"] <= prev["open"]
            and body > prev_body):
        return "bearish_engulfing"
    if range_ > 0:
        upper_wick = last["high"] - max(last["open"], last["close"])
        lower_wick = min(last["open"], last["close"]) - last["low"]
        if lower_wick > body * 2 and upper_wick < body and body > 0:
            return "hammer"
        if upper_wick > body * 2 and lower_wick < body and body > 0:
            return "shooting_star"
    if range_ > 0 and body / range_ < 0.1:
        return "doji"
    return None

def check_intraday_signals(label, yahoo_symbol, state, session):
    """5-min-bar EMA cross + candle pattern check, deduped by bar timestamp
    so the same event doesn't re-alert on every subsequent poll."""
    try:
        bars = fetch_intraday_bars(yahoo_symbol)
    except Exception as e:
        print(f"[{label}] intraday fetch failed: {e}")
        return
    if len(bars) < 22:
        print(f"[{label}] not enough intraday bars yet ({len(bars)}).")
        return

    closes = [b["close"] for b in bars]
    ema8 = ema_series(closes, 8)
    ema21 = ema_series(closes, 21)
    cross_state = detect_cross(ema8, ema21)
    pattern = detect_candle_pattern(bars)
    latest_ts = bars[-1]["ts"]

    already_alerted_cross = state.get("intraday_cross_alert_ts") == latest_ts
    already_alerted_pattern = state.get("intraday_pattern_alert_ts") == latest_ts

    if cross_state in ("golden_cross_just_occurred", "death_cross_just_occurred") and not already_alerted_cross:
        direction = "bullish" if "golden" in cross_state else "bearish"
        msg = (f"{label} INTRADAY {cross_state.replace('_just_occurred', '').replace('_', ' ').upper()}: "
               f"8/21 EMA cross on the 5-min chart just flipped {direction} at {bars[-1]['close']}.")
        print(msg)
        send_telegram(msg)
        state["intraday_cross_alert_ts"] = latest_ts

    if pattern in NOTABLE_INTRADAY_PATTERNS and not already_alerted_pattern:
        bias = NOTABLE_INTRADAY_PATTERNS[pattern]
        msg = (f"{label} INTRADAY CANDLE: 5-min {pattern.replace('_', ' ')} ({bias}) just formed "
               f"at {bars[-1]['close']}.")
        print(msg)
        send_telegram(msg)
        state["intraday_pattern_alert_ts"] = latest_ts


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

def get_price(yahoo_symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}?interval=1m&range=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    result = data["chart"]["result"][0]
    price = result["meta"]["regularMarketPrice"]
    quote = result["indicators"]["quote"][0]
    volumes = [v for v in quote.get("volume", []) if v]
    today_volume = sum(volumes) if volumes else 0
    return price, today_volume

def get_avg_daily_volume(yahoo_symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}?interval=1d&range=1mo"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    result = data["chart"]["result"][0]
    volumes = [v for v in result["indicators"]["quote"][0]["volume"] if v]
    return statistics.mean(volumes) if volumes else None

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Missing Telegram credentials, skipping send.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": message}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10)

def session_label(now_utc=None):
    now_utc = now_utc or datetime.datetime.utcnow()
    if now_utc.weekday() >= 5:
        return "weekend"
    hour = now_utc.hour + now_utc.minute / 60.0
    if US_MARKET_OPEN_UTC_HOUR <= hour < US_MARKET_CLOSE_UTC_HOUR:
        return "regular_session"
    return "pre_or_after_hours"

def volume_read(ratio, session):
    if ratio is None:
        return "volume data unavailable"
    if session != "regular_session":
        # Comparing partial-session volume-so-far against a full regular-day
        # average is misleading outside regular hours -- don't call it "low
        # conviction", just say the comparison isn't reliable right now.
        return f"{ratio}x avg volume (outside regular hours -- this ratio isn't a reliable conviction read right now)"
    if ratio >= 1.3:
        return f"{ratio}x avg volume (high conviction)"
    if ratio <= 0.6:
        return f"{ratio}x avg volume (low conviction, touch may be noise)"
    return f"{ratio}x avg volume (normal)"

def ema_cross_read(state):
    return {
        "golden_cross_just_occurred": "8/21 EMA just golden-crossed (bullish)",
        "death_cross_just_occurred": "8/21 EMA just death-crossed (bearish)",
        "bullish_bias": "8-EMA above 21-EMA (bullish bias)",
        "bearish_bias": "8-EMA below 21-EMA (bearish bias)",
    }.get(state, "EMA state unknown")

def rsi_read_text(rsi, read):
    if rsi is None:
        return "RSI unavailable"
    return f"RSI {rsi} ({read})"

def classify_zone(price, support, resistance, approach_dist):
    """Returns 'support', 'approaching_support', 'resistance',
    'approaching_resistance', or None. Exact touch takes priority."""
    if support is not None and price <= support:
        return "support"
    if resistance is not None and price >= resistance:
        return "resistance"
    if support is not None and price <= support + approach_dist:
        return "approaching_support"
    if resistance is not None and price >= resistance - approach_dist:
        return "approaching_resistance"
    return None

def classify_pair_zone(price, lower, upper, approach_dist, lower_name, upper_name):
    """Generic version of classify_zone for any lower/upper level pair (e.g. VAL/VAH)."""
    if lower is not None and price <= lower:
        return lower_name
    if upper is not None and price >= upper:
        return upper_name
    if lower is not None and price <= lower + approach_dist:
        return f"approaching_{lower_name}"
    if upper is not None and price >= upper - approach_dist:
        return f"approaching_{upper_name}"
    return None

def classify_poc_zone(price, poc, approach_dist):
    """POC is a single magnet level, not a pair. 'at_poc' uses a tighter band
    than the general approach distance since POC is a precise price, not a zone."""
    if poc is None:
        return None
    tight = approach_dist * 0.3
    if abs(price - poc) <= tight:
        return "at_poc"
    if abs(price - poc) <= approach_dist:
        return "approaching_poc"
    return None

def check_value_area_and_poc(label, price, levels, approach_dist, state, vol_text, rsi, rsi_lbl, cross_state, pattern):
    poc = levels.get("poc")
    vah = levels.get("vah")
    val = levels.get("val")

    # ---- Value Area (VAL/VAH pair) -- same touch/approach/reclaim pattern as support/resistance ----
    va_zone = classify_pair_zone(price, val, vah, approach_dist, "val", "vah")
    last_va_zone = state.get("last_va_zone")
    if "va_touch_count" not in state:
        state["va_touch_count"] = {"val": 0, "vah": 0}

    if va_zone and va_zone != last_va_zone:
        is_touch = va_zone in ("val", "vah")
        if is_touch:
            state["va_touch_count"][va_zone] = state["va_touch_count"].get(va_zone, 0) + 1
            touch_n = state["va_touch_count"][va_zone]
            touch_desc = "1st touch" if touch_n == 1 else ("2nd retest" if touch_n == 2 else f"{touch_n}th retest")
            level_val = val if va_zone == "val" else vah
            label_name = "VALUE AREA LOW" if va_zone == "val" else "VALUE AREA HIGH"
            msg = (f"{label} ALERT: price {price} hit {label_name} at {level_val} ({touch_desc}). "
                   f"{vol_text.capitalize()}. {rsi_read_text(rsi, rsi_lbl)}. {ema_cross_read(cross_state)}.")
        else:
            base = va_zone.replace("approaching_", "")
            level_val = val if base == "val" else vah
            label_name = "VALUE AREA LOW" if base == "val" else "VALUE AREA HIGH"
            distance = round(abs(price - level_val), 2)
            msg = (f"{label} HEADS-UP: price {price} is {distance}pts from {label_name} ({level_val}). "
                   f"{vol_text.capitalize()}. {rsi_read_text(rsi, rsi_lbl)}. {ema_cross_read(cross_state)}.")
        print(msg)
        send_telegram(msg)
        state["last_va_zone"] = va_zone
    elif va_zone != "val" and last_va_zone == "val":
        msg = (f"{label} RECLAIM: price {price} moved back above VALUE AREA LOW ({val}) -- "
               f"back inside fair value from below. {vol_text.capitalize()}. {rsi_read_text(rsi, rsi_lbl)}.")
        print(msg)
        send_telegram(msg)
        state["last_va_zone"] = va_zone
    elif va_zone != "vah" and last_va_zone == "vah":
        msg = (f"{label} REJECTION: price {price} moved back below VALUE AREA HIGH ({vah}) -- "
               f"back inside fair value from above. {vol_text.capitalize()}. {rsi_read_text(rsi, rsi_lbl)}.")
        print(msg)
        send_telegram(msg)
        state["last_va_zone"] = va_zone
    elif va_zone is None and last_va_zone is not None:
        state["last_va_zone"] = None

    # ---- POC (single magnet level) -- touch + approach only ----
    poc_zone = classify_poc_zone(price, poc, approach_dist)
    last_poc_zone = state.get("last_poc_zone")
    if poc_zone and poc_zone != last_poc_zone:
        if poc_zone == "at_poc":
            msg = (f"{label} AT POC: price {price} is at the Point of Control ({poc}) -- "
                   f"the price level with the most traded volume. {vol_text.capitalize()}. "
                   f"{rsi_read_text(rsi, rsi_lbl)}. {ema_cross_read(cross_state)}.")
        else:
            distance = round(abs(price - poc), 2)
            msg = (f"{label} APPROACHING POC: price {price} is {distance}pts from POC ({poc}). "
                   f"{vol_text.capitalize()}. {rsi_read_text(rsi, rsi_lbl)}.")
        print(msg)
        send_telegram(msg)
        state["last_poc_zone"] = poc_zone
    elif poc_zone is None and last_poc_zone is not None:
        state["last_poc_zone"] = None

def check_symbol(label, yahoo_symbol):
    levels_file = f"levels_{label}.json"
    indicators_file = f"indicators_{label}.json"
    state_file = f"last_alert_state_{label}.json"
    approach_dist = APPROACH_DISTANCE.get(label, 10.0)
    session = session_label()

    levels = load_json(levels_file)
    if not levels:
        print(f"No {levels_file} yet, skipping {label}.")
        return
    indicators = load_json(indicators_file, default={})

    support = levels.get("support")
    resistance = levels.get("resistance")

    try:
        price, today_volume = get_price(yahoo_symbol)
    except Exception as e:
        print(f"[{label}] Failed to fetch price: {e}")
        return

    avg_volume = None
    try:
        avg_volume = get_avg_daily_volume(yahoo_symbol)
    except Exception as e:
        print(f"[{label}] Could not fetch avg volume: {e}")

    vol_ratio = round(today_volume / avg_volume, 2) if avg_volume and today_volume else None

    zone = classify_zone(price, support, resistance, approach_dist)

    state = load_json(state_file, default={"last_zone": None, "touch_count": {"support": 0, "resistance": 0}})
    if "touch_count" not in state:
        state["touch_count"] = {"support": 0, "resistance": 0}

    last_zone = state.get("last_zone")
    is_exact_touch = zone in ("support", "resistance")

    rsi = indicators.get("rsi14")
    rsi_lbl = indicators.get("rsi_read")
    cross_state = indicators.get("ema_cross_state")
    pattern = indicators.get("last_candle_pattern")
    vol_text = volume_read(vol_ratio, session)

    check_intraday_signals(label, yahoo_symbol, state, session)
    check_value_area_and_poc(label, price, levels, approach_dist, state, vol_text, rsi, rsi_lbl, cross_state, pattern)

    if zone and zone != last_zone:
        # ---- New touch or new approach ----
        if is_exact_touch:
            state["touch_count"][zone] = state["touch_count"].get(zone, 0) + 1
            touch_n = state["touch_count"][zone]
            touch_desc = "1st touch" if touch_n == 1 else ("2nd retest" if touch_n == 2 else f"{touch_n}th retest")

            level_val = support if zone == "support" else resistance
            other_level = resistance if zone == "support" else support
            distance_to_other = round(abs(other_level - price), 2) if other_level else None

            lines = [
                f"{label} ALERT: price {price} hit {zone.upper()} at {level_val} ({touch_desc}).",
                vol_text.capitalize() + ".",
                rsi_read_text(rsi, rsi_lbl) + ". " + ema_cross_read(cross_state) + ".",
            ]
            if pattern:
                lines.append(f"Last daily candle: {pattern.replace('_', ' ')}.")
            if distance_to_other:
                lines.append(f"{distance_to_other}pts to the opposite level ({other_level}).")
            if touch_n >= 2:
                lines.append("Repeated test of this level -- worth a closer look now.")
        else:
            base = zone.replace("approaching_", "")
            level_val = support if base == "support" else resistance
            distance = round(abs(price - level_val), 2)
            lines = [
                f"{label} HEADS-UP: price {price} is {distance}pts from {base.upper()} ({level_val}).",
                vol_text.capitalize() + ".",
                rsi_read_text(rsi, rsi_lbl) + ". " + ema_cross_read(cross_state) + ".",
            ]
            if pattern:
                lines.append(f"Last daily candle: {pattern.replace('_', ' ')}.")

        msg = " ".join(lines)
        print(msg)
        send_telegram(msg)
        state["last_zone"] = zone
        save_json(state_file, state)

    elif zone != "support" and last_zone == "support":
        # ---- Price left an exact support touch: reclaim (bullish) ----
        distance = round(price - support, 2) if support else None
        lines = [
            f"{label} RECLAIM (possible LONG setup): price {price} moved back above SUPPORT ({support}), "
            f"{distance}pts clear.",
            vol_text.capitalize() + ".",
            rsi_read_text(rsi, rsi_lbl) + ". " + ema_cross_read(cross_state) + ".",
            "Still worth confirming with a retest/hold before sizing in -- this alone isn't full confirmation.",
        ]
        msg = " ".join(lines)
        print(msg)
        send_telegram(msg)
        state["last_zone"] = zone
        save_json(state_file, state)

    elif zone != "resistance" and last_zone == "resistance":
        # ---- Price left an exact resistance touch: rejection (bearish) ----
        distance = round(resistance - price, 2) if resistance else None
        lines = [
            f"{label} REJECTION (possible SHORT setup): price {price} moved back below RESISTANCE ({resistance}), "
            f"{distance}pts clear.",
            vol_text.capitalize() + ".",
            rsi_read_text(rsi, rsi_lbl) + ". " + ema_cross_read(cross_state) + ".",
            "Still worth confirming with a retest/hold before sizing in -- this alone isn't full confirmation.",
        ]
        msg = " ".join(lines)
        print(msg)
        send_telegram(msg)
        state["last_zone"] = zone
        save_json(state_file, state)

    elif zone is None and last_zone is not None:
        # Left an approach zone without a real touch -- not alert-worthy, just reset.
        state["last_zone"] = None
        print(f"[{label}] Price {price} back in mid-range (no touch had occurred), alert state reset.")
    else:
        print(f"[{label}] Price {price}, no alert (zone={zone}, last_zone={last_zone}).")

    # Unconditional save -- covers VA/POC/intraday state changes even on paths
    # above that don't already save (e.g. the final 'no alert' branch).
    save_json(state_file, state)

def send_test_alert():
    msg = "TEST ALERT: this is a manual connectivity check from the futures-alert-monitor pipeline. If you're seeing this, Telegram delivery is working correctly."
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
