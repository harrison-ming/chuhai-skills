"""Per-skill settings: names, folders, CSV columns, extra self-check rules.

tools/new_skill.py fills the double-underscore placeholders; edit the rest
by hand. The other scripts only read from here, so renaming a folder or a
column is a one-line change.
Python 3.8+, standard library only.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shared import checks, evidence  # noqa: E402

NAME = "launch-readiness"
TOOL_VERSION = "0.1.0"  # keep equal to SKILL.md metadata.version
TITLE = {"zh": "开店上线前体检", "en": "Launch readiness check"}

# Delivery: <Documents>/<ROOT_NAME>/<subject> <RUN_LABEL> <date>/
ROOT_NAME = {"zh": "出海体检报告", "en": "Store checks"}
RUN_LABEL = {"zh": "上线体检", "en": "launch check"}
REPORT_NAME = {"zh": "%s 上线体检报告", "en": "%s launch readiness report"}
README_TEXT = {
    "zh": (
        "这是生成报告用的原始数据, 普通用户可以不看.\n"
        "报告在上一级文件夹的 PDF (找不到浏览器时是 .html) 里, "
        "表格是上一级文件夹的 .csv 文件, 可以用 Excel 或 WPS 直接打开.\n"
        "这里的文件供 AI 助手复核和重新生成报告, 请不要改名或删除.\n"
    ),
    "en": (
        "Raw data used to build the report; you do not need to open it.\n"
        "The report is the PDF (or .html when no browser was found) one folder "
        "up; the .csv tables there open in Excel.\n"
        "Keep these files unchanged so the assistant can re-check the report.\n"
    ),
}

# CSV tables: file name per language and (zh header, en header, field) columns.
# deliver.py writes one CSV per table whose rows exist in summary.json.
CSV_TABLES = [
    {
        "source": "fixlist",
        "name": {"zh": "整改清单.csv", "en": "fix-list.csv"},
        "columns": [
            ("优先级", "Priority", "priority"),
            ("编号", "ID", "id"),
            ("问题", "Issue", "issue"),
            ("涉及市场", "Markets", "markets"),
            ("在哪 (网址)", "Where", "where"),
            ("证据原文", "Evidence", "evidence"),
            ("标签", "Badge", "badge"),
            ("怎么改", "Fix", "fix"),
            ("依据 (来源与网址)", "Source", "basis"),
            ("预计耗时", "Effort", "effort"),
            ("状态", "Status", "status"),
            ("复查方法", "How to recheck", "recheck"),
        ],
    },
]

# Badge set: add "user_input" when users type in numbers ([你提供的]).
BADGE_KEYS = evidence.DEFAULT_BADGE_KEYS
# Extra technical words kept out of the report body: (regex, plain hint).
EXTRA_TECH_TERMS = {
    "zh": [
        (r"(?i)\bcanonical\b", "规范网址"),
        (r"(?i)\bnoindex\b", "不让搜索引擎收录"),
        (r"(?i)robots\.txt", "搜索引擎抓取规则"),
        (r"\bGMC\b(?!-\d)", "谷歌商家中心"),
        (r"(?i)\bx-default\b", "默认语言版本"),
        (r"audit\.json|claims\.jsonl|pages\.json", "原始数据文件 (放附录)"),
        (r"(?i)hreflang", "多语言网址标注"),
        (r"(?i)\bsitemap", "网站地图"),
        (r"products\.json|meta\.json", "商品公开数据"),
        (r"(?i)myshopify", "店铺后台域名 (放附录)"),
    ],
    "en": [
        (r"\bGMC\b(?!-\d)", "Google Merchant Center"),
        (r"audit\.json|claims\.jsonl|pages\.json", "raw data file (appendix only)"),
        (r"(?i)hreflang", "language/region annotations"),
        (r"products\.json|meta\.json", "public product data"),
        (r"(?i)myshopify", "admin domain (appendix only)"),
    ],
}
# Conclusions the skill must never state before the appendix: (regex, why, fix).
FORBIDDEN_TERMS = {
    "zh": [
        (
            r"已合规|不合规|合规(?!服务商)|违法|合法",
            "体检不能给出 '%s' 这种法律结论.",
            "改成 '与 X 的官方要求一致/不一致 [推测] 依据: ...'.",
        ),
    ],
    "en": [
        (
            r"(?i)\b(non-?compliant|compliant|in compliance|illegal|unlawful|lawful)\b",
            "A launch check must not state '%s' (a legal conclusion).",
            "Write 'differs from / matches the official requirement of X [Inferred] Basis: ...'.",
        ),
    ],
}
# A requirement gap must be labelled as a judgement.
RULES = {
    "zh": [
        checks.claim_badge_rule(
            r"官方要求.{0,6}(不一致|一致)|要求不一致",
            "legal_needs_inferred",
            "法规/官方要求的对照是判断, 只能标 [推测].",
            "这一行加 [推测] 和 '依据: <来源>'.",
            required=("inferred",),
        ),
    ],
    "en": [
        checks.claim_badge_rule(
            r"(?i)(differs from|matches|inconsistent with|consistent with) (the )?official",
            "legal_needs_inferred",
            "Comparisons with official requirements are judgements: label them [Inferred].",
            "Add [Inferred] and 'Basis: <source>' to this line.",
            required=("inferred",),
        ),
    ],
}
# Data files shipped in references/ that carry valid_as_of (warn when old).
DATA_FILES = ["rules.json"]
DATA_MAX_AGE_DAYS = 90


def profile(lang):
    """Self-check profile for one language (used by check_report / deliver)."""
    return checks.Profile(
        lang=lang,
        badge_keys=BADGE_KEYS,
        extra_tech_terms=EXTRA_TECH_TERMS[lang],
        forbidden_terms=FORBIDDEN_TERMS[lang],
        rules=RULES[lang],
        require_status=True,
    )
