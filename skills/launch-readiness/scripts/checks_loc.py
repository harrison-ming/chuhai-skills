"""Privacy and cookies (PRV), multi-market and translation (LOC).
Python 3.8+, standard library only.
"""

import json
import re

import checks_policy as cp
import hreflang
import markets
import pages

TRACKING = [
    ("meta_pixel", ("connect.facebook.net", "fbq(")),
    ("tiktok_pixel", ("analytics.tiktok.com",)),
    ("ga4", ("gtag/js?id=g-", "gtag('config', 'g-", 'gtag("config", "g-')),
    ("google_ads", ("gtag/js?id=aw-",)),
    ("gtm", ("googletagmanager.com/gtm.js",)),
    ("pinterest_tag", ("s.pinimg.com/ct/core.js", "pintrk(")),
    ("snap_pixel", ("sc-static.net/scevent", "snaptr(")),
    ("microsoft_uet", ("bat.bing.com",)),
    ("reddit_pixel", ("redditstatic.com/ads",)),
]
CONSENT_RE = re.compile(
    r"cookiebot|onetrust|cookielaw|cookieyes|usercentrics|iubenda|termly|consentmanager|"
    r"didomi|complianz|cookie-law-info|cookie-notice|quantcast|trustarc|klaro|osano|"
    r"privacy-banner|shopify-pc__banner|consent-banner|cookie-banner|cookie_consent|"
    r"cookieconsent|pandectes|consentmo|enzuzo|axeptio|cookiefirst",
    re.I,
)
PRIVACY_ITEMS = [
    ("controller_contact", r"contact|e-?mail|@"),
    (
        "purposes",
        r"purpose|we use (your|the|this|personal)|use (of )?(your )?(personal )?information",
    ),
    ("recipients", r"share|third[- ]part|service provider|disclos|processor"),
    (
        "transfer",
        r"transfer|outside (of )?(the )?(eea|eu|european|uk)|international|countries",
    ),
    (
        "retention",
        r"retain|retention|as long as|keep (your )?(personal )?(data|information)|delete",
    ),
    ("rights", r"right to|your rights|access, correct|delete your|erasure|opt[- ]out"),
]
LEGAL_BASIS_RE = re.compile(
    r"legal basis|lawful basis|legitimate interest|consent|contract", re.I
)
ENGLISH_WORDS = set(
    "the and your with you our are for cart free shipping add more about sign all from "
    "this that will have shop now new collection sale search menu account".split()
)
UNIT_METRIC = re.compile(r"\b\d+(\.\d+)?\s?(cm|mm|kg|ml)\b", re.I)
UNIT_IMPERIAL = re.compile(
    r"\b\d+(\.\d+)?\s?(in|inch|inches|lbs?|oz|ft)\b|\d+(\.\d+)?\s?\"", re.I
)
DUTY_RE = re.compile(
    r"dut(y|ies)|customs|import (tax|fee|charge)|\bzoll|douane|\bDDP\b|\bDAP\b|"
    r"vat included|taxes included|inkl\.? mwst|tax(es)? (are )?included|关税",
    re.I,
)


def run(ctx, claims):
    privacy(ctx)
    localisation(ctx)


def tracking_tags(src):
    low = (src or "").lower()
    return [n for n, pats in TRACKING if any(p in low for p in pats)]


def privacy(ctx):
    s = ctx.site
    p = s.policy("privacy-policy")
    if not p:
        ctx.add(
            "PRV-01",
            "skip" if (s.paste or ctx.result("POL-03") == "fail") else "unknown",
            note="no_privacy_policy",
        )
    else:
        t = p["text"]
        miss = [k for k, rx in PRIVACY_ITEMS if not re.search(rx, t, re.I)]
        if ctx.targets("EU", "UK") and not LEGAL_BASIS_RE.search(t):
            miss.append("legal_basis")
        ctx.add(
            "PRV-01",
            "fail" if miss else "pass",
            where=p["url"],
            note=",".join(miss) or None,
            data={"missing": miss},
        )
    tags = tracking_tags(s.home) if s.online() else []
    if not s.online():
        ctx.add("PRV-02", "unknown")
        ctx.add("PRV-03", "unknown")
    else:
        ctx.add("PRV-02", "manual" if tags else "pass", evidence=tags[:4])
        if not tags:
            ctx.add("PRV-03", "pass", note="no_tracking_seen")
        else:
            seen = CONSENT_RE.search(s.home or "")
            ctx.add(
                "PRV-03",
                "pass" if seen else "fail",
                where=s.home_url,
                evidence=["tracking: " + ", ".join(tags[:4])],
            )
    ctx.add("PRV-04", "unknown", note="needs_browser")


