"""Cluster-buy test.

Question: if you bought every cluster buy (2+ different insiders buying the same stock
within WINDOW days) where the price hadn't moved more than MOVE% since the insiders
bought, and held for HOLD trading days, how many trades were profitable?

Timing (no look-ahead):
  - The signal is the filing that makes it a cluster (the 2nd distinct insider).
  - Filed before ~9:30pm ET -> decided that evening; later -> next evening.
  - "Move" = close on the decision evening vs close on the cluster's first trade date
    (both from the same split/dividend-adjusted series, so the adjustments cancel).
  - Buy at the next session's open, sell at the close HOLD sessions later. No stop.

Comparison groups (same entry/exit and costs):
  - clusters that moved more than +MOVE% / less than -MOVE% before you could buy
  - fresh single-insider buys with |move| <= MOVE%
  - control: stock-days with no insider buy in the prior 30 days and |3-day move| <= MOVE%

Outputs go to results/cluster/.
"""
import argparse
import json
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import bt  # noqa: E402
import config as C  # noqa: E402

HOLDS = [5, 10, 20, 40, 60]
CONTROL_LOOKBACK = 3          # sessions used to measure the control's "move"
MIN_PRICE, MIN_DVOL = 1.0, 1e6  # "tradeable" universe: $1+ stock, $1M+/day average volume


