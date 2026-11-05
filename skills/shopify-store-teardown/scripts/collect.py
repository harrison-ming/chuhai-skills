#!/usr/bin/env python3
"""Collect public storefront data of a Shopify store into ``<out>/raw/``.

Fetches the public endpoints defined in references/data-contract.md
(meta.json, paginated products.json, collections.json, sitemap, homepage,
best-selling order, RDAP, Wayback CDX; plus policy, about, one main
collection page and three sample product pages at L1),
records every request in ``raw/_status.json`` and then runs summarize.py
unless ``--no-summarize`` is given. A failing request never aborts the run.

CLI:
    python collect.py <store_url_or_domain> [--out-root DIR | --out DIR]
                      [--depth L0|L1] [--max-pages 100]
                      [--max-sitemap-children 50] [--skip-offsite]
                      [--delay 0.5] [--timeout 20] [--no-summarize]
                      [--init-only]

By default a new run folder ``<root>/<domain> 对标拆解 <YYYY-MM-DD>[ (N)]/``
is created, where <root> is resolved by delivery.resolve_output_root
(``--out-root`` > $CHUHAI_OUTPUT_DIR > cloud sandbox > Documents/出海拆解报告),
and data goes to its ``原始数据/`` subfolder. ``--out`` names that raw-data
folder directly (legacy behaviour). ``--init-only`` only creates that run
folder and prints it (no network), so mode B saves files in the same place.
Python 3.8+, standard library only. TLS verification is always on; the
HTTP / certificate handling lives in ``_shared/fetch.py``.
"""

import argparse
import ipaddress
import json
import os
import re
import socket
import subprocess  # noqa: F401  (tests patch collect.subprocess.run)
import sys
from urllib.parse import quote, urlsplit
from urllib.error import URLError
from urllib.request import HTTPSHandler, build_opener

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import delivery  # noqa: E402
import summarize  # noqa: E402
from _shared import fetch  # noqa: E402
from _shared.fetch import (  # noqa: E402,F401  (re-exported API)
    ACCEPT_HTML,
    ACCEPT_JSON,
    CERT_NOTE,
    MAX_BYTES,
    SECOND_LEVEL,
    USER_AGENT,
    WIN_ROOT_REFRESH_NOTE,
    WIN_ROOT_REFRESH_TIMEOUT,
    cert_failures,
    cert_note,
    decode_body,
    describe_error,
    is_cert_error,
    make_ssl_context,
    normalize_input,
    refresh_windows_roots,
    registrable_domain,
    site_root,
    strip_www,
    utc_now,
)

TOOL_VERSION = summarize.TOOL_VERSION
PRODUCTS_LIMIT = 250
PRODUCTS_OFFSET_CAP = 25000
COLLECTIONS_MAX_PAGES = 4
SITEMAP_CHILD_CAP = 50
ABOUT_SLUGS = ["about", "about-us", "our-story"]
# L1 sample product pages: skip add-ons (rule shared with summarize.py).
NON_PRODUCT_RE = summarize.NON_PRODUCT_RE
MIN_SAMPLE_PRICE = summarize.MIN_REAL_PRICE
L1_SAMPLE_ROLES = ("entry", "main", "premium")


def ensure_public_url(url):
    """Reject non-HTTP targets and hosts resolving to non-public addresses."""
    parts = urlsplit(url)
    host = parts.hostname
    if parts.scheme not in ("http", "https") or not host:
        raise ValueError("only public http(s) URLs are allowed")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parts.port or 443)}
    except socket.gaierror as exc:
        raise ValueError("host name did not resolve") from exc
    if not addresses:
        raise ValueError("host name did not resolve")
    for raw in addresses:
        ip = ipaddress.ip_address(raw.split("%", 1)[0])
        if not ip.is_global:
            raise ValueError(
                "private, local or reserved network targets are not allowed"
            )
    return True


class PublicOnlyRedirect(fetch.LimitedRedirect):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            ensure_public_url(newurl)
        except ValueError as exc:
            raise URLError(str(exc))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_Redirect = fetch.LimitedRedirect


class Fetcher(fetch.Fetcher):
    """Shared polite fetcher; the opener is built from this module's names
    so tests can patch ``collect.build_opener`` / ``collect.make_ssl_context``."""

    def _build_opener(self):
        self.ctx = make_ssl_context()  # verification stays on
        self.opener = build_opener(HTTPSHandler(context=self.ctx), _Redirect())


