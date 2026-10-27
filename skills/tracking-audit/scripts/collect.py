#!/usr/bin/env python3
"""Step 1 (online): save the public pages and tag containers a tracking audit needs.

CLI:
    python collect.py <url_or_domain> [--lang zh|en] [--out-root DIR | --out DIR]
                      [--delay 1.0] [--timeout 20] [--no-audit]

Fetches, one request at a time: the homepage, one collection page, one
product page, the cart page (/cart, empty) and the privacy policy, then up to
5 public tag containers referenced by those pages (Google Tag Manager
``gtm.js?id=GTM-...`` and Google tag ``gtag/js?id=...``). Never touches
checkout, never submits anything. Files go to ``<raw-data>/raw/`` and every
request to ``raw/_status.json``; then audit.py runs unless --no-audit.
Python 3.8+, standard library only. TLS verification is always on.
"""

import argparse
import html
import json
import os
import re
import sys
from urllib.parse import urljoin, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit  # noqa: E402
import detect  # noqa: E402
import skill_config as cfg  # noqa: E402
from _shared import fetch, i18n, output  # noqa: E402

GTM_URL = "https://www.googletagmanager.com/gtm.js?id=%s"
GTAG_URL = "https://www.googletagmanager.com/gtag/js?id=%s"
# One request to a well-known host when the homepage got no HTTP answer: tells
# "this machine is offline" (network_unavailable) from "the site is down or
# the address is wrong" (site_unreachable). Nothing is saved from it.
PROBE_URL = "https://www.googletagmanager.com/"
CHECKOUT_RE = re.compile(r"/checkouts?(/|\?|$)|/account|/orders", re.I)
HREF_RE = re.compile(r"""href\s*=\s*["']([^"'#]+)["']""", re.I)
PRODUCT_RE = re.compile(r"^(?:/[a-z]{2}(?:-[a-z]{2})?)?/products/[^/?]+$", re.I)
COLLECTION_RE = re.compile(r"^(?:/[a-z]{2}(?:-[a-z]{2})?)?/collections/[^/?]+$", re.I)
OTHER_COLLECTION_RE = re.compile(
    r"/(?:w|c|category|categories|collections|shop)/[^/?]+", re.I
)
OTHER_PRODUCT_RE = re.compile(r"/(?:product|products|p|item|dp|t)/[^/?]+", re.I)


def classify(status, body):
    if status is None:
        return "error"
    if status == 404:
        return "not_found"
    if status in (401, 403, 429, 430):
        return "blocked"
    if status >= 400 or not (body or b"").strip():
        return "error"
    return "ok"


def same_site(url, host):
    h = (urlsplit(url).hostname or "").lower()
    base = fetch.strip_www(host)
    return h == base or h.endswith("." + base)


def links(html_text, base_url, host):
    """Same-site absolute links in page order (no checkout / account)."""
    out = []
    for m in HREF_RE.finditer(html_text or ""):
        url = urljoin(base_url, html.unescape(m.group(1).strip()))
        if not url.startswith("http") or not same_site(url, host):
            continue
        if CHECKOUT_RE.search(urlsplit(url).path):
            continue
        if url not in out:
            out.append(url)
    return out


def pick(urls, rx, skip=()):
    for u in urls:
        path = urlsplit(u).path.rstrip("/")
        if rx.search(path) and not any(s in path for s in skip):
            return u.split("?")[0]
    return None


def privacy_link(html_text, base_url, host):
    for m in re.finditer(
        r"(?is)<a\b[^>]*href\s*=\s*[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", html_text or ""
    ):
        href, label = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
        if re.search(r"privacy", href + " " + label, re.I):
            url = urljoin(base_url, html.unescape(href.strip()))
            if url.startswith("http"):
                return url
    return None


