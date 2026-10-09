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
                "date": _v(t, "transactionDate"),
                "code": _v(t, "transactionCoding/transactionCode"),
                "shares": _num(_v(t, "transactionAmounts/transactionShares")),
                "price": _num(_v(t, "transactionAmounts/transactionPricePerShare")),
                "acq_disp": _v(t, "transactionAmounts/transactionAcquiredDisposedCode"),
                "owned_after": _num(_v(t, "postTransactionAmounts/sharesOwnedFollowingTransaction")),
                "direct": (_v(t, "ownershipNature/directOrIndirectOwnership") or "D") == "D",
            })
    return {
        "form": _v(root, "documentType"),
        "period": _v(root, "periodOfReport"),
        "issuer_cik": (_v(issuer, "issuerCik") or "").lstrip("0"),
        "issuer_name": _v(issuer, "issuerName") or "",
        "ticker": (_v(issuer, "issuerTradingSymbol") or "").upper().strip(),
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
