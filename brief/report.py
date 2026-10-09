"""The HTML brief: one self-contained page, opens in any browser, no internet needed to view."""
import html
import json
import re
from datetime import date

import numpy as np
import pandas as pd


def e(x) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    return html.escape(str(x))


def money(v) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "n/a"
    if v < 0:
        return "\u2212" + money(-v)
    if v == 0:
        return "$0"
    a = abs(v)
    for d, s in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if a >= d:
            return f"${v/d:.2f}{s}" if a / d < 10 else f"${v/d:.1f}{s}"
    if a >= 1e3:
        return f"${v/1e3:.0f}k"
    return f"${v:,.2f}"


def pct(v, signed=False, digits=None) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "n/a"
    if digits is None:
        a = abs(v)
        digits = 0 if a >= 0.1 else 1 if a >= 0.01 else 2 if a >= 0.001 else 3 if a >= 0.0001 else 4
    s = f"{v:+.{digits}%}" if signed else f"{v:.{digits}%}"
    return s.replace("-", "−")


def shares(v) -> str:
    if v is None:
        return "n/a"
    if abs(v) >= 1e6:
        return f"{v/1e6:.2f}M"
    return f"{v:,.0f}"


def nice_date(s) -> str:
    try:
        t = pd.Timestamp(str(s)[:10])
    except Exception:
        return "n/a"
    if pd.isna(t):
        return "n/a"
    return f"{t.day} {t:%b %Y}"


# ---------------------------------------------------------------------------
def page_name(ticker: str) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "_", ticker or "unknown")


def save_payload(t: dict, co: dict | None) -> str:
    """Snapshot stored when a trade is saved, so it still shows after it leaves the site."""
    tk = t.get("ticker") or (co or {}).get("ticker") or ""
    d = {"k": tkey(t), "tk": tk, "co": t.get("company") or (co or {}).get("name", ""),
         "who": t.get("insider", ""), "pos": t.get("position", ""), "s": t["code"],
         "v": round(float(t["value"]), 2), "td": (t.get("trade_date") or "")[:10],
         "fd": (t.get("filing_date") or "")[:10], "p": round(float(t.get("chart_price") or t["price"]), 4),
         "url": t.get("url", ""), "page": f"c/{page_name(tk)}.html#t={(t.get('trade_date') or '')[:10]}"}
    return html.escape(json.dumps(d, separators=(",", ":")), quote=True)


def save_button(t: dict, co: dict | None, text: bool = False) -> str:
    cls = "star txt" if text else "star"
    return (f'<button type=button class="{cls}" data-save="{save_payload(t, co)}" aria-pressed=false '
            f'aria-label="Save this trade" title="Save this trade">{"Save" if text else "&#9734;"}</button>')


def tkey(t: dict) -> str:
    """Stable id for one trade, shared by the chart markers and the rows they point to."""
    return re.sub(r"[^0-9A-Za-z]", "", str(t.get("accession", ""))) + str(t.get("code", ""))


