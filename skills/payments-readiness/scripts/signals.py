"""Offline extraction of storefront signals from saved pages (raw/).

Everything here reads files only; nothing touches the network. The patterns
follow shopify-store-teardown (identity, policies, payment icons, app
signatures) with payment-specific additions. Every signature hit is only a
trace on the page ("possible"): an icon or a script never proves that a
payment method is enabled in the merchant's back office.
Python 3.8+, standard library only.
"""

import html
import json
import os
import re

POLICY_NAMES = [
    "refund-policy",
    "shipping-policy",
    "privacy-policy",
    "terms-of-service",
    "contact-information",
]
POLICY_MIN_CHARS = 200

# Link text / href keywords used to discover policy pages on any platform.
POLICY_LINK_HINTS = {
    "refund-policy": r"refund|return",
    "shipping-policy": r"shipping|delivery",
    "privacy-policy": r"privacy",
    "terms-of-service": r"terms|conditions",
    "contact-information": r"contact",
}
PAGE_LINK_HINTS = {
    "contact": r"contact",
    "faq": r"\bfaqs?\b|frequently|help-?center|/help\b",
}

BLOCK_MARKERS = [
    "just a moment...",
    "cf-chl",
    "captcha",
    "access denied",
    "your connection needs to be verified",
    "attention required",
    "verify you are human",
]

# Payment provider script / SDK traces (S05). Matched against raw HTML.
# Domestic aggregators are deliberately absent until calibrated on real
# samples (spec open question 4).
PAYMENT_SIGNATURES = [
    ("Stripe", ["js.stripe.com", "m.stripe.network"]),
    ("PayPal", ["paypal.com/sdk/js", "paypalobjects.com", "paypal-button"]),
    ("Braintree", ["braintreegateway.com", "js.braintreegateway"]),
    ("Adyen", ["checkoutshopper-live.adyen.com", "adyen.com/checkoutshopper"]),
    ("Checkout.com", ["cdn.checkout.com", "frames.checkout.com"]),
    ("Airwallex", ["checkout.airwallex.com", "static.airwallex.com"]),
    ("Klarna", ["klarna.com", "klarnaservices.com", "klarna-placement"]),
    ("Afterpay", ["afterpay.com", "afterpay-placement", "static.afterpay"]),
    ("Affirm", ["affirm.com/js", "cdn1.affirm.com"]),
    ("Shop Pay Installments", ["shopify-payment-terms", "shop_pay_installments"]),
]
BNPL_NAMES = ("Klarna", "Afterpay", "Affirm", "Shop Pay Installments")

# Third-party apps that matter for payments readiness.
APP_SIGNATURES = [
    # customer service / chat (T01)
    ("Gorgias", "chat", ["gorgias.chat", "gorgias.io"]),
    ("Tidio", "chat", ["tidio.co", "tidiochat"]),
    ("Zendesk", "chat", ["zdassets.com", "zendesk.com/embeddable"]),
    ("Intercom", "chat", ["widget.intercom.io", "intercomcdn"]),
    ("Re:amaze", "chat", ["reamaze.com", "reamaze.js"]),
    ("LiveChat", "chat", ["livechatinc.com"]),
    ("Tawk.to", "chat", ["embed.tawk.to"]),
    ("Crisp", "chat", ["client.crisp.chat"]),
    ("Freshchat", "chat", ["wchat.freshchat.com", "freshchat"]),
    ("Shopify Inbox", "chat", ["shopify-chat", "inbox.shopify.com"]),
    ("WhatsApp", "chat", ["wa.me/", "api.whatsapp.com/send"]),
    # subscriptions (P06)
    ("ReCharge", "subscription", ["rechargecdn.com", "rechargepayments.com"]),
    ("Skio", "subscription", ["skio.com", "skio-plan"]),
    ("Seal Subscriptions", "subscription", ["sealsubscriptions"]),
    ("Appstle", "subscription", ["appstle"]),
    ("Bold Subscriptions", "subscription", ["bold-subscriptions", "boldsubs"]),
    # pop-ups / promotions (P08)
    ("Privy", "popup", ["privy.com", "widget.privy"]),
    ("Justuno", "popup", ["justuno.com"]),
    ("OptinMonster", "popup", ["optinmonster", "omappapi.com"]),
    ("Klaviyo forms", "popup", ["static.klaviyo.com/onsite", "klaviyo-form"]),
    # third-party currency converters (M03)
    ("MLVeda currency converter", "currency", ["mlveda"]),
    # Only vendor strings unlikely to appear in normal copy; generic words
    # ("doubly") matched marketing text on a real store and were dropped.
    ("BEST Currency Converter", "currency", ["best-currency-converter"]),
    ("Auto Currency Switcher", "currency", ["autocurrencyswitcher"]),
]

