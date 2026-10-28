"""Per-skill settings for profit-model: names, folders, CSV columns, rules.

The other scripts only read from here. ``profile(lang, raw_dir)`` builds the
self-check profile; with a raw-data folder it also enables the rules that
need the run's own numbers (traceability, duty disclosure).
Python 3.8+, standard library only.
"""

import json
import os
import re
import sys
from decimal import Decimal, InvalidOperation

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edition  # noqa: E402
from _shared import checks, evidence  # noqa: E402

NAME = "profit-model"
TOOL_VERSION = "0.1.0"  # keep equal to SKILL.md metadata.version
TITLE = {"zh": "利润测算", "en": "profit model"}

# Delivery: <Documents>/<ROOT_NAME>/<product> <RUN_LABEL> <date>/
ROOT_NAME = {"zh": "出海利润测算", "en": "Profit models"}
RUN_LABEL = TITLE
REPORT_NAME = {"zh": "%s 利润测算报告", "en": "%s profit model report"}
README_TEXT = {
    "zh": (
        "这是生成报告用的原始数据, 普通用户可以不看.\n"
        "报告在上一级文件夹的 PDF (找不到浏览器时是 .html) 里, "
        "表格是上一级文件夹的 .csv 文件, 可以用 Excel 或 WPS 直接打开.\n"
        "inputs.csv 是你的输入, 改了数字可以让 AI 助手重新算.\n"
        "其余文件供 AI 助手复核和重新生成报告, 请不要改名或删除.\n"
    ),
    "en": (
        "Raw data used to build the report; you do not need to open it.\n"
        "The report is the PDF (or .html when no browser was found) one folder "
        "up; the .csv tables there open in Excel.\n"
        "inputs.csv holds your inputs; edit it and ask the assistant to re-run.\n"
        "Keep the other files unchanged so the assistant can re-check the report.\n"
    ),
}

# CSV tables: deliver.py writes one CSV per table whose rows exist in
# summary.json (written by pm_report.py).
CSV_TABLES = [
    {
        "source": "per_order",
        "name": {"zh": "每单测算.csv", "en": "per-order.csv"},
        "columns": [
            ("商品", "Product", "product"),
            ("情景", "Scenario", "scenario"),
            ("售价", "Price", "price"),
            ("实付", "Buyer pays", "G"),
            ("净收入", "Net revenue", "N"),
            ("落地货本", "Landed goods", "L"),
            ("履约", "Fulfilment", "ful"),
            ("尾程", "Last mile", "last"),
            ("退货", "Returns", "returns"),
            ("收款费", "Payment fees", "pay"),
            ("每单贡献", "Contribution", "cm0"),
            ("贡献率%", "Margin %", "cm0_pct"),
            ("盈亏平衡ROAS", "Break-even ROAS", "roas_be"),
            ("最高CPA", "Max CPA", "max_cpa"),
            ("广告后利润", "Profit after ads", "cm1"),
            ("标签", "Label", "badge"),
        ],
    },
    {
        "source": "cost_breakdown",
        "name": {"zh": "成本明细.csv", "en": "cost-breakdown.csv"},
        "columns": [
            ("商品", "Product", "product"),
            ("情景", "Scenario", "scenario"),
            ("项目", "Item", "item"),
            ("金额", "Amount", "amount"),
            ("币种", "Currency", "currency"),
            ("原始值", "Original value", "raw"),
            ("原始币种", "Original currency", "raw_currency"),
            ("来源", "Source", "source"),
            ("标签", "Label", "badge"),
        ],
    },
    {
        "source": "startup",
        "name": {"zh": "启动现金.csv", "en": "startup-cash.csv"},
        "columns": [
            ("路线", "Route", "route"),
            ("项目", "Item", "item"),
            ("金额", "Amount", "amount"),
            ("币种", "Currency", "currency"),
            ("约合人民币", "About CNY", "cny"),
            ("依据", "Label", "badge"),
        ],
    },
    {
        "source": "sensitivity",
        "name": {"zh": "敏感度.csv", "en": "sensitivity.csv"},
        "columns": [
            ("商品", "Product", "product"),
            ("项目", "Item", "item"),
            ("-10% 时利润", "Profit at -10%", "down"),
            ("+10% 时利润", "Profit at +10%", "up"),
            ("影响幅度", "Impact", "impact"),
            ("排名", "Rank", "rank"),
        ],
    },
]

