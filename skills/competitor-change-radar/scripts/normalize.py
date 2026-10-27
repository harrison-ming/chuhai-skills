#!/usr/bin/env python3
"""Turn one snapshot's raw files into a normalized ``snapshot.json``.

A snapshot folder is a slim shopify-store-teardown run::

    <snapshot>/
        snapshot.json            radar/snapshot/1 (this module writes it)
        原始数据/ (en: data/)     raw/, summary.json, products.tsv, evidence.jsonl

``snapshot.json`` keeps only the fields the diff needs and that are stable
between two captures (no ``updated_at``, no image URLs, no ordering). It is
derived deterministically from raw/ + summary.json, so it can be rebuilt at
any time (``radar.py renormalize``) without fetching again.

CLI:
    python normalize.py <snapshot folder> [--source live|archive|teardown_import]
Python 3.8+, standard library only.
"""

import argparse
import decimal
import hashlib
import json
import os
import re
import sys
import unicodedata
import zipfile
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import summarize  # noqa: E402

SCHEMA = "radar/snapshot/1"
TOOL_VERSION = "0.1.0"
SNAPSHOT_FILE = "snapshot.json"
META_FILE = "snapshot-meta.json"  # written by snapshot.py / archive.py
RAW_DIRNAMES = ("原始数据", "data")
POLICY_NAMES = list(summarize.POLICY_NAMES)
STATUSES = ("complete", "partial", "blocked", "not_shopify")
CENT = decimal.Decimal("0.01")

# Homepage promo wording (lower-cased line must match one). The list may grow
# but never shrink; references/method.md mirrors it and a test keeps both equal.
PROMO_PATTERNS = [
    r"\d{1,2}\s?% off",
    r"save \$?\d+",
    r"buy \d+ get",
    r"free (?:shipping|gift)",
    r"code[: ]+[a-z0-9]{3,}",
    r"\bsale\b",
    r"black friday|cyber monday|bfcm|prime day|singles day|clearance|bundle",
    r"折|满减|包邮|优惠码",
]
PROMO_RE = re.compile("|".join("(?:%s)" % p for p in PROMO_PATTERNS), re.I)
# Banners are short; long lines that mention "sale" are legal text, e.g.
# "opt out of the sale or sharing of personal information".
PROMO_MAX_LINE = 120
PROMO_EXCLUDE_RE = re.compile(
    r"personal (?:information|data)|opt[ -]out|privacy|cookie|do not sell", re.I
)
# Homepage lines dropped as noise before any comparison.
NOISE_RES = [
    re.compile(r"^[\d\s:.,/%-]+$"),  # bare numbers, countdown digits, dates
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b"),  # countdown timers
    re.compile(r"(?i)csrf|token|nonce|session"),
    re.compile(r"(?i)^(?:©|\(c\)|copyright)|\b(?:19|20)\d{2}\b.*(?:©|rights reserved)"),
    re.compile(r"(?i)^(?:cart|bag|basket)\s*\(?\d*\)?$|^\d+\s+items?$"),
    re.compile(
        r"(?i)^(?:regular|sale|unit|from|now|was)?\s*(?:price)?\s*[:]?\s*"
        r"(?:[$€£¥]|usd|cad|eur|gbp|aud)?\s*\d[\d,.]*\s*(?:usd|cad|eur|gbp|aud)?"
        r"(?:\s*(?:-|to|/)\s*(?:[$€£¥])?\d[\d,.]*)?$"
    ),
    re.compile(
        r"(?i)^(?:regular price|sale price|unit price|per|sold out|add to cart)$"
    ),
]
MIN_LINE, MAX_LINE = 3, 300
LAST_UPDATED_RE = re.compile(
    r"(?i)^(?:last (?:updated|modified|revised)|updated on|effective date|最后更新|更新日期)"
)
DATE_RE = re.compile(
    r"(?i)\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d{1,2},? \d{4})\b"
)
# CJK sentence ends written as escapes so the file stays half-width.
SENTENCE_SPLIT_RE = re.compile(
    "(?<=[.!?\u3002\uff01\uff1f])\\s+|(?<=[\u3002\uff01\uff1f])|\\n+"
)