CARD_ICONS = {
    "visa",
    "master",
    "mastercard",
    "american-express",
    "amex",
    "discover",
    "diners-club",
    "diners",
    "jcb",
    "unionpay",
    "maestro",
    "apple-pay",
    "google-pay",
}

LEGAL_SUFFIX_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9&'.\- ]{1,60}?,?\s(?:Co\.,?\s?Ltd\.?|Company Limited|Limited|Ltd\.?|"
    r"LLC|L\.L\.C\.|Inc\.?|Corp\.?|Corporation|GmbH|Pty Ltd|B\.V\.|S\.A\.|SARL))(?![A-Za-z])"
)
CN_LEGAL_RE = re.compile(r"[一-鿿]{2,30}有限公司")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(
    r"(?:\+\d{1,3}[\s\-.]?)?\(?\d{2,4}\)?[\s\-.]\d{3,4}[\s\-.]\d{3,4}(?!\d)"
)
CURRENCY_CODES = (
    "USD",
    "CAD",
    "AUD",
    "NZD",
    "HKD",
    "SGD",
    "EUR",
    "GBP",
    "JPY",
    "CNY",
    "RMB",
)
DOLLAR_MARKETS = ("US", "CA", "AU", "NZ", "SG", "HK")
EU_UK_MARKETS = (
    "GB",
    "UK",
    "EU",
    "DE",
    "FR",
    "IT",
    "ES",
    "NL",
    "BE",
    "AT",
    "IE",
    "SE",
    "DK",
    "FI",
    "PL",
    "PT",
    "CZ",
    "GR",
    "RO",
    "HU",
)


def read_text(path):
    """File text or None (BOM stripped, undecodable bytes replaced)."""
    if not path or not os.path.isfile(path):
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


def html_to_text(src):
    src = re.sub(r"(?is)<(script|style|noscript|svg|template)\b.*?</\1>", " ", src)
    src = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</div>|</tr>", "\n", src)
    src = re.sub(r"(?s)<[^>]+>", " ", src)
    src = html.unescape(src)
    src = re.sub(r"[ \t\r\f\v ]+", " ", src)
    src = re.sub(r"\n\s*\n+", "\n\n", src)
    return src.strip()


def extract_balanced_div(src, marker):
    """Inner HTML of the div whose opening tag contains ``marker``."""
    idx = src.find(marker)
    if idx < 0:
        return None
    start = src.rfind("<div", 0, idx)
    tag_end = src.find(">", idx)
    if start < 0 or tag_end < 0:
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


def main_text(src):
    """Readable text of a policy / content page (Shopify policy body first)."""
    if not src:
        return ""
    if not re.search(r"(?i)<html|<body|<div|<p\b", src):
        return src.strip()  # already plain text / markdown (mode B)
    for marker in ("shopify-policy__body", "shopify-policy__container"):
        body = extract_balanced_div(src, marker)
        if body is not None:
            return html_to_text(body)
    m = re.search(r"(?is)<main\b.*?</main>", src)
    if m:
        return html_to_text(m.group(0))
    m = re.search(r"(?is)<article\b.*?</article>", src)
    if m:
        return html_to_text(m.group(0))
    return html_to_text(src)


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
    m = re.search(pattern, src or "")
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


