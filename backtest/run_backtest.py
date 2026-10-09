"""Run every strategy variant, baselines and the control group; write results/."""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import bt  # noqa: E402
import config as C  # noqa: E402


def pct(x, d=2):
    if not isinstance(x, (int, float, np.floating, np.integer)) or not np.isfinite(x):
        return ""
    return f"{x * 100:.{d}f}%"


def stats_row(name, r):
    s = bt.stats(r)
    return {"strategy": name, **s}


def md_table(rows, cols):
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    body = ""
    for r in rows:
        cells = []
        for c in cols:
            v = r.get(c, "")
            if c in ("win_rate", "mean", "median", "avg_win", "avg_loss", "max_drawdown", "total_return"):
                v = pct(v)
            elif c in ("t_stat", "profit_factor"):
                v = f"{v:.2f}" if isinstance(v, (int, float, np.floating)) and np.isfinite(v) else ""
            cells.append(str(v))
        body += "| " + " | ".join(cells) + " |\n"
    return head + body


def load_prices():
    prices = {}
    for f in sorted((C.DATA / "prices").glob("*.parquet")):
        df = pd.read_parquet(f)
        if len(df) >= 30:
            prices[f.stem] = bt.add_features(df)
    return prices


def main():
    C.RESULTS.mkdir(parents=True, exist_ok=True)
    ins = pd.read_parquet(C.DATA / "insiders.parquet")
    ins = ins[ins["filing_dt"] >= pd.Timestamp(C.START)]
    prices = load_prices()
    spy = prices["SPY"]
    calendar = pd.Series(spy.index.values)
    print(f"{len(ins)} insider purchases, price history for {len(prices)} tickers")

    # 1. calibrate indicator definitions against the video
    cal, ind = bt.calibrate(prices)
    print("indicator definitions chosen:", ind)

    # 2. signals + market data
    sig = bt.build_signals(ins, calendar)
    data = bt.attach_market_data(sig, prices, spy, ind)
    coverage = len(data) / max(len(sig), 1)
    print(f"{len(sig)} signals, {len(data)} with usable prices ({coverage:.0%})")
    data["period"] = np.where(pd.to_datetime(data["entry_date"]) <= pd.Timestamp(C.TRAIN_END), "train", "test")

    summary, by_year, trades_out = [], {}, {}
    # 3. each variant
    for name, v in C.VARIANTS.items():
        tr = bt.simulate(bt.apply_filters(data, v), v)
        trades_out[name] = tr
        for per in ("all", "train", "test"):
            sub = tr if per == "all" else tr[tr["period"] == per]
            row = stats_row(name, sub["ret"])
            row["period"] = per
            if v["kind"] == "day" and len(sub):
                eq = bt.equity_curve(sub)
                row["total_return"] = float(eq["equity"].iloc[-1] - 1)
                row["max_drawdown"] = float(eq["drawdown"].min())
                row["stop_hit_rate"] = float((sub["exit_reason"] == "stop").mean())
            summary.append(row)
        if len(tr):
            yr = tr.groupby(pd.to_datetime(tr["entry_date"]).dt.year)["ret"]
            by_year[name] = yr.agg(["size", "mean", lambda s: (s > 0).mean()]).set_axis(
                ["n", "mean", "win_rate"], axis=1)

    # 4. baselines
    v2 = C.VARIANTS["day_v2"]
    base_all = bt.simulate(data, {**v2, "min_dvol": None, "max_dvol": None, "min_atr": None,
                                  "max_atr": None, "spy_gate": False})
    base_all_nostop = bt.simulate(data, {**v2, "stop_atr_mult": None})
    no_repeat = data.copy()
    no_repeat["repeat_buy"] = False
    v2_allow_repeat = bt.simulate(bt.apply_filters(no_repeat, v2), v2)
    v2_trades = trades_out["day_v2"]
    spy_days = spy.reindex(pd.to_datetime(v2_trades["entry_date"].unique()))
    spy_oc = (spy_days["Close"] / spy_days["Open"] - 1).dropna()

    runs = (ins.assign(run=ins["filing_dt"].dt.normalize())
            .groupby("ticker")["run"].apply(lambda s: np.sort(s.values)).to_dict())
    ctrl = bt.control_returns(prices, spy, runs, v2, ind)

    comp = [
        stats_row("Variant #2 (his rules)", v2_trades["ret"]),
        stats_row("Variant #2 but repeat buys allowed", v2_allow_repeat["ret"]),
        stats_row("All $25k+ insider buys, open->close, 1.5xATR stop", base_all["ret"]),
        stats_row("All $25k+ insider buys, open->close, no stop", base_all_nostop["ret"]),
        stats_row("CONTROL: same filters, no insider buy", ctrl["ret"]),
        stats_row("SPY open->close on the same days", spy_oc),
    ]
    for per in ("train", "test"):
        comp.append(stats_row(f"Variant #2 [{per}]", v2_trades.loc[v2_trades["period"] == per, "ret"]))
        cp = ctrl[pd.to_datetime(ctrl["entry_date"]) <= pd.Timestamp(C.TRAIN_END)] if per == "train" \
            else ctrl[pd.to_datetime(ctrl["entry_date"]) > pd.Timestamp(C.TRAIN_END)]
        comp.append(stats_row(f"CONTROL [{per}]", cp["ret"]))

    # 5. sensitivity grid for Variant #2, train period only
    train = data[data["period"] == "train"]
    grid = []
    for a in C.GRID_MIN_ATR:
        for dv in C.GRID_MIN_DVOL:
            vv = {**v2, "min_atr": a, "min_dvol": dv}
            tr = bt.simulate(bt.apply_filters(train, vv), vv)
            s = bt.stats(tr["ret"]) if len(tr) else {"n": 0}
            grid.append({"min_atr": a, "min_dvol_m": dv / 1e6, **s})
    grid = pd.DataFrame(grid)

    # 6. known-trade check
    kt, kd = C.KNOWN_TRADE
    known = data[(data["ticker"] == kt) & (pd.to_datetime(data["entry_date"]) == pd.Timestamp(kd))]
    known_txt = "not found in data"
    if len(known):
        k = known.iloc[0]
        known_txt = (f"{kt} entry {kd}: open {k['open']:.2f} -> close {k['close']:.2f} = "
                     f"{pct(k['close'] / k['open'] - 1)} before costs; "
                     f"ATR {pct(k['atr'])}, $vol {k['dvol'] / 1e6:.1f}M, repeat={k['repeat_buy']}; "
                     f"in Variant #2 trades: {bool(((v2_trades['ticker'] == kt) & (pd.to_datetime(v2_trades['entry_date']) == pd.Timestamp(kd))).any())}")

    # ---------------------------------------------------------------- write outputs
    for name, tr in trades_out.items():
        cols = ["ticker", "signal_date", "entry_date", "first_filing", "value", "n_insiders", "titles",
                "multiple_buys", "atr", "matr", "dvol", "gap", "spy_gap", "open", "close", "stop_pct",
                "exit_reason", "ret", "period"]
        tr[[c for c in cols if c in tr]].to_csv(C.RESULTS / f"trades_{name}.csv", index=False)
        if C.VARIANTS[name]["kind"] == "day" and len(tr):
            bt.equity_curve(tr).to_csv(C.RESULTS / f"equity_{name}.csv")
    grid.to_csv(C.RESULTS / "grid_day_v2_train.csv", index=False)
    cal.to_csv(C.RESULTS / "calibration.csv", index=False)
    pd.DataFrame(summary).to_csv(C.RESULTS / "summary.csv", index=False)
    pd.DataFrame(comp).to_csv(C.RESULTS / "comparison.csv", index=False)
    for name, yr in by_year.items():
        yr.to_csv(C.RESULTS / f"by_year_{name}.csv")

    meta = {
        "insider_source": str(ins["source"].iloc[0]) if len(ins) else None,
        "n_purchases": int(len(ins)), "n_signals": int(len(sig)), "price_coverage": coverage,
        "period": [str(ins["filing_dt"].min()), str(ins["filing_dt"].max())],
        "train_end": C.TRAIN_END, "cost_per_side": C.COST_PER_SIDE, "indicators": ind,
        "known_trade": known_txt,
    }
    (C.RESULTS / "meta.json").write_text(json.dumps(meta, indent=2, default=str))

    cols = ["strategy", "period", "n", "win_rate", "mean", "median", "t_stat", "profit_factor",
            "avg_win", "avg_loss", "total_return", "max_drawdown"]
    gcols = ["min_atr", "min_dvol_m", "n", "win_rate", "mean", "t_stat"]
    md = [
        "# Insider-buy strategy backtest\n",
        f"- Insider data: **{meta['insider_source']}**, {meta['n_purchases']:,} purchases "
        f"({meta['period'][0][:10]} to {meta['period'][1][:10]}), {meta['n_signals']:,} signals, "
        f"{coverage:.0%} with price data",
        f"- Train <= {C.TRAIN_END}, test after. Costs {C.COST_PER_SIDE:.2%} per side. "
        f"Returns are per trade, after costs.",
        f"- Indicator definitions chosen by calibration: {ind}",
        f"- Known trade check: {known_txt}\n",
        "## Does the insider buy matter?\n", md_table(comp, cols[:1] + cols[2:10]),
        "\n## Variants\n", md_table(summary, cols),
        "\n## Variant #2 sensitivity (train period)\n",
        md_table(grid.to_dict("records"), gcols),
        "\n## Calibration vs the video\n", cal.round(4).to_markdown(index=False) if len(cal) else "no calibration tickers found",
    ]
    (C.RESULTS / "summary.md").write_text("\n".join(md))
    print((C.RESULTS / "summary.md").read_text())


if __name__ == "__main__":
    main()