def find_raw_parent(snap_dir):
    """The raw-data folder (contains raw/) of a snapshot, or None."""
    for name in RAW_DIRNAMES:
        cand = os.path.join(snap_dir, name)
        if os.path.isdir(os.path.join(cand, "raw")) or os.path.isfile(
            os.path.join(cand, "raw.zip")
        ):
            return cand
    if os.path.isdir(os.path.join(snap_dir, "raw")):
        return snap_dir
    return None


def ensure_raw(data_dir):
    """Unpack raw.zip (made by retention) when raw/ is gone. Returns raw/ path."""
    raw = os.path.join(data_dir, "raw")
    packed = os.path.join(data_dir, "raw.zip")
    if not os.path.isdir(raw) and os.path.isfile(packed):
        with zipfile.ZipFile(packed) as zf:
            zf.extractall(data_dir)
    return raw


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def money(value):
    """Canonical 2-decimal price string, or None."""
    if value is None or value == "":
        return None
    try:
        d = decimal.Decimal(str(value).strip())
    except (decimal.InvalidOperation, ValueError):
        return None
    if not d.is_finite():
        return None
    return str(d.quantize(CENT, rounding=decimal.ROUND_HALF_UP))


def clean_line(line):
    return re.sub(r"\s+", " ", line or "").strip()


def is_noise(line):
    if len(line) < MIN_LINE or len(line) > MAX_LINE:
        return True
    return any(r.search(line) for r in NOISE_RES)


def homepage_lines(html_text):
    """(other_lines, promo_lines): sorted unique, noise removed, lower-cased."""
    if not html_text:
        return [], []
    text = summarize.html_to_text(html_text)
    other, promo = set(), set()
    for raw in text.splitlines():
        line = clean_line(raw)
        if not line or is_noise(line):
            continue
        low = line.lower()
        is_promo = (
            len(low) <= PROMO_MAX_LINE
            and PROMO_RE.search(low)
            and not PROMO_EXCLUDE_RE.search(low)
        )
        (promo if is_promo else other).add(low)
    return sorted(other), sorted(promo)


def norm_policy_text(text):
    """NFKC, collapse whitespace, drop 'Last updated' lines and dates."""
    text = unicodedata.normalize("NFKC", text or "")
    out = []
    for line in text.splitlines():
        line = clean_line(line)
        if not line or LAST_UPDATED_RE.search(line):
            continue
        out.append(DATE_RE.sub("", line).strip())
    return "\n".join(x for x in out if x)


def sentences(text):
    parts = (clean_line(s) for s in SENTENCE_SPLIT_RE.split(text or ""))
    return [s for s in parts if len(s) >= 3]


RETURN_DAYS_RE = re.compile(
    r"(\d{1,3})[\s-]*(?:calendar\s+|business\s+|working\s+)?days?\b", re.I
)
FREE_SHIP_RE = re.compile(
    r"free (?:standard |ground |domestic )?shipping (?:on|for) (?:all |any )?"
    r"(?:orders?|purchases?) (?:over|above|of|exceeding)\s*(?:[$€£]|usd |cad )?\s*"
    r"(\d+(?:\.\d+)?)",
    re.I,
)
PROCESSING_RE = re.compile(
    r"(\d{1,2})\s*(?:-|to|–)\s*(\d{1,2})\s*(?:business|working)\s+days?", re.I
)
RESTOCK_RE = re.compile(
    r"(\d{1,2})\s?%\s*restocking fee|restocking fee of\s*(\d{1,2})\s?%", re.I
)
CUSTOMER_PAYS_RE = re.compile(
    r"(?:customers?|you|buyers?)(?:'s| are| is| will be)? (?:responsible for|pays?|cover)"
    r"[^.]{0,40}(?:return )?shipping|return shipping (?:costs? )?(?:is|are) "
    r"(?:the )?(?:customer|buyer)'?s? responsibility|deducted from (?:your|the) refund",
    re.I,
)
STORE_PAYS_RE = re.compile(
    r"free returns|prepaid return label|we (?:will )?(?:cover|pay) (?:the )?return shipping",
    re.I,
)


