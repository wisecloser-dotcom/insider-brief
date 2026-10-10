"""Download daily OHLCV for every ticker with an insider purchase, plus SPY.

Uses Yahoo Finance via yfinance (free). Yahoo throttles heavy users: after ~1,500
tickers it starts returning empty data for everything. So this script:
  - downloads SPY and the calibration tickers first,
  - uses small chunks with pauses,
  - treats a chunk where (almost) every ticker fails as throttling, backs off and retries,
  - only marks a ticker "missing" (likely delisted) after it fails in a normal chunk twice,
  - stops after --max-minutes so the workflow can save progress; re-running resumes.

Known limitation: delisted tickers are often missing from Yahoo, which biases results
upward (survivorship bias). They are listed in results/missing_prices.txt.
"""
import argparse
import json
import logging
import os
import sys
import time

import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.dirname(__file__))
import config as C  # noqa: E402

PX = C.DATA / "prices"
MISSING = C.DATA / "missing_prices.txt"     # cached across runs: confirmed-missing tickers
ATTEMPTS = C.DATA / "failed_once.txt"       # failed once in a normal chunk
HIST = C.DATA / "price_history_start.json"  # start date each cached file was downloaded from
OLD_DEFAULT_START = "2014-05-01"            # files cached before this marker existed
CHUNK = 25

logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def yahoo_symbol(t):
    return t.replace(".", "-")


def _read_set(p):
    return set(p.read_text().split()) if p.exists() else set()


def download_chunk(chunk, start):
    """Return {ticker: DataFrame or None}."""
    syms = [yahoo_symbol(t) for t in chunk]
    try:
        raw = yf.download(syms, start=start, auto_adjust=True, group_by="ticker",
                          threads=True, progress=False)
    except Exception as e:  # noqa: BLE001
        print(f"  download error: {e!r}")
        return {t: None for t in chunk}
    out = {}
    for t, s in zip(chunk, syms):
        try:
            df = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
            df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Open", "Close"])
        except (KeyError, TypeError):
            df = None
        out[t] = df if df is not None and len(df) >= 30 else None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-minutes", type=float, default=200)
    ap.add_argument("--retry-missing", action="store_true", help="re-try tickers already marked missing")
    a = ap.parse_args()
    t0 = time.time()
    PX.mkdir(parents=True, exist_ok=True)
    C.RESULTS.mkdir(parents=True, exist_ok=True)

    ins = pd.read_parquet(C.DATA / "insiders.parquet")
    priority = ["SPY", "IWM"] + [c[0] for c in C.CALIBRATION]
    rest = sorted(set(ins["ticker"]) - set(priority))
    tickers = priority + rest
    start = (pd.Timestamp(C.START) - pd.DateOffset(months=8)).strftime("%Y-%m-%d")
    stale = pd.Timestamp.today().normalize() - pd.Timedelta(days=7)

    missing = set() if a.retry_missing else _read_set(MISSING) - set(priority)
    failed_once = _read_set(ATTEMPTS)
    hist = json.loads(HIST.read_text()) if HIST.exists() else {}
    # A cached file is reusable only if it is recent AND was downloaded from at least as far back
    # as this run needs (extending BT_START to earlier years re-downloads everything once).
    have = {f.stem for f in PX.glob("*.parquet")
            if pd.Timestamp(f.stat().st_mtime, unit="s") > stale and hist.get(f.stem, OLD_DEFAULT_START) <= start}
    todo = [t for t in tickers if t not in have and t not in missing]
    print(f"{len(tickers)} tickers: {len(have)} cached, {len(missing)} known missing, {len(todo)} to download")

    queue = list(todo)
    backoff = 60
    done_n = 0
    while queue:
        if (time.time() - t0) / 60 > a.max_minutes:
            print(f"time budget reached with {len(queue)} tickers left; re-run the workflow to continue")
            break
        chunk, queue = queue[:CHUNK], queue[CHUNK:]
        res = download_chunk(chunk, start)
        ok = [t for t, df in res.items() if df is not None]
        bad = [t for t, df in res.items() if df is None]
        for t in ok:
            df = res[t]
            df.index = pd.to_datetime(df.index).tz_localize(None)
            df.to_parquet(PX / f"{t}.parquet")
            failed_once.discard(t)
            hist[t] = start
        throttled = False
        if not ok or (len(chunk) >= 5 and len(ok) <= 1):
            # A chunk of delisted tickers also fails entirely, so confirm with a canary.
            throttled = download_chunk(["SPY"], start)["SPY"] is None
        if throttled:
            print(f"  throttled ({len(bad)}/{len(chunk)} failed) - waiting {backoff}s")
            queue = chunk + queue
            time.sleep(backoff)
            backoff = min(backoff * 2, 900)
            continue
        backoff = 60
        for t in bad:
            if t in failed_once:
                missing.add(t)
                failed_once.discard(t)
            else:
                failed_once.add(t)
                queue.append(t)          # second chance later in the run
        done_n += len(ok)
        if done_n and done_n % 500 < len(ok):
            print(f"  {done_n} downloaded, {len(missing)} missing, {len(queue)} queued, "
                  f"{(time.time() - t0) / 60:.0f} min")
        MISSING.write_text("\n".join(sorted(missing)))
        ATTEMPTS.write_text("\n".join(sorted(failed_once)))
        HIST.write_text(json.dumps(hist))
        time.sleep(3)

    MISSING.write_text("\n".join(sorted(missing)))
    ATTEMPTS.write_text("\n".join(sorted(failed_once)))
    (C.RESULTS / "missing_prices.txt").write_text("\n".join(sorted(missing)))
    n_have = len(list(PX.glob("*.parquet")))
    print(f"prices on disk for {n_have} of {len(tickers)} tickers; {len(missing)} confirmed missing; "
          f"{len(queue)} not yet attempted")
    if not (PX / "SPY.parquet").exists():
        sys.exit("SPY prices missing - Yahoo is blocking this runner. Re-run later.")


if __name__ == "__main__":
    main()
