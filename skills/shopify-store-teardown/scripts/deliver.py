#!/usr/bin/env python3
"""Turn a finished run folder into files an ordinary seller can open.

Reads ``<run>/原始数据/report.md`` (+ summary.json, products.tsv) and writes:

    <run>/<店名> 对标拆解报告.pdf   (or .html when no browser can print)
    <run>/商品清单.csv             (UTF-8 with BOM, Chinese headers)
    <run>/原始数据/report.html
    <run>/原始数据/说明.txt

CLI:
    python deliver.py "<run folder>" [--no-open] [--html-only] [--title TITLE]
                      [--verbose] [--strict]

Before rendering, report.md is run through check_report.check_text() (the
same gate the agent runs by hand). Hard errors are printed and noted in the
final message, but the files are still generated; with ``--strict`` hard
errors stop delivery (exit code 1).

``--title`` replaces the default "<店名> 对标拆解报告" file name and fallback
page title (for example ``--title 多店对比报告`` in the Pro edition).

Markdown is converted by the small built-in converter in
``_shared/render.py`` (no third-party packages). PDF printing uses a local
Chrome / Edge / Chromium in headless mode with a throwaway profile. Exit code 0 on success (HTML fallback
included), 1 on fatal errors such as a missing report.md.
Python 3.8+, standard library only.
"""

import argparse
import csv
import datetime
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_report  # noqa: E402
import delivery  # noqa: E402
from _shared import evidence, output, render  # noqa: E402
from _shared.render import (  # noqa: E402,F401  (re-exported API)
    CSS,
    IMAGE_WARN_BYTES,
    PDF_TIMEOUT,
    _run_browser,
    _stop,
    csv_safe,
    is_nowrap_cell,
    join_lines,
    split_row,
    strip_md,
    valid_pdf,
)

LANG = "zh"
GENERATOR = "shopify-store-teardown"
BROWSER_EXIT_NOTE = render.exit_note(LANG)
BADGES = evidence.class_map(LANG)
BADGE_CLASS = dict(BADGES)
BADGE_RE = render.badge_regex(LANG)


class Converter(render.Converter):
    """Teardown report converter (Chinese badges and section markers)."""

    def __init__(self, base_dir="."):
        render.Converter.__init__(self, base_dir, LANG)


def build_html(md_text, base_dir, fallback_title, date_str):
    return render.build_html(
        md_text, base_dir, fallback_title, date_str, GENERATOR, LANG
    )


def print_pdf(
    browser,
    html_path,
    pdf_path,
    timeout=PDF_TIMEOUT,
    popen=subprocess.Popen,
    poll_interval=0.5,
):
    """Print html_path to pdf_path with a throwaway profile. Returns (ok, note).

    ``--headless=new`` is tried first, then legacy ``--headless``.
    """
    return render.print_pdf(
        browser,
        html_path,
        pdf_path,
        timeout=timeout,
        popen=popen,
        poll_interval=poll_interval,
        lang=LANG,
        profile_prefix="teardown-browser-",
    )


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

CSV_COLUMNS = [
    ("商品名称", "title"),
    ("类型", "product_type"),
    ("最低价", "price_min"),
    ("最高价", "price_max"),
    ("原价", "compare_at_max"),
    ("币种", "currency"),
    ("是否有货", "available"),
    ("规格数", "variant_count"),
    ("首次上架时间", "created_at"),
    ("商品链接", "url"),
]


def fmt_available(v):
    v = (v or "").strip().lower()
    if v in ("true", "1", "yes"):
        return "是"
    if v in ("false", "0", "no"):
        return "否"
    return ""


def write_products_csv(tsv_path, csv_path):
    """Convert products.tsv to an Excel-friendly CSV. Returns row count."""
    with open(tsv_path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)
        rows = list(reader)
    out = []
    for r in rows:
        cells = []
        for _cn, key in CSV_COLUMNS:
            v = r.get(key) or ""
            if key == "available":
                v = fmt_available(v)
            elif key == "created_at":
                v = (v or r.get("published_at") or "")[:10]
            cells.append(v)
        out.append(cells)
    return render.write_csv(csv_path, [c for c, _ in CSV_COLUMNS], out)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def locate_run(path):
    """Return (run_dir, raw_dir). Accepts the 原始数据 folder itself too."""
    return output.locate_run(path, LANG)


