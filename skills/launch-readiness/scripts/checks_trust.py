"""Unfinished-looking items (TRU) and indexing basics (SEO).
Python 3.8+, standard library only.
"""

import datetime
import re

import crawl
import pages
import products

SAMPLE_TEXT_RE = re.compile(
    r"lorem ipsum|your content here|talk about your brand|"
    r"use this (text|section) to|share information about your brand|"
    r"pair text with an image|add a description here|write something|"
    r"tell (your|customers) (brand'?s )?story|this is (a|your) (sample|demo) ",
    re.I,
)
PLACEHOLDER_IMG_RE = re.compile(
    r'<svg[^>]*class="[^"]*placeholder-svg|<img[^>]+src="[^"]*(placeholder|no-image|noimage)',
    re.I,
)
DEFAULT_NAME_RE = re.compile(r"^\s*(my store|my shop|store name|shop name)\b", re.I)
COPY_RE = re.compile(r"(?:©|&copy;|copyright)\s*(\d{4})(?:\s*[-–]\s*(\d{4}))?", re.I)
SOCIAL_HOME_RE = re.compile(
    r"^https?://(www\.)?(facebook|instagram|twitter|x|tiktok|youtube|pinterest)\.com/?"
    r"(shopify/?)?$",
    re.I,
)


def run(ctx, today=None):
    s = ctx.site
    today = today or datetime.date.today()
    items = [("home", s.home_url, pages.page_text(s.home))] if s.home else []
    items += [("pdp", p["url"], p.get("text") or "") for p in s.pdps]
    items += [
        (r, p["url"], p["text"])
        for r, ps in s.info.items()
        for p in ps
        if p.get("text")
    ]
    hit = None
    for _l, url, text in items:
        m = SAMPLE_TEXT_RE.search(text or "")
        if m:
            hit = (url, pages.snippet(text, m.start(), m.end(), 60))
            break
    if hit:
        ctx.add("TRU-01", "fail", where=hit[0], evidence=[hit[1]])
    else:
        ctx.add("TRU-01", "pass" if items else "unknown")
    if s.online():
        main = pages.main_html(s.home)
        m = PLACEHOLDER_IMG_RE.search(main)
        ctx.add(
            "TRU-02",
            "fail" if m else "pass",
            where=s.home_url,
            evidence=[m.group(0)[:120]] if m else None,
        )
        ctx.add(
            "TRU-04", "pass" if pages.has_favicon(s.home) else "fail", where=s.home_url
        )
    else:
        ctx.add("TRU-02", "unknown")
        ctx.add("TRU-04", "unknown")
    tests = products.test_products(s.products)
    if tests:
        ctx.add(
            "TRU-03",
            "fail",
            evidence=["%s (%s)" % (t[1], t[2]) for t in tests[:3]],
            data={"count": len(tests)},
        )
    else:
        ctx.add("TRU-03", "pass" if s.products else "unknown")
    if s.password:
        ctx.add("TRU-05", "manual", where=s.home_url, note="password_page")
    elif s.online():
        ctx.add("TRU-05", "pass")
    else:
        ctx.add("TRU-05", "unknown")
    tru06(ctx, today)
    seo(ctx)


def tru06(ctx, today):
    s = ctx.site
    if not s.online():
        ctx.add("TRU-06", "unknown")
        return
    bad = []
    title = pages.title(s.home) or ""
    if DEFAULT_NAME_RE.search(title) or DEFAULT_NAME_RE.search(
        str(s.meta.get("name") or "")
    ):
        bad.append("title: " + title[:60])
    footer_text = pages.html_to_text(s.footer)
    m = None
    for m in COPY_RE.finditer(footer_text):
        pass
    if m:
        year = int(m.group(2) or m.group(1))
        if year < today.year - 1:
            bad.append("copyright " + str(year))
    for url, _t, _a in s.footer_links():
        if SOCIAL_HOME_RE.match(url or ""):
            bad.append("social: " + url)
            break
    ctx.add("TRU-06", "fail" if bad else "pass", where=s.home_url, evidence=bad[:3])


def seo(ctx):
    s = ctx.site
    if not s.online():
        for rid in ("SEO-01", "SEO-02", "SEO-03"):
            ctx.add(rid, "unknown", note="needs_live_site")
        return
    rob = _result(s, "robots")
    if s.robots is not None:
        ctx.add(
            "SEO-01",
            "fail" if crawl.blocks_all(s.robots) else "pass",
            evidence=["Disallow: /"] if crawl.blocks_all(s.robots) else None,
        )
    elif rob == "not_found":
        ctx.add("SEO-01", "pass", note="no_robots_file")
    else:
        ctx.add("SEO-01", "unknown", note="robots_" + str(rob))
    noindex = pages.meta_noindex(s.home)
    ctx.add(
        "SEO-02",
        "fail" if noindex else "pass",
        where=s.home_url,
        evidence=[noindex] if noindex else None,
    )
    sm = _result(s, "sitemap")
    if s.sitemap is not None:
        ok = re.search(r"product", s.sitemap, re.I)
        ctx.add("SEO-03", "pass" if ok else "fail", note=None if ok else "no_products")
    elif sm in ("not_found", "error"):
        ctx.add("SEO-03", "fail", note="sitemap_" + sm)
    else:
        ctx.add("SEO-03", "unknown", note="sitemap_" + str(sm))


def _result(s, role):
    for row in s.index:
        if row.get("role") == role:
            return row.get("result")
    return None
