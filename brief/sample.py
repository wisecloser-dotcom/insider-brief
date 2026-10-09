"""Made-up companies and people, so the page can be previewed without internet."""
import numpy as np
import pandas as pd


def _chart(seed, start_price, drift):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(end="2026-10-08", periods=127)
    close = start_price * np.exp(np.cumsum(rng.normal(drift, 0.022, len(days))))
    return days, close


def _trade(co, days, close, i, insider, position, code, shares_n, held_after, plan=False,
           headline=False, direct=True, cik="1"):
    price = float(close[i]) * 1.002
    value = price * shares_n
    before = held_after - shares_n if code == "P" else held_after + shares_n
    return {"accession": f"0000000000-26-{abs(hash((insider, i))) % 999999:06d}",
            "filing_date": str((days[min(i + 2, len(days) - 1)]).date()),
            "trade_date": str(days[i].date()), "url": "https://www.sec.gov/",
            "issuer_cik": co["cik"], "company": co["name"], "ticker": co["ticker"],
            "insider_cik": cik, "insider": insider, "position": position,
            "is_director": "Director" in position, "is_officer": "Chief" in position,
            "is_ten_pct": "10%" in position, "joint_filers": [],
            "side": "Buy" if code == "P" else "Sell", "code": code, "shares": shares_n,
            "price": price, "value": value, "owned_after": held_after, "direct": direct,
            "stake_change": (shares_n / before) * (1 if code == "P" else -1) if before > 0 else None,
            "new_position": code == "P" and before <= 0, "plan_10b5_1": plan,
            "held_after": held_after, "held_before": before, "headline": headline,
            "chart_price": price}


def intraday(cos: dict) -> dict:
    """Made-up hourly closes for the last 6 trading days, ending at each sample price."""
    out = {}
    for n, co in enumerate(cos.values()):
        rng = np.random.default_rng(40 + n)
        days = pd.bdate_range(end="2026-10-08", periods=6)
        times = [f"{d.date()} {h:02d}:30" for d in days for h in range(9, 16)]
        walk = np.exp(np.cumsum(rng.normal(0.0004, 0.006, len(times))))
        closes = co["price"] * walk / walk[-1]
        out[co["ticker"]] = list(zip(times, [float(c) for c in closes]))
    return out


def _ohlc(days, close, seed):
    rng = np.random.default_rng(seed)
    o = np.r_[close[0], close[:-1]] * np.exp(rng.normal(0, 0.006, len(close)))
    hi = np.maximum(o, close) * (1 + np.abs(rng.normal(0, 0.012, len(close))))
    lo = np.minimum(o, close) * (1 - np.abs(rng.normal(0, 0.012, len(close))))
    v = rng.lognormal(13, 0.5, len(close))
    r = lambda a: [round(float(x), 4) for x in a]
    return {"t": [str(d.date()) for d in days], "o": r(o), "h": r(hi), "l": r(lo), "c": r(close), "v": [int(x) for x in v]}


