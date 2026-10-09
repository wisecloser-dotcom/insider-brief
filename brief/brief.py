"""Put together everything shown for one company."""
from datetime import date, timedelta

import pandas as pd

from . import form4, market, news
from .config import Config
from .edgar import Edgar


def _key(t):
    return (t["accession"], t["code"])


def insider_wealth(ed: Edgar, cfg: Config, t: dict, price_now: float | None, cache: dict) -> dict:
    """Holdings disclosed in the insider's own SEC filings, valued at today's prices.
    A floor on wealth: excludes cash, property, private companies and unvested awards."""
    rows = ed.owner_holdings(t["insider_cik"], cfg.holdings_filings) if t["insider_cik"] else []
    rows = [r for r in rows if r["issuer_cik"] != t["issuer_cik"]]
    rows.insert(0, {"issuer_cik": t["issuer_cik"], "company": t["company"], "ticker": t["ticker"],
                    "shares": t["held_after"], "as_of": t["filing_date"]})
    total, priced = 0.0, []
    for r in rows:
        tk = r["ticker"]
        if r["issuer_cik"] == t["issuer_cik"]:
            p = price_now
        else:
            if tk and tk not in cache:
                cache[tk] = market.last_price(tk)
            p = cache.get(tk) if tk else None
        val = r["shares"] * p if (r["shares"] and p) else None
        priced.append({**r, "price": p, "value": val})
        total += val or 0
    before = total - t["value"] if t["code"] == "P" else total + t["value"]
    return {"holdings": priced, "total_after": total or None,
            "trade_vs_holdings": (t["value"] / before) if before and before > 0 else None}


_PX: dict = {}   # price histories fetched during this run, shared across companies


def px_hist(ticker: str):
    if ticker and ticker not in _PX:
        _PX[ticker] = market.history(ticker, months=61)
    return _PX.get(ticker)


def track_record(ed: Edgar, t: dict, horizon: int = 63, max_buys: int = 8) -> dict:
    """This person's earlier open-market buys (any company, last 5 years) and how each did
    over the next ~3 months (63 trading days), next to the S&P 500 (SPY) over the same days."""
    buys = ed.owner_buys(t["insider_cik"], t["trade_date"])[:max_buys]
    spy = px_hist("SPY")
    rows = []
    for b in buys:
        r = {**b, "ret": None, "spy": None, "complete": False}
        px = px_hist(b["ticker"]) if b["ticker"] else None
        if px is not None and len(px):
            i = int(px.index.searchsorted(pd.Timestamp(b["date"])))
            if i < len(px):
                entry = market.to_chart_scale(px, b["date"], b["price"])
                c0 = float(px["close"].iloc[i])
                if entry and c0 and abs(entry / c0 - 1) < 0.5:   # else the ticker/price data don't match
                    j = min(i + horizon, len(px) - 1)
                    r["complete"] = i + horizon < len(px)
                    r["ret"] = float(px["close"].iloc[j] / entry - 1)
                    r["exit_date"] = str(px.index[j].date())
                    if spy is not None and len(spy):
                        si = int(spy.index.searchsorted(px.index[i]))
                        sj = int(spy.index.searchsorted(px.index[j]))
                        if sj < len(spy) and si < len(spy):
                            r["spy"] = float(spy["close"].iloc[sj] / spy["close"].iloc[si] - 1)
        rows.append(r)
    done = [r for r in rows if r["complete"] and r["ret"] is not None]
    vs = [r["ret"] - r["spy"] for r in done if r["spy"] is not None]
    return {"buys": rows, "n_done": len(done),
            "avg": sum(r["ret"] for r in done) / len(done) if done else None,
            "avg_vs_spy": sum(vs) / len(vs) if vs else None,
            "wins": sum(1 for r in done if r["ret"] > 0)}


def earnings_tag(trade_date: str | None, earnings: list[str]) -> tuple[int | None, str | None]:
    """How long after the company's last earnings report (8-K item 2.02) the trade happened."""
    if not trade_date:
        return None, None
    prior = [d for d in earnings if d <= trade_date[:10]]
    if not prior:
        return None, None
    d = (date.fromisoformat(trade_date[:10]) - date.fromisoformat(max(prior))).days
    if d == 0:
        return 0, "On earnings day"
    if d <= 10:
        return d, f"{d} day{'s' if d > 1 else ''} after earnings"
    return d, f"Mid-quarter, {d} days after earnings"


