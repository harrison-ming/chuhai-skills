#!/usr/bin/env python3
"""Mode A (online): fetch the public pages a launch check needs into raw/.

CLI:
    python audit_collect.py <url_or_domain> [--markets "US,UK,DE"] [--no-ads]
        [--lang zh|en] [--out-root DIR | --out DIR] [--delay 0.6]
        [--timeout 20] [--no-audit]

Order: homepage -> (password page or block: stop) -> http->https probe ->
Shopify: /policies/*, /meta.json, /products.json, 3 product pages;
other platforms: bounded crawl (40 pages, depth 2, same host, robots.txt) ->
footer/header info pages -> up to 3 market versions -> robots.txt, sitemap.
Writes raw/ files, raw/pages.json (index) and raw/_status.json, then runs
audit.py unless --no-audit. Read-only: no login, no forms, no checkout.
Python 3.8+, standard library only. TLS verification is always on.
"""

import argparse
import json
import os
import re
import sys
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crawl  # noqa: E402
import pages  # noqa: E402
import products  # noqa: E402
import skill_config as cfg  # noqa: E402
from _shared import fetch, i18n, output  # noqa: E402

MAX_MARKETS = 3


def classify(status, body):
    if status is None:
        return "error"
    if status == 404:
        return "not_found"
    if status in (401, 403, 429, 430):
        return "blocked"
    if status >= 400 or not (body or b"").strip():
        return "error"
    text = body.decode("utf-8", errors="replace")
    if (
        len(text) < 40000
        and pages.looks_blocked(text)
        and "shopify" not in text.lower()
    ):
        return "blocked"
    return "ok"


class Collector(object):
    def __init__(self, fetcher, raw):
        self.f = fetcher
        self.raw = raw
        self.requests = []
        self.index = []

    def get(self, key, url, fname, role, accept=fetch.ACCEPT_HTML, **extra):
        r = self.f.get(url, accept)
        result = classify(r["status"], r["body"])
        if (
            result == "ok"
            and role != "home"
            and pages.is_password_page(
                r["body"].decode("utf-8", "replace")[:200000], r["final_url"]
            )
        ):
            result = "password"
        self.requests.append(fetch.status_entry(key, url, r, result, fname))
        text = None
        if result == "ok" and fname:
            path = os.path.join(self.raw, fname)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(r["body"])
            text = r["body"].decode("utf-8", errors="replace")
        row = dict(
            key=key,
            file=fname if text is not None else None,
            url=url,
            final_url=r["final_url"],
            role=role,
            result=result,
            http_status=r["status"],
        )
        row.update(extra)
        self.index.append(row)
        return text, r, result


def slug(text, n=60):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text or "").strip("-")[:n] or "page"


def collect(target, raw, fetcher, markets_text=None, ads=True):
    scheme, host = fetch.normalize_input(target)
    base = "https://%s" % host if scheme == "https" else "%s://%s" % (scheme, host)
    os.makedirs(raw, exist_ok=True)
    c = Collector(fetcher, raw)
    started = fetch.utc_now()
    status = {
        "schema": "%s/status/1" % cfg.NAME,
        "tool_version": cfg.TOOL_VERSION,
        "mode": "online",
        "input": target,
        "domain": fetch.strip_www(host),
        "base_url": base,
        "user_markets": markets_text or None,
        "ads": ads,
        "started_at_utc": started,
    }
    home, r, result = c.get("homepage", base + "/", "homepage.html", "home")
    final = r.get("final_url") or base + "/"
    probe = (r.get("body") or b"").decode("utf-8", errors="replace")[:300000]
    status["password_protected"] = bool(probe) and pages.is_password_page(probe, final)
    if status["password_protected"] or result != "ok":
        status["platform"] = "shopify" if pages.is_shopify(probe) else "unknown"
        return finish(raw, status, c)
    netloc = urlsplit(final).netloc or host
    host = urlsplit(final).hostname or host
    base = "%s://%s" % (urlsplit(final).scheme or "https", netloc)
    status["base_url"] = base
    shopify = pages.is_shopify(home)
    status["platform"] = "shopify" if shopify else "other"
    # http -> https probe (GMC-02)
    _t, hr, _res = c.get("http_probe", "http://%s/" % netloc, None, "probe")
    status["https_redirect"] = (
        (hr.get("final_url") or "").startswith("https://") if hr.get("status") else None
    )
    robots, _r, _res = c.get(
        "robots", base + "/robots.txt", "robots.txt", "robots", accept="text/plain,*/*"
    )
    disallow = crawl.parse_robots(robots) if robots else []
    if shopify:
        collect_shopify(c, base)
    else:
        collect_generic(c, base, host, home, disallow)
    collect_info_pages(c, base, host, home, shopify, disallow)
    collect_markets(c, base, home, shopify)
    c.get(
        "sitemap",
        base + "/sitemap.xml",
        "sitemap.xml",
        "sitemap",
        accept="application/xml,text/xml,*/*",
    )
    return finish(raw, status, c)