def load_summary(raw):
    try:
        with open(os.path.join(raw, "summary.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def guess_domain(run_dir, summary):
    if summary.get("domain"):
        return summary["domain"]
    name = os.path.basename(run_dir)
    return name.split(" ")[0] if name else "store"


def main(argv=None):
    delivery.safe_console()
    ap = argparse.ArgumentParser(
        description="Render report.md into a PDF (or HTML) plus a product CSV."
    )
    ap.add_argument(
        "run_dir", help='run folder, e.g. ".../example.com 对标拆解 2026-09-30"'
    )
    ap.add_argument("--no-open", action="store_true", help="do not open the folder")
    ap.add_argument("--html-only", action="store_true", help="skip PDF printing")
    ap.add_argument(
        "--verbose", action="store_true", help="print extra notes (e.g. no CSV)"
    )
    ap.add_argument(
        "--title",
        help='report file name / fallback title, default "<店名> 对标拆解报告"',
    )
    ap.add_argument(
        "--strict",
        action="store_true",
        help="do not generate files when the report self-check has hard errors",
    )
    args = ap.parse_args(argv)

    run_dir, raw = locate_run(args.run_dir)
    md_path = os.path.join(raw, "report.md")
    if not os.path.isfile(md_path):
        print("错误: 找不到报告正文 %s" % md_path, file=sys.stderr)
        print(
            "请先让 agent 把报告写进这个文件, 再运行 deliver.py. "
            '传入的应当是 "<域名> 对标拆解 <日期>" 这一层文件夹.',
            file=sys.stderr,
        )
        return 1
    summary = load_summary(raw)
    domain = guess_domain(run_dir, summary)
    shop_name = (summary.get("shop") or {}).get("name") or ""
    if args.title and args.title.strip():
        base = delivery.sanitize_filename(args.title, fallback="report")
    else:
        base = delivery.report_basename(shop_name, domain)
    today = datetime.date.today().isoformat()

    try:
        md_text = output.read_text(md_path)
    except OSError as e:
        print("错误: 读取 report.md 失败: %s" % e, file=sys.stderr)
        return 1
    check = check_report.check_text(md_text)
    if check.errors or check.warnings:
        print(check_report.format_result(check, md_path, limit=12))
        print("")
    if check.errors and args.strict:
        print(
            "错误: 报告自检有 %d 个硬错误, 按 --strict 要求不生成文件. "
            "改完 report.md 后重新运行." % len(check.errors),
            file=sys.stderr,
        )
        return 1
    doc, warnings = build_html(md_text, raw, base, today)
    html_path = os.path.join(raw, "report.html")
    try:
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(doc)
        delivery.write_readme(raw)
    except OSError as e:
        print("错误: 写入文件失败: %s" % e, file=sys.stderr)
        return 1
    for w in warnings:
        print("提示: %s" % w)

    report_path, kind, why = render.publish(
        html_path,
        run_dir,
        raw,
        base,
        html_only=args.html_only,
        find_browser=delivery.find_browser,
        printer=print_pdf,
        lang=LANG,
    )

    tsv = os.path.join(raw, "products.tsv")
    csv_path = os.path.join(run_dir, delivery.CSV_NAME)
    csv_rows = None
    if os.path.isfile(tsv):
        try:
            csv_rows = write_products_csv(tsv, csv_path)
        except (OSError, csv.Error, UnicodeDecodeError) as e:
            print("提示: 商品清单生成失败: %s" % e)
    elif args.verbose:
        print("提示: 没有 products.tsv, 未生成商品清单.")

    print("")
    print("完成. 拆解文件夹: %s" % run_dir)
    if kind == "PDF":
        print("报告 (PDF): %s" % report_path)
    else:
        print("报告 (网页版 HTML): %s" % report_path)
        print("  %s, 所以给的是网页版." % why)
        print("  双击用浏览器打开即可; 需要 PDF 可在浏览器里 打印 → 另存为 PDF.")
    if csv_rows is not None:
        print("商品清单 (%d 个商品, Excel/WPS 可直接打开): %s" % (csv_rows, csv_path))
    print("原始数据 (普通用户可以不看): %s" % raw)
    if check.errors:
        print(
            "注意: 报告自检有 %d 个硬错误没改 (详见上方), 文件照常生成. "
            "建议改完 report.md 后重新运行 deliver.py." % len(check.errors)
        )
    elif check.over_label_count():
        print(
            "注意: 报告自检有 %d 处疑似标高, 请确认已逐条复查."
            % check.over_label_count()
        )
    if not args.no_open and delivery.open_folder(run_dir):
        print("已为你打开这个文件夹.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
