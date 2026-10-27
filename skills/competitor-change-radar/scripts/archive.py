"""Optional web-archive baseline (archive.org) for a store's first run.

Budget per bootstrap: at most 8 archive requests (3 index lookups + up to 5
original files), at least 5 seconds apart; a 429/503 is retried once after
the server's Retry-After (capped), then we give up. Archived product data is
often only the first page, so an archive snapshot never supports removal
findings and every change based on it is at most [Shown on page].

Lookup: CDX ``url=<domain>/products.json`` (prefix), ``<domain>/`` (exact)
and ``<domain>/policies/`` (prefix), status 200, window 28-120 days back;
the capture closest to the target date (default 30 days back) wins.
Originals: ``https://web.archive.org/web/<timestamp>id_/<original>`` (raw,
not rewritten).
Python 3.8+, standard library only.
"""

import datetime
import json
import os
import shutil
import sys
from urllib.parse import quote, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect  # noqa: E402
import snapshot  # noqa: E402
from _shared import fetch, output  # noqa: E402

CDX = "https://web.archive.org/cdx/search/cdx"
RAW_URL = "https://web.archive.org/web/%sid_/%s"
MAX_REQUESTS = 8
MIN_DELAY = 5.0
WINDOW_DAYS = (28, 120)
TARGET_DAYS = 30
POLICY_ORDER = ["refund-policy", "shipping-policy", "terms-of-service"]


class Budget(object):
    def __init__(self, fetcher, limit=MAX_REQUESTS):
        self.f = fetcher
        self.limit = limit
        self.used = 0
        self.log = []

    def get(self, key, url, accept):
        if self.used >= self.limit:
            return None
        self.used += 1
        r = self.f.get(url, accept)
        self.log.append(
            {"key": key, "url": url, "http_status": r["status"], "note": r["note"]}
        )
        return r


def archive_down(body):
    """archive.org answers 200 with an HTML notice while it is offline."""
    head = (body or b"")[:4000].decode("utf-8", "replace")
    return "Internet Archive: Temporarily Offline" in head


def ts_to_dt(ts):
    return datetime.datetime.strptime(ts[:14].ljust(14, "0"), "%Y%m%d%H%M%S").replace(
        tzinfo=datetime.timezone.utc
    )


def cdx_url(target, match, start, end):
    return (
        "%s?url=%s&matchType=%s&filter=statuscode:200&fl=timestamp,original,digest&from=%s&to=%s&output=json&collapse=digest&limit=1000"
        % (
            CDX,
            quote(target, safe="/"),
            match,
            start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
        )
    )


def parse_cdx(body):
    try:
        rows = json.loads((body or b"").decode("utf-8", "replace") or "[]")
    except ValueError:
        return None
    out = []
    for row in rows[1:] if rows and rows[0] and rows[0][0] == "timestamp" else rows:
        if isinstance(row, list) and len(row) >= 2 and str(row[0]).isdigit():
            out.append((str(row[0]), str(row[1])))
    return out


def closest(rows, target_dt):
    if not rows:
        return None
    return min(
        rows, key=lambda r: (abs((ts_to_dt(r[0]) - target_dt).total_seconds()), r[0])
    )


def is_first_products_page(original):
    parts = urlsplit(original)
    if not parts.path.rstrip("/").endswith("/products.json"):
        return False
    q = dict(p.split("=", 1) for p in parts.query.split("&") if "=" in p)
    return q.get("page", "1") == "1"


