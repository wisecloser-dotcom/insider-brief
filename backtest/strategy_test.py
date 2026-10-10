"""Alen's cluster-buy trading rules, with a C-suite test.

Signal: a cluster buy (2+ different insiders buying within WINDOW days). Main group:
clusters that include a CEO, CFO or President (title matches CEO|CFO|Pres).

Rules
  Entry      first market open (09:30 ET) after the Form 4 that completes the cluster.
  Skip       if that open is more than MOVE away from the close on the cluster's
             first insider trade date (you can see the open before buying).
  Take profit  if the price reaches +TP from entry within the first 5 trading days,
             sell at +TP (or at the open, if it gaps above).
  Cluster sell if 2+ different insiders file open-market sales within 14 days of each
             other while you hold, sell at the next open after that filing.
  Otherwise  sell at the close of the last trading day within HOLD calendar days.
  No stop-loss. Costs COST_PER_SIDE each way.

Pre-registered pass rules (judged on 2005-2014, which was never used to find the idea):
  1. C-suite clusters beat IWM on average, t > 2.
  2. C-suite clusters beat non-C-suite clusters, t > 2 (Welch).
  3. Rule 1 still holds with t > 2 when trades are averaged per month first.

Outputs: results/strategy/.
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import config as C  # noqa: E402
import cluster_test as CT  # noqa: E402

CSUITE = r"CEO|CFO|Pres"
OPEN = pd.Timedelta(hours=9, minutes=30)
TP_DAYS = 5
SELL_WINDOW = 14
MIN_PRICE, MIN_DVOL = 1.0, 1e6
PERIODS = [("2005-2014 (untouched)", "2005-01-01", "2014-12-31"),
           ("2015-now (where the idea came from)", "2015-01-01", "2100-01-01")]
GRID_TP = [None, 0.05, 0.10, 0.15, 0.20]
GRID_MOVE = [0.03, 0.05, 0.10, None]


# ---------------------------------------------------------------- insider events
def cluster_sell_times(sales):
    """Filing times at which 2+ different insiders have sold within SELL_WINDOW days."""
    s = sales.dropna(subset=["trade_date", "filing_dt", "insider", "ticker"]).copy()
    lag = (s["filing_dt"].dt.normalize() - s["trade_date"]).dt.days
    s = s[lag.between(0, 60)]
    s["who"] = s["insider"].map(CT.norm_name)
    s = s.sort_values("filing_dt").drop_duplicates(["ticker", "trade_date", "qty", "price"])
    win = np.timedelta64(SELL_WINDOW, "D")
    out = {}
    for t, g in s.groupby("ticker", sort=False):
        f = g["filing_dt"].values
        td = g["trade_date"].values
        who = g["who"].values
        ev = []
        lo = 0
        for i in range(len(f)):
            while f[lo] < f[i] - np.timedelta64(75, "D"):
                lo += 1
            names = {who[j] for j in range(lo, i + 1) if abs(td[j] - td[i]) <= win}
            if len(names) >= 2:
                ev.append(f[i])
        if ev:
            out[t] = np.unique(np.array(ev, dtype="datetime64[ns]"))
    return out


def entry_index(dates, when):
    """Index of the first session whose 09:30 open is strictly after `when`."""
    opens = dates + OPEN.to_timedelta64()
    return np.searchsorted(opens, np.asarray(when, dtype="datetime64[ns]"), side="right")


# ---------------------------------------------------------------- simulation
def simulate(dates, opn, high, close, ei, sells, tp, hold_days, cost):
    """One trade. Returns (ret, reason, exit_index, exit_at) or None if not finished yet."""
    end = dates[ei] + np.timedelta64(hold_days, "D")
    if dates[-1] < end:
        return None                                   # still open at the end of the data
    last = int(np.searchsorted(dates, end, side="right") - 1)
    sell_day = None
    if sells is not None and len(sells):
        entry_open = dates[ei] + OPEN.to_timedelta64()
        k = np.searchsorted(sells, entry_open, side="right")
        if k < len(sells):
            j = int(entry_index(dates, sells[k]))
            if j <= last:
                sell_day = j
    px = opn[ei]
    target = px * (1 + tp) if tp else None
    for d in range(ei, last + 1):
        if sell_day is not None and d == sell_day:
            return opn[d] / px - 1 - 2 * cost, "cluster_sell", d, "open"
        if target is not None and d - ei < TP_DAYS:
            if d > ei and opn[d] >= target:
                return opn[d] / px - 1 - 2 * cost, "take_profit", d, "open"
            if high[d] >= target:
                return tp - 2 * cost, "take_profit", d, "close"
    return close[last] / px - 1 - 2 * cost, "held_to_end", last, "close"


def bench_ret(bench, entry_date, exit_date, exit_at):
    try:
        b0 = bench.at[entry_date, "Open"]
        b1 = bench.at[exit_date, "Open" if exit_at == "open" else "Close"]
        return b1 / b0 - 1
    except KeyError:
        return np.nan


def build_candidates(clusters, px):
    """Entry index, move-at-open and liquidity for each cluster signal."""
    rows = []
    for r in clusters.itertuples(index=False):
        df = px.get(r.ticker)
        if df is None:
            continue
        dates = df.index.values
        ei = int(entry_index(dates, r.filing_dt))
        if ei <= 0 or ei >= len(dates):
            continue
        a = int(np.searchsorted(dates, np.datetime64(r.first_trade), side="right") - 1)
        if a < 0 or a >= ei:
            continue
        if (dates[ei] - np.datetime64(r.filing_dt)) > np.timedelta64(7, "D"):
            continue                                  # gap in the price data
        rows.append({"ticker": r.ticker, "filing_dt": r.filing_dt, "first_trade": r.first_trade,
                     "entry_date": pd.Timestamp(dates[ei]), "ei": ei, "titles": r.titles,
                     "value": r.value, "n_insiders": r.n_insiders,
                     "move": df["Open"].values[ei] / df["Close"].values[a] - 1,
                     "price": df["Close"].values[ei - 1], "dvol20": df["dvol20"].values[ei - 1]})
    c = pd.DataFrame(rows)
    if len(c):
        c["csuite"] = c["titles"].str.contains(CSUITE, case=False, na=False)
        c["tradeable"] = (c["price"] >= MIN_PRICE) & (c["dvol20"] >= MIN_DVOL)
    return c


def run_trades(cand, px, sells, bench, tp, move, hold_days, cost):
    out = []
    for r in cand.itertuples(index=False):
        if move is not None and abs(r.move) > move:
            continue
        df = px[r.ticker]
        res = simulate(df.index.values, df["Open"].values, df["High"].values, df["Close"].values,
                       r.ei, sells.get(r.ticker), tp, hold_days, cost)
        if res is None:
            continue
        ret, reason, xi, at = res
        exit_date = df.index[xi]
        out.append({**r._asdict(), "exit_date": exit_date, "exit_reason": reason, "ret": ret,
                    "days_held": int(xi - r.ei + 1),
                    "iwm": bench_ret(bench["IWM"], r.entry_date, exit_date, at),
                    "spy": bench_ret(bench["SPY"], r.entry_date, exit_date, at)})
    t = pd.DataFrame(out)
    if len(t):
        t["vs_iwm"] = t["ret"] - t["iwm"]
        t["vs_spy"] = t["ret"] - t["spy"]
    return t


def control_trades(px, ins, sells, bench, tp, move, hold_days, cost, step=21):
    """Same rules on stocks with no insider purchase filed in the prior 30 days.
    'Move' = entry open vs the close 3 sessions earlier. One window every `step` sessions."""
    look = np.timedelta64(30, "D")
    buys = ins.groupby("ticker")["filing_dt"].apply(lambda s: np.sort(s.values)).to_dict()
    out = []
    for t, df in px.items():
        if t in ("SPY", "IWM") or len(df) < 60:
            continue
        dates, opn, high, close = df.index.values, df["Open"].values, df["High"].values, df["Close"].values
        dv = df["dvol20"].values
        idx = np.arange(4, len(df) - 1, step)
        mv = opn[idx] / close[idx - 3] - 1
        keep = (close[idx - 1] >= MIN_PRICE) & (dv[idx - 1] >= MIN_DVOL)
        if move is not None:
            keep &= np.abs(mv) <= move
        idx, mv = idx[keep], mv[keep]
        b = buys.get(t)
        if b is not None and len(b) and len(idx):
            eo = dates[idx] + OPEN.to_timedelta64()
            hit = np.searchsorted(b, eo, "left") - np.searchsorted(b, eo - look, "left")
            idx, mv = idx[hit == 0], mv[hit == 0]
        for ei, m in zip(idx, mv):
            res = simulate(dates, opn, high, close, int(ei), sells.get(t), tp, hold_days, cost)
            if res is None:
                continue
            ret, reason, xi, at = res
            e, x = df.index[ei], df.index[xi]
            out.append({"ticker": t, "entry_date": e, "exit_date": x, "exit_reason": reason, "ret": ret,
                        "move": m, "iwm": bench_ret(bench["IWM"], e, x, at), "spy": bench_ret(bench["SPY"], e, x, at)})
    c = pd.DataFrame(out)
    if len(c):
        c["vs_iwm"] = c["ret"] - c["iwm"]
        c["vs_spy"] = c["ret"] - c["spy"]
    return c


# ---------------------------------------------------------------- statistics
def tstat(x):
    x = pd.Series(x, dtype=float).dropna()
    if len(x) < 2 or x.std(ddof=1) == 0:
        return np.nan
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))


def monthly_t(df, col="vs_iwm"):
    if df.empty:
        return np.nan, 0
    m = df.groupby(df["entry_date"].dt.to_period("M"))[col].mean()
    return tstat(m), int(len(m))


def welch(a, b):
    a, b = pd.Series(a, dtype=float).dropna(), pd.Series(b, dtype=float).dropna()
    if len(a) < 2 or len(b) < 2:
        return np.nan, np.nan
    d = a.mean() - b.mean()
    return float(d), float(d / np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b)))


def describe(t, name, period):
    if t.empty:
        return {"group": name, "period": period, "n": 0}
    r = t["ret"]
    mt, nm = monthly_t(t)
    ex = t["exit_reason"].value_counts(normalize=True)
    return {
        "group": name, "period": period, "n": int(len(t)), "profitable": int((r > 0).sum()),
        "win_rate": float((r > 0).mean()), "mean": float(r.mean()), "median": float(r.median()),
        "beat_iwm_rate": float((t["vs_iwm"] > 0).mean()), "mean_vs_iwm": float(t["vs_iwm"].mean()),
        "t_vs_iwm": tstat(t["vs_iwm"]), "t_vs_iwm_monthly": mt, "months": nm,
        "mean_vs_spy": float(t["vs_spy"].mean()), "t_vs_spy": tstat(t["vs_spy"]),
        "take_profit_share": float(ex.get("take_profit", 0)), "cluster_sell_share": float(ex.get("cluster_sell", 0)),
        "held_to_end_share": float(ex.get("held_to_end", 0)), "avg_days_held": float(t["days_held"].mean()) if "days_held" in t else np.nan,
        "avg_win": float(r[r > 0].mean()) if (r > 0).any() else np.nan,
        "avg_loss": float(r[r <= 0].mean()) if (r <= 0).any() else np.nan,
    }


def in_period(t, a, b):
    return t[(t["entry_date"] >= pd.Timestamp(a)) & (t["entry_date"] <= pd.Timestamp(b))]


def pct(x):
    return "" if x is None or not np.isfinite(x) else f"{x * 100:+.2f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tp", type=float, default=10.0, help="take-profit %% within the first 5 trading days (0 = none)")
    ap.add_argument("--move", type=float, default=3.0, help="skip if the open is more than this %% from where insiders bought")
    ap.add_argument("--hold", type=int, default=30, help="calendar days to hold otherwise")
    ap.add_argument("--window", type=int, default=14)
    a = ap.parse_args()
    tp = a.tp / 100 if a.tp > 0 else None
    mv = a.move / 100
    cost = C.COST_PER_SIDE
    out_dir = C.RESULTS / "strategy"
    out_dir.mkdir(parents=True, exist_ok=True)

    ins = pd.read_parquet(C.DATA / "insiders.parquet")
    sales = pd.read_parquet(C.DATA / "sales.parquet")
    px = CT.load_prices()
    for b in ("SPY", "IWM"):
        if b not in px:
            sys.exit(f"{b} prices missing")
    bench = {"SPY": px["SPY"], "IWM": px["IWM"]}
    print(f"{len(ins):,} purchases, {len(sales):,} sales, prices for {len(px):,} tickers; "
          f"purchases {ins['filing_dt'].min():%Y-%m-%d} -> {ins['filing_dt'].max():%Y-%m-%d}")

    clean = CT.prep_insiders(ins)
    clusters = CT.find_clusters(clean, a.window)
    sells = cluster_sell_times(sales)
    print(f"{len(clusters):,} cluster buys; cluster-sell events for {len(sells):,} tickers "
          f"({sum(len(v) for v in sells.values()):,} events)")
    cand = build_candidates(clusters, px)
    cand = cand[cand["tradeable"]]
    print(f"{len(cand):,} tradeable cluster buys with prices ({cand['csuite'].mean():.0%} include a CEO/CFO/President)")

    trades = run_trades(cand, px, sells, bench, tp, mv, a.hold, cost)
    ctrl = control_trades(px, clean, sells, bench, tp, mv, a.hold, cost)
    print(f"{len(trades):,} trades, {len(ctrl):,} control trades")

    rows, verdict = [], {}
    for pname, p0, p1 in PERIODS:
        t = in_period(trades, p0, p1)
        cs, nc = t[t["csuite"]], t[~t["csuite"]]
        rows += [describe(cs, "Cluster incl. CEO/CFO/President", pname),
                 describe(nc, "Cluster without CEO/CFO/President", pname),
                 describe(t, "Every cluster buy", pname),
                 describe(in_period(ctrl, p0, p1).assign(days_held=np.nan), "No insider buy, same rules (control)", pname)]
        d, dt = welch(cs["vs_iwm"], nc["vs_iwm"])
        mt, _ = monthly_t(cs)
        verdict[pname] = {"csuite_n": int(len(cs)), "csuite_mean_vs_iwm": float(cs["vs_iwm"].mean()) if len(cs) else np.nan,
                          "rule1_t_vs_iwm": tstat(cs["vs_iwm"]), "rule2_diff_vs_non_csuite": d, "rule2_t": dt,
                          "rule3_monthly_t": mt}
        v = verdict[pname]
        v["pass"] = bool(v["csuite_mean_vs_iwm"] > 0 and v["rule1_t_vs_iwm"] > 2 and d > 0 and dt > 2 and mt > 2)
    summ = pd.DataFrame(rows)

    # Sensitivity: take-profit level x skip threshold, C-suite clusters, both periods
    grid = []
    for g_tp in GRID_TP:
        for g_mv in GRID_MOVE:
            gt = run_trades(cand[cand["csuite"]], px, sells, bench, g_tp, g_mv, a.hold, cost)
            for pname, p0, p1 in PERIODS:
                s = describe(in_period(gt, p0, p1), "csuite", pname) if len(gt) else {"n": 0}
                grid.append({"take_profit": g_tp if g_tp else 0, "max_move": g_mv if g_mv else 9.99, "period": pname,
                             **{k: s.get(k) for k in ("n", "win_rate", "mean", "mean_vs_iwm", "t_vs_iwm")}})
    grid = pd.DataFrame(grid)

    years = []
    for y, g in trades.groupby(trades["entry_date"].dt.year):
        for name, sub in (("csuite", g[g["csuite"]]), ("other", g[~g["csuite"]])):
            if len(sub):
                years.append({"year": int(y), "group": name, "n": len(sub), "win_rate": (sub["ret"] > 0).mean(),
                              "mean": sub["ret"].mean(), "mean_vs_iwm": sub["vs_iwm"].mean()})
    years = pd.DataFrame(years)

    summ.to_csv(out_dir / "summary.csv", index=False)
    grid.to_csv(out_dir / "grid.csv", index=False)
    years.to_csv(out_dir / "by_year.csv", index=False)
    trades.drop(columns=["ei"]).to_csv(out_dir / "trades.csv", index=False)
    meta = {"take_profit": tp, "max_move": mv, "hold_calendar_days": a.hold, "window_days": a.window,
            "cost_per_side": cost, "purchases": int(len(ins)), "sales": int(len(sales)),
            "cluster_buys": int(len(clusters)), "tradeable_candidates": int(len(cand)), "trades": int(len(trades)),
            "verdict": verdict}
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, default=float))

    L = [f"# Cluster-buy rules: TP {pct(tp) if tp else 'none'} in 5 days, else {a.hold} calendar days, "
         f"cluster-sell exit, skip if moved > {mv:.0%}\n",
         f"- {len(clusters):,} cluster buys, {len(cand):,} tradeable with prices, {len(trades):,} trades taken",
         "- Benchmarks over each trade's exact holding window. Costs "
         f"{cost:.2%} per side. No stop-loss.\n", "## Verdict (pre-registered, judged on 2005-2014)\n"]
    for pname, v in verdict.items():
        L.append(f"- **{pname}**: C-suite n={v['csuite_n']}, avg vs IWM {pct(v['csuite_mean_vs_iwm'])} "
                 f"(t {v['rule1_t_vs_iwm']:.2f}); minus non-C-suite {pct(v['rule2_diff_vs_non_csuite'])} "
                 f"(t {v['rule2_t']:.2f}); monthly t {v['rule3_monthly_t']:.2f} -> {'PASS' if v['pass'] else 'FAIL'}")
    L.append("\n## Groups\n\n| group | period | trades | profitable | win rate | avg | median | vs IWM | t | beat IWM | vs SPY | TP / sell / held |\n|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in summ.to_dict("records"):
        if not r.get("n"):
            continue
        L.append(f"| {r['group']} | {r['period']} | {r['n']:,} | {r['profitable']:,} | {r['win_rate']:.1%} | {pct(r['mean'])} | "
                 f"{pct(r['median'])} | {pct(r['mean_vs_iwm'])} | {r['t_vs_iwm']:.2f} | {r['beat_iwm_rate']:.1%} | "
                 f"{pct(r['mean_vs_spy'])} | {r['take_profit_share']:.0%} / {r['cluster_sell_share']:.0%} / {r['held_to_end_share']:.0%} |")
    L.append("\n## C-suite sensitivity: average vs IWM (take-profit x skip threshold)\n")
    for pname, *_ in PERIODS:
        g = grid[grid["period"] == pname].pivot(index="take_profit", columns="max_move", values="mean_vs_iwm")
        L.append(f"\n{pname}\n\n" + (g * 100).round(2).to_markdown())
    (out_dir / "summary.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
