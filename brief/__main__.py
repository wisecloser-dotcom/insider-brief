"""Insider trade brief from SEC EDGAR.

  python -m brief daily  --sec-agent "Your Name you@email.com"            yesterday's filings
  python -m brief daily  --day 2026-10-08 --sec-agent "..."               a specific day
  python -m brief ticker NVDA --sec-agent "..."                           one company, last 90 days
  python -m brief sample                                                  preview with made-up data
  python -m brief site --sec-agent "..." --out site                       update the website (run on a schedule)
  python -m brief site-sample --out site                                  website preview with made-up data
"""
import argparse
import webbrowser
from datetime import datetime
from pathlib import Path

from . import brief, report
from .config import Config


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["daily", "ticker", "sample", "site", "site-sample"])
    ap.add_argument("symbol", nargs="?", help="ticker, for the ticker command")
    ap.add_argument("--sec-agent", help='"Your Name you@email.com" (the SEC requires it)')
    ap.add_argument("--day", help="YYYY-MM-DD (daily; default = last US trading day)")
    ap.add_argument("--min-buy", type=float, help="smallest buy to include, USD (default 25000)")
    ap.add_argument("--min-sell", type=float, help="smallest sale to include, USD (default 250000)")
    ap.add_argument("--max-companies", type=int, help="default 25, biggest trades first")
    ap.add_argument("--data-dir", help="where cache and briefs go (default ./data)")
    ap.add_argument("--no-open", action="store_true", help="don't open the page in a browser")
    ap.add_argument("--out", default="site", help="website output folder (site commands)")
    ap.add_argument("--window-days", type=int, default=7, help="site: days of trades on the front page")
    ap.add_argument("--max-enrich", type=int, default=40, help="site: companies refreshed per run")
    a = ap.parse_args(argv)

    cfg = Config()
    if a.sec_agent: cfg.sec_user_agent = a.sec_agent
    if a.min_buy is not None: cfg.min_buy_usd = a.min_buy
    if a.min_sell is not None: cfg.min_sell_usd = a.min_sell
    if a.max_companies: cfg.max_companies = a.max_companies
    if a.data_dir: cfg.data_dir = Path(a.data_dir)
    cfg.ensure_dirs()

    if a.command in ("site", "site-sample"):
        from . import site
        out = Path(a.out)
        if a.command == "site-sample":
            site.build_sample(out)
        else:
            from .edgar import Edgar
            from .sec import SecClient
            ed = Edgar(SecClient(cfg.sec_user_agent, cfg.cache_dir, cfg.sec_max_rps))
            try:
                site.build(ed, cfg, out, a.window_days, a.max_enrich)
            except Exception:
                import traceback
                tb = traceback.format_exc()
                print(tb)
                # an ::error:: line becomes an annotation on the run page, readable without logs
                print("::error title=Site build failed::" + tb.replace("%", "%25").replace("\n", "%0A"))
                raise
        print(f"Site in {out}/index.html")
        return

    if a.command == "sample":
        from . import sample
        html = report.page(sample.companies(), "Thursday 8 October 2026", "Insider trades filed on EDGAR",
                           sample=True)
        out = cfg.out_dir / "sample.html"
    else:
        from .edgar import Edgar, last_weekday
        from .sec import SecClient
        ed = Edgar(SecClient(cfg.sec_user_agent, cfg.cache_dir, cfg.sec_max_rps))
        if a.command == "daily":
            day = datetime.strptime(a.day, "%Y-%m-%d").date() if a.day else last_weekday()
            print(f"Reading EDGAR for {day} (first run takes a few minutes; repeats use the cache)...")
            try:
                cos = brief.daily(ed, cfg, day)
            except Exception as err:
                print(f"  EDGAR's daily index for {day} isn't available ({err}). "
                      "It appears after the US close; weekends and US holidays have none.")
                cos = []
            html = report.page(cos, f"{day:%A} {day.day} {day:%B %Y}", "Insider trades filed on EDGAR")
            out = cfg.out_dir / f"brief_{day}.html"
        else:
            if not a.symbol:
                ap.error("give a ticker, e.g.  python -m brief ticker NVDA")
            cos = brief.single(ed, cfg, a.symbol)
            html = report.page(cos, f"{a.symbol.upper()}: insider trades", "Last 90 days on EDGAR",
                               mode="ticker")
            out = cfg.out_dir / f"ticker_{a.symbol.upper()}_{datetime.now():%Y-%m-%d}.html"
    out.write_text(html, encoding="utf-8")
    (cfg.out_dir / "latest.html").write_text(html, encoding="utf-8")
    print(f"Saved {out}")
    if not a.no_open:
        webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()
