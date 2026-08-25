"""
Daily job: recalculates ES and NQ support/resistance using swing points + volume profile.
Run once per day before market open. Writes levels_<SYMBOL>.json for the 15-min checker to read.
"""
import urllib.request
import json
import statistics
from yahoo_session import yahoo_json

SYMBOLS = {"ES": "ES=F", "NQ": "NQ=F"}
LOOKBACK_RANGE = "3mo"
NUM_VOLUME_BINS = 40
SWING_WINDOW = 3  # bars on each side to confirm a swing high/low

# Cluster tolerance scales with each instrument's typical point size
CLUSTER_TOLERANCE = {"ES": 15.0, "NQ": 60.0}

def fetch_daily_bars(yahoo_symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}?interval=1d&range={LOOKBACK_RANGE}"
    data = yahoo_json(url)
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

def calc_for_symbol(label, yahoo_symbol):
    bars = fetch_daily_bars(yahoo_symbol)
    current_price = bars[-1]["close"]
    tolerance = CLUSTER_TOLERANCE.get(label, 15.0)
    swing_highs, swing_lows = find_swing_points(bars)
    resistance_clusters = cluster(swing_highs, tolerance)
    support_clusters = cluster(swing_lows, tolerance)
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

    # ---- Point of Control (POC) + Value Area (70% of volume around POC) ----
    total_volume = sum(volume_bins.values())
    poc_price = vah_price = val_price = None
    if total_volume > 0:
        sorted_bins = sorted(volume_bins.items(), key=lambda kv: kv[1], reverse=True)
        poc_idx = sorted_bins[0][0]
        poc_price = round(bin_prices[poc_idx], 2)

        all_idxs = sorted(volume_bins.keys())
        poc_pos = all_idxs.index(poc_idx)
        lo_pos, hi_pos = poc_pos, poc_pos
        cum_vol = volume_bins[poc_idx]
        target = total_volume * 0.70

        while cum_vol < target:
            next_lo_idx = all_idxs[lo_pos - 1] if lo_pos - 1 >= 0 else None
            next_hi_idx = all_idxs[hi_pos + 1] if hi_pos + 1 < len(all_idxs) else None
            vol_lo = volume_bins.get(next_lo_idx, -1) if next_lo_idx is not None else -1
            vol_hi = volume_bins.get(next_hi_idx, -1) if next_hi_idx is not None else -1
            if vol_lo < 0 and vol_hi < 0:
                break
            if vol_lo >= vol_hi:
                cum_vol += vol_lo
                lo_pos -= 1
            else:
                cum_vol += vol_hi
                hi_pos += 1

        val_price = round(bin_prices[all_idxs[lo_pos]], 2)
        vah_price = round(bin_prices[all_idxs[hi_pos]], 2)

    levels = {
        "symbol": label,
        "current_price_at_calc": round(current_price, 2),
        "support": support,
        "resistance": resistance,
        "poc": poc_price,
        "vah": vah_price,
        "val": val_price,
        "note": "Auto-calculated from 3mo swing points confirmed against volume profile. POC/VAH/VAL from the same volume profile (70% value area around point of control). Edit by hand if needed.",
    }
    out_file = f"levels_{label}.json"
    with open(out_file, "w") as f:
        json.dump(levels, f, indent=2)
    print(json.dumps(levels, indent=2))

def main():
    for label, yahoo_symbol in SYMBOLS.items():
        calc_for_symbol(label, yahoo_symbol)

if __name__ == "__main__":
    main()