STUB_CHARS = 400


def follow_stub(c, base, name, text):
    """A short policy that only links to a /pages/ page: read that page too."""
    if not text or len(pages.policy_text(text)) >= STUB_CHARS:
        return
    host = urlsplit(base).hostname
    body = pages.balanced_div(text, "shopify-policy__body") or text
    urls = [u for u, _t, _a in pages.links(body, base + "/")]
    urls += re.findall(r"https?://[^\s\"'<>]+", pages.policy_text(text))
    for url in urls:
        url = pages.strip_fragment(url)
        if pages.same_host(url, host) and "/policies/" not in url and "/pages/" in url:
            c.get(
                "policy_page:" + name,
                url,
                "policies/%s.page.html" % name,
                "policy",
                policy=name,
                followed=True,
            )
            return


def collect_shopify(c, base):
    for name in pages.POLICY_NAMES:
        text, _r, res = c.get(
            "policy:" + name,
            "%s/policies/%s" % (base, name),
            "policies/%s.html" % name,
            "policy",
            policy=name,
        )
        if res == "ok":
            follow_stub(c, base, name, text)
    c.get("meta", base + "/meta.json", "meta.json", "meta", accept=fetch.ACCEPT_JSON)
    text, _r, res = c.get(
        "products",
        base + "/products.json?limit=250",
        "products.json",
        "products",
        accept=fetch.ACCEPT_JSON,
    )
    items = []
    if res == "ok":
        try:
            items = json.loads(text).get("products") or []
        except (ValueError, AttributeError):
            items = []
    for role, handle, price in products.samples(items):
        c.get(
            "pdp:" + handle,
            "%s/products/%s" % (base, handle),
            "pdp/%s.html" % slug(handle),
            "pdp",
            sample=role,
            price=price,
            handle=handle,
        )


PDP_PATH_RE = re.compile(r"/(products?|shop|item|p)/[^/?#]+/?$", re.I)


def collect_generic(c, base, host, home, disallow):
    budget = [crawl.MAX_PAGES]

    def fetch_page(url):
        n = len([x for x in c.index if x.get("crawl")])
        text, r, res = c.get(
            "crawl:%d" % n,
            url,
            "pages/%02d-%s.html" % (n, slug(urlsplit(url).path)),
            "page",
            crawl=True,
        )
        return text, r.get("final_url"), res

    rows = crawl.crawl(base + "/", home, host, fetch_page, disallow, budget[0] - 3)
    for row in c.index:
        if row.get("crawl"):
            match = [x for x in rows if x["url"] == row["url"]]
            row["page_role"] = match[0]["role"] if match else None
    # Up to three product-looking links from the homepage (no JavaScript).
    seen = 0
    for url, _t, _a in pages.links(home, base + "/"):
        if seen >= 3 or not pages.same_host(url, host):
            continue
        if PDP_PATH_RE.search(urlsplit(url).path) and crawl.allowed(url, disallow):
            if any(x["url"] == url for x in c.index):
                continue
            seen += 1
            c.get(
                "pdp:%d" % seen,
                url,
                "pdp/%d-%s.html" % (seen, slug(urlsplit(url).path)),
                "pdp",
                sample="link",
            )


def collect_info_pages(c, base, host, home, shopify, disallow):
    """Contact / FAQ / about / legal pages linked from the header or footer."""
    if not shopify:
        return  # the generic crawl already covered navigation links
    nav = pages.header_html(home) + "\n" + pages.footer_html(home)
    done = set(x["url"] for x in c.index)
    n = 0
    for url, text, _a in pages.links(nav, base + "/"):
        url = pages.strip_fragment(url)
        role = crawl.role_of(url, text)
        if role is None or url in done or not pages.same_host(url, host):
            continue
        if "/policies/" in url or not crawl.allowed(url, disallow) or n >= 10:
            continue
        done.add(url)
        n += 1
        c.get(
            "page:%d" % n,
            url,
            "pages/%02d-%s.html" % (n, slug(urlsplit(url).path)),
            "page",
            page_role=role,
            link_text=text[:80],
        )