def _capture(
    store, store_dir, lang, target_date=None, fetcher=None, today=None, log=print
):
    """Write a ``<date>-archive`` snapshot. Returns (id or None, info dict)."""
    domain = snapshot.domain_of(store)
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    if target_date:
        target = datetime.datetime.strptime(target_date, "%Y-%m-%d").date()
        start, end = (
            target - datetime.timedelta(days=45),
            target + datetime.timedelta(days=45),
        )
    else:
        target = today - datetime.timedelta(days=TARGET_DAYS)
        start, end = (
            today - datetime.timedelta(days=WINDOW_DAYS[1]),
            today - datetime.timedelta(days=WINDOW_DAYS[0]),
        )
    target_dt = datetime.datetime(
        target.year, target.month, target.day, tzinfo=datetime.timezone.utc
    )
    fetcher = fetcher or fetch.Fetcher(
        timeout=30.0, delay=MIN_DELAY, retries=1, lang=lang
    )
    fetcher.delay = max(fetcher.delay, MIN_DELAY)
    b = Budget(fetcher)
    info = {"result": "unknown", "requests": b.log, "target": target.isoformat()}

    r = b.get(
        "cdx_products",
        cdx_url("%s/products.json" % domain, "prefix", start, end),
        fetch.ACCEPT_JSON,
    )
    if r is None or r["status"] is None:
        info["note"] = "archive unreachable"
        return None, info
    if r["status"] != 200 or archive_down(r["body"]):
        info["note"] = (
            "archive temporarily offline"
            if archive_down(r["body"])
            else "archive index HTTP %s" % r["status"]
        )
        return None, info
    prod_rows = [
        x for x in (parse_cdx(r["body"]) or []) if is_first_products_page(x[1])
    ]
    pick_p = closest(prod_rows, target_dt)
    anchor = ts_to_dt(pick_p[0]) if pick_p else target_dt
    r = b.get(
        "cdx_homepage", cdx_url("%s/" % domain, "exact", start, end), fetch.ACCEPT_JSON
    )
    pick_h = (
        closest(parse_cdx(r["body"]) or [], anchor)
        if r and r["status"] == 200
        else None
    )
    r = b.get(
        "cdx_policies",
        cdx_url("%s/policies/" % domain, "prefix", start, end),
        fetch.ACCEPT_JSON,
    )
    pol_rows = (parse_cdx(r["body"]) or []) if r and r["status"] == 200 else []
    picks = []
    if pick_p:
        picks.append(("products_json", "products-1.json", "json", pick_p))
    if pick_h:
        picks.append(("homepage", "homepage.html", "html", pick_h))
    for name in POLICY_ORDER:
        rows = [
            x
            for x in pol_rows
            if urlsplit(x[1]).path.rstrip("/").endswith("/policies/" + name)
        ]
        pk = closest(rows, anchor)
        if pk:
            picks.append(
                (
                    "policy_" + name.replace("-", "_"),
                    "policy-%s.html" % name,
                    "html",
                    pk,
                )
            )
    if not pick_p and not pick_h:
        info["result"] = "not_found"
        info["note"] = "no archived capture in the window"
        return None, info
    base = snapshot.snap_root(store_dir, lang)
    os.makedirs(base, exist_ok=True)
    tmp = os.path.join(base, ".archive.partial")
    shutil.rmtree(tmp, ignore_errors=True)
    raw = os.path.join(tmp, output.raw_dirname(lang), "raw")
    os.makedirs(raw)
    requests, stamps = [], {}
    for key, fname, kind, (ts, original) in picks:
        url = RAW_URL % (ts, original)
        resp = b.get(
            key, url, fetch.ACCEPT_JSON if kind == "json" else fetch.ACCEPT_HTML
        )
        if resp is None:
            break
        result, cnote, keep = collect.classify(resp["status"], resp["body"], kind)
        if keep and archive_down(resp["body"]):
            result, cnote, keep = "error", "archive temporarily offline", False
        entry = fetch.status_entry(key, url, resp, result, fname, "archive %s" % ts)
        if cnote:
            entry["note"] = "; ".join(x for x in (entry["note"], cnote) if x)
        requests.append(entry)
        if keep:
            with open(os.path.join(raw, fname), "wb") as fh:
                fh.write(resp["body"])
            stamps[fname] = ts
    if "products-1.json" not in stamps and "homepage.html" not in stamps:
        shutil.rmtree(tmp, ignore_errors=True)
        info["result"] = "error"
        info["note"] = "archived files could not be read (%s)" % ", ".join(
            "%s %s" % (r["key"], r.get("http_status") or r.get("result"))
            for r in requests
        )
        return None, info
    # the snapshot is dated by what was actually saved (products first)
    anchor_ts = stamps.get("products-1.json") or stamps["homepage.html"]
    captured = ts_to_dt(anchor_ts).strftime("%Y-%m-%dT%H:%M:%SZ")
    day = "%s-%s-%s" % (anchor_ts[:4], anchor_ts[4:6], anchor_ts[6:8])
    sid = snapshot.next_id(store_dir, lang, day, "-archive")
    status = {
        "schema": "store-teardown/status/1",
        "domain": domain,
        "base_url": "https://%s" % domain,
        "depth": "archive",
        "started_at_utc": captured,
        "finished_at_utc": captured,
        "requests": requests,
    }
    with open(os.path.join(raw, "_status.json"), "w", encoding="utf-8") as fh:
        json.dump(status, fh, ensure_ascii=False, indent=1)
    snapshot.write_meta(
        tmp,
        {
            "domain": domain,
            "snapshot_id": sid,
            "captured_at_utc": captured,
            "source": "archive",
            "archive_timestamps": stamps,
            "archive_requests": b.used,
        },
    )
    snapshot.finish(tmp, os.path.join(base, sid), "archive")
    info["result"] = "ok"
    info["snapshot"] = sid
    log(
        (
            "已取到网页历史存档基线 %s (%d 个存档请求)"
            if lang == "zh"
            else "Web archive baseline %s (%d archive requests)"
        )
        % (sid, b.used)
    )
    return sid, info


ATTEMPT_FILE = "archive-attempt.json"


def capture_archive(
    store, store_dir, lang, target_date=None, fetcher=None, today=None, log=print
):
    """Write a ``<date>-archive`` snapshot. Returns (id or None, info dict).

    The outcome (also a failed one) is kept in ``<store>/archive-attempt.json``
    so the report can say why there is no archive baseline.
    """
    sid, info = _capture(store, store_dir, lang, target_date, fetcher, today, log)
    record = {k: v for k, v in info.items() if k != "requests"}
    record["requests_used"] = len(info.get("requests") or [])
    os.makedirs(store_dir, exist_ok=True)
    with open(os.path.join(store_dir, ATTEMPT_FILE), "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    return sid, info
