"""Download historical insider open-market purchases.

Primary source: OpenInsider screener (has the exact filing timestamp, which the
strategy needs to decide which evening's run a filing belongs to).
Fallback: SEC quarterly "Insider Transactions Data Sets" (filing date only, no time).

Output: data/insiders.parquet with one row per filing line:
    filing_dt (ET, naive), trade_date, ticker, insider, title, price, qty, value, delta_own, source
"""
import argparse
import io
import os
import re
import sys
import time
import zipfile
from datetime import date

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(__file__))
import config as C  # noqa: E402

OI_URL = "http://openinsider.com/screener"
SEC_URL = ("https://www.sec.gov/files/structureddata/data/"
           "insider-transactions-data-sets/{y}q{q}_form345.zip")


def _num(s):
    if pd.isna(s):
        return float("nan")
    s = str(s)
    if "new" in s.lower():
        return float("inf")
    s = re.sub(r"[^0-9.\-]", "", s.replace(">", ""))
    try:
        return float(s)
    except ValueError:
        return float("nan")


def _month_windows(start, end):
    cur = pd.Timestamp(start).replace(day=1)
    end = pd.Timestamp(end)
    while cur <= end:
        nxt = cur + pd.offsets.MonthBegin(1)
        yield cur, min(nxt - pd.Timedelta(days=1), end)
        cur = nxt


def _get(url, params=None, headers=None, tries=5):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=60)
            if r.status_code == 200:
                return r
            print(f"  HTTP {r.status_code}, retry {i + 1}")
        except requests.RequestException as e:
            print(f"  {e!r}, retry {i + 1}")
        time.sleep(5 * (i + 1))
    raise RuntimeError(f"failed: {url}")


def parse_openinsider_html(html, kind="purchases"):
    try:
        tables = pd.read_html(io.StringIO(html), attrs={"class": "tinytable"}, flavor="lxml")
    except Exception:  # noqa: BLE001 - no results table, or HTML lxml can't read
        return pd.DataFrame()
    if not tables:
        return pd.DataFrame()
    t = tables[0]
    t.columns = [str(c).replace("\xa0", " ").strip() for c in t.columns]
    col = {c.lower(): c for c in t.columns}

    def pick(*names):
        for n in names:
            if n in col:
                return t[col[n]]
        return pd.Series([None] * len(t))

    out = pd.DataFrame({
        "filing_dt": pd.to_datetime(pick("filing date"), errors="coerce"),
        "trade_date": pd.to_datetime(pick("trade date"), errors="coerce"),
        "ticker": pick("ticker").astype(str).str.strip().str.upper(),
        "insider": pick("insider name"),
        "title": pick("title"),
        "trade_type": pick("trade type").astype(str),
        "price": pick("price").map(_num),
        "qty": pick("qty").map(_num),
        "value": pick("value").map(_num),
        "delta_own": pick("δown", "Δown".lower(), "deltaown").map(_num),
    })
    if kind == "sales":
        out = out[out["trade_type"].str.strip() == "S - Sale"]   # plain open-market sales, not Sale+OE
    else:
        out = out[out["trade_type"].str.startswith("P")]
    return out.dropna(subset=["filing_dt", "ticker"])


def _table_rows(html):
    """Rows in the results table before type filtering (to know if another page exists)."""
    i = html.find('class="tinytable"')
    if i < 0:
        return 0
    j = html.find("</table>", i)
    return max(html.count("<tr", i, j if j > 0 else None) - 1, 0)


