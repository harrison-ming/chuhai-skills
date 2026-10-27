#!/usr/bin/env python3
"""Rule-based extraction of statements (claims) and conflict detection.

Topics: return_days, return_start, delivery_days, free_threshold,
return_payer, restocking_fee, refund_days, currency_word, entity, email.
Each claim: {topic, value, source, url, snippet}. Regex extraction misreads
sometimes (e.g. a 30-day warranty is not a return window), so conflicts are
candidates the agent must re-read before writing [有矛盾].

CLI (debug): python extract_claims.py "<raw folder>"  -> prints claims.
Python 3.8+, standard library only.
"""

import json
import re
import sys

import pages

DAY = r"(?:days?|tage[n]?|jours?|d[ií]as|giorni|dagen|天)"
NUM = r"(\d{1,3})"
RANGE = r"(\d{1,3})\s*(?:-|–|—|to|bis|à|a|~|至)\s*(\d{1,3})"
RETURN_W = re.compile(
    r"\b(return|returns|returned|exchange|money[- ]back|retour|rücksend|"
    r"widerruf|withdraw|cancel)\w*|退货|退换",
    re.I,
)
REFUND_W = re.compile(r"\brefund\w*|erstatt|rembours|reembols|退款", re.I)
WARRANTY_W = re.compile(r"warrant|guarantee(?!.{0,20}(money|refund))|质保|保修", re.I)
PROCESS_W = re.compile(
    r"process|credited|issued|reflect|back to your|appear|到账", re.I
)
SHIP_W = re.compile(
    r"deliver|arriv|transit|shipping time|ship(?:ped|s)? within|versand|lieferzeit|"
    r"livraison|送达|配送|运输",
    re.I,
)
HANDLING_W = re.compile(r"process|handling|prepar|dispatch|处理|备货", re.I)
START_PURCHASE = re.compile(
    r"(?:from|of|after)\s+(?:the\s+)?(?:date\s+of\s+)?(?:your\s+)?"
    r"(purchase|order(?:\s+date)?|ordering|payment|placing)\b|下单|购买之?日",
    re.I,
)
START_DELIVERY = re.compile(
    r"(?:from|of|after)\s+(?:the\s+)?(?:date\s+of\s+)?(?:your\s+)?"
    r"(deliver\w*|receiv\w*|receipt|arrival)\b|签收|收到",
    re.I,
)
FREE_SHIP = re.compile(
    r"free (?:standard )?(?:shipping|delivery)[^.\n]{0,40}?(?:over|above|from|on orders (?:of |over |above )?)"
    r"\s*([$€£]|USD|EUR|GBP|CAD|AUD|A\$|C\$)?\s?(\d{1,4}(?:[.,]\d{2})?)",
    re.I,
)
FREE_SHIP_ZH = re.compile(r"满\s*([$€£]|USD|EUR)?\s?(\d{1,4})\s*(?:美元)?\s*包邮")
BUYER_PAYS = re.compile(
    r"(customer|buyer|you)\w*\s+(?:are|is|will be|shall be)?\s*responsible for\s+(?:the\s+|all\s+)?"
    r"(?:cost of\s+)?return|return shipping (?:costs?|fees?) (?:are|is|will be) (?:paid|covered|borne) by (?:the )?"
    r"(customer|buyer|you)|at (?:the )?(?:customer|buyer)'?s? (?:own )?(?:expense|cost)|"
    r"(customer|buyer)s? (?:pay|cover)s? (?:for )?(?:the )?(?:cost of )?return|"
    r"买家承担|顾客承担",
    re.I,
)
SELLER_PAYS = re.compile(
    r"free returns?|we (?:will )?(?:cover|pay)(?: for)? (?:the )?return|prepaid return label|"
    r"return shipping is free|returns? (?:are|is) free|免费退货",
    re.I,
)
RESTOCK = re.compile(
    r"(\d{1,2})\s?%\s*restocking fee|restocking fee of\s*(\d{1,2})\s?%", re.I
)
NO_RESTOCK = re.compile(r"no restocking fee", re.I)
CURRENCY_WORD = re.compile(r"\b(RMB|CNY)\b|人民币")
ENTITY = re.compile(
    r"\b([A-Z][A-Za-z0-9&.' -]{1,50}?\s(?:Co\.,?\s?Ltd\.?|Ltd\.?|Limited|LLC|L\.L\.C\.|Inc\.?|GmbH|"
    r"UG|S\.?A\.?S\.?|S\.?L\.?|B\.?V\.?|Pty Ltd|Corp\.?|Corporation))(?![A-Za-z])"
)


