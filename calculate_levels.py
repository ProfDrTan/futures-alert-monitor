"""
Daily job: recalculates MES support/resistance using swing points + volume profile.
Run once per day before market open. Writes levels.json for the 15-min checker to read.
"""
import urllib.request
import json
import statistics

SYMBOL = "MES=F"
LOOKBACK_RANGE = "3mo"
LEVELS_FILE = "levels.json"
NUM_VOLUME_BINS = 40
SWING_WINDOW = 3  # bars on each side to confirm a swing high/low

def fetch_daily_bars():
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{SYMBOL}?interval=1d&range={LOOKBACK_RANGE}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    result = data["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    bars = []
    for i in range(len(result["timestamp"])):
        if quote["close"][i] is None:
            continue
        bars.append({
            "high": quote["high"][i],
            "low": quote["low"][i],
            "close": quote["close"][i],
            "volume": quote["volume"][i] or 0,
        })
    return bars

def find_swing_points(bars):
    swing_highs, swing_lows = [], []
    for i in range(SWING_WINDOW, len(bars) - SWING_WINDOW):
        window = bars[i - SWING_WINDOW:i + SWING_WINDOW + 1]
        if bars[i]["high"] == max(b["high"] for b in window):
            swing_highs.append(bars[i]["high"])
        if bars[i]["low"] == min(b["low"] for b in window):
            swing_lows.append(bars[i]["low"])
    return swing_highs, swing_lows

def build_volume_profile(bars):
    all_prices = [b["close"] for b in bars]
    lo, hi = min(all_prices), max(all_prices)
    bin_size = (hi - lo) / NUM_VOLUME_BINS if hi > lo else 1
    bins = {}
    for b in bars:
        idx = int((b["close"] - lo) / bin_size) if bin_size else 0
        idx = min(idx, NUM_VOLUME_BINS - 1)
        bins[idx] = bins.get(idx, 0) + b["volume"]
    # convert bin index back to a representative price (bin midpoint)
    bin_prices = {idx: lo + (idx + 0.5) * bin_size for idx in bins}
    return bins, bin_prices

def cluster(points, tolerance=15.0):
    if not points:
        return []
    points = sorted(points)
    clusters = [[points[0]]]
    for p in points[1:]:
        if p - clusters[-1][-1] <= tolerance:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return [statistics.mean(c) for c in clusters]

def nearest_high_volume_price(target, bins, bin_prices, top_n=8):
    top_bins = sorted(bins.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    top_prices = [bin_prices[idx] for idx, _ in top_bins]
    return min(top_prices, key=lambda p: abs(p - target)) if top_prices else target

def main():
    bars = fetch_daily_bars()
    current_price = bars[-1]["close"]
    swing_highs, swing_lows = find_swing_points(bars)
    resistance_clusters = cluster(swing_highs)
    support_clusters = cluster(swing_lows)
    volume_bins, bin_prices = build_volume_profile(bars)

    # confirmed levels = swing cluster that also sits near a high-volume node
    resistance_candidates = [r for r in resistance_clusters if r > current_price]
    support_candidates = [s for s in support_clusters if s < current_price]

    resistance = min(resistance_candidates, default=None,
                      key=lambda r: abs(r - current_price)) if resistance_candidates else None
    support = max(support_candidates, default=None,
                   key=lambda s: -abs(s - current_price)) if support_candidates else None

    if resistance:
        resistance = round(nearest_high_volume_price(resistance, volume_bins, bin_prices), 2)
    if support:
        support = round(nearest_high_volume_price(support, volume_bins, bin_prices), 2)

    levels = {
        "symbol": "MES",
        "current_price_at_calc": round(current_price, 2),
        "support": support,
        "resistance": resistance,
        "note": "Auto-calculated from 3mo swing points confirmed against volume profile. Edit by hand if needed.",
    }
    with open(LEVELS_FILE, "w") as f:
        json.dump(levels, f, indent=2)
    print(json.dumps(levels, indent=2))

if __name__ == "__main__":
    main()
