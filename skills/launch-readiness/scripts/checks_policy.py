"""Policy pages: presence (POL), content (CON), conflicts (CNS), entity (BIZ).
Python 3.8+, standard library only.
"""

import re

import extract_claims
import pages
import products
from ruleset import first_match

FOOTER_RE = {
    "refund-policy": r"refund|return|widerruf|retour|rückgabe|devoluc|退货",
    "shipping-policy": r"shipping|delivery|versand|livraison|envío|运费",
    "privacy-policy": r"privacy|datenschutz|confidentialit|privacidad|隐私",
    "terms-of-service": r"terms|\bagb\b|conditions|cgv|términos",
    "legal-notice": r"impressum|imprint|legal notice|mentions légales|aviso legal",
}
PLACEHOLDER_RE = re.compile(
    r"\[(?:insert|your|company|store|business|email|address|phone|name|date)[^\]]{0,40}\]|"
    r"your store name|\{\{\s*\w+|\bmy store\b|lorem ipsum|xxx@|\[email\]",
    re.I,
)
FINAL_RE = re.compile(
    r"all sales (?:are )?final|\bno refunds?\b|\bno returns?\b|non-?refundable|"
    r"概不退换|不退不换",
    re.I,
)
FAULTY_RE = re.compile(
    r"defect|damag|faulty|wrong item|incorrect|quality|not as described|unless|except|"
    r"质量问题|破损",
    re.I,
)
EXEMPT_RE = re.compile(
    r"gift ?card|personali[sz]|custom(?:ized|-made)|hygiene|underwear|earring|"
    r"shipping (?:fee|cost|charge)|duties|customs|insurance|e-?gift",
    re.I,
)
FREE_MAIL = re.compile(
    r"@(gmail|qq|163|126|yeah|outlook|hotmail|live|yahoo|foxmail|sina|aliyun|icloud|"
    r"protonmail|gmx)\.",
    re.I,
)
TEMPLATE_INFO = re.compile(
    r"\bexample\.(?:com|org|net)\b|123 (?:main|fake) st|\b555[- .]\d{4}\b|"
    r"your@email|email@example|you@yourstore",
    re.I,
)
CHINA_RE = re.compile(
    r"\bchina\b|shenzhen|guangzhou|yiwu|dongguan|zhejiang|guangdong|"
    r"中国|深圳|广州|义乌",
    re.I,
)
TOPIC_RULE = {
    "return_days": "CNS-01",
    "delivery_days": "CNS-02",
    "return_payer": "CNS-03",
    "free_threshold": "CNS-04",
    "restocking_fee": "CNS-05",
    "refund_days": "CNS-07",
}
PLATFORM_NAMES = re.compile(
    r"shopify|google|meta|facebook|paypal|stripe|klarna|apple|amazon|afterpay|"
    r"microsoft|tiktok|pinterest|klaviyo",
    re.I,
)


def run(ctx, claims):
    s = ctx.site
    presence(ctx)
    contact(ctx)
    content(ctx, claims)
    conflicts(ctx, claims)
    business(ctx, claims)
    return s


def policy_items(s, *handles):
    out = []
    for h in handles:
        p = s.policy(h)
        if p:
            out.append((h, p["url"], p["text"]))
    return out


