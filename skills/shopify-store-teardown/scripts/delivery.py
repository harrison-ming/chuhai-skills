"""Delivery helpers for collect.py and deliver.py (teardown wording).

Resolves where the user-facing ``出海拆解报告`` folder lives, names the
per-run folder, cleans file names, locates a Chromium-family browser for
PDF printing and opens a folder in the local file manager. The generic code
lives in ``_shared/output.py``; this module keeps the teardown names and
defaults (Pro scripts import it too).

Python 3.8+, standard library only.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shared import output  # noqa: E402
from _shared.output import (  # noqa: E402,F401  (re-exported API)
    LINUX_BROWSERS,
    browser_candidates,
    can_open_gui,
    documents_dir,
    find_browser,
    in_cloud_sandbox,
    open_folder,
    safe_console,
    sanitize_filename,
)

ROOT_NAME = "出海拆解报告"
RAW_DIRNAME = "原始数据"
README_NAME = "说明.txt"
REPORT_SUFFIX = " 对标拆解报告"
CSV_NAME = "商品清单.csv"
ENV_OUTPUT = output.ENV_OUTPUT
SANDBOX_OUTPUTS = output.SANDBOX_OUTPUTS
ILLEGAL_CHARS = output.ILLEGAL_CHARS
RUN_LABEL = "对标拆解"

README_TEXT = (
    "这是生成报告用的原始数据, 普通用户可以不看.\n"
    '报告正文在上一级文件夹里的 PDF ("...对标拆解报告.pdf", '
    "找不到浏览器时是 .html) 里.\n"
    '商品清单在上一级文件夹的 "商品清单.csv", 可以用 Excel 或 WPS 直接打开.\n'
    "这里的 raw/, summary.json, products.tsv, evidence.jsonl, report.md, "
    "report.html 供 AI 助手复核和重新生成报告, 请不要改名或删除.\n"
)
COMPARE_MARKER = "compare.json"
README_TEXT_COMPARE = (
    "这是生成多店对比报告用的数据, 普通用户可以不看.\n"
    "对比报告在上一级文件夹里的 PDF (找不到浏览器时是 .html) 里, "
    "表格在上一级文件夹的 .csv 文件里, 可以用 Excel 或 WPS 直接打开.\n"
    "这里的 compare.md, compare.json, report.md, report.html "
    "供 AI 助手复核和重新生成报告, 请不要改名或删除.\n"
)


def resolve_output_root(
    cli_root=None, environ=None, platform=None, home=None, sandbox=SANDBOX_OUTPUTS
):
    """Resolve the delivery root folder (not created here).

    Order: ``--out-root`` > $CHUHAI_OUTPUT_DIR > cloud sandbox outputs >
    ``<Documents>/出海拆解报告``.
    """
    return output.resolve_output_root(
        cli_root,
        ROOT_NAME,
        environ=environ,
        platform=platform,
        home=home,
        sandbox=sandbox,
    )


def run_dir_name(domain, date=None):
    return output.run_dir_name(domain, RUN_LABEL, date)


def create_run_dir(root, domain, date=None):
    """Create and return a fresh ``<root>/<domain> 对标拆解 <date>[ (N)]``."""
    return output.create_run_dir(root, domain, RUN_LABEL, date)


def report_basename(shop_name, domain):
    return sanitize_filename(shop_name or domain) + REPORT_SUFFIX


def write_readme(raw_dir):
    """Write 说明.txt; a multi-store compare folder gets its own wording."""
    compare = os.path.isfile(os.path.join(raw_dir, COMPARE_MARKER))
    return output.write_notepad_text(
        os.path.join(raw_dir, README_NAME),
        README_TEXT_COMPARE if compare else README_TEXT,
    )
