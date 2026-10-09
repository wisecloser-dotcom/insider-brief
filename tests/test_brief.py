"""Offline checks. Run:  python -m pytest -q"""
from brief import form4, report, sample

XML = b"""<?xml version="1.0"?>
<ownershipDocument><documentType>4</documentType>
 <issuer><issuerCik>0000320193</issuerCik><issuerName>Apple Inc.</issuerName>
  <issuerTradingSymbol>aapl</issuerTradingSymbol></issuer>
 <reportingOwner><reportingOwnerId><rptOwnerCik>0001214156</rptOwnerCik><rptOwnerName>Doe Jane</rptOwnerName></reportingOwnerId>
  <reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>1</isOfficer>
  <officerTitle>Chief Executive Officer</officerTitle></reportingOwnerRelationship></reportingOwner>
 <nonDerivativeTable>
  <nonDerivativeTransaction><securityTitle><value>Common Stock</value></securityTitle>
   <transactionDate><value>2026-10-06</value></transactionDate>
   <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
   <transactionAmounts><transactionShares><value>600</value></transactionShares>
    <transactionPricePerShare><value>200</value></transactionPricePerShare></transactionAmounts>
   <postTransactionAmounts><sharesOwnedFollowingTransaction><value>4400</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
   <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
  </nonDerivativeTransaction>
  <nonDerivativeTransaction><securityTitle><value>Common Stock</value></securityTitle>
   <transactionDate><value>2026-10-07</value></transactionDate>
   <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
   <transactionAmounts><transactionShares><value>400</value></transactionShares>
    <transactionPricePerShare><value>205</value></transactionPricePerShare></transactionAmounts>
   <postTransactionAmounts><sharesOwnedFollowingTransaction><value>4000</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
   <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
  </nonDerivativeTransaction>
  <nonDerivativeHolding><securityTitle><value>Common Stock</value></securityTitle>
   <postTransactionAmounts><sharesOwnedFollowingTransaction><value>10000</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
   <ownershipNature><directOrIndirectOwnership><value>I</value></directOrIndirectOwnership></ownershipNature>
  </nonDerivativeHolding>
  <nonDerivativeHolding><securityTitle><value>Series A Preferred</value></securityTitle>
   <postTransactionAmounts><sharesOwnedFollowingTransaction><value>999999</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
  </nonDerivativeHolding>
 </nonDerivativeTable>
 <footnotes><footnote id="F1">Sold under a Rule 10b5-1 trading plan adopted March 2026.</footnote></footnotes>
</ownershipDocument>"""


def test_parse_and_aggregate():
    f = form4.parse(form4.extract_xml("<SEC-DOCUMENT><XML>" + XML.decode() + "</XML>"))
    assert f["ticker"] == "AAPL" and f["owners"][0]["position"] == "Chief Executive Officer, Director"
    assert f["plan_10b5_1"]                         # found in the footnote
    (t,) = form4.open_market_trades(f, "acc", "2026-10-08", "u")
    assert t["side"] == "Sell" and t["shares"] == 1000 and t["value"] == 600 * 200 + 400 * 205
    assert t["trade_date"] == "2026-10-06"
    assert t["held_after"] == 14_000              # 4,000 direct + 10,000 in a trust; preferred ignored
    assert t["held_before"] == 15_000
    assert abs(t["stake_change"] + 0.2) < 1e-9    # sold 1,000 of 5,000 direct shares


def test_sample_page_renders():
    html = report.page(sample.companies(), "Test", "Sub", sample=True)
    assert "Northwind" in html and "<svg" in html and "Disclosed public holdings" in html


def test_empty_page():
    assert "No trades matched" in report.page([], "Test", "Sub")


ATOM = b"""<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>4 - Apple Inc. (0000320193) (Issuer)</title>
 <link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/0000320193-26-000001-index.htm"/>
 <updated>2026-10-08T16:31:07-04:00</updated><category term="4"/>
 <id>urn:tag:sec.gov,2008:accession-number=0000320193-26-000001</id></entry>
<entry><title>4 - Doe Jane (0001214156) (Reporting)</title>
 <link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/1214156/000032019326000001/0000320193-26-000001-index.htm"/>
 <updated>2026-10-08T16:31:07-04:00</updated><category term="4"/>
 <id>urn:tag:sec.gov,2008:accession-number=0000320193-26-000001</id></entry>
</feed>"""


