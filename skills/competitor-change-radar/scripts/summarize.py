#!/usr/bin/env python3
"""Offline summarizer for shopify-store-teardown runs.

Reads the raw files of one run directory (see references/data-contract.md) and
writes three derived artifacts next to ``raw/``:

- ``summary.json``   derived statistics, endpoint status, identity signals
- ``products.tsv``   one row per product
- ``evidence.jsonl`` machine-generated evidence entries (E1 / E4)

It never touches the network, so it also works in sandboxes where raw files
were saved manually by a web-reading tool. Missing, corrupted, truncated or
markdown-wrapped files are tolerated and reported instead of crashing.

CLI:
    python summarize.py <run folder | 原始数据 folder> [--domain example.com] [--quiet]

Python 3.8+, standard library only.
"""

import argparse
import datetime
import html
import json
import os
import re
import sys
from collections import Counter

SCHEMA = "store-teardown/summary/1"
RAW_DIRNAME = "原始数据"  # same as delivery.RAW_DIRNAME
TOOL_VERSION = "0.2.3"

POLICY_NAMES = [
    "refund-policy",
    "shipping-policy",
    "privacy-policy",
    "terms-of-service",
    "contact-information",
]

PRICE_EDGES = [0, 10, 25, 50, 100, 200, 500, 1000, 2000, 5000]
# Add-ons that are not real merchandise (shipping protection, gift cards,
# insurance...). Shared by products.price_excluding_virtual and the L1
# sample picker in collect.py so both use the same price basis.
NON_PRODUCT_RE = re.compile(
    r"protect|insurance|guarantee|gift[\s-]*card|e-?gift|warranty|shipping|"
    r"\broute\b|donation|\btips?\b|运费|保障|保险|退货险|礼品卡",
    re.I,
)
MIN_REAL_PRICE = 5.0

POLICY_MIN_CHARS = 200
# Fewer sampled product-page prices than this: warn that the sample is small.
PRICE_SAMPLE_SMALL = 8
COLLECTIONS_MISMATCH_RATIO = 0.20
SITEMAP_MISMATCH_RATIO = 0.10

# (name, [lowercase substrings]) - matched against raw HTML source only.
APP_SIGNATURES = [
    ("Judge.me", ["judge.me", "judgeme"]),
    ("Yotpo", ["staticw2.yotpo.com", "cdn-widgetsrepository.yotpo.com", "yotpo.com"]),
    ("Okendo", ["okendo.io", "okendo-reviews"]),
    ("Loox", ["loox.io", "loox-rating"]),
    ("Stamped.io", ["stamped.io"]),
    ("Reviews.io", ["reviews.io", "reviewsio"]),
    ("Junip", ["junip.co"]),
    ("Klaviyo", ["klaviyo.com", "klaviyo"]),
    ("Omnisend", ["omnisnippet", "omnisend.com"]),
    ("Attentive", ["attn.tv", "attentivemobile"]),
    ("Postscript", ["postscript.io"]),
    ("Privy", ["privy.com", "widget.privy"]),
    ("Gorgias", ["gorgias.chat", "gorgias.io", "gorgias"]),
    ("Tidio", ["tidio.co", "tidiochat"]),
    ("Zendesk", ["zdassets.com", "zendesk.com"]),
    ("Intercom", ["widget.intercom.io", "intercomcdn"]),
    ("Rebuy", ["rebuyengine.com"]),
    ("ReCharge", ["rechargecdn.com", "rechargepayments.com", "rechargeapps.com"]),
    ("Afterpay", ["afterpay.com", "afterpay-placement", "static.afterpay"]),
    ("Klarna", ["klarna.com", "klarnaservices.com", "klarna-placement"]),
    ("Shop Pay Installments", ["shopify-payment-terms", "shop_pay_installments"]),
    ("Affirm", ["affirm.com/js", "cdn1.affirm.com"]),
    ("Smile.io", ["smile.io", "smile-ui"]),
    ("LoyaltyLion", ["loyaltylion"]),
    ("Yotpo Loyalty (Swell)", ["swellrewards", "loyalty.yotpo.com"]),
    ("PageFly", ["pagefly"]),
    ("GemPages", ["gempages", "gem-page"]),
    ("Shogun", ["getshogun.com", "shogun-frontend"]),
    ("Weglot", ["weglot.com", "cdn.weglot"]),
    ("Langify", ["langify"]),
    ("Bold", ["boldcommerce.com", "boldapps.net"]),
    ("Hotjar", ["static.hotjar.com"]),
    ("Lucky Orange", ["luckyorange"]),
    ("Elevar", ["getelevar.com"]),
    ("Triple Whale", ["triplewhale", "triple-pixel"]),
    ("Searchanise", ["searchanise"]),
    ("Boost Commerce", ["boostcommerce", "bc-sf-filter"]),
    ("Globo / Geolocation apps", ["globo.io"]),
    ("Route", ["routeapp.io", "route-widget"]),
]

TRACKING_SIGNATURES = [
    ("meta_pixel", ["connect.facebook.net", "fbq("]),
    ("tiktok_pixel", ["analytics.tiktok.com"]),
    ("ga4", ["gtag/js?id=g-", "gtag('config', 'g-", 'gtag("config", "g-']),
    ("google_ads", ["gtag/js?id=aw-", "'aw-", '"aw-']),
    ("gtm", ["googletagmanager.com/gtm.js", "gtm-"]),
    ("pinterest_tag", ["s.pinimg.com/ct/core.js", "pintrk("]),
    ("snap_pixel", ["sc-static.net/scevent", "snaptr("]),
    ("klaviyo", ["static.klaviyo.com", "klaviyo.js"]),
    ("microsoft_uet", ["bat.bing.com"]),
    ("reddit_pixel", ["redditstatic.com/ads", "rdt("]),
]

# endpoint key -> (first raw file, evidence locator, evidence level)
ENDPOINT_FILES = {
    "meta_json": ("meta.json", "meta.json", "E1"),
    "products_json": ("products-1.json", "products-*.json", "E1"),
    "collections_json": ("collections-1.json", "collections-*.json", "E1"),
    "sitemap": ("sitemap.xml", "sitemap.xml", "E1"),
    "sitemap_products": ("sitemap-products-1.xml", "sitemap-products-*.xml", "E1"),
    "homepage": ("homepage.html", "homepage.html", "E1"),
    "best_selling": ("best-selling.html", "best-selling.html", "E1"),
    "page_about": ("page-about.html", "page-about.html", "E1"),
    "rdap": ("rdap.json", "rdap.json", "E4"),
    "wayback": ("wayback.json", "wayback.json", "E4"),
}
for _name in POLICY_NAMES:
    ENDPOINT_FILES["policy_" + _name.replace("-", "_")] = ("", "", "E1")

HANDLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-_.%]*$")

# Storefront framework hints (headless storefronts have no Shopify.theme).
FRAMEWORK_SIGNATURES = [
    ("next.js", ["__next_data__", "/_next/static/"]),
    ("hydrogen", ["@shopify/hydrogen", "hydrogen-", "oxygen-v2"]),
    ("remix", ["__remixcontext", "window.__remix"]),
    ("nuxt", ["__nuxt__", "/_nuxt/"]),
    ("gatsby", ["___gatsby"]),
]

