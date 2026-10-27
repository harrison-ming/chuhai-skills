#!/usr/bin/env python3
"""Mode P (paste / export): turn text the seller provides into raw/.

For stores still behind the password page (never ask for the password) or
sellers who only want the texts checked. Input is a folder (or one file) of
.txt / .md / .html files plus an optional product export CSV (Shopify admin
Products > Export). Files are classified by name first, then by content
keywords, into the same raw/ layout audit_collect.py writes, so audit.py
runs unchanged. Pasted text is the store's own claim: findings from it are
labelled [店铺自称] and on-page checks become [查不到].

CLI:
    python paste_ingest.py <folder_or_file> --name <store name or domain>
        [--markets "EU,AU"] [--no-ads] [--lang zh|en] [--out-root DIR | --out DIR]
        [--no-audit]
Python 3.8+, standard library only.
"""

import argparse
import csv
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pages  # noqa: E402
import skill_config as cfg  # noqa: E402
from _shared import fetch, i18n, output  # noqa: E402

TEXT_EXT = (".txt", ".md", ".html", ".htm", ".text")
# (role, policy handle or None, file-name regex, content regex)
KINDS = [
    (
        "policy",
        "refund-policy",
        r"refund|return|退货|退款|widerruf|retour",
        r"\b(refund|return)s?\b.{0,80}\b(days?|within)\b|退货|退款",
    ),
    (
        "policy",
        "shipping-policy",
        r"ship|deliver|运费|物流|发货|versand",
        r"\b(shipping|delivery|dispatch)\b.{0,80}\b(days?|business)\b|运费|发货",
    ),
    (
        "policy",
        "privacy-policy",
        r"privacy|隐私|datenschutz",
        r"personal (data|information)|privacy|隐私|个人信息",
    ),
    ("policy", "terms-of-service", r"terms|tos|条款|agb", r"terms of service|服务条款"),
    (
        "policy",
        "legal-notice",
        r"impressum|imprint|legal|法律声明",
        r"impressum|legal notice",
    ),
    ("policy", "subscription-policy", r"subscri|订阅", r"subscription"),
    ("policy", "contact-information", r"contact-information", r"^$"),
    ("page", "contact", r"contact|联系", r"contact us|联系我们|email us"),
    (
        "page",
        "faq",
        r"faq|常见问题|questions|help",
        r"\bfaq\b|frequently asked|常见问题",
    ),
    ("page", "about", r"about|关于", r"about us|our story|关于我们"),
    ("home", None, r"home|首页|announcement|公告|footer|页脚", r"^$"),
]


def classify(name, text):
    low = name.lower()
    for role, handle, name_re, _c in KINDS:
        if re.search(name_re, low, re.I):
            return role, handle
    head = (text or "")[:3000]
    for role, handle, _n, content_re in KINDS:
        if content_re != "^$" and re.search(content_re, head, re.I | re.S):
            return role, handle
    return "page", "other"


def is_product_csv(path):
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            head = fh.readline()
    except OSError:
        return False
    return "Handle" in head and "Title" in head


def read_export(path):
    """Shopify product export CSV -> products.json-like list."""
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        rows = list(csv.DictReader(io.StringIO(fh.read())))
    out, by_handle = [], {}
    for row in rows:
        handle = (row.get("Handle") or "").strip()
        if not handle:
            continue
        p = by_handle.get(handle)
        if p is None:
            p = {
                "handle": handle,
                "title": row.get("Title") or "",
                "body_html": row.get("Body (HTML)") or "",
                "product_type": row.get("Type") or row.get("Product Category") or "",
                "tags": row.get("Tags") or "",
                "status": row.get("Status") or "",
                "variants": [],
            }
            by_handle[handle] = p
            out.append(p)
        elif row.get("Title") and not p["title"]:
            p["title"] = row["Title"]
        price = row.get("Variant Price")
        if price not in (None, ""):
            ship = (row.get("Variant Requires Shipping") or "").strip().lower()
            p["variants"].append(
                {
                    "price": price,
                    "compare_at_price": row.get("Variant Compare At Price") or None,
                    "requires_shipping": False if ship == "false" else True,
                }
            )
    return out


