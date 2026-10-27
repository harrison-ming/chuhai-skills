#!/usr/bin/env python3
"""Step 2 (offline): raw pages + questionnaire -> findings, fees, report draft.

CLI:
    python compute.py "<run folder or raw-data folder>" [--lang zh|en]

Works the same on files saved by collect.py (mode A) and on pages saved by
hand with a web-reading tool (mode B: same file names, .html / .md / .txt).
Writes into the raw-data folder:
    summary.json      status, warnings, store facts, counts, fee totals,
                      fix_list + fee_rows (the two CSV tables)
    findings.json     every check result (references/checks.md)
    fees.json         fee estimate (references/fees.md)
    evidence.jsonl    evidence index for the appendix
    report-draft.md   a complete first draft the agent reviews into report.md
Python 3.8+, standard library only.
"""

import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import draft  # noqa: E402
import fees as fees_mod  # noqa: E402
import pr_checks  # noqa: E402
import questionnaire  # noqa: E402
import signals  # noqa: E402
import skill_config as cfg  # noqa: E402
from _shared import datafile, evidence, fetch, i18n, output  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TEXT_EXTS = (".html", ".md", ".txt")
WHO = {
    "zh": {"self": "自己可改", "tech": "需要建站或技术人员"},
    "en": {"self": "You", "tech": "Your web developer"},
}
STATE = {
    "zh": {"issue": "待改", "unknown": "待确认"},
    "en": {"issue": "To fix", "unknown": "To confirm"},
}
PRIORITY_UNKNOWN = {"zh": "待确认", "en": "To confirm"}


