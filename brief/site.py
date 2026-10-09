"""The self-updating website.

Each run (every 30 minutes on GitHub Actions):
  1. update()  reads EDGAR's live Form 4 feed (plus the daily index for yesterday, to fill
               any gaps) and adds new open-market trades to data/state/trades.json
  2. build()   picks trades filed in the last 7 days that pass the size filters,
               enriches each company (cached, refreshed every few hours or when a new
               trade arrives), and writes static pages into the output folder:
                 index.html          latest trades, newest first, with filters and search
                 c/<TICKER>.html     the full company brief
                 status.json         when the data last changed (pages poll this)
"""
import json
import re
import shutil
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from lxml import etree

from . import brief, form4, market, report
from .config import Config
from .edgar import Edgar, last_weekday
from .report import e, money, nice_date, pct, shares

FEED = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&company=&dateb="
        "&owner=include&start={start}&count=100&output=atom")
SGT = timezone(timedelta(hours=8))
CACHE_VERSION = 4   # bump to rebuild every cached company page after a logic change


def page_name(ticker: str) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "_", ticker or "unknown")


def warn(msg):
    """Shows up as a warning on the GitHub Actions run page."""
    print(f"::warning::{msg}")


# ---------------------------------------------------------------------------
class State:
    def __init__(self, cfg: Config):
        self.dir = cfg.data_dir / "state"
        (self.dir / "companies").mkdir(parents=True, exist_ok=True)
        self.trades = self._load("trades.json", [])
        for t in self.trades:   # older runs may have stored dates with a timezone suffix
            t["trade_date"] = form4._date(t.get("trade_date"))
        self.seen = self._load("seen.json", {})          # accession -> filing date
        self.days_done = set(self._load("days_done.json", []))

    def _load(self, name, default):
        p = self.dir / name
        return json.loads(p.read_text()) if p.exists() else default

    def save(self, keep_days=120):
        cut = str(date.today() - timedelta(days=keep_days))
        self.trades = [t for t in self.trades if t["filing_date"] >= cut]
        self.seen = {a: d for a, d in self.seen.items() if d >= cut}
        for name, obj in (("trades.json", self.trades), ("seen.json", self.seen),
                          ("days_done.json", sorted(self.days_done)[-60:])):
            tmp = self.dir / (name + ".tmp")
            tmp.write_text(json.dumps(obj, default=str))
            tmp.replace(self.dir / name)

    def company_path(self, cik) -> Path:
        return self.dir / "companies" / f"{int(cik)}.json"


def _add_filing(ed: Edgar, st: State, cik: str, accession: str, filing_date: str) -> int:
    if accession in st.seen:
        return 0
    st.seen[accession] = filing_date
    try:
        f = ed.filing(cik, accession)
    except Exception:
        st.seen.pop(accession, None)   # retry next run
        return 0
    if not f:
        return 0
    new = form4.open_market_trades(f, accession, filing_date, ed.index_url(cik, accession))
    st.trades += new
    return len(new)


def update(ed: Edgar, cfg: Config, st: State, backfill_days: int = 3, log=print) -> int:
    added = 0
    # 1) live feed, newest first, until we reach filings we've already seen
    for page in range(30):
        xml = ed.c.get_bytes(FEED.format(start=page * 100), max_age_hours=0)
        root = etree.fromstring(xml, parser=etree.XMLParser(recover=True))
        ns = {"a": "http://www.w3.org/2005/Atom"}
        entries = root.findall("a:entry", ns)
        if not entries:
            break
        fresh = 0
        for en in entries:
            acc = re.search(r"accession-number=([\d-]+)", en.findtext("a:id", "", ns) or "")
            link = en.find("a:link", ns)
            m = re.search(r"/data/(\d+)/", link.get("href", "") if link is not None else "")
            if not acc or not m:
                continue
            if acc.group(1) in st.seen:
                continue
            fresh += 1
            day = (en.findtext("a:updated", "", ns) or "")[:10] or str(date.today())
            added += _add_filing(ed, st, m.group(1), acc.group(1), day)
        if fresh == 0:
            break
    # 2) daily index for recent weekdays not yet done (fills anything the feed missed)
    days, d = [], last_weekday()
    while len(days) < (backfill_days if not st.days_done else 2):
        days.append(d)
        d = last_weekday(d)
    for d in reversed(days):
        if str(d) in st.days_done:
            continue
        try:
            idx = ed.daily_form4_list(d)
        except Exception:
            continue               # not posted yet
        log(f"  daily index {d}: {len(idx)} Form 4s")
        for r in idx.itertuples():
            added += _add_filing(ed, st, r.path.split("/")[2], r.accession, str(d))
        st.days_done.add(str(d))
    log(f"  {added} new open-market trades")
    return added