class PublicOnlyFetcher(Fetcher):
    def _build_opener(self):
        self.ctx = make_ssl_context()
        self.opener = build_opener(HTTPSHandler(context=self.ctx), PublicOnlyRedirect())


def classify(status, body, kind):
    """Map a response to (result, note, keep_body)."""
    if status is None:
        return "error", "", False
    if status == 404:
        return "not_found", "", False
    if status in (401, 403, 429, 430):
        return "blocked", "HTTP %d" % status, False
    if status >= 400:
        return "error", "HTTP %d" % status, False
    text = body.decode("utf-8", errors="replace") if body else ""
    if not text.strip():
        return "error", "empty body", False
    if kind == "json":
        try:
            json.loads(text)
        except ValueError:
            if summarize.looks_blocked(text) or text.lstrip().startswith("<"):
                return (
                    "blocked",
                    "HTML instead of JSON (possible challenge page)",
                    False,
                )
            return "error", "invalid JSON", False
    elif kind == "html" and len(text) < 30000 and summarize.looks_blocked(text):
        if "shopify" not in text.lower():
            return "blocked", "challenge page", False
    return "ok", "", True


class Collector(object):
    def __init__(
        self,
        target,
        out_dir,
        depth,
        max_pages,
        max_sitemap_children,
        skip_offsite,
        fetcher,
        public_only=False,
    ):
        self.scheme, self.host = normalize_input(target)
        self.input = target
        self.base = "%s://%s" % (self.scheme, self.host)
        self.domain = strip_www(self.host)
        self.out = out_dir
        self.raw = os.path.join(out_dir, "raw")
        self.depth = depth
        self.max_pages = max_pages
        self.max_sitemap_children = max_sitemap_children
        self.skip_offsite = skip_offsite
        self.f = fetcher
        self.public_only = public_only
        self.requests = []
        self.started = utc_now()
        self.products = []
        self.collections = []
        self.best_selling = []
        self.l1_samples = []

    def fetch(self, key, url, fname, kind):
        """Fetch url, save to raw/fname on success, log status. Returns text or None."""
        r = self.f.get(url, ACCEPT_JSON if kind == "json" else ACCEPT_HTML)
        result, cnote, keep = classify(r["status"], r["body"], kind)
        note = "; ".join(n for n in (r["note"], cnote) if n)
        if r["status"] is None and not note:
            note = "network error"
        body = r["body"] or b""
        entry = {
            "key": key,
            "file": fname,
            "url": url,
            "final_url": r["final_url"],
            "http_status": r["status"],
            "result": result,
            "bytes": len(body),
            "elapsed_ms": r["elapsed_ms"],
            "note": note,
        }
        self.requests.append(entry)
        if keep:
            with open(os.path.join(self.raw, fname), "wb") as fh:
                fh.write(body)
            return body.decode("utf-8", errors="replace")
        return None

    def run(self):
        os.makedirs(self.raw, exist_ok=True)
        # Homepage first: settle the canonical host to avoid a redirect per request.
        self.fetch("homepage", self.base + "/", "homepage.html", "html")
        final = self.requests[-1].get("final_url") or ""
        parts = urlsplit(final)
        if parts.netloc and parts.scheme in ("http", "https"):
            new_base = "%s://%s" % (parts.scheme, parts.netloc)
            if new_base != self.base:
                self.requests[-1]["note"] = (
                    self.requests[-1]["note"] + "; "
                    if self.requests[-1]["note"]
                    else ""
                ) + "base redirected to %s" % new_base
                self.base = new_base
        self.fetch("meta_json", self.base + "/meta.json", "meta.json", "json")
        self.collect_products()
        self.collect_collections()
        self.collect_sitemap()
        best_txt = self.fetch(
            "best_selling",
            self.base + "/collections/all?sort_by=best-selling",
            "best-selling.html",
            "html",
        )
        if best_txt:
            self.best_selling = summarize.best_selling_handles(best_txt, limit=30)
        if self.depth == "L1":
            self.collect_l1_pages()
            for name in summarize.POLICY_NAMES:
                self.fetch(
                    "policy_" + name.replace("-", "_"),
                    "%s/policies/%s" % (self.base, name),
                    "policy-%s.html" % name,
                    "html",
                )
            for slug in ABOUT_SLUGS:
                if self.fetch(
                    "page_about",
                    "%s/pages/%s" % (self.base, slug),
                    "page-about.html",
                    "html",
                ):
                    break
        if not self.skip_offsite:
            self.collect_offsite()
        self.write_status()

    def collect_products(self):
        page = 1
        while page <= self.max_pages and page * PRODUCTS_LIMIT <= PRODUCTS_OFFSET_CAP:
            url = "%s/products.json?limit=%d&page=%d" % (
                self.base,
                PRODUCTS_LIMIT,
                page,
            )
            text = self.fetch("products_json", url, "products-%d.json" % page, "json")
            if text is None:
                break
            try:
                items = json.loads(text).get("products")
            except (ValueError, AttributeError):
                break
            if not items:
                break
            self.products.extend(p for p in items if isinstance(p, dict))
            page += 1

    def collect_collections(self):
        for page in range(1, COLLECTIONS_MAX_PAGES + 1):
            url = "%s/collections.json?limit=%d&page=%d" % (
                self.base,
                PRODUCTS_LIMIT,
                page,
            )
            text = self.fetch(
                "collections_json", url, "collections-%d.json" % page, "json"
            )
            if text is None:
                break
            try:
                items = json.loads(text).get("collections")
            except (ValueError, AttributeError):
                break
            if items:
                self.collections.extend(c for c in items if isinstance(c, dict))
            if not items or len(items) < PRODUCTS_LIMIT:
                break

    def collect_sitemap(self):
        text = self.fetch("sitemap", self.base + "/sitemap.xml", "sitemap.xml", "xml")
        if not text:
            return
        locs = re.findall(r"<loc>\s*([^<]+?)\s*</loc>", text)
        base_host = (urlsplit(self.base).hostname or "").lower()
        children = []
        for raw_url in locs:
            url = summarize.html.unescape(raw_url)
            parts = urlsplit(url)
            if "sitemap_products" not in url:
                continue
            if (
                parts.scheme not in ("http", "https")
                or (parts.hostname or "").lower() != base_host
            ):
                continue
            if self.public_only:
                try:
                    ensure_public_url(url)
                except ValueError:
                    continue
            children.append(url)
        for i, url in enumerate(children[: self.max_sitemap_children], 1):
            self.fetch("sitemap_products", url, "sitemap-products-%d.xml" % i, "xml")

    def collect_l1_pages(self):
        """One main collection page and three sample product pages."""
        if not self.fetch(
            "page_collection",
            self.base + "/collections/all",
            "page-collection-all.html",
            "html",
        ):
            handle = pick_collection(self.collections)
            if handle:
                self.fetch(
                    "page_collection",
                    "%s/collections/%s" % (self.base, quote(handle, safe="")),
                    "page-collection-%s.html" % safe_slug(handle),
                    "html",
                )
        self.l1_samples = select_l1_samples(self.products, self.best_selling)
        for sample in self.l1_samples:
            fname = "page-product-%s.html" % safe_slug(sample["handle"])
            sample["file"] = "raw/" + fname
            self.fetch(
                "page_product",
                "%s/products/%s" % (self.base, quote(sample["handle"], safe="")),
                fname,
                "html",
            )
            sample["result"] = self.requests[-1]["result"]

    def collect_offsite(self):
        reg = registrable_domain(self.domain)
        if reg is None:
            self.requests.append(
                {
                    "key": "rdap",
                    "file": "rdap.json",
                    "url": None,
                    "result": "not_checked",
                    "note": "no registrable domain",
                }
            )
            return
        self.fetch("rdap", "https://rdap.org/domain/%s" % reg, "rdap.json", "json")
        wb = (
            "https://web.archive.org/cdx/search/cdx?url=%s&output=json"
            "&fl=timestamp&collapse=timestamp:6" % quote(self.domain, safe="")
        )
        self.fetch("wayback", wb, "wayback.json", "json")

    def write_status(self):
        status = {
            "schema": "store-teardown/status/1",
            "tool_version": TOOL_VERSION,
            "input": self.input,
            "domain": self.domain,
            "base_url": self.base,
            "depth": self.depth,
            "started_at_utc": self.started,
            "finished_at_utc": utc_now(),
            "l1_samples": self.l1_samples,
            "requests": self.requests,
        }
        with open(os.path.join(self.raw, "_status.json"), "w", encoding="utf-8") as fh:
            json.dump(status, fh, ensure_ascii=False, indent=2)
            fh.write("\n")