def presence(ctx):
    s = ctx.site
    for rid, handle in (
        ("POL-01", "refund-policy"),
        ("POL-02", "shipping-policy"),
        ("POL-03", "privacy-policy"),
        ("POL-04", "terms-of-service"),
    ):
        p = s.policy(handle)
        link = s.footer_has(FOOTER_RE[handle]) if s.online() else None
        if p:
            if s.online() and not link and not s.policy_result(handle) == "skipped":
                ctx.add(
                    rid,
                    "fail",
                    where=p["url"],
                    level="P1",
                    note="exists_not_in_footer",
                    evidence=["%s (%s)" % (handle, p["url"])],
                )
            else:
                ctx.add(rid, "pass", where=p["url"])
        elif s.paste:
            ctx.add(rid, "fail", note="not_in_pasted_text")
        elif not s.home:
            ctx.add(rid, "unknown", note="homepage_unavailable")
        elif link:
            ctx.add(rid, "unknown", where=link, note="footer_link_not_read")
        elif s.policy_result(handle) in ("not_found", None) or s.platform != "shopify":
            ctx.add(
                rid,
                "fail",
                note="no_page_no_footer_link",
                where=(s.policies.get(handle) or {}).get("url"),
            )
        else:
            ctx.add(rid, "unknown", note="fetch_" + str(s.policy_result(handle)))
    # POL-07 template text / too short
    bad = []
    for h, p in sorted(s.policies.items()):
        t = p.get("text") or ""
        if not t:
            continue
        m = PLACEHOLDER_RE.search(t)
        if m:
            bad.append("%s: %s" % (h, pages.snippet(t, m.start(), m.end(), 40)))
        elif len(t) < 200:
            bad.append("%s: %d chars" % (h, len(t)))
    if bad:
        ctx.add("POL-07", "fail", evidence=bad)
    elif s.policies:
        ctx.add("POL-07", "pass")
    else:
        ctx.add("POL-07", "unknown")
    pdp_html = [p.get("html") or p.get("text") for p in s.pdps]
    if products.has_subscription(s.products, pdp_html):
        if s.policy("subscription-policy"):
            ctx.add("POL-08", "pass")
        else:
            ctx.add("POL-08", "fail", note="subscription_products_no_policy")
    else:
        ctx.add("POL-08", "skip", note="no_subscription_seen")


def contact_signals(s):
    """(contact page url, emails, phones) seen anywhere readable."""
    page = s.info_pages("contact") or (
        [s.policy("contact-information")] if s.policy("contact-information") else []
    )
    url = (
        page[0]["url"]
        if page
        else (s.footer_has(r"contact|kontakt") if s.online() else None)
    )
    texts = [t for _l, _u, t in s.texts()]
    blob = "\n".join(texts)
    if s.home:
        blob += "\n" + " ".join(re.findall(r'mailto:([^"\'?>\s]+)', s.home))
    return (
        url,
        pages.emails(blob),
        pages.phones(
            "\n".join([p["text"] for p in page] + [pages.html_to_text(s.footer)])
        ),
    )


def contact(ctx):
    s = ctx.site
    url, mails, phones = contact_signals(s)
    if url or mails:
        ctx.add("POL-05", "pass", where=url, evidence=mails[:2])
    elif not s.home and not s.paste:
        ctx.add("POL-05", "unknown")
    else:
        ctx.add("POL-05", "fail")
    # POL-06 Impressum (DE/AT)
    legal = s.policy("legal-notice")
    if not ctx.targets("DE", "AT"):
        ctx.add("POL-06", "skip")
    elif not legal:
        ctx.add(
            "POL-06",
            "fail" if (s.home or s.paste) else "unknown",
            note="no_legal_notice",
        )
    else:
        t = legal["text"]
        miss = []
        if not pages.emails(t):
            miss.append("email")
        if not re.search(r"\b\d{4,5}\b", t):
            miss.append("address")
        link = s.footer_has(FOOTER_RE["legal-notice"]) if s.online() else True
        if not link:
            miss.append("footer_link")
        ctx.add(
            "POL-06",
            "fail" if miss else "pass",
            where=legal["url"],
            note=",".join(miss) or None,
        )


def returns_texts(s):
    items = policy_items(s, "refund-policy", "terms-of-service", "shipping-policy")
    for p in s.info_pages("faq"):
        items.append(("faq", p["url"], p["text"]))
    for p in s.pdps:
        if p.get("text"):
            items.append(("pdp", p["url"], p["text"]))
    if s.home:
        items.append(
            (
                "home",
                s.home_url,
                pages.announcement_text(s.home) + "\n" + pages.html_to_text(s.footer),
            )
        )
    return items