BLOCK_MARKERS = [
    "just a moment...",
    "cf-chl",
    "captcha",
    "access denied",
    "your connection needs to be verified",
    "attention required",
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path):
    """Return file text or None if missing/unreadable."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8", errors="replace")


def looks_blocked(text):
    head = (text or "")[:20000].lower()
    return any(m in head for m in BLOCK_MARKERS)


def _salvage_array(text, key):
    """Recover complete objects of a top-level array named ``key``."""
    m = re.search(r'"%s"\s*:\s*\[' % re.escape(key), text)
    if not m:
        return None
    decoder = json.JSONDecoder()
    pos = m.end()
    items = []
    n = len(text)
    while pos < n:
        while pos < n and text[pos] in " \t\r\n,":
            pos += 1
        if pos >= n or text[pos] == "]":
            break
        try:
            obj, pos = decoder.raw_decode(text, pos)
        except ValueError:
            break
        items.append(obj)
    return items


def parse_json_lenient(text, salvage_key=None):
    """Parse JSON that may be wrapped in markdown or truncated.

    Returns (obj, state, note). state is one of:
    ok | extracted | salvaged | error. ``salvaged`` means the document was
    truncated and only complete array items under ``salvage_key`` were kept.
    """
    if text is None:
        return None, "missing", ""
    stripped = text.strip()
    if not stripped:
        return None, "error", "empty file"
    try:
        return json.loads(stripped), "ok", ""
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    # Only the first JSON start character is a document candidate; later
    # braces are nested values of a truncated document (e.g. one product).
    starts = [i for i in (stripped.find("{"), stripped.find("[")) if i >= 0]
    for start in sorted(starts)[:1]:
        try:
            obj, _end = decoder.raw_decode(stripped, start)
        except ValueError:
            break
        if salvage_key and not (isinstance(obj, dict) and salvage_key in obj):
            break
        return obj, "extracted", "extracted JSON from wrapped text"
    if salvage_key:
        items = _salvage_array(stripped, salvage_key)
        if items is not None:
            return (
                {salvage_key: items},
                "salvaged",
                "truncated JSON, kept %d complete items" % len(items),
            )
    if looks_blocked(stripped) or stripped.lower().startswith(("<!doctype", "<html")):
        return None, "blocked", "HTML instead of JSON (possible challenge page)"
    return None, "error", "unparseable or truncated JSON"


def page_number(name, prefix, suffix):
    m = re.match(r"^%s(\d+)%s$" % (re.escape(prefix), re.escape(suffix)), name)
    return int(m.group(1)) if m else None


def list_pages(raw_dir, prefix, suffix):
    out = []
    if not os.path.isdir(raw_dir):
        return out
    for name in os.listdir(raw_dir):
        n = page_number(name, prefix, suffix)
        if n is not None:
            out.append((n, name))
    out.sort()
    return out


def percentile(sorted_vals, q):
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def median(vals):
    return percentile(sorted(vals), 0.5)


def to_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def r2(v):
    return None if v is None else round(v, 2)


def lowest_price(product):
    """Lowest variant price of a product, or None."""
    vals = [
        x
        for x in (
            to_float(v.get("price"))
            for v in product.get("variants") or []
            if isinstance(v, dict)
        )
        if x is not None
    ]
    return min(vals) if vals else None


def is_real_product(product, price):
    """False for add-ons such as shipping protection, gift cards, insurance."""
    if price is None or price < MIN_REAL_PRICE:
        return False
    text = " ".join(
        str(product.get(k) or "") for k in ("handle", "title", "product_type")
    )
    if NON_PRODUCT_RE.search(text):
        return False
    variants = [v for v in product.get("variants") or [] if isinstance(v, dict)]
    if variants and all(v.get("requires_shipping") is False for v in variants):
        return False  # gift cards and other non-shipping items
    return True


def in_stock(product):
    """True unless every variant is explicitly unavailable (L1 sample rule)."""
    return any(
        v.get("available") is not False
        for v in product.get("variants") or []
        if isinstance(v, dict)
    )


def price_quartiles(sorted_vals):
    """{min, p25, median, p75, max} of an ascending list (None when empty)."""
    sp = sorted_vals
    return {
        "min": r2(sp[0]) if sp else None,
        "p25": r2(percentile(sp, 0.25)),
        "median": r2(percentile(sp, 0.5)),
        "p75": r2(percentile(sp, 0.75)),
        "max": r2(sp[-1]) if sp else None,
    }


def top_n(counter, n=10):
    return [[k, c] for k, c in counter.most_common(n)]


def html_to_text(src):
    src = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", src)
    src = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</div>", "\n", src)
    src = re.sub(r"(?s)<[^>]+>", " ", src)
    src = html.unescape(src)
    src = re.sub(r"[ \t\r\f\v]+", " ", src)
    src = re.sub(r"\n\s*\n+", "\n\n", src)
    return src.strip()


def extract_balanced_div(src, marker):
    """Return inner HTML of the div whose opening tag contains ``marker``."""
    idx = src.find(marker)
    if idx < 0:
        return None
    start = src.rfind("<div", 0, idx)
    if start < 0:
        return None
    tag_end = src.find(">", idx)
    if tag_end < 0:
        return None
    depth = 1
    pos = tag_end + 1
    tag_re = re.compile(r"(?i)<(/?)div\b")
    while depth > 0:
        m = tag_re.search(src, pos)
        if not m:
            return src[tag_end + 1 :]
        depth += -1 if m.group(1) else 1
        pos = m.end()
        if depth == 0:
            return src[tag_end + 1 : m.start()]
    return None


def parse_attrs(tag):
    attrs = {}
    for m in re.finditer(
        r'([a-zA-Z_:\-]+)\s*=\s*("([^"]*)"|\'([^\']*)\'|([^\s>]+))', tag
    ):
        val = m.group(3) if m.group(3) is not None else m.group(4)
        if val is None:
            val = m.group(5)
        attrs[m.group(1).lower()] = html.unescape(val)
    return attrs


def js_object_after(src, pattern):
    """Decode the JSON object literal following regex ``pattern``."""
    m = re.search(pattern, src)
    if not m:
        return None
    brace = src.find("{", m.end() - 1)
    if brace < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(src, brace)
        return obj if isinstance(obj, dict) else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# sitemap and product page parsing (also used by collect.py)
# ---------------------------------------------------------------------------

LOC_RE = re.compile(r"<loc>\s*([^<]+?)\s*</loc>")
URL_BLOCK_RE = re.compile(r"(?is)<url>(.*?)</url>")
LASTMOD_RE = re.compile(r"(?is)<lastmod>\s*([^<]+?)\s*</lastmod>")
LD_JSON_RE = re.compile(
    r"(?is)<script\b[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>"
)
META_TAG_RE = re.compile(r"(?is)<meta\b[^>]*>")
TITLE_RE = re.compile(r"(?is)<title\b[^>]*>(.*?)</title>")


def product_handle(url):
    """Handle of a ``.../products/<handle>`` URL (unescaped), or None."""
    url = html.unescape(url or "")
    if "/products/" not in url:
        return None
    handle = url.split("/products/", 1)[1].split("?")[0].split("#")[0].strip("/")
    if not handle or "/" in handle:
        return None
    return handle


def sitemap_product_entries(text):
    """[(handle, lastmod or None)] of product URLs in a sitemap, in order.

    Handles may repeat (localized child maps list the same products).
    """
    out = []
    blocks = URL_BLOCK_RE.findall(text or "")
    if blocks:
        for block in blocks:
            m = LOC_RE.search(block)
            handle = product_handle(m.group(1)) if m else None
            if handle:
                lm = LASTMOD_RE.search(block)
                out.append((handle, lm.group(1).strip() if lm else None))
        return out
    for loc in LOC_RE.findall(text or ""):
        handle = product_handle(loc)
        if handle:
            out.append((handle, None))
    return out


def _money(value):
    """Float from 25, "25.00" or "1,234.50"; None otherwise."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if re.match(r"^\d+(?:\.\d+)?$", text):
            return float(text)
    return None


