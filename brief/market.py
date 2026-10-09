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
    return pd.DataFrame({"close": h["Close"], "volume": h["Volume"], "split_after": after})


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
