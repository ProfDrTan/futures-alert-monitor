"""
bonds_gold.py
=============
Builds docs/bonds-gold/data.json for the Market Journal "Bonds -> Gold" page.

Sources (all free, end-of-day):
  - FRED (Federal Reserve Bank of St. Louis) CSV downloads, no API key:
      DGS2      2-year Treasury constant-maturity yield, %
      DGS10     10-year Treasury constant-maturity yield, %
      DFII10    10-year TIPS yield = real yield, %
      T10YIE    10-year breakeven inflation, %
      DTWEXBGS  Nominal broad U.S. dollar index (trade-weighted)
      THREEFYTP10  10-year term premium (Kim-Wright model, Fed Board), %
  - Yahoo Finance chart API (via yahoo_session.py): GC=F gold futures daily close
  - TreasuryDirect auction results API: recent 10-year and 30-year auctions

FRED daily yields are published with about a one-business-day lag, so this
page is macro context, not trade timing.
"""
import csv, io, json, math, os, sys, urllib.request, datetime as dt
from yahoo_session import yahoo_json

OUT = "docs/bonds-gold/data.json"
UA = {"User-Agent": "Mozilla/5.0 (market-journal bonds-gold bot)"}
START = (dt.date.today() - dt.timedelta(days=3 * 366)).isoformat()
FRED = {
    "DGS2": "2-year Treasury yield",
    "DGS10": "10-year Treasury yield",
    "DFII10": "10-year real yield (TIPS)",
    "T10YIE": "10-year breakeven inflation",
    "DTWEXBGS": "Broad U.S. dollar index",
    "THREEFYTP10": "10-year term premium",
}
log = []


