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
RUN_LABEL = TITLE
REPORT_NAME = {"zh": "%s 开店上线前体检报告", "en": "%s Launch readiness check report"}
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

# Pages collect.py fetches: (key, path, file name, kind json|html|xml).
PAGES = [("homepage", "/", "homepage.html", "html")]
# Strings that identify the target platform in the homepage (empty: any site).
TARGET_MARKERS = ["cdn.shopify.com", "Shopify.theme"]

# CSV tables: file name per language and (zh header, en header, field) columns.
# deliver.py writes one CSV per table whose rows exist in summary.json.
CSV_TABLES = [
    {
        "source": "checks",
        "name": {"zh": "检查清单.csv", "en": "checklist.csv"},
        "columns": [
            ("检查项", "Item", "item"),
            ("结果", "Result", "result"),
            ("证据", "Evidence", "badge"),
            ("说明", "Note", "note"),
        ],
    },
]

# Badge set: add "user_input" when users type in numbers ([你提供的]).
BADGE_KEYS = evidence.DEFAULT_BADGE_KEYS
# Extra technical words kept out of the report body: (regex, plain hint).
EXTRA_TECH_TERMS = {"zh": [], "en": []}
# Conclusions the skill must never state: (regex, why with %s, fix).
FORBIDDEN_TERMS = {"zh": [], "en": []}
# Pluggable rules, e.g. checks.claim_badge_rule(...); see _shared/checks.py.
RULES = {"zh": [], "en": []}
# Data files shipped in references/ that carry valid_as_of (warn when old).
DATA_FILES = []
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
