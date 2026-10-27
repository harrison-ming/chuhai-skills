#!/usr/bin/env python3
"""Step 2 (offline): turn ``<raw-data>/raw/`` into summary.json + evidence.jsonl.

CLI:
    python compute.py "<run folder or raw-data folder>" [--lang zh|en]

Works the same on files saved by collect.py (mode A) and on files saved by
hand with a web-reading tool (mode B). summary.json fields:
    status      complete | partial | failed (shared evidence.STATUSES)
    is_target   true | false | "unknown" (homepage not fetched)
    checks      rows for the CSV: item, result, badge (key), note
    endpoints   per request: result, http_status, url
    warnings    machine codes the report must explain
    data_files  freshness of shipped data files (valid_as_of)
Python 3.8+, standard library only.
"""

import argparse
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import skill_config as cfg  # noqa: E402
from _shared import datafile, evidence, fetch, i18n, output  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def load_status(raw):
    try:
        with open(os.path.join(raw, "_status.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def summarize(data_dir, lang="zh"):
    raw = os.path.join(data_dir, "raw")
    status = load_status(raw)
    endpoints = {}
    for req in status.get("requests", []):
        endpoints[req.get("key")] = {
            "result": req.get("result"),
            "http_status": req.get("http_status"),
            "url": req.get("url"),
        }
    home = read_text(os.path.join(raw, "homepage.html"))
    if home is not None and "homepage" not in endpoints:
        endpoints["homepage"] = {"result": "ok", "http_status": None, "url": None}
    warnings = []
    checks = []
    if home is None:
        is_target = "unknown"
        warnings.append("homepage_unavailable")
        checks.append(row(lang, "homepage", "fail", "not_found"))
    else:
        found = [m for m in cfg.TARGET_MARKERS if m in home]
        is_target = bool(found) or not cfg.TARGET_MARKERS
        if not is_target:
            warnings.append("not_target_platform")
        m = TITLE_RE.search(home)
        title = html.unescape(m.group(1)).strip() if m else None
        checks.append(row(lang, "homepage", "ok", "verified"))
        checks.append(
            row(lang, "title", "ok" if title else "missing", "verified", title or "")
        )
    failed = [k for k, v in endpoints.items() if v["result"] != "ok"]
    if home is None:
        run_status = "failed"
    elif failed or is_target is False:
        run_status = "partial"
    else:
        run_status = "complete"
    data_files = []
    for rel in cfg.DATA_FILES:
        info = datafile.freshness(
            datafile.load_json(os.path.join(HERE, "..", rel)),
            cfg.DATA_MAX_AGE_DAYS,
            lang=lang,
        )
        info["file"] = rel
        data_files.append(info)
        if info["stale"] is not False:
            warnings.append("data_file_stale:%s" % rel)
    return {
        "schema": "%s/summary/1" % cfg.NAME,
        "tool_version": cfg.TOOL_VERSION,
        "lang": lang,
        "domain": status.get("domain") or guess_domain(data_dir),
        "summarized_at_utc": fetch.utc_now(),
        "status": run_status,
        "is_target": is_target,
        "endpoints": endpoints,
        "warnings": warnings,
        "checks": checks,
        "data_files": data_files,
    }


ITEMS = {
    "homepage": {"zh": "首页能打开", "en": "Homepage reachable"},
    "title": {"zh": "页面标题", "en": "Page title"},
}


def row(lang, item, result, badge_key, note=""):
    return {
        "item": ITEMS[item][lang],
        "result": result,
        "badge": evidence.badge(badge_key, lang),
        "note": note,
    }


def guess_domain(data_dir):
    run = os.path.basename(os.path.dirname(os.path.abspath(data_dir)))
    return run.split(" ")[0] or "site"


def write_outputs(data_dir, summary):
    with open(os.path.join(data_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    with open(os.path.join(data_dir, "evidence.jsonl"), "w", encoding="utf-8") as fh:
        for i, c in enumerate(summary["checks"], 1):
            rec = {"evidence_id": "E%03d" % i, "item": c["item"], "badge": c["badge"]}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main(argv=None):
    output.safe_console()
    ap = argparse.ArgumentParser(description="Compute summary.json offline.")
    ap.add_argument("path", help="run folder or its raw-data folder")
    ap.add_argument("--lang", default="zh", choices=i18n.LANGS)
    args = ap.parse_args(argv)
    run_dir, data_dir = output.locate_run(args.path, args.lang)
    if not os.path.isdir(os.path.join(data_dir, "raw")):
        data_dir = os.path.abspath(args.path)
    if not os.path.isdir(os.path.join(data_dir, "raw")):
        print("error: no raw/ under %s" % args.path, file=sys.stderr)
        return 2
    summary = summarize(data_dir, args.lang)
    write_outputs(data_dir, summary)
    print("status: %s, warnings: %s" % (summary["status"], summary["warnings"]))
    print("summary: %s" % os.path.join(data_dir, "summary.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
