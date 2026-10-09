# Insider trade brief (SEC EDGAR)

A website of insider buys and sells filed on SEC EDGAR that **updates itself every 30 minutes**.
It gives you what you need to judge each trade yourself. No scores, no predictions.

For every trade:
1. Company, industry, exchange
2. The person's position (CEO, CFO, Director, 10% owner...)
3. Amount: shares x price, and whether it was a pre-arranged 10b5-1 plan trade
4. Amount vs company size: % of market cap and vs a typical day's trading
5. Amount vs the person's **disclosed public holdings** (see the note below)
6. Trade date and filing date
7. 6-month price chart with insider trades marked on it
8. News from the last 3 months: the company's 8-K filings plus Google News headlines
9. Every other insider buying or selling the stock in the last 90 days, with net totals
10. How much the person held in the company before and after the trade (shares, value, % of company)

## Put the website online (once, about 10 minutes)

It runs free on GitHub: **Actions** does the work on a timer, **Pages** hosts the site.

1. Create a **public** GitHub repository (e.g. `insider-brief`) and put these files in it.
2. Add your SEC contact: repo **Settings > Secrets and variables > Actions > New repository secret**.
   Name `SEC_USER_AGENT`, value `Your Name you@email.com`. The SEC requires this.
3. Switch on Pages: **Settings > Pages > Build and deployment > Source: GitHub Actions**.
4. Start the first run: **Actions > Update site > Run workflow**.

The first run backfills the last 3 trading days and takes about 15 to 25 minutes. After that it
runs twice an hour and usually finishes in a few minutes. Your site is at
`https://<your-username>.github.io/<repo-name>/`.

How fresh it is: EDGAR's live feed is checked every 30 minutes (GitHub sometimes starts
scheduled runs late). An open page shows a "New filings have come in" bar when an update
lands. On a busy day, companies with the biggest trades are filled in first; the rest appear
over the next run or two.

To change the filters, edit the `python -m brief site ...` line in
`.github/workflows/update.yml`, e.g. add `--min-buy 50000 --min-sell 1000000 --window-days 14`.

## Saved trades (wishlist)

Click the star next to any trade (or **Save** on a company page) to keep it on the **Saved** page,
with your own note. Saves live in your browser's storage, so they survive closing the browser,
but they're per browser and device. Use **Download backup** / **Load backup** on the Saved page
to move them to another device.

## Run it on your own computer (optional)

```
pip install -r requirements.txt
python -m brief site-sample --out site                         # preview with made-up data
python -m brief site --sec-agent "Your Name you@email.com"     # real data -> site/index.html
python -m brief ticker NVDA --sec-agent "..."                  # one company, last 90 days
python -m brief daily --sec-agent "..."                        # one-off page for the last US trading day
```

On Windows, use `py` instead of `python` if `python` isn't found.

## Data sources and limits

- **EDGAR:** the live Form 4 feed and daily index (trades, positions, holdings), 8-Ks, and
  shares outstanding (for market cap).
- **Yahoo Finance:** prices only. If Yahoo has no data for a ticker, the chart and the
  "vs company size" numbers show n/a.
- **Google News RSS:** headlines and links only.
- **Disclosed public holdings is not net worth.** It is the shares the person reported in their
  own SEC filings (this company plus their latest filings for other public companies), valued at
  today's prices. It leaves out cash, property, private companies, options and unvested awards,
  so treat it as a minimum.
- Open-market trades only (Form 4 codes P and S). Grants, option exercises, tax withholding and
  gifts are left out on purpose.
- Company pages exist for companies with a qualifying trade in the window. To look up any other
  company, run the `ticker` command on your computer.

Charts use TradingView's open-source Lightweight Charts library (Apache 2.0, included in
`brief/static/` with its license).

Information only, not investment advice.
