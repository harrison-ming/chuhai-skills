#!/usr/bin/env python3
"""Step 1 (online, read-only): fetch the public pages a payments check needs.

CLI:
    python collect.py <url_or_domain> [--lang zh|en] [--out-root DIR | --out DIR]
                      [--questionnaire FILE] [--delay 0.8] [--timeout 20]
                      [--no-compute]
    python collect.py --offline "<store name>" [--questionnaire FILE] ...

Creates ``<root>/<domain> 收款体检 <date>/`` (root: --out-root >
$CHUHAI_OUTPUT_DIR > cloud sandbox > Documents/出海拆解报告), saves pages to
``<raw-data>/raw/`` and every request to ``raw/_status.json``, then runs
compute.py unless --no-compute. ``--offline`` makes the run folder without
any request (store not live yet: fee estimate + entity checks only).

Pages (GET only, sequential, polite): homepage, meta.json, the first page
of products.json, five /policies/ pages, /cart (empty cart, nothing is
added), up to three product pages, a contact page and an FAQ page. Pages
not at the Shopify paths are discovered from homepage links. It never adds
to cart, never enters checkout, never logs in, never submits a form.
Python 3.8+, standard library only. TLS verification is always on.
"""

import argparse
import json
import os
import re
import sys
from urllib.parse import quote, urljoin, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import signals  # noqa: E402
import skill_config as cfg  # noqa: E402
from _shared import fetch, i18n, output  # noqa: E402

PRODUCT_SAMPLES = 3
CONTACT_SLUGS = ["/pages/contact", "/pages/contact-us", "/contact", "/contact-us"]
FAQ_SLUGS = ["/pages/faq", "/pages/faqs", "/pages/help", "/faq"]
CHECKOUT_RE = re.compile(r"(?i)/checkouts?(?:/|\?|$)|/checkout\b")
NON_PRODUCT_RE = re.compile(
    r"protect|insurance|guarantee|gift[\s-]*(?:card|wrap|box|bag)|e-?gift|warranty|shipping|"
    r"\broute\b|donation|\btips?\b",
    re.I,
)


def classify(status, body, kind):
    """(result, note, keep_body) for one response."""
    if status is None:
        return "error", "", False
    if status == 404:
        return "not_found", "", False
    if status in (401, 403, 429, 430):
        return "blocked", "HTTP %d" % status, False
    if status >= 400:
        return "error", "HTTP %d" % status, False
    text = (body or b"").decode("utf-8", errors="replace")
    if not text.strip():
        return "error", "empty body", False
    if kind == "json":
        try:
            json.loads(text)
        except ValueError:
            if signals.looks_blocked(text) or text.lstrip().startswith("<"):
                return "blocked", "HTML instead of JSON", False
            return "error", "invalid JSON", False
    elif len(text) < 30000 and signals.looks_blocked(text):
        if "shopify" not in text.lower():
            return "blocked", "challenge page", False
    return "ok", "", True


