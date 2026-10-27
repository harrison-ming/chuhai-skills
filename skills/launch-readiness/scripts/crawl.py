"""Bounded crawler for non-Shopify stores, plus a tiny robots.txt reader.

Limits (spec section 7): same host only, at most ``max_pages`` pages
(default 40), depth 2, only links from the header/footer/navigation or links
whose text or path looks like a policy / contact / FAQ page, robots.txt
respected, no JavaScript, no forms.
Python 3.8+, standard library only.
"""

import re
from urllib.parse import urlsplit

import pages

MAX_PAGES = 40
MAX_DEPTH = 2
# Link text / path keywords per page role (English, German, French, Spanish).
ROLE_WORDS = [
    ("refund", r"refund|return|retour|r[uü]ckgabe|widerruf|devoluci|rembourse"),
    ("shipping", r"shipping|delivery|versand|lieferung|livraison|env[ií]o"),
    ("privacy", r"privacy|datenschutz|confidentialit|privacidad"),
    ("terms", r"terms|conditions|\bagb\b|cgv|t[eé]rminos|conditions-generales"),
    (
        "legal",
        r"impressum|imprint|legal[- ]notice|mentions[- ]l[eé]gales|aviso[- ]legal",
    ),
    ("contact", r"contact|kontakt|contacto"),
    ("faq", r"\bfaq|help|hilfe|aide|ayuda|questions"),
    ("about", r"about|[uü]ber[- ]uns|qui[- ]sommes|nosotros"),
]
ROLE_RES = [(role, re.compile(pat, re.I)) for role, pat in ROLE_WORDS]
SKIP_EXT = re.compile(
    r"\.(jpe?g|png|gif|webp|svg|pdf|zip|mp4|css|js|xml|json|ico)(\?|$)", re.I
)
SKIP_PATH = re.compile(
    r"/(cart|checkout|account|login|wp-admin|my-account|search)\b", re.I
)


def role_of(url, text):
    """Page role guessed from link text first, then from the URL path."""
    path = urlsplit(url).path
    for source in (text or "", path):
        for role, rx in ROLE_RES:
            if rx.search(source):
                return role
    return None


def parse_robots(text):
    """Disallow rules for ``User-agent: *`` -> list of path prefixes."""
    rules, active, saw_rule = [], False, False
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, val = [x.strip() for x in line.split(":", 1)]
        key = key.lower()
        if key == "user-agent":
            if saw_rule:
                active, saw_rule = False, False
            if val == "*":
                active = True
        elif key in ("disallow", "allow"):
            saw_rule = True
            if active and key == "disallow" and val:
                rules.append(val)
    return rules


def blocks_all(text):
    return "/" in parse_robots(text)


def allowed(url, disallow):
    path = urlsplit(url).path or "/"
    return not any(path.startswith(d) for d in disallow if d != "/") and (
        "/" not in disallow
    )


def candidates(src, base_url, host):
    """[(url, text, from_nav)] same-host links worth following, deduped."""
    nav = pages.header_html(src) + "\n" + pages.footer_html(src)
    nav_urls = set(u for u, _t, _a in pages.links(nav, base_url))
    out, seen = [], set()
    for url, text, _attrs in pages.links(src, base_url):
        url = pages.strip_fragment(url)
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        if not pages.same_host(url, host) or SKIP_EXT.search(url):
            continue
        if SKIP_PATH.search(urlsplit(url).path):
            continue
        from_nav = url in nav_urls
        if not from_nav and role_of(url, text) is None:
            continue
        seen.add(url)
        out.append((url, text, from_nav))
    return out


def crawl(start_url, start_html, host, fetch_page, disallow=(), max_pages=MAX_PAGES):
    """Breadth-first crawl from an already fetched homepage.

    ``fetch_page(url) -> (html or None, final_url, result)`` performs one
    polite request. Returns a list of {url, final_url, role, depth, text,
    result, html}. The homepage itself is not refetched or returned.
    """
    queue = [(u, t, 1) for u, t, _n in candidates(start_html, start_url, host)]
    done = {pages.strip_fragment(start_url)}
    out = []
    # Policy-looking links first, then the rest of the navigation.
    queue.sort(key=lambda x: role_of(x[0], x[1]) is None)
    while queue and len(out) < max_pages:
        url, text, depth = queue.pop(0)
        if url in done:
            continue
        done.add(url)
        if not allowed(url, disallow):
            out.append(_row(url, url, role_of(url, text), depth, text, "robots", None))
            continue
        body, final_url, result = fetch_page(url)
        out.append(_row(url, final_url, role_of(url, text), depth, text, result, body))
        if body and depth < MAX_DEPTH:
            for u, t, _n in candidates(body, final_url or url, host):
                if u not in done and role_of(u, t):
                    queue.append((u, t, depth + 1))
    return out


def _row(url, final_url, role, depth, text, result, body):
    return {
        "url": url,
        "final_url": final_url,
        "role": role,
        "depth": depth,
        "text": text,
        "result": result,
        "html": body,
    }