CLUSTER_DAYS = 7   # 2+ different insiders buying the same stock within this many days


def cluster(trades: list[dict], days: int = CLUSTER_DAYS, today: date | None = None) -> dict:
    """Distinct insiders who bought on the open market in the last `days` days."""
    cut = str((today or date.today()) - timedelta(days=days))
    buys = [t for t in trades if t["code"] == "P" and (t.get("trade_date") or "") >= cut]
    names = list(dict.fromkeys(t["insider"] for t in buys))
    return {"n": len({t["insider_cik"] for t in buys}), "insiders": names, "days": days,
            "value": float(sum(t["value"] for t in buys))}


def first_buy_label(h: dict, trade_date: str, years: int = 5) -> tuple[str | None, str | None]:
    """(badge for the front page, note for the company page)."""
    td = date.fromisoformat(trade_date[:10])
    if h.get("prior_buy"):
        prior = date.fromisoformat(h["prior_buy"])
        gap = (td - prior).days / 365.25
        note = f"Last open-market buy here: {prior.day} {prior:%b %Y}"
        if gap >= 1:
            n = int(gap)
            return f"First buy in {n} yr{'s' if n > 1 else ''}", note
        return None, note
    first = h.get("first_filing")
    if first:
        since = (td - date.fromisoformat(first)).days / 365.25
        if since < 1:
            return "New insider, first buy", f"First SEC filing as an insider: {nice(first)}"
        if not h.get("complete", True):
            return None, "No earlier buy found in their recent filings"
        n = min(int(since), years)
        plus = "+" if since >= years else ""
        return (f"First buy in {n}{plus} yr{'s' if n > 1 else ''}",
                f"No open-market buy here in their filings since {nice(first)}"
                if since < years else f"No open-market buy here in {years}+ years of filings")
    return None, None


def nice(d: str) -> str:
    x = date.fromisoformat(d[:10])
    return f"{x:%b %Y}"