# ---------------------------------------------------------------------------
def _featured(st: State, cfg: Config, window_days: int) -> list[dict]:
    cut = str(date.today() - timedelta(days=window_days))
    for t in st.trades:
        t["ticker"] = form4.clean_ticker(t.get("ticker"))
    # the trade itself must be recent, not just the filing (late filings of old trades drop out)
    keep = [dict(t, joint_filers=list(t.get("joint_filers") or [])) for t in st.trades
            if t["ticker"] and t["filing_date"] >= cut and (t.get("trade_date") or "") >= cut and (
                (t["code"] == "P" and t["value"] >= cfg.min_buy_usd) or
                (t["code"] == "S" and t["value"] >= cfg.min_sell_usd))]
    return form4.dedupe_joint(sorted(keep, key=lambda t: (t["filing_date"], t["accession"])))


def _key(t):
    return f"{t['accession']}|{t['code']}"


def enrich(ed: Edgar, cfg: Config, st: State, featured: list[dict], max_enrich: int,
           max_age_hours: float = 6, log=print) -> dict:
    by_co = {}
    for t in featured:
        by_co.setdefault(t["issuer_cik"], []).append(t)
    order = sorted(by_co, key=lambda k: -max(t["value"] for t in by_co[k]))
    out, done = {}, 0
    for cik in order:
        heads = by_co[cik]
        p = st.company_path(cik)
        cached = json.loads(p.read_text()) if p.exists() else None
        want = {_key(t) for t in heads}
        fresh = (cached and cached.get("_v") == CACHE_VERSION
                 and time.time() - cached["_built"] < max_age_hours * 3600
                 and want <= set(cached["_heads"]))
        if not fresh and done < max_enrich:
            try:
                co = brief.company(ed, cfg, cik, heads[0]["ticker"], heads[0]["company"],
                                   [dict(t) for t in heads], log=log)
                co["_built"], co["_heads"], co["_v"] = time.time(), sorted(want), CACHE_VERSION
                p.write_text(json.dumps(co, default=str))
                cached = co
                done += 1
            except Exception as err:
                log(f"  {heads[0]['ticker']}: {err}")
        if cached:
            # trades that left the 7-day window are no longer headline trades
            for t in cached["trades"]:
                t["trade_date"] = form4._date(t.get("trade_date"))
                t["headline"] = _key(t) in want
            out[cik] = cached
    log(f"  enriched {done} companies this run, {len(out)} ready, "
        f"{len(by_co) - len(out)} waiting for the next run")
    return out


# ---------------------------------------------------------------------------
JS_SITE = r"""
const f=document.forms.feed;
if(f){const KEY='ib-filters';let saved={};try{saved=JSON.parse(localStorage.getItem(KEY)||'{}')}catch(_){}
for(const [k,v] of Object.entries(saved)){const el=f.elements[k];if(!el)continue;if(el.type==='checkbox')el.checked=v;else el.value=v}
const rows=[...document.querySelectorAll('tr[data-side]')],days=[...document.querySelectorAll('tbody[data-day]')];
const apply=()=>{const side=f.side.value,min=+f.min.value,np=f.noplan.checked,role=f.role.value,sig=f.sig.value,q=f.q.value.trim().toLowerCase();
 let n=0;rows.forEach(r=>{const ok=(side==='all'||r.dataset.side===side)&&+r.dataset.value>=min&&!(np&&r.dataset.plan==='1')
  &&(role==='all'||r.dataset.role.includes(role))&&(sig==='all'||r.dataset.sig.includes(sig))&&(!q||r.dataset.q.includes(q));r.hidden=!ok;if(ok)n++});
 days.forEach(d=>{d.hidden=![...d.querySelectorAll('tr[data-side]')].some(r=>!r.hidden)});
 document.getElementById('count').textContent=n===rows.length?`${n} trades`:`${n} of ${rows.length} trades`;
 document.getElementById('none').hidden=n>0;
 try{localStorage.setItem(KEY,JSON.stringify({side:f.side.value,min:f.min.value,noplan:f.noplan.checked,role:f.role.value,sig:f.sig.value}))}catch(_){}};
f.addEventListener('input',apply);apply();
document.querySelectorAll('button.show').forEach(b=>b.addEventListener('click',()=>{
 const want=JSON.parse(b.dataset.f);['side','role','sig','min'].forEach(k=>{f.elements[k].value=want[k]||(k==='min'?'0':'all')});
 f.q.value='';apply();document.querySelector('table.feed').scrollIntoView({behavior:'smooth',block:'start'})}));}
const built=+document.body.dataset.built;
async function check(){try{const r=await fetch('STATUS?'+Date.now(),{cache:'no-store'});const s=await r.json();
 if(s.built>built)document.getElementById('fresh').hidden=false}catch(_){}}
setInterval(check,5*60*1000);
"""