def companies():
    out = []
    specs = [
        ("Northwind Therapeutics, Inc.", "NWTX", "Pharmaceutical Preparations", "Nasdaq", 41_200_000, 18.0, -0.002),
        ("Harbor Grid Systems Corp", "HBGS", "Electric Services", "NYSE", 212_000_000, 64.0, 0.001),
    ]
    for n, (name, tk, ind, ex, so, p0, drift) in enumerate(specs):
        days, close = _chart(n + 3, p0, drift)
        co = {"cik": str(900000 + n), "ticker": tk, "name": name, "industry": ind, "exchange": ex,
              "price": float(close[-1]), "chg_6m": float(close[-1] / close[0] - 1),
              "adv": float(close[-1]) * (380_000 if n == 0 else 2_100_000),
              "shares_out": so, "market_cap": so * float(close[-1]),
              "chart": {"dates": [str(d.date()) for d in days], "close": [float(x) for x in close]},
              "edgar_url": "https://www.sec.gov/", "ohlc": _ohlc(days, close, 70 + n)}
        if n == 0:
            tr = [_trade(co, days, close, 124, "Rivera Elena M.", "Chief Executive Officer, Director", "P", 60_000, 1_840_000, headline=True, cik="11"),
                  _trade(co, days, close, 123, "Okafor Daniel", "Chief Financial Officer", "P", 12_000, 95_000, headline=True, cik="12"),
                  _trade(co, days, close, 101, "Lindqvist Petra", "Director", "P", 25_000, 410_000, cik="13"),
                  _trade(co, days, close, 118, "Patel Anika", "General Counsel", "S", 9_000, 40_000, cik="15"),
                  _trade(co, days, close, 40, "Chen Marcus", "Chief Medical Officer", "S", 8_000, 66_000, plan=True, cik="14")]
            tr[0]["wealth"] = {"total_after": 61_500_000, "trade_vs_holdings": 0.019, "holdings": [
                {"company": name, "ticker": tk, "shares": 1_840_000, "value": 1_840_000 * co["price"], "as_of": tr[0]["filing_date"], "price": co["price"]},
                {"company": "Meridian Bio Labs Inc", "ticker": "MBLX", "shares": 520_000, "value": 28_100_000, "as_of": "2026-05-14", "price": 54.0}]}
            tr[1]["wealth"] = {"total_after": tr[1]["held_after"] * co["price"], "trade_vs_holdings": 12_000 / 83_000, "holdings": [
                {"company": name, "ticker": tk, "shares": 95_000, "value": 95_000 * co["price"], "as_of": tr[1]["filing_date"], "price": co["price"]}]}
            eks = [{"date": "2026-09-30", "form": "8-K", "what": ["Executive or director change, or pay"], "url": "https://www.sec.gov/"},
                   {"date": "2026-08-12", "form": "8-K", "what": ["Earnings results"], "url": "https://www.sec.gov/"},
                   {"date": "2026-07-21", "form": "8-K", "what": ["Signed a material agreement", "Other material event"], "url": "https://www.sec.gov/"}]
            news = [{"date": "2026-10-02", "title": "Northwind's lead drug clears mid-stage trial goal, shares slide on safety questions", "source": "Example Wire", "url": "https://example.com"},
                    {"date": "2026-09-30", "title": "Northwind Therapeutics names new chief operating officer", "source": "Example Biotech News", "url": "https://example.com"},
                    {"date": "2026-08-12", "title": "Northwind posts wider quarterly loss as R&D spending rises", "source": "Example Markets", "url": "https://example.com"}]
        else:
            tr = [_trade(co, days, close, 124, "Abbott Graham J.", "President and Chief Operating Officer", "S", 150_000, 610_000, plan=False, headline=True, cik="21"),
                  _trade(co, days, close, 110, "Abbott Graham J.", "President and Chief Operating Officer", "S", 50_000, 760_000, plan=True, cik="21"),
                  _trade(co, days, close, 77, "Nakamura Rei", "Director", "S", 20_000, 140_000, plan=True, cik="22")]
            tr[0]["wealth"] = {"total_after": 610_000 * co["price"], "trade_vs_holdings": 150_000 / 760_000, "holdings": [
                {"company": name, "ticker": tk, "shares": 610_000, "value": 610_000 * co["price"], "as_of": tr[0]["filing_date"], "price": co["price"]}]}
            eks = [{"date": "2026-10-01", "form": "8-K", "what": ["Signed a material agreement"], "url": "https://www.sec.gov/"},
                   {"date": "2026-07-29", "form": "8-K", "what": ["Earnings results"], "url": "https://www.sec.gov/"}]
            news = [{"date": "2026-10-01", "title": "Harbor Grid wins multi-year transmission contract", "source": "Example Energy Daily", "url": "https://example.com"}]
        for t in tr:
            t["since_trade"] = co["price"] / t["chart_price"] - 1
            t["pct_mcap"] = t["value"] / co["market_cap"]
            t["pct_adv"] = t["value"] / co["adv"]
            t["held_value_after"] = t["held_after"] * co["price"]
            t["held_value_before"] = t["held_before"] * co["price"]
            t["pct_company_after"] = t["held_after"] / co["shares_out"]
        if n == 0:
            tr[0]["first_buy_label"], tr[0]["last_buy_note"] = "First buy in 4 yrs", "Last open-market buy here: Mar 2022"
            tr[1]["first_buy_label"], tr[1]["last_buy_note"] = "New insider, first buy", "First SEC filing as an insider: Jul 2026"
            tr[0]["earnings_tag"], tr[1]["earnings_tag"] = "Mid-quarter, 55 days after earnings", "Mid-quarter, 54 days after earnings"
            tr[0]["track"] = {"n_done": 3, "wins": 2, "avg": 0.094, "avg_vs_spy": 0.061, "buys": [
                {"date": "2022-03-14", "ticker": "NWTX", "company": name, "price": 21.4, "ret": 0.18, "spy": 0.04, "complete": True},
                {"date": "2021-05-03", "ticker": "MBLX", "company": "Meridian Bio Labs Inc", "price": 38.2, "ret": -0.04, "spy": 0.02, "complete": True},
                {"date": "2019-11-20", "ticker": "NWTX", "company": name, "price": 9.85, "ret": 0.14, "spy": 0.04, "complete": True}]}
            tr[1]["track"] = {"n_done": 0, "wins": 0, "avg": None, "avg_vs_spy": None, "buys": []}
        from .brief import cluster
        co["cluster"] = cluster(tr, today=days[-1].date())
        b = [t for t in tr if t["code"] == "P"]
        s = [t for t in tr if t["code"] == "S"]
        co.update(trades=sorted(tr, key=lambda t: t["trade_date"], reverse=True), eight_ks=eks, news=news,
                  totals={"buyers": len({t["insider"] for t in b}), "sellers": len({t["insider"] for t in s}),
                          "bought": sum(t["value"] for t in b), "sold": sum(t["value"] for t in s),
                          "sold_on_plan": sum(t["value"] for t in s if t["plan_10b5_1"])})
        out.append(co)
    return out
