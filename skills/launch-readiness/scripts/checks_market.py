"""Before Google Shopping ads (GMC) and per-market requirements (EU, DE,
UK, CA, AU, US). Legal gaps are judgements: their badge is [推测].
Python 3.8+, standard library only.
"""

import re

import checks_policy as cp
import pages
import products

TAX_RE = re.compile(
    r"tax(es)? included|incl(uding|\.)? (vat|tax)|inkl\.? mwst|\bttc\b|"
    r"shipping calculated at checkout|calculated at checkout|tax(es)? and shipping|"
    r"shipping and tax|zzgl\.? versand|excl(uding|\.)? (vat|tax)|plus shipping|"
    r"\bvat\b|duties",
    re.I,
)
CART_RE = re.compile(
    r'name=["\']add["\']|add[ -_]?to[ -_]?(cart|bag|basket)|/cart/add|in den warenkorb|'
    r"ajouter au panier|añadir al carrito|buy now|add_to_cart",
    re.I,
)
ADDRESS_RE = re.compile(
    r"\b\d{1,5}\s+\w+(\s\w+){0,3}\s(street|st\.?|road|rd\.?|avenue|ave\.?|lane|blvd|drive|way)\b|"
    r"stra(ße|sse)\s*\d|\bstr\.\s*\d|\b\d{5}\s+[A-ZÀ-ÿ][a-z]|"
    r"\b[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}\b|\b[A-Z]\d[A-Z] ?\d[A-Z]\d\b|"
    r"\b[A-Z]{2}\s\d{5}(-\d{4})?\b|suite \d|room \d|floor|building|district|"
    r"路|号|室",
    re.I,
)
WITHDRAW_RE = re.compile(
    r"(14|fourteen|vierzehn|quatorze|catorce)[ -]?(day|tage|jours|días|giorni|dagen)\w*.{0,120}"
    r"(withdraw|widerruf|rétractation|desistimiento|cancel)|"
    r"(withdraw|widerruf|rétractation|desistimiento|right to cancel|cancel).{0,120}"
    r"(14|fourteen|vierzehn|quatorze|catorce)[ -]?(day|tage|jours|días)",
    re.I | re.S,
)
FORM_RE = re.compile(
    r"withdrawal form|widerrufsformular|muster-?widerruf|formulaire de rétractation|"
    r"model (withdrawal|cancellation) form|cancellation form",
    re.I,
)
ODR_RE = re.compile(
    r"ec\.europa\.eu/consumers/odr|online dispute resolution|os-plattform|"
    r"plateforme (européenne )?de règlement en ligne",
    re.I,
)
SALE_RE = re.compile(
    r'class="[^"]*(compare-at|price--on-sale|price__sale|was-price|old-price|price-compare)',
    re.I,
)
GPSR_RE = re.compile(
    r"manufacturer|hersteller|fabricant|fabricante|responsible person|eu representative|"
    r"eu-verantwortlich|gpsr|product safety|produktsicherheit",
    re.I,
)
UNOPENED_RE = re.compile(
    r"only.{0,40}(unopened|unused|sealed)|(unopened|sealed) items? only", re.I
)
SHIP_TIME_RE = re.compile(
    r"ships? within \d+|processing time|\d+\s*(-|to|–)\s*\d+\s*(business |working )?days|"
    r"within \d+ (business |working )?days",
    re.I,
)
CA_CUR_RE = re.compile(r"\bCAD\b|C\$|canadian dollar", re.I)


def run(ctx, claims):
    gmc(ctx, claims)
    legal(ctx, claims)


def readable(s):
    return [x for x in s.texts()]