SITE_CSS = """
.status{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:13px;color:var(--muted);margin:10px 0 0}
#fresh{position:sticky;top:0;z-index:5;background:var(--mark);color:#1B2A3A;padding:10px 14px;
font-weight:600;display:flex;gap:12px;align-items:center}
#fresh button{font:inherit;background:#1B2A3A;color:#fff;border:0;padding:4px 12px;cursor:pointer}
.filters input[type=search]{font:inherit;color:inherit;background:var(--sheet);border:1px solid var(--rule);
padding:4px 8px;min-width:220px}
#count{margin-left:auto;color:var(--muted)}
table.feed td{vertical-align:middle}
table.feed .tk a{font-weight:800;font-size:16px}
table.feed td:nth-child(3){min-width:190px}table.feed td:nth-child(2){min-width:130px}
tbody[data-day] th.day{font-size:14px;color:var(--ink);padding-top:22px;border-bottom:2px solid var(--ink)}
.back{display:inline-block;margin-bottom:4px;font-size:14px}
section.co:first-of-type{margin-top:10px}
.plan,.late{font-size:12px;color:var(--muted);white-space:nowrap}.late{color:var(--sell)}
.strip{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--rule);background:var(--sheet);margin:18px 0 4px}
.sc{padding:12px 14px;border-left:1px solid var(--rule);display:flex;flex-direction:column;gap:2px}
.sc:first-child{border-left:0}.sc h3{margin:0;font-size:12px;font-weight:600;color:var(--muted)}
.sc .sv{margin:0;font-size:22px;font-weight:800;line-height:1.2}.sc .sl{margin:2px 0 0;font-size:13px;line-height:1.6}
.sc .sl a{font-weight:700}.show{margin-top:auto;align-self:flex-start;font:inherit;font-size:13px;color:var(--ink);
background:none;border:1px solid var(--rule);padding:3px 10px;cursor:pointer}.show:hover{border-color:var(--ink)}
.sp{display:inline-flex;align-items:center;gap:8px;justify-content:flex-end}
.spark polyline{fill:none;stroke-width:1.6;stroke-linejoin:round}.spark .base{stroke:var(--muted);stroke-dasharray:2 2;stroke-width:1}
.spark.up polyline{stroke:var(--buy)}.spark.down polyline{stroke:var(--sell)}.spark.up circle{fill:var(--buy)}.spark.down circle{fill:var(--sell)}
.tvbox{margin-top:18px;border:1px solid var(--rule);background:var(--sheet)}
.tvbar{display:flex;flex-wrap:wrap;gap:8px 18px;align-items:flex-start;padding:10px 12px;border-bottom:1px solid var(--field)}
.ranges{display:flex;border:1px solid var(--rule)}
.ranges button{font:inherit;font-size:13px;font-weight:600;color:var(--muted);background:none;border:0;
border-left:1px solid var(--rule);padding:4px 11px;cursor:pointer}.ranges button:first-child{border-left:0}
.ranges button[aria-pressed=true]{background:var(--ink);color:var(--sheet)}
.tvlegend{font-size:13px;line-height:1.5;font-variant-numeric:tabular-nums;min-height:20px}
.tvlegend .up{color:var(--buy);font-weight:600}.tvlegend .down{color:var(--sell);font-weight:600}
.tv{height:440px}
@media (max-width:720px){.tv{height:340px}}
.badge{display:inline-block;font-size:12px;font-weight:700;padding:0 6px;margin-top:3px;white-space:nowrap}
.badge.cl{background:var(--mark);color:#1B2A3A}.badge.fb{border:1px solid currentColor;color:var(--ink)}
table.feed .up{color:var(--buy);font-weight:600}table.feed .down{color:var(--sell);font-weight:600}
@media (max-width:720px){#count{margin-left:0;width:100%}
 .strip{grid-template-columns:1fr 1fr}.sc:nth-child(3){border-left:0}.sc:nth-child(n+3){border-top:1px solid var(--rule)}
 .feed th:nth-child(2),.feed td:nth-child(2),.feed th:nth-child(8),.feed td:nth-child(8),
 .feed th:nth-child(9),.feed td:nth-child(9),.feed th:nth-child(10),.feed td:nth-child(10){display:none}}
table.feed td:nth-child(9),table.feed td:nth-child(10){white-space:nowrap}
@media (max-width:720px){
 /* phones: each trade becomes two lines: who and what / amount, stake, move since */
 table.feed,table.feed tbody{display:block}table.feed thead{display:none}
 tbody[data-day]>tr:first-child{display:block}tbody[data-day] th.day{display:block;padding-left:0}
 table.feed tr[data-side]{display:grid;grid-template-columns:auto 1fr auto;
  grid-template-areas:"tk who side" "amt stake since";gap:6px 14px;padding:12px 0;border-bottom:1px solid var(--field)}
 table.feed tr[data-side]>td{border:0;padding:0;text-align:left}
 table.feed td:nth-child(1){grid-area:tk}table.feed td:nth-child(3){grid-area:who}
 table.feed td:nth-child(4){grid-area:side;text-align:right}table.feed td:nth-child(5){grid-area:amt}
 table.feed td:nth-child(6){grid-area:stake}table.feed td:nth-child(7){grid-area:since;text-align:right}
 table.feed td:nth-child(6)::before{content:"Stake ";font-size:12px;color:var(--muted);font-weight:400}
 table.feed td:nth-child(7)::before{content:"Since ";font-size:12px;color:var(--muted);font-weight:400}}
"""