def safe_slug(handle):
    """File-name-safe version of a URL handle."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(handle or "")).strip("-.")
    return slug[:80] or "item"


def pick_collection(collections):
    """Fallback main collection: first non-empty one that is not ``all``."""
    for c in collections:
        handle = c.get("handle")
        count = c.get("products_count")
        if not handle or handle == "all":
            continue
        if count is None or (isinstance(count, (int, float)) and count > 0):
            return handle
    return None


sample_price = summarize.lowest_price
is_real_product = summarize.is_real_product
SAMPLE_BASIS = {
    "entry": "closest_to_p25_excluding_virtual",
    "main": "best_selling_in_p25_p75_excluding_virtual",
    "main_fallback": "closest_to_median_excluding_virtual",
    "premium": "max_excluding_virtual",
}


def select_l1_samples(products, best_selling=None):
    """Pick entry (~P25), main (~median, best-selling first) and premium (max).

    Returns up to three dicts ``{handle, role, price, basis, basis_value,
    basis_pool}`` with distinct handles. Percentiles are computed on real
    products only (same rule as summary ``price_excluding_virtual``); when at
    least three are in stock, only in-stock items form the pool, so
    ``basis_value`` can differ slightly from the summary figures.
    """
    pool = []
    for p in products or []:
        handle = p.get("handle")
        price = sample_price(p)
        if handle and is_real_product(p, price):
            pool.append(
                {"handle": handle, "price": price, "available": summarize.in_stock(p)}
            )
    if not pool:
        return []
    avail = [x for x in pool if x["available"]]
    basis_pool = "all_real_products"
    if len(avail) >= 3:
        pool = avail
        basis_pool = "in_stock_real_products"
    prices = sorted(x["price"] for x in pool)
    p25 = summarize.percentile(prices, 0.25)
    med = summarize.percentile(prices, 0.5)
    p75 = summarize.percentile(prices, 0.75)
    rank = {}
    for i, h in enumerate(best_selling or []):
        rank.setdefault(h, i)
    chosen = []

    def free(x):
        return all(x["handle"] != c["handle"] for c in chosen)

    def take(role, item, basis, value):
        if item is not None:
            chosen.append(
                {
                    "handle": item["handle"],
                    "role": role,
                    "price": item["price"],
                    "basis": basis,
                    "basis_value": summarize.r2(value),
                    "basis_pool": basis_pool,
                }
            )

    # premium: highest price
    top = max(pool, key=lambda x: (x["price"], -rank.get(x["handle"], 1e9)))
    take("premium", top, SAMPLE_BASIS["premium"], top["price"])
    # main: best-selling item in the P25-P75 band, else closest to the median
    band = [
        x for x in pool if free(x) and x["handle"] in rank and p25 <= x["price"] <= p75
    ]
    main_basis, main_value = SAMPLE_BASIS["main"], med
    if band:
        main = min(band, key=lambda x: rank[x["handle"]])
    else:
        main_basis = SAMPLE_BASIS["main_fallback"]
        rest = [x for x in pool if free(x)]
        main = (
            min(rest, key=lambda x: (abs(x["price"] - med), rank.get(x["handle"], 1e9)))
            if rest
            else None
        )
    take("main", main, main_basis, main_value)
    rest = [x for x in pool if free(x)]
    if rest:
        take(
            "entry",
            min(
                rest,
                key=lambda x: (
                    abs(x["price"] - p25),
                    rank.get(x["handle"], 1e9),
                    x["price"],
                ),
            ),
            SAMPLE_BASIS["entry"],
            p25,
        )
    order = {r: i for i, r in enumerate(L1_SAMPLE_ROLES)}
    return sorted(chosen, key=lambda c: order[c["role"]])


def main(argv=None):
    delivery.safe_console()
    ap = argparse.ArgumentParser(description="Collect public Shopify storefront data.")
    ap.add_argument("store", help="domain or any URL of the store")
    ap.add_argument(
        "--out-root",
        help="delivery root folder (default: Documents/%s)" % delivery.ROOT_NAME,
    )
    ap.add_argument(
        "--out",
        help="raw-data folder itself (legacy; overrides --out-root)",
    )
    ap.add_argument("--depth", choices=["L0", "L1"], default="L0")
    ap.add_argument("--max-pages", type=int, default=100)
    ap.add_argument(
        "--max-sitemap-children",
        type=int,
        default=SITEMAP_CHILD_CAP,
        help="maximum product sitemap files to fetch (0-%d)" % SITEMAP_CHILD_CAP,
    )
    ap.add_argument("--skip-offsite", action="store_true", help="skip RDAP and Wayback")
    ap.add_argument(
        "--public-only",
        action="store_true",
        help="reject private/reserved targets and cross-origin product sitemaps",
    )
    ap.add_argument("--delay", type=float, default=0.5, help="seconds between requests")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--no-summarize", action="store_true")
    ap.add_argument(
        "--init-only",
        action="store_true",
        help="only create the run folder (and raw/) in the delivery root and "
        "print it; no network. For mode B (files saved by hand).",
    )
    args = ap.parse_args(argv)

    try:
        _scheme, host = normalize_input(args.store)
    except ValueError as e:
        print("错误: 无法解析店铺地址: %s" % e, file=sys.stderr)
        return 2
    try:
        if args.out:
            out = os.path.abspath(os.path.expanduser(args.out))
            run_dir = os.path.dirname(out)
        else:
            root = delivery.resolve_output_root(args.out_root)
            run_dir = delivery.create_run_dir(root, strip_www(host))
            out = os.path.join(run_dir, delivery.RAW_DIRNAME)
        os.makedirs(out, exist_ok=True)
        delivery.write_readme(out)
    except OSError as e:
        print("错误: 无法创建输出文件夹: %s" % e, file=sys.stderr)
        return 2
    if args.init_only:
        os.makedirs(os.path.join(out, "raw"), exist_ok=True)
        print("本次拆解文件夹: %s" % run_dir)
        print("原始文件请存到: %s" % os.path.join(out, "raw"))
        print('存好后运行: python summarize.py "%s"' % run_dir)
        return 0
    if args.public_only:
        try:
            scheme, host = normalize_input(args.store)
            ensure_public_url("%s://%s/" % (scheme, host))
        except ValueError as exc:
            print("错误: 只允许访问公网 HTTP(S) 店铺: %s" % exc, file=sys.stderr)
            return 2
    fetcher_cls = PublicOnlyFetcher if args.public_only else Fetcher
    fetcher = fetcher_cls(timeout=args.timeout, delay=max(args.delay, 0.0))
    col = Collector(
        args.store,
        out,
        args.depth,
        max(args.max_pages, 1),
        min(max(args.max_sitemap_children, 0), SITEMAP_CHILD_CAP),
        args.skip_offsite,
        fetcher,
        args.public_only,
    )
    print("开始采集 %s (深度 %s)" % (col.base, args.depth))
    print("原始数据保存到: %s" % out)
    col.run()

    counts = {}
    for r in col.requests:
        counts[r["result"]] = counts.get(r["result"], 0) + 1
    print(
        "请求 %d 个: %s"
        % (len(col.requests), ", ".join("%s=%d" % kv for kv in sorted(counts.items())))
    )
    if cert_failures(col.requests):
        print("提示: %s" % CERT_NOTE)
    if args.no_summarize:
        print('已跳过汇总. 之后可运行: python summarize.py "%s"' % out)
        print_next_step(run_dir, out)
        return 0
    try:
        summary = summarize.summarize_run(out)
    except Exception as e:  # noqa: BLE001 - raw data is kept even if summarize fails
        print("汇总失败 (原始数据已保存): %s" % e, file=sys.stderr)
        return 1
    print(summarize.human_digest(summary))
    print_next_step(run_dir, out)
    return 0


def print_next_step(run_dir, out):
    print("")
    print("本次拆解文件夹: %s" % run_dir)
    print(
        '下一步: agent 把报告写到 %s, 然后运行 deliver.py "%s"'
        % (os.path.join(out, "report.md"), run_dir)
    )
    if os.path.basename(out) != delivery.RAW_DIRNAME:
        print(
            '注意: --out 指定的文件夹不叫 "%s", deliver.py 需要'
            " <拆解文件夹>/%s/ 结构." % (delivery.RAW_DIRNAME, delivery.RAW_DIRNAME)
        )


if __name__ == "__main__":
    sys.exit(main())
