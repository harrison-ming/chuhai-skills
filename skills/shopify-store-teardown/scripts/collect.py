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
                      [--depth L0|L1] [--max-pages 100] [--skip-offsite]
                      [--delay 0.5] [--timeout 20] [--no-summarize]

By default a new run folder ``<root>/<domain> 对标拆解 <YYYY-MM-DD>[ (N)]/``
is created, where <root> is resolved by delivery.resolve_output_root
(``--out-root`` > $CHUHAI_OUTPUT_DIR > cloud sandbox > Documents/出海拆解报告),
and data goes to its ``原始数据/`` subfolder. ``--out`` names that raw-data
folder directly (legacy behaviour).
Python 3.8+, standard library only. TLS verification is always on.
"""

import argparse
import datetime
import gzip
import http.client
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
import zlib
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import delivery  # noqa: E402
import summarize  # noqa: E402

TOOL_VERSION = summarize.TOOL_VERSION
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
ACCEPT_HTML = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
ACCEPT_JSON = "application/json,text/javascript,*/*;q=0.5"
MAX_BYTES = 40 * 1024 * 1024
PRODUCTS_LIMIT = 250
PRODUCTS_OFFSET_CAP = 25000
COLLECTIONS_MAX_PAGES = 4
SITEMAP_CHILD_CAP = 50
ABOUT_SLUGS = ["about", "about-us", "our-story"]
# L1 sample product pages: skip add-ons (rule shared with summarize.py).
NON_PRODUCT_RE = summarize.NON_PRODUCT_RE
MIN_SAMPLE_PRICE = summarize.MIN_REAL_PRICE
L1_SAMPLE_ROLES = ("entry", "main", "premium")


def cert_note(platform=None):
    """Plain-language hint for a certificate verification failure."""
    platform = platform or sys.platform
    if platform == "darwin":
        return (
            "证书校验失败. 如果 Python 是从 python.org 下载安装的, 请到 应用程序 → "
            "Python 文件夹里双击运行一次 Install Certificates.command, 然后重试"
        )
    if platform == "win32":
        return (
            "证书校验失败. 请先用浏览器 (Edge 或 Chrome) 打开一次该网站, "
            "或等几分钟后重试 (Windows 会自动补齐证书)"
        )
    return (
        "证书校验失败. 请更新系统证书 (例如 sudo apt install ca-certificates, "
        "或 sudo yum update ca-certificates) 后重试"
    )


CERT_NOTE = cert_note()
WIN_ROOT_REFRESH_NOTE = "windows root certificate refresh triggered, retried once"
WIN_ROOT_REFRESH_TIMEOUT = 20
SECOND_LEVEL = {
    "co.uk",
    "org.uk",
    "ac.uk",
    "com.au",
    "net.au",
    "org.au",
    "com.cn",
    "net.cn",
    "org.cn",
    "com.hk",
    "com.tw",
    "co.jp",
    "co.nz",
    "co.kr",
    "com.sg",
    "com.my",
    "com.br",
    "com.mx",
    "com.ar",
    "co.za",
    "co.in",
    "com.tr",
}


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_input(raw):
    """Return (scheme, host) from a domain or any store URL."""
    raw = raw.strip()
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", raw):
        raw = "https://" + raw
    parts = urlsplit(raw)
    host = (parts.netloc or "").split("@")[-1].lower().rstrip(".")
    if not host:
        raise ValueError("cannot parse host from %r" % raw)
    scheme = (
        parts.scheme.lower() if parts.scheme.lower() in ("http", "https") else "https"
    )
    return scheme, host


def strip_www(host):
    host = host.split(":")[0]
    return host[4:] if host.startswith("www.") else host


def registrable_domain(host):
    """Very small public-suffix approximation; None for IPs / localhost."""
    host = strip_www(host)
    if re.match(r"^[\d.]+$", host) or "." not in host or host.endswith(".local"):
        return None
    labels = host.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


class _Redirect(HTTPRedirectHandler):
    max_redirections = 8


def make_ssl_context():
    """Default verifying context, minus VERIFY_X509_STRICT.

    Python 3.13+ turns on VERIFY_X509_STRICT by default, which rejects some
    public CA chains that browsers accept (e.g. "Basic Constraints of CA cert
    not marked critical"). Clearing only that flag is a compatibility fix, not
    a downgrade: chain verification (CERT_REQUIRED) and hostname checking stay
    on. On Windows the context reads the system store via load_default_certs,
    so a fresh context also picks up roots Windows downloaded meanwhile.
    """
    ctx = ssl.create_default_context()
    strict = getattr(ssl, "VERIFY_X509_STRICT", None)
    if strict is not None:
        ctx.verify_flags &= ~strict
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def site_root(url):
    """``scheme://host[:port]/`` of url, or None when it is unsafe to quote."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return None
    if not re.match(r"^[A-Za-z0-9.\-]+(:\d+)?$", parts.netloc or ""):
        return None
    return "%s://%s/" % (parts.scheme, parts.netloc)


def refresh_windows_roots(url, run=None):
    """Ask Windows to fetch missing root certificates for url's site.

    A fresh Windows install downloads trusted roots on demand (AuthRoot
    auto-update) only when a Windows TLS client meets them; OpenSSL never
    triggers that. One PowerShell HEAD request does. Returns True when
    PowerShell ran (whatever the HTTP result), False when it is unavailable.
    """
    root = site_root(url)
    if root is None:
        return False
    run = run or subprocess.run
    script = (
        "try { Invoke-WebRequest -UseBasicParsing -Method Head -Uri '%s' "
        "-TimeoutSec 15 | Out-Null } catch {}" % root
    )
    try:
        run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=WIN_ROOT_REFRESH_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return True  # it ran; the roots may still have been fetched
    except (OSError, ValueError):
        return False
    return True


class Fetcher(object):
    def __init__(self, timeout=20.0, delay=0.5, retries=2):
        self.timeout = timeout
        self.delay = delay
        self.retries = retries
        self._refreshed_hosts = set()
        self._build_opener()
        self._last = 0.0

    def _build_opener(self):
        self.ctx = make_ssl_context()  # verification stays on
        self.opener = build_opener(HTTPSHandler(context=self.ctx), _Redirect())

    def _try_cert_refresh(self, url):
        """Windows only, at most once per host: refresh roots, rebuild context."""
        if sys.platform != "win32":
            return False
        host = (urlsplit(url).hostname or "").lower()
        if not host or host in self._refreshed_hosts:
            return False
        self._refreshed_hosts.add(host)
        if not refresh_windows_roots(url):
            return False
        self._build_opener()
        return True

    def _sleep_polite(self):
        wait = self.delay - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)

    def get(self, url, accept):
        """Return dict(status, body(bytes|None), final_url, elapsed_ms, note, net_error)."""
        attempt = 0
        note = ""
        refreshed = False
        while True:
            self._sleep_polite()
            t0 = time.time()
            status, body, final_url, headers, err = None, None, url, {}, None
            req = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": accept,
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate",
                },
            )
            try:
                resp = self.opener.open(req, timeout=self.timeout)
                status = resp.getcode()
                final_url = resp.geturl()
                headers = resp.headers
                body = resp.read(MAX_BYTES + 1)
                resp.close()
            except HTTPError as e:
                status = e.code
                final_url = e.geturl() or url
                headers = e.headers or {}
                try:
                    body = e.read(MAX_BYTES + 1)
                except Exception:  # noqa: BLE001
                    body = b""
                finally:
                    e.close()
            except (
                URLError,
                http.client.HTTPException,
                socket.timeout,
                ssl.SSLError,
                ConnectionError,
                OSError,
            ) as e:
                err = e
            elapsed = int((time.time() - t0) * 1000)
            self._last = time.time()
            if body is not None:
                body = decode_body(body, headers)
                if len(body) > MAX_BYTES:
                    body = body[:MAX_BYTES]
                    note = "body truncated at %d bytes" % MAX_BYTES
            if err is not None:
                note = describe_error(err)
                if refreshed:
                    note += "; " + WIN_ROOT_REFRESH_NOTE
                if is_cert_error(err) and not refreshed and self._try_cert_refresh(url):
                    refreshed = True
                    continue
                if is_cert_error(err) or attempt >= self.retries:
                    return {
                        "status": None,
                        "body": None,
                        "final_url": url,
                        "elapsed_ms": elapsed,
                        "note": note,
                    }
                attempt += 1
                time.sleep(1.5 * (2 ** (attempt - 1)))
                continue
            if (status == 429 or (status is not None and status >= 500)) and (
                attempt < self.retries
            ):
                attempt += 1
                wait = 1.5 * (2 ** (attempt - 1))
                ra = headers.get("Retry-After") if hasattr(headers, "get") else None
                if ra and str(ra).isdigit():
                    wait = min(float(ra), 15.0)
                time.sleep(wait)
                continue
            if refreshed:
                note = (note + "; " if note else "") + WIN_ROOT_REFRESH_NOTE
            if attempt:
                note = (note + "; " if note else "") + "retried %d" % attempt
            return {
                "status": status,
                "body": body,
                "final_url": final_url,
                "elapsed_ms": elapsed,
                "note": note,
            }