def norm_name(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", str(s).lower())).strip()


def load_prices():
    px = {}
    for f in sorted((C.DATA / "prices").glob("*.parquet")):
        df = pd.read_parquet(f).sort_index()
        if len(df) < 30:
            continue
        df["dvol20"] = (df["Close"] * df["Volume"]).rolling(20).mean()
        px[f.stem] = df
    return px


def prep_insiders(ins, max_lag_days=60):
    ins = ins.dropna(subset=["trade_date", "filing_dt", "insider", "ticker"]).copy()
    lag = (ins["filing_dt"].dt.normalize() - ins["trade_date"]).dt.days
    ins = ins[lag.between(0, max_lag_days)]          # drop stale / mis-dated filings
    ins["who"] = ins["insider"].map(norm_name)
    # Joint filings (e.g. a fund and its general partner) report the same shares on one
    # day at one price; count those once so one buyer can't look like a cluster.
    ins = ins.sort_values("filing_dt").drop_duplicates(["ticker", "trade_date", "qty", "price"])
    return ins


def find_clusters(ins, window_days, cooldown_days=30):
    """One signal per cluster: the filing that brings >= 2 different insiders whose trade
    dates are within `window_days` of each other. Buys within `cooldown_days` after a
    signal don't create another."""
    win = pd.Timedelta(days=window_days)
    cool = pd.Timedelta(days=cooldown_days)
    out = []
    for t, g in ins.sort_values(["ticker", "filing_dt"]).groupby("ticker", sort=False):
        recs = list(g[["filing_dt", "trade_date", "who", "value", "title"]].itertuples(index=False))
        last = None
        for i, r in enumerate(recs):
            if last is not None and r.filing_dt - last < cool:
                continue
            sel = [p for p in recs[:i + 1] if abs(p.trade_date - r.trade_date) <= win
                   and (last is None or p.filing_dt - last >= cool)]
            names = {p.who for p in sel}
            if len(names) >= 2:
                out.append({
                    "ticker": t, "filing_dt": r.filing_dt,
                    "first_trade": min(p.trade_date for p in sel),
                    "n_insiders": len(names), "value": float(np.nansum([p.value for p in sel])),
                    "titles": "; ".join(sorted({str(p.title) for p in sel}))[:120],
                })
                last = r.filing_dt
    return pd.DataFrame(out)


def fresh_singles(ins, clusters, lookback_days=30):
    """Single-insider buys with no other purchase of that stock in the prior 30 days and
    no cluster signal for it within 30 days either side."""
    g = ins.sort_values("filing_dt").copy()
    g["day"] = g["filing_dt"].dt.normalize()
    agg = g.groupby(["ticker", "day"]).agg(filing_dt=("filing_dt", "max"), first_trade=("trade_date", "min"),
                                           n_insiders=("who", "nunique"), value=("value", "sum"),
                                           titles=("title", lambda s: "; ".join(sorted(set(map(str, s))))[:120]))
    agg = agg.reset_index()
    agg = agg[agg["n_insiders"] == 1]
    look = np.timedelta64(lookback_days, "D")
    times = g.groupby("ticker")["filing_dt"].apply(lambda s: np.sort(s.values)).to_dict()
    ctimes = clusters.groupby("ticker")["filing_dt"].apply(lambda s: np.sort(s.values)).to_dict() if len(clusters) else {}
    keep = []
    for t, d, f in zip(agg["ticker"], agg["day"].values, agg["filing_dt"].values):
        arr = times[t]
        prior = np.searchsorted(arr, d, "left") - np.searchsorted(arr, d - look, "left")
        c = ctimes.get(t)
        near_cluster = c is not None and (np.searchsorted(c, f + look, "right") - np.searchsorted(c, f - look, "left")) > 0
        keep.append(prior == 0 and not near_cluster)
    return agg[np.array(keep, dtype=bool)].drop(columns="day")


def measure(sig, px, spy, cost=C.COST_PER_SIDE):
    """Entry/exit returns for each signal at every hold length."""
    cal = spy.index.values
    fd = sig["filing_dt"]
    hours = fd.dt.hour + fd.dt.minute / 60
    run = (fd.dt.normalize() + pd.to_timedelta((hours >= C.RUN_CUTOFF_HOUR).astype(int), unit="D")).values
    pos = np.searchsorted(cal, run, side="right")
    rows = []
    for i, r in enumerate(sig.itertuples(index=False)):
        df = px.get(r.ticker)
        if df is None or pos[i] <= 0 or pos[i] >= len(cal):
            continue
        entry, signal = cal[pos[i]], cal[pos[i] - 1]
        idx = df.index
        si, ei = idx.searchsorted(signal), idx.searchsorted(entry)
        if si >= len(idx) or ei >= len(idx) or idx[si] != signal or idx[ei] != entry or ei != si + 1:
            continue
        a = idx.searchsorted(r.first_trade, side="right") - 1
        if a < 0 or a > si:
            continue
        close, opn = df["Close"].values, df["Open"].values
        rec = {"ticker": r.ticker, "filing_dt": r.filing_dt, "first_trade": r.first_trade,
               "signal_date": idx[si], "entry_date": idx[ei], "n_insiders": r.n_insiders,
               "value": r.value, "titles": r.titles,
               "move": close[si] / close[a] - 1, "lag_sessions": int(si - a),
               "price": close[si], "dvol20": df["dvol20"].values[si], "entry_open": opn[ei]}
        for h in HOLDS:
            j = ei + h - 1
            if j < len(idx) and (idx[j] - idx[ei]).days <= h * 2 + 10:   # skip gaps in the data
                rec[f"ret_{h}"] = close[j] / opn[ei] - 1 - 2 * cost
            else:
                rec[f"ret_{h}"] = np.nan
        rows.append(rec)
    out = pd.DataFrame(rows)
    if len(out):
        for h in HOLDS:
            out[f"spy_{h}"] = bt.spy_window_ret(spy, out["entry_date"], h)
    return out


def control(px, ins, spy, hold, move_max, cost=C.COST_PER_SIDE):
    """No insider buy in the prior 30 days, |move over the last 3 sessions| <= move_max.
    Non-overlapping windows per ticker so one stock's run isn't counted many times."""
    look = np.timedelta64(30, "D")
    times = ins.groupby("ticker")["filing_dt"].apply(lambda s: np.sort(s.dt.normalize().values)).to_dict()
    out = []
    for t, df in px.items():
        if t == "SPY" or len(df) < hold + 30:
            continue
        n = len(df)
        i = np.arange(CONTROL_LOOKBACK, n - hold)
        i = i[i % hold == 0]
        close, opn = df["Close"].values, df["Open"].values
        mv = close[i] / close[i - CONTROL_LOOKBACK] - 1
        ok = np.abs(mv) <= move_max
        i, mv = i[ok], mv[ok]
        if not len(i):
            continue
        arr = times.get(t)
        if arr is not None and len(arr):
            sd = df.index.values[i]
            hit = np.searchsorted(arr, sd + np.timedelta64(1, "D"), "left") - np.searchsorted(arr, sd - look, "left")
            i, mv = i[hit == 0], mv[hit == 0]
        if not len(i):
            continue
        out.append(pd.DataFrame({"ticker": t, "entry_date": df.index.values[i + 1], "move": mv,
                                 "price": close[i], "dvol20": df["dvol20"].values[i],
                                 f"ret_{hold}": close[i + hold] / opn[i + 1] - 1 - 2 * cost}))
    res = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    if len(res):
        res[f"spy_{hold}"] = bt.spy_window_ret(spy, res["entry_date"], hold)
    return res


def summarize(df, hold, name):
    r = df[f"ret_{hold}"]
    ok = r.notna()
    r, s = r[ok], df.loc[ok, f"spy_{hold}"]
    ex = (r - s).dropna()
    n = len(r)
    if n == 0:
        return {"group": name, "hold": hold, "n": 0}
    se = ex.std(ddof=1) / np.sqrt(len(ex)) if len(ex) > 1 else np.nan
    return {
        "group": name, "hold": hold, "n": n,
        "profitable": int((r > 0).sum()), "win_rate": float((r > 0).mean()),
        "beat_spy_rate": float((ex > 0).mean()) if len(ex) else np.nan,
        "mean": float(r.mean()), "median": float(r.median()),
        "mean_minus_spy": float(ex.mean()), "median_minus_spy": float(ex.median()),
        "t_minus_spy": float(ex.mean() / se) if se and se > 0 else np.nan,
        "ci_low": float(ex.mean() - 1.96 * se) if se == se else np.nan,
        "ci_high": float(ex.mean() + 1.96 * se) if se == se else np.nan,
        "avg_win": float(r[r > 0].mean()) if (r > 0).any() else np.nan,
        "avg_loss": float(r[r <= 0].mean()) if (r <= 0).any() else np.nan,
    }


def pct(x):
    return "" if x is None or not np.isfinite(x) else f"{x * 100:+.2f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, default=20)
    ap.add_argument("--move", type=float, default=3.0, help="max price move in % since insiders bought")
    ap.add_argument("--window", type=int, default=14, help="days within which 2+ insiders must buy")
    a = ap.parse_args()
    if a.hold not in HOLDS:
        HOLDS.append(a.hold)
        HOLDS.sort()
    mv = a.move / 100
    out_dir = C.RESULTS / "cluster"
    out_dir.mkdir(parents=True, exist_ok=True)

    ins = pd.read_parquet(C.DATA / "insiders.parquet")
    ins = ins[ins["filing_dt"] >= pd.Timestamp(C.START)]
    px = load_prices()
    spy = px["SPY"]
    print(f"{len(ins)} purchases, prices for {len(px)} tickers")

    clean = prep_insiders(ins)
    clusters = find_clusters(clean, a.window)
    singles = fresh_singles(clean, clusters)
    print(f"{len(clusters)} cluster signals, {len(singles)} fresh single-insider signals")

    cm = measure(clusters, px, spy)
    sm = measure(singles, px, spy)
    print(f"with prices: {len(cm)} clusters ({len(cm) / max(len(clusters), 1):.0%}), {len(sm)} singles")
    for d in (cm, sm):
        d["tradeable"] = (d["price"] >= MIN_PRICE) & (d["dvol20"] >= MIN_DVOL)
        d["period"] = np.where(d["entry_date"] <= pd.Timestamp(C.TRAIN_END), "2015-2021", "2022-now")
    cm["bucket"] = np.select([cm["move"].abs() <= mv, cm["move"] > mv], ["flat", "up"], "down")

    rows, hold_rows, year_rows = [], [], []
    for universe in ("all", "tradeable"):
        u = (lambda d: d) if universe == "all" else (lambda d: d[d["tradeable"]])
        c, s = u(cm), u(sm)
        ctrl = control(px, clean, spy, a.hold, mv)
        if universe == "tradeable" and len(ctrl):
            ctrl = ctrl[(ctrl["price"] >= MIN_PRICE) & (ctrl["dvol20"] >= MIN_DVOL)]
        flat = c[c["bucket"] == "flat"]
        groups = [
            (f"Cluster buy, price within ±{a.move:g}% (your test)", flat),
            (f"Cluster buy, price already up >{a.move:g}%", c[c["bucket"] == "up"]),
            (f"Cluster buy, price already down >{a.move:g}%", c[c["bucket"] == "down"]),
            ("Every cluster buy", c),
            (f"Single insider buy, price within ±{a.move:g}%", s[s["move"].abs() <= mv]),
            (f"No insider buy, price within ±{a.move:g}% (control)", ctrl),
        ]
        for name, d in groups:
            rows.append({"universe": universe, "period": "all", **summarize(d, a.hold, name)})
        for per in ("2015-2021", "2022-now"):
            for name, d in groups[:1] + groups[4:5]:
                rows.append({"universe": universe, "period": per, **summarize(d[d["period"] == per], a.hold, name)})
            cp = ctrl[ctrl["entry_date"] <= pd.Timestamp(C.TRAIN_END)] if per == "2015-2021" \
                else ctrl[ctrl["entry_date"] > pd.Timestamp(C.TRAIN_END)]
            rows.append({"universe": universe, "period": per, **summarize(cp, a.hold, groups[5][0])})
        top = flat[f"ret_{a.hold}"].nlargest(10).index
        rows.append({"universe": universe, "period": "all",
                     **summarize(flat.drop(top), a.hold, "Your test, without the 10 best trades")})
        for h in HOLDS:
            hold_rows.append({"universe": universe, **summarize(flat, h, "cluster_flat")})
            hc = control(px, clean, spy, h, mv)
            if universe == "tradeable" and len(hc):
                hc = hc[(hc["price"] >= MIN_PRICE) & (hc["dvol20"] >= MIN_DVOL)]
            hold_rows.append({"universe": universe, **summarize(hc, h, "control")})
        for y, d in flat.groupby(flat["entry_date"].dt.year):
            year_rows.append({"universe": universe, "year": int(y), **summarize(d, a.hold, "cluster_flat")})

    summ = pd.DataFrame(rows)
    holds = pd.DataFrame(hold_rows)
    years = pd.DataFrame(year_rows)
    summ.to_csv(out_dir / "summary.csv", index=False)
    holds.to_csv(out_dir / "by_hold.csv", index=False)
    years.to_csv(out_dir / "by_year.csv", index=False)
    cols = ["ticker", "filing_dt", "first_trade", "signal_date", "entry_date", "n_insiders", "value", "titles",
            "move", "lag_sessions", "price", "dvol20", "tradeable", "bucket", "period"] + \
           [f"ret_{h}" for h in HOLDS] + [f"spy_{h}" for h in HOLDS]
    cm[cols].to_csv(out_dir / "cluster_trades.csv", index=False)
    meta = {"hold": a.hold, "move_pct": a.move, "window_days": a.window,
            "clusters_found": int(len(clusters)), "clusters_with_prices": int(len(cm)),
            "flat_clusters": int((cm["bucket"] == "flat").sum()),
            "flat_tradeable": int(((cm["bucket"] == "flat") & cm["tradeable"]).sum()),
            "median_lag_sessions": float(cm["lag_sessions"].median()),
            "cost_per_side": C.COST_PER_SIDE}
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    lines = [f"# Cluster-buy test: hold {a.hold} sessions, price within ±{a.move:g}%\n",
             f"- {meta['clusters_found']:,} cluster signals (2+ insiders within {a.window} days); "
             f"{meta['clusters_with_prices']:,} with prices; {meta['flat_clusters']:,} within ±{a.move:g}% "
             f"({meta['flat_tradeable']:,} tradeable: ${MIN_PRICE:g}+ price, ${MIN_DVOL / 1e6:g}M+/day volume)",
             f"- Median {meta['median_lag_sessions']:.0f} sessions between the first insider trade and when you could buy\n"]
    for universe in ("all", "tradeable"):
        lines.append(f"\n## {universe}\n\n| group | period | trades | profitable | win rate | beat SPY | avg | median | avg minus SPY | t |\n|---|---|---|---|---|---|---|---|---|---|")
        for r in summ[summ["universe"] == universe].to_dict("records"):
            if not r.get("n"):
                continue
            lines.append(f"| {r['group']} | {r['period']} | {r['n']:,} | {r['profitable']:,} | {r['win_rate']:.1%} | "
                         f"{r['beat_spy_rate']:.1%} | {pct(r['mean'])} | {pct(r['median'])} | {pct(r['mean_minus_spy'])} | {r['t_minus_spy']:.2f} |")
    lines.append("\n## By hold length (avg minus SPY)\n")
    lines.append(holds.pivot_table(index=["universe", "hold"], columns="group", values="mean_minus_spy").round(4).to_markdown())
    (out_dir / "summary.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