def chart_svg(co: dict) -> str:
    ch = co["chart"]
    if not ch or len(ch["close"]) < 5:
        return "<p class=muted>No price history from Yahoo Finance for this ticker.</p>"
    W, H, L, R, T, B = 760, 250, 52, 20, 14, 28
    dates = pd.to_datetime(ch["dates"])
    y = np.array(ch["close"])
    marks = [t for t in co["trades"] if t.get("trade_date") and t.get("chart_price")
             and pd.Timestamp(str(t["trade_date"])[:10]) >= dates[0]]
    lo = min([y.min()] + [t["chart_price"] for t in marks])
    hi = max([y.max()] + [t["chart_price"] for t in marks])
    pad = (hi - lo) * 0.08 or hi * 0.05
    lo, hi = lo - pad, hi + pad
    X = lambda i: L + i * (W - L - R) / (len(y) - 1)
    Y = lambda v: T + (hi - v) * (H - T - B) / (hi - lo)
    pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(y))
    grid = []
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        grid.append(f'<line x1="{L}" x2="{W-R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" class=g />'
                    f'<text x="{L-8}" y="{Y(v)+4:.1f}" text-anchor=end>{money(v)}</text>')
    months = []
    for i, d in enumerate(dates):
        if i == 0 or d.month != dates[i - 1].month:
            if i > 3:
                months.append(f'<line x1="{X(i):.1f}" x2="{X(i):.1f}" y1="{H-B}" y2="{H-B+5}" class=a />'
                              f'<text x="{X(i):.1f}" y="{H-8}" text-anchor=middle>{d:%b}</text>')
    dots = []
    for t in sorted(marks, key=lambda t: t["headline"]):  # headline trades drawn on top
        i = min(int(dates.searchsorted(pd.Timestamp(str(t["trade_date"])[:10]))), len(y) - 1)
        x, yy = X(i), Y(t["chart_price"])
        r = 7 if t["headline"] else 5
        if t["code"] == "P":
            shape = f'<path d="M{x:.1f},{yy-r:.1f} L{x+r:.1f},{yy+r*.8:.1f} L{x-r:.1f},{yy+r*.8:.1f}Z" class="m buy{" hl" if t["headline"] else ""}"/>'
        else:
            shape = f'<path d="M{x:.1f},{yy+r:.1f} L{x+r:.1f},{yy-r*.8:.1f} L{x-r:.1f},{yy-r*.8:.1f}Z" class="m sell{" hl" if t["headline"] else ""}"/>'
        dots.append(f'<g class=mk data-k="{tkey(t)}" tabindex=0 role=button '
                    f'aria-label="Show {e(t["insider"])} {t["side"].lower()} on {nice_date(t["trade_date"])}">'
                    f'<title>{e(t["insider"])}: {t["side"].lower()} {money(t["value"])} '
                    f'on {nice_date(t["trade_date"])} at {money(t["chart_price"])}. Click to see the trade.</title>{shape}</g>')
    label = (f"Price of {co['ticker']} over the last 6 months, with {len(marks)} insider trades marked")
    return (f'<svg viewBox="0 0 {W} {H}" class=chart role=img aria-label="{e(label)}">'
            f'{"".join(grid)}{"".join(months)}'
            f'<polyline points="{pts}" class=line />{"".join(dots)}</svg>'
            '<p class=legend><span class="k buy"></span>Insider buy <span class="k sell"></span>Insider sell '
            '<span class="k ring"></span>Trades in this brief</p>')


def trade_block(t: dict, co: dict) -> str:
    buy = t["code"] == "P"
    w = t.get("wealth") or {}
    held_line = "n/a"
    if t["held_after"] is not None:
        before = (f"{shares(t['held_before'])} shares ({money(t['held_value_before'])})"
                  if t["held_before"] and t["held_before"] > 0 else "None")
        after = f"{shares(t['held_after'])} shares ({money(t['held_value_after'])})"
        held_line = f"{before} &rarr; {after}"
    stake = ("New position" if t["new_position"] else pct(t["stake_change"], signed=True))
    vs_hold = w.get("trade_vs_holdings")
    vs_text = ("n/a" if vs_hold is None else
               (f"Adds {pct(vs_hold)} to them" if buy else f"Cuts them by {pct(vs_hold)}"))
    hold_rows = "".join(
        f"<tr><td>{e(h['company'])}</td><td>{e(h['ticker'])}</td><td class=n>{shares(h['shares'])}</td>"
        f"<td class=n>{money(h['value']) if h['value'] else 'no price'}</td><td>{nice_date(h['as_of'])}</td></tr>"
        for h in w.get("holdings", []))
    flags = []
    if t.get("earnings_tag"):
        flags.append(t["earnings_tag"])
    if t.get("first_buy_label"):
        flags.append(t["first_buy_label"])
    if t.get("last_buy_note"):
        flags.append(t["last_buy_note"])
    if t["plan_10b5_1"]:
        flags.append("Pre-arranged 10b5-1 plan trade")
    if (t.get("days_late") or 0) > 10:
        flags.append(f"Filed {t['days_late']} days after the trade (the rule is 2 business days)")
    if not t["direct"]:
        flags.append("Held indirectly (trust, family or fund)")
    if t["joint_filers"]:
        flags.append("Filed jointly with " + ", ".join(t["joint_filers"][:3]))
    a = t["pct_adv"]
    adv_text = "n/a" if not a else (f"{a:.1f}x" if a >= 1 else f"{pct(a)} of")
    fields = [
        ("Amount", f"<b class=big>{money(t['value'])}</b><br>{shares(t['shares'])} shares at {money(t['price'])}"),
        ("Traded / filed", f"{nice_date(t['trade_date'])}<br><span class=muted>filed {nice_date(t['filing_date'])}</span>"),
        ("Price since the trade", (f"<b class='big {'up' if t['since_trade'] >= 0 else 'down'}'>{pct(t['since_trade'], signed=True)}</b>"
                                   f"<br><span class=muted>{money(t['chart_price'])} then, {money(co['price'])} now</span>")
                                  if t.get("since_trade") is not None else "n/a"),
        ("Share of company size", f"{pct(t['pct_mcap'])} of market cap<br><span class=muted>{adv_text} a typical day&rsquo;s trading</span>"),
        ("Holding in " + e(co["ticker"]), f"{held_line}<br><span class=muted>"
                                          f"{stake if t['new_position'] else 'Stake ' + stake}"
                                          f"{'; ' + pct(t['pct_company_after']) + ' of the company' if t['pct_company_after'] else ''}</span>"),
        ("Disclosed public holdings*", f"{money(w.get('total_after'))}<br><span class=muted>{vs_text}</span>"),
    ]
    return f"""
<article id="tb-{tkey(t)}" class="trade {'buy' if buy else 'sell'}" data-side="{t['code']}" data-value="{t['value']:.0f}" data-plan="{int(t['plan_10b5_1'])}">
 <header><span class=side>{t['side']}</span><h3>{e(t['insider'])}</h3><p>{e(t['position'])}</p>
  <span class=hdr-r>{save_button(t, co, text=True)}<a href="{e(t['url'])}">Form 4 on EDGAR</a></span></header>
 <dl>{''.join(f'<div><dt>{k}</dt><dd>{v}</dd></div>' for k, v in fields)}</dl>
 {'<p class=flags>' + ' / '.join(e(f) for f in flags) + '</p>' if flags else ''}
 {track_html(t)}
 {f'<details><summary>Where the holdings figure comes from</summary><table class=hold><tr><th>Company</th><th>Ticker</th><th class=n>Shares</th><th class=n>Value today</th><th>Latest filing</th></tr>{hold_rows}</table></details>' if hold_rows else ''}
</article>"""


