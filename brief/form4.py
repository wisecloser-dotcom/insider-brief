"""Parse a Form 4 (also 3/5) ownership XML into plain Python dicts."""
import re

from lxml import etree

CODES = {"P": "Buy", "S": "Sell"}  # open-market purchase / sale; other codes are ignored


def extract_xml(submission_text: str) -> bytes | None:
    """A filing's .txt submission wraps the ownership document in <XML>...</XML>."""
    m = re.search(r"<XML>\s*(.*?)\s*</XML>", submission_text, re.S | re.I)
    return m.group(1).encode() if m else None


def _v(node, path):
    if node is None:
        return None
    hit = node.find(path + "/value")
    if hit is None:
        hit = node.find(path)
    return hit.text.strip() if hit is not None and hit.text and hit.text.strip() else None


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _date(x):
    """Dates can carry a timezone ("2026-10-06-05:00", "2026-10-06Z"); keep YYYY-MM-DD."""
    m = re.match(r"\d{4}-\d{2}-\d{2}", x or "")
    return m.group(0) if m else None


def clean_ticker(x) -> str:
    """Filers sometimes write N/A, NONE or several symbols; keep one usable ticker or ''."""
    t = (x or "").upper().strip()
    if t in ("N/A", "N.A.", "NA", "NONE", "NULL", "-", "--", "TBD", "NOT APPLICABLE"):
        return ""
    t = re.split(r"[\s,;/]+", t)[0]
    if not re.fullmatch(r"[A-Z0-9.\-]{1,10}", t):
        return ""
    return t.replace(".", "-")   # BRK.B -> BRK-B, the form Yahoo uses


ENTITY_RE = re.compile(r"\b(l\.?l\.?c|l\.?p|l\.?l\.?p|inc|corp|co|ltd|fund|funds|trust|partners?|capital|"
                       r"management|holdings?|advisors?|advisers|group|investments?|ventures?|associates|"
                       r"master|offshore|onshore|opportunit(?:y|ies)|equity|gp|spv|plc|s\.?a|n\.?v|ag|"
                       r"limited|company|the|and|of|de|[ivx]+|\d+)\b\.?", re.I)


def entity_stem(name: str) -> str | None:
    """'Saba Capital Master Fund, Ltd.' and 'Saba Capital Management, L.P.' -> 'saba'.
    Only for organisations: two people who share a surname are not merged."""
    n = (name or "").lower()
    if not re.search(r"\b(llc|l\.l\.c|lp|l\.p|fund|trust|partners|capital|management|holdings|advisors|"
                     r"ltd|inc|corp|investments|ventures|group|spv|plc)\b", n):
        return None
    words = [w for w in re.split(r"[^a-z0-9&]+", ENTITY_RE.sub(" ", n)) if len(w) > 1]
    return " ".join(words[:2]) if words else None


def insider_groups(trades: list[dict]) -> dict:
    """Map each insider CIK to a group id, merging related filers: entities that file
    Form 4s together (a fund and its manager) or share a name stem (Fund IV / Fund V)."""
    parent = {}
    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    by_name, by_stem = {}, {}
    for t in trades:
        c = t.get("insider_cik") or t.get("insider")
        find(c)
        by_name[(t.get("insider") or "").lower()] = c
    for t in trades:
        c = t.get("insider_cik") or t.get("insider")
        for jc in t.get("joint_ciks") or []:
            union(c, jc)
        for jn in t.get("joint_filers") or []:
            if jn.lower() in by_name:
                union(c, by_name[jn.lower()])
        st = entity_stem(t.get("insider"))
        if st:
            if st in by_stem:
                union(c, by_stem[st])
            else:
                by_stem[st] = c
    return {c: find(c) for c in list(parent)}


def dedupe_joint(trades: list[dict]) -> list[dict]:
    """Funds often report one trade under several related entities (a fund, its
    manager, its general partner). Show it once and list the other filers."""
    out, seen = [], {}
    for t in trades:
        k = (t["issuer_cik"], t["trade_date"], t["code"], round(t["shares"]), round(t["price"], 2))
        if k in seen and seen[k]["insider_cik"] != t["insider_cik"]:
            first = seen[k]
            first["joint_filers"] = list(dict.fromkeys(first.get("joint_filers", []) + [t["insider"]]))
            continue
        seen[k] = t
        out.append(t)
    return out


def days_late(t: dict) -> int | None:
    try:
        from datetime import date
        return (date.fromisoformat(t["filing_date"][:10]) - date.fromisoformat(t["trade_date"][:10])).days
    except Exception:
        return None


def _flag(x) -> bool:
    return (x or "").strip().lower() in ("1", "true", "y", "yes")


def position_of(rel: dict) -> str:
    parts = []
    if rel["officer_title"]:
        parts.append(rel["officer_title"])
    elif rel["is_officer"]:
        parts.append("Officer")
    if rel["is_director"]:
        parts.append("Director")
    if rel["is_ten_pct"]:
        parts.append("10% owner")
    if not parts and rel["other_text"]:
        parts.append(rel["other_text"])
    return ", ".join(parts) or "Not stated"