def shell(title, subtitle, facts, body, built_ts, status_url, script=True, note="",
          compact=False) -> str:
    when = datetime.fromtimestamp(built_ts, SGT)
    facts_html = "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in facts)
    status = (f"Updated {when.day} {when:%b %Y, %H:%M} Singapore time. "
              f"Checks EDGAR every 30 minutes. {e(note)}")
    head = (f"<p class=status style='margin:0 0 6px'>{status}</p>" if compact else
            f"<header class=top><h1><small>{e(subtitle)}</small>{e(title)}</h1>"
            f"<dl class=facts>{facts_html}</dl><p class=status>{status}</p></header>")
    return f"""<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>{e(title)}</title>
<base target=_blank>
<link rel=preconnect href=https://fonts.googleapis.com>
<link href="https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600;700;800&display=swap" rel=stylesheet>
<style>{report.CSS}{SITE_CSS}</style>
<body data-built="{int(built_ts)}">
<div id=fresh hidden role=status>New filings have come in. <button onclick="location.reload()">Show them</button></div>
<div class=wrap>
{head}
{body}
<footer><p>* Disclosed public holdings = shares the person reported in their own SEC filings (this
company plus other public companies they file for), valued at today&rsquo;s prices. It is a floor,
not net worth: cash, property, private companies, options and unvested awards are not included.</p>
<p>Sources: SEC EDGAR (Form 4, 8-K, shares outstanding), Yahoo Finance (prices), Google News
(headlines). Charts by TradingView Lightweight Charts. Open-market buys and sells only. Information only, not investment advice.</p></footer>
</div><script>{JS_SITE.replace('STATUS', status_url) if script else ''}</script></body></html>"""


def _role(t) -> str:
    pos = (t.get("position") or "").lower()
    r = []
    if re.search(r"\bceo\b|chief executive|\bcfo\b|chief financial|president|chief operating|\bcoo\b", pos):
        r.append("csuite")
    if t.get("is_director"):
        r.append("director")
    if t.get("is_ten_pct"):
        r.append("tenpct")
    return " ".join(r) or "other"


def spark_svg(points: list[float], base: float) -> str:
    """Tiny line of the price since the trade; the dashed line is the insider's price."""
    if not points or not base:
        return ""
    vals = [base] + points
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or base * 0.01
    W, H, P = 92, 28, 3
    X = lambda i: i * W / (len(vals) - 1) if len(vals) > 1 else W
    Y = lambda v: P + (hi - v) * (H - 2 * P) / span
    pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals))
    cls = "up" if points[-1] >= base else "down"
    return (f'<svg class="spark {cls}" viewBox="0 0 {W} {H}" width={W} height={H} aria-hidden=true>'
            f'<line x1=0 x2={W} y1="{Y(base):.1f}" y2="{Y(base):.1f}" class=base />'
            f'<polyline points="{pts}" /><circle cx="{X(len(vals)-1):.1f}" cy="{Y(vals[-1]):.1f}" r=2.2 /></svg>')


def since_series(t: dict, src: dict, co: dict | None, intraday: dict) -> tuple[list[float], float | None]:
    """Prices from the trade date to now (hourly if we have them, else daily closes)."""
    base = src.get("chart_price") or t.get("price")
    day = (t.get("trade_date") or "")[:10]
    bars = intraday.get(t["ticker"]) or []
    pts = [c for ts, c in bars if ts[:10] >= day]
    if len(pts) < 2 and co and co.get("chart"):
        ch = co["chart"]
        pts = [c for d, c in zip(ch["dates"], ch["close"]) if d >= day]
    return pts, base