def company(ed: Edgar, cfg: Config, cik, ticker: str, name: str,
            headline: list[dict], related: list[dict] | None = None, log=print) -> dict:
    since = date.today() - timedelta(days=cfg.lookback_days)
    sub = ed.submissions(cik)
    px = market.history(ticker, months=61) if ticker else None   # 5 years for the candle chart
    s = market.summary(px)
    shares_out = ed.shares_outstanding(cik)
    mcap = shares_out * s["price"] if (shares_out and s["price"]) else None
    yf_mcap = market.market_cap_fallback(ticker) if ticker else None
    # SEC share counts can be in ordinary shares while the US price is per ADR, or stale
    # after a reverse split. If the two estimates disagree by more than 3x, trust Yahoo's.
    if yf_mcap and (not mcap or not (1 / 3 < mcap / yf_mcap < 3)):
        mcap = yf_mcap
        shares_out = yf_mcap / s["price"] if s["price"] else None

    if related is None:
        related = ed.company_trades(cik, since)
    seen = {_key(t) for t in related}
    head_keys = {_key(t) for t in headline}
    # when a fund reports one trade under several entities, keep the headline copy
    merged = related + [t for t in headline if _key(t) not in seen]
    related = form4.dedupe_joint(sorted(merged, key=lambda t: _key(t) not in head_keys))

    eight_ks = ed.eight_ks(cik, since)
    earnings = sorted(k["date"] for k in eight_ks if "Earnings results" in k["what"])
    price_cache = {}
    for t in related:
        t["earnings_days"], t["earnings_tag"] = earnings_tag(t.get("trade_date"), earnings)
        t["headline"] = _key(t) in head_keys
        t["pct_mcap"] = t["value"] / mcap if mcap else None
        t["pct_adv"] = t["value"] / s["adv"] if s["adv"] else None
        p = s["price"]
        t["held_value_after"] = t["held_after"] * p if (t["held_after"] and p) else None
        t["held_value_before"] = t["held_before"] * p if (t["held_before"] and p) else None
        t["pct_company_after"] = t["held_after"] / shares_out if (t["held_after"] and shares_out) else None
        # anything above 100% means the inputs don't match (wrong share class, bad filing)
        if t["pct_mcap"] and t["pct_mcap"] > 1:
            t["pct_mcap"] = None
        if t["pct_company_after"] and t["pct_company_after"] > 1:
            t["pct_company_after"] = None
        t["days_late"] = form4.days_late(t)
        t["chart_price"] = market.to_chart_scale(px, t["trade_date"], t["price"])
        t["since_trade"] = (s["price"] / t["chart_price"] - 1) if (s["price"] and t["chart_price"]) else None
    for t in related:
        if t["headline"]:
            t["wealth"] = insider_wealth(ed, cfg, t, s["price"], price_cache)
            if t["code"] == "P" and t.get("insider_cik") and t.get("trade_date"):
                h = ed.owner_buy_history(t["insider_cik"], cik, t["trade_date"], t["filing_date"])
                t["first_buy_label"], t["last_buy_note"] = first_buy_label(h, t["trade_date"])
                t["track"] = track_record(ed, t)

    df = pd.DataFrame(related, columns=["code", "insider_cik", "value", "plan_10b5_1"]
                      if not related else None)
    buys, sells = df[df["code"] == "P"], df[df["code"] == "S"]
    totals = {
        "buyers": int(buys["insider_cik"].nunique()) if len(buys) else 0,
        "sellers": int(sells["insider_cik"].nunique()) if len(sells) else 0,
        "bought": float(buys["value"].sum()) if len(buys) else 0.0,
        "sold": float(sells["value"].sum()) if len(sells) else 0.0,
        "sold_on_plan": float(sells.loc[sells["plan_10b5_1"], "value"].sum()) if len(sells) else 0.0,
    }
    chart = None
    if px is not None:
        c = px["close"]
        c = c[c.index >= c.index[-1] - pd.DateOffset(months=cfg.chart_months)]
        chart = {"dates": [d.strftime("%Y-%m-%d") for d in c.index], "close": [float(x) for x in c]}

    log(f"  {ticker or name}: {len(headline)} headline trade(s), {len(related)} in {cfg.lookback_days} days")
    return {
        "cik": str(int(cik)), "ticker": ticker, "name": sub.get("name") or name,
        "industry": sub.get("sicDescription") or "", "exchange": ", ".join(sub.get("exchanges") or []),
        "price": s["price"], "chg_6m": s["chg_6m"], "adv": s["adv"],
        "market_cap": mcap, "shares_out": shares_out,
        "trades": sorted(related, key=lambda t: (t["trade_date"] or "", t["filing_date"]), reverse=True),
        "totals": totals, "chart": chart, "cluster": cluster(related), "ohlc": market.ohlc(px),
        "eight_ks": eight_ks,
        "news": news.headlines(sub.get("name") or name, ticker, cfg.lookback_days, cfg.news_items),
        "edgar_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={int(cik)}&type=4&owner=only",
    }


def daily(ed: Edgar, cfg: Config, day: date, log=print) -> list[dict]:
    trades = ed.daily_trades(day, log)
    keep = [t for t in trades if t["ticker"] and (
        (t["code"] == "P" and t["value"] >= cfg.min_buy_usd) or
        (t["code"] == "S" and t["value"] >= cfg.min_sell_usd))]
    log(f"  {len(trades)} open-market trades, {len(keep)} pass the size filters")
    by_co = {}
    for t in keep:
        by_co.setdefault(t["issuer_cik"], []).append(t)
    order = sorted(by_co, key=lambda k: -sum(t["value"] for t in by_co[k]))[: cfg.max_companies]
    out = []
    for k in order:
        ts = by_co[k]
        try:
            out.append(company(ed, cfg, k, ts[0]["ticker"], ts[0]["company"], ts, log=log))
        except Exception as e:
            log(f"  skipped {ts[0]['ticker']}: {e}")
    return out


def single(ed: Edgar, cfg: Config, ticker: str, log=print) -> list[dict]:
    cik, name = ed.ticker_to_cik(ticker)
    related = ed.company_trades(cik, date.today() - timedelta(days=cfg.lookback_days))
    recent = [t for t in related if t["filing_date"] >= str(date.today() - timedelta(days=30))]
    headline = recent or sorted(related, key=lambda t: t["filing_date"], reverse=True)[:5]
    return [company(ed, cfg, cik, ticker.upper(), name, headline, related, log=log)]