def _ld_nodes(obj):
    """Every dict inside a JSON-LD document (lists and @graph included)."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, list):
            stack.extend(reversed(cur))
        elif isinstance(cur, dict):
            yield cur
            if "@graph" in cur:
                stack.append(cur["@graph"])


def _ld_is(node, kind):
    t = node.get("@type")
    types = t if isinstance(t, list) else [t]
    return any(isinstance(x, str) and x.split("/")[-1] == kind for x in types)


def _availability(values):
    """True if any offer is in stock, False if all are sold out, else None."""
    flags = []
    for v in values:
        low = str(v or "").lower()
        if "instock" in low or "limitedavailability" in low or "preorder" in low:
            flags.append(True)
        elif "outofstock" in low or "soldout" in low or "discontinued" in low:
            flags.append(False)
    if any(flags):
        return True
    if flags:
        return False
    return None


def _from_json_ld(src):
    """Price facts of the first JSON-LD Product with a usable offer."""
    for raw in LD_JSON_RE.findall(src):
        doc = None
        for text in (raw.strip(), html.unescape(raw.strip())):
            try:
                doc = json.loads(text)
                break
            except ValueError:
                continue
        if doc is None:
            continue
        for node in _ld_nodes(doc):
            if not (_ld_is(node, "Product") or _ld_is(node, "ProductGroup")):
                continue
            offers = node.get("offers")
            if offers is None and isinstance(node.get("hasVariant"), list):
                offers = [
                    v.get("offers") for v in node["hasVariant"] if isinstance(v, dict)
                ]
            offer_list = []
            for o in _ld_nodes(offers):
                if (
                    _ld_is(o, "Offer")
                    or _ld_is(o, "AggregateOffer")
                    or ("price" in o or "lowPrice" in o)
                ):
                    offer_list.append(o)
            prices, highs, compare, currencies, avail = [], [], [], [], []
            for o in offer_list:
                low = _money(o.get("lowPrice"))
                high = _money(o.get("highPrice"))
                price = _money(o.get("price"))
                if price is None:
                    spec = o.get("priceSpecification")
                    for sp in _ld_nodes(spec):
                        kind = str(sp.get("priceType") or "")
                        val = _money(sp.get("price"))
                        if val is None:
                            continue
                        if "Strikethrough" in kind or "ListPrice" in kind:
                            compare.append(val)
                        elif price is None:
                            price = val
                else:
                    for sp in _ld_nodes(o.get("priceSpecification")):
                        kind = str(sp.get("priceType") or "")
                        val = _money(sp.get("price"))
                        if val is not None and (
                            "Strikethrough" in kind or "ListPrice" in kind
                        ):
                            compare.append(val)
                for v in (price, low):
                    if v is not None:
                        prices.append(v)
                for v in (price, high):
                    if v is not None:
                        highs.append(v)
                cur = o.get("priceCurrency")
                if isinstance(cur, str) and cur.strip():
                    currencies.append(cur.strip().upper())
                if o.get("availability") is not None:
                    avail.append(o.get("availability"))
            if not prices:
                continue
            name = node.get("name")
            return {
                "title": name.strip() if isinstance(name, str) else None,
                "price": min(prices),
                "price_max": max(highs) if highs else min(prices),
                "compare_at": max(compare) if compare else None,
                "currency": Counter(currencies).most_common(1)[0][0]
                if currencies
                else None,
                "available": _availability(avail),
                "price_from": "json_ld",
            }
    return None


def _meta_values(src):
    out = {}
    for tag in META_TAG_RE.findall(src):
        attrs = parse_attrs(tag)
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key and "content" in attrs:
            out.setdefault(key, attrs["content"])
    return out


def parse_product_page(src):
    """Price facts visible on a public product page, or None.

    Reads JSON-LD ``Product`` offers first (price / lowPrice / highPrice,
    priceCurrency, availability), then the ``og:price:amount`` /
    ``product:price:amount`` meta tags. Returns ``{title, price, price_max,
    compare_at, currency, available, price_from}``; ``compare_at`` is only set
    when JSON-LD states a strikethrough / list price (never guessed).
    """
    if not src:
        return None
    meta = _meta_values(src)
    found = _from_json_ld(src)
    if found is None:
        price = None
        for key in ("og:price:amount", "product:price:amount"):
            price = _money(meta.get(key))
            if price is not None:
                break
        if price is None:
            return None
        cur = meta.get("og:price:currency") or meta.get("product:price:currency")
        found = {
            "title": None,
            "price": price,
            "price_max": price,
            "compare_at": None,
            "currency": cur.strip().upper() if cur and cur.strip() else None,
            "available": None,
            "price_from": "meta",
        }
    if not found.get("title"):
        title = meta.get("og:title")
        if not title:
            m = TITLE_RE.search(src)
            title = html.unescape(m.group(1)).strip() if m else None
        found["title"] = title or None
    return found


def sample_product(entry):
    """Minimal products.json-like dict for a sampled product page, so the
    shared price rules (is_real_product, select_l1_samples) apply."""
    variant = {"price": "%.2f" % entry["price"]}
    if entry.get("available") is not None:
        variant["available"] = entry["available"]
    if entry.get("compare_at") is not None:
        variant["compare_at_price"] = "%.2f" % entry["compare_at"]
    return {
        "handle": entry.get("handle"),
        "title": entry.get("title") or entry.get("handle"),
        "variants": [variant],
    }


# ---------------------------------------------------------------------------
# summarizer
# ---------------------------------------------------------------------------


class Run(object):
    def __init__(self, run_dir, domain=None):
        self.run_dir = os.path.abspath(run_dir)
        self.raw = os.path.join(self.run_dir, "raw")
        self.warnings = []
        self.errors = []
        self.evidence = []
        self.status = self._load_status()
        self.domain = domain or self.status.get("domain") or self._guess_domain()
        self.captured_at = (
            self.status.get("finished_at_utc")
            or self.status.get("started_at_utc")
            or utc_now()
        )
        self.by_file = {}
        for req in self.status.get("requests", []) or []:
            if isinstance(req, dict) and req.get("file"):
                self.by_file[req["file"]] = req

    # -- context -----------------------------------------------------------

    def _load_status(self):
        text = read_text(os.path.join(self.raw, "_status.json"))
        if text is None:
            return {}
        obj, state, note = parse_json_lenient(text)
        if not isinstance(obj, dict):
            self.errors.append("_status.json unreadable: %s" % note)
            return {}
        return obj

    def _guess_domain(self):
        parent = os.path.basename(os.path.dirname(self.run_dir))
        if "." in parent:
            return parent.split(" ")[0]
        base = os.path.basename(self.run_dir)
        return base if "." in base else "unknown"

    def base_url(self):
        return self.status.get("base_url") or "https://%s" % self.domain

    def url_for(self, fname, default_path):
        req = self.by_file.get(fname)
        if req and req.get("url"):
            return req["url"]
        return self.base_url() + default_path

    def path(self, fname):
        return os.path.join(self.raw, fname)

    def rel(self, fname):
        return "raw/" + fname

    # -- endpoint status ---------------------------------------------------

    def status_entry(self, fname, parse_state=None, note=""):
        """Build an endpoint entry for one raw file."""
        req = self.by_file.get(fname) or {}
        exists = os.path.isfile(self.path(fname))
        entry = {"url": req.get("url")}
        if req.get("http_status") is not None:
            entry["http_status"] = req.get("http_status")
        if not exists:
            entry["result"] = req.get("result") or "not_checked"
            if req.get("note"):
                entry["note"] = req["note"]
            return entry
        if parse_state in (None, "ok", "extracted"):
            entry["result"] = "ok"
        elif parse_state == "salvaged":
            entry["result"] = "ok"
            entry["truncated"] = True
        elif parse_state == "blocked":
            entry["result"] = "blocked"
        else:
            entry["result"] = "error"
        if parse_state == "extracted":
            entry["note"] = "JSON extracted from wrapped text"
        if note and parse_state not in ("ok", None):
            entry["note"] = note
        elif req.get("note"):
            entry.setdefault("note", req["note"])
        return entry

    def add_evidence(self, level, kind, claim, source_url, locator):
        self.evidence.append(
            {
                "evidence_id": "E%03d" % (len(self.evidence) + 1),
                "level": level,
                "kind": kind,
                "claim": claim,
                "source_url": source_url,
                "captured_at_utc": self.captured_at,
                "locator": locator,
            }
        )

    def load_json_file(self, fname, salvage_key=None):
        text = read_text(self.path(fname))
        obj, state, note = parse_json_lenient(text, salvage_key)
        if state not in ("ok", "missing"):
            if state in ("error", "blocked"):
                self.errors.append("%s: %s" % (fname, note))
            else:
                self.warnings.append("json_%s:%s" % (state, fname))
        return obj, state, note

    def load_html_file(self, fname):
        text = read_text(self.path(fname))
        if text is None:
            return None, None
        if not text.strip():
            return None, "error"
        if len(text) < 30000 and looks_blocked(text) and "shopify" not in text.lower():
            return text, "blocked"
        return text, "ok"


def summarize_run(run_dir, domain=None):
    """Summarize ``run_dir`` and write artifacts. Returns the summary dict."""
    run = Run(run_dir, domain)
    endpoints = {}
    base = run.base_url()

    # meta.json ------------------------------------------------------------
    meta, meta_state, meta_note = run.load_json_file("meta.json")
    if not isinstance(meta, dict):
        meta = None
    endpoints["meta_json"] = run.status_entry("meta.json", meta_state, meta_note)
    # products.json pages --------------------------------------------------
    products, prod_ep = load_products(run)
    endpoints["products_json"] = prod_ep

    # collections ----------------------------------------------------------
    collections = []
    col_pages = list_pages(run.raw, "collections-", ".json")
    col_ep = {"result": "not_checked", "pages": 0}
    if col_pages:
        col_ep = run.status_entry(col_pages[0][1], None)
        col_ep["pages"] = len(col_pages)
        states = []
        for _n, fname in col_pages:
            obj, state, note = run.load_json_file(fname, "collections")
            states.append(state)
            if isinstance(obj, dict) and isinstance(obj.get("collections"), list):
                collections.extend(c for c in obj["collections"] if isinstance(c, dict))
        if all(s in ("error", "blocked") for s in states):
            col_ep["result"] = "blocked" if "blocked" in states else "error"
        if "salvaged" in states:
            col_ep["truncated"] = True
    else:
        col_ep = run.status_entry("collections-1.json", None)
        col_ep["pages"] = 0
    endpoints["collections_json"] = col_ep

    # sitemap --------------------------------------------------------------
    sitemap_txt, sm_state = run.load_html_file("sitemap.xml")
    endpoints["sitemap"] = run.status_entry("sitemap.xml", sm_state)
    sm_children = []
    if sitemap_txt:
        sm_children = [
            html.unescape(u)
            for u in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", sitemap_txt)
            if "sitemap_products" in u
        ]
    sp_pages = list_pages(run.raw, "sitemap-products-", ".xml")
    sitemap_product_urls = None
    sitemap_handle_order = []
    sitemap_lastmod = {}  # handle -> latest <lastmod> (last-modified, not publish)
    if sp_pages:
        # Localized child maps (e.g. /es-US/sitemap_products_1.xml) repeat the
        # same products, so count unique handles instead of raw URLs.
        handles = set()
        sitemap_handle_cap = 2000
        sitemap_truncated = False
        for _n, fname in sp_pages:
            txt = read_text(run.path(fname)) or ""
            for handle, lastmod in sitemap_product_entries(txt):
                if handle not in handles:
                    handles.add(handle)
                    sitemap_handle_order.append(handle)
                if lastmod and len(lastmod) >= 7 and re.match(r"^\d{4}-\d{2}", lastmod):
                    prev = sitemap_lastmod.get(handle)
                    if prev is None or lastmod > prev:
                        sitemap_lastmod[handle] = lastmod
                if len(handles) >= sitemap_handle_cap:
                    sitemap_truncated = True
                    break
            if sitemap_truncated:
                break
        sitemap_product_urls = len(handles)
        if sitemap_truncated:
            run.warnings.append("sitemap_products_truncated_at_2000")
        if sm_children and len(sp_pages) < len(sm_children):
            run.warnings.append("sitemap_products_incomplete")
        endpoints["sitemap_products"] = {
            "result": "ok",
            "files": len(sp_pages),
            "declared": len(sm_children),
            "product_handle_cap": sitemap_handle_cap,
            "truncated": sitemap_truncated,
        }
    else:
        entry = run.status_entry("sitemap-products-1.xml", None)
        entry["declared"] = len(sm_children)
        endpoints["sitemap_products"] = entry

    # homepage -------------------------------------------------------------
    home_txt, home_state = run.load_html_file("homepage.html")
    endpoints["homepage"] = run.status_entry("homepage.html", home_state)
    if home_state != "ok":
        home_txt = None

    best_txt, best_state = run.load_html_file("best-selling.html")
    endpoints["best_selling"] = run.status_entry("best-selling.html", best_state)

    # identity -------------------------------------------------------------
    signals = []
    if meta and meta.get("myshopify_domain"):
        signals.append(
            {
                "name": "meta_myshopify_domain",
                "value": meta.get("myshopify_domain"),
                "source": run.rel("meta.json"),
                "family": "shopify_json_api",
            }
        )
    if prod_ep.get("_structure_ok"):
        signals.append(
            {
                "name": "products_json_structure",
                "value": "products[].variants[] present",
                "source": run.rel("products-1.json"),
                "family": "shopify_json_api",
            }
        )
    if home_txt:
        hits = [
            p
            for p in (
                "Shopify.shop",
                "cdn.shopify.com",
                "Shopify.theme",
                "myshopify.com",
            )
            if p in home_txt
        ]
        if hits:
            signals.append(
                {
                    "name": "homepage_shopify_markers",
                    "value": ", ".join(hits),
                    "source": run.rel("homepage.html"),
                    "family": "html_source",
                }
            )
    if sm_children:
        signals.append(
            {
                "name": "sitemap_products_index",
                "value": "%d sitemap_products child map(s)" % len(sm_children),
                "source": run.rel("sitemap.xml"),
                "family": "sitemap",
            }
        )
    identity = {"status": identity_status(signals, endpoints), "signals": signals}
    for sig in signals:
        run.add_evidence(
            "E1",
            "identity_signal",
            "Shopify 身份信号 %s: %s" % (sig["name"], sig["value"]),
            run.url_for(sig["source"][4:], "/"),
            sig["source"],
        )

    # shop -----------------------------------------------------------------
    shop = build_shop(meta, home_txt)

    # theme ----------------------------------------------------------------
    theme = parse_theme(home_txt)
    if theme["source"] == "Shopify.theme":
        run.add_evidence(
            "E1",
            "theme",
            "首页源码 Shopify.theme: name=%s, schema_name=%s, schema_version=%s"
            % (theme["name"], theme["schema_name"], theme["schema_version"]),
            run.url_for("homepage.html", "/"),
            run.rel("homepage.html"),
        )
    elif home_txt is None:
        theme["source"] = None

    # products -------------------------------------------------------------
    currency = shop.get("currency") or None
    pstats, rows = product_stats(products, currency, base)
    page_files = sorted(
        n
        for n in (os.listdir(run.raw) if os.path.isdir(run.raw) else [])
        if n.startswith("page-") and n.endswith(".html")
    )
    samples, sample_ep = ([], None) if products else load_page_sample(run, page_files)
    if sample_ep is not None:
        endpoints["product_page_sample"] = sample_ep
    priced = [x for x in samples if x.get("price") is not None]
    price_source = None
    if priced:
        price_source = "product_page_sample"
        sample_currency = Counter(
            x["currency"] for x in priced if x.get("currency")
        ).most_common(1)
        sstats, rows = sample_stats(
            priced, sample_currency[0][0] if sample_currency else currency, base
        )
        pstats.update(sstats)
    elif products and pstats["price"].get("median") is not None:
        price_source = "products_json"
    pstats["price_source"] = price_source
    pstats.setdefault("unavailable_reasons", {})
    if price_source == "product_page_sample":
        pstats["price_sample_size"] = len(priced)
    elif price_source == "products_json":
        pstats["price_sample_size"] = (
            pstats["price_excluding_virtual"]["count"]
            + (pstats["price_excluding_virtual"]["excluded"])
        )
    else:
        pstats["price_sample_size"] = None
    if prod_ep.get("result") == "ok":
        pstats["count_source"] = "products_json"
        if prod_ep.get("truncated") or prod_ep.get("pagination_complete") is False:
            pstats["count_is_lower_bound"] = True
    elif sitemap_product_urls is not None:
        pstats["count"] = sitemap_product_urls
        pstats["count_source"] = "sitemap"
    else:
        pstats["count"] = None
        pstats["count_source"] = None
    pstats["sitemap_product_urls"] = sitemap_product_urls
    pstats["price_population"] = (
        sitemap_product_urls
        if price_source == "product_page_sample"
        else (pstats.get("count") if price_source else None)
    )
    lastmod_months = Counter(v[:7] for v in sitemap_lastmod.values())
    pstats["sitemap_lastmod_by_month"] = dict(sorted(lastmod_months.items()))
    all_count = None
    for c in collections:
        if c.get("handle") == "all" and c.get("products_count") is not None:
            all_count = c.get("products_count")
            break
    pstats["collections_all_products_count"] = all_count
    pstats["meta_published_products_count"] = (meta or {}).get(
        "published_products_count"
    )
    count = pstats.get("count")
    if all_count is not None and count:
        if abs(all_count - count) / float(count) > COLLECTIONS_MISMATCH_RATIO:
            run.warnings.append("collections_count_mismatch")
    if (
        sitemap_product_urls is not None
        and count
        and pstats["count_source"] != "sitemap"
    ):
        if abs(sitemap_product_urls - count) / float(count) > SITEMAP_MISMATCH_RATIO:
            run.warnings.append("sitemap_count_mismatch")
    if prod_ep.get("truncated"):
        run.warnings.append("products_json_truncated")
    if prod_ep.get("pagination_complete") is False:
        run.warnings.append("products_pagination_unverified")
    if prod_ep.get("result") == "ok" and count == 0:
        run.warnings.append("products_json_empty")
    if count is not None:
        run.add_evidence(
            "E1",
            "product_count",
            "公开商品数 %s (来源 %s)" % (count, pstats["count_source"]),
            run.url_for("products-1.json", "/products.json?limit=250&page=1")
            if pstats["count_source"] != "sitemap"
            else run.url_for("sitemap.xml", "/sitemap.xml"),
            "raw/products-*.json"
            if pstats["count_source"] != "sitemap"
            else "raw/sitemap.xml",
        )
    if price_source == "product_page_sample":
        pr = pstats["price"]
        run.add_evidence(
            "E1",
            "price_sample",
            "商品页抽样 %d 款 (共约 %s 款) 的最低价中位数 %s %s (P25 %s, P75 %s); "
            "是抽样估计, 不是全店统计"
            % (
                len(priced),
                sitemap_product_urls if sitemap_product_urls is not None else "?",
                pr["median"],
                pr.get("currency") or "",
                pr["p25"],
                pr["p75"],
            ),
            run.url_for(priced[0]["file"][4:], "/products/%s" % priced[0]["handle"]),
            "raw/page-product-*.html",
        )
    elif pstats.get("price", {}).get("median") is not None:
        pr = pstats["price"]
        pv = pstats.get("price_excluding_virtual") or {}
        run.add_evidence(
            "E1",
            "price_summary",
            "商品最低价中位数 %s %s (P25 %s, P75 %s, 基础币种); "
            "排除虚拟商品后 (%s 款) 中位数 %s, P25 %s, P75 %s"
            % (
                pr["median"],
                pr.get("currency") or "",
                pr["p25"],
                pr["p75"],
                pv.get("count"),
                pv.get("median"),
                pv.get("p25"),
                pv.get("p75"),
            ),
            run.url_for("products-1.json", "/products.json?limit=250&page=1"),
            "raw/products-*.json",
        )

    # best selling ---------------------------------------------------------
    best = []
    if best_txt and best_state == "ok":
        best = best_selling_handles(best_txt)
        if best:
            run.add_evidence(
                "E1",
                "best_selling_order",
                "best-selling 排序页前列商品: %s (商家可控排序, 非销量)"
                % ", ".join(best[:3]),
                run.url_for(
                    "best-selling.html", "/collections/all?sort_by=best-selling"
                ),
                run.rel("best-selling.html"),
            )

    # offsite --------------------------------------------------------------
    offsite, off_eps = build_offsite(run)
    endpoints.update(off_eps)
    if site_unreachable(endpoints):
        # other hosts unreachable too: this machine is offline, not the site
        run.warnings.append(
            "network_unavailable"
            if network_unavailable(off_eps)
            else "site_unreachable"
        )

    # policies -------------------------------------------------------------
    policies = {}
    for name in POLICY_NAMES:
        fname = "policy-%s.html" % name
        txt, state = run.load_html_file(fname)
        entry = run.status_entry(fname, state)
        if txt and state == "ok":
            body = extract_balanced_div(txt, "shopify-policy__body")
            if body is None:
                body = extract_balanced_div(txt, "shopify-policy__container")
            if body is None:
                m = re.search(r"(?is)<main\b.*?</main>", txt)
                body = m.group(0) if m else txt
            text = html_to_text(body)
            tpath = "policy-%s.txt" % name
            with open(run.path(tpath), "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
            entry["chars"] = len(text)
            entry["text_path"] = run.rel(tpath)
            if len(text) < POLICY_MIN_CHARS:
                run.warnings.append("policy_near_empty:%s" % name)
            run.add_evidence(
                "E1",
                "policy_text",
                "%s 正文约 %d 字符" % (name, len(text)),
                run.url_for(fname, "/policies/%s" % name),
                run.rel(fname),
            )
        policies[name] = entry
        endpoints["policy_" + name.replace("-", "_")] = {
            k: v for k, v in entry.items() if k not in ("chars", "text_path")
        }

    # about / other pages --------------------------------------------------
    if "page-about.html" in page_files or "page-about.html" in run.by_file:
        endpoints["page_about"] = run.status_entry("page-about.html", None)
    from_collect = (sample_ep or {}).get("source") == "collect"
    l1_samples = build_l1_samples(
        run,
        products,
        page_files,
        {x["file"][4:] for x in samples} if from_collect else (),
        {x["handle"]: x["price"] for x in priced},
    )
    for sample in l1_samples:
        if sample.get("result") == "ok":
            run.add_evidence(
                "E1",
                "l1_sample_page",
                "商品页样本 (%s): %s, 最低价 %s %s"
                % (
                    L1_ROLE_LABELS.get(sample.get("role"), "未标注"),
                    sample["handle"],
                    sample.get("price"),
                    currency or "",
                ),
                run.url_for(sample["file"][4:], "/products/%s" % sample["handle"]),
                sample["file"],
            )

    # html signals -------------------------------------------------------------
    sources = []
    if home_txt:
        sources.append(("homepage.html", home_txt))
    for fname in page_files:
        txt, state = run.load_html_file(fname)
        if txt and state == "ok":
            sources.append((fname, txt))
    html_signals = build_html_signals(sources)
    if theme["source"] == "not_observed" and signals:
        # Shopify signals but no Online Store theme object: likely headless.
        theme["storefront_hint"] = (
            "headless_possible" if html_signals["frameworks_possible"] else "unknown"
        )

    # finalize endpoints (drop private keys) + one evidence entry per ok endpoint
    for key, ep in endpoints.items():
        if ep.get("result") == "ok" and key in ENDPOINT_FILES:
            fname, locator, level = ENDPOINT_FILES[key]
            if key.startswith("policy_"):
                fname = locator = "policy-%s.html" % key[7:].replace("_", "-")
            run.add_evidence(
                level,
                "endpoint",
                "端点可访问 (HTTP 200): %s%s"
                % (key, " (内容被截断)" if ep.get("truncated") else ""),
                ep.get("url") or run.url_for(fname, ""),
                "raw/" + locator,
            )
    for ep in endpoints.values():
        for k in [k for k in ep if k.startswith("_")]:
            del ep[k]

    coverage = build_coverage(run, endpoints, pstats)
    if coverage["products_json"] == "rate_limited" or (
        "products_json" in coverage["rate_limited_endpoints"]
    ):
        run.warnings.append("products_json_rate_limited")
    if price_source == "product_page_sample":
        run.warnings.append("prices_from_page_sample")
        if len(priced) < PRICE_SAMPLE_SMALL:
            run.warnings.append("price_sample_small")

    summary = {
        "schema": SCHEMA,
        "tool_version": TOOL_VERSION,
        "domain": run.domain,
        "captured_at_utc": run.captured_at,
        "summarized_at_utc": utc_now(),
        "endpoints": endpoints,
        "identity": identity,
        "shop": shop,
        "theme": theme,
        "products": pstats,
        "coverage": coverage,
        "best_selling_top": best,
        "l1_samples": l1_samples,
        "offsite": offsite,
        "policies": policies,
        "html_signals": html_signals,
        "warnings": sorted(set(run.warnings), key=run.warnings.index),
        "errors": run.errors,
    }

    write_outputs(run, summary, rows)
    return summary


L1_ROLE_LABELS = {"entry": "入门款", "main": "主力款", "premium": "高价款"}


def build_l1_samples(run, products, page_files, exclude=(), page_prices=None):
    """L1 sample product pages from _status.json, else from file names.

    ``exclude``: file names that belong to collect.py's price page sample
    (not L1 samples unless _status.json lists them as such).
    ``page_prices``: {handle: price} read from sampled product pages, used
    when products.json gave no price."""
    prices = {}
    for p in products:
        vals = [
            x
            for x in (to_float(v.get("price")) for v in p.get("variants") or [])
            if x is not None
        ]
        if p.get("handle") and vals:
            prices[p["handle"]] = min(vals)
    for handle, price in (page_prices or {}).items():
        prices.setdefault(handle, price)
    out = []
    listed = run.status.get("l1_samples")
    if isinstance(listed, list) and listed:
        for item in listed:
            if not isinstance(item, dict) or not item.get("handle"):
                continue
            fname = (item.get("file") or "")[4:] or (
                "page-product-%s.html" % item["handle"]
            )
            exists = fname in page_files
            entry = {
                "handle": item["handle"],
                "role": item.get("role"),
                "price": item.get("price", prices.get(item["handle"])),
                "file": "raw/" + fname,
                "result": "ok" if exists else (item.get("result") or "missing"),
            }
            for key in ("basis", "basis_value", "basis_pool"):
                if key in item:
                    entry[key] = item[key]
            out.append(entry)
        return out
    for fname in page_files:
        if fname.startswith("page-product-") and fname not in exclude:
            handle = fname[len("page-product-") : -len(".html")]
            out.append(
                {
                    "handle": handle,
                    "role": None,
                    "price": prices.get(handle),
                    "file": "raw/" + fname,
                    "result": "ok",
                }
            )
    return out


SAMPLE_BASIS = "per-product lowest price shown on sampled product pages"
SAMPLE_UNAVAILABLE = {
    "on_sale_share": "product pages do not reliably show compare-at prices",
    "discount_depth_median": "product pages do not reliably show compare-at prices",
    "variants": "variant lists are not read from product pages",
    "created_first": "creation dates are only in the bulk product data",
    "published_by_month": "publish dates are only in the bulk product data",
}


def load_page_sample(run, page_files):
    """Sampled product pages: (entries, endpoint entry or None).

    Entries come from ``_status.json`` ``price_sample.items`` (collect.py);
    without it (files saved by hand), every ``page-product-*.html`` is used.
    Each entry: handle, file, result, and the parse_product_page() facts.
    """
    info = run.status.get("price_sample")
    listed = []
    if isinstance(info, dict) and isinstance(info.get("items"), list):
        origin = "collect"
        for it in info["items"]:
            if isinstance(it, dict) and it.get("handle"):
                fname = (it.get("file") or "")[4:] or (
                    "page-product-%s.html" % it["handle"]
                )
                listed.append((it["handle"], fname, it.get("result"), it))
    else:
        origin = "saved_files"
        info = {}
        for fname in page_files:
            if fname.startswith("page-product-"):
                listed.append(
                    (fname[len("page-product-") : -len(".html")], fname, None, {})
                )
    if not listed:
        return [], None
    out = []
    for handle, fname, result, item in listed:
        entry = {"handle": handle, "file": "raw/" + fname}
        txt, state = run.load_html_file(fname)
        if txt and state == "ok":
            entry["result"] = "ok"
            facts = parse_product_page(txt)
            entry.update(facts or {"price": None})
        else:
            entry["result"] = "blocked" if state == "blocked" else (result or "missing")
            entry["price"] = None
            if item.get("http_status") is not None:
                entry["http_status"] = item["http_status"]
        out.append(entry)
    fetched = sum(1 for x in out if x["result"] == "ok")
    priced = sum(1 for x in out if x.get("price") is not None)
    ep = {
        "result": "ok" if priced else ("error" if fetched else "blocked"),
        "source": origin,
        "requested": len(out),
        "fetched": fetched,
        "priced": priced,
        "population": info.get("population"),
        "stopped": info.get("stopped"),
    }
    if any(x.get("http_status") == 429 for x in out):
        ep["http_status"] = 429
    return out, ep


def sample_stats(priced, currency, base):
    """Price statistics of sampled product pages: (stats subset, tsv rows).

    Only what a product page shows reliably is computed; discount, variant
    and date fields are None, with the reason in ``unavailable_reasons``.
    """
    stats, _rows = product_stats([sample_product(x) for x in priced], currency, base)
    out = {
        "price": stats["price"],
        "price_excluding_virtual": stats["price_excluding_virtual"],
        "price_bands": stats["price_bands"],
        "availability": stats["availability"],
        "on_sale_share": None,
        "discount_depth_median": None,
        "variants": {"median": None, "max": None, "multi_variant_share": None},
        "options_top": [],
        "product_types_top": [],
        "vendors_top": [],
        "tags_top": [],
        "created_first": None,
        "created_last": None,
        "published_by_month": {},
        "unavailable_reasons": dict(SAMPLE_UNAVAILABLE),
    }
    out["price"]["basis"] = SAMPLE_BASIS
    out["price_excluding_virtual"]["basis"] = (
        SAMPLE_BASIS + ", excluding virtual items and prices below %g" % MIN_REAL_PRICE
    )
    rows = []
    for x in priced:
        rows.append(
            [
                None,
                x["handle"],
                "%s/products/%s" % (base, x["handle"]),
                x.get("title"),
                "",
                "",
                "",
                x["price"],
                x.get("price_max"),
                x.get("compare_at"),
                x.get("currency") or currency or "",
                None,
                "",
                ""
                if x.get("available") is None
                else ("true" if x["available"] else "false"),
                None,
                None,
                "product_page",
            ]
        )
    return out, rows


COVERAGE_RESULTS = ("not_found", "blocked", "error")


def build_coverage(run, endpoints, pstats):
    """What the numbers in this summary are based on (see data-contract.md)."""
    limited = []
    for req in run.status.get("requests") or []:
        if isinstance(req, dict) and req.get("http_status") == 429:
            if req.get("key") and req["key"] not in limited:
                limited.append(req["key"])
    ep = endpoints.get("products_json") or {}
    res = ep.get("result")
    if res == "ok":
        partial = (
            ep.get("truncated")
            or ep.get("pagination_complete") is False
            or "products_json" in limited
        )
        pj = "partial" if partial else "ok"
    elif res in COVERAGE_RESULTS:
        pj = res
        if res != "not_found" and (
            ep.get("http_status") == 429 or "products_json" in limited
        ):
            pj = "rate_limited"
    else:
        pj = "not_checked"
    price_source = pstats.get("price_source")
    return {
        "products_json": pj,
        "product_count_source": pstats.get("count_source"),
        "price_source": price_source,
        "price_sample_size": pstats.get("price_sample_size"),
        "price_population": pstats.get("price_population"),
        "discount_data": price_source == "products_json",
        "new_product_cadence": bool(
            pstats.get("published_by_month") or pstats.get("created_first")
        ),
        "rate_limited_endpoints": limited,
    }


def load_products(run):
    pages = list_pages(run.raw, "products-", ".json")
    ep = {"result": "not_checked", "pages": 0, "truncated": False}
    products = []
    if not pages:
        e = run.status_entry("products-1.json", None)
        e.update({"pages": 0, "truncated": False})
        return products, e
    ep = run.status_entry(pages[0][1], None)
    ep["pages"] = len(pages)
    ep["truncated"] = False
    seen = set()
    page_lens = []
    states = []
    for _n, fname in pages:
        obj, state, note = run.load_json_file(fname, "products")
        states.append(state)
        items = None
        if isinstance(obj, dict) and isinstance(obj.get("products"), list):
            items = [p for p in obj["products"] if isinstance(p, dict)]
        if items is None:
            page_lens.append(None)
            continue
        page_lens.append(len(items))
        if state == "salvaged":
            ep["truncated"] = True
        for p in items:
            key = p.get("id") or p.get("handle")
            if key in seen and key is not None:
                continue
            seen.add(key)
            products.append(p)
    good = [s for s in states if s in ("ok", "extracted", "salvaged")]
    if not good:
        ep["result"] = "blocked" if "blocked" in states else "error"
        if pages[0][1] in run.by_file and run.by_file[pages[0][1]].get("result"):
            if run.by_file[pages[0][1]]["result"] != "ok":
                ep["result"] = run.by_file[pages[0][1]]["result"]
        return [], ep
    ep["result"] = "ok"
    if "extracted" in states:
        ep["note"] = "some pages extracted from wrapped text"
    lens = [n for n in page_lens if n is not None]
    last = page_lens[-1]
    complete = False
    if last == 0:
        complete = True
    elif last is not None and lens and last < max(lens):
        complete = True
    elif last is not None and len(lens) == 1 and last not in (30, 50, 250):
        complete = True
    ep["pagination_complete"] = complete and not ep["truncated"]
    ep["_structure_ok"] = any(isinstance(p.get("variants"), list) for p in products)
    # a legal empty page still has the Shopify shape, but it is not a signal
    return products, ep


def identity_status(signals, endpoints):
    if len(signals) >= 2:
        return "confirmed"
    if len(signals) == 1:
        return "probable"
    core = ["meta_json", "products_json", "homepage", "sitemap"]
    results = [endpoints.get(k, {}).get("result") for k in core]
    trunc = any(endpoints.get(k, {}).get("truncated") for k in core)
    if endpoints.get("homepage", {}).get("result") == "ok" and not trunc:
        if all(r in ("ok", "not_found") for r in results):
            return "not_shopify"
    return "unconfirmed"


def build_shop(meta, home_txt):
    shop = {
        "name": None,
        "shop_id": None,
        "myshopify_domain": None,
        "currency": None,
        "country": None,
        "ships_to_count": None,
        "published_products_count": None,
        "published_collections_count": None,
        "source": None,
    }
    if meta:
        ships = meta.get("ships_to_countries")
        shop.update(
            {
                "name": meta.get("name"),
                "shop_id": meta.get("id"),
                "myshopify_domain": meta.get("myshopify_domain"),
                "currency": meta.get("currency"),
                "country": meta.get("country"),
                "ships_to_count": len(ships) if isinstance(ships, list) else None,
                "published_products_count": meta.get("published_products_count"),
                "published_collections_count": meta.get("published_collections_count"),
                "source": "meta.json",
            }
        )
    if home_txt:
        if not shop["myshopify_domain"]:
            m = re.search(r'Shopify\.shop\s*=\s*"([^"]+)"', home_txt)
            if m:
                shop["myshopify_domain"] = m.group(1)
                shop["source"] = shop["source"] or "homepage"
        if not shop["currency"]:
            cur = js_object_after(home_txt, r"Shopify\.currency\s*=\s*\{")
            if cur and cur.get("active"):
                # storefront currency may be geo-localized, not the base currency
                shop["currency"] = cur.get("active")
                shop["currency_source"] = "homepage_Shopify.currency (may be localized)"
        if not shop["country"]:
            m = re.search(r'Shopify\.country\s*=\s*"([A-Z]{2})"', home_txt)
            if m:
                shop["country"] = m.group(1)
                shop["country_source"] = "homepage_Shopify.country (visitor market)"
    return shop


def parse_theme(home_txt):
    theme = {
        "name": None,
        "schema_name": None,
        "schema_version": None,
        "theme_store_id": None,
        "role": None,
        "id": None,
        "source": "not_observed",
    }
    if not home_txt:
        return theme
    obj = js_object_after(home_txt, r"Shopify\.theme\s*=\s*\{")
    if obj is None:
        m = re.search(r"Shopify\.theme\s*=\s*\{(.*?)\};", home_txt, re.S)
        if not m:
            return theme
        obj = {}
        for key in ("name", "schema_name", "schema_version", "role"):
            km = re.search(r'"?%s"?\s*:\s*"([^"]*)"' % key, m.group(1))
            if km:
                obj[key] = km.group(1)
        for key in ("theme_store_id", "id"):
            km = re.search(r'"?%s"?\s*:\s*(\d+|null)' % key, m.group(1))
            if km and km.group(1) != "null":
                obj[key] = int(km.group(1))
    for key in (
        "name",
        "schema_name",
        "schema_version",
        "theme_store_id",
        "role",
        "id",
    ):
        if key in obj:
            theme[key] = obj.get(key)
    theme["source"] = "Shopify.theme"
    return theme


def product_stats(products, currency, base):
    rows = []
    prices = []
    real_prices = []
    real_in_stock = []
    depths = []
    on_sale = 0
    var_counts = []
    options = Counter()
    types = Counter()
    vendors = Counter()
    tags = Counter()
    created = []
    by_month = Counter()
    avail = {"available": 0, "sold_out": 0, "unknown": 0}
    for p in products:
        variants = [v for v in (p.get("variants") or []) if isinstance(v, dict)]
        vprices = [
            x for x in (to_float(v.get("price")) for v in variants) if x is not None
        ]
        pmin = min(vprices) if vprices else None
        pmax = max(vprices) if vprices else None
        if pmin is not None:
            prices.append(pmin)
            if is_real_product(p, pmin):
                real_prices.append(pmin)
                if in_stock(p):
                    real_in_stock.append(pmin)
        cmp_max = None
        best_depth = None
        for v in variants:
            pr = to_float(v.get("price"))
            ca = to_float(v.get("compare_at_price"))
            if ca is not None:
                cmp_max = ca if cmp_max is None else max(cmp_max, ca)
            if pr is not None and ca is not None and ca > pr and ca > 0:
                d = 1.0 - pr / ca
                best_depth = d if best_depth is None else max(best_depth, d)
        if best_depth is not None:
            on_sale += 1
            depths.append(best_depth)
        var_counts.append(len(variants))
        opt_names = []
        for o in p.get("options") or []:
            if isinstance(o, dict):
                name = o.get("name")
                vals = o.get("values") or []
            else:
                name, vals = str(o), []
            if not name or (name == "Title" and vals in ([], ["Default Title"])):
                continue
            opt_names.append(name)
            options[name] += 1
        ptype = (p.get("product_type") or "").strip()
        if ptype:
            types[ptype] += 1
        vendor = (p.get("vendor") or "").strip()
        if vendor:
            vendors[vendor] += 1
        ptags = p.get("tags") or []
        if isinstance(ptags, str):
            ptags = [t.strip() for t in ptags.split(",")]
        ptags = [t for t in ptags if t]
        for t in ptags:
            tags[t] += 1
        cat = (p.get("created_at") or "")[:10]
        if cat:
            created.append(cat)
        pat = p.get("published_at") or ""
        if pat[:7]:
            by_month[pat[:7]] += 1
        avail_flags = [v.get("available") for v in variants if "available" in v]
        if any(a is True for a in avail_flags):
            available = True
            avail["available"] += 1
        elif avail_flags:
            available = False
            avail["sold_out"] += 1
        else:
            available = None
            avail["unknown"] += 1
        handle = p.get("handle") or ""
        rows.append(
            [
                p.get("id"),
                handle,
                "%s/products/%s" % (base, handle) if handle else "",
                p.get("title"),
                ptype,
                vendor,
                ", ".join(ptags),
                pmin,
                pmax,
                cmp_max,
                currency or "",
                len(variants),
                "/".join(opt_names),
                "" if available is None else ("true" if available else "false"),
                p.get("created_at"),
                p.get("published_at"),
                "products_json",
            ]
        )
    sp = sorted(prices)
    n = len(products)
    bands = []
    if sp:
        edges = PRICE_EDGES + [None]
        for i in range(len(edges) - 1):
            lo, hi = edges[i], edges[i + 1]
            c = sum(1 for x in sp if x >= lo and (hi is None or x < hi))
            if c:
                bands.append({"from": lo, "to": hi, "count": c})
    if avail["unknown"] == 0:
        del avail["unknown"]
    stats = {
        "count": n,
        "count_source": None,
        "price": dict(
            {"currency": currency, "basis": "per-product lowest variant price"},
            **price_quartiles(sp),
        ),
        "price_excluding_virtual": dict(
            {
                "currency": currency,
                "basis": "per-product lowest variant price, excluding virtual "
                "items and prices below %g" % MIN_REAL_PRICE,
                "count": len(real_prices),
                "excluded": len(prices) - len(real_prices),
            },
            in_stock=dict(
                {"count": len(real_in_stock)}, **price_quartiles(sorted(real_in_stock))
            ),
            **price_quartiles(sorted(real_prices)),
        ),
        "price_bands": bands,
        "on_sale_share": round(on_sale / float(n), 4) if n else None,
        "discount_depth_median": round(median(depths), 4) if depths else None,
        "variants": {
            "median": median(var_counts) if var_counts else None,
            "max": max(var_counts) if var_counts else None,
            "multi_variant_share": (
                round(sum(1 for c in var_counts if c > 1) / float(n), 4) if n else None
            ),
        },
        "options_top": top_n(options),
        "product_types_top": top_n(types),
        "vendors_top": top_n(vendors),
        "tags_top": top_n(tags),
        "created_first": min(created) if created else None,
        "created_last": max(created) if created else None,
        "published_by_month": dict(sorted(by_month.items())),
        "availability": avail,
    }
    return stats, rows


def best_selling_handles(src, limit=10):
    out = []
    for m in re.finditer(r'href=["\']([^"\']*?/products/([^"\'?#/]+))', src):
        handle = m.group(2)
        if not HANDLE_RE.match(handle) or handle.endswith((".js", ".json", ".oembed")):
            continue
        if handle in out:
            continue
        out.append(handle)
        if len(out) >= limit:
            break
    return out


def build_offsite(run):
    eps = {}
    offsite = {
        "rdap": {"registered": None, "expires": None, "registrar": None},
        "wayback": {"first_capture": None, "captures_by_year": {}},
    }
    rdap, st, note = run.load_json_file("rdap.json")
    eps["rdap"] = run.status_entry("rdap.json", st, note)
    if isinstance(rdap, dict):
        for ev in rdap.get("events") or []:
            if not isinstance(ev, dict):
                continue
            action = (ev.get("eventAction") or "").lower()
            date = (ev.get("eventDate") or "")[:10] or None
            if action == "registration":
                offsite["rdap"]["registered"] = date
            elif action == "expiration":
                offsite["rdap"]["expires"] = date
        for ent in rdap.get("entities") or []:
            if isinstance(ent, dict) and "registrar" in (ent.get("roles") or []):
                vcard = ent.get("vcardArray")
                if isinstance(vcard, list) and len(vcard) > 1:
                    for item in vcard[1]:
                        if isinstance(item, list) and len(item) > 3 and item[0] == "fn":
                            offsite["rdap"]["registrar"] = item[3]
                            break
        if offsite["rdap"]["registered"]:
            run.add_evidence(
                "E4",
                "domain_registration",
                "域名注册日期 %s (是域名注册日期, 不是开店日期)"
                % offsite["rdap"]["registered"],
                run.url_for("rdap.json", ""),
                run.rel("rdap.json"),
            )
    wb, st, note = run.load_json_file("wayback.json")
    eps["wayback"] = run.status_entry("wayback.json", st, note)
    if isinstance(wb, list):
        stamps = []
        for row in wb:
            if isinstance(row, list) and row and re.match(r"^\d{8,14}$", str(row[0])):
                stamps.append(str(row[0]))
        if stamps:
            stamps.sort()
            offsite["wayback"]["first_capture"] = "%s-%s" % (
                stamps[0][:4],
                stamps[0][4:6],
            )
            years = Counter(s[:4] for s in stamps)
            offsite["wayback"]["captures_by_year"] = dict(sorted(years.items()))
            run.add_evidence(
                "E4",
                "wayback_first_capture",
                "Wayback 最早快照 %s" % offsite["wayback"]["first_capture"],
                run.url_for("wayback.json", ""),
                run.rel("wayback.json"),
            )
    return offsite, eps


def build_html_signals(sources):
    apps = []
    seen_apps = set()
    tracking = []
    icons = []
    hreflang = []
    frameworks = []
    localization = "not_observed"
    for fname, src in sources:
        low = src.lower()
        for name, pats in APP_SIGNATURES:
            if name in seen_apps:
                continue
            for pat in pats:
                if pat in low:
                    seen_apps.add(name)
                    apps.append(
                        {
                            "name": name,
                            "pattern": pat,
                            "source": "raw/" + fname,
                            "confidence": "possible",
                        }
                    )
                    break
        for name, pats in TRACKING_SIGNATURES:
            if name not in tracking and any(p in low for p in pats):
                tracking.append(name)
        for m in re.finditer(
            r'(?:aria-labelledby|id)\s*=\s*["\']pi-([a-z0-9_\-]+)["\']|payment-icon--([a-z0-9_\-]+)',
            low,
        ):
            name = (m.group(1) or m.group(2)).replace("_", "-")
            if name and name not in icons:
                icons.append(name)
        for tag in re.findall(r"(?is)<[a-z]+\b[^>]*payment[^>]*>", src):
            attrs = parse_attrs(tag)
            if "payment" not in attrs.get("class", "").lower():
                continue
            label = attrs.get("aria-label") or attrs.get("title") or attrs.get("alt")
            if label and len(label) <= 40:
                name = re.sub(r"[\s_]+", "-", label.strip().lower())
                if name not in icons:
                    icons.append(name)
        for name, pats in FRAMEWORK_SIGNATURES:
            if name not in frameworks and any(p in low for p in pats):
                frameworks.append(name)
        for tag in re.findall(r"(?is)<link\b[^>]*>", src):
            attrs = parse_attrs(tag)
            if attrs.get("rel", "").lower() == "alternate" and attrs.get("hreflang"):
                pair = [attrs["hreflang"].lower(), attrs.get("href", "")]
                if pair not in hreflang:
                    hreflang.append(pair)
        if re.search(
            r'id=["\']localization_form|action=["\'][^"\']*/localization["\']', low
        ):
            localization = "present"
    return {
        "confidence": "possible",
        "sources": ["raw/" + f for f, _ in sources],
        "apps_possible": apps,
        "payment_icons_observed": icons,
        "hreflang": hreflang,
        "tracking_tags": tracking,
        "frameworks_possible": frameworks,
        "localization_selector": localization if sources else None,
    }


TSV_HEADER = [
    "product_id",
    "handle",
    "url",
    "title",
    "product_type",
    "vendor",
    "tags",
    "price_min",
    "price_max",
    "compare_at_max",
    "currency",
    "variant_count",
    "options",
    "available",
    "created_at",
    "published_at",
    "source",
]


def tsv_cell(v):
    if v is None:
        return ""
    if isinstance(v, float):
        v = ("%.2f" % v).rstrip("0").rstrip(".") if v != int(v) else str(int(v))
    return re.sub(r"[\t\r\n]+", " ", str(v))


def write_outputs(run, summary, rows):
    with open(os.path.join(run.run_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    with open(os.path.join(run.run_dir, "products.tsv"), "w", encoding="utf-8") as fh:
        fh.write("\t".join(TSV_HEADER) + "\n")
        for row in rows:
            fh.write("\t".join(tsv_cell(c) for c in row) + "\n")
    with open(os.path.join(run.run_dir, "evidence.jsonl"), "w", encoding="utf-8") as fh:
        for ev in run.evidence:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")


STORE_ENDPOINTS = (
    "homepage",
    "meta_json",
    "products_json",
    "collections_json",
    "sitemap",
    "best_selling",
)


def site_unreachable(endpoints):
    """True when the homepage failed on the network (no HTTP answer) and no
    store endpoint returned anything: wrong domain, DNS failure or offline."""
    home = endpoints.get("homepage") or {}
    if home.get("result") != "error" or home.get("http_status") is not None:
        return False
    return not any(
        (endpoints.get(k) or {}).get("result") in ("ok", "blocked", "not_found")
        for k in STORE_ENDPOINTS
    )


def network_unavailable(off_eps):
    """True when the off-site lookups (registry, archive) were tried and got
    no HTTP answer either: the environment has no network."""
    tried = [e for e in off_eps.values() if e.get("result") != "not_checked"]
    return bool(tried) and all(
        e.get("result") == "error" and e.get("http_status") is None for e in tried
    )


def human_digest(summary):
    """Short Chinese digest printed by both CLIs."""
    p = summary.get("products") or {}
    th = summary.get("theme") or {}
    eps = summary.get("endpoints") or {}
    failed = [k for k, v in eps.items() if v.get("result") in ("blocked", "error")]
    lines = [
        "店铺: %s" % summary.get("domain"),
        "Shopify 身份: %s (%d 个信号)"
        % (summary["identity"]["status"], len(summary["identity"]["signals"])),
        "公开商品数: %s (来源 %s%s)"
        % (
            p.get("count"),
            p.get("count_source"),
            ", 下限值" if p.get("count_is_lower_bound") else "",
        ),
        "主题: %s" % (th.get("name") or th.get("source") or "未观察到"),
    ]
    if p.get("price", {}).get("median") is not None:
        pr = p["price"]
        lines.append(
            "价格中位数: %s %s (P25 %s / P75 %s)"
            % (pr["median"], pr.get("currency") or "", pr["p25"], pr["p75"])
        )
        pv = p.get("price_excluding_virtual") or {}
        if pv.get("median") is not None:
            lines.append(
                "排除虚拟商品后: 中位数 %s (P25 %s / P75 %s, 排除 %s 款)"
                % (pv["median"], pv["p25"], pv["p75"], pv.get("excluded"))
            )
    cov = summary.get("coverage") or {}
    warns = summary.get("warnings") or []
    if "products_json_rate_limited" in warns:
        if cov.get("products_json") == "rate_limited":
            lines.append(
                "注意: 店铺限制了批量读取商品数据 (429), 已按要求等待后仍受限; "
                "没有反复重试, 也没有换身份绕过."
            )
        else:
            lines.append(
                "注意: 批量读取商品数据中途被店铺限流 (429), 只拿到了部分商品."
            )
    if cov.get("price_source") == "product_page_sample":
        lines.append(
            "价格基于 %s 个商品页抽样 (共约 %s 款), 是估计不是全店统计; "
            "折扣, 上新节奏这类数据本次没有."
            % (
                cov.get("price_sample_size"),
                cov.get("price_population")
                if cov.get("price_population") is not None
                else "?",
            )
        )
        if "price_sample_small" in warns:
            lines.append(
                "注意: 抽样不足 %d 款, 价格结论只能当参考." % PRICE_SAMPLE_SMALL
            )
    elif cov.get("products_json") == "rate_limited":
        lines.append("价格: 本次没有取到 (商品页抽样也没有拿到价格).")
    if cov.get("products_json") == "rate_limited":
        lines.append(
            "建议: 报告写明价格依据 (抽样或缺失); 不要立刻重跑, "
            "隔一段时间 (例如 1 小时后) 再试一次."
        )
    lines.append("失败端点: %s" % (", ".join(failed) if failed else "无"))
    if summary.get("warnings"):
        lines.append("警告: %s" % ", ".join(summary["warnings"]))
    if "network_unavailable" in (summary.get("warnings") or []):
        lines.append(
            "注意: 当前环境连不上网 (店铺和其他网站都没有响应), 不代表网站不存在. "
            "有网页读取工具就按 SKILL.md 模式 B 取数; 没有就写 failed 短报告, "
            "说明是当前环境不能联网, 并运行 deliver.py 交付."
        )
    if "site_unreachable" in (summary.get("warnings") or []):
        lines.append(
            "注意: 网站打不开 (域名不存在, 拼错或当前网络不通). 不要改用别的方式"
            "回答, 按 SKILL.md 的'失败时'写一份 failed 短报告并运行 deliver.py 交付."
        )
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Summarize a store-teardown run dir (offline)."
    )
    ap.add_argument(
        "run_dir",
        help=(
            'the "<domain> 对标拆解 <date>" run folder (its %s/ subfolder is used '
            "automatically) or the %s/ folder itself, i.e. the folder that "
            "contains raw/" % (RAW_DIRNAME, RAW_DIRNAME)
        ),
    )
    ap.add_argument("--domain", help="override domain name")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    data_dir = resolve_data_dir(args.run_dir)
    raw = os.path.join(data_dir, "raw")
    if not os.path.isdir(raw):
        print(
            "错误: 找不到 %s, 请先运行 collect.py 或手动保存原始文件." % raw,
            file=sys.stderr,
        )
        return 2
    summary = summarize_run(data_dir, args.domain)
    if not args.quiet:
        print(human_digest(summary))
        print("产物: %s" % os.path.abspath(data_dir))
    return 0


def resolve_data_dir(path):
    """Accept the top-level run folder or its 原始数据/ folder."""
    path = os.path.abspath(os.path.expanduser(path))
    nested = os.path.join(path, RAW_DIRNAME)
    if not os.path.isdir(os.path.join(path, "raw")) and os.path.isdir(nested):
        return nested
    return path


if __name__ == "__main__":
    sys.exit(main())
