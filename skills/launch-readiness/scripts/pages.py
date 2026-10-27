"""HTML and text helpers shared by audit_collect.py, paste_ingest.py and audit.py.

Regex based on purpose (no third-party parser, no JavaScript). Every helper
accepts None and returns an empty value instead of raising.
Python 3.8+, standard library only.
"""

import html
import os
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

BLOCK_MARKERS = [
    "just a moment...",
    "cf-chl",
    "captcha",
    "access denied",
    "your connection needs to be verified",
    "attention required",
    "verify you are human",
]
SHOPIFY_MARKERS = [
    "cdn.shopify.com",
    "shopify.theme",
    "shopify-section",
    ".myshopify.com",
    "window.shopify",
    "shopify-digital-wallet",
]
# Shopify policy handles (S10 lists these seven).
POLICY_NAMES = [
    "refund-policy",
    "shipping-policy",
    "privacy-policy",
    "terms-of-service",
    "contact-information",
    "legal-notice",
    "subscription-policy",
]
POLICY_MIN_CHARS = 200
TAG_RE = re.compile(r"(?s)<[^>]+>")
A_RE = re.compile(r"(?is)<a\b([^>]*)>(.*?)</a\s*>")
ATTR_RE = re.compile(r'([a-zA-Z_:\-]+)\s*=\s*("([^"]*)"|\'([^\']*)\'|([^\s>]+))')
TITLE_RE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
HAN_RE = re.compile("[一-鿿]{2,}")
LANG_NAMES_RE = re.compile("简体中文|繁體中文|中文|日本語|한국어|正體中文")
SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|(?<=[\u3002\uff01\uff1f])\s*|\n+")


def read_text(path):
    """File text (UTF-8, BOM stripped, errors replaced) or None."""
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


def parse_attrs(tag):
    attrs = {}
    for m in ATTR_RE.finditer(tag or ""):
        val = m.group(3) if m.group(3) is not None else m.group(4)
        if val is None:
            val = m.group(5)
        attrs[m.group(1).lower()] = html.unescape(val)
    return attrs


def is_html(text):
    head = (text or "")[:2000].lower()
    return "<html" in head or "<!doctype html" in head or "<body" in head


def html_to_text(src):
    """Visible-ish text of an HTML fragment; plain text passes through."""
    if not src:
        return ""
    if "<" not in src:
        return re.sub(r"[ \t\r\f\v]+", " ", src).strip()
    src = re.sub(r"(?is)<(script|style|noscript|svg|template)\b.*?</\1\s*>", " ", src)
    src = re.sub(r"(?is)<!--.*?-->", " ", src)
    src = re.sub(
        r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</div>|</tr>|</td>|</dt>|</dd>", "\n", src
    )
    src = TAG_RE.sub(" ", src)
    src = html.unescape(src)
    src = re.sub(r"[ \t\r\f\v ]+", " ", src)
    src = re.sub(r" *\n[ \n]*", "\n", src)
    return src.strip()


def tag_blocks(src, tag):
    """Inner HTML of each <tag ...>...</tag> (non-nested match)."""
    if not src:
        return []
    pat = re.compile(r"(?is)<%s\b[^>]*>(.*?)</%s\s*>" % (tag, tag))
    return [m.group(1) for m in pat.finditer(src)]


def remove_blocks(src, tags):
    for tag in tags:
        src = re.sub(r"(?is)<%s\b[^>]*>.*?</%s\s*>" % (tag, tag), " ", src or "")
    return src


def footer_html(src):
    """Footer markup: <footer> blocks plus Shopify footer section groups.

    Falls back to the last quarter of the document when nothing is marked.
    """
    if not src:
        return ""
    parts = tag_blocks(src, "footer")
    m = re.search(r'(?is)<[a-z]+[^>]*id="shopify-section[^"]*footer[^"]*"', src)
    if m:
        parts.append(src[m.start() : m.start() + 60000])
    if not parts:
        parts.append(src[int(len(src) * 0.75) :])
    return "\n".join(parts)


def header_html(src):
    if not src:
        return ""
    parts = tag_blocks(src, "header") + tag_blocks(src, "nav")
    m = re.search(
        r'(?is)<[a-z]+[^>]*id="shopify-section[^"]*(?:header|announcement)', src
    )
    if m:
        parts.append(src[m.start() : m.start() + 30000])
    return "\n".join(parts)


def announcement_text(src):
    """Announcement bar text (Shopify sections or common class names)."""
    out = []
    for m in re.finditer(
        r"(?is)<(div|section|aside|p)\b[^>]*(?:announcement|promo-bar|top-bar|topbar)[^>]*>",
        src or "",
    ):
        out.append(html_to_text(src[m.start() : m.start() + 3000])[:400])
    return "\n".join(out)


def main_html(src):
    """Main content: <main> when present, else body minus header/footer/nav."""
    if not src:
        return ""
    mains = tag_blocks(src, "main")
    if mains:
        return max(mains, key=len)
    body = tag_blocks(src, "body")
    body = body[0] if body else src
    return remove_blocks(body, ("header", "footer", "nav"))


def balanced_div(src, marker):
    """Inner HTML of the div whose opening tag contains ``marker``."""
    idx = (src or "").find(marker)
    if idx < 0:
        return None
    tag_end = src.find(">", idx)
    if tag_end < 0:
        return None
    depth, pos = 1, tag_end + 1
    tag_re = re.compile(r"(?i)<(/?)div\b")
    while depth > 0:
        m = tag_re.search(src, pos)
        if not m:
            return src[tag_end + 1 :]
        depth += -1 if m.group(1) else 1
        pos = m.end()
    return src[tag_end + 1 : m.start()]