def strip_html(info: list[dict]) -> str:
    buys = [x for x in info if x["t"]["code"] == "P"]
    if not buys:
        return ""
    link = lambda x: (f'<a href="{x["href"]}">{e(x["t"]["ticker"])}</a>' if x["href"] else e(x["t"]["ticker"]))
    def tickers(rows, n=4):
        seen, out = set(), []
        for x in sorted(rows, key=lambda x: -x["t"]["value"]):
            if x["t"]["ticker"] not in seen:
                seen.add(x["t"]["ticker"])
                out.append(f'{link(x)} <span class=muted>{money(x["t"]["value"])}</span>')
        more = len(seen) - n
        return " ".join(out[:n]) + (f' <span class=muted>+{more} more</span>' if more > 0 else ""), len(seen)
    big = max(buys, key=lambda x: x["t"]["value"])
    cl, n_cl = tickers([x for x in buys if x["cl"] >= 2])
    cs, n_cs = tickers([x for x in buys if "csuite" in x["role"]])
    fb, n_fb = tickers([x for x in buys if x["fb"]])
    co_word = lambda n: f"{n} compan{'y' if n == 1 else 'ies'}"
    cards = [
        ("Biggest buy", money(big["t"]["value"]),
         f'{link(big)} <span class=muted>{e(big["t"]["position"])}'
         + (f', {pct(big["src"]["pct_mcap"])} of cap' if big["src"].get("pct_mcap") else "") + "</span>", None),
        ("Cluster buys, 7 days", co_word(n_cl), cl or "<span class=muted>None this week</span>",
         '{"side":"P","sig":"cluster"}' if n_cl else None),
        ("CEO and CFO buys", co_word(n_cs), cs or "<span class=muted>None this week</span>",
         '{"side":"P","role":"csuite"}' if n_cs else None),
        ("First buys in 1+ yr", co_word(n_fb), fb or "<span class=muted>None this week</span>",
         '{"side":"P","sig":"first"}' if n_fb else None),
    ]
    return ("<section class=strip aria-label=\"This week's buy signals\">" + "".join(
        f"<div class=sc><h3>{h}</h3><p class=sv>{v}</p><p class=sl>{l}</p>"
        + (f"<button type=button class=show data-f='{f}'>Show in the list</button>" if f else "") + "</div>"
        for h, v, l, f in cards) + "</section>")


def render_index(featured, cos, cfg, built_ts, window_days, clusters=None, intraday=None) -> str:
    clusters = clusters or {}
    intraday = intraday or {}
    rows_by_day, info = {}, []
    for t in sorted(featured, key=lambda t: (t["filing_date"], t["value"]), reverse=True):
        co = cos.get(t["issuer_cik"])
        tk = t["ticker"]
        enriched = next((x for x in co["trades"] if _key(x) == _key(t)), None) if co else None
        src = enriched or t
        link = (f'<a href="c/{page_name(tk)}.html#t={(t.get("trade_date") or "")[:10]}">{e(tk)}</a>'
                if co else e(tk))
        held = (f"{shares(src['held_after'])}"
                + (f"<br><span class=muted>{pct(src.get('pct_company_after'))} of co.</span>"
                   if src.get("pct_company_after") else "")) if src.get("held_after") else "n/a"
        q = f"{tk} {t['company']} {t['insider']} {' '.join(t.get('joint_filers') or [])}".lower()
        late = form4.days_late(t)
        cl = clusters.get(t["issuer_cik"], {}).get("n", 0) if t["code"] == "P" else 0
        fb = src.get("first_buy_label") if t["code"] == "P" else None
        sig = " ".join(x for x, on in (("cluster", cl >= 2), ("first", bool(fb))) if on)
        tags = ((f"<br><span class='badge cl'>Cluster: {cl} buyers</span>" if cl >= 2 else "")
                + (f"<br><span class='badge fb'>{e(fb)}</span>" if fb else "")
                + ("<br><span class=plan>10b5-1 plan</span>" if t["plan_10b5_1"] else "")
                + (f"<br><span class=late>Filed {late} days late</span>" if late and late > 10 else ""))
        sc = src.get("stake_change")
        stake = ("<span class=up>New</span>" if src.get("new_position") else
                 "n/a" if sc is None else
                 f"<span class={'up' if sc >= 0 else 'down'}>{pct(sc, signed=True)}</span>")
        pts, base = since_series(t, src, co, intraday)
        since = (pts[-1] / base - 1) if (pts and base) else src.get("since_trade")
        since_html = ("n/a" if since is None else
                      f"<span class=sp>{spark_svg(pts, base)}"
                      f"<span class={'up' if since >= 0 else 'down'}>{pct(since, signed=True)}</span></span>")
        href = f"c/{page_name(tk)}.html#t={(t.get('trade_date') or '')[:10]}" if co else None
        info.append({"t": t, "src": src, "cl": cl, "fb": fb, "role": _role(t), "href": href})
        mc = src.get("pct_mcap")
        others = len(t.get("joint_filers") or [])
        who = e(t["insider"]) + (f" <span class=muted>+{others} related filer{'s' if others > 1 else ''}</span>"
                                 if others else "")
        rows_by_day.setdefault(t["filing_date"], []).append(
            f"<tr class={'buy' if t['code']=='P' else 'sell'} data-side={t['code']} data-value={t['value']:.0f} "
            f"data-plan={int(t['plan_10b5_1'])} data-role=\"{_role(t)}\" data-q=\"{e(q)}\" data-sig=\"{sig}\">"
            f"<td class=tk>{link}</td><td>{e(t['company'])}</td>"
            f"<td>{who}<br><span class=muted>{e(t['position'])}</span></td>"
            f"<td><span class=side>{t['side']}</span>{tags}</td>"
            f"<td class=n><b>{money(t['value'])}</b><br><span class=muted>{pct(mc) + ' of cap' if mc else 'cap n/a'}</span></td>"
            f"<td class=n>{stake}</td><td class=n>{since_html}</td>"
            f"<td class=n>{held}</td><td>{nice_date(t['trade_date'])}</td>"
            f"<td><a href=\"{e(t['url'])}\">Form 4</a></td></tr>")
    tbodies = "".join(
        f"<tbody data-day=\"{d}\"><tr><th class=day colspan=10>Filed {nice_date(d)}</th></tr>{''.join(rs)}</tbody>"
        for d, rs in rows_by_day.items())
    body = strip_html(info) + f"""
<form name=feed class=filters aria-label="Filter trades" onsubmit="return false">
 <label><input type=search name=q placeholder="Ticker, company or person" aria-label="Search"></label>
 <label>Show <select name=side><option value=all>Buys and sells</option><option value=P>Buys only</option>
  <option value=S>Sells only</option></select></label>
 <label>Who <select name=role><option value=all>Anyone</option><option value=csuite>CEO, CFO, President, COO</option>
  <option value=director>Directors</option><option value=tenpct>10% owners</option></select></label>
 <label>At least <select name=min><option value=0>Any amount</option><option value=100000>$100k</option>
  <option value=500000>$500k</option><option value=1000000>$1M</option><option value=5000000>$5M</option></select></label>
 <label>Signal <select name=sig><option value=all>Any</option><option value=cluster>Cluster buys (7 days)</option>
  <option value=first>First buy in 1+ yr</option></select></label>
 <label><input type=checkbox name=noplan> Hide 10b5-1 plan trades</label>
 <span id=count aria-live=polite></span>
</form>
<div class=scroll><table class=feed><thead><tr><th>Ticker</th><th>Company</th><th>Insider</th><th></th>
<th class=n>Amount</th><th class=n>Stake</th><th class=n>Since trade</th><th class=n>Shares held after</th><th>Traded</th><th></th></tr></thead>
{tbodies}</table></div>
<p id=none class=empty hidden>No trades match these filters. Clear one to see more.</p>
{'' if featured else '<div class=empty><h2>No trades yet</h2><p>The first update is still collecting filings from EDGAR. Check back after the next run.</p></div>'}"""
    bought = sum(t["value"] for t in featured if t["code"] == "P")
    sold = sum(t["value"] for t in featured if t["code"] == "S")
    facts = [("Trades", f"{len(featured):,}"), ("Bought", money(bought)), ("Sold", money(sold))]
    sub = f"Traded in the last {window_days} days, from SEC EDGAR"
    note = (f"Open-market buys from {money(cfg.min_buy_usd)} and sells from {money(cfg.min_sell_usd)}.")
    return shell("Insider trades", sub, facts, body, built_ts, "status.json", note=note)


