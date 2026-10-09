"""Everything we read from SEC EDGAR."""
from datetime import date, timedelta

import pandas as pd

from . import form4
from .sec import SecClient

ARCHIVES = "https://www.sec.gov/Archives/"
DAILY_INDEX = ARCHIVES + "edgar/daily-index/{y}/QTR{q}/form.{ymd}.idx"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SHARES_OUT = ("https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/"
              "dei/EntityCommonStockSharesOutstanding.json")
TICKERS = "https://www.sec.gov/files/company_tickers.json"

# 8-K item codes -> plain English (9.01 "exhibits" is left out on purpose)
ITEMS_8K = {
    "1.01": "Signed a material agreement", "1.02": "Ended a material agreement",
    "1.03": "Bankruptcy or receivership", "1.05": "Cybersecurity incident",
    "2.01": "Completed an acquisition or sale of assets", "2.02": "Earnings results",
    "2.03": "Took on new debt", "2.04": "Debt obligation accelerated",
    "2.05": "Restructuring or layoffs", "2.06": "Asset impairment",
    "3.01": "Exchange listing notice", "3.02": "Sold unregistered shares",
    "3.03": "Change to shareholder rights", "4.01": "Changed auditor",
    "4.02": "Past financials no longer reliable", "5.01": "Change in control",
    "5.02": "Executive or director change, or pay", "5.03": "Bylaws or fiscal year change",
    "5.07": "Shareholder vote results", "7.01": "Investor presentation or Reg FD disclosure",
    "8.01": "Other material event",
}


class Edgar:
    def __init__(self, client: SecClient):
        self.c = client
        self._tickers = None

    # ---- lookups ---------------------------------------------------------
    def ticker_to_cik(self, ticker: str) -> tuple[int, str]:
        if self._tickers is None:
            raw = self.c.get_json(TICKERS, max_age_hours=24 * 7)
            self._tickers = {v["ticker"].upper(): (int(v["cik_str"]), v["title"]) for v in raw.values()}
        t = ticker.upper().replace(".", "-")
        if t not in self._tickers:
            raise SystemExit(f"{ticker} isn't in the SEC's ticker list. Check the symbol.")
        return self._tickers[t]

    def submissions(self, cik) -> dict:
        return self.c.get_json(SUBMISSIONS.format(cik=int(cik)), max_age_hours=6)

    def recent_filings(self, cik) -> pd.DataFrame:
        r = self.submissions(cik)["filings"]["recent"]
        df = pd.DataFrame({k: r.get(k, []) for k in ("accessionNumber", "filingDate", "form",
                                                     "primaryDocument", "items")})
        df["filingDate"] = pd.to_datetime(df["filingDate"])
        return df

    def shares_outstanding(self, cik) -> float | None:
        """Latest shares outstanding the company reported to the SEC (all classes)."""
        try:
            j = self.c.get_json(SHARES_OUT.format(cik=int(cik)), max_age_hours=24 * 7)
        except Exception:
            return None
        rows = pd.DataFrame(j.get("units", {}).get("shares", []))
        if rows.empty:
            return None
        rows = rows[rows["filed"] == rows["filed"].max()]
        rows = rows[rows["end"] == rows["end"].max()]
        return float(rows["val"].drop_duplicates().sum())

    # ---- Form 4s ---------------------------------------------------------
    def filing(self, cik, accession: str) -> dict | None:
        path = f"edgar/data/{int(cik)}/{accession.replace('-', '')}/{accession}.txt"
        return self.filing_by_path(path)

    def filing_by_path(self, path: str) -> dict | None:
        xml = form4.extract_xml(self.c.get_text(ARCHIVES + path))
        return form4.parse(xml) if xml else None

    @staticmethod
    def index_url(cik, accession) -> str:
        return (f"{ARCHIVES}edgar/data/{int(cik)}/{accession.replace('-', '')}/"
                f"{accession}-index.htm")

    def daily_form4_list(self, day: date) -> pd.DataFrame:
        url = DAILY_INDEX.format(y=day.year, q=(day.month - 1) // 3 + 1, ymd=day.strftime("%Y%m%d"))
        text = self.c.get_text(url)
        rows = []
        for line in text.splitlines():
            parts = line.split()
            if parts and parts[0] in ("4", "4/A") and parts[-1].endswith(".txt"):
                rows.append({"form": parts[0], "path": parts[-1],
                             "accession": parts[-1].rsplit("/", 1)[-1][:-4]})
        # each filing is listed once per company involved (issuer and owner)
        return pd.DataFrame(rows).drop_duplicates("accession") if rows else pd.DataFrame()

    def daily_trades(self, day: date, log=print) -> list[dict]:
        idx = self.daily_form4_list(day)
        log(f"  {len(idx)} Form 4 filings on EDGAR for {day}")
        trades = []
        for i, r in enumerate(idx.itertuples(), 1):
            try:
                f = self.filing_by_path(r.path)
            except Exception as e:
                log(f"    skipped {r.accession}: {e}")
                continue
            if f:
                cik = r.path.split("/")[2]
                trades += form4.open_market_trades(f, r.accession, str(day),
                                                   self.index_url(cik, r.accession))
            if i % 250 == 0:
                log(f"    read {i}/{len(idx)}")
        return trades

    def company_trades(self, cik, since: date) -> list[dict]:
        """Every open-market insider buy/sell at a company since a date."""
        df = self.recent_filings(cik)
        df = df[df["form"].isin(["4", "4/A"]) & (df["filingDate"] >= pd.Timestamp(since))]
        trades = []
        for r in df.itertuples():
            try:
                f = self.filing(cik, r.accessionNumber)
            except Exception:
                continue
            if f and f["issuer_cik"] == str(int(cik)):   # skip filings where it's the *owner*
                trades += form4.open_market_trades(f, r.accessionNumber, str(r.filingDate.date()),
                                                   self.index_url(cik, r.accessionNumber))
        return trades

    def owner_holdings(self, owner_cik, max_filings=25) -> list[dict]:
        """An insider's latest disclosed share count in each public company they file for."""
        try:
            df = self.recent_filings(owner_cik)
        except Exception:
            return []
        df = df[df["form"].isin(["3", "4", "5", "4/A"])
                & (df["filingDate"] >= pd.Timestamp.today() - pd.Timedelta(days=730))]
        latest = {}
        for r in df.head(max_filings).itertuples():   # newest first
            try:
                f = self.filing(owner_cik, r.accessionNumber)
            except Exception:
                continue
            if not f or f["issuer_cik"] in latest:
                continue
            latest[f["issuer_cik"]] = {"issuer_cik": f["issuer_cik"], "company": f["issuer_name"],
                                       "ticker": f["ticker"], "shares": form4.holdings(f),
                                       "as_of": str(r.filingDate.date())}
        return list(latest.values())

    def eight_ks(self, cik, since: date) -> list[dict]:
        df = self.recent_filings(cik)
        df = df[df["form"].isin(["8-K", "8-K/A"]) & (df["filingDate"] >= pd.Timestamp(since))]
        out = []
        for r in df.itertuples():
            codes = [c.strip() for c in str(r.items or "").split(",") if c.strip()]
            what = [ITEMS_8K[c] for c in codes if c in ITEMS_8K] or ["Filing with exhibits only"]
            out.append({"date": str(r.filingDate.date()), "form": r.form, "what": what,
                        "url": self.index_url(cik, r.accessionNumber)})
        return out


def last_weekday(today: date | None = None) -> date:
    """The US session that most recently closed, as seen from Singapore in the morning."""
    d = (today or date.today()) - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d