def decode_body(body, headers):
    enc = ""
    if hasattr(headers, "get"):
        enc = (headers.get("Content-Encoding") or "").lower()
    try:
        if "gzip" in enc or body[:2] == b"\x1f\x8b":
            return gzip.decompress(body)
        if "deflate" in enc:
            try:
                return zlib.decompress(body)
            except zlib.error:
                return zlib.decompress(body, -zlib.MAX_WBITS)
    except (OSError, zlib.error, EOFError):
        return body
    return body


def is_cert_error(err):
    reason = getattr(err, "reason", err)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return True
    return "CERTIFICATE_VERIFY_FAILED" in str(err)


def describe_error(err):
    if is_cert_error(err):
        return CERT_NOTE
    reason = getattr(err, "reason", err)
    if isinstance(reason, socket.timeout) or "timed out" in str(reason):
        return "timeout"
    return "%s: %s" % (type(reason).__name__, reason)


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
    def __init__(self, target, out_dir, depth, max_pages, skip_offsite, fetcher):
        self.scheme, self.host = normalize_input(target)
        self.input = target
        self.base = "%s://%s" % (self.scheme, self.host)
        self.domain = strip_www(self.host)
        self.out = out_dir
        self.raw = os.path.join(out_dir, "raw")
        self.depth = depth
        self.max_pages = max_pages
        self.skip_offsite = skip_offsite
        self.f = fetcher
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
        children = [summarize.html.unescape(u) for u in locs if "sitemap_products" in u]
        for i, url in enumerate(children[:SITEMAP_CHILD_CAP], 1):
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
    ap.add_argument("--skip-offsite", action="store_true", help="skip RDAP and Wayback")
    ap.add_argument("--delay", type=float, default=0.5, help="seconds between requests")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--no-summarize", action="store_true")
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
    fetcher = Fetcher(timeout=args.timeout, delay=max(args.delay, 0.0))
    col = Collector(
        args.store, out, args.depth, max(args.max_pages, 1), args.skip_offsite, fetcher
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
    certs = [r for r in col.requests if CERT_NOTE in (r.get("note") or "")]
    if certs:
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