def ingest(src, raw):
    files = (
        [src]
        if os.path.isfile(src)
        else [
            os.path.join(src, n)
            for n in sorted(os.listdir(src))
            if not n.startswith(".")
        ]
    )
    os.makedirs(raw, exist_ok=True)
    index, used = [], set()
    products = []
    for path in files:
        name = os.path.basename(path)
        if name.lower().endswith(".csv") and is_product_csv(path):
            products.extend(read_export(path))
            index.append(
                {
                    "key": "products",
                    "file": "products.json",
                    "url": "paste:" + name,
                    "role": "products",
                    "result": "ok",
                }
            )
            continue
        if not name.lower().endswith(TEXT_EXT):
            index.append(
                {
                    "key": "skipped",
                    "file": None,
                    "url": "paste:" + name,
                    "role": "skipped",
                    "result": "skipped",
                }
            )
            continue
        text = pages.read_text(path) or ""
        role, handle = classify(name, text)
        if role == "policy" and handle in used:
            role, handle = "page", "other"
        if role == "policy":
            used.add(handle)
            rel = "policies/%s.html" % handle
            row = {"key": "policy:" + handle, "policy": handle, "role": "policy"}
        elif role == "home":
            rel = "homepage.html"
            row = {"key": "homepage", "role": "home"}
        else:
            rel = "pages/%s-%s.txt" % (handle, re.sub(r"[^A-Za-z0-9]+", "-", name)[:40])
            row = {"key": "page:" + name, "role": "page", "page_role": handle}
        dest = os.path.join(raw, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(text)
        row.update(
            {"file": rel, "url": "paste:" + name, "result": "ok", "source": "paste"}
        )
        index.append(row)
    if products:
        with open(os.path.join(raw, "products.json"), "w", encoding="utf-8") as fh:
            json.dump({"products": products}, fh, ensure_ascii=False)
        for p in products[:3]:
            rel = "pdp/%s.txt" % re.sub(r"[^A-Za-z0-9._-]+", "-", p["handle"])[:60]
            with open(_mk(raw, rel), "w", encoding="utf-8") as fh:
                fh.write(p["title"] + "\n\n" + pages.html_to_text(p["body_html"]))
            index.append(
                {
                    "key": "pdp:" + p["handle"],
                    "file": rel,
                    "role": "pdp",
                    "url": "paste:export",
                    "handle": p["handle"],
                    "result": "ok",
                    "source": "paste",
                }
            )
    return index


def _mk(raw, rel):
    path = os.path.join(raw, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def main(argv=None):
    output.safe_console()
    ap = argparse.ArgumentParser(
        description="Turn pasted texts / product export into raw/."
    )
    ap.add_argument("source", help="folder or single file with the texts")
    ap.add_argument("--name", required=True, help="store name or domain (folder name)")
    ap.add_argument("--markets", help='target markets, e.g. "EU,AU"')
    ap.add_argument("--no-ads", action="store_true")
    ap.add_argument("--lang", default="zh", choices=i18n.LANGS)
    ap.add_argument("--out-root", help="delivery root folder")
    ap.add_argument("--out", help="raw-data folder itself (tests)")
    ap.add_argument("--no-audit", action="store_true")
    args = ap.parse_args(argv)
    src = os.path.abspath(os.path.expanduser(args.source))
    if not os.path.exists(src):
        print("error: not found: %s" % src, file=sys.stderr)
        return 2
    subject = args.name
    try:
        subject = fetch.strip_www(fetch.normalize_input(args.name)[1])
    except ValueError:
        pass
    if args.out:
        data_dir = os.path.abspath(args.out)
        run_dir = os.path.dirname(data_dir)
    else:
        root = output.resolve_output_root(args.out_root, cfg.ROOT_NAME[args.lang])
        run_dir = output.create_run_dir(root, subject, cfg.RUN_LABEL[args.lang])
        data_dir = os.path.join(run_dir, output.raw_dirname(args.lang))
    raw = os.path.join(data_dir, "raw")
    os.makedirs(raw, exist_ok=True)
    output.write_readme(data_dir, cfg.README_TEXT[args.lang], args.lang)
    index = ingest(src, raw)
    status = {
        "schema": "%s/status/1" % cfg.NAME,
        "tool_version": cfg.TOOL_VERSION,
        "mode": "paste",
        "input": args.name,
        "domain": subject,
        "base_url": None,
        "user_markets": args.markets or None,
        "ads": not args.no_ads,
        "platform": "unknown",
        "password_protected": None,
        "started_at_utc": fetch.utc_now(),
        "finished_at_utc": fetch.utc_now(),
        "requests": [],
    }
    with open(os.path.join(raw, "_status.json"), "w", encoding="utf-8") as fh:
        json.dump(status, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(raw, "pages.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=2)
    print("files: %d" % len([x for x in index if x.get("result") == "ok"]))
    print(("运行目录: %s" if args.lang == "zh" else "run folder: %s") % run_dir)
    if args.no_audit:
        return 0
    import audit  # noqa: E402

    return audit.main([data_dir, "--lang", args.lang])


if __name__ == "__main__":
    sys.exit(main())