def track_html(t: dict) -> str:
    """The insider's earlier buys, collapsed until clicked."""
    tr = t.get("track")
    if not tr or t["code"] != "P":
        return ""
    if not tr["buys"]:
        return ("<details class=track><summary>Their earlier buys</summary>"
                "<p>No other open-market buys in their last 5 years of SEC filings.</p></details>")
    head = f"{len(tr['buys'])} earlier buy{'s' if len(tr['buys']) != 1 else ''}"
    summ = ""
    if tr["n_done"]:
        summ = (f"<p class=trsum>After 3 months, {tr['wins']} of {tr['n_done']} were up. "
                f"Average {pct(tr['avg'], signed=True)}"
                + (f", {pct(tr['avg_vs_spy'], signed=True)} vs the S&amp;P 500" if tr["avg_vs_spy"] is not None else "")
                + ".</p>")
    rows = "".join(
        f"<tr><td>{nice_date(b['date'])}</td><td>{e(b['ticker'] or '')}</td><td>{e(b['company'])}</td>"
        f"<td class=n>{money(b['price'])}</td>"
        f"<td class=n>{'n/a' if b['ret'] is None else ('<span class=' + ('up' if b['ret'] >= 0 else 'down') + '>' + pct(b['ret'], signed=True) + '</span>' + ('' if b['complete'] else ' <span class=muted>so far</span>'))}</td>"
        f"<td class=n>{'n/a' if b['spy'] is None else pct(b['spy'], signed=True)}</td></tr>"
        for b in tr["buys"])
    return (f"<details class=track><summary>Their earlier buys: {head}</summary>{summ}"
            f"<table class=hold><tr><th>Bought</th><th>Ticker</th><th>Company</th><th class=n>Paid</th>"
            f"<th class=n>3 months later</th><th class=n>S&amp;P 500 same period</th></tr>{rows}</table>"
            f"<p class=muted>Uses closing prices 63 trading days after each buy. \"So far\" means "
            f"3 months haven't passed yet. n/a means no usable price history (often delisted).</p></details>")


def range_panel(co: dict) -> str:
    """52-week low/high bar shown beside the chart, with today's price and recent insider prices."""
    o = co.get("ohlc")
    if o and len(o.get("t", [])) >= 20:
        lo, hi, label = min(o["l"][-252:]), max(o["h"][-252:]), "52-week range"
    elif co.get("chart") and co["chart"].get("close"):
        lo, hi, label = min(co["chart"]["close"]), max(co["chart"]["close"]), "6-month range"
    else:
        return ""
    p = co.get("price")
    if not p or hi <= lo:
        return ""
    pos = lambda v: max(0.0, min(1.0, (v - lo) / (hi - lo)))
    where = pos(p)
    word = ("Near the 52-week low" if where <= 0.15 else "Near the 52-week high" if where >= 0.85
            else "Lower half of the range" if where < 0.5 else "Upper half of the range")
    if label != "52-week range":
        word = word.replace("52-week", "6-month")
    ticks = "".join(
        f'<i class="tick {"buy" if t["code"] == "P" else "sell"}" style="bottom:{pos(t["chart_price"]) * 100:.1f}%" '
        f'title="{e(t["insider"])} {t["side"].lower()} at {money(t["chart_price"])}"></i>'
        for t in co["trades"] if t.get("headline") and t.get("chart_price"))
    return f"""<aside class=r52 aria-label="{label}">
 <h4>{label}</h4><p class=r52w>{word}</p><p class=r52p>Now {money(p)} <span class=muted>(black line)</span></p>
 <div class=r52body><div class=r52bar><b class=r52now style="bottom:{where * 100:.1f}%"></b>{ticks}</div>
  <div class=r52lab><span>{money(hi)}<br><small>high</small></span>
   <span>{money(lo)}<br><small>low</small></span></div></div>
 <p class=muted>{pct(p / lo - 1)} above the low, {pct(1 - p / hi)} below the high.
 {'Ticks mark this week&rsquo;s insider prices.' if ticks else ''}</p>
</aside>"""