class Collector(object):
    def __init__(self, target, data_dir, fetcher, log=print):
        self.scheme, self.host = fetch.normalize_input(target)
        self.base = "%s://%s" % (self.scheme, self.host)
        self.raw = os.path.join(data_dir, "raw")
        os.makedirs(self.raw, exist_ok=True)
        self.fetcher = fetcher
        self.requests = []
        self.texts = {}
        self.log = log

    def get(self, key, url, fname, accept=fetch.ACCEPT_HTML):
        r = self.fetcher.get(url, accept)
        result = classify(r["status"], r["body"])
        final = r.get("final_url") or url
        if result == "ok" and CHECKOUT_RE.search(urlsplit(final).path):
            result, r["body"] = "skipped_checkout", None
        self.requests.append(fetch.status_entry(key, url, r, result, fname))
        if result == "ok":
            with open(os.path.join(self.raw, fname), "wb") as fh:
                fh.write(r["body"])
            text = r["body"].decode("utf-8", errors="replace")
            self.texts[key] = text
            return text, final
        return None, final

    def skip(self, key, note):
        self.requests.append(
            {
                "key": key,
                "file": None,
                "url": None,
                "final_url": None,
                "http_status": None,
                "result": "not_found",
                "bytes": 0,
                "elapsed_ms": None,
                "note": note,
            }
        )

    def run(self):
        started = fetch.utc_now()
        files = cfg.PAGE_FILES
        home, home_url = self.get("homepage", self.base + "/", files["homepage"])
        probe = None
        first = self.requests[0] if self.requests else {}
        if home is None and first.get("http_status") is None:
            probe = self.probe_network()
        if home is not None:
            final_host = urlsplit(home_url).hostname or self.host
            self.host = final_host
            self.base = "%s://%s" % (urlsplit(home_url).scheme or "https", final_host)
            is_shop = len(detect.shopify_markers(home)) >= 1
            urls = links(home, home_url, self.host)
            coll = (
                pick(urls, COLLECTION_RE, skip=("/collections/all",))
                if is_shop
                else None
            )
            prod = pick(urls, PRODUCT_RE) if is_shop else pick(urls, OTHER_PRODUCT_RE)
            if is_shop:
                coll = coll or self.base + "/collections/all"
                ctext, cfinal = self.get("collection", coll, files["collection"])
                if not prod and ctext:
                    prod = pick(links(ctext, cfinal, self.host), PRODUCT_RE)
            else:
                coll = pick(urls, OTHER_COLLECTION_RE)
                if coll:
                    ctext, cfinal = self.get("collection", coll, files["collection"])
                    if not prod and ctext:
                        prod = pick(links(ctext, cfinal, self.host), OTHER_PRODUCT_RE)
                else:
                    self.skip("collection", "no collection link found on the homepage")
            if prod:
                self.get("product", prod, files["product"])
            else:
                self.skip("product", "no product link found on the pages fetched")
            self.get("cart", self.base + "/cart", files["cart"])
            priv = privacy_link(home, home_url, self.host)
            if not priv and is_shop:
                priv = self.base + "/policies/privacy-policy"
            if priv:
                self.get("privacy", priv, files["privacy"])
            else:
                self.skip("privacy", "no privacy policy link found on the homepage")
            self.fetch_containers()
        status = {
            "schema": "%s/status/1" % cfg.NAME,
            "tool_version": cfg.TOOL_VERSION,
            "input": self.base,
            "domain": fetch.strip_www(self.host),
            "base_url": self.base,
            "started_at_utc": started,
            "finished_at_utc": fetch.utc_now(),
            "requests": self.requests,
        }
        if probe is not None:
            status["network_probe"] = probe
        with open(os.path.join(self.raw, "_status.json"), "w", encoding="utf-8") as fh:
            json.dump(status, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        return status

    def probe_network(self):
        r = self.fetcher.get(PROBE_URL, "*/*")
        return {
            "url": PROBE_URL,
            "http_status": r.get("status"),
            "result": "error" if r.get("status") is None else "answered",
            "note": (r.get("note") or "")[:200],
        }

    def fetch_containers(self):
        gtm, gtag = [], []
        for text in self.texts.values():
            g1, g2 = detect.container_ids(text)
            gtm += [g for g in g1 if g not in gtm]
            gtag += [g for g in g2 if g not in gtag]
        todo = [("gtm", g) for g in gtm] + [("gtag", g) for g in gtag]
        for kind, cid in todo[: cfg.MAX_CONTAINERS]:
            url = (GTM_URL if kind == "gtm" else GTAG_URL) % cid
            fname = "container-%s.js" % cid
            self.get("container:%s" % cid, url, fname, accept="*/*")
        for kind, cid in todo[cfg.MAX_CONTAINERS :]:
            self.skip(
                "container:%s" % cid,
                "over the limit of %d containers" % cfg.MAX_CONTAINERS,
            )


def collect(target, data_dir, fetcher):
    return Collector(target, data_dir, fetcher).run()


def main(argv=None):
    output.safe_console()
    ap = argparse.ArgumentParser(
        description="Collect public pages for a tracking audit."
    )
    ap.add_argument("target", help="domain or any URL")
    ap.add_argument("--lang", default="zh", choices=i18n.LANGS)
    ap.add_argument("--out-root", help="delivery root folder")
    ap.add_argument("--out", help="raw-data folder itself (tests)")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--no-audit", action="store_true")
    ap.add_argument(
        "--mode",
        choices=("own", "other"),
        default="own",
        help="own: the user's store (default); other: someone else's store",
    )
    args = ap.parse_args(argv)
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
        timeout=args.timeout, delay=max(args.delay, 0.0), lang=args.lang
    )
    status = collect(args.target, data_dir, fetcher)
    if fetch.cert_failures(status["requests"], fetcher.cert_note):
        print("! %s" % fetcher.cert_note)
    ok = sum(1 for r in status["requests"] if r["result"] == "ok")
    print("requests: %d, ok: %d" % (len(status["requests"]), ok))
    print("run folder: %s" % run_dir)
    if args.no_audit:
        return 0
    return audit.main([data_dir, "--lang", args.lang, "--mode", args.mode])


if __name__ == "__main__":
    sys.exit(main())
