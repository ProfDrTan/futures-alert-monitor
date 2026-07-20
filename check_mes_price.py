import urllib.request
import json
import os
import sys
import statistics

LEVELS_FILE = "levels.json"
INDICATORS_FILE = "indicators.json"
STATE_FILE = "last_alert_state.json"

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

def get_mes_price():
    url = "https://query1.finance.yahoo.com/v8/finance/chart/MES=F?interval=1m&range=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    result = data["chart"]["result"][0]
    price = result["meta"]["regularMarketPrice"]
    quote = result["indicators"]["quote"][0]
    volumes = [v for v in quote.get("volume", []) if v]
    today_volume = sum(volumes) if volumes else 0
    return price, today_volume

def get_avg_daily_volume():
    url = "https://query1.finance.yahoo.com/v8/finance/chart/MES=F?interval=1d&range=1mo"
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

def main():
    levels = load_json(LEVELS_FILE)
    if not levels:
        print("No levels.json yet, skipping.")
        sys.exit(0)
    indicators = load_json(INDICATORS_FILE, default={})

    support = levels.get("support")
    resistance = levels.get("resistance")

    try:
        price, today_volume = get_mes_price()
    except Exception as e:
        print(f"Failed to fetch price: {e}")
        sys.exit(0)

    avg_volume = None
    try:
        avg_volume = get_avg_daily_volume()
    except Exception as e:
        print(f"Could not fetch avg volume: {e}")

    vol_ratio = round(today_volume / avg_volume, 2) if avg_volume and today_volume else None

    zone = None
    if support is not None and price <= support:
        zone = "support"
    elif resistance is not None and price >= resistance:
        zone = "resistance"

    state = load_json(STATE_FILE, default={"last_zone": None, "touch_count": {"support": 0, "resistance": 0}})
    if "touch_count" not in state:
        state["touch_count"] = {"support": 0, "resistance": 0}

    if zone and zone != state.get("last_zone"):
        state["touch_count"][zone] = state["touch_count"].get(zone, 0) + 1
        touch_n = state["touch_count"][zone]
        touch_desc = "1st touch" if touch_n == 1 else ("2nd retest" if touch_n == 2 else f"{touch_n}th retest")

        level_val = support if zone == "support" else resistance
        other_level = resistance if zone == "support" else support
        distance_to_other = round(abs(other_level - price), 2) if other_level else None

        rsi = indicators.get("rsi14")
        rsi_lbl = indicators.get("rsi_read")
        cross_state = indicators.get("ema_cross_state")
        pattern = indicators.get("last_candle_pattern")

        lines = [
            f"MES ALERT: price {price} hit {zone.upper()} at {level_val} ({touch_desc}).",
            volume_read(vol_ratio).capitalize() + ".",
            rsi_read_text(rsi, rsi_lbl) + ". " + ema_cross_read(cross_state) + ".",
        ]
        if pattern:
            lines.append(f"Last daily candle: {pattern.replace('_', ' ')}.")
        if distance_to_other:
            lines.append(f"{distance_to_other}pts to the opposite level ({other_level}).")
        if touch_n >= 2:
            lines.append("Repeated test of this level -- worth a closer look now.")

        msg = " ".join(lines)
        print(msg)
        send_telegram(msg)
        state["last_zone"] = zone
        save_json(STATE_FILE, state)
    elif zone is None and state.get("last_zone") is not None:
        state["last_zone"] = None
        save_json(STATE_FILE, state)
        print(f"Price {price} back in mid-range, alert state reset.")
    else:
        print(f"Price {price}, no alert (zone={zone}, last_zone={state.get('last_zone')}).")

if __name__ == "__main__":
    main()