def fetch_openinsider(start, end, kind="purchases"):
    cache = C.DATA / ("openinsider" if kind == "purchases" else "openinsider_sales")
    cache.mkdir(parents=True, exist_ok=True)
    frames = []
    this_month = pd.Timestamp(date.today()).replace(day=1)
    for a, b in _month_windows(start, end):
        f = cache / f"{a:%Y-%m}.csv"
        if f.exists() and a < this_month:
            frames.append(pd.read_csv(f, parse_dates=["filing_dt", "trade_date"]))
            continue
        rows, page = [], 1
        while True:
            params = {"s": "", "o": "", "pl": "", "ph": "", "ll": "", "lh": "", "fd": "-1",
                      "fdr": f"{a:%m/%d/%Y} - {b:%m/%d/%Y}", "td": "0", "tdr": "",
                      "fdlyl": "", "fdlyh": "", "daysago": "",
                      **({"xp": "1"} if kind == "purchases" else {"xs": "1"}),
                      "vl": str(C.MIN_VALUE // 1000), "vh": "", "ocl": "", "och": "",
                      "sic1": "-1", "sicl": "100", "sich": "9999", "grp": "0",
                      "nfl": "", "nfh": "", "nil": "", "nih": "", "nol": "", "noh": "",
                      "v2l": "", "v2h": "", "oc2l": "", "oc2h": "", "sortcol": "0",
                      "cnt": "1000", "page": str(page)}
            # A page with no results table is either the end of the results (when the previous
            # page was exactly full) or an error/rate-limit page. After page 1, one confirming
            # retry is enough; page 1 must have results, so it gets a longer backoff.
            tries = 6 if page == 1 else 2
            r = None
            for attempt in range(tries):
                r = _get(OI_URL, params=params, headers={"User-Agent": "Mozilla/5.0"})
                if 'class="tinytable"' in r.text:
                    break
                if attempt < tries - 1:
                    wait = 30 * 2 ** attempt
                    print(f"  {a:%Y-%m} page {page}: no results table, retrying in {wait}s")
                    time.sleep(wait)
            else:
                if page == 1:
                    raise RuntimeError(f"OpenInsider kept returning pages without results for {a:%Y-%m}; "
                                       "months downloaded so far are cached - re-run to continue")
                print(f"  {a:%Y-%m}: page {page} is empty - end of results")
                break
            raw_n = _table_rows(r.text)
            df = parse_openinsider_html(r.text, kind)
            rows.append(df)
            time.sleep(1.5)
            if raw_n < 1000 or page >= 40:
                if page >= 40:
                    print(f"  WARNING {a:%Y-%m}: hit the 40-page cap, some {kind} may be missing")
                break
            page += 1
        df = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
        print(f"OpenInsider {a:%Y-%m}: {len(df)} {kind} lines")
        df.to_csv(f, index=False)
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    df["source"] = "openinsider"
    return df


def fetch_sec(start, end):
    """Fallback. Filing TIME is not in these files: we assume 17:00 ET (same-evening run).
    Note EDGAR dates filings accepted after 17:30 as the next business day, so some
    signals will enter one day later than the real strategy would."""
    ua = os.environ.get("SEC_USER_AGENT", "insider-brief backtest research (github.com/wisecloser-dotcom/insider-brief)")
    cache = C.DATA / "sec"
    cache.mkdir(parents=True, exist_ok=True)
    frames = []
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    for y in range(s.year, e.year + 1):
        for q in range(1, 5):
            qstart = pd.Timestamp(year=y, month=3 * q - 2, day=1)
            if qstart > e:
                break
            f = cache / f"{y}q{q}.zip"
            if not f.exists():
                try:
                    r = _get(SEC_URL.format(y=y, q=q), headers={"User-Agent": ua}, tries=3)
                except RuntimeError:
                    print(f"SEC {y}q{q}: not available yet")
                    continue
                f.write_bytes(r.content)
                time.sleep(1)
            with zipfile.ZipFile(f) as z:
                rd = lambda n: pd.read_csv(z.open(n), sep="\t", dtype=str, low_memory=False)  # noqa: E731
                sub = rd("SUBMISSION.tsv")
                own = rd("REPORTINGOWNER.tsv")
                tr = rd("NONDERIV_TRANS.tsv")
            tr = tr[(tr["TRANS_CODE"] == "P") & (tr["TRANS_ACQUIRED_DISP_CD"] == "A")].copy()
            tr["qty"] = pd.to_numeric(tr["TRANS_SHARES"], errors="coerce")
            tr["price"] = pd.to_numeric(tr["TRANS_PRICEPERSHARE"], errors="coerce")
            tr["value"] = tr["qty"] * tr["price"]
            g = tr.groupby("ACCESSION_NUMBER").agg(
                qty=("qty", "sum"), value=("value", "sum"), price=("price", "mean"),
                trade_date=("TRANS_DATE", "first")).reset_index()
            own1 = own.drop_duplicates("ACCESSION_NUMBER")
            m = g.merge(sub, on="ACCESSION_NUMBER").merge(own1, on="ACCESSION_NUMBER", how="left")
            m = m[m["DOCUMENT_TYPE"] == "4"]
            fd = pd.to_datetime(m["FILING_DATE"], format="%d-%b-%Y", errors="coerce")
            fd = fd.fillna(pd.to_datetime(m["FILING_DATE"], errors="coerce"))
            frames.append(pd.DataFrame({
                "filing_dt": fd + pd.Timedelta(hours=17),
                "trade_date": pd.to_datetime(m["trade_date"], format="%d-%b-%Y", errors="coerce"),
                "ticker": m["ISSUERTRADINGSYMBOL"].astype(str).str.strip().str.upper(),
                "insider": m.get("RPTOWNERNAME"), "title": m.get("RPTOWNER_TITLE"),
                "price": m["price"], "qty": m["qty"], "value": m["value"],
                "delta_own": float("nan")}))
            print(f"SEC {y}q{q}: {len(frames[-1])} purchase filings")
    df = pd.concat(frames, ignore_index=True)
    df["source"] = "sec"
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="openinsider", choices=["openinsider", "sec"])
    ap.add_argument("--start", default=C.START)
    ap.add_argument("--end", default=str(date.today()))
    ap.add_argument("--kind", default="purchases", choices=["purchases", "sales"])
    a = ap.parse_args()
    C.DATA.mkdir(parents=True, exist_ok=True)
    if a.kind == "sales":
        df = fetch_openinsider(a.start, a.end, kind="sales")
        df = df[df["value"].abs() >= C.MIN_VALUE]
        df = df[df["ticker"].str.fullmatch(r"[A-Z][A-Z0-9.\-]{0,6}", na=False)].drop_duplicates()
        df.to_parquet(C.DATA / "sales.parquet", index=False)
        print(f"saved {len(df)} sales, {df['ticker'].nunique()} tickers, "
              f"{df['filing_dt'].min()} -> {df['filing_dt'].max()}")
        return
    if a.source == "openinsider":
        try:
            df = fetch_openinsider(a.start, a.end)
        except Exception as e:  # noqa: BLE001
            print(f"OpenInsider failed ({e!r}); falling back to SEC data sets")
            df = fetch_sec(a.start, a.end)
    else:
        df = fetch_sec(a.start, a.end)
    df = df[df["value"] >= C.MIN_VALUE]
    df = df[df["ticker"].str.fullmatch(r"[A-Z][A-Z0-9.\-]{0,6}", na=False)]
    df = df.drop_duplicates()
    df.to_parquet(C.DATA / "insiders.parquet", index=False)
    print(f"saved {len(df)} purchases, {df['ticker'].nunique()} tickers, "
          f"{df['filing_dt'].min()} -> {df['filing_dt'].max()} (source: {df['source'].iloc[0]})")


if __name__ == "__main__":
    main()