def get(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def fred(series):
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={START}"
    rows = list(csv.reader(io.StringIO(get(url))))
    out = []
    for r in rows[1:]:
        if len(r) < 2 or r[1] in ("", "."):
            continue
        try:
            out.append([r[0], round(float(r[1]), 4)])
        except ValueError:
            pass
    return out


def gold():
    j = yahoo_json("https://query2.finance.yahoo.com/v8/finance/chart/GC%3DF?range=3y&interval=1d")
    res = j["chart"]["result"][0]
    ts, cl = res["timestamp"], res["indicators"]["quote"][0]["close"]
    out = {}
    for t, c in zip(ts, cl):
        if c is None:
            continue
        out[dt.datetime.utcfromtimestamp(t).date().isoformat()] = round(c, 2)
    return sorted(out.items())


def auctions():
    out = []
    for term, label in (("10-Year", "10-year note"), ("30-Year", "30-year bond")):
        typ = "Note" if term == "10-Year" else "Bond"
        rows = None
        for url in (f"https://www.treasurydirect.gov/TA_WS/securities/auctioned?format=json&type={typ}&days=200",
                    f"https://www.treasurydirect.gov/TA_WS/securities/search?type={typ}&format=json&dateFieldName=auctionDate&startDate={(dt.date.today()-dt.timedelta(days=200)).isoformat()}"):
            try:
                rows = json.loads(get(url)); log.append(f"auction url ok: {url} ({len(rows)} rows)"); break
            except Exception as e:
                log.append(f"auction url failed: {url}: {e}")
        for a in rows or []:
            if a.get("securityTerm") != term or not a.get("highYield"):
                continue
            tot = float(a.get("competitiveAccepted") or 0) or None
            ind = float(a.get("indirectBidderAccepted") or 0)
            out.append({
                "date": a["auctionDate"][:10], "security": label, "reopening": a.get("reopening") == "Yes",
                "high_yield": float(a["highYield"]), "bid_to_cover": float(a["bidToCoverRatio"]) if a.get("bidToCoverRatio") else None,
                "indirect_pct": round(ind / tot * 100, 1) if tot else None,
            })
    out.sort(key=lambda x: x["date"], reverse=True)
    return out[:8]


def change(series, days):
    """Change from the last value on or before (latest date - days) to the latest value."""
    if not series:
        return None
    last_d = dt.date.fromisoformat(series[-1][0]); target = last_d - dt.timedelta(days=days)
    past = [v for d, v in series if dt.date.fromisoformat(d) <= target]
    return None if not past else series[-1][1] - past[-1]


def pct_change(series, days):
    c = change(series, days)
    if c is None:
        return None
    base = series[-1][1] - c
    return c / base * 100 if base else None


def corr_of_changes(a, b, window=60):
    """Pearson correlation of daily changes over the last `window` common dates."""
    da, db = dict(a), dict(b)
    common = sorted(set(da) & set(db))
    if len(common) < window + 1:
        return None
    common = common[-(window + 1):]
    xa = [da[common[i]] - da[common[i - 1]] for i in range(1, len(common))]
    xb = [db[common[i]] - db[common[i - 1]] for i in range(1, len(common))]
    ma, mb = sum(xa) / len(xa), sum(xb) / len(xb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(xa, xb))
    va = math.sqrt(sum((x - ma) ** 2 for x in xa)); vb = math.sqrt(sum((y - mb) ** 2 for y in xb))
    return round(cov / (va * vb), 2) if va and vb else None


def verdict(value, up_is, threshold):
    if value is None:
        return "n/a"
    if abs(value) < threshold:
        return "neutral"
    return up_is if value > 0 else ("headwind" if up_is == "supportive" else "supportive")


def main():
    data = {"generated_utc": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M"), "series": {}, "drivers": [], "errors": []}
    for s, name in FRED.items():
        try:
            data["series"][s] = fred(s)
            log.append(f"{s}: {len(data['series'][s])} rows, last {data['series'][s][-1]}")
        except Exception as e:
            data["errors"].append(f"{s}: {e}"); log.append(f"{s}: ERROR {e}")
    try:
        data["series"]["GOLD"] = gold(); log.append(f"GOLD: {len(data['series']['GOLD'])} rows, last {data['series']['GOLD'][-1]}")
    except Exception as e:
        data["errors"].append(f"GOLD: {e}"); log.append(f"GOLD: ERROR {e}")
    S = data["series"]
    if "DGS10" in S and "DGS2" in S:
        d2 = dict(S["DGS2"]); S["CURVE"] = [[d, round(v - d2[d], 4)] for d, v in S["DGS10"] if d in d2]

    def drv(key, name, unit, up_is, thr, kind="level", note=""):
        s = S.get(key)
        if not s:
            return
        m = pct_change(s, 30) if kind == "pct" else change(s, 30)
        data["drivers"].append({
            "key": key, "name": name, "unit": unit, "date": s[-1][0], "value": s[-1][1],
            "d1": change(s, 1) if kind == "level" else pct_change(s, 1),
            "w1": change(s, 7) if kind == "level" else pct_change(s, 7),
            "m1": m, "kind": kind, "verdict": verdict(m, up_is, thr) if up_is else "context", "note": note,
        })

    drv("DFII10", "10-year real yield", "%", "headwind", 0.10, note="Rising real yields raise the cost of holding gold, which pays no interest.")
    drv("DTWEXBGS", "Broad U.S. dollar index", "", "headwind", 0.5, kind="pct", note="Gold is priced in dollars; a stronger dollar usually weighs on it.")
    drv("T10YIE", "10-year breakeven inflation", "%", "supportive", 0.05, note="Higher expected inflation lifts demand for gold as an inflation hedge.")
    drv("THREEFYTP10", "10-year term premium", "%", "supportive", 0.10, note="A rising term premium signals worry about U.S. debt supply, the fiscal-risk case for gold.")
    drv("DGS10", "10-year Treasury yield", "%", None, 0, note="Nominal yield = real yield + breakeven inflation; read it through those two.")
    drv("DGS2", "2-year Treasury yield", "%", None, 0, note="Tracks where the market expects the Fed's policy rate to go.")
    drv("CURVE", "10-year minus 2-year (curve)", "pp", None, 0, note="Steepening led by the long end often reflects fiscal or inflation worry.")
    data["corr_gold_real_60d"] = corr_of_changes(S.get("GOLD", []), S.get("DFII10", []), 60)
    data["corr_gold_real_250d"] = corr_of_changes(S.get("GOLD", []), S.get("DFII10", []), 250)
    for k in ("GOLD",):
        if S.get(k):
            data["gold"] = {"date": S[k][-1][0], "value": S[k][-1][1], "w1": pct_change(S[k], 7), "m1": pct_change(S[k], 30)}
    try:
        data["auctions"] = auctions(); log.append(f"auctions: {len(data['auctions'])}")
    except Exception as e:
        data["auctions"] = []; data["errors"].append(f"auctions: {e}"); log.append(f"auctions: ERROR {e}")
    # keep charts light: last ~2 years
    cut = (dt.date.today() - dt.timedelta(days=740)).isoformat()
    for k in S:
        S[k] = [p for p in S[k] if p[0] >= cut]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(data, open(OUT, "w"), separators=(",", ":"))
    print("\n".join(log)); print("corr60", data["corr_gold_real_60d"], "corr250", data["corr_gold_real_250d"]); print("errors:", data["errors"])
    if len(data["errors"]) > 3:
        sys.exit(1)


if __name__ == "__main__":
    main()
