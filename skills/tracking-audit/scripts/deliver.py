#!/usr/bin/env python3
"""Step 5: turn a checked run folder into files a seller can open.

Reads ``<run>/<raw-data>/report.md`` + summary.json and writes:

    <run>/<subject> <title> report.pdf   (or .html when no browser can print)
    <run>/<table>.csv ...                one per skill_config.CSV_TABLES with data
    <raw-data>/report.html, README, delivery.json

delivery.json records skill, lang, status, report and CSV paths and the
self-check result, for the acceptance harness.

CLI:
    python deliver.py "<run folder>" [--lang zh|en] [--no-open] [--html-only]
                      [--strict]
Python 3.8+, standard library only.
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_report  # noqa: E402
import skill_config as cfg  # noqa: E402
from _shared import checks, output, render  # noqa: E402

MSG = {
    "zh": {
        "no_report": "错误: 找不到报告正文 %s",
        "strict": "错误: 报告自检有 %d 个硬错误, 按 --strict 要求不生成文件.",
        "done": "完成. 文件夹: %s",
        "pdf": "报告 (PDF): %s",
        "html": "报告 (网页版 HTML): %s\n  %s, 所以给的是网页版; 需要 PDF 可在浏览器里 打印 → 另存为 PDF.",
        "csv": "表格 (%d 行, Excel/WPS 可直接打开): %s",
        "errors": "注意: 报告自检有 %d 个硬错误没改, 文件照常生成.",
        "opened": "已为你打开这个文件夹.",
        "draft": "提示: 没有 report.md, 先用自动草稿 %s 生成.",
    },
    "en": {
        "no_report": "Error: report not found: %s",
        "strict": "Error: %d hard self-check errors, --strict stops delivery.",
        "done": "Done. Folder: %s",
        "pdf": "Report (PDF): %s",
        "html": "Report (HTML): %s\n  %s, so this is the web version; print it to PDF from the browser if needed.",
        "csv": "Table (%d rows, opens in Excel): %s",
        "errors": "Note: %d hard self-check errors remain; files were generated anyway.",
        "opened": "Opened the folder for you.",
        "draft": "Note: no report.md, using the automatic draft %s.",
    },
}


def load_summary(raw):
    try:
        with open(os.path.join(raw, "summary.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def csv_tables(summary, lang):
    """[(file name, header, rows or None)] from skill_config.CSV_TABLES."""
    zh = lang == "zh"
    out = []
    for table in cfg.CSV_TABLES:
        data = summary.get(table["source"])
        header = [c[0] if zh else c[1] for c in table["columns"]]
        rows = None
        if data:
            rows = [[r.get(c[2]) for c in table["columns"]] for r in data]
        out.append((table["name"][lang], header, rows))
    return out


def main(argv=None):
    output.safe_console()
    ap = argparse.ArgumentParser(description="Render report.md into PDF/HTML + CSV.")
    ap.add_argument("run_dir")
    ap.add_argument("--lang")
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--html-only", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args(argv)
    lang = check_report.detect_lang(args.run_dir, args.lang)
    msg = MSG[lang]
    run_dir, raw = output.locate_run(args.run_dir, lang)
    md_path = os.path.join(raw, "report.md")
    if not os.path.isfile(md_path):
        draft_path = os.path.join(raw, "report.draft.md")
        if not os.path.isfile(draft_path):
            print(msg["no_report"] % md_path, file=sys.stderr)
            return 1
        print(msg["draft"] % draft_path)
        md_path = draft_path
    with open(md_path, encoding="utf-8-sig") as fh:
        md_text = fh.read()
    profile = cfg.profile(lang)
    result = checks.check_text(md_text, profile)
    if result.errors or result.warnings:
        print(checks.format_result(result, profile, md_path, limit=12))
        print("")
    if result.errors and args.strict:
        print(msg["strict"] % len(result.errors), file=sys.stderr)
        return 1
    summary = load_summary(raw)
    subject = summary.get("domain") or os.path.basename(run_dir).split(" ")[0]
    base = output.sanitize_filename(cfg.REPORT_NAME[lang] % subject, fallback="report")
    today = datetime.date.today().isoformat()
    doc, warnings = render.build_html(
        md_text, raw, base, today, cfg.NAME, lang, cfg.BADGE_KEYS
    )
    html_path = os.path.join(raw, "report.html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    output.write_readme(raw, cfg.README_TEXT[lang], lang)
    for w in warnings:
        print("! %s" % w)
    report_path, kind, why = render.publish(
        html_path, run_dir, raw, base, args.html_only, lang=lang
    )
    written = render.write_csvs(run_dir, csv_tables(summary, lang))
    record = {
        "skill": cfg.NAME,
        "version": cfg.TOOL_VERSION,
        "lang": lang,
        "status": result.status or summary.get("status"),
        "report": os.path.relpath(report_path, run_dir),
        "report_kind": kind,
        "csv": [os.path.relpath(p, run_dir) for p, _n in written],
        "check": checks.result_dict(result, with_status=True, lang=lang),
    }
    with open(os.path.join(raw, "delivery.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("")
    print(msg["done"] % run_dir)
    print(
        msg["pdf"] % report_path if kind == "PDF" else msg["html"] % (report_path, why)
    )
    for path, n in written:
        print(msg["csv"] % (n, path))
    if result.errors:
        print(msg["errors"] % len(result.errors))
    if not args.no_open and output.open_folder(run_dir):
        print(msg["opened"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