def mixed_note(co: dict, days: int = 30) -> str:
    """Flag when some insiders are selling while others are buying."""
    from datetime import date as _d, timedelta as _td
    cut = str(_d.today() - _td(days=days))
    recent = [t for t in co["trades"] if (t.get("trade_date") or "") >= cut]
    buyers = {t["insider_cik"] for t in recent if t["code"] == "P"}
    sells = [t for t in recent if t["code"] == "S"]
    sellers = {t["insider_cik"] for t in sells}
    if not buyers or not sellers:
        return ""
    sold = sum(t["value"] for t in sells)
    plan = sum(t["value"] for t in sells if t["plan_10b5_1"])
    bought = sum(t["value"] for t in recent if t["code"] == "P")
    names = list(dict.fromkeys(t["insider"] for t in sells))
    return (f"<p class='callout mixed'><b>Mixed signals:</b> {len(sellers)} insider{'s' if len(sellers) > 1 else ''} "
            f"sold {money(sold)} in the last {days} days"
            + (f" ({money(plan)} on pre-arranged 10b5-1 plans)" if plan else "")
            + f" while {len(buyers)} bought {money(bought)}. Sellers: {', '.join(e(n) for n in names[:4])}"
            + (" and others" if len(names) > 4 else "") + ".</p>")


def related_table(co: dict) -> str:
    tr = co["trades"]
    tot = co["totals"]
    def who(n, verb, none, amt):
        return none if n == 0 else f"{n} insider{'s' if n != 1 else ''} {verb} {money(amt)}"
    summ = ((lambda x: x[0].upper() + x[1:])(who(tot["buyers"], "bought", "no insider buys", tot["bought"])) + "; "
            + who(tot["sellers"], "sold", "no insider sales", tot["sold"])
            + (f" ({money(tot['sold_on_plan'])} of that on 10b5-1 plans)" if tot["sold_on_plan"] else "")
            + f". Net {money(tot['bought'] - tot['sold'])}.")
    if not tr:
        return f"<p>No open-market insider trades in the last 90 days.</p>"
    rows = "".join(
        f"<tr id=\"tr-{tkey(t)}\" class=\"{'hl ' if t['headline'] else ''}{'buy' if t['code']=='P' else 'sell'}\" data-side=\"{t['code']}\" data-value=\"{t['value']:.0f}\" data-plan=\"{int(t['plan_10b5_1'])}\">"
        f"<td>{nice_date(t['trade_date'])}</td><td>{e(t['insider'])}</td><td>{e(t['position'])}</td>"
        f"<td><span class=side>{t['side']}</span></td><td class=n>{money(t['value'])}</td>"
        f"<td class=n data-l='Held after'>{shares(t['held_after'])}</td><td>{'10b5-1 plan' if t['plan_10b5_1'] else ''}</td>"
        f"<td><a href=\"{e(t['url'])}\">Filing</a></td></tr>" for t in tr)
    return (f"<p class=summ>{summ}</p><div class=scroll><table class=rel><tr><th>Traded</th><th>Insider</th>"
            f"<th>Position</th><th></th><th class=n>Amount</th><th class=n>Shares held after</th>"
            f"<th>10b5-1 plan</th><th></th></tr>{rows}</table></div>")