def key_terms(sents):
    """Plain facts that matter most to a seller, by regex (English first)."""
    terms = {}
    for s in sents:
        low = s.lower()
        if "return_days" not in terms and re.search(r"return|refund|exchange", low):
            m = RETURN_DAYS_RE.search(s)
            if m and 0 < int(m.group(1)) <= 365:
                terms["return_days"] = int(m.group(1))
        if "free_shipping_over" not in terms:
            m = FREE_SHIP_RE.search(s)
            if m:
                terms["free_shipping_over"] = money(m.group(1))
        if "processing_days" not in terms:
            m = PROCESSING_RE.search(s)
            if m and re.search(r"process|ship|dispatch|fulfil", low):
                terms["processing_days"] = "%s-%s" % (m.group(1), m.group(2))
        if "restocking_fee_pct" not in terms:
            m = RESTOCK_RE.search(s)
            if m:
                terms["restocking_fee_pct"] = int(m.group(1) or m.group(2))
        if "return_shipping_paid_by" not in terms:
            if CUSTOMER_PAYS_RE.search(s):
                terms["return_shipping_paid_by"] = "customer"
            elif STORE_PAYS_RE.search(s):
                terms["return_shipping_paid_by"] = "store"
    return terms


def norm_variant(v):
    opts = [v.get(k) for k in ("option1", "option2", "option3")]
    opts = [str(o) for o in opts if o not in (None, "", "Default Title")]
    avail = v.get("available")
    return {
        "title": v.get("title") or "",
        "sku": (v.get("sku") or "").strip(),
        "options": opts,
        "price": money(v.get("price")),
        "compare_at_price": money(v.get("compare_at_price")),
        "available": avail if isinstance(avail, bool) else None,
    }


def norm_product(p):
    variants = {}
    for i, v in enumerate(p.get("variants") or []):
        if not isinstance(v, dict):
            continue
        vid = v.get("id")
        key = str(vid) if vid is not None else "idx-%d" % i
        variants[key] = norm_variant(v)
    tags = p.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    price = summarize.lowest_price(p)
    return {
        "handle": p.get("handle") or "",
        "title": clean_line(p.get("title") or ""),
        "vendor": clean_line(p.get("vendor") or ""),
        "product_type": clean_line(p.get("product_type") or ""),
        "tags": sorted({t for t in tags if t}),
        "created_at": p.get("created_at") or None,
        "published_at": p.get("published_at") or None,
        "virtual": not summarize.is_real_product(p, price),
        "variants": variants,
    }


def network_only(status):
    """ "network" when every request failed before any HTTP answer (DNS,
    no route, sandbox without internet): says nothing about the store."""
    reqs = [r for r in (status or {}).get("requests") or [] if isinstance(r, dict)]
    if reqs and all(
        r.get("http_status") is None and r.get("result") == "error" for r in reqs
    ):
        return "network"
    return None


def collect_page_size():
    return 250  # collect.PRODUCTS_LIMIT, also used for collections


def snapshot_status(identity, prod_ep, homepage_ep, products):
    if identity == "not_shopify":
        return "not_shopify"
    results = {prod_ep.get("result"), homepage_ep.get("result")}
    if prod_ep.get("result") != "ok" and "blocked" in results:
        return "blocked"
    if prod_ep.get("result") != "ok" and homepage_ep.get("result") != "ok":
        return "blocked"
    if (
        prod_ep.get("result") == "ok"
        and prod_ep.get("pagination_complete")
        and identity in ("confirmed", "probable")
        and products
    ):
        return "complete"
    return "partial"