def gmc(ctx, claims):
    s = ctx.site
    url, mails, _ph = cp.contact_signals(s)
    if not s.online():
        ctx.add("GMC-01", "unknown", note="needs_live_site")
    else:
        visible = (
            s.footer_has(r"contact|kontakt")
            or pages.emails(pages.html_to_text(s.footer + s.header))
            or "mailto:" in (s.footer + s.header)
        )
        ctx.add("GMC-01", "pass" if visible else "fail", where=url)
    red = s.status.get("https_redirect")
    if not s.online() or red is None:
        ctx.add("GMC-02", "unknown")
    else:
        ctx.add(
            "GMC-02",
            "pass" if red else "fail",
            where="http://%s/" % s.status.get("domain"),
        )
    live_pdps = [p for p in s.pdps if p.get("html") and not s.paste]
    if not live_pdps:
        ctx.add("GMC-03", "unknown", note="no_product_page")
        ctx.add("GMC-05", "unknown", note="no_product_page")
    else:
        tax = any(TAX_RE.search(p["text"] + p["html"][:0]) for p in live_pdps)
        ctx.add("GMC-03", "pass" if tax else "fail", where=live_pdps[0]["url"])
        no_cart = [p["url"] for p in live_pdps if not CART_RE.search(p["html"])]
        soldout = [p.get("handle") for p in live_pdps if _sold_out(s, p.get("handle"))]
        bad = no_cart if len(no_cart) == len(live_pdps) else []
        if bad or (soldout and len(soldout) == len(live_pdps)):
            ctx.add(
                "GMC-05",
                "fail",
                where=(bad or [live_pdps[0]["url"]])[0],
                evidence=bad[:2] or ["sold out: %s" % ", ".join(map(str, soldout))],
            )
        else:
            ctx.add("GMC-05", "pass", where=live_pdps[0]["url"])
    lang_gap = language_gaps(ctx)
    if lang_gap is None:
        ctx.add("GMC-04", "unknown")
    else:
        ctx.add("GMC-04", "fail" if lang_gap else "pass", evidence=lang_gap[:3])
    items = [x for x in readable(s) if x[0] != "home"]
    _i, sent = cp.first_match(ADDRESS_RE, items)
    if sent:
        ctx.add("GMC-06", "pass", evidence=[sent])
    elif not s.home and not s.paste:
        ctx.add("GMC-06", "unknown")
    else:
        ctx.add("GMC-06", "fail")
    pdp_text = [p.get("text") for p in s.pdps]
    if products.has_subscription(s.products, pdp_text):
        ok = any(
            re.search(
                r"every \d+|per month|/\s?mo|monthly|billed|cancel any ?time",
                t or "",
                re.I,
            )
            for t in pdp_text
        )
        ctx.add("GMC-07", "pass" if ok else "fail")
    else:
        ctx.add("GMC-07", "skip", note="no_subscription_seen")
    bad = [r for r in ("CNS-01", "CNS-03", "CNS-07") if ctx.result(r) == "fail"]
    if bad:
        f = ctx.findings[bad[0]]
        ctx.add(
            "GMC-08",
            "fail",
            where=f["where"],
            evidence=f["evidence"],
            note="see " + ",".join(bad),
        )
    else:
        ctx.add("GMC-08", "pass")


def _sold_out(s, handle):
    for p in s.products:
        if p.get("handle") == handle:
            return not products.in_stock(p)
    return False


def language_gaps(ctx):
    """Target countries whose language has no version, or None if unknown."""
    import markets

    s = ctx.site
    if not s.home:
        return None
    have = set()
    lang = (pages.html_lang(s.home) or "en").lower().split("-")[0]
    have.add(lang)
    for code, _h in pages.hreflang_links(s.home):
        have.add(code.lower().split("-")[0])
    gaps = []
    for c in ctx.lang_countries:
        want = markets.languages(c)
        if want and not (set(want) & have):
            gaps.append("%s: %s" % (c, "/".join(want)))
    return gaps


