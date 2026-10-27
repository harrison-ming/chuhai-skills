"""Find ad / analytics tags in saved page source and public container scripts.

Pure functions on text; no network. Signatures come from
references/tracking-signatures.json (calibrated on real stores). A hit from
a signature whose status is not "calibrated" can support at most the
"inferred" badge; calibrated hits support "on_page" (code is in the page,
which does not prove it fires).

Bare vendor domains are never enough: cookie-consent inventories and
script-blocking lists mention vendor URLs without installing anything, so
script / loader signatures require code context (see ``code_context``).

Hit dict keys:
    platform     meta | ga4 | google_ads | google_tag | gtm | tiktok |
                 pinterest | snap | klaviyo | microsoft | reddit | ua | shopify
    id           the ID read (None when the signature carries no ID)
    install_via  app_pixel | custom_pixel | theme_code | gtm_container |
                 google_tag_script
    kind         signature kind (shopify_app_pixel, inline_call, ...)
    calibrated   bool
    source       raw/<file>
    detail       short plain note (never configuration values or tokens)
    problem      None | placeholder | bad_format
Python 3.8+, standard library only.
"""

import html
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SIGNATURES_PATH = os.path.join(HERE, "..", "references", "tracking-signatures.json")

# Display order of platforms in reports and CSV.
PLATFORMS = (
    "meta",
    "ga4",
    "google_ads",
    "google_tag",
    "gtm",
    "tiktok",
    "pinterest",
    "snap",
    "klaviyo",
    "microsoft",
    "reddit",
)
AD_PLATFORMS = ("meta", "google_ads", "tiktok", "pinterest", "snap", "microsoft")
GOOGLE_PREFIX = {"G": "ga4", "AW": "google_ads", "GT": "google_tag", "DC": "google_tag"}
GOOGLE_PREFIX["MC"] = "google_tag"

ID_RE = {
    "meta": re.compile(r"\d{10,20}"),
    "google": re.compile(r"(?:G|AW|GT|DC|MC)-[A-Za-z0-9]{4,14}"),
    "tiktok": re.compile(r"[A-Z0-9]{20}"),
    "pinterest": re.compile(r"\d{13}"),
    "snap": re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"),
    "gtm": re.compile(r"GTM-[A-Z0-9]{4,10}"),
    "klaviyo": re.compile(r"[A-Za-z0-9]{6}"),
}