def path_of(href):
    parts = urlsplit(href or "")
    return parts.path or "/"


def snapshot_id_of(snap_dir, meta_id=None):
    """Folder name; while still in a ``.<x>.partial`` folder, the id the
    writer recorded in snapshot-meta.json."""
    name = os.path.basename(os.path.normpath(snap_dir))
    if name.startswith(".") and name.endswith(".partial"):
        name = meta_id or name[1 : -len(".partial")]
    return name


def build_snapshot(snap_dir, source=None):
    """Return the radar/snapshot/1 dict for ``snap_dir`` (does not write)."""
    data_dir = find_raw_parent(snap_dir)
    if data_dir is None:
        raise ValueError("no raw data under %s" % snap_dir)
    ensure_raw(data_dir)
    meta = load_json(os.path.join(snap_dir, META_FILE), {}) or {}
    summary = load_json(os.path.join(data_dir, "summary.json"))
    if not isinstance(summary, dict):
        summary = summarize.summarize_run(data_dir, meta.get("domain"))
    run = summarize.Run(data_dir, meta.get("domain") or summary.get("domain"))
    products_raw, prod_ep = summarize.load_products(run)
    eps = summary.get("endpoints") or {}
    identity = (summary.get("identity") or {}).get("status") or "unconfirmed"
    shop = summary.get("shop") or {}
    theme = summary.get("theme") or {}
    sig = summary.get("html_signals") or {}

    products = {}
    for p in products_raw:
        pid = p.get("id")
        key = str(pid) if pid is not None else "handle:%s" % (p.get("handle") or "")
        products[key] = norm_product(p)

    collections = {}
    col_ep = eps.get("collections_json") or {}
    from_cols = []
    col_lens = []
    for _n, fname in summarize.list_pages(run.raw, "collections-", ".json"):
        obj, _state, _note = summarize.parse_json_lenient(
            summarize.read_text(run.path(fname)), "collections"
        )
        if isinstance(obj, dict) and isinstance(obj.get("collections"), list):
            from_cols.extend(c for c in obj["collections"] if isinstance(c, dict))
            col_lens.append(len(obj["collections"]))
    # collect.py stops after a few full pages: a full last page means the
    # list was cut, so collections are not comparable (cut-off order varies)
    cols_complete = bool(col_lens) and col_lens[-1] < collect_page_size()
    for c in from_cols:
        if c.get("handle"):
            collections[c["handle"]] = {
                "id": c.get("id"),
                "title": clean_line(c.get("title") or ""),
                "products_count": c.get("products_count"),
            }

    home_ep = eps.get("homepage") or {}
    home_html = None
    if home_ep.get("result") == "ok":
        home_html = summarize.read_text(run.path("homepage.html"))
    other, promo = homepage_lines(home_html)

    policies, checked = {}, {}
    for name in POLICY_NAMES:
        ep = eps.get("policy_" + name.replace("-", "_")) or {}
        checked[name] = ep.get("result") or "not_checked"
        txt = summarize.read_text(run.path("policy-%s.txt" % name))
        if ep.get("result") != "ok" or txt is None:
            continue
        norm = norm_policy_text(txt)
        sents = sentences(norm)
        policies[name] = {
            "text_sha256": hashlib.sha256(norm.encode("utf-8")).hexdigest(),
            "sentences": sents,
            "key_terms": key_terms(sents),
        }

    coverage_products = "none"
    if prod_ep.get("result") == "ok":
        complete = bool(prod_ep.get("pagination_complete")) and not prod_ep.get(
            "truncated"
        )
        coverage_products = "full" if complete else "partial"
    source = source or meta.get("source") or "live"
    if source == "archive":
        coverage_products = "partial" if products else "none"
    status = snapshot_status(identity, prod_ep, home_ep, products)
    if source == "archive":
        # a 429 from the archive says nothing about the store itself
        status = "partial" if (products or home_html is not None) else "blocked"
    loc = sig.get("localization_selector")
    out = {
        "schema": SCHEMA,
        "tool_version": TOOL_VERSION,
        "domain": meta.get("domain") or summary.get("domain"),
        "snapshot_id": snapshot_id_of(snap_dir, meta.get("snapshot_id")),
        "captured_at_utc": meta.get("captured_at_utc")
        or summary.get("captured_at_utc"),
        "source": source,
        "status": status,
        "identity_status": identity,
        "fetch_error": network_only(run.status),
        "coverage": {
            "products": coverage_products,
            "pagination_complete": bool(
                prod_ep.get("result") == "ok"
                and prod_ep.get("pagination_complete")
                and source != "archive"
            ),
            "product_pages": prod_ep.get("pages") or 0,
            "policies": sorted(policies),
            "policies_checked": checked,
            "homepage": home_html is not None,
            "collections": col_ep.get("result") == "ok" and cols_complete,
            "meta": (eps.get("meta_json") or {}).get("result") == "ok",
        },
        "endpoints": {
            k: (v or {}).get("result")
            for k, v in sorted(eps.items())
            if k in ("meta_json", "products_json", "homepage", "collections_json")
        },
        "shop": {
            "name": shop.get("name"),
            "currency": shop.get("currency"),
            "currency_source": "meta"
            if shop.get("source") == "meta.json"
            else ("homepage" if shop.get("currency") else None),
            "country": shop.get("country"),
            "ships_to_count": shop.get("ships_to_count"),
        },
        "theme": {
            "name": theme.get("name"),
            "schema_name": theme.get("schema_name"),
            "schema_version": theme.get("schema_version"),
            "theme_store_id": theme.get("theme_store_id"),
        },
        "signals": {
            "apps_possible": sorted(
                {a.get("name") for a in sig.get("apps_possible") or [] if a.get("name")}
            ),
            "tracking_tags": sorted(set(sig.get("tracking_tags") or [])),
            "hreflang": sorted(
                {(h[0], path_of(h[1])) for h in sig.get("hreflang") or [] if h}
            ),
            "localization_form": (
                "observed"
                if loc == "present"
                else ("not_observed" if loc == "not_observed" else None)
            ),
            "payment_icons_observed": sorted(
                set(sig.get("payment_icons_observed") or [])
            ),
        },
        "products": products,
        "collections": collections,
        "homepage": {"lines": other, "promo_lines": promo},
        "policies": policies,
    }
    if meta.get("archive_timestamps"):
        out["archive_timestamps"] = meta["archive_timestamps"]
    if meta.get("imported_from"):
        out["imported_from"] = meta["imported_from"]
    out["signals"]["hreflang"] = [list(x) for x in out["signals"]["hreflang"]]
    return out


def write_snapshot(snap_dir, source=None):
    snap = build_snapshot(snap_dir, source)
    with open(os.path.join(snap_dir, SNAPSHOT_FILE), "w", encoding="utf-8") as fh:
        # compact: a large store (10k products) is ~20 MB indented
        json.dump(snap, fh, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fh.write("\n")
    return snap


def load_snapshot(snap_dir):
    snap = load_json(os.path.join(snap_dir, SNAPSHOT_FILE))
    if not isinstance(snap, dict) or snap.get("schema") != SCHEMA:
        raise ValueError("not a %s file in %s" % (SCHEMA, snap_dir))
    return snap


def main(argv=None):
    ap = argparse.ArgumentParser(description="Normalize one radar snapshot.")
    ap.add_argument("snapshot_dir")
    ap.add_argument("--source", choices=["live", "archive", "teardown_import"])
    args = ap.parse_args(argv)
    snap = write_snapshot(os.path.abspath(args.snapshot_dir), args.source)
    print(
        "%s: %s, %d products, status %s"
        % (snap["snapshot_id"], snap["source"], len(snap["products"]), snap["status"])
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