def collect_markets(c, base, home, shopify):
    """Up to 3 market versions from the homepage annotations."""
    pairs = pages.hreflang_links(home)
    own = pages.html_lang(home) or ""
    picked = []
    for code, href in pairs:
        low = code.lower()
        if low == "x-default" or low == own.lower() or not href.startswith("http"):
            continue
        if pages.strip_fragment(href).rstrip("/") == base.rstrip("/"):
            continue
        if href not in [h for _c, h in picked]:
            picked.append((code, href))
    pdp = [x for x in c.index if x["role"] == "pdp" and x.get("handle")]
    for i, (code, href) in enumerate(picked[:MAX_MARKETS]):
        tag = slug(code, 20)
        c.get(
            "market:%s" % code,
            href,
            "markets/%s/homepage.html" % tag,
            "market_home",
            code=code,
        )
        if not shopify:
            continue
        prefix = href.rstrip("/")
        if pdp:
            h = pdp[0]["handle"]
            c.get(
                "market_js:%s" % code,
                "%s/products/%s.js" % (prefix, h),
                "markets/%s/product.js" % tag,
                "market_product",
                accept=fetch.ACCEPT_JSON,
                code=code,
                handle=h,
            )
        c.get(
            "market_policy:%s" % code,
            prefix + "/policies/refund-policy",
            "markets/%s/refund-policy.html" % tag,
            "market_policy",
            code=code,
        )


def finish(raw, status, c):
    status["finished_at_utc"] = fetch.utc_now()
    status["requests"] = c.requests
    with open(os.path.join(raw, "_status.json"), "w", encoding="utf-8") as fh:
        json.dump(status, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    with open(os.path.join(raw, "pages.json"), "w", encoding="utf-8") as fh:
        json.dump(c.index, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return status


MSG = {
    "zh": {
        "password": "店铺开着密码页, 已停止抓取 (不需要也不要提供店铺访问密码). 请走粘贴模式 (paste_ingest.py) 或上线后重跑.",
        "failed": "首页打不开或被拦截, 已停止抓取. 报告只能写 failed.",
        "run": "运行目录: %s",
    },
    "en": {
        "password": "The store is password-protected; collection stopped (do not share the password). Use paste mode (paste_ingest.py) or re-run after launch.",
        "failed": "Homepage unreachable or blocked; collection stopped. The report can only be failed.",
        "run": "run folder: %s",
    },
}


def main(argv=None):
    output.safe_console()
    ap = argparse.ArgumentParser(description="Collect public pages for a launch check.")
    ap.add_argument("target", help="domain or any URL")
    ap.add_argument(
        "--markets", help='target markets, e.g. "US,UK,DE" (default: seen on site + US)'
    )
    ap.add_argument(
        "--no-ads", action="store_true", help="not planning Google Shopping ads"
    )
    ap.add_argument("--lang", default="zh", choices=i18n.LANGS)
    ap.add_argument("--out-root", help="delivery root folder")
    ap.add_argument("--out", help="raw-data folder itself (tests)")
    ap.add_argument("--delay", type=float, default=0.6)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--no-audit", action="store_true")
    args = ap.parse_args(argv)
    if re.search(r"\s", args.target.strip()):
        print(
            "error: one store URL only, without spaces: %r" % args.target,
            file=sys.stderr,
        )
        return 2
    try:
        _scheme, host = fetch.normalize_input(args.target)
    except ValueError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    if args.out:
        data_dir = os.path.abspath(args.out)
        run_dir = os.path.dirname(data_dir)
    else:
        root = output.resolve_output_root(args.out_root, cfg.ROOT_NAME[args.lang])
        run_dir = output.create_run_dir(
            root, fetch.strip_www(host), cfg.RUN_LABEL[args.lang]
        )
        data_dir = os.path.join(run_dir, output.raw_dirname(args.lang))
    os.makedirs(data_dir, exist_ok=True)
    output.write_readme(data_dir, cfg.README_TEXT[args.lang], args.lang)
    fetcher = fetch.Fetcher(
        timeout=args.timeout, delay=max(args.delay, 0.3), lang=args.lang
    )
    status = collect(
        args.target,
        os.path.join(data_dir, "raw"),
        fetcher,
        args.markets,
        not args.no_ads,
    )
    if fetch.cert_failures(status["requests"], fetcher.cert_note):
        print("! %s" % fetcher.cert_note)
    msg = MSG[args.lang]
    if status.get("password_protected"):
        print("! " + msg["password"])
    elif status["requests"] and status["requests"][0]["result"] != "ok":
        print("! " + msg["failed"])
    print(msg["run"] % run_dir)
    if args.no_audit:
        return 0
    import audit  # noqa: E402  (imported late: audit imports many modules)

    return audit.main([data_dir, "--lang", args.lang])


if __name__ == "__main__":
    sys.exit(main())