def news_block(co: dict) -> str:
    ks = "".join(f"<li><span class=d>{nice_date(k['date'])}</span><a href=\"{e(k['url'])}\">{e('; '.join(k['what']))}</a></li>"
                 for k in co["eight_ks"]) or "<li class=muted>No 8-K filings in the last 90 days.</li>"
    ns = "".join(f"<li><span class=d>{nice_date(n['date'])}</span><span><a class=newslink href=\"{e(n['url'])}\">{e(n['title'])}</a>"
                 f" <span class=muted>{e(n['source'])}</span></span></li>" for n in co["news"]) \
        or "<li class=muted>No headlines found. Try searching the company name yourself.</li>"
    return (f"<div class=news><div><h4>Official company announcements (8-K)</h4><ul>{ks}</ul></div>"
            f"<div><h4>Headlines</h4><ul>{ns}</ul></div></div>")


def cluster_note(co: dict) -> str:
    c = co.get("cluster") or {}
    out = ""
    lst = lambda ns: ", ".join(e(n) for n in ns[:6]) + (" and others" if len(ns) > 6 else "")
    if c.get("n", 0) >= 2:
        out += (f"<p class=callout><b>Cluster buy:</b> {c['n']} separate insiders bought {money(c['value'])} "
                f"in the last {c.get('days', 7)} days ({lst(c['insiders'])}).</p>")
    if c.get("sell_n", 0) >= 2:
        out += (f"<p class='callout mixed'><b>Cluster sell:</b> {c['sell_n']} separate insiders sold "
                f"{money(c['sell_value'])} in the last {c.get('days', 7)} days, not counting pre-arranged "
                f"10b5-1 plan sales ({lst(c['sell_insiders'])}).</p>")
    return out


def ext_links(co: dict) -> str:
    """Quick links to the stock on Yahoo Finance and to live posts about it on X."""
    tk = co.get("ticker")
    if not tk:
        return ""
    from urllib.parse import quote
    yahoo = f"https://finance.yahoo.com/quote/{quote(tk)}"
    x = f"https://x.com/search?q={quote('$' + tk.replace('-', '.'))}&f=live"
    return (f"<p class=ext><a class=extlink href=\"{e(yahoo)}\">Yahoo Finance</a>"
            f"<a class=extlink href=\"{e(x)}\">${e(tk.replace('-', '.'))} on X</a></p>")


def company_section(co: dict, mode: str) -> str:
    head = [t for t in co["trades"] if t["headline"]]
    facts = [("Price", money(co["price"])), ("6-month change", pct(co["chg_6m"], signed=True)),
             ("Market cap", money(co["market_cap"])), ("Typical day&rsquo;s trading", money(co["adv"]))]
    title = {"daily": "Trades in this brief", "ticker": "Last 30 days",
             "site": "Trades in the last 7 days"}.get(mode, "Recent trades")
    return f"""
<section class=co id="co-{e(co['ticker'] or co['cik'])}">
 <div class=cohead><div><h2>{e(co['name'])} <span>{e(co['ticker'])}</span></h2>
  <p class=muted>{e(co['industry'])}{' / ' + e(co['exchange']) if co['exchange'] else ''} /
  <a href="{e(co['edgar_url'])}">All insider filings on EDGAR</a></p>{ext_links(co)}</div>
  <dl class=facts>{''.join(f'<div><dt>{k}</dt><dd>{v}</dd></div>' for k, v in facts)}</dl></div>
 {cluster_note(co)}{mixed_note(co)}
 <div class=chartrow><div class=chartmain>{chart_svg(co)}</div>{range_panel(co)}</div>
 <h4>{title}</h4>{''.join(trade_block(t, co) for t in head)}
 <h4>Everyone trading {e(co['ticker'])} in the last 90 days</h4>{related_table(co)}
 {news_block(co)}
</section>"""