JUMP_JS = r"""
// Chart marker -> scroll to that trade (its detail card if it has one, else its table row) and flash it
window.showTrades=function(keys){
  const els=[];keys.forEach(k=>{const el=document.getElementById('tb-'+k)||document.getElementById('tr-'+k);if(el&&!els.includes(el))els.push(el)});
  if(!els.length)return;
  const smooth=!matchMedia('(prefers-reduced-motion: reduce)').matches;
  els[0].scrollIntoView({behavior:smooth?'smooth':'auto',block:'center'});
  els.forEach(el=>{el.classList.remove('flash');void el.offsetWidth;el.classList.add('flash');
    setTimeout(()=>el.classList.remove('flash'),2700)});
  if(!els[0].hasAttribute('tabindex'))els[0].setAttribute('tabindex','-1');
  els[0].focus({preventScroll:true});
};
document.querySelectorAll('.chart .mk').forEach(m=>{
  const go=()=>window.showTrades([m.dataset.k]);
  m.addEventListener('click',go);
  m.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();go()}});
});
"""


TV_JS = r"""
(function(){
const box=document.getElementById('tvbox'),el=document.getElementById('tv');
if(!box||!window.LightweightCharts)return;
const D=JSON.parse(document.getElementById('pxdata').textContent);
if(!D.t||D.t.length<5)return;
box.hidden=false;const fb=document.getElementById('svgchart');if(fb)fb.hidden=true;   // needs a width before drawing
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const L=LightweightCharts;
const theme=()=>({layout:{background:{type:'solid',color:css('--sheet')},textColor:css('--muted'),
  fontFamily:'"Public Sans",system-ui,sans-serif'},grid:{vertLines:{color:css('--field')},horzLines:{color:css('--field')}},
  rightPriceScale:{borderColor:css('--rule')},timeScale:{borderColor:css('--rule')}});
const chart=L.createChart(el,{...theme(),autoSize:true,crosshair:{mode:0},
  timeScale:{borderColor:css('--rule'),rightOffset:4},localization:{locale:'en-US',priceFormatter:p=>'$'+p.toFixed(p<10?3:2)}});
const candles=chart.addSeries(L.CandlestickSeries,{upColor:css('--buy'),downColor:css('--sell'),
  borderVisible:false,wickUpColor:css('--buy'),wickDownColor:css('--sell')});
candles.setData(D.t.map((t,i)=>({time:t,open:D.o[i],high:D.h[i],low:D.l[i],close:D.c[i]})));
candles.priceScale().applyOptions({scaleMargins:{top:0.08,bottom:0.24}});
const vol=chart.addSeries(L.HistogramSeries,{priceFormat:{type:'volume'},priceScaleId:'',lastValueVisible:false,priceLineVisible:false});
vol.priceScale().applyOptions({scaleMargins:{top:0.8,bottom:0}});
vol.setData(D.t.map((t,i)=>({time:t,value:D.v[i],color:(D.c[i]>=D.o[i]?css('--buy'):css('--sell'))+'55'})));
// snap each trade to the first trading day on or after its date
const snap=d=>{for(const t of D.t){if(t>=d)return t}return D.t[D.t.length-1]};
const byDay={};
D.trades.forEach(x=>{const day=snap(x.d);(byDay[day]=byDay[day]||[]).push(x)});
const marks=D.trades.filter(x=>x.d>=D.t[0]).map(x=>({time:snap(x.d),position:x.s==='P'?'belowBar':'aboveBar',
  shape:x.s==='P'?'arrowUp':'arrowDown',color:x.s==='P'?css('--buy'):css('--sell'),size:x.h?1.1:0.8,
  text:x.h?(x.s==='P'?'Buy ':'Sell ')+x.v:'',id:x.k})).sort((a,b)=>a.time<b.time?-1:a.time>b.time?1:0);
L.createSeriesMarkers(candles,marks);
// the trade you clicked: dashed line at the insider's price, and zoom to it
const focus=(location.hash.match(/t=(\d{4}-\d{2}-\d{2})/)||[])[1];
const ft=focus&&D.trades.find(x=>x.d===focus&&x.h)||D.trades.find(x=>x.h);
if(ft)candles.createPriceLine({price:ft.p,color:css('--ink'),lineWidth:1,lineStyle:2,axisLabelVisible:true,
  title:(ft.s==='P'?'Insider buy ':'Insider sell ')+'$'+ft.p.toFixed(2)});
const last=D.t[D.t.length-1];
const back=(d,days)=>{const x=new Date(d+'T00:00:00Z');x.setUTCDate(x.getUTCDate()-days);return x.toISOString().slice(0,10)};
const setRange=days=>{if(!days){chart.timeScale().fitContent();return}
  const from=back(last,days);chart.timeScale().setVisibleRange({from:from<D.t[0]?D.t[0]:from,to:last})};
const btns=[...box.querySelectorAll('[data-r]')];
const pick=days=>{btns.forEach(b=>b.setAttribute('aria-pressed',String(+b.dataset.r===days)));setRange(days)};
btns.forEach(b=>b.addEventListener('click',()=>pick(+b.dataset.r)));
requestAnimationFrame(()=>{if(focus){const from=back(focus,30);btns.forEach(b=>b.setAttribute('aria-pressed','false'));
  chart.timeScale().setVisibleRange({from:from<D.t[0]?D.t[0]:from,to:last})}else pick(182)});
// legend: the bar under the cursor, plus any insider trades that day
const lg=document.getElementById('tvlegend');
const fmt=v=>'$'+v.toFixed(v<10?3:2);
const show=i=>{const t=D.t[i],chg=i?D.c[i]/D.c[i-1]-1:0;
  let h='<b>'+t+'</b> O '+fmt(D.o[i])+' H '+fmt(D.h[i])+' L '+fmt(D.l[i])+' C '+fmt(D.c[i])+
   ' <span class='+(chg>=0?'up':'down')+'>'+(chg>=0?'+':'')+(chg*100).toFixed(2)+'%</span>';
  (byDay[t]||[]).forEach(x=>{h+='<br><span class='+(x.s==='P'?'up':'down')+'>'+(x.s==='P'?'Buy':'Sell')+
   '</span> '+x.n+' ('+x.r+') '+x.v+' at '+fmt(x.p)});lg.innerHTML=h};
show(D.t.length-1);
chart.subscribeCrosshairMove(p=>{el.style.cursor=(p.time&&byDay[p.time])?'pointer':'';
  if(!p.time){show(D.t.length-1);return}const i=D.t.indexOf(p.time);if(i>=0)show(i)});
chart.subscribeClick(p=>{const hit=p.hoveredObjectId&&D.trades.find(x=>x.k===p.hoveredObjectId);
  const list=hit?[hit]:(p.time&&byDay[p.time])||[];if(list.length)window.showTrades(list.map(x=>x.k))});
matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>chart.applyOptions(theme()));
})();
"""