def policy_text(src):
    """Readable text of a policy/info page (HTML or plain text)."""
    if not src:
        return ""
    if not is_html(src) and "<" not in src[:500]:
        return html_to_text(src)
    body = balanced_div(src, "shopify-policy__body")
    if body is None:
        body = balanced_div(src, "shopify-policy__container")
    if body is None:
        body = main_html(src)
    return html_to_text(body)


def page_text(src):
    return policy_text(src)


def full_text(src):
    """All visible text including header and footer."""
    if not src:
        return ""
    body = tag_blocks(src, "body")
    return html_to_text(body[0] if body else src)


def links(src, base_url=None):
    """[(absolute url, anchor text, attrs)] for every <a href>."""
    out = []
    for m in A_RE.finditer(src or ""):
        attrs = parse_attrs(m.group(1))
        href = (attrs.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "#")):
            continue
        text = (
            html_to_text(m.group(2))
            or attrs.get("aria-label")
            or attrs.get("title")
            or ""
        )
        url = urljoin(base_url, href) if base_url else href
        out.append((url, text.strip()[:120], attrs))
    return out


def same_host(url, host):
    h = (urlsplit(url).hostname or "").lower()
    host = (host or "").lower()
    return h == host or h == "www." + host or "www." + h == host


def strip_fragment(url):
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path or "/", p.query, ""))


def title(src):
    m = TITLE_RE.search(src or "")
    return html.unescape(m.group(1)).strip() if m else None


def html_lang(src):
    m = re.search(r"(?is)<html\b([^>]*)>", src or "")
    if not m:
        return None
    return (parse_attrs(m.group(1)).get("lang") or "").strip() or None


def link_tags(src):
    return [parse_attrs(t) for t in re.findall(r"(?is)<link\b[^>]*>", src or "")]


def meta_tags(src):
    return [parse_attrs(t) for t in re.findall(r"(?is)<meta\b[^>]*>", src or "")]


def hreflang_links(src):
    """[[code, href]] from <link rel=alternate hreflang=...> in document order."""
    out = []
    for a in link_tags(src):
        rels = (a.get("rel") or "").lower().split()
        if "alternate" in rels and a.get("hreflang"):
            pair = [a["hreflang"].strip(), (a.get("href") or "").strip()]
            if pair not in out:
                out.append(pair)
    return out


def canonical(src):
    for a in link_tags(src):
        if "canonical" in (a.get("rel") or "").lower().split():
            return (a.get("href") or "").strip() or None
    return None


def has_favicon(src):
    for a in link_tags(src):
        rels = (a.get("rel") or "").lower().split()
        if "icon" in rels or "apple-touch-icon" in rels:
            return True
    return False


def meta_noindex(src):
    for a in meta_tags(src):
        name = (a.get("name") or "").lower()
        if (
            name in ("robots", "googlebot")
            and "noindex" in (a.get("content") or "").lower()
        ):
            return a.get("content")
    return None


def looks_blocked(text):
    head = (text or "")[:20000].lower()
    return any(m in head for m in BLOCK_MARKERS)


def is_shopify(text):
    low = (text or "").lower()
    return sum(1 for m in SHOPIFY_MARKERS if m in low) >= 1


def is_password_page(text, final_url=None):
    """Shopify storefront password page (private mode)."""
    path = urlsplit(final_url or "").path.rstrip("/")
    if path.endswith("/password"):
        return True
    low = (text or "")[:200000].lower()
    if re.search(r'<form[^>]+action=["\'][^"\']*/password["\']', low):
        return True
    return "template-password" in low or 'id="password-page' in low


def js_rendered(src):
    """True when the page carries almost no text without JavaScript."""
    if not src:
        return False
    words = len(full_text(src).split())
    scripts = len(re.findall(r"(?i)<script\b", src))
    app_root = re.search(r'(?i)id=["\'](app|root|__next|__nuxt)["\']', src)
    return words < 25 and (scripts >= 3 or (scripts >= 1 and bool(app_root)))


def sentences(text):
    return [s.strip() for s in SENT_SPLIT_RE.split(text or "") if s and s.strip()]


def han_runs(text):
    """Runs of 2+ Han characters, minus language-selector names."""
    text = LANG_NAMES_RE.sub(" ", text or "")
    return HAN_RE.findall(text)


def snippet(text, start, end, pad=80):
    a = max(0, start - pad)
    b = min(len(text), end + pad)
    out = re.sub(r"\s+", " ", text[a:b]).strip()
    return ("..." if a > 0 else "") + out + ("..." if b < len(text) else "")


def emails(text):
    found = re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text or "")
    out = []
    for e in found:
        e = e.strip(".").lower()
        if e.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
            continue
        if e not in out:
            out.append(e)
    return out


PHONE_RE = re.compile(
    r"(?:(?:\+|00)\d{1,3}[\s.\-]?)?(?:\(\d{2,4}\)[\s.\-]?)?\d{2,4}[\s.\-]\d{3,4}[\s.\-]?\d{3,4}"
)


def phones(text):
    out = []
    for m in PHONE_RE.finditer(text or ""):
        p = m.group(0).strip()
        digits = re.sub(r"\D", "", p)
        if 8 <= len(digits) <= 15 and p not in out:
            # skip date-like and price-like matches
            if re.match(r"^\d{4}[\-.]\d{2}[\-.]\d{2}$", p):
                continue
            out.append(p)
    return out