def topic(claims, name, sources=None):
    return [
        c
        for c in claims
        if c["topic"] == name and (sources is None or c["source"] in sources)
    ]


def content(ctx, claims):
    s = ctx.site
    refund = s.policy("refund-policy")
    if refund:
        t = refund["text"]
        rc = [c for c in claims if c["source"] == "policy:refund-policy"]
        need = {
            "window": any(c["topic"] == "return_days" for c in rc)
            or FINAL_RE.search(t),
            "start": any(c["topic"] == "return_start" for c in rc)
            or re.search(
                r"from the (date|day)|after (you )?receiv|of (delivery|receipt)",
                t,
                re.I,
            ),
            "condition": re.search(
                r"unused|unworn|original (packaging|condition)|tags|unopened|"
                r"same condition|resal",
                t,
                re.I,
            ),
            "how": re.search(
                r"contact|email|portal|start a return|request|initiate|rma|form",
                t,
                re.I,
            ),
            "refund_time": any(c["topic"] == "refund_days" for c in rc)
            or re.search(r"refund.{0,80}(days|processed|issued)", t, re.I | re.S),
        }
        miss = [k for k, v in need.items() if not v]
        ctx.add(
            "CON-01",
            "fail" if miss else "pass",
            where=refund["url"],
            note=",".join(miss) or None,
            data={"missing": miss},
        )
    else:
        ctx.add(
            "CON-01",
            "skip" if (s.paste or ctx.result("POL-01") == "fail") else "unknown",
            note="no_refund_policy",
        )
    starts = [c for c in topic(claims, "return_start") if c["value"] == "purchase"]
    if starts:
        ctx.add(
            "CON-02", "fail", where=starts[0]["url"], evidence=[starts[0]["snippet"]]
        )
    else:
        ctx.add("CON-02", "pass" if refund else "unknown")
    ship = s.policy("shipping-policy")
    if ship:
        t = ship["text"]
        need = {
            "processing": re.search(
                r"process|handling|dispatch|prepar|ships? within", t, re.I
            ),
            "transit": re.search(
                r"(deliver|transit|arriv)\w*.{0,60}\d", t, re.I | re.S
            ),
            "rates": re.search(
                r"free (shipping|delivery)|rate|cost|fee|calculated at checkout|"
                r"[$€£]\s?\d",
                t,
                re.I,
            ),
            "tracking": re.search(r"track", t, re.I),
        }
        miss = [k for k, v in need.items() if not v]
        ctx.add(
            "CON-03",
            "fail" if miss else "pass",
            where=ship["url"],
            note=",".join(miss) or None,
            data={"missing": miss},
        )
    else:
        ctx.add(
            "CON-03",
            "skip" if (s.paste or ctx.result("POL-02") == "fail") else "unknown",
            note="no_shipping_policy",
        )
    fees = [
        c
        for c in claims
        if c["source"] == "policy:refund-policy"
        and (
            (c["topic"] == "restocking_fee" and c["value"])
            or (c["topic"] == "return_payer" and c["value"] == "buyer")
        )
    ]
    if fees and s.pdps and not s.paste:
        on_pdp = any(
            re.search(r"restocking|return", p.get("text") or "", re.I) for p in s.pdps
        )
        ctx.add(
            "CON-04",
            "pass" if on_pdp else "fail",
            where=fees[0]["url"],
            evidence=[fees[0]["snippet"]],
        )
    else:
        ctx.add("CON-04", "pass" if not fees else "unknown")
    restock = [c for c in topic(claims, "restocking_fee") if c["value"]]
    ctx.add(
        "CON-05",
        "fail" if restock else "pass",
        evidence=[restock[0]["snippet"]] if restock else None,
        where=restock[0]["url"] if restock else None,
    )
    final_check(ctx)
    china = first_match(CHINA_RE, policy_items(s, "refund-policy"))[1]
    payer = topic(claims, "return_payer", ("policy:refund-policy",))
    if china and re.search(r"return|send|ship|address", china, re.I) and not payer:
        ctx.add("CON-07", "fail", where=refund["url"], evidence=[china])
    else:
        ctx.add("CON-07", "pass" if refund else "unknown")