def tv_block(co: dict) -> str:
    o = co.get("ohlc")
    if not o or len(o.get("t", [])) < 5:
        return ""
    trades = [{"d": (t.get("trade_date") or "")[:10], "s": t["code"], "p": round(t.get("chart_price") or t["price"], 4),
               "v": money(t["value"]), "n": t["insider"], "r": t["position"], "h": bool(t.get("headline")),
               "k": report.tkey(t)}
              for t in co["trades"] if t.get("trade_date")]
    data = json.dumps({**o, "trades": trades}).replace("</", "<\\/")
    ranges = "".join(f'<button type=button data-r={d}>{l}</button>'
                     for l, d in (("1M", 30), ("3M", 91), ("6M", 182), ("1Y", 365), ("5Y", 0)))
    return (f'<div id=tvbox class=tvbox hidden><div class=tvbar><div class=ranges role=group '
            f'aria-label="Chart range">{ranges}</div><div id=tvlegend class=tvlegend aria-live=off></div></div>'
            f'<div id=tv class=tv></div></div>'
            f'<script type=application/json id=pxdata>{data}</script>')


def render_company(co, built_ts) -> str:
    section = report.company_section(co, "site")
    svg = report.chart_svg(co)
    tv = tv_block(co)
    if tv and svg in section:   # interactive chart, with the simple chart as a no-script fallback
        section = section.replace(svg, tv + f"<div id=svgchart>{svg}</div>", 1)
    section = section.replace("Trades in this brief", "Traded in the last 7 days")
    body = (f'<a class=back href="../index.html" target=_self>&larr; All insider trades</a>' + section
            + f"<script>{JUMP_JS}</script>"
            + ('<script src="../assets/lightweight-charts.js"></script>'
               f"<script>{TV_JS}</script>" if tv else ""))
    return shell(f"{co['ticker']} insider trades: {co['name']}", "", [], body, built_ts,
                 "../status.json", compact=True)