def page(companies: list[dict], title: str, subtitle: str, mode: str = "daily",
         sample: bool = False) -> str:
    allt = [t for c in companies for t in c["trades"] if t["headline"]]
    bought = sum(t["value"] for t in allt if t["code"] == "P")
    sold = sum(t["value"] for t in allt if t["code"] == "S")
    idx = "".join(
        f"<tr data-side=\"{t['code']}\" data-value=\"{t['value']:.0f}\" data-plan=\"{int(t['plan_10b5_1'])}\">"
        f"<td><a href=\"#co-{e(c['ticker'] or c['cik'])}\">{e(c['ticker'])}</a></td><td>{e(c['name'])}</td>"
        f"<td>{e(t['insider'])}<br><span class=muted>{e(t['position'])}</span></td>"
        f"<td><span class=\"side {'buy' if t['code']=='P' else 'sell'}\">{t['side']}</span></td>"
        f"<td class=n>{money(t['value'])}</td><td class=n>{pct(t['pct_mcap'])}</td>"
        f"<td>{nice_date(t['trade_date'])}</td></tr>"
        for c in companies for t in c["trades"] if t["headline"])
    body = (f"""
<form name=filters class=filters aria-label="Filter trades">
 <label>Show <select name=side><option value=all>Buys and sells</option><option value=P>Buys only</option>
  <option value=S>Sells only</option></select></label>
 <label><input type=checkbox name=noplan> Hide 10b5-1 plan trades</label>
 <label>At least <select name=min><option value=0>Any amount</option><option value=100000>$100k</option>
  <option value=500000>$500k</option><option value=1000000>$1M</option><option value=5000000>$5M</option></select></label>
</form>
<div class=scroll><table class=index><tr><th>Ticker</th><th>Company</th><th>Insider</th><th></th>
<th class=n>Amount</th><th class=n>Of market cap</th><th>Traded</th></tr>{idx}</table></div>
{''.join(company_section(c, mode) for c in companies)}"""
            if companies else """<div class=empty><h2>No trades matched</h2><p>EDGAR had no open-market
insider buys or sells above your size filters for this day. The daily index appears after
the US close; if it's early, try again later, or lower <code>--min-buy</code> / <code>--min-sell</code>.</p></div>""")
    banner = ('<p class=sample>Sample page with made-up companies and people, to show the layout. '
              'Run the tool for real data.</p>') if sample else ""
    return f"""<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>{e(title)}</title>
<link rel=preconnect href=https://fonts.googleapis.com>
<link href="https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600;700;800&display=swap" rel=stylesheet>
<style>{CSS}</style><div class=wrap>{banner}
<header class=top><h1><small>{e(subtitle)}</small>{e(title)}</h1>
 <dl class=facts><div><dt>Companies</dt><dd>{len(companies)}</dd></div>
 <div><dt>Bought</dt><dd>{money(bought)}</dd></div><div><dt>Sold</dt><dd>{money(sold)}</dd></div></dl></header>
{body}
<footer><p>* Disclosed public holdings = shares the person reported in their own SEC filings (this
company plus other public companies they file for), valued at today&rsquo;s prices. It is a floor,
not net worth: cash, property, private companies, options and unvested awards are not included.</p>
<p>Sources: SEC EDGAR (Form 4, 8-K, shares outstanding), Yahoo Finance (prices), Google News
(headlines). Built {date.today().isoformat()}. Information only, not investment advice.</p></footer>
</div><script>{JS}</script></html>"""


