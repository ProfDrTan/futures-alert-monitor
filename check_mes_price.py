import urllib.request
import json
import os
import sys
import statistics

LEVELS_FILE = "levels.json"
STATE_FILE = "last_alert_state.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def load_levels():
    with open(LEVELS_FILE) as f:
        return json.load(f)

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

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_zone": None}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def main():
    try:
        levels = load_levels()
    except Exception as e:
        print(f"Failed to load levels.json (has calculate_levels.py run yet?): {e}")
        sys.exit(0)

    support = levels.get("support")
    resistance = levels.get("resistance")

    try:
        price, today_volume = get_mes_price()
    except Exception as e:
        print(f"Failed to fetch price: {e}")
        sys.exit(0)  # don't fail the workflow, just skip this run

    avg_volume = None
    try:
        avg_volume = get_avg_daily_volume()
    except Exception as e:
        print(f"Could not fetch avg volume, continuing without it: {e}")

    vol_note = ""
    if avg_volume and today_volume:
        ratio = round(today_volume / avg_volume, 2)
        vol_note = f" Volume so far today is {ratio}x the 20-day average."

    zone = None
    if support is not None and price <= support:
        zone = "support"
    elif resistance is not None and price >= resistance:
        zone = "resistance"

    state = load_state()

    if zone and zone != state.get("last_zone"):
        level_val = support if zone == "support" else resistance
        msg = (f"MES ALERT: price {price} has hit the {zone.upper()} zone (level: {level_val}).{vol_note} "
               f"Data delayed ~15min (Yahoo Finance free feed). Levels last calculated: "
               f"{levels.get('current_price_at_calc', 'unknown')}.")
        print(msg)
        send_telegram(msg)
        state["last_zone"] = zone
        save_state(state)
    elif zone is None and state.get("last_zone") is not None:
        state["last_zone"] = None
        save_state(state)
        print(f"Price {price} back in mid-range, alert state reset.")
    else:
        print(f"Price {price}, no alert (zone={zone}, last_zone={state.get('last_zone')}).{vol_note}")

if __name__ == "__main__":
    main()