def load_status(raw):
    try:
        with open(os.path.join(raw, "_status.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def find_file(raw, stem):
    """raw/<stem>.html|.md|.txt -> (file name, text) or (None, None)."""
    for ext in TEXT_EXTS:
        path = os.path.join(raw, stem + ext)
        text = signals.read_text(path)
        if text is not None:
            return stem + ext, text
    return None, None


def gather(data_dir):
    """Every storefront fact the rules need, from raw/ only."""
    raw = os.path.join(data_dir, "raw")
    status = load_status(raw)
    mode = status.get("mode") or "online"
    endpoints = {}
    for req in status.get("requests", []):
        key = req.get("key")
        if key == "page_product":
            continue
        prev = endpoints.get(key)
        if prev and prev.get("result") == "ok":
            continue  # keep the attempt that worked (contact / faq fallbacks)
        endpoints[key] = {
            "result": req.get("result"),
            "http_status": req.get("http_status"),
            "url": req.get("url"),
            "final_url": req.get("final_url"),
            "file": req.get("file"),
            "note": req.get("note"),
        }
    home_name, home = find_file(raw, "homepage")
    # Mode B: pages saved by hand (into an --offline run folder or without
    # any _status.json) are a manual collection, not an offline quote.
    manual = not status.get("requests") and (mode != "offline" or home_name is not None)
    # an online run whose homepage request failed but whose page was then
    # saved by hand (mode B after a network error) is a manual collection too
    home_req = endpoints.get("homepage", {}).get("result")
    if home_name is not None and home_req not in (None, "ok"):
        manual = True
    if manual:
        mode = "manual"
    if (
        home is not None
        and signals.looks_blocked(home)
        and len(home) < 30000
        and "shopify" not in home.lower()
    ):
        home = None
        endpoints.setdefault("homepage", {})["result"] = "blocked"
    if home is not None:
        ep = endpoints.setdefault("homepage", {"url": None})
        ep.setdefault("result", "ok")
        ep["result"] = "ok"
    meta_text = signals.read_text(os.path.join(raw, "meta.json"))
    meta, meta_state = signals.parse_json_lenient(meta_text)
    if not isinstance(meta, dict):
        meta = None
    if meta_text is not None or manual:
        endpoints.setdefault("meta_json", {})["result"] = (
            "ok" if meta else endpoints.get("meta_json", {}).get("result", meta_state)
        )
    prod_text = signals.read_text(os.path.join(raw, "products-1.json"))
    prod_obj, _ = signals.parse_json_lenient(prod_text)
    products = prod_obj.get("products") if isinstance(prod_obj, dict) else []
    products = [p for p in (products or []) if isinstance(p, dict)]
    if prod_text is not None:
        endpoints.setdefault("products_json", {})["result"] = (
            "ok" if prod_obj else "error"
        )
    markers = signals.shopify_markers(home)
    if meta and meta.get("myshopify_domain") and "myshopify.com" not in markers:
        markers.append("meta.json")
    identity = (
        signals.identity_status(markers, endpoints) if mode != "offline" else "unknown"
    )
    if identity == "not_shopify" and (
        mode == "manual"
        or not re.search(r"(?i)<(?:html|head|script|div)\b", home or "")
    ):
        # pages saved by a web-reading tool lose scripts and markup, so the
        # absence of Shopify markers proves nothing
        identity = "unconfirmed"
    policies = {}
    for name in signals.POLICY_NAMES:
        fname, src = find_file(raw, "policy-" + name)
        key = "policy_" + name.replace("-", "_")
        ep = endpoints.get(key, {})
        if src is not None and not (signals.looks_blocked(src) and len(src) < 30000):
            text = signals.main_text(src)
            if signals.looks_js_rendered(src, text):
                policies[name] = {
                    "state": "js_rendered",
                    "text": "",
                    "raw": "",
                    "chars": 0,
                    "file": fname,
                }
                ep["result"] = "js_rendered"
                endpoints[key] = ep
                continue
            policies[name] = {
                "state": "ok",
                "text": text,
                "raw": src,
                "chars": len(text),
                "file": fname,
            }
            ep["result"] = "ok"
            endpoints[key] = ep
        else:
            state = ep.get("result") or "missing"
            policies[name] = {
                "state": state,
                "text": "",
                "raw": "",
                "chars": 0,
                "file": None,
            }
    cart_name, cart_src = find_file(raw, "cart")
    cart = {
        "state": endpoints.get("cart", {}).get("result")
        or ("ok" if cart_src else "missing"),
        "checkout_button": False,
        "empty": False,
    }
    if cart_src:
        cart["state"] = "ok"
        low = cart_src.lower()
        cart["checkout_button"] = bool(
            re.search(
                r'name=["\']checkout["\']|href=["\'][^"\']*/checkout|action=["\'][^"\']*/checkout',
                low,
            )
        )
        cart["empty"] = bool(
            re.search(
                r"cart is empty|cart--empty|is-empty|empty-cart|your bag is empty|cart-empty",
                low,
            )
        )
    product_pages = []
    for fn in sorted(os.listdir(raw)) if os.path.isdir(raw) else []:
        if fn.startswith("page-product") and fn.endswith(TEXT_EXTS):
            product_pages.append((fn, signals.read_text(os.path.join(raw, fn)) or ""))
    contact = find_file(raw, "page-contact")
    faq = find_file(raw, "page-faq")
    contact = contact if contact[0] else None
    faq = faq if faq[0] else None
    sources = [(home_name or "homepage.html", home or "")] if home else []
    sources += product_pages
    if cart_src:
        sources.append((cart_name, cart_src))
    other_texts = []
    if home:
        other_texts.append((home_name, signals.html_to_text(home)))
    for fn, src in product_pages:
        other_texts.append((fn, signals.main_text(src)))
    if faq:
        other_texts.append((faq[0], signals.main_text(faq[1])))
    ships = meta.get("ships_to_countries") if meta else None
    return {
        "mode": mode,
        "domain": status.get("domain"),
        "base_url": status.get("base_url"),
        "endpoints": endpoints,
        "home_raw": home or "",
        "home_text": signals.html_to_text(home) if home else "",
        "identity": identity,
        "markers": markers,
        "meta": meta,
        "shop_name": signals.shop_name(meta, home),
        "base_currency": meta.get("currency") if meta else None,
        "meta_country": meta.get("country") if meta else None,
        "ships_to": [str(c).upper() for c in ships]
        if isinstance(ships, list)
        else None,
        "market": signals.storefront_market(home),
        "product_stats": signals.product_stats(products),
        "policies": policies,
        "policies_seen": any(p["state"] == "ok" for p in policies.values()),
        "cart": cart,
        "product_pages": product_pages,
        "contact": contact,
        "faq": faq,
        "other_texts": other_texts,
        "icons": signals.payment_icons(sources),
        "payment_signatures": signals.match_signatures(
            sources, signals.PAYMENT_SIGNATURES
        ),
        "apps": signals.match_signatures(sources, signals.APP_SIGNATURES),
        "hreflang": signals.hreflang_links(home),
        "localization": signals.has_localization(home),
    }


def run_status(facts):
    """(status, warnings) of the collection itself."""
    warnings = []
    eps = facts["endpoints"]
    if facts["mode"] == "offline":
        warnings.append("offline_no_store")
        return "partial", warnings
    home = eps.get("homepage", {}).get("result")
    if home != "ok":
        warnings.append("homepage_unavailable")
        if home == "blocked":
            warnings.append("blocked")
        return "failed", warnings
    if facts["mode"] == "manual":
        warnings.append("manual_collection")
    failed = sorted(
        k for k, v in eps.items() if v.get("result") in ("blocked", "error")
    )
    if failed:
        warnings.append("endpoints_failed:%s" % ",".join(failed))
    js = sorted(k for k, v in eps.items() if v.get("result") == "js_rendered")
    if js:
        warnings.append("pages_need_browser:%s" % ",".join(js))
    failed += js
    if facts["identity"] == "not_shopify":
        warnings.append("not_shopify")
    if facts["identity"] == "unconfirmed":
        warnings.append("platform_unconfirmed")
    if failed or facts["identity"] in ("not_shopify", "unconfirmed"):
        return "partial", warnings
    return "complete", warnings


def fix_rows(findings, lang, date):
    rows = []
    for f in pr_checks.priority_list(findings) + [
        x for x in findings if x["result"] == "unknown"
    ]:
        rows.append(
            {
                "priority": f["severity"]
                if f["result"] == "issue"
                else PRIORITY_UNKNOWN[lang],
                "id": f["id"],
                "check": f["check"],
                "finding": f["finding"]
                + (
                    (" " + evidence.BASIS_WORD[lang] + ": " + f["basis"])
                    if f["basis"]
                    else ""
                ),
                "badge": evidence.badge(f["badge"], lang),
                "where": ", ".join(f["where"]),
                "fix": f["fix"],
                "who": WHO[lang].get(f["who"], ""),
                "state": STATE[lang][f["result"]],
                "date": date,
            }
        )
    return rows


def evidence_items(facts, findings, q, fee_result, rates):
    items = []
    seen = set()

    def add(level, content, source, url=""):
        if (content, source) in seen:
            return
        seen.add((content, source))
        items.append(
            {
                "evidence_id": "E%03d" % (len(items) + 1),
                "level": level,
                "content": content,
                "source": source,
                "url": url,
            }
        )

    for key, ep in sorted(facts["endpoints"].items()):
        if ep.get("result") == "ok":
            add(
                "E1",
                "page fetched: %s" % key,
                "raw/" + (ep.get("file") or key),
                ep.get("final_url") or ep.get("url") or "",
            )
    for f in findings:
        for w in f["where"]:
            if re.match(r"^S\d+$", w):
                src = (rates.get("sources") or {}).get(w, {})
                add(
                    "E3",
                    "%s official source (%s)" % (f["id"], w),
                    w,
                    src.get("url", ""),
                )
    if q and q.get("answered"):
        add(
            "-",
            "questionnaire answers: %s" % ", ".join(q["answered"]),
            "questionnaire.json",
        )
    by_id = fees_mod.index_rates(rates)
    for sid in (fee_result or {}).get("sources_used", []):
        rec = by_id.get(sid)
        if rec:
            add(
                "E3",
                "rate %s (retrieved %s)" % (sid, rec.get("retrieved")),
                sid,
                rec.get("source_url", ""),
            )
    return items


def summarize(data_dir, lang="zh", today=None):
    today = today or datetime.date.today()
    facts = gather(data_dir)
    q = questionnaire.load(data_dir)
    status, warnings = run_status(facts)
    findings = pr_checks.evaluate(facts, q, lang)
    rates = datafile.load_json(os.path.join(HERE, "..", cfg.RATES_FILE))
    fresh = datafile.freshness(rates, cfg.DATA_MAX_AGE_DAYS, today=today, lang=lang)
    if fresh["stale"] is not False:
        warnings.append("rates_stale")
    fee_result = None
    if q and q.get("answered"):
        fee_result = fees_mod.compute(
            q, rates, lang, identity=facts["identity"], today=today
        )
        if fee_result["status"] == "not_computed":
            warnings.append("fees_missing_inputs:%s" % ",".join(fee_result["missing"]))
    else:
        warnings.append("no_questionnaire")
    date = today.isoformat()
    summary = {
        "schema": "%s/summary/1" % cfg.NAME,
        "tool_version": cfg.TOOL_VERSION,
        "lang": lang,
        "mode": facts["mode"],
        "domain": facts["domain"] or guess_subject(data_dir),
        "shop_name": facts["shop_name"],
        "base_url": facts["base_url"],
        "summarized_at_utc": fetch.utc_now(),
        "status": status,
        "warnings": warnings,
        # same shape as shopify-store-teardown (tools read identity.status)
        "identity": {"status": facts["identity"], "markers": facts["markers"]},
        "visitor_market": facts["market"],
        "base_currency": facts["base_currency"],
        "store_country": facts["meta_country"],
        "ships_to_count": len(facts["ships_to"])
        if facts["ships_to"] is not None
        else None,
        "endpoints": facts["endpoints"],
        "signals": {
            "payment_icons": facts["icons"],
            "payment_scripts": facts["payment_signatures"],
            "apps": facts["apps"],
            "hreflang_count": len(facts["hreflang"]),
            "localization_selector": facts["localization"],
            "product_stats": facts["product_stats"],
        },
        "counts": pr_checks.counts(findings),
        "questionnaire_answered": (q or {}).get("answered", []),
        "fees": None
        if fee_result is None
        else {
            k: fee_result[k]
            for k in (
                "status",
                "missing",
                "currency",
                "monthly_sales",
                "monthly_total",
                "yearly_total",
                "pct_of_sales",
                "unknown_items",
            )
        },
        "rates": fresh,
        "fix_list": fix_rows(findings, lang, date),
        "fee_rows": fees_mod.csv_rows(fee_result, lang)
        if fee_result and fee_result["rows"]
        else [],
    }
    ev = evidence_items(facts, findings, q, fee_result, rates)
    return summary, findings, fee_result, ev, facts, q, rates


def guess_subject(data_dir):
    run = os.path.basename(os.path.dirname(os.path.abspath(data_dir)))
    return run.split(" ")[0] or "store"


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(fees_mod.jsonable(obj), fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def main(argv=None):
    output.safe_console()
    ap = argparse.ArgumentParser(
        description="Compute findings, fees and a report draft offline."
    )
    ap.add_argument("path", help="run folder or its raw-data folder")
    ap.add_argument("--lang", choices=i18n.LANGS)
    args = ap.parse_args(argv)
    data_dir, lang = None, args.lang
    for code in [lang] if lang else list(i18n.LANGS):
        _run, raw = output.locate_run(args.path, code)
        if os.path.isdir(os.path.join(raw, "raw")) or os.path.isfile(
            os.path.join(raw, "_status.json")
        ):
            data_dir, lang = raw, code
            break
    if data_dir is None and os.path.isdir(os.path.join(args.path, "raw")):
        data_dir, lang = os.path.abspath(args.path), lang or "zh"
    if data_dir is None:
        print("error: no raw/ under %s" % args.path, file=sys.stderr)
        return 2
    os.makedirs(os.path.join(data_dir, "raw"), exist_ok=True)
    summary, findings, fee_result, ev, facts, q, rates = summarize(data_dir, lang)
    write_json(os.path.join(data_dir, "summary.json"), summary)
    write_json(os.path.join(data_dir, "findings.json"), findings)
    if fee_result is not None:
        write_json(os.path.join(data_dir, "fees.json"), fee_result)
    with open(os.path.join(data_dir, "evidence.jsonl"), "w", encoding="utf-8") as fh:
        for item in ev:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    text = draft.build(summary, findings, fee_result, ev, facts, q, rates, lang)
    with open(os.path.join(data_dir, "report-draft.md"), "w", encoding="utf-8") as fh:
        fh.write(text)
    c = summary["counts"]
    print("status: %s, warnings: %s" % (summary["status"], summary["warnings"]))
    print(
        "issues: P0 %d, P1 %d, P2 %d, P3 %d; unclear %d"
        % (c["P0"], c["P1"], c["P2"], c["P3"], c["unknown"])
    )
    if summary["fees"] and summary["fees"]["monthly_total"] is not None:
        fz = summary["fees"]
        print(
            "fees: %.2f %s / month, %.2f / year (%.2f%% of sales); unknown items: %d"
            % (
                fz["monthly_total"],
                fz["currency"],
                fz["yearly_total"],
                fz["pct_of_sales"] or 0,
                len(fz["unknown_items"]),
            )
        )
    print("summary: %s" % os.path.join(data_dir, "summary.json"))
    print("draft:   %s" % os.path.join(data_dir, "report-draft.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