CSS = """
:root{--paper:#EDF1F4;--sheet:#F8FAFB;--ink:#1B2A3A;--muted:#566676;--rule:#B9C5CF;--field:#D5DEE5;
--buy:#1F6B49;--buy-bg:#DCEEE4;--sell:#A13434;--sell-bg:#F5DEDE;--mark:#F6E35A;--focus:#2457A6}
@media (prefers-color-scheme:dark){:root{--paper:#121A22;--sheet:#18222C;--ink:#E2E8EE;--muted:#93A2B0;
--rule:#344454;--field:#26333F;--buy:#72CC9F;--buy-bg:#163327;--sell:#F29191;--sell-bg:#3A1C1F;
--mark:#6E6320;--focus:#8DB4F2}}
*{box-sizing:border-box}html{background:var(--paper);color:var(--ink)}
body{margin:0;font:15px/1.5 "Public Sans",system-ui,sans-serif;font-variant-numeric:tabular-nums}
a{color:inherit;text-decoration-color:var(--rule);text-underline-offset:3px}a:hover{text-decoration-color:currentColor}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
.wrap{max-width:1080px;margin:0 auto;padding:28px 16px 64px}
.muted{color:var(--muted)}
.sample{background:var(--mark);color:#1B2A3A;padding:8px 12px;margin:0 0 18px;font-weight:600}
header.top{display:grid;grid-template-columns:1fr auto;gap:12px 32px;align-items:end;
border-bottom:2px solid var(--ink);padding-bottom:16px}
h1{font-size:clamp(28px,4.4vw,44px);line-height:1.05;margin:0;font-weight:800;letter-spacing:-.02em}
h1 small{display:block;font-size:.42em;font-weight:500;letter-spacing:0;color:var(--muted);margin-bottom:6px}
dl{margin:0}dt{font-size:12px;color:var(--muted)}dd{margin:0;font-weight:600}
.facts{display:grid;grid-auto-flow:column;border:1px solid var(--rule);background:var(--sheet)}
.facts div{padding:7px 14px;border-left:1px solid var(--rule)}.facts div:first-child{border-left:0}
.facts dd{font-size:18px;font-weight:700}
.filters{display:flex;flex-wrap:wrap;gap:10px 24px;align-items:center;padding:14px 0;font-size:14px}
.filters label{display:flex;gap:8px;align-items:center}
select,input{font:inherit;color:inherit;accent-color:var(--ink)}
select{background:var(--sheet);border:1px solid var(--rule);padding:4px 8px}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:14px}
th{font-size:12px;font-weight:600;color:var(--muted);text-align:left;padding:6px 10px;border-bottom:1px solid var(--ink)}
td{padding:8px 10px;border-bottom:1px solid var(--field);vertical-align:top}
.n{text-align:right;white-space:nowrap}
.side{display:inline-block;font-size:12px;font-weight:700;padding:1px 7px;border-radius:2px}
.buy .side,.side.buy{background:var(--buy-bg);color:var(--buy)}
.sell .side,.side.sell{background:var(--sell-bg);color:var(--sell)}
section.co{margin-top:44px;padding-top:22px;border-top:2px solid var(--ink)}
.cohead{display:flex;flex-wrap:wrap;justify-content:space-between;gap:12px 24px;align-items:flex-start}
h2{margin:0;font-size:26px;line-height:1.15;font-weight:800}h2 span{color:var(--muted);font-weight:600}
.cohead p{margin:4px 0 0;font-size:14px}
h4{font-size:15px;margin:26px 0 8px;font-weight:700}
.chart{width:100%;height:auto;margin-top:18px;display:block}
.chart text{font-size:11px;fill:var(--muted)}.chart .g{stroke:var(--field)}.chart .a{stroke:var(--rule)}
.chart .line{fill:none;stroke:var(--ink);stroke-width:1.6;stroke-linejoin:round}
.mk{cursor:pointer}.mk:hover path,.mk:focus path{stroke:var(--ink);stroke-width:2}.mk:focus{outline:none}
.flash,tr.flash td{animation:flash 2.6s ease-out;outline:2px solid var(--mark);outline-offset:2px}
@keyframes flash{0%,35%{background-color:color-mix(in srgb,var(--mark) 70%,transparent)}100%{background-color:transparent}}
@media (prefers-reduced-motion:reduce){.flash,tr.flash td{animation:none;background-color:color-mix(in srgb,var(--mark) 40%,transparent)}}
.chartrow{display:grid;grid-template-columns:minmax(0,1fr) 170px;gap:18px;align-items:stretch;margin-top:18px}
.chartrow .chart,.chartrow .tvbox{margin-top:0}
.r52{border:1px solid var(--rule);background:var(--sheet);padding:12px 14px;display:flex;flex-direction:column}
.r52 h4{margin:0;font-size:12px;font-weight:600;color:var(--muted)}.r52w{margin:2px 0 2px;font-weight:700}.r52p{margin:0 0 12px;font-size:13px}
.r52body{flex:1;display:grid;grid-template-columns:14px 1fr;gap:10px;min-height:200px}
.r52bar{position:relative;background:linear-gradient(to top,var(--sell-bg),var(--field) 50%,var(--buy-bg));border:1px solid var(--rule)}
.r52now{position:absolute;left:-5px;right:-5px;height:4px;margin-bottom:-2px;background:var(--ink)}
.tick{position:absolute;left:-3px;width:8px;height:8px;margin-bottom:-4px;border-radius:50%;border:2px solid var(--sheet)}
.tick.buy{background:var(--buy)}.tick.sell{background:var(--sell);left:auto;right:-3px}
.r52lab{position:relative;display:flex;flex-direction:column;justify-content:space-between;font-size:13px;font-weight:600}
.r52lab small{font-weight:400;color:var(--muted)}
.r52cur{position:absolute;left:0;transform:translateY(50%);background:var(--sheet);padding:2px 0}
.r52 .muted{font-size:12px;margin:10px 0 0}
.ext{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 0}
.ext a{font-size:13px;font-weight:600;text-decoration:none;border:1px solid var(--rule);padding:3px 10px;background:var(--sheet)}
.ext a:hover{border-color:var(--ink)}
.callout.mixed{background:color-mix(in srgb,var(--sell-bg) 80%,transparent);border-left-color:var(--sell)}
details.track{border-top:1px solid var(--field);padding:8px 14px;font-size:14px}
details.track[open] summary{margin-bottom:6px}.trsum{margin:4px 0 6px;font-weight:600}
.hdr-r{margin-left:auto;display:flex;gap:16px;align-items:baseline}
.star{font:inherit;cursor:pointer;background:none;border:1px solid var(--rule);color:var(--muted);
padding:0 7px;line-height:1.5;font-size:15px}.star[aria-pressed=true]{color:#B8860B;border-color:#B8860B}
.star.txt{font-size:13px;padding:1px 10px}
@media (prefers-color-scheme:dark){.star[aria-pressed=true]{color:var(--mark-ink,#F6E35A);border-color:currentColor}}
@media (max-width:900px){.chartrow{grid-template-columns:1fr}.r52body{min-height:150px}}
@media (max-width:560px){table.hold{font-size:12px}table.hold th,table.hold td{padding:5px 4px}
 details.track table.hold th:nth-child(3),details.track table.hold td:nth-child(3),
 details:not(.track) table.hold th:nth-child(5),details:not(.track) table.hold td:nth-child(5){display:none}
 table.hold td{overflow-wrap:anywhere}table.hold .n{white-space:normal}}
@media (max-width:720px){article header .hdr-r{margin-left:0;width:100%}
 table.rel,table.rel tbody{display:block}table.rel tr:first-child{display:none}
 table.rel tr[id]{display:grid;grid-template-columns:1fr auto;gap:2px 12px;padding:10px 0;border-bottom:1px solid var(--field)}
 table.rel tr[id] td{border:0;padding:0;text-align:left}
 table.rel td:nth-child(5),table.rel td:nth-child(4){text-align:right}
 table.rel td:nth-child(6)::before{content:attr(data-l) " ";color:var(--muted);font-size:12px}}
.m.buy{fill:var(--buy)}.m.sell{fill:var(--sell)}.m.hl{stroke:var(--mark);stroke-width:3;paint-order:stroke}
.legend{font-size:12px;color:var(--muted);margin:4px 0 0}
.k{display:inline-block;width:10px;height:10px;margin:0 6px 0 14px;vertical-align:-1px}
.k:first-child{margin-left:0}.k.buy{background:var(--buy)}.k.sell{background:var(--sell)}
.k.ring{border:3px solid var(--mark);border-radius:50%}
article.trade{border:1px solid var(--rule);background:var(--sheet);margin:10px 0;border-left-width:5px}
article.buy{border-left-color:var(--buy)}article.sell{border-left-color:var(--sell)}
article header{display:flex;flex-wrap:wrap;gap:4px 14px;align-items:baseline;padding:12px 14px 8px}
article h3{margin:0;font-size:18px}article header p{margin:0;color:var(--muted)}
article header a{margin-left:auto;font-size:14px}
article dl{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));border-top:1px solid var(--field)}
article dl div{padding:10px 14px;border-right:1px solid var(--field)}article dd{font-weight:500}
.big{font-size:20px;font-weight:800}.up{color:var(--buy)}.down{color:var(--sell)}
.callout{margin:16px 0 0;padding:10px 14px;background:color-mix(in srgb,var(--mark) 45%,transparent);
border-left:5px solid var(--mark)}
.flags{margin:0;padding:8px 14px;border-top:1px solid var(--field);font-size:13px;color:var(--muted)}
details{border-top:1px solid var(--field);padding:8px 14px;font-size:14px}
summary{cursor:pointer;color:var(--muted)}table.hold{margin:8px 0}
.summ{margin:0 0 8px}
tr.hl td{background:color-mix(in srgb,var(--mark) 30%,transparent)}
.news{display:grid;grid-template-columns:1fr 1.4fr;gap:8px 32px}
.news ul{list-style:none;margin:0;padding:0;font-size:14px}
.news li{padding:6px 0;border-bottom:1px solid var(--field);display:grid;grid-template-columns:96px 1fr;gap:10px}
.news li.muted{display:block}
.news .d{color:var(--muted);white-space:nowrap}
.empty{padding:48px 0;max-width:62ch}
footer{margin-top:40px;color:var(--muted);font-size:13px;max-width:80ch;border-top:1px solid var(--rule)}
[hidden]{display:none!important}
@media (max-width:720px){header.top{grid-template-columns:1fr}.news{grid-template-columns:1fr}
 .facts{grid-auto-flow:row;grid-template-columns:1fr 1fr}.facts div:nth-child(3){border-left:0}
 .facts div:nth-child(n+3){border-top:1px solid var(--rule)}
 article header a{margin-left:0;width:100%}
 .index th:nth-child(2),.index td:nth-child(2),.index th:nth-child(7),.index td:nth-child(7){display:none}.news li{grid-template-columns:1fr;gap:0}}
"""

JS = """
const f=document.forms.filters;if(f){const apply=()=>{const side=f.side.value,min=+f.min.value,np=f.noplan.checked;
document.querySelectorAll('[data-side]').forEach(el=>{el.hidden=!((side==='all'||el.dataset.side===side)
&&+el.dataset.value>=min&&!(np&&el.dataset.plan==='1'))});};f.addEventListener('input',apply);apply();}
"""
