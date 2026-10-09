# Insider-buy strategy backtest

Tests the "AI Trade Plans" insider-buying strategies reverse-engineered from
Steven Silverglade's video, and whether the insider buy actually adds anything.

## Run it
GitHub → **Actions** → **Backtest insider strategies** → **Run workflow**.
Results are committed to `backtest/results/` and shown on the run page.
The first run takes ~30–60 min (downloading ~10 years of data); later runs reuse the cache.

## What it tests
| Variant | Rules |
|---|---|
| `day_v2` | Any month, $ volume ≥ $30M, daily ATR(5) 7–20%, no insider buy in prior 30 days, SPY opens within ±0.5%. Buy next open, stop −1.5×ATR, sell at close |
| `day_v1_*` | Earnings-season months only, $30–100M volume, ATR ≥ 3% (two month definitions tested) |
| `swing_*d` | Monthly ATR ≥ 30%, $30M+ volume, hold 5 / 10 / 20 days |

Rules marked (EST) in `config.py` are estimates from the video's alerts.

## Comparisons (`comparison.csv`)
- **All $25k+ insider buys** — does his filter add anything?
- **Control** — same filters on stock-days *without* an insider buy. If this matches
  Variant #2, the edge is "volatile stock after a big move", not the insider buy.
- **SPY open→close** on the same days.
- **Train (2015–2021) vs test (2022+)** and a sensitivity grid, to catch overfitting.

## Known limitations
- Yahoo prices miss many delisted stocks → results biased upward (see `missing_prices.txt`).
- Stops assume a fill exactly at the stop price.
- SEC fallback has filing dates but no times, so some entries are a day late.

## Files
`fetch_insiders.py` → `fetch_prices.py` → `run_backtest.py`; logic in `bt.py`, settings in `config.py`.