def legal(ctx, claims):
    s = ctx.site
    items = cp.policy_items(s, "refund-policy", "terms-of-service", "legal-notice")
    for p in s.info_pages("faq"):
        items.append(("faq", p["url"], p["text"]))
    blob = "\n".join(t for _l, _u, t in items)
    where = items[0][1] if items else None
    days = [
        c["value"]
        for c in claims
        if c["topic"] == "return_days"
        and c["source"] in ("policy:refund-policy", "policy:terms-of-service")
    ]
    purchase = any(
        c["value"] == "purchase" for c in claims if c["topic"] == "return_start"
    )
    has_14 = bool(WITHDRAW_RE.search(blob)) or (
        days and max(days) >= 14 and not purchase
    )
    if not items:
        for rid in ("EU-01", "EU-02", "EU-03", "EU-04", "UK-01", "UK-02"):
            ctx.add(
                rid,
                "fail"
                if s.paste or s.policy_result("refund-policy") == "not_found"
                else "unknown",
                note="no_return_text",
            )
    else:
        ev = ["return days: %s" % ", ".join(map(str, days))] if days else None
        ctx.add("EU-01", "pass" if has_14 else "fail", where=where, evidence=ev)
        ctx.add("UK-01", "pass" if has_14 else "fail", where=where, evidence=ev)
        ctx.add("EU-02", "pass" if FORM_RE.search(blob) else "fail", where=where)
        refund = [c for c in claims if c["topic"] == "refund_days"]
        slow = [c for c in refund if c["value"] > 14]
        ctx.add(
            "EU-03",
            "fail" if (slow or not refund) else "pass",
            where=where,
            evidence=[slow[0]["snippet"]] if slow else None,
            note=None if refund else "not_stated",
        )
        ctx.add(
            "UK-02",
            "fail" if (slow or not refund) else "pass",
            where=where,
            evidence=[slow[0]["snippet"]] if slow else None,
        )
        payer = [c for c in claims if c["topic"] == "return_payer"]
        ctx.add("EU-04", "pass" if payer else "fail", where=where)
    odr = cp.first_match(ODR_RE, s.texts())
    odr_html = ODR_RE.search(s.footer or "")
    if odr[1] or odr_html:
        ctx.add(
            "EU-05",
            "fail",
            where=odr[0][1] if odr[0] else s.home_url,
            evidence=[odr[1] or "footer link"],
        )
    else:
        ctx.add("EU-05", "pass")
    sale = any(SALE_RE.search(p.get("html") or "") for p in s.pdps) or any(
        _on_sale(p) for p in s.products
    )
    ctx.add("EU-06", "manual" if sale else "pass")
    live = [p for p in s.pdps if p.get("text")]
    if not live:
        ctx.add("EU-07", "unknown", note="no_product_page")
    else:
        ok = any(GPSR_RE.search(p["text"]) for p in live)
        ctx.add("EU-07", "pass" if ok else "fail", where=live[0]["url"])
    if ctx.result("CNS-08") == "fail" and s.policy("legal-notice"):
        f = ctx.findings["CNS-08"]
        ctx.add("DE-01", "fail", where=f["where"], evidence=f["evidence"])
    else:
        ctx.add("DE-01", "same", same_as="POL-06")
    _u, mails, phones = cp.contact_signals(s)
    miss = []
    if not phones:
        miss.append("phone")
    if not cp.first_match(ADDRESS_RE, [x for x in s.texts() if x[0] != "home"])[1]:
        miss.append("address")
    if not CA_CUR_RE.search(blob + (s.home or "")[:200000]):
        miss.append("currency")
    if not s.policy("refund-policy"):
        miss.append("refund_terms")
    ctx.add(
        "CA-01",
        "fail" if miss else "pass",
        note=",".join(miss) or None,
        data={"missing": miss},
    )
    unopened = cp.first_match(UNOPENED_RE, items)[1]
    if ctx.result("CON-06") == "fail":
        f = ctx.findings["CON-06"]
        ctx.add("AU-01", "fail", where=f["where"], evidence=f["evidence"])
    elif unopened and not cp.FAULTY_RE.search(blob):
        ctx.add("AU-01", "fail", where=where, evidence=[unopened])
    else:
        ctx.add("AU-01", "pass")
    ship = cp.policy_items(s, "shipping-policy") + [
        ("faq", p["url"], p["text"]) for p in s.info_pages("faq")
    ]
    if (
        any(c["topic"] == "delivery_days" for c in claims)
        or cp.first_match(SHIP_TIME_RE, ship)[1]
    ):
        ctx.add("US-01", "pass")
    else:
        ctx.add("US-01", "fail" if (s.home or s.paste) else "unknown")


def _on_sale(p):
    for v in products.variants(p):
        a, b = (
            products.to_float(v.get("compare_at_price")),
            products.to_float(v.get("price")),
        )
        if a and b and a > b:
            return True
    return False
