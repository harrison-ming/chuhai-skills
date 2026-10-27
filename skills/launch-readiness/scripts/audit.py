#!/usr/bin/env python3
"""Offline judgement: raw/ -> audit.json, summary.json, claims.jsonl,
evidence.jsonl and a report draft (report.draft.md).

Works the same for mode A (audit_collect.py), mode B (files saved by hand,
roles inferred from file names) and mode P (paste_ingest.py).

CLI:
    python audit.py "<run folder or raw-data folder>" [--lang zh|en]
        [--markets "US,UK,DE"] [--no-ads]
summary.json: status, lang, domain, mode, platform, markets, verdict,
counts, fixlist (CSV rows), warnings, endpoints, data_files.
Python 3.8+, standard library only.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import checks_loc  # noqa: E402
import checks_market  # noqa: E402
import checks_policy  # noqa: E402
import checks_trust  # noqa: E402
import draft_report  # noqa: E402
import export_fixlist  # noqa: E402
import extract_claims  # noqa: E402
import markets  # noqa: E402
import pages  # noqa: E402
import ruleset  # noqa: E402
import skill_config as cfg  # noqa: E402
import storesite  # noqa: E402
from _shared import datafile, i18n, output  # noqa: E402

KEY_ENDPOINTS = (
    "policy:refund-policy",
    "policy:shipping-policy",
    "policy:privacy-policy",
    "products",
)


def resolve(path, lang):
    """(run_dir, data_dir) from a run folder, its raw-data folder or raw/."""
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.basename(path) == "raw":
        path = os.path.dirname(path)
    for code in (lang,) + tuple(c for c in i18n.LANGS if c != lang):
        if os.path.isdir(os.path.join(path, output.raw_dirname(code), "raw")):
            return path, os.path.join(path, output.raw_dirname(code))
    return os.path.dirname(path), path


def choose_markets(site, user_text):
    seen = (
        markets.hreflang_countries(pages.hreflang_links(site.home)) if site.home else []
    )
    loc = markets.localization_countries(site.home) if site.home else []
    countries, how = markets.default_markets(user_text, seen + loc)
    if how == "user":
        lang_countries = countries
    else:
        lang_countries = [c for c in seen if c in countries] + ["US"]
        lang_countries = list(dict.fromkeys(lang_countries))
    return countries, how, lang_countries


def overall_status(site, findings):
    if site.mode == "online" and not site.home:
        return "failed"
    if site.paste or site.mode != "online" or site.password:
        return "partial"
    if site.platform != "shopify" or pages.js_rendered(site.home or ""):
        return "partial"
    bad = [
        r
        for r in site.index
        if r.get("key") in KEY_ENDPOINTS and r.get("result") in ("blocked", "error")
    ]
    return "partial" if bad else "complete"


def verdict(status, findings):
    if status == "failed":
        return None
    if any(f["id"] == "TRU-05" and f["result"] == "manual" for f in findings) and not [
        f for f in findings if f["result"] == "fail"
    ]:
        return None  # password page only: nothing judged yet
    p0 = [f for f in findings if f["result"] == "fail" and f["level"] == "P0"]
    if not p0:
        return "go"
    return "fix_p0" if len(p0) <= 2 else "hold"


def warnings_for(site, status, how):
    out = []
    if site.mode == "online" and not site.home:
        out.append("homepage_unavailable")
    if site.password:
        out.append("password_protected")
    if site.paste:
        out.append("paste_mode")
    if site.mode == "manual":
        out.append("manual_mode")
    if site.home and site.platform != "shopify":
        out.append("not_shopify")
    if site.home and not site.password and pages.js_rendered(site.home):
        out.append("js_rendered")
    for r in site.index:
        if r.get("result") == "blocked":
            out.append("blocked:%s" % r.get("key"))
    if how == "site":
        out.append("markets_default")
    asked = (site.status.get("input") or "").lower()
    final = (site.home_url or "").lower()
    if (
        site.home
        and asked
        and fetch_host(final)
        and fetch_host(final) not in (fetch_host(asked), "www." + fetch_host(asked))
        and fetch_host(asked) != "www." + fetch_host(final)
    ):
        out.append("redirected_host:%s" % fetch_host(final))
    return out


def fetch_host(url):
    from urllib.parse import urlsplit

    if "://" not in url:
        url = "https://" + url
    return (urlsplit(url).hostname or "").lower()


def run_checks(site, countries, lang_countries, ads):
    ctx = ruleset.Ctx(site, countries, ads, lang_countries)
    claims = (
        extract_claims.extract(site)
        if (site.home or site.paste or site.policies)
        else []
    )
    if site.mode == "online" and not site.home:
        ctx.default_note = "homepage_unavailable"
        return ctx, claims  # failed: every rule stays "not evaluated" -> unknown
    if site.password and not site.paste:
        # Only the password page is public: nothing else can be judged.
        ctx.add("TRU-05", "manual", where=site.home_url, note="password_page")
        ctx.default_note = "password_only"
        return ctx, []
    checks_policy.run(ctx, claims)
    checks_market.run(ctx, claims)
    checks_loc.run(ctx, claims)
    checks_trust.run(ctx)
    host = fetch_host(site.home_url or "")
    shell = (
        host.startswith("checkout.")
        or ".checkout." in host
        or host.endswith("myshopify.com")
    )
    if pages.js_rendered(site.home or "") or shell:
        soften_js_home(ctx, shell)
    return ctx, claims


# Checks that read the homepage HTML: on a JavaScript-rendered homepage an
# absence there is unreadable, not "missing".
HOME_DEPENDENT = (
    "POL-01",
    "POL-02",
    "POL-03",
    "POL-04",
    "POL-05",
    "GMC-01",
    "LOC-01",
    "TRU-04",
    "TRU-06",
    "GMC-06",
    "BIZ-03",
)


def soften_js_home(ctx, shell=False):
    """``shell``: the visitor was sent to a checkout / admin domain (headless
    storefront), so homepage-level results say nothing about the real shop."""
    if shell:
        for rid in (
            "SEO-01",
            "SEO-02",
            "SEO-03",
            "GMC-02",
            "TRU-02",
            "LOC-03",
            "POL-06",
        ):
            f = ctx.findings.get(rid)
            if f and f["result"] == "fail":
                f.update(result="unknown", badge="not_found", note="js_home")
    for rid in HOME_DEPENDENT:
        f = ctx.findings.get(rid)
        if f and f["result"] == "fail" and not f["evidence"]:
            f.update(result="unknown", badge="not_found", note="js_home")


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def audit(data_dir, lang="zh", user_markets=None, ads=None):
    raw = os.path.join(data_dir, "raw")
    site = storesite.Site(raw)
    user_markets = user_markets or site.status.get("user_markets")
    ads = site.status.get("ads", True) if ads is None else ads
    countries, how, lang_countries = choose_markets(site, user_markets)
    ctx, claims = run_checks(site, countries, lang_countries, ads)
    findings = ctx.ordered()
    status = overall_status(site, findings)
    fresh = datafile.freshness(
        datafile.load_json(ruleset.RULES_PATH), cfg.DATA_MAX_AGE_DAYS, lang=lang
    )
    warnings = warnings_for(site, status, how)
    if fresh.get("stale"):
        warnings.append("data_file_stale:rules.json")
    counts = dict((lv, 0) for lv in ruleset.LEVELS)
    for f in findings:
        if f["result"] in ruleset.IN_FIXLIST:
            counts[f["level"]] += 1
    summary = {
        "schema": "%s/summary/1" % cfg.NAME,
        "tool_version": cfg.TOOL_VERSION,
        "lang": lang,
        "status": status,
        "domain": site.status.get("domain"),
        "base_url": site.status.get("base_url"),
        "mode": site.mode,
        "platform": site.platform,
        "password_protected": site.password,
        "ads": ads,
        "markets": countries,
        "markets_source": how,
        "language_markets": lang_countries,
        "verdict": verdict(status, findings),
        "counts": counts,
        "fixlist": export_fixlist.build_rows(findings, lang),
        "warnings": warnings,
        "endpoints": dict(
            (
                r.get("key"),
                {
                    "result": r.get("result"),
                    "url": r.get("url"),
                    "http_status": r.get("http_status"),
                },
            )
            for r in site.index
        ),
        "data_files": {"rules.json": fresh},
    }
    audit_doc = dict(
        summary, findings=findings, rules_valid_as_of=ctx.data["valid_as_of"]
    )
    audit_doc.pop("fixlist")
    for name, doc in (("summary.json", summary), ("audit.json", audit_doc)):
        with open(os.path.join(data_dir, name), "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    write_jsonl(os.path.join(data_dir, "claims.jsonl"), claims)
    ev = [
        {
            "key": r.get("key"),
            "url": r.get("url"),
            "file": r.get("file"),
            "result": r.get("result"),
            "level": "E2" if site.paste else "E1",
        }
        for r in site.index
    ]
    write_jsonl(os.path.join(data_dir, "evidence.jsonl"), ev)
    draft = draft_report.render(summary, findings, ctx, lang)
    with open(os.path.join(data_dir, "report.draft.md"), "w", encoding="utf-8") as fh:
        fh.write(draft)
    return summary


def main(argv=None):
    output.safe_console()
    ap = argparse.ArgumentParser(description="Judge raw/ and write audit.json + draft.")
    ap.add_argument("path")
    ap.add_argument("--lang", default="zh", choices=i18n.LANGS)
    ap.add_argument("--markets")
    ap.add_argument("--no-ads", action="store_true")
    args = ap.parse_args(argv)
    _run_dir, data_dir = resolve(args.path, args.lang)
    if not os.path.isdir(os.path.join(data_dir, "raw")):
        print("error: no raw/ folder under %s" % data_dir, file=sys.stderr)
        return 2
    s = audit(data_dir, args.lang, args.markets, False if args.no_ads else None)
    zh = args.lang == "zh"
    print(
        (
            "状态: %s; 结论: %s; P0/P1/P2/P3 = %s"
            if zh
            else "status: %s; verdict: %s; P0/P1/P2/P3 = %s"
        )
        % (
            s["status"],
            s["verdict"],
            "/".join(str(s["counts"][k]) for k in ruleset.LEVELS),
        )
    )
    if s["warnings"]:
        print("warnings: " + ", ".join(s["warnings"]))
    print(
        ("报告草稿: %s" if zh else "report draft: %s")
        % os.path.join(data_dir, "report.draft.md")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