def pick_products(products, n=PRODUCT_SAMPLES):
    """Cheapest, middle and dearest real, in-stock products (handles)."""
    pool = []
    for p in products:
        if not isinstance(p, dict) or not p.get("handle"):
            continue
        text = " ".join(
            str(p.get(k) or "") for k in ("handle", "title", "product_type")
        )
        if NON_PRODUCT_RE.search(text):
            continue
        prices = []
        for v in p.get("variants") or []:
            try:
                prices.append(float(v.get("price")))
            except (TypeError, ValueError, AttributeError):
                pass
        if not prices or min(prices) < 5:
            continue
        pool.append((min(prices), p["handle"]))
    pool.sort()
    if len(pool) <= n:
        return [h for _p, h in pool]
    picks = [pool[0], pool[len(pool) // 2], pool[-1]]
    out = []
    for _p, h in picks:
        if h not in out:
            out.append(h)
    return out[:n]


class Collector(object):
    def __init__(self, target, data_dir, fetcher):
        self.scheme, self.host = fetch.normalize_input(target)
        self.input = target
        self.base = "%s://%s" % (self.scheme, self.host)
        self.domain = fetch.strip_www(self.host)
        self.raw = os.path.join(data_dir, "raw")
        self.f = fetcher
        self.requests = []
        self.started = fetch.utc_now()
        self.products = []

    def fetch(self, key, url, fname, kind="html", note=""):
        r = self.f.get(url, fetch.ACCEPT_JSON if kind == "json" else fetch.ACCEPT_HTML)
        result, cnote, keep = classify(r["status"], r["body"], kind)
        if (
            keep
            and result == "ok"
            and CHECKOUT_RE.search(urlsplit(r["final_url"]).path)
        ):
            result, cnote, keep = (
                "skipped",
                "redirected into checkout, not saved",
                False,
            )
        entry = fetch.status_entry(
            key, url, r, result, fname, "; ".join(x for x in (cnote, note) if x)
        )
        self.requests.append(entry)
        if keep:
            with open(os.path.join(self.raw, fname), "wb") as fh:
                fh.write(r["body"])
            if result == "ok":
                return r["body"].decode("utf-8", errors="replace")
        return None

    def run(self):
        os.makedirs(self.raw, exist_ok=True)
        home = self.fetch("homepage", self.base + "/", "homepage.html")
        final = self.requests[-1].get("final_url") or ""
        parts = urlsplit(final)
        if parts.netloc and parts.scheme in ("http", "https"):
            new_base = "%s://%s" % (parts.scheme, parts.netloc)
            if new_base != self.base:
                self.requests[-1]["note"] = (
                    (self.requests[-1]["note"] + "; ")
                    if self.requests[-1]["note"]
                    else ""
                ) + "base redirected to %s" % new_base
                self.base = new_base
        if (
            self.requests[-1]["result"] in ("error",)
            and self.requests[-1]["http_status"] is None
        ):
            self.write_status()  # network down: do not hammer the rest
            return
        shopify = bool(signals.shopify_markers(home))
        meta = self.fetch("meta_json", self.base + "/meta.json", "meta.json", "json")
        shopify = shopify or bool(meta)
        prod = self.fetch(
            "products_json",
            self.base + "/products.json?limit=250",
            "products-1.json",
            "json",
        )
        if prod:
            try:
                self.products = json.loads(prod).get("products") or []
            except (ValueError, AttributeError):
                self.products = []
        links = signals.discover_links(home, urlsplit(self.base).netloc) if home else {}
        for name in signals.POLICY_NAMES:
            key = "policy_" + name.replace("-", "_")
            fname = "policy-%s.html" % name
            got = None
            if shopify or name != "contact-information":
                got = self.fetch(key, "%s/policies/%s" % (self.base, name), fname)
            if got is None and name in links:
                url = urljoin(self.base + "/", links[name])
                self.fetch(key, url, fname, note="found via homepage link")
        self.fetch("cart", self.base + "/cart", "cart.html")
        for handle in pick_products(self.products):
            self.fetch(
                "page_product",
                "%s/products/%s" % (self.base, quote(handle, safe="")),
                "page-product-%s.html" % safe_slug(handle),
            )
        for key, slugs in (("page_contact", CONTACT_SLUGS), ("page_faq", FAQ_SLUGS)):
            name = key[5:]
            fname = "page-%s.html" % name
            tried = []
            if name in links:
                tried.append(urljoin(self.base + "/", links[name]))
            tried += [self.base + s for s in slugs]
            seen = set()
            for url in tried:
                if url in seen:
                    continue
                seen.add(url)
                if self.fetch(key, url, fname):
                    break
        self.write_status()

    def write_status(self):
        write_status_file(
            self.raw, self.input, self.domain, self.base, self.started, self.requests
        )


def write_status_file(raw, target, domain, base, started, requests, mode="online"):
    status = {
        "schema": "%s/status/1" % cfg.NAME,
        "tool_version": cfg.TOOL_VERSION,
        "mode": mode,
        "input": target,
        "domain": domain,
        "base_url": base,
        "started_at_utc": started,
        "finished_at_utc": fetch.utc_now(),
        "requests": requests,
    }
    os.makedirs(raw, exist_ok=True)
    with open(os.path.join(raw, "_status.json"), "w", encoding="utf-8") as fh:
        json.dump(status, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return status


def safe_slug(handle):
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(handle or "")).strip("-.")
    return slug[:80] or "item"


def main(argv=None):
    output.safe_console()
    ap = argparse.ArgumentParser(
        description="Collect public pages for a payments check."
    )
    ap.add_argument("target", nargs="?", help="domain or any URL of the store")
    ap.add_argument(
        "--offline", metavar="NAME", help="no store yet: name for the run folder"
    )
    ap.add_argument("--lang", default="zh", choices=i18n.LANGS)
    ap.add_argument("--out-root", help="delivery root folder")
    ap.add_argument("--out", help="raw-data folder itself (tests)")
    ap.add_argument("--questionnaire", help="questionnaire JSON to copy into the run")
    ap.add_argument("--delay", type=float, default=0.8)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--no-compute", action="store_true")
    args = ap.parse_args(argv)
    if bool(args.target) == bool(args.offline):
        print(
            "error: give a store URL, or --offline <name> (not both)", file=sys.stderr
        )
        return 2
    subject = None
    if args.target:
        try:
            _scheme, host = fetch.normalize_input(args.target)
        except ValueError as e:
            print("error: %s" % e, file=sys.stderr)
            return 2
        subject = fetch.strip_www(host)
    else:
        subject = output.sanitize_filename(args.offline, fallback="store")
    if args.out:
        data_dir = os.path.abspath(args.out)
        run_dir = os.path.dirname(data_dir)
    else:
        root = output.resolve_output_root(args.out_root, cfg.ROOT_NAME[args.lang])
        run_dir = output.create_run_dir(root, subject, cfg.RUN_LABEL[args.lang])
        data_dir = os.path.join(run_dir, output.raw_dirname(args.lang))
    os.makedirs(data_dir, exist_ok=True)
    output.write_readme(data_dir, cfg.README_TEXT[args.lang], args.lang)
    if args.questionnaire:
        import questionnaire  # noqa: E402

        try:
            with open(args.questionnaire, encoding="utf-8-sig") as fh:
                q = questionnaire.normalize(json.load(fh))
        except (OSError, ValueError) as e:
            print("error: questionnaire: %s" % e, file=sys.stderr)
            return 2
        questionnaire.save(data_dir, q)
    if args.offline:
        now = fetch.utc_now()
        write_status_file(
            os.path.join(data_dir, "raw"),
            args.offline,
            subject,
            None,
            now,
            [],
            "offline",
        )
    else:
        fetcher = fetch.Fetcher(
            timeout=args.timeout, delay=max(args.delay, 0.0), lang=args.lang
        )
        col = Collector(args.target, data_dir, fetcher)
        col.run()
        if fetch.cert_failures(col.requests, fetcher.cert_note):
            print("! %s" % fetcher.cert_note)
        failed_net = [
            r
            for r in col.requests
            if r.get("http_status") is None and r["result"] == "error"
        ]
        if col.requests and len(failed_net) == len(col.requests):
            print("! network: no request got any answer (no internet access here?)")
    print("run folder: %s" % run_dir)
    if args.no_compute:
        return 0
    import compute  # noqa: E402

    return compute.main([data_dir, "--lang", args.lang])


if __name__ == "__main__":
    sys.exit(main())
