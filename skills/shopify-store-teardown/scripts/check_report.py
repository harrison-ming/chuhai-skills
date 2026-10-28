#!/usr/bin/env python3
"""Self-check gate for a finished report.md, run before deliver.py.

CLI:
    python check_report.py "<run folder>"        (or its 原始数据 folder,
                                                  or the report.md itself)
    python check_report.py "<run folder>" --json

Exit code: 0 = no hard errors (warnings may remain), 1 = hard errors found,
2 = report.md not found.

Hard errors (must be fixed, then re-run):
  - missing "## 一页看懂" (or it is not the first "##" section)
  - missing "## 附录..." section
  - technical words before the appendix (E1-E6, L0/L1/L2, products.json,
    myshopify, Shopify.theme, hreflang, RDAP, Wayback, sitemap, JSON, ...).
    A line that explains the word in plain Chinese (contains 也就是 / 即: /
    指的是 / 俗称 ...) is downgraded to a warning.
  - old labels such as [事实] [推断] [存疑] [未知] [观察] [品牌自述] [第三方声称]
  - [推测] without "依据" in the same line (or its continuation line / cell)
  - missing the fixed ending "本报告没有覆盖的"

Warnings (do not fail, but the agent must review each one):
  - suspected over-labeling: the claim attached to [已核实] contains
    judgment words (JUDGMENT_WORDS) or source words (MIXED_SOURCE_WORDS)
  - "handle" / "tag" / snake_case field names before the appendix
  - full-width punctuation

The rule engine is ``_shared/checks.py``; this module holds the teardown
word lists. deliver.py imports check_text() / format_result() from here.
Python 3.8+, standard library only.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shared import checks, evidence, output  # noqa: E402
from _shared.checks import (  # noqa: E402,F401  (re-exported API)
    FULLWIDTH_RE,
    LIST_RE,
    QUOTE_RE,
    URL_RE,
    Issue,
    Result,
    clip,
    content_lines,
    strip_quotes,
)

# ---------------------------------------------------------------------------
# configuration (edit these lists to tune the checker)
# ---------------------------------------------------------------------------

SUMMARY_HEADING = "## 一页看懂"
APPENDIX_PREFIX = "## 附录"
FIXED_ENDING = "本报告没有覆盖的"
RAW_DIRNAME = "原始数据"

BADGES = evidence.labels("zh")
OLD_LABELS = dict(evidence.OLD_LABELS_ZH)
JUDGMENT_WORDS = list(checks.JUDGMENT_WORDS_ZH)
MIXED_SOURCE_WORDS = list(checks.MIXED_SOURCE_WORDS_ZH)

# Technical words forbidden before the appendix: (regex, plain-Chinese hint).
TECH_TERMS = [
    (r"(?<![A-Za-z0-9])E[1-6](?![0-9])", "证据等级只写在附录, 正文用标签"),
    (r"(?<![A-Za-z0-9])L[012](?![0-9])", "改成 快速核验 / 深度拆解 / 浏览器实测"),
    (r"products\.json", "店铺公开的商品数据"),
    (r"collections\.json", "店铺公开的商品分类数据"),
    (r"summary\.json|evidence\.jsonl|products\.tsv", "放附录"),
    (r"myshopify", "店铺在 Shopify 上的原始域名"),
    (r"Shopify\.theme", "店铺用的主题"),
    (r"hreflang", "多国站点设置"),
    (r"\bMarkets\b", "多国站点设置"),
    (r"\bRDAP\b", "网站注册记录"),
    (r"\bWHOIS\b", "网站注册记录"),
    (r"Wayback", "网页历史存档"),
    (r"sitemap", "网站地图"),
    (r"\bJSON\b", "店铺公开数据"),
    (r"\bAPI\b", "公开数据接口"),
    (r"(?<![A-Za-z])raw/", "放附录"),
    (r"compare_at_price|created_at|published_at|ships_to|products_count", "放附录"),
]
# Softer technical words (common in English quotes): warning only.
SOFT_TECH_TERMS = [
    (r"\bhandles?\b", "商品网址标识"),
    (r"\btags?\b", "商品后台分类标记"),
    (r"\b[a-z]+_[a-z_]+\b", "字段名放附录"),
]
EXPLAIN_RE = checks.EXPLAIN_RE["zh"]

PROFILE = checks.Profile(
    lang="zh",
    summary_heading=SUMMARY_HEADING,
    appendix_prefix=APPENDIX_PREFIX,
    appendix_example=APPENDIX_PREFIX + ": 证据与数据来源",
    fixed_ending=FIXED_ENDING,
    old_labels=OLD_LABELS,
    judgment_words=JUDGMENT_WORDS,
    mixed_source_words=MIXED_SOURCE_WORDS,
    tech_terms=TECH_TERMS,
    soft_tech_terms=SOFT_TECH_TERMS,
    explain_re=EXPLAIN_RE,
    template_name="report-template.md",
)
BADGE_RE = PROFILE.badge_re
OLD_RE = PROFILE.old_re
JUDGMENT_RE = PROFILE.judgment_re
MIXED_RE = PROFILE.mixed_re


def claim_segments(line):
    """Yield (badge, claim_text) for each badge in a line."""
    return checks.claim_segments(line, BADGE_RE)


def check_text(md_text):
    """Run every rule on report markdown and return a Result."""
    return checks.check_text(md_text, PROFILE)


def format_result(result, path=None, limit=None):
    """Plain-Chinese report of a Result, one block per issue."""
    return checks.format_result(result, PROFILE, path, limit)


def find_report(path):
    """Accept the run folder, its 原始数据 folder, or report.md itself."""
    return checks.find_report(path, RAW_DIRNAME)


def main(argv=None):
    try:
        import delivery

        delivery.safe_console()
    except Exception:  # noqa: BLE001 - console tweak is optional
        pass
    ap = argparse.ArgumentParser(description="Self-check report.md before delivery.")
    ap.add_argument("path", help="run folder, its 原始数据 folder, or report.md")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = ap.parse_args(argv)
    md_path = find_report(args.path)
    if not md_path:
        print("错误: 找不到 report.md (在 %s 下)" % args.path, file=sys.stderr)
        return 2
    result = check_text(output.read_text(md_path))
    if args.json:
        data = checks.result_dict(result)
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(format_result(result, md_path))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