def localisation(ctx):
    s = ctx.site
    if not s.online():
        for rid in ("LOC-01", "LOC-02", "LOC-03", "LOC-04", "LOC-05", "LOC-09"):
            ctx.add(rid, "unknown", note="needs_live_site")
    else:
        selector = markets.localization_countries(s.home) or re.search(
            r"currency[-_ ]?(selector|switcher|converter|picker)|country[-_ ]?(selector|picker)|"
            r"localization_form|/localization",
            s.home,
            re.I,
        )
        ctx.add("LOC-01", "pass" if selector else "fail", where=s.home_url)
        currencies(ctx)
        versions = [
            {
                "url": s.home_url,
                "final_url": s.home_url,
                "pairs": pages.hreflang_links(s.home),
            }
        ]
        for m in s.markets:
            h = m.get("market_home")
            if h and h.get("html"):
                versions.append(
                    {
                        "url": h["url"],
                        "final_url": h["url"],
                        "pairs": pages.hreflang_links(h["html"]),
                    }
                )
        res = hreflang.check(versions)
        if res["status"] == "absent":
            ctx.add(
                "LOC-03",
                "fail"
                if len(ctx.lang_countries) > 1 and _languages_needed(ctx)
                else "pass",
                note="no_annotations",
            )
        else:
            lines = [hreflang.describe(p, "en") for p in res["problems"]]
            ctx.add(
                "LOC-03",
                "fail" if res["problems"] else "pass",
                where=s.home_url,
                evidence=lines[:3],
                data=res,
            )
        canon_bad = []
        for m in s.markets:
            h = m.get("market_home")
            if h and h.get("html"):
                c = pages.canonical(h["html"])
                if c and hreflang.norm_url(c) != hreflang.norm_url(h["url"]):
                    canon_bad.append("%s -> %s" % (h["url"], c))
        ctx.add(
            "LOC-04",
            "fail" if canon_bad else ("pass" if s.markets else "skip"),
            evidence=canon_bad[:2],
        )
        translation(ctx)
        same_policy(ctx)
    han(ctx)
    units(ctx)
    texts = cp.policy_items(s, "shipping-policy", "refund-policy", "terms-of-service")
    texts += [("faq", p["url"], p["text"]) for p in s.info_pages("faq")]
    texts += [("pdp", p["url"], p.get("text") or "") for p in s.pdps]
    if s.home:
        texts.append(("home", s.home_url, pages.html_to_text(s.footer)))
    hit = cp.first_match(DUTY_RE, texts)[1]
    ctx.add(
        "LOC-08",
        "pass" if hit else ("fail" if texts else "unknown"),
        evidence=[hit] if hit else None,
    )
    ctx.add("LOC-10", "info" if s.markets else "skip")


def _languages_needed(ctx):
    langs = set()
    for c in ctx.lang_countries:
        langs.update(markets.languages(c)[:1])
    return len(langs) > 1


def currencies(ctx):
    s = ctx.site
    if s.platform != "shopify":
        ctx.add("LOC-02", "skip", note="not_shopify")
        return
    bad, checked = [], 0
    for m in s.markets:
        h = m.get("market_home")
        region = (m.get("code") or "").split("-")[-1].upper()
        want = markets.currency("GB" if region == "UK" else region)
        if not (h and h.get("html") and want):
            continue
        mm = re.search(r"Shopify\.currency\s*=\s*(\{[^}]*\})", h["html"])
        active = None
        if mm:
            try:
                active = json.loads(mm.group(1)).get("active")
            except ValueError:
                active = None
        if active:
            checked += 1
            if active != want:
                bad.append("%s: %s (expected %s)" % (m.get("code"), active, want))
    ctx.add(
        "LOC-02",
        "fail" if bad else ("pass" if checked else "unknown"),
        evidence=bad[:3],
    )


def english_share(text):
    words = re.findall(r"[A-Za-zÀ-ɏ]+", text or "")
    if len(words) < 30:
        return None
    hits = sum(1 for w in words if w.lower() in ENGLISH_WORDS)
    return min(1.0, hits / float(len(words)) / 0.3)


def translation(ctx):
    s = ctx.site
    bad, checked = [], 0
    for m in s.markets:
        h = m.get("market_home")
        lang = (m.get("code") or "").split("-")[0].lower()
        if not (h and h.get("html")) or lang in ("en", "x"):
            continue
        checked += 1
        if "translation missing" in h["html"].lower():
            i = h["html"].lower().find("translation missing")
            bad.append(
                "%s: %s" % (m.get("code"), pages.html_to_text(h["html"][i : i + 120]))
            )
            continue
        share = english_share(pages.page_text(h["html"]))
        if share is not None and share > 0.3:
            bad.append("%s: ~%d%% English" % (m.get("code"), int(share * 100)))
    ctx.add(
        "LOC-05", "fail" if bad else ("pass" if checked else "skip"), evidence=bad[:3]
    )


def same_policy(ctx):
    s = ctx.site
    main = s.policy("refund-policy")
    legal_gap = ctx.result("EU-01") == "fail" or ctx.result("UK-01") == "fail"
    same = []
    for m in s.markets:
        p = m.get("market_policy")
        region = (m.get("code") or "").split("-")[-1].upper()
        if region not in markets.EU and region not in ("GB", "UK"):
            continue
        if main and p and p.get("text") and p["text"].strip() == main["text"].strip():
            same.append(m.get("code"))
    ctx.add(
        "LOC-09",
        "fail" if (same and legal_gap) else ("pass" if s.markets else "skip"),
        evidence=same[:3],
    )


def han(ctx):
    s = ctx.site
    hits = []
    for p in s.pdps:
        src = p.get("html") or p.get("text") or ""
        lang = (pages.html_lang(src) or "en").lower()
        if lang.startswith(("zh", "ja", "ko")):
            continue
        body = pages.page_text(pages.main_html(src)) if p.get("html") else p.get("text")
        runs = pages.han_runs(body)
        if runs:
            hits.append("%s: %s" % (p.get("url"), " ".join(runs[:3])[:80]))
    ctx.add(
        "LOC-06",
        "fail" if hits else ("pass" if s.pdps else "unknown"),
        evidence=hits[:2],
    )


def units(ctx):
    s = ctx.site
    if not s.pdps:
        ctx.add("LOC-07", "unknown")
        return
    blob = "\n".join(p.get("text") or "" for p in s.pdps)
    metric, imperial = bool(UNIT_METRIC.search(blob)), bool(UNIT_IMPERIAL.search(blob))
    us_gap = ctx.targets("US") and metric and not imperial
    eu_gap = ctx.targets("EU", "UK", "AU", "CA") and imperial and not metric
    ctx.add(
        "LOC-07",
        "fail" if (us_gap or eu_gap) else "pass",
        note="metric_only" if us_gap else ("imperial_only" if eu_gap else None),
    )