# The Pro edition adds its own tables (channel comparison, cash plan ...).
CSV_TABLES = CSV_TABLES + list(getattr(edition, "EXTRA_CSV_TABLES", []))

BADGE_KEYS = evidence.DEFAULT_BADGE_KEYS + ("user_input",)
# Internal words kept out of the report body: (regex, plain hint).
EXTRA_TECH_TERMS = {
    "zh": [
        (r"ue_model|pm_calc|pm_inputs|pm_report|result\.json", "放附录"),
        (r"inputs\.normalized|fee_tables|\bDecimal\b", "放附录"),
        (r"\bCM[01]\b", "写成 每单贡献 / 广告后每单利润"),
    ],
    "en": [
        (r"ue_model|pm_calc|pm_inputs|pm_report|result\.json", "move to the appendix"),
        (r"inputs\.normalized|fee_tables|\bDecimal\b", "move to the appendix"),
        (r"\bCM[01]\b", "say contribution / profit after ads"),
    ],
}
# Promises the skill must never make: (regex, why with %s, fix).
FORBIDDEN_TERMS = {
    "zh": [
        (
            r"稳赚|保证(盈利|赚|回本|利润)|一定(能)?赚|躺赚|包赚",
            '"%s" 是收益承诺, 本技能只做条件测算.',
            '改成 "按你给的数字, 每单贡献 X" 这类条件句.',
        ),
    ],
    "en": [
        (
            r"(?i)guaranteed (profit|income|return)|risk[- ]free|sure profit",
            '"%s" promises income; this skill only computes conditional numbers.',
            'Say "with your numbers, contribution per order is X".',
        ),
    ],
}
RESULT_WORDS = {
    "zh": r"每单贡献|每单赚|每单亏|广告后|利润|回本|盈亏平衡|获客成本|底线价|目标价|启动现金|开张现金|ROAS",
    "en": r"(?i)contribution|profit|break-even|\bCPA\b|price floor|target price|launch cash|\bROAS\b",
}
NUMBER_RE = re.compile(r"(?<![\w.])\d+\.\d+(?![\w.])")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
MONEY_RE = re.compile(r"[$€£¥]\s?\d|\d+\.\d{2}\b")
SECTION_ASSUMPTIONS = {"zh": "假设清单", "en": "Assumptions"}
MSG = {
    "zh": {
        "no_badge": ("结果数字没有标签.", "在这一行加上标签 (取依赖链上最弱的一环)."),
        "verified_no_date": (
            "[已核实] 的句子里没有日期.",
            "写出官方页或费率表的日期 (YYYY-MM-DD), 或者改标 [推测] 依据: ...",
        ),
        "no_assumptions": ('缺 "假设清单" 一节.', "按模板加上 ## 2. 假设清单."),
        "untraced": (
            "数字 %s 在输入, 费率表和计算结果里都找不到.",
            "只写脚本算出的数字; 自己算的数字要写进输入重新跑.",
        ),
        "no_duty_note": (
            '没填关税, 但报告没写 "未含关税".',
            '在一页看懂里写明 "未含关税" 并标 [查不到].',
        ),
    },
    "en": {
        "no_badge": (
            "A result number has no label.",
            "Add the weakest label of its inputs.",
        ),
        "verified_no_date": (
            "A [Verified] line has no date.",
            "Add the official page or fee table date (YYYY-MM-DD), or use [Inferred] Basis: ...",
        ),
        "no_assumptions": (
            "The Assumptions section is missing.",
            "Add ## 2. Assumptions.",
        ),
        "untraced": (
            "Number %s is not in the inputs, the fee table or the results.",
            "Only write numbers the script computed; put your own figures into the inputs.",
        ),
        "no_duty_note": (
            "Duty was not provided but the report does not say duty is excluded.",
            'State "Duty not included" in the summary with [Not found].',
        ),
    },
}
DUTY_NOTE = {"zh": "未含关税", "en": "Duty not included"}
DATA_FILES = ["data/fee_tables.json"]
DATA_MAX_AGE_DAYS = 90