def parse(xml_bytes: bytes) -> dict:
    root = etree.fromstring(xml_bytes, parser=etree.XMLParser(recover=True))
    issuer = root.find("issuer")
    owners = []
    for o in root.findall("reportingOwner"):
        rel = o.find("reportingOwnerRelationship")
        r = {"cik": (_v(o, "reportingOwnerId/rptOwnerCik") or "").lstrip("0"),
             "name": _v(o, "reportingOwnerId/rptOwnerName") or "Unknown",
             "is_director": _flag(_v(rel, "isDirector")),
             "is_officer": _flag(_v(rel, "isOfficer")),
             "is_ten_pct": _flag(_v(rel, "isTenPercentOwner")),
             "officer_title": _v(rel, "officerTitle"),
             "other_text": _v(rel, "otherText")}
        r["position"] = position_of(r)
        owners.append(r)

    footnotes = " ".join(f.text or "" for f in root.iter("footnote"))
    remarks = _v(root, "remarks") or ""
    plan = _flag(_v(root, "aff10b5One")) or bool(re.search(r"10b5-?1", footnotes + remarks, re.I))

    rows = []
    for kind, path in (("trade", "nonDerivativeTable/nonDerivativeTransaction"),
                       ("holding", "nonDerivativeTable/nonDerivativeHolding")):
        for t in root.findall(path):
            rows.append({
                "kind": kind,
                "security": _v(t, "securityTitle") or "",
                "date": _date(_v(t, "transactionDate")),
                "code": _v(t, "transactionCoding/transactionCode"),
                "shares": _num(_v(t, "transactionAmounts/transactionShares")),
                "price": _num(_v(t, "transactionAmounts/transactionPricePerShare")),
                "acq_disp": _v(t, "transactionAmounts/transactionAcquiredDisposedCode"),
                "owned_after": _num(_v(t, "postTransactionAmounts/sharesOwnedFollowingTransaction")),
                "direct": (_v(t, "ownershipNature/directOrIndirectOwnership") or "D") == "D",
            })
    return {
        "form": _v(root, "documentType"),
        "period": _date(_v(root, "periodOfReport")),
        "issuer_cik": (_v(issuer, "issuerCik") or "").lstrip("0"),
        "issuer_name": _v(issuer, "issuerName") or "",
        "ticker": clean_ticker(_v(issuer, "issuerTradingSymbol")),
        "owners": owners,
        "plan_10b5_1": plan,
        "rows": rows,
    }


def open_market_trades(f: dict, accession: str, filing_date: str, url: str) -> list[dict]:
    """One record per filing and side (buy/sell): several lines are summed."""
    out = []
    owner = f["owners"][0] if f["owners"] else {"cik": "", "name": "Unknown", "position": "Not stated"}
    for code, side in CODES.items():
        lines = [r for r in f["rows"] if r["kind"] == "trade" and r["code"] == code
                 and r["shares"] and r["price"]]
        if not lines:
            continue
        shares = sum(r["shares"] for r in lines)
        value = sum(r["shares"] * r["price"] for r in lines)
        last = lines[-1]
        after = last["owned_after"]
        before = None if after is None else (after - shares if code == "P" else after + shares)
        out.append({
            "accession": accession, "filing_date": filing_date, "url": url,
            "trade_date": min(r["date"] for r in lines if r["date"]) if any(r["date"] for r in lines) else None,
            "issuer_cik": f["issuer_cik"], "company": f["issuer_name"], "ticker": f["ticker"],
            "insider_cik": owner["cik"], "insider": owner["name"], "position": owner["position"],
            "is_director": owner.get("is_director", False), "is_officer": owner.get("is_officer", False),
            "is_ten_pct": owner.get("is_ten_pct", False),
            "joint_filers": [o["name"] for o in f["owners"][1:]],
            "joint_ciks": [o["cik"] for o in f["owners"][1:] if o.get("cik")],
            "side": side, "code": code, "shares": shares, "price": value / shares, "value": value,
            "owned_after": after, "direct": last["direct"],
            "stake_change": (shares / before * (1 if code == "P" else -1)) if before and before > 0 else None,
            "new_position": code == "P" and before is not None and before <= 0,
            "plan_10b5_1": f["plan_10b5_1"],
            # whole holding in this company, direct + indirect (trusts, family, funds)
            "held_after": (held := holdings(f)),
            "held_before": (held - shares if code == "P" else held + shares) if held else None,
        })
    return out


def holdings(f: dict) -> float:
    """Total non-derivative shares reported as held after this filing
    (latest figure per security and direct/indirect line)."""
    latest = {}
    for r in f["rows"]:
        title = r["security"].lower()
        if re.search(r"preferred|warrant|unit|note|option|right", title):
            continue   # only count common-type shares, which trade at the quoted price
        if r["owned_after"] is not None:
            latest[(r["security"].lower(), r["direct"])] = r["owned_after"]
    return sum(latest.values())