def test_site_update_reads_live_feed_once(tmp_path):
    from brief import site
    from brief.config import Config

    class FakeClient:
        def get_bytes(self, url, max_age_hours=None):
            return ATOM if "start=0" in url else b"<feed xmlns='http://www.w3.org/2005/Atom'/>"

    class FakeEdgar:
        c = FakeClient()
        calls = 0
        def filing(self, cik, acc):
            FakeEdgar.calls += 1
            return form4.parse(XML)
        def index_url(self, cik, acc):
            return f"https://www.sec.gov/{cik}/{acc}"
        def daily_form4_list(self, d):
            raise RuntimeError("not posted")

    cfg = Config(data_dir=tmp_path)
    st = site.State(cfg)
    st.days_done = {"x"}                      # skip the daily-index backfill in this test
    assert site.update(FakeEdgar(), cfg, st, log=lambda *a: None) == 1
    assert FakeEdgar.calls == 1               # listed twice in the feed, fetched once
    assert st.trades[0]["filing_date"] == "2026-10-08"
    assert site.update(FakeEdgar(), cfg, st, log=lambda *a: None) == 0   # nothing new next run
    st.save()
    assert site.State(cfg).seen                # state survives a reload


def test_sample_site_builds(tmp_path):
    from brief import site
    site.build_sample(tmp_path)
    assert (tmp_path / "index.html").exists() and (tmp_path / "c" / "NWTX.html").exists()


def test_full_site_build_with_awkward_data(tmp_path, monkeypatch):
    """Runs update -> enrich -> render end to end with fakes, including a timezone-suffixed
    date, a ticker Yahoo doesn't know, and an insider with no other holdings."""
    import pandas as pd
    from brief import brief as brief_mod, market, news, site
    from brief.config import Config

    xml = XML.replace(b"<value>2026-10-06</value>", b"<value>2026-10-06-04:00</value>")
    xml = xml.replace(b"<value>2026-10-07</value>", b"<value>2026-10-07Z</value>")

    class FakeClient:
        def get_bytes(self, url, max_age_hours=None):
            return ATOM if "start=0" in url else b"<feed xmlns='http://www.w3.org/2005/Atom'/>"

    class FakeEdgar:
        c = FakeClient()
        def filing(self, cik, acc): return form4.parse(xml)
        def index_url(self, cik, acc): return "https://www.sec.gov/x"
        def daily_form4_list(self, d): raise RuntimeError("not posted")
        def submissions(self, cik): return {"name": "Apple Inc.", "sicDescription": "Computers", "exchanges": ["Nasdaq"]}
        def shares_outstanding(self, cik): return 15e9
        def company_trades(self, cik, since): return []
        def owner_holdings(self, cik, n): return []
        def eight_ks(self, cik, since): return [{"date": "2026-09-01", "form": "8-K", "what": ["Earnings results"], "url": "u"}]

    days = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=130)
    px = pd.DataFrame({"close": [190.0 + i % 7 for i in range(130)], "volume": 5e7, "split_after": 1.0}, index=days)
    monkeypatch.setattr(market, "history", lambda t, months=7: px)
    monkeypatch.setattr(market, "last_price", lambda t: None)
    monkeypatch.setattr(market, "market_cap_fallback", lambda t: None)
    monkeypatch.setattr(news, "headlines", lambda *a, **k: [])

    today = str(pd.Timestamp.today().date())
    atom = ATOM.replace(b"2026-10-08T", today.encode() + b"T")
    FakeClient.get_bytes = lambda self, url, max_age_hours=None: atom if "start=0" in url else b"<feed xmlns='http://www.w3.org/2005/Atom'/>"

    cfg = Config(data_dir=tmp_path / "data", min_sell_usd=1000)
    st = site.State(cfg); st.days_done = {"x"}; st.save()
    (tmp_path / "data" / "state" / "days_done.json").write_text('["x"]')
    site.build(FakeEdgar(), cfg, tmp_path / "site", log=lambda *a: None)
    idx = (tmp_path / "site" / "index.html").read_text()
    page = (tmp_path / "site" / "c" / "AAPL.html").read_text()
    assert "Doe Jane" in idx and "6 Oct 2026" in idx
    assert "<svg" in page and "Earnings results" in page