def final_check(ctx):
    s = ctx.site
    hit = None
    for label, url, text in returns_texts(s):
        doc_exempt = bool(FAULTY_RE.search(text)) and label.startswith(
            ("refund", "terms")
        )
        for sent in pages.sentences(text):
            if (
                FINAL_RE.search(sent)
                and not FAULTY_RE.search(sent)
                and not EXEMPT_RE.search(sent)
            ):
                if not doc_exempt:
                    hit = (url, sent)
                    break
        if hit:
            break
    level = "P0" if ctx.targets("AU", "EU", "UK") else "P1"
    if hit:
        ctx.add("CON-06", "fail", where=hit[0], evidence=[hit[1]], level=level)
    else:
        ctx.add("CON-06", "pass", level=level)


def conflicts(ctx, claims):
    found = dict((c["topic"], c) for c in extract_claims.conflicts(claims))
    for t, rid in sorted(TOPIC_RULE.items()):
        c = found.get(t)
        if c:
            ctx.add(
                rid,
                "fail",
                where=c["a"]["url"],
                evidence=[
                    "%s: %s" % (c["a"]["source"], c["a"]["snippet"]),
                    "%s: %s" % (c["b"]["source"], c["b"]["snippet"]),
                ],
                data={"a": c["a"]["value"], "b": c["b"]["value"]},
            )
        else:
            ctx.add(rid, "pass")
    cur = (ctx.site.meta or {}).get("currency")
    cny = topic(claims, "currency_word")
    if cny and cur and cur != "CNY":
        ctx.add(
            "CNS-06",
            "fail",
            where=cny[0]["url"],
            evidence=[
                "%s: %s" % (cny[0]["source"], cny[0]["snippet"]),
                "store: %s" % cur,
            ],
        )
    else:
        ctx.add("CNS-06", "pass")
    ents = [c for c in topic(claims, "entity") if not PLATFORM_NAMES.search(c["value"])]
    found = extract_claims.conflicts(ents)
    if found:
        c = found[0]
        ctx.add(
            "CNS-08",
            "fail",
            where=c["a"]["url"],
            evidence=[
                "%s: %s" % (c["a"]["source"], c["a"]["value"]),
                "%s: %s" % (c["b"]["source"], c["b"]["value"]),
            ],
        )
    else:
        ctx.add("CNS-08", "pass")


def business(ctx, claims):
    s = ctx.site
    ctx.add("BIZ-01", "same", same_as="CNS-08")
    _url, mails, phones = contact_signals(s)
    own = [
        m
        for m in mails
        if not re.search(r"shopify|sentry|wixpress|@example\.(com|org)$", m)
    ]
    free = [m for m in own if FREE_MAIL.search(m)]
    ctx.add(
        "BIZ-02", "fail" if free else ("pass" if own else "unknown"), evidence=free[:2]
    )
    tmpl = first_match(TEMPLATE_INFO, s.texts())
    if tmpl[1]:
        ctx.add("BIZ-03", "fail", where=tmpl[0][1], evidence=[tmpl[1]])
    else:
        ctx.add("BIZ-03", "pass" if (s.home or s.paste) else "unknown")
    local = [p for p in phones if not p.startswith(("+", "00"))]
    cn = [p for p in phones if re.sub(r"[\s.\-()]", "", p).startswith(("+86", "0086"))]
    hours = re.search(
        r"hours|mon|am\b|pm\b|time ?zone|\b(EST|PST|GMT|UTC|CET)\b",
        "\n".join(t for _l, _u, t in s.texts()),
        re.I,
    )
    if (local and len(ctx.countries) > 1) or (cn and not hours):
        ctx.add("BIZ-04", "fail", evidence=(cn or local)[:2])
    else:
        ctx.add(
            "BIZ-04", "pass" if phones else "skip", note=None if phones else "no_phone"
        )