def claims_from_text(text, source, url):
    out = []

    def add(topic, value, sent):
        out.append(
            {
                "topic": topic,
                "value": value,
                "source": source,
                "url": url,
                "snippet": sent[:240],
            }
        )

    for sent in pages.sentences(text):
        if len(sent) > 600:
            sent = sent[:600]
        has_ret = bool(RETURN_W.search(sent))
        has_ref = bool(REFUND_W.search(sent))
        warranty = bool(WARRANTY_W.search(sent))
        processing = bool(PROCESS_W.search(sent))
        if (has_ret or (has_ref and not processing)) and not (warranty and not has_ret):
            for m in re.finditer(
                NUM + r"\s*[- ]?\s*(?:calendar\s+)?" + DAY, sent, re.I
            ):
                n = int(m.group(1))
                if 7 <= n <= 365 and not re.search(
                    r"business|working|werktag", sent[m.end() : m.end() + 12], re.I
                ):
                    if not (has_ref and processing):
                        add("return_days", n, sent)
                        break
            if START_PURCHASE.search(sent):
                add("return_start", "purchase", sent)
            elif START_DELIVERY.search(sent):
                add("return_start", "delivery", sent)
        if has_ref and processing:
            m = re.search(
                r"(?:within|up to|in)\s+" + NUM + r"\s*(?:business |working )?" + DAY,
                sent,
                re.I,
            )
            if m:
                add("refund_days", int(m.group(1)), sent)
        if SHIP_W.search(sent) and not HANDLING_W.search(sent) and not has_ret:
            m = re.search(RANGE + r"\s*(?:business |working )?" + DAY, sent, re.I)
            if m and int(m.group(1)) <= int(m.group(2)) <= 90:
                add("delivery_days", "%s-%s" % (m.group(1), m.group(2)), sent)
        for rx in (FREE_SHIP, FREE_SHIP_ZH):
            m = rx.search(sent)
            if m:
                amount = float(m.group(2).replace(",", "."))
                add("free_threshold", "%s%g" % (m.group(1) or "", amount), sent)
                break
        if has_ret or "return" in sent.lower():
            if BUYER_PAYS.search(sent):
                add("return_payer", "buyer", sent)
            elif SELLER_PAYS.search(sent):
                add("return_payer", "seller", sent)
        m = RESTOCK.search(sent)
        if m:
            add("restocking_fee", int(m.group(1) or m.group(2)), sent)
        elif NO_RESTOCK.search(sent):
            add("restocking_fee", 0, sent)
        if CURRENCY_WORD.search(sent):
            add("currency_word", "CNY", sent)
        for m in ENTITY.finditer(sent):
            name = re.sub(r"\s+", " ", m.group(1)).strip()
            name = re.sub(
                r"^(?:(?:operated|owned|run|managed|sold|provided)\s+by|by|the|from|and|to|at|for|with|contact)\s+",
                "",
                name,
                flags=re.I,
            )
            add("entity", name, sent)
    for e in pages.emails(text):
        add_email = {
            "topic": "email",
            "value": e,
            "source": source,
            "url": url,
            "snippet": e,
        }
        out.append(add_email)
    return out


def extract(site):
    out = []
    for source, url, text in site.texts():
        out.extend(claims_from_text(text, source, url))
    return out


def group_of(source):
    return "home" if source in ("home", "footer", "announcement") else source


def conflicts(claims, topics=None):
    """Candidate conflicts: {topic, a, b} where two sources disagree."""
    out = []
    by_topic = {}
    for c in claims:
        if topics and c["topic"] not in topics:
            continue
        by_topic.setdefault(c["topic"], []).append(c)
    for topic, items in sorted(by_topic.items()):
        firsts, values = {}, {}
        for c in items:
            g = group_of(c["source"])
            firsts.setdefault(g, c)
            values.setdefault(g, set()).add(_norm(topic, c["value"]))
        groups = list(firsts)
        found = None
        for i, ga in enumerate(groups):
            for gb in groups[i + 1 :]:
                a, b = firsts[ga], firsts[gb]
                va, vb = _norm(topic, a["value"]), _norm(topic, b["value"])
                if va == vb or va in values[gb] or vb in values[ga]:
                    continue
                if topic == "entity" and (va in vb or vb in va):
                    continue
                if topic == "delivery_days" and _overlap(va, vb):
                    continue
                found = {"topic": topic, "a": a, "b": b}
                break
            if found:
                break
        if found:
            out.append(found)
    return out


def _norm(topic, v):
    if topic == "free_threshold":
        return re.sub(r"[^\d.]", "", str(v))
    return str(v).lower()


def _overlap(a, b):
    try:
        a1, a2 = [int(x) for x in a.split("-")]
        b1, b2 = [int(x) for x in b.split("-")]
    except ValueError:
        return False
    return a1 <= b2 and b1 <= a2


if __name__ == "__main__":
    import os

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import storesite

    s = storesite.Site(sys.argv[1])
    for c in extract(s):
        print(json.dumps(c, ensure_ascii=False))
