"""Recent headlines from Google News' public RSS search. Headlines and links only."""
import re
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests
from lxml import etree

SUFFIXES = r"[,.]?\s+(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|holdings?|group|n\.?v|s\.?a|l\.?p|lp|llc|\/de\/|\/md\/)\.?$"


def clean_name(name: str) -> str:
    n = re.sub(r"\s*/[A-Z]{2}/?\s*$", "", name.strip(), flags=re.I)  # "/DE/" style state tags
    for _ in range(3):
        n = re.sub(SUFFIXES, "", n, flags=re.I).strip()
    return n.title() if n.isupper() else n


def headlines(company: str, ticker: str, days: int = 90, limit: int = 10) -> list[dict]:
    q = f'"{clean_name(company)}" when:{days}d'
    url = f"https://news.google.com/rss/search?q={quote(q)}&hl=en-US&gl=US&ceid=US:en"
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 insider-brief"})
        r.raise_for_status()
        root = etree.fromstring(r.content, parser=etree.XMLParser(recover=True))
    except Exception:
        return []
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        try:
            when = parsedate_to_datetime(it.findtext("pubDate")).date().isoformat()
        except Exception:
            when = ""
        out.append({"date": when, "title": title, "source": src, "url": it.findtext("link") or ""})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out[:limit]
