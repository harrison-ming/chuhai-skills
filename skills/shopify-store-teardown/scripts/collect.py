#!/usr/bin/env python3
"""Collect public storefront data of a Shopify store into ``<out>/raw/``.

Fetches the public endpoints defined in references/data-contract.md
(meta.json, paginated products.json, collections.json, sitemap, homepage,
best-selling order, RDAP, Wayback CDX; plus policy and about pages at L1),
records every request in ``raw/_status.json`` and then runs summarize.py
unless ``--no-summarize`` is given. A failing request never aborts the run.

CLI:
    python collect.py <store_url_or_domain> [--out DIR] [--depth L0|L1]
                      [--max-pages 100] [--skip-offsite] [--delay 0.5]
                      [--timeout 20] [--no-summarize]

Default ``--out`` is ``./store-teardown/<domain>/<YYYY-MM-DD>/``.
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
import sys
import time
import zlib
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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
CERT_NOTE = "证书校验失败, macOS python.org 版本可运行 Install Certificates.command"
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


class Fetcher(object):
    def __init__(self, timeout=20.0, delay=0.5, retries=2):
        self.timeout = timeout
        self.delay = delay
        self.retries = retries
        self.ctx = ssl.create_default_context()  # verification stays on
        self.opener = build_opener(HTTPSHandler(context=self.ctx), _Redirect())
        self._last = 0.0

    def _sleep_polite(self):
        wait = self.delay - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)

    def get(self, url, accept):
        """Return dict(status, body(bytes|None), final_url, elapsed_ms, note, net_error)."""
        attempt = 0
        note = ""
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
        self.fetch(
            "best_selling",
            self.base + "/collections/all?sort_by=best-selling",
            "best-selling.html",
            "html",
        )
        if self.depth == "L1":
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
            "requests": self.requests,
        }
        with open(os.path.join(self.raw, "_status.json"), "w", encoding="utf-8") as fh:
            json.dump(status, fh, ensure_ascii=False, indent=2)
            fh.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Collect public Shopify storefront data.")
    ap.add_argument("store", help="domain or any URL of the store")
    ap.add_argument(
        "--out", help="run directory (default ./store-teardown/<domain>/<date>/)"
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
    out = args.out or os.path.join(
        "store-teardown", strip_www(host), datetime.date.today().isoformat()
    )
    out = os.path.abspath(os.path.expanduser(out))
    fetcher = Fetcher(timeout=args.timeout, delay=max(args.delay, 0.0))
    col = Collector(
        args.store, out, args.depth, max(args.max_pages, 1), args.skip_offsite, fetcher
    )
    print("开始采集 %s (深度 %s), 输出到 %s" % (col.base, args.depth, out))
    col.run()

    counts = {}
    for r in col.requests:
        counts[r["result"]] = counts.get(r["result"], 0) + 1
    print(
        "请求 %d 个: %s"
        % (len(col.requests), ", ".join("%s=%d" % kv for kv in sorted(counts.items())))
    )
    certs = [r for r in col.requests if r.get("note") == CERT_NOTE]
    if certs:
        print("提示: %s" % CERT_NOTE)
    if args.no_summarize:
        print("已跳过汇总. 之后可运行: python summarize.py %s" % out)
        return 0
    try:
        summary = summarize.summarize_run(out)
    except Exception as e:  # noqa: BLE001 - raw data is kept even if summarize fails
        print("汇总失败 (原始数据已保存): %s" % e, file=sys.stderr)
        return 1
    print(summarize.human_digest(summary))
    print("产物: %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