def parse_json_lenient(text):
    """(obj, state): state ok | extracted | blocked | error | missing."""
    if text is None:
        return None, "missing"
    s = text.strip()
    if not s:
        return None, "error"
    try:
        return json.loads(s), "ok"
    except ValueError:
        pass
    starts = [i for i in (s.find("{"), s.find("[")) if i >= 0]
    if starts:
        try:
            obj, _ = json.JSONDecoder().raw_decode(s, min(starts))
            return obj, "extracted"
        except ValueError:
            pass
    if looks_blocked(s) or s.lower().startswith(("<!doctype", "<html")):
        return None, "blocked"
    return None, "error"


# ---------------------------------------------------------------------------
# links on the homepage (policy / contact / FAQ discovery for any platform)
# ---------------------------------------------------------------------------


def page_links(src, base_host):
    """[(href, text)] of same-site links (relative or same host)."""
    out = []
    for m in re.finditer(r"(?is)<a\b([^>]*)>(.*?)</a>", src or ""):
        attrs = parse_attrs(m.group(1))
        # hrefs inside JSON-escaped HTML look like \"https:\/\/x\/a\"
        href = (attrs.get("href") or "").replace("\\/", "/").strip("\\\"' ")
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        if href.startswith("//"):
            href = "https:" + href
        low = href.lower()
        if low.startswith("http"):
            host = re.sub(r"^https?://", "", low).split("/")[0].split(":")[0]
            if base_host and host.replace("www.", "") != base_host.replace("www.", ""):
                continue
        text = re.sub(r"\s+", " ", html_to_text(m.group(2)))[:80]
        out.append((href, text))
    return out


def discover_links(src, base_host):
    """{policy or page name: href} guessed from link text and paths."""
    found = {}
    hints = dict(POLICY_LINK_HINTS)
    hints.update(PAGE_LINK_HINTS)
    for href, text in page_links(src, base_host):
        hay = (href + " " + text).lower()
        for name, pat in hints.items():
            if name in found:
                continue
            if name == "contact-information":
                continue  # Shopify-only policy; contact pages use "contact"
            if re.search(pat, hay):
                if (
                    name == "terms-of-service"
                    and "condition" in hay
                    and "return" in hay
                ):
                    continue
                found[name] = href
    return found


# ---------------------------------------------------------------------------
# storefront signals
# ---------------------------------------------------------------------------


def shopify_markers(home):
    low = (home or "").lower()
    out = []
    for marker in (
        "cdn.shopify.com",
        "shopify.theme",
        "myshopify.com",
        "shopify-section",
    ):
        if marker in low:
            out.append(marker)
    return out


def identity_status(markers, endpoints):
    """confirmed | probable | not_shopify | unconfirmed (teardown semantics)."""
    if len(markers) >= 2:
        return "confirmed"
    if len(markers) == 1:
        return "probable"
    home = endpoints.get("homepage", {})
    if home.get("result") == "ok":
        core = [
            endpoints.get(k, {}).get("result") for k in ("meta_json", "products_json")
        ]
        if all(r in (None, "ok", "not_found", "not_checked") for r in core):
            return "not_shopify"
    return "unconfirmed"


def match_signatures(sources, table):
    """[{name, pattern, source, confidence}] for (name, patterns) tables."""
    seen = set()
    out = []
    for fname, src in sources:
        low = (src or "").lower()
        for entry in table:
            name, pats = entry[0], entry[-1]
            if name in seen:
                continue
            for pat in pats:
                if pat in low:
                    seen.add(name)
                    hit = {
                        "name": name,
                        "pattern": pat,
                        "source": "raw/" + fname,
                        "confidence": "possible",
                    }
                    if len(entry) == 3:
                        hit["kind"] = entry[1]
                    out.append(hit)
                    break
    return out


