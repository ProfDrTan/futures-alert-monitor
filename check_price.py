import urllib.request
import json
import os
import sys
import statistics

SYMBOLS = {"ES": "ES=F", "NQ": "NQ=F"}

# Distance (in points) from a level that counts as "approaching" it —
# gives an early heads-up before an exact touch, since the checker
# itself only runs every 5-60 min depending on GitHub's scheduler load.
APPROACH_DISTANCE = {"ES": 10.0, "NQ": 40.0}

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

def volume_read(ratio):
    if ratio is None:
        return "volume data unavailable"
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
    """Returns one of: 'support', 'approaching_support', 'resistance',
    'approaching_resistance', or None. Exact touch takes priority over approach."""
    if support is not None and price <= support:
        return "support"
    if resistance is not None and price >= resistance:
        return "resistance"
    if support is not None and price <= support + approach_dist:
        return "approaching_support"
    if resistance is not None and price >= resistance - approach_dist:
        return "approaching_resistance"
    return None

def check_symbol(label, yahoo_symbol):
    levels_file = f"levels_{label}.json"
    indicators_file = f"indicators_{label}.json"
    state_file = f"last_alert_state_{label}.json"
    approach_dist = APPROACH_DISTANCE.get(label, 10.0)

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

    is_exact_touch = zone in ("support", "resistance")
    is_approach = zone in ("approaching_support", "approaching_resistance")

    if zone and zone != state.get("last_zone"):
        rsi = indicators.get("rsi14")
        rsi_lbl = indicators.get("rsi_read")
        cross_state = indicators.get("ema_cross_state")
        pattern = indicators.get("last_candle_pattern")

        if is_exact_touch:
            state["touch_count"][zone] = state["touch_count"].get(zone, 0) + 1
            touch_n = state["touch_count"][zone]
            touch_desc = "1st touch" if touch_n == 1 else ("2nd retest" if touch_n == 2 else f"{touch_n}th retest")

            level_val = support if zone == "support" else resistance
            other_level = resistance if zone == "support" else support
            distance_to_other = round(abs(other_level - price), 2) if other_level else None

            lines = [
                f"{label} ALERT: price {price} hit {zone.upper()} at {level_val} ({touch_desc}).",
                volume_read(vol_ratio).capitalize() + ".",
                rsi_read_text(rsi, rsi_lbl) + ". " + ema_cross_read(cross_state) + ".",
            ]
            if pattern:
                lines.append(f"Last daily candle: {pattern.replace('_', ' ')}.")
            if distance_to_other:
                lines.append(f"{distance_to_other}pts to the opposite level ({other_level}).")
            if touch_n >= 2:
                lines.append("Repeated test of this level -- worth a closer look now.")
        else:
            # approaching_support / approaching_resistance -- early heads-up
            base = zone.replace("approaching_", "")
            level_val = support if base == "support" else resistance
            distance = round(abs(price - level_val), 2)
            lines = [
                f"{label} HEADS-UP: price {price} is {distance}pts from {base.upper()} ({level_val}).",
                volume_read(vol_ratio).capitalize() + ".",
                rsi_read_text(rsi, rsi_lbl) + ". " + ema_cross_read(cross_state) + ".",
            ]
            if pattern:
                lines.append(f"Last daily candle: {pattern.replace('_', ' ')}.")

        msg = " ".join(lines)
        print(msg)
        send_telegram(msg)
        state["last_zone"] = zone
        save_json(state_file, state)
    elif zone is None and state.get("last_zone") is not None:
        state["last_zone"] = None
        save_json(state_file, state)
        print(f"[{label}] Price {price} back in mid-range, alert state reset.")
    else:
        print(f"[{label}] Price {price}, no alert (zone={zone}, last_zone={state.get('last_zone')}).")


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
