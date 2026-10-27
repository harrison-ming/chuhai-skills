#!/usr/bin/env python3
"""Step 1 (online): fetch the public pages listed in skill_config.PAGES.

CLI:
    python collect.py <url_or_domain> [--lang zh|en] [--out-root DIR | --out DIR]
                      [--delay 0.5] [--timeout 20] [--no-compute]

Creates ``<root>/<domain> <label> <date>/`` (root: --out-root >
$CHUHAI_OUTPUT_DIR > cloud sandbox > Documents/<root name>), saves pages to
``<raw-data>/raw/`` and every request to ``raw/_status.json``, then runs
compute.py unless --no-compute. A failing request never aborts the run.
Python 3.8+, standard library only. TLS verification is always on.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compute  # noqa: E402
import skill_config as cfg  # noqa: E402
from _shared import fetch, i18n, output  # noqa: E402


def classify(status, body):
    if status is None:
        return "error"
    if status == 404:
        return "not_found"
    if status in (401, 403, 429, 430):
        return "blocked"
    if status >= 400 or not (body or b"").strip():
        return "error"
    return "ok"


def collect(target, data_dir, fetcher):
    scheme, host = fetch.normalize_input(target)
    base = "%s://%s" % (scheme, host)
    raw = os.path.join(data_dir, "raw")
    os.makedirs(raw, exist_ok=True)
    requests = []
    started = fetch.utc_now()
    for key, path, fname, kind in cfg.PAGES:
        url = base + path
        accept = fetch.ACCEPT_JSON if kind == "json" else fetch.ACCEPT_HTML
        r = fetcher.get(url, accept)
        result = classify(r["status"], r["body"])
        requests.append(fetch.status_entry(key, url, r, result, fname))
        if result == "ok":
            with open(os.path.join(raw, fname), "wb") as fh:
                fh.write(r["body"])
    status = {
        "schema": "%s/status/1" % cfg.NAME,
        "tool_version": cfg.TOOL_VERSION,
        "input": target,
        "domain": fetch.strip_www(host),
        "base_url": base,
        "started_at_utc": started,
        "finished_at_utc": fetch.utc_now(),
        "requests": requests,
    }
    with open(os.path.join(raw, "_status.json"), "w", encoding="utf-8") as fh:
        json.dump(status, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return status


def main(argv=None):
    output.safe_console()
    ap = argparse.ArgumentParser(description="Collect public pages.")
    ap.add_argument("target", help="domain or any URL")
    ap.add_argument("--lang", default="zh", choices=i18n.LANGS)
    ap.add_argument("--out-root", help="delivery root folder")
    ap.add_argument("--out", help="raw-data folder itself (tests)")
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--no-compute", action="store_true")
    args = ap.parse_args(argv)
    try:
        _scheme, host = fetch.normalize_input(args.target)
    except ValueError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    if args.out:
        data_dir = os.path.abspath(args.out)
        run_dir = os.path.dirname(data_dir)
    else:
        root = output.resolve_output_root(args.out_root, cfg.ROOT_NAME[args.lang])
        run_dir = output.create_run_dir(
            root, fetch.strip_www(host), cfg.RUN_LABEL[args.lang]
        )
        data_dir = os.path.join(run_dir, output.raw_dirname(args.lang))
    os.makedirs(data_dir, exist_ok=True)
    output.write_readme(data_dir, cfg.README_TEXT[args.lang], args.lang)
    fetcher = fetch.Fetcher(
        timeout=args.timeout, delay=max(args.delay, 0.0), lang=args.lang
    )
    status = collect(args.target, data_dir, fetcher)
    if fetch.cert_failures(status["requests"], fetcher.cert_note):
        print("! %s" % fetcher.cert_note)
    print("run folder: %s" % run_dir)
    if args.no_compute:
        return 0
    return compute.main([data_dir, "--lang", args.lang])


if __name__ == "__main__":
    sys.exit(main())
