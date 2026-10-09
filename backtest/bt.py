"""Core backtest logic: indicators, signal timing, trade simulation, statistics."""
import numpy as np
import pandas as pd

import config as C


# ---------------------------------------------------------------- indicators
def _tr(h, l, pc):
    return pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)


def add_features(df, n=C.ATR_LEN):
    """Add indicator columns, all known at the CLOSE of each row's day."""
    df = df.sort_index().copy()
    h, l, c, v = df["High"], df["Low"], df["Close"], df["Volume"]
    tr = _tr(h, l, c.shift(1))
    df["atr_wilder"] = tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / c
    df["atr_sma"] = tr.rolling(n).mean() / c
    df["atr14"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / c
    df["dvol_1d"] = c * v
    df["dvol_5d"] = df["dvol_1d"].rolling(5).mean()
    df["dvol_20d"] = df["dvol_1d"].rolling(20).mean()

    # Monthly ATR: Wilder ATR(n) on monthly bars. "asof" includes the current, partial month.
    per = df.index.to_period("M")
    M = df.resample("ME").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    M.index = M.index.to_period("M")
    trM = _tr(M["High"], M["Low"], M["Close"].shift(1))
    atrM = trM.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    prev_atr = pd.Series(atrM.shift(1).values, index=M.index).reindex(per).values
    prev_close = pd.Series(M["Close"].shift(1).values, index=M.index).reindex(per).values
    mh = df["High"].groupby(per).cummax().values
    ml = df["Low"].groupby(per).cummin().values
    tr_part = np.nanmax(np.vstack([mh - ml, np.abs(mh - prev_close), np.abs(ml - prev_close)]), axis=0)
    df["matr_asof"] = (prev_atr * (1 - 1 / n) + tr_part / n) / c.values
    df["matr_completed"] = prev_atr / c.values
    return df


INDICATOR_CHOICES = {
    "atr": ["atr_wilder", "atr_sma", "atr14"],
    "matr": ["matr_asof", "matr_completed"],
    "dvol": ["dvol_1d", "dvol_5d", "dvol_20d"],
}
DEFAULT_INDICATORS = {"atr": "atr_wilder", "matr": "matr_asof", "dvol": "dvol_1d"}


def calibrate(prices):
    """Compare our indicator definitions to the values shown in the video; pick closest."""
    rows = []
    for t, d, atr, matr, dvol in C.CALIBRATION:
        df = prices.get(t)
        if df is None:
            continue
        d = pd.Timestamp(d)
        if d not in df.index:
            continue
        r = df.loc[d]
        rec = {"ticker": t, "date": d.date(), "video_atr": atr, "video_matr": matr, "video_dvol": dvol}
        for k in sum(INDICATOR_CHOICES.values(), []):
            rec[k] = float(r[k])
        rows.append(rec)
    cal = pd.DataFrame(rows)
    chosen = dict(DEFAULT_INDICATORS)
    if len(cal):
        for key, target in (("atr", "video_atr"), ("matr", "video_matr"), ("dvol", "video_dvol")):
            errs = {k: float(np.nanmean(np.abs(cal[k] / cal[target] - 1))) for k in INDICATOR_CHOICES[key]}
            errs = {k: v for k, v in errs.items() if np.isfinite(v)}
            if errs:
                chosen[key] = min(errs, key=errs.get)
    return cal, chosen


# ---------------------------------------------------------------- signal timing
def build_signals(ins, calendar):
    """Group filings into one signal per (ticker, evening run).

    A filing belongs to the run on its own date if filed before ~9:30pm ET, otherwise the
    next day's run. Entry is the first trading day after the run; indicators are read
    from the last trading day on or before the run (its close is already known).
    """
    ins = ins.copy()
    fd = ins["filing_dt"]
    hours = fd.dt.hour + fd.dt.minute / 60
    run = fd.dt.normalize() + pd.to_timedelta((hours >= C.RUN_CUTOFF_HOUR).astype(int), unit="D")
    ins["run_date"] = run
    cal = calendar.values
    pos = np.searchsorted(cal, run.values, side="right")  # first trading day > run
    ok = (pos > 0) & (pos < len(cal))
    ins = ins[ok].copy()
    pos = pos[ok]
    ins["entry_date"] = cal[pos]
    ins["signal_date"] = cal[pos - 1]

    g = ins.groupby(["ticker", "entry_date"]).agg(
        signal_date=("signal_date", "first"), run_date=("run_date", "first"),
        first_filing=("filing_dt", "min"), value=("value", "sum"),
        n_filings=("filing_dt", "size"), n_insiders=("insider", "nunique"),
        titles=("title", lambda s: "; ".join(sorted(set(map(str, s))))[:120]),
    ).reset_index()
    g["multiple_buys"] = (g["n_filings"] > 1) | (g["n_insiders"] > 1)

    # Repeat buy: any purchase filing for the ticker in the 30 days before this group's first filing
    times = ins.groupby("ticker")["filing_dt"].apply(lambda s: np.sort(s.values)).to_dict()
    look = np.timedelta64(C.REPEAT_LOOKBACK_DAYS, "D")
    rep = []
    for t, f in zip(g["ticker"], g["first_filing"].values):
        arr = times[t]
        lo = np.searchsorted(arr, f - look, side="left")
        hi = np.searchsorted(arr, f, side="left")
        rep.append(hi > lo)
    g["repeat_buy"] = rep
    return g


# ---------------------------------------------------------------- trade simulation
def attach_market_data(sig, prices, spy, ind, max_hold=20):
    """Add indicators (at signal_date) and forward prices (from entry_date) to each signal."""
    spy_gap = (spy["Open"] / spy["Close"].shift(1) - 1)
    out = []
    for t, grp in sig.groupby("ticker"):
        df = prices.get(t)
        if df is None:
            continue
        idx = df.index
        for r in grp.itertuples(index=False):
            if r.signal_date not in idx or r.entry_date not in idx:
                continue
            si = idx.get_loc(r.signal_date)
            ei = idx.get_loc(r.entry_date)
            if ei != si + 1:          # ticker missing a session between signal and entry
                continue
            s = df.iloc[si]
            fwd = df.iloc[ei:ei + max_hold]
            rec = r._asdict()
            rec.update(
                atr=s[ind["atr"]], matr=s[ind["matr"]], dvol=s[ind["dvol"]], sig_close=s["Close"],
                open=fwd["Open"].iloc[0], high=fwd["High"].iloc[0], low=fwd["Low"].iloc[0],
                close=fwd["Close"].iloc[0],
                fwd_close=fwd["Close"].values, fwd_low=fwd["Low"].values,
                spy_gap=spy_gap.get(r.entry_date, np.nan),
                gap=fwd["Open"].iloc[0] / s["Close"] - 1,
            )
            out.append(rec)
    return pd.DataFrame(out)


def apply_filters(d, v):
    m = pd.Series(True, index=d.index)
    m &= ~d["repeat_buy"]
    if v.get("min_dvol") is not None:
        m &= d["dvol"] >= v["min_dvol"]
    if v.get("max_dvol") is not None:
        m &= d["dvol"] <= v["max_dvol"]
    if v.get("min_atr") is not None:
        m &= d["atr"] >= v["min_atr"]
    if v.get("max_atr") is not None:
        m &= d["atr"] <= v["max_atr"]
    if v.get("min_matr") is not None:
        m &= d["matr"] >= v["min_matr"]
    if v.get("months"):
        m &= pd.to_datetime(d["entry_date"]).dt.month.isin(v["months"])
    if v.get("spy_gate"):
        m &= d["spy_gap"].abs() <= C.SPY_GAP_MAX
    return d[m].copy()


def simulate(d, v, cost=C.COST_PER_SIDE):
    """Return per-trade results. Day: buy open, sell close, stop on intraday low.
    Swing: buy open, hold N sessions, sell close; optional stop checked on daily lows."""
    if d.empty:
        return d.assign(ret=[], exit_reason=[])
    entry = d["open"].values
    mult = v.get("stop_atr_mult")
    stop_pct = d["atr"].values * mult if mult else np.full(len(d), np.nan)
    stop_px = entry * (1 - stop_pct)
    rets, reasons = [], []
    hold = 1 if v["kind"] == "day" else v["hold_days"]
    for i in range(len(d)):
        closes, lows = d["fwd_close"].iloc[i], d["fwd_low"].iloc[i]
        if len(closes) < hold:
            rets.append(np.nan)
            reasons.append("insufficient_data")
            continue
        exit_px, reason = closes[hold - 1], "close"
        if mult:
            for k in range(hold):
                if lows[k] <= stop_px[i]:
                    exit_px, reason = stop_px[i], "stop"
                    break
        rets.append(exit_px / entry[i] - 1 - 2 * cost)
        reasons.append(reason)
    d = d.copy()
    d["stop_pct"] = stop_pct
    d["ret"] = rets
    d["exit_reason"] = reasons
    return d.dropna(subset=["ret"])


# ---------------------------------------------------------------- statistics
def stats(r):
    r = pd.Series(r, dtype=float).dropna()
    n = len(r)
    if n == 0:
        return dict(n=0)
    wins, losses = r[r > 0], r[r <= 0]
    sd = r.std(ddof=1) if n > 1 else np.nan
    return dict(
        n=n, win_rate=float((r > 0).mean()), mean=float(r.mean()), median=float(r.median()),
        std=float(sd), t_stat=float(r.mean() / (sd / np.sqrt(n))) if n > 1 and sd > 0 else np.nan,
        avg_win=float(wins.mean()) if len(wins) else np.nan,
        avg_loss=float(losses.mean()) if len(losses) else np.nan,
        profit_factor=float(wins.sum() / -losses.sum()) if losses.sum() < 0 else np.nan,
    )


def equity_curve(trades, alloc=C.DAY_ALLOCATION):
    """Day strategy: same-day trades split the daily allocation equally."""
    if trades.empty:
        return pd.DataFrame(columns=["equity", "drawdown"])
    daily = trades.groupby("entry_date")["ret"].mean() * alloc
    eq = (1 + daily).cumprod()
    return pd.DataFrame({"daily_ret": daily, "equity": eq, "drawdown": eq / eq.cummax() - 1})


def control_returns(prices, spy, ins_runs, v, ind, cost=C.COST_PER_SIDE):
    """Same filters + same exit, but on stock-days with NO insider buy in the prior 30 days.
    Answers: is the edge the insider buy, or just volatile stocks after a big move?"""
    spy_gap = (spy["Open"] / spy["Close"].shift(1) - 1)
    out = []
    look = pd.Timedelta(days=C.REPEAT_LOOKBACK_DAYS)
    for t, df in prices.items():
        if t == "SPY" or len(df) < 30:
            continue
        x = pd.DataFrame({
            "signal_date": df.index[:-1], "entry_date": df.index[1:],
            "atr": df[ind["atr"]].values[:-1], "matr": df[ind["matr"]].values[:-1],
            "dvol": df[ind["dvol"]].values[:-1],
            "open": df["Open"].values[1:], "low": df["Low"].values[1:], "close": df["Close"].values[1:],
        })
        x["spy_gap"] = spy_gap.reindex(x["entry_date"]).values
        x["repeat_buy"] = False
        x = apply_filters(x, v)
        if x.empty:
            continue
        runs = ins_runs.get(t)
        if runs is not None and len(runs):
            sd = x["signal_date"].values
            lo = np.searchsorted(runs, sd - look.to_timedelta64(), side="left")
            hi = np.searchsorted(runs, sd + np.timedelta64(1, "D"), side="left")
            x = x[hi == lo]
        if x.empty:
            continue
        stop_px = x["open"] * (1 - x["atr"] * v["stop_atr_mult"]) if v.get("stop_atr_mult") else None
        exit_px = x["close"].where(x["low"] > stop_px, stop_px) if stop_px is not None else x["close"]
        x["ret"] = exit_px / x["open"] - 1 - 2 * cost
        x["ticker"] = t
        out.append(x[["ticker", "entry_date", "ret"]])
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=["ticker", "entry_date", "ret"])
