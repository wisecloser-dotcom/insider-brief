"""Backtest settings.

Values marked (VIDEO) were read directly off the strategy author's Discord alerts.
Values marked (EST) are estimated from which trades passed or failed in those alerts
and should be treated as guesses — the sensitivity grid tests ranges around them.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("BT_DATA_DIR", ROOT / "data"))
RESULTS = Path(os.environ.get("BT_RESULTS_DIR", ROOT / "results"))

START = os.environ.get("BT_START") or "2015-01-01"   # set BT_START to extend history
TRAIN_END = "2021-12-31"          # fit/explore on <= this, judge on after it

MIN_VALUE = 25_000                # (VIDEO) OpenInsider "Latest Insider Purchases 25k+"
RUN_CUTOFF_HOUR = 21.5            # (VIDEO) bot runs ~9:30pm ET; later filings roll to next run
REPEAT_LOOKBACK_DAYS = 30         # (VIDEO) "not bought by an insider in the past 30 days"
SPY_GAP_MAX = 0.005               # (VIDEO) skip if SPY opens outside +/-0.5% of prior close
ATR_LEN = 5                       # (VIDEO) thinkorswim ATR(5, Wilder's)
COST_PER_SIDE = 0.0015            # slippage + fees per side (assumption)
DAY_ALLOCATION = 0.33             # (VIDEO) 33% of account per day (50% in the LW example)

VARIANTS = {
    # Day Variant #2: any month, $30M+ volume, daily ATR 7-20%, stop -1.5x ATR, no TP, sell at close
    "day_v2": dict(kind="day", min_dvol=30e6, max_dvol=None, min_atr=0.07, max_atr=0.20,
                   months=None, min_matr=None, stop_atr_mult=1.5, spy_gate=True),
    # Day Variant #1: earnings-season months only, $30M-$100M volume (EST), daily ATR >= ~3% (EST)
    "day_v1_feb_may_aug_nov": dict(kind="day", min_dvol=30e6, max_dvol=100e6, min_atr=0.03,
                                   max_atr=None, months=[2, 5, 8, 11], min_matr=None,
                                   stop_atr_mult=1.5, spy_gate=True),
    "day_v1_post_earnings_8mo": dict(kind="day", min_dvol=30e6, max_dvol=100e6, min_atr=0.03,
                                     max_atr=None, months=[2, 3, 5, 6, 8, 9, 11, 12], min_matr=None,
                                     stop_atr_mult=1.5, spy_gate=True),
    # Swing: rules not shown. Monthly ATR >= ~30% (EST, LW at 27% failed, AEHR at 40% passed)
    **{f"swing_{n}d": dict(kind="swing", hold_days=n, min_dvol=30e6, max_dvol=None, min_atr=None,
                           max_atr=None, months=None, min_matr=0.30, stop_atr_mult=None,
                           spy_gate=False)
       for n in (5, 10, 20)},
}

# Values shown in the video's alerts, used to check our indicator definitions match his.
CALIBRATION = [
    # ticker, as-of date (signal day), daily ATR, monthly ATR, daily $ volume
    ("LW",   "2024-07-29", 0.0804, 0.2744, 270.4e6),
    ("KALU", "2024-07-29", 0.0659, 0.2116, 18.9e6),
    ("TCBI", "2024-07-29", 0.0362, 0.1210, 23.6e6),
    ("MXL",  "2024-07-30", 0.1652, 0.5338, 19.0e6),
    ("PMT",  "2024-07-30", 0.0251, 0.1034, 5.5e6),
    ("ALLE", "2024-07-30", 0.0287, 0.1080, 105.8e6),
    ("AEHR", "2024-07-30", 0.0761, 0.4000, 24.1e6),
]
# Known trade from the video: LW, Variant #2, entry 2024-07-30 open, exit at close.
KNOWN_TRADE = ("LW", "2024-07-30")

# Sensitivity grid for Variant #2 (run on the train period only)
GRID_MIN_ATR = [0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.10]
GRID_MIN_DVOL = [5e6, 10e6, 20e6, 30e6, 50e6, 100e6]
