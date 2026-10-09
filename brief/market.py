"""Prices from Yahoo Finance (via yfinance). EDGAR has no prices."""
from datetime import date, timedelta

import numpy as np
import pandas as pd


def history(ticker: str, months: int = 7) -> pd.DataFrame | None:
    """Daily closes, split-adjusted, plus each day's split factor so filed prices can
    be put on the same scale. Columns: close, volume, split_after."""
    import yfinance as yf
    try:
        h = yf.Ticker(ticker).history(start=date.today() - timedelta(days=31 * months + 10),
                                      auto_adjust=False, actions=True)
    except Exception:
        return None
    if h is None or h.empty:
        return None
    h.index = pd.DatetimeIndex(h.index).tz_localize(None).normalize()
    split = h.get("Stock Splits", pd.Series(0.0, index=h.index)).replace(0, 1.0).fillna(1.0)
    after = split[::-1].cumprod()[::-1].shift(-1, fill_value=1.0)
    return pd.DataFrame({"open": h["Open"], "high": h["High"], "low": h["Low"], "close": h["Close"],
                         "volume": h["Volume"], "split_after": after})


def ohlc(px: pd.DataFrame | None) -> dict | None:
    """Daily candles for the interactive chart, in a compact form for the page."""
    if px is None or px.empty or "open" not in px:
        return None
    d = px.dropna(subset=["open", "high", "low", "close"])
    r = lambda s: [round(float(x), 4) for x in s]
    return {"t": [x.strftime("%Y-%m-%d") for x in d.index], "o": r(d["open"]), "h": r(d["high"]),
            "l": r(d["low"]), "c": r(d["close"]), "v": [int(x) if x == x else 0 for x in d["volume"]]}


def intraday_batch(tickers: list[str], period: str = "1mo", interval: str = "60m") -> dict:
    """Hourly closes for many tickers in a few batched requests: {ticker: [(time, close), ...]}.
    Used for the mini charts, so they're refreshed on every run."""
    import yfinance as yf
    out = {}
    tickers = sorted({t for t in tickers if t})
    for i in range(0, len(tickers), 80):
        chunk = tickers[i:i + 80]
        try:
            df = yf.download(chunk, period=period, interval=interval, group_by="ticker",
                             auto_adjust=False, progress=False, threads=True)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        for t in chunk:
            try:
                s = df[t]["Close"] if isinstance(df.columns, pd.MultiIndex) else df["Close"]
            except KeyError:
                continue
            s = s.dropna()
            if s.empty:
                continue
            idx = s.index.tz_convert("America/New_York").tz_localize(None) if s.index.tz is not None else s.index
            out[t] = [(ts.strftime("%Y-%m-%d %H:%M"), float(v)) for ts, v in zip(idx, s.values)]
    return out


def last_price(ticker: str) -> float | None:
    import yfinance as yf
    try:
        p = yf.Ticker(ticker).fast_info["last_price"]
        return float(p) if p and np.isfinite(p) else None
    except Exception:
        return None


def market_cap_fallback(ticker: str) -> float | None:
    import yfinance as yf
    try:
        v = yf.Ticker(ticker).fast_info["market_cap"]
        return float(v) if v and np.isfinite(v) else None
    except Exception:
        return None


def summary(px: pd.DataFrame | None) -> dict:
    if px is None or px.empty:
        return {"price": None, "adv": None, "chg_6m": None}
    c = px["close"]
    six = c[c.index >= c.index[-1] - pd.DateOffset(months=6)]
    return {"price": float(c.iloc[-1]),
            "adv": float((c * px["volume"]).tail(60).mean()),   # average daily dollar volume
            "chg_6m": float(six.iloc[-1] / six.iloc[0] - 1) if len(six) > 1 else None}


def to_chart_scale(px: pd.DataFrame | None, trade_date: str | None, price: float) -> float:
    """A price filed before a later split, divided down to today's share basis."""
    if px is None or not trade_date:
        return price
    pos = px.index.searchsorted(pd.Timestamp(str(trade_date)[:10]))
    if pos >= len(px):
        return price
    return price / px["split_after"].iloc[pos]
