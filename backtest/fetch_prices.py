"""Download daily OHLCV for every ticker with an insider purchase, plus SPY.

Uses Yahoo Finance via yfinance (free). Known limitation: delisted tickers are often
missing, which biases results upward (survivorship bias). Missing tickers are listed
in results/missing_prices.txt so we can see how big the gap is.
"""
import os
import sys
import time

import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.dirname(__file__))
import config as C  # noqa: E402

PX = C.DATA / "prices"


def yahoo_symbol(t):
    return t.replace(".", "-")


def main():
    PX.mkdir(parents=True, exist_ok=True)
    C.RESULTS.mkdir(parents=True, exist_ok=True)
    ins = pd.read_parquet(C.DATA / "insiders.parquet")
    tickers = sorted(set(ins["ticker"]) | {"SPY"} | {c[0] for c in C.CALIBRATION})
    start = (pd.Timestamp(C.START) - pd.DateOffset(months=8)).strftime("%Y-%m-%d")
    stale = pd.Timestamp.today().normalize() - pd.Timedelta(days=3)
    todo = []
    for t in tickers:
        f = PX / f"{t}.parquet"
        if f.exists() and pd.Timestamp(f.stat().st_mtime, unit="s") > stale:
            continue
        todo.append(t)
    print(f"{len(tickers)} tickers, {len(todo)} to download")
    missing = []
    for i in range(0, len(todo), 80):
        chunk = todo[i:i + 80]
        syms = [yahoo_symbol(t) for t in chunk]
        for attempt in range(3):
            try:
                raw = yf.download(syms, start=start, auto_adjust=True, group_by="ticker",
                                  threads=True, progress=False)
                break
            except Exception as e:  # noqa: BLE001
                print(f"  chunk {i}: {e!r}, retry")
                time.sleep(10 * (attempt + 1))
        else:
            missing += chunk
            continue
        for t, s in zip(chunk, syms):
            try:
                df = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
                df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Open", "Close"])
            except KeyError:
                df = pd.DataFrame()
            if len(df) < 30:
                missing.append(t)
                continue
            df.index = pd.to_datetime(df.index).tz_localize(None)
            df.to_parquet(PX / f"{t}.parquet")
        print(f"  {min(i + 80, len(todo))}/{len(todo)} done, {len(missing)} missing so far")
        time.sleep(2)
    (C.RESULTS / "missing_prices.txt").write_text("\n".join(sorted(missing)))
    print(f"missing price history for {len(missing)} of {len(tickers)} tickers")


if __name__ == "__main__":
    main()