Q = r"\\?['\"]"  # a quote, optionally backslash-escaped (code stored in JSON)
# Loose loader / init calls: capture whatever ID text is there, then validate,
# so template placeholders ("YOUR_PIXEL_ID") are caught as T10 problems.
LOOSE_CALLS = [
    ("meta", re.compile(r"fbq\(\s*%sinit%s\s*,\s*%s([^'\"\\]{0,40})%s" % (Q, Q, Q, Q))),
    (
        "google",
        re.compile(r"gtag\(\s*%sconfig%s\s*,\s*%s([^'\"\\]{0,40})%s" % (Q, Q, Q, Q)),
    ),
    ("tiktok", re.compile(r"ttq\.load\(\s*%s([^'\"\\]{0,40})%s" % (Q, Q))),
    (
        "pinterest",
        re.compile(r"pintrk\(\s*%sload%s\s*,\s*%s([^'\"\\]{0,40})%s" % (Q, Q, Q, Q)),
    ),
    (
        "snap",
        re.compile(r"snaptr\(\s*%sinit%s\s*,\s*%s([^'\"\\]{0,60})%s" % (Q, Q, Q, Q)),
    ),
    (
        "gtm",
        re.compile(
            r"\(\s*window\s*,\s*document\s*,\s*%sscript%s\s*,\s*%s\w+%s\s*,\s*%s"
            r"([^'\"\\]{0,30})%s\s*\)" % (Q, Q, Q, Q, Q, Q)
        ),
    ),
]
# signature JSON (vendor, kind) of each loose call, for calibration status
LOOSE_KIND = {
    "meta": ("meta", "inline_call"),
    "google": ("google", "inline_call"),
    "tiktok": ("tiktok", "inline_call"),
    "pinterest": ("pinterest", "inline_call"),
    "snap": ("snap", "inline_call"),
    "gtm": ("gtm", "inline_snippet"),
}
WEB_PIXELS_RE = re.compile(r"webPixelsConfigList\s*:\s*\[")
SHOPIFY_MARKERS = [
    ("cdn.shopify.com", "cdn.shopify.com"),
    ("Shopify.theme", "Shopify.theme"),
    ("Shopify.shop", "Shopify.shop"),
    ("webPixelsConfigList", "webPixelsConfigList"),
    ("/cdn/shop/", "/cdn/shop/"),
]
OTHER_PLATFORMS = [
    ("WooCommerce", ["woocommerce", "wp-content"], 2),
    ("WordPress", ["wp-content", "wp-includes"], 1),
    ("BigCommerce", ["cdn11.bigcommerce.com", "bigcommerce"], 1),
    ("Magento / Adobe Commerce", ["mage/cookies", "magento_", "static/version"], 1),
    ("Salesforce Commerce Cloud", ["demandware"], 1),
    ("Wix", ["static.wixstatic.com", "wix-"], 1),
    ("Squarespace", ["static1.squarespace.com"], 1),
    ("Next.js", ["/_next/static/"], 1),
]
BLOCK_MARKERS = [
    "just a moment...",
    "cf-chl",
    "attention required! | cloudflare",
    "access denied",
    "your connection needs to be verified",
    "px-captcha",
    "captcha-delivery.com",
    "_incapsula_resource",
    "request unsuccessful. incapsula",
]
EU_REGIONS = set(
    "at be bg hr cy cz dk ee fi fr de gr hu ie it lv lt lu mt nl pl pt ro sk si "
    "es se gb uk is li no ch".split()
)
EU_CURRENCIES = ("EUR", "GBP", "SEK", "DKK", "PLN", "CHF", "NOK", "CZK", "HUF", "RON")
PLATFORM_WORDS = {
    "meta": r"(?i)\bmeta\b|facebook|instagram",
    "ga4": r"(?i)google analytics|\bgoogle\b",
    "google_ads": r"(?i)google ads|adwords|\bgoogle\b",
    "google_tag": r"(?i)\bgoogle\b",
    "gtm": r"(?i)tag manager|\bgoogle\b",
    "tiktok": r"(?i)tiktok",
    "pinterest": r"(?i)pinterest",
    "snap": r"(?i)snap(chat)?\b",
    "klaviyo": r"(?i)klaviyo",
    "microsoft": r"(?i)microsoft|\bbing\b",
    "reddit": r"(?i)reddit",
}