def build(ed: Edgar, cfg: Config, out: Path, window_days=7, max_enrich=40,
          backfill_days=3, log=print):
    st = State(cfg)
    update(ed, cfg, st, backfill_days, log)
    st.save()
    featured = _featured(st, cfg, window_days)
    cos = enrich(ed, cfg, st, featured, max_enrich, log=log)
    built = time.time()
    tmp = out.with_name(out.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "c").mkdir(parents=True)
    (tmp / "assets").mkdir()
    static = Path(__file__).parent / "static"
    for f in static.iterdir():
        shutil.copy(f, tmp / "assets" / f.name)
    try:
        intraday = market.intraday_batch([t["ticker"] for t in featured])
    except Exception as err:
        warn(f"hourly prices unavailable this run: {err!r}")
        intraday = {}
    log(f"  hourly prices for {len(intraday)} tickers")
    clusters = {}
    for cik in {t["issuer_cik"] for t in featured}:
        if cik in cos:   # recomputed every run, so the window always ends today
            cos[cik]["cluster"] = clusters[cik] = brief.cluster(cos[cik]["trades"])
        else:   # not enriched yet: use the trades we've collected ourselves
            clusters[cik] = brief.cluster(form4.dedupe_joint(
                [dict(t) for t in st.trades if t["issuer_cik"] == cik]))
    (tmp / "index.html").write_text(render_index(featured, cos, cfg, built, window_days, clusters, intraday),
                                    encoding="utf-8")
    for co in cos.values():
        if co.get("ticker"):
            try:   # one odd filing must never take the whole site down
                (tmp / "c" / f"{page_name(co['ticker'])}.html").write_text(render_company(co, built), encoding="utf-8")
            except Exception as err:
                warn(f"page for {co['ticker']} skipped: {err!r}")
    (tmp / "status.json").write_text(json.dumps({"built": int(built), "trades": len(featured)}))
    (tmp / ".nojekyll").write_text("")
    shutil.rmtree(out, ignore_errors=True)
    tmp.replace(out)
    log(f"Site written to {out}/ ({len(featured)} trades, {len(cos)} company pages)")


def build_sample(out: Path):
    """Offline preview of the site with made-up data."""
    from . import sample
    cos = {c["cik"]: c for c in sample.companies()}
    featured = [t for c in cos.values() for t in c["trades"] if t["headline"]]
    built = time.time()
    shutil.rmtree(out, ignore_errors=True)
    (out / "c").mkdir(parents=True)
    cfg = Config()
    clusters = {k: c["cluster"] for k, c in cos.items()}
    (out / "assets").mkdir()
    for f in (Path(__file__).parent / "static").iterdir():
        shutil.copy(f, out / "assets" / f.name)
    intraday = sample.intraday(cos)
    page = render_index(featured, cos, cfg, built, 7, clusters, intraday).replace(
        "<div class=wrap>", "<div class=wrap><p class=sample>Sample site with made-up companies and people.</p>", 1)
    (out / "index.html").write_text(page, encoding="utf-8")
    for co in cos.values():
        (out / "c" / f"{page_name(co['ticker'])}.html").write_text(render_company(co, built), encoding="utf-8")
    (out / "status.json").write_text(json.dumps({"built": int(built)}))
