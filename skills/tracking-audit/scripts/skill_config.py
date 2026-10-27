"""Per-skill settings: names, folders, CSV columns, extra self-check rules.

The other scripts only read from here. An optional ``pro_extend`` module next
to this file (shipped only in the Pro bundle) may add CSV tables and rules.
Python 3.8+, standard library only.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shared import checks, evidence  # noqa: E402

NAME = "tracking-audit"
TOOL_VERSION = "0.1.0"  # keep equal to SKILL.md metadata.version
TITLE = {"zh": "广告与追踪体检", "en": "Ad tracking audit"}

# Delivery: <Documents>/<ROOT_NAME>/<subject> <RUN_LABEL> <date>/
ROOT_NAME = {"zh": "出海体检报告", "en": "Store checks"}
RUN_LABEL = TITLE
REPORT_NAME = {"zh": "%s 广告与追踪体检报告", "en": "%s ad tracking audit report"}
README_TEXT = {
    "zh": (
        "这是生成报告用的原始数据, 普通用户可以不看.\n"
        "报告在上一级文件夹的 PDF (找不到浏览器时是 .html) 里, "
        "体检清单是上一级文件夹的 .csv 文件, 可以用 Excel 或 WPS 直接打开.\n"
        "user-inputs/ 里是你提供的后台截图和回答, 只存在你自己的电脑上.\n"
        "这里的文件供 AI 助手复核和重新生成报告, 请不要改名或删除.\n"
    ),
    "en": (
        "Raw data used to build the report; you do not need to open it.\n"
        "The report is the PDF (or .html when no browser was found) one folder "
        "up; the checklist .csv there opens in Excel.\n"
        "user-inputs/ holds the screenshots and answers you provided; they stay "
        "on your computer.\n"
        "Keep these files unchanged so the assistant can re-check the report.\n"
    ),
}

# Pages collect.py fetches. Product / collection / privacy URLs are found
# from the homepage at run time; see collect.py.
PAGE_FILES = {
    "homepage": "homepage.html",
    "collection": "collection.html",
    "product": "product.html",
    "cart": "cart.html",
    "privacy": "privacy.html",
}
MAX_CONTAINERS = 5

CSV_TABLES = [
    {
        "source": "checks",
        "name": {"zh": "体检清单.csv", "en": "checklist.csv"},
        "columns": [
            ("检查编号", "Check ID", "id"),
            ("平台", "Platform", "platform_label"),
            ("检查项", "Check", "item"),
            ("结果", "Result", "result_label"),
            ("标签", "Label", "badge"),
            ("严重度", "Severity", "severity_label"),
            ("依据", "Basis", "basis"),
            ("证据位置", "Evidence location", "evidence"),
            ("整改建议", "Fix", "fix"),
            ("需要你提供", "Needs from you", "needs_user_label"),
            ("数据来源", "Data source", "data_source"),
            ("检查时间", "Checked at", "checked_at"),
        ],
    },
]

BADGE_KEYS = evidence.DEFAULT_BADGE_KEYS

# Technical words kept out of the report body: (regex, plain hint).
_TECH = [
    (r"\bfbq\b", "写 'Meta 像素代码'"),
    (r"gtag\(", "写 '谷歌代码'"),
    (r"\bdataLayer\b", "写 '谷歌代码管理器'"),
    (r"\bttq\b", "写 'TikTok 像素代码'"),
    (r"\bpintrk\b", "写 'Pinterest 标签代码'"),
    (r"\bsnaptr\b", "写 'Snap 像素代码'"),
    (r"\bevent_id\b|\beventID\b", "写 '事件编号'"),
    (r"\bgc[sd]=", "放附录"),
    (r"\bruntimeContext\b", "写 '运行环境'"),
    (r"webPixelsConfigList", "写 '店铺后台登记的像素'"),
    (r"\bapiClientId\b", "放附录"),
    (r"products\.json", "放附录"),
    (r"myshopify", "放附录"),
]
_TECH_EN = [
    (r"\bfbq\b", "say 'Meta pixel code'"),
    (r"gtag\(", "say 'Google tag code'"),
    (r"\bdataLayer\b", "say 'Google Tag Manager'"),
    (r"\bttq\b", "say 'TikTok pixel code'"),
    (r"\bpintrk\b", "say 'Pinterest tag code'"),
    (r"\bsnaptr\b", "say 'Snap pixel code'"),
    (r"\bevent_id\b|\beventID\b", "say 'event ID'"),
    (r"\bgc[sd]=", "move to the appendix"),
    (r"\bruntimeContext\b", "say 'sandbox type'"),
    (r"webPixelsConfigList", "say 'pixels registered in the store admin'"),
    (r"\bapiClientId\b", "move to the appendix"),
    (r"products\.json", "move to the appendix"),
    (r"myshopify", "move to the appendix"),
]
EXTRA_TECH_TERMS = {"zh": _TECH, "en": _TECH_EN}

# Conclusions this skill must never state: (regex, why with %s, fix).
FORBIDDEN_TERMS = {
    "zh": [
        (
            r"已合规|不合规|违法|违规",
            "隐私部分只是提示, 不能下 '%s' 这种法律判断.",
            "改成 '提示: ...', 引用监管机构原话, 并说明请咨询专业人士.",
        ),
    ],
    "en": [
        (
            r"(?i)\b(non-?compliant|is compliant|illegal|unlawful)\b",
            "Privacy notes are hints; '%s' is a legal verdict.",
            "Rephrase as a note quoting the regulator and suggest professional advice.",
        ),
    ],
}

WORKING_ZH = r"正常工作|正常发送|在正常|已生效|生效了|去重生效|回传正常|成功回传"
WORKING_EN = r"(?i)\b(is working|works fine|is firing|fires correctly|dedup\w* works|is deduplicated)\b"
CHECK_EVIDENCE_ZH = r"截图|实测|测试单|你提供|原文|源码"
CHECK_EVIDENCE_EN = r"(?i)screenshot|browser test|test order|you provided|source text"
PLATFORM_WORDS = (
    r"(?i)meta|facebook|谷歌|google|ga4|gtm|tiktok|pinterest|snap|klaviyo|像素|pixel"
)


def _not_installed_rule(lang):
    rx = re.compile(
        r"没装|未安装|没有安装|没安装"
        if lang == "zh"
        else r"(?i)not installed|isn't installed"
    )
    why = {
        "zh": "'没看到' 不等于 '没装': 这里只查了公开页面.",
        "en": "'Not seen' is not 'not installed': only public pages were checked.",
    }[lang]
    fix = {
        "zh": "改成 '本次检查的页面里没看到 [查不到]'.",
        "en": "Say 'not seen on the pages checked [Not found]'.",
    }[lang]

    def rule(ctx):
        out = []
        for ln, line in ctx.body_lines():
            if rx.search(line) and "not_found" in ctx.badges_in(line):
                out.append(ctx.issue("error", "not_seen_as_absent", ln, line, why, fix))
        return out

    return rule


def _overlabel_rule(lang):
    """Free tier: a tag line marked verified needs a screenshot / test basis."""
    ev = re.compile(CHECK_EVIDENCE_ZH if lang == "zh" else CHECK_EVIDENCE_EN)
    plat = re.compile(PLATFORM_WORDS)
    why = {
        "zh": "公开页面只能证明代码在页面里, [已核实] 需要截图或浏览器实测作依据.",
        "en": "Public pages only show the code exists; [Verified] needs a screenshot or browser test.",
    }[lang]
    fix = {
        "zh": "改成 [页面显示], 或在同一行写明依据 (例: 依据: 你提供的截图).",
        "en": "Use [Shown on page], or name the screenshot / test on the same line.",
    }[lang]

    def rule(ctx):
        out = []
        for ln, line in ctx.body_lines():
            if line.lstrip().startswith("#"):
                continue
            for cell in line.split("|"):
                if "verified" in ctx.badges_in(cell) and plat.search(cell):
                    if not ev.search(cell):
                        out.append(
                            ctx.issue("warning", "over_label", ln, line, why, fix)
                        )
                        break
        return out

    return rule


def _working_rule(lang):
    msg = {
        "zh": (
            "'在工作/已生效' 只能由截图或实测证明, 不能只靠 [页面显示] 或 [推测].",
            "改成 '代码在页面里 [页面显示]', 或附上截图依据并标 [已核实].",
        ),
        "en": (
            "'Working / firing' needs a screenshot or browser test, not [Shown on page] or [Inferred].",
            "Say 'the code is on the page [Shown on page]' or cite the screenshot.",
        ),
    }[lang]
    return checks.claim_badge_rule(
        WORKING_ZH if lang == "zh" else WORKING_EN,
        "code_as_working",
        msg[0],
        msg[1],
        forbidden=("on_page", "inferred"),
    )


RULES = {
    lang: [_not_installed_rule(lang), _overlabel_rule(lang), _working_rule(lang)]
    for lang in ("zh", "en")
}

DATA_FILES = ["references/tracking-signatures.json"]
DATA_MAX_AGE_DAYS = 90

try:  # Pro bundle only: extra tables and rules
    import pro_extend  # noqa: E402

    CSV_TABLES = CSV_TABLES + list(getattr(pro_extend, "CSV_TABLES", []))
    for _lang in ("zh", "en"):
        RULES[_lang] = RULES[_lang] + list(
            getattr(pro_extend, "RULES", {}).get(_lang, [])
        )
except ImportError:
    pro_extend = None


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