def _rule_result_badges(lang):
    words = re.compile(RESULT_WORDS[lang])
    why, fix = MSG[lang]["no_badge"]

    def rule(ctx):
        out = []
        lines = ctx.body_lines()
        for idx, (ln, line) in enumerate(lines):
            s = line.strip()
            if not s or s.startswith(("#", ">", "|---", "| ---")):
                continue
            nxt = lines[idx + 1][1].strip() if idx + 1 < len(lines) else ""
            if s.startswith("|") and nxt.startswith("|---"):
                continue  # table header
            if words.search(s) and MONEY_RE.search(s) and not ctx.badges_in(s):
                out.append(ctx.issue("error", "result_no_badge", ln, line, why, fix))
        return out

    return rule


def _rule_verified_date(lang):
    why, fix = MSG[lang]["verified_no_date"]
    tag = evidence.badge("verified", lang)

    def rule(ctx):
        return [
            ctx.issue("error", "verified_no_date", ln, line, why, fix)
            for ln, line in ctx.lines
            if tag in line and not DATE_RE.search(line)
        ]

    return rule


def known_numbers(raw_dir):
    """Every decimal that appears in the run's inputs, results and the
    fee table, normalized (16.550 == 16.55)."""
    known = set()
    names = ("inputs.normalized.json", "result.json", "inputs.csv", "fx.json")
    texts = []
    for name in names:
        try:
            # numbers are ASCII; never let an odd byte stop the report check
            with open(
                os.path.join(raw_dir, name), encoding="utf-8-sig", errors="replace"
            ) as fh:
                texts.append(fh.read())
        except OSError:
            continue
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (
        os.path.join(raw_dir, "fee_tables.snapshot.json"),
        os.path.join(here, "..", "data", "fee_tables.json"),
    ):
        try:
            with open(path, encoding="utf-8") as fh:
                texts.append(fh.read())
            break
        except OSError:
            continue
    for text in texts:
        for tok in re.findall(r"\d+\.\d+", text):
            known.add(_norm(tok))
    return known


def _norm(tok):
    try:
        return str(Decimal(tok).normalize())
    except InvalidOperation:
        return tok


def _rule_traceable(lang, known):
    why, fix = MSG[lang]["untraced"]

    def rule(ctx):
        out = []
        for ln, line in ctx.body_lines():
            plain = checks.URL_RE.sub(" ", line)
            for tok in NUMBER_RE.findall(plain):
                if _norm(tok) not in known:
                    out.append(
                        ctx.issue("error", "untraced_number", ln, line, why % tok, fix)
                    )
                    break
        return out

    return rule


def _rule_duty(lang):
    why, fix = MSG[lang]["no_duty_note"]

    def rule(ctx):
        if DUTY_NOTE[lang] not in ctx.md_text:
            return [ctx.issue("error", "no_duty_note", 0, "", why, fix)]
        return []

    return rule


def rules(lang, raw_dir=None):
    out = [_rule_result_badges(lang), _rule_verified_date(lang)]
    if raw_dir and os.path.isfile(os.path.join(raw_dir, "result.json")):
        out.append(_rule_traceable(lang, known_numbers(raw_dir)))
        try:
            with open(os.path.join(raw_dir, "result.json"), encoding="utf-8") as fh:
                result = json.load(fh)
        except (OSError, ValueError):
            result = {}
        if "duty_missing" in (result.get("flags") or []) and any(
            not s.get("blocked") for s in result.get("skus", [])
        ):
            out.append(_rule_duty(lang))
        if any(not s.get("blocked") for s in result.get("skus", [])):
            out.append(_rule_assumptions_simple(lang))
    return out


def _rule_assumptions_simple(lang):
    why, fix = MSG[lang]["no_assumptions"]
    word = SECTION_ASSUMPTIONS[lang]

    def rule(ctx):
        if not any(s.startswith("## ") and word in s for _ln, s in ctx.lines):
            return [ctx.issue("error", "no_assumptions", 0, "", why, fix)]
        return []

    return rule


def profile(lang, raw_dir=None):
    """Self-check profile for one language (used by check_report / deliver)."""
    return checks.Profile(
        lang=lang,
        badge_keys=BADGE_KEYS,
        extra_tech_terms=EXTRA_TECH_TERMS[lang],
        forbidden_terms=FORBIDDEN_TERMS[lang],
        rules=rules(lang, raw_dir),
        require_status=True,
    )