def payment_icons(sources):
    icons = []
    for _fname, src in sources:
        low = (src or "").lower()
        for m in re.finditer(
            r'(?:aria-labelledby|id)\s*=\s*["\']pi-([a-z0-9_\-]+)["\']'
            r"|payment-icon--([a-z0-9_\-]+)|icon--payment-([a-z0-9_\-]+)",
            low,
        ):
            name = (m.group(1) or m.group(2) or m.group(3)).replace("_", "-")
            if name and name not in icons:
                icons.append(name)
        for tag in re.findall(
            r"(?is)<(?:svg|img|li|span|div)\b[^>]*payment[^>]*>", src or ""
        ):
            attrs = parse_attrs(tag)
            if "payment" not in attrs.get("class", "").lower():
                continue
            label = attrs.get("aria-label") or attrs.get("title") or attrs.get("alt")
            if label and len(label) <= 40:
                name = re.sub(r"[\s_]+", "-", label.strip().lower())
                if name not in icons:
                    icons.append(name)
    return icons


def hreflang_links(src):
    out = []
    for tag in re.findall(r"(?is)<link\b[^>]*>", src or ""):
        attrs = parse_attrs(tag)
        if attrs.get("rel", "").lower() == "alternate" and attrs.get("hreflang"):
            pair = [attrs["hreflang"].lower(), attrs.get("href", "")]
            if pair not in out:
                out.append(pair)
    return out


def has_localization(src):
    low = (src or "").lower()
    return bool(
        re.search(
            r'id=["\']localization_form|action=["\'][^"\']*/localization["\']', low
        )
    )


def storefront_market(home):
    """Visitor market and currency the storefront rendered for this fetch."""
    out = {"country": None, "currency_active": None, "currency_rate": None}
    if not home:
        return out
    m = re.search(r'Shopify\.country\s*=\s*"([A-Z]{2})"', home)
    if m:
        out["country"] = m.group(1)
    cur = js_object_after(home, r"Shopify\.currency\s*=\s*\{")
    if cur:
        out["currency_active"] = cur.get("active")
        out["currency_rate"] = cur.get("rate")
    return out


def shop_name(meta, home):
    if isinstance(meta, dict) and meta.get("name"):
        return str(meta["name"]).strip()
    for pat in (
        r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:site_name',
    ):
        m = re.search(pat, home or "", re.I)
        if m:
            return html.unescape(m.group(1)).strip()
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", home or "")
    if m:
        title = html.unescape(m.group(1)).strip()
        return re.split(r"\s[|\-–—]\s", title)[-1].strip() or None
    return None


def product_stats(products):
    """Description length and images per product (T04)."""
    lens, imgs = [], []
    for p in products:
        if not isinstance(p, dict):
            continue
        lens.append(len(html_to_text(p.get("body_html") or "")))
        imgs.append(len(p.get("images") or []))
    if not lens:
        return {"count": 0, "desc_chars_median": None, "images_median": None}
    return {
        "count": len(lens),
        "desc_chars_median": median(lens),
        "images_median": median(imgs),
        "desc_under_100": sum(1 for x in lens if x < 100),
    }


def median(vals):
    s = sorted(vals)
    if not s:
        return None
    mid = len(s) // 2
    if len(s) % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


# ---------------------------------------------------------------------------
# policy text facts
# ---------------------------------------------------------------------------

DAYS_RE = re.compile(
    r"(?i)\b(\d{1,3})\s*(?:-|\s)?\s*(?:calendar\s+|business\s+|working\s+)?days?\b"
)
RETURN_CTX_RE = re.compile(r"(?i)return|refund|exchange|money[- ]back")


