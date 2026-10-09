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


def test_tickers_dedupe_and_late():
    assert form4.clean_ticker("N/A") == "" and form4.clean_ticker("none") == ""
    assert form4.clean_ticker("brk.b") == "BRK-B" and form4.clean_ticker("GOOGL, GOOG") == "GOOGL"
    base = {"issuer_cik": "1", "trade_date": "2026-10-06", "code": "S", "shares": 1000.0, "price": 10.0,
            "insider_cik": "a", "insider": "Fund IV LP", "joint_filers": []}
    out = form4.dedupe_joint([base, dict(base, insider_cik="b", insider="Fund GP LLC"),
                              dict(base, insider_cik="c", insider="Other Person", shares=5.0)])
    assert len(out) == 2 and out[0]["joint_filers"] == ["Fund GP LLC"]
    assert form4.days_late({"filing_date": "2026-10-08", "trade_date": "2026-09-01"}) == 37


def test_front_page_needs_a_recent_trade_date(tmp_path):
    from datetime import date, timedelta
    from brief import site
    from brief.config import Config
    today, old = date.today(), date.today() - timedelta(days=22)
    base = {"ticker": "AAA", "issuer_cik": "1", "insider_cik": "a", "insider": "X", "code": "P",
            "value": 1e6, "shares": 1e4, "price": 100.0, "plan_10b5_1": False, "accession": "1",
            "filing_date": str(today), "trade_date": str(today - timedelta(days=2))}
    st = site.State(Config(data_dir=tmp_path))
    st.trades = [base, dict(base, accession="2", insider_cik="b", trade_date=str(old))]
    got = site._featured(st, Config(), 7)
    assert [t["accession"] for t in got] == ["1"]   # filed today but traded 3 weeks ago: left out


def test_cluster_and_first_buy_labels():
    from datetime import date
    from brief.brief import cluster, first_buy_label
    t = lambda who, d, code="P", v=1e5: {"insider_cik": who, "insider": who, "trade_date": d, "code": code, "value": v}
    c = cluster([t("a", "2026-10-06"), t("b", "2026-10-03"), t("a", "2026-10-01"),
                 t("c", "2026-09-25"), t("d", "2026-10-05", "S")], today=date(2026, 10, 9))
    assert c["n"] == 2 and c["insiders"] == ["a", "b"]          # c is 14 days back, d is a sale
    assert cluster([t("a", "2026-10-06"), t("c", "2026-09-25")], days=30, today=date(2026, 10, 9))["n"] == 2
    assert first_buy_label({"prior_buy": "2022-03-01"}, "2026-10-06")[0] == "First buy in 4 yrs"
    assert first_buy_label({"prior_buy": "2026-06-01"}, "2026-10-06")[0] is None
    assert first_buy_label({"first_filing": "2026-07-01"}, "2026-10-06")[0] == "New insider, first buy"
    assert first_buy_label({"first_filing": "2015-01-01"}, "2026-10-06")[0] == "First buy in 5+ yrs"
    assert first_buy_label({"first_filing": "2015-01-01", "complete": False}, "2026-10-06")[0] is None


def test_owner_buy_history_finds_last_earlier_buy():
    import pandas as pd
    from brief.edgar import Edgar
    old = XML.replace(b"<transactionCode>S</transactionCode>", b"<transactionCode>P</transactionCode>") \
             .replace(b"2026-10-06", b"2023-02-01").replace(b"2026-10-07", b"2023-02-02")
    other = XML.replace(b"0000320193", b"0000999999")       # a different company: ignored
    ed = Edgar.__new__(Edgar)
    ed.recent_filings = lambda cik: pd.DataFrame({
        "accessionNumber": ["n", "x", "o", "first"], "form": ["4", "4", "4", "3"],
        "filingDate": pd.to_datetime(["2026-10-08", "2025-01-01", "2023-02-03", "2019-05-01"]),
        "primaryDocument": "", "items": ""})
    ed.filing = lambda cik, acc: form4.parse({"n": XML, "x": other, "o": old}.get(acc, XML))
    h = ed.owner_buy_history("1214156", "320193", "2026-10-06", "2026-10-08")
    assert h["prior_buy"] == "2023-02-02" and h["first_filing"] == "2019-05-01"


def test_spark_and_chart_data():
    from brief import site, sample
    svg = site.spark_svg([10.0, 10.5, 11.0], 10.0)
    assert 'class="spark up"' in svg and "<polyline" in svg
    assert 'class="spark down"' in site.spark_svg([9.5, 9.0], 10.0)
    assert site.spark_svg([], 10.0) == ""
    co = sample.companies()[0]
    co["trades"][0]["insider"] = "Evil </script><b>x"      # data must not break out of the script tag
    block = site.tv_block(co)
    assert "</script><b>" not in block.split("id=pxdata>")[1].split("</script>")[0] + "</script><b>"[:0]
    assert block.count("</script>") == 1
    page = site.render_company(co, 0)
    assert "lightweight-charts.js" in page and "id=svgchart" in page