def load_signatures(path=None):
    with open(path or SIGNATURES_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def sig_status(sigs, vendor, kind):
    for s in sigs.get("signatures", []):
        if s.get("vendor") == vendor and s.get("kind") == kind:
            return s.get("status")
    return None


def google_platform(tag_id):
    return GOOGLE_PREFIX.get(tag_id.split("-", 1)[0].upper(), "google_tag")


def is_placeholder(value, sigs):
    for pat in (sigs.get("placeholders") or {}).get("patterns", []):
        if re.search(pat, value or ""):
            return True
    return False


def code_context(text, start, end):
    """True when a URL / call at [start, end) sits in code, not in a list.

    Rejects consent-tool inventories ({"Tag":"https://..."}) and regex /
    pipe-separated block lists; accepts when a loader is nearby (src=,
    'script', createElement, fbq / ttq / pintrk / snaptr calls).
    """
    before = text[max(0, start - 300) : start]
    near = text[max(0, start - 40) : start]
    after = text[end : end + 3]
    if re.search(r'"Tag"\s*:\s*"[^"]*$', near) or "|" in near[-3:] or after[:1] == "|":
        return False
    if re.search(r"\\\\\?|\\\\\.", near):  # escaped regex list
        return False
    return bool(
        re.search(
            r"src\s*=|\.src\b|['\"]script['\"]|createElement|fbq|ttq|pintrk|snaptr"
            r"|importScripts|<script|asyncLoad",
            before,
            re.I,
        )
    )


def strip_tags(src):
    src = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", src or "")
    src = re.sub(r"(?s)<[^>]+>", " ", src)
    return re.sub(r"\s+", " ", html.unescape(src)).strip()


# ---------------------------------------------------------------- Shopify


def web_pixels_span(text):
    """(start, end, entries) of the web pixels list, or None.

    entries is None when the list is present but could not be decoded.
    """
    m = WEB_PIXELS_RE.search(text or "")
    if not m:
        return None
    start = m.end() - 1
    try:
        arr, end = json.JSONDecoder().raw_decode(text, start)
    except ValueError:
        return (start, min(len(text), start + 200000), None)
    return (start, end, arr if isinstance(arr, list) else None)


def _config(entry):
    cfg = entry.get("configuration")
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except ValueError:
            return {}, cfg
        return (cfg if isinstance(cfg, dict) else {}), entry.get("configuration")
    return (cfg if isinstance(cfg, dict) else {}), json.dumps(cfg or {})


def app_pixel_hits(entries, sigs, source):
    """Hits and an inventory from decoded web pixel entries."""
    by_client = {}
    for a in sigs.get("app_pixels", []):
        by_client[str(a["apiClientId"])] = a
    hints = sigs.get("app_pixel_config_hints") or []
    name_hints = sigs.get("custom_pixel_name_hints") or {}
    hits, inventory = [], []
    for e in entries:
        if not isinstance(e, dict):
            continue
        typ = e.get("type")
        client = str(e.get("apiClientId")) if e.get("apiClientId") is not None else ""
        cfg, raw_cfg = _config(e)
        item = {
            "type": typ,
            "app": client or None,
            "name": (e.get("name") or "").strip()[:80] or None,
            "sandbox": e.get("runtimeContext"),
            "purposes": e.get("privacyPurposes") or [],
            "platform": None,
            "calibrated": False,
        }
        if client == "shopify-pixel":
            item["platform"] = "shopify"
            item["calibrated"] = True
            inventory.append(item)
            continue
        if typ == "APP" and client in by_client:
            spec = by_client[client]
            vendor = spec["vendor"]
            item["platform"] = vendor
            item["calibrated"] = spec.get("status") == "calibrated"
            ids = []
            if vendor == "google":
                ids = sorted(set(ID_RE["google"].findall(raw_cfg or "")))
            else:
                for key in spec.get("id_keys", []):
                    if cfg.get(key):
                        ids.append(str(cfg[key]))
            if vendor == "meta" and cfg.get("pixel_type") not in (
                None,
                "facebook_pixel",
            ):
                item["calibrated"] = False
            if not ids:
                ids = [None]
            for i in ids:
                platform = google_platform(i) if vendor == "google" and i else vendor
                if vendor == "google" and not i:
                    platform = "google_tag"
                hits.append(
                    make_hit(
                        platform,
                        i,
                        "app_pixel",
                        "shopify_app_pixel",
                        item["calibrated"],
                        source,
                        sigs,
                    )
                )
            inventory.append(item)
            continue
        if typ == "APP":
            for h in hints:
                if h["key"] in cfg:
                    val = cfg.get(h["key"])
                    pid = str(val) if isinstance(val, (str, int)) else None
                    item["platform"] = h["vendor"]
                    hits.append(
                        make_hit(
                            h["vendor"],
                            pid,
                            "app_pixel",
                            "shopify_app_pixel_hint",
                            False,
                            source,
                            sigs,
                        )
                    )
                    break
            inventory.append(item)
            continue
        # custom pixel: the code is not visible, only the merchant's name
        name = (item["name"] or "").lower()
        for vendor, words in name_hints.items():
            if vendor.startswith("_"):
                continue
            if any(w in name for w in words):
                item["platform"] = vendor
                platform = "google_tag" if vendor == "google" else vendor
                hits.append(
                    make_hit(
                        platform,
                        None,
                        "custom_pixel",
                        "custom_pixel_name",
                        False,
                        source,
                        sigs,
                        detail=item["name"],
                    )
                )
                break
        inventory.append(item)
    return hits, inventory


def fallback_app_pixel_hits(text, sigs, source):
    """Regex fallback when the pixel list cannot be decoded."""
    hits = []
    for s in sigs.get("signatures", []):
        if s.get("kind") != "shopify_app_pixel":
            continue
        cal = s.get("status") == "calibrated"
        for m in re.finditer(s["pattern"], text):
            ident = m.group(1) if m.groups() else None
            vendor = s["vendor"]
            if vendor == "google":
                window = text[max(0, m.start() - 1500) : m.start()]
                for gid in sorted(set(ID_RE["google"].findall(window)))[:4]:
                    hits.append(
                        make_hit(
                            google_platform(gid),
                            gid,
                            "app_pixel",
                            s["kind"],
                            cal,
                            source,
                            sigs,
                        )
                    )
                continue
            hits.append(
                make_hit(vendor, ident, "app_pixel", s["kind"], cal, source, sigs)
            )
    return hits


# ---------------------------------------------------------------- pages


def make_hit(platform, ident, via, kind, calibrated, source, sigs, detail=None):
    problem = None
    if ident is not None:
        key = "google" if platform in ("ga4", "google_ads", "google_tag") else platform
        rx = ID_RE.get(key)
        if is_placeholder(ident, sigs):
            problem = "placeholder"
        elif rx is not None and not rx.fullmatch(ident):
            problem = "bad_format"
    return {
        "platform": platform,
        "id": ident,
        "install_via": via,
        "kind": kind,
        "calibrated": bool(calibrated),
        "source": source,
        "detail": detail,
        "problem": problem,
    }


def scan_page(text, sigs, source):
    """All hits in one saved page. Returns (hits, inventory, info)."""
    text = text or ""
    hits, inventory = [], []
    span = web_pixels_span(text)
    info = {"web_pixels": "not_observed", "web_pixels_count": 0}
    skip = (0, 0)
    if span:
        start, end, entries = span
        skip = (start, end)
        if entries is None:
            info["web_pixels"] = "undecodable"
            hits += fallback_app_pixel_hits(text[start:end], sigs, source)
        else:
            info["web_pixels"] = "present"
            info["web_pixels_count"] = len(entries)
            h, inventory = app_pixel_hits(entries, sigs, source)
            hits += h
    outside = text[: skip[0]] + " " * (skip[1] - skip[0]) + text[skip[1] :]
    outside = outside.replace("\\/", "/")
    # loose init / loader calls (validated afterwards)
    for vendor, rx in LOOSE_CALLS:
        v, kind = LOOSE_KIND[vendor]
        cal = sig_status(sigs, v, kind) == "calibrated"
        for m in rx.finditer(outside):
            ident = m.group(1).strip()
            if vendor == "google":
                if ident.upper().startswith("UA-"):
                    continue  # legacy, handled below
                platform = google_platform(ident) if "-" in ident else "google_tag"
            else:
                platform = vendor
            hits.append(
                make_hit(platform, ident, "theme_code", kind, cal, source, sigs)
            )
    # script src / noscript signatures from the data file
    for s in sigs.get("signatures", []):
        if s.get("kind") not in ("script_src", "noscript"):
            continue
        cal = s.get("status") == "calibrated"
        for m in re.finditer(s["pattern"], outside):
            if s["kind"] == "script_src" and not (
                code_context(outside, m.start(), m.end())
                or re.search(
                    r"<script[^>]*$", outside[max(0, m.start() - 200) : m.start()]
                )
            ):
                continue
            ident = None
            if m.groups():
                ident = next((g for g in reversed(m.groups()) if g), None)
            vendor = s["vendor"]
            platform = (
                google_platform(ident) if vendor == "google" and ident else vendor
            )
            hits.append(
                make_hit(platform, ident, "theme_code", s["kind"], cal, source, sigs)
            )
    return dedupe(hits), inventory, info


def legacy_ua(text, sigs):
    pat = (sigs.get("legacy_ua") or {}).get("pattern")
    if not pat:
        return []
    return sorted(set(re.findall(pat, text or "")))


def container_ids(text):
    """GTM container IDs and gtag script IDs referenced by a page."""
    gtm, gtag = [], []
    for m in re.finditer(r"GTM-[A-Z0-9]{4,10}", text or ""):
        ctx = text[max(0, m.start() - 160) : m.start()]
        if re.search(r"dataLayer|gtm\.js|googletagmanager", ctx):
            if m.group(0) not in gtm:
                gtm.append(m.group(0))
    for m in re.finditer(
        r"googletagmanager\.com/gtag/js\?id=((?:G|AW|GT|DC)-[A-Za-z0-9]{4,14})",
        text or "",
    ):
        if m.group(1) not in gtag:
            gtag.append(m.group(1))
    return gtm, gtag


def scan_container(text, sigs, source, via, container):
    """Hits from a downloaded GTM / Google tag container script."""
    cs = sigs.get("container_signals") or {}
    hits = []
    gpat = (cs.get("google_id") or {}).get("pattern")
    if gpat:
        for gid in sorted(set(re.findall(gpat, text or ""))):
            hits.append(
                make_hit(
                    google_platform(gid),
                    gid,
                    via,
                    "container_google_id",
                    (cs["google_id"].get("status") == "calibrated"),
                    source,
                    sigs,
                    detail=container,
                )
            )
    for v in cs.get("vendors", []):
        if re.search(v["pattern"], text or ""):
            hits.append(
                make_hit(
                    v["vendor"],
                    None,
                    via,
                    "container_vendor_code",
                    v.get("status") == "calibrated",
                    source,
                    sigs,
                    detail=container,
                )
            )
    return dedupe(hits)


def dedupe(hits):
    """Merge identical hits; ``count`` = how many times the same code was seen."""
    seen, out = {}, []
    for h in hits:
        key = (h["platform"], h["id"], h["install_via"], h["kind"], h["source"])
        if key in seen:
            seen[key]["count"] = seen[key].get("count", 1) + h.get("count", 1)
            continue
        h.setdefault("count", 1)
        seen[key] = h
        out.append(h)
    return out


# ---------------------------------------------------------------- site facts


def shopify_markers(text):
    return [name for name, needle in SHOPIFY_MARKERS if needle in (text or "")]


def other_platform(text):
    low = (text or "").lower()
    for name, needles, need in OTHER_PLATFORMS:
        if sum(1 for n in needles if n in low) >= need:
            return name
    return None


def looks_blocked(text):
    head = (text or "")[:20000].lower()
    if "webpixelsconfiglist" in (text or "").lower():
        return False
    return any(m in head for m in BLOCK_MARKERS)


def market_signals(text):
    """hreflang regions, storefront currency / country, html lang."""
    out = {"hreflang": [], "currency": None, "country": None, "html_lang": None}
    for m in re.finditer(r"(?is)<link\b[^>]*>", text or ""):
        tag = m.group(0)
        if "alternate" not in tag.lower():
            continue
        h = re.search(r"hreflang\s*=\s*[\"']([^\"']+)", tag, re.I)
        if h and h.group(1).lower() not in out["hreflang"]:
            out["hreflang"].append(h.group(1).lower())
    m = re.search(
        r'Shopify\.currency\s*=\s*\{[^}]*"active"\s*:\s*"([A-Z]{3})"', text or ""
    )
    if m:
        out["currency"] = m.group(1)
    m = re.search(r'Shopify\.country\s*=\s*"([A-Z]{2})"', text or "")
    if m:
        out["country"] = m.group(1)
    m = re.search(r"<html[^>]*\blang\s*=\s*[\"']([^\"']+)", text or "", re.I)
    if m:
        out["html_lang"] = m.group(1).lower()
    return out


def regions(markets):
    """Which privacy regimes the storefront seems to face (inferred)."""
    langs = list(markets.get("hreflang") or [])
    if markets.get("html_lang"):
        langs.append(markets["html_lang"])
    reg = set()
    for code in langs:
        parts = re.split(r"[-_]", code)
        region = parts[1] if len(parts) > 1 else ""
        if region in EU_REGIONS or parts[0] in (
            "de",
            "fr",
            "nl",
            "it",
            "es",
            "sv",
            "da",
            "pl",
            "fi",
        ):
            reg.add("eu_uk")
        if region == "us":
            reg.add("us")
        if region == "ca":
            reg.add("ca")
    cur = markets.get("currency") or ""
    country = (markets.get("country") or "").lower()
    if cur in EU_CURRENCIES or country in EU_REGIONS:
        reg.add("eu_uk")
    if cur == "USD" or country == "us":
        reg.add("us")
    if cur == "CAD" or country == "ca":
        reg.add("ca")
    return sorted(reg)


def consent_signals(texts, sigs):
    """Consent markers across pages + containers: literal calls and CMPs."""
    c = sigs.get("consent") or {}
    joined = "\n".join(t for t in texts if t)
    out = {
        "consent_default": False,
        "consent_v2": False,
        "cmp": [],
        "us_opt_out": False,
    }
    d = (c.get("google_consent_default") or {}).get("pattern")
    if d and re.search(d, joined):
        out["consent_default"] = True
        v2 = (c.get("consent_v2_params") or {}).get("pattern")
        out["consent_v2"] = bool(v2 and re.search(v2, joined))
    for cmp in c.get("cmp", []):
        if re.search(cmp["pattern"], joined, re.I):
            out["cmp"].append(cmp["name"])
    us = (c.get("us_opt_out") or {}).get("pattern")
    out["us_opt_out"] = bool(us and re.search(us, joined))
    return out


def privacy_mentions(text):
    """Which platforms a privacy policy text names, plus ad / opt-out words."""
    plain = strip_tags(text)
    found = [p for p, rx in PLATFORM_WORDS.items() if re.search(rx, plain)]
    return {
        "chars": len(plain),
        "platforms": found,
        "advertising": bool(
            re.search(r"(?i)advertis|interest-based|targeted ads|remarketing", plain)
        ),
        "opt_out": bool(
            re.search(r"(?i)opt[- ]?out|do not sell|your privacy choices", plain)
        ),
    }