def return_windows(text, window=90):
    """Day counts mentioned near return / refund words, most frequent first."""
    counts = {}
    for m in DAYS_RE.finditer(text or ""):
        n = int(m.group(1))
        if n <= 0 or n > 365:
            continue
        ctx = text[max(0, m.start() - window) : m.end() + window]
        if not RETURN_CTX_RE.search(ctx):
            continue
        # skip processing-time phrases ("refund processed within 5-10 days")
        near = text[max(0, m.start() - 40) : m.end() + 10].lower()
        if re.search(
            r"process|business day|working day|bank|credit card|reflect|post", near
        ):
            continue
        counts[n] = counts.get(n, 0) + 1
    return [n for n, _c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def page_return_claims(text):
    """Return windows claimed in short marketing phrases ("30-day returns")."""
    out = []
    for m in re.finditer(
        r"(?i)\b(\d{1,3})[- ]?days?\s+(?:free\s+)?(?:returns?|money[- ]back|refunds?|exchanges?)"
        r"|(?:returns?|refunds?)\s+(?:within|in)\s+(\d{1,3})\s*days?",
        text or "",
    ):
        n = int(m.group(1) or m.group(2))
        if 0 < n <= 365 and n not in out:
            out.append(n)
    return out


def snippet(text, pattern, width=70):
    m = re.search(pattern, text or "", re.I)
    if not m:
        return None
    a = max(0, m.start() - width // 2)
    b = min(len(text), m.end() + width // 2)
    # widen to word boundaries so quotes do not start mid-word
    while a > 0 and a > m.start() - width and text[a - 1].isalnum():
        a -= 1
    while b < len(text) and b < m.end() + width and text[b].isalnum():
        b += 1
    return re.sub(r"\s+", " ", text[a:b]).strip()


def contains(text, pattern):
    return bool(re.search(pattern, text or "", re.I))


LEGAL_SUFFIX_ONLY = re.compile(
    r"(?i)^(?:co\.?,?\s?ltd\.?|company limited|limited|ltd\.?|llc|l\.l\.c\.|inc\.?|corp\.?"
    r"|corporation|gmbh|pty ltd|b\.v\.|s\.a\.|sarl)$"
)
NAME_WORD_RE = re.compile(r"^(?:[A-Z0-9][A-Za-z0-9&'.\-]*|&|and|of)$")


def legal_names(text):
    """Company names with a legal suffix, e.g. "AB Dnam, LLC".

    Only the trailing run of capitalised words before the suffix is kept
    (at most five), so "mail addressed to AB Dnam, LLC" gives "AB Dnam, LLC".
    """
    names = []
    for m in LEGAL_SUFFIX_RE.finditer(text or ""):
        full = re.sub(r"\s+", " ", m.group(1)).strip(" .")
        sm = re.search(
            r",?\s(Co\.,?\s?Ltd\.?|Company Limited|Limited|Ltd\.?|LLC|L\.L\.C\.|Inc\.?|Corp\.?"
            r"|Corporation|GmbH|Pty Ltd|B\.V\.|S\.A\.|SARL)$",
            full,
        )
        if not sm:
            continue
        words = full[: sm.start()].split()
        keep = []
        for w in reversed(words):
            if not NAME_WORD_RE.match(w) or w.endswith((".", ",")) or len(keep) >= 5:
                break
            keep.insert(0, w)
        while keep and keep[0] in ("&", "and", "of"):
            keep = keep[1:]
        while keep and keep[0].lower() in (
            "the",
            "this",
            "our",
            "by",
            "copyright",
            "a",
            "an",
        ):
            keep = keep[1:]
        if not keep:
            continue
        name = " ".join(keep) + full[sm.start() :]
        if name not in names and not LEGAL_SUFFIX_ONLY.match(name.strip(" ,")):
            names.append(name)
    for m in CN_LEGAL_RE.finditer(text or ""):
        if m.group(0) not in names:
            names.append(m.group(0))
    return names


def looks_js_rendered(src, text):
    """True when a page carries almost no readable text but a big HTML shell
    (client-side rendered or a script redirect): content needs a browser."""
    if not src or "shopify-policy__body" in src:
        return False
    return (
        len(text.strip()) < 150
        and len(src) > 20000
        or bool(re.match(r"(?i)^\s*redirecting", text or ""))
    )


def norm_legal(name):
    n = re.sub(
        r"(?i)[.,]|co\b|company|limited|ltd|llc|l l c|inc|corp(?:oration)?|有限公司",
        " ",
        name,
    )
    return " ".join(n.lower().split())
