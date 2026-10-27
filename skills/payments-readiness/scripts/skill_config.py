"""Per-skill settings: names, folders, CSV columns, extra self-check rules.

The other scripts only read from here, so renaming a folder or a column is a
one-line change.
Python 3.8+, standard library only.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shared import checks, evidence  # noqa: E402

NAME = "payments-readiness"
TOOL_VERSION = "0.1.0"  # keep equal to SKILL.md metadata.version
TITLE = {"zh": "收款上线体检", "en": "Payments readiness check"}

# Delivery: <Documents>/<ROOT_NAME>/<subject> <RUN_LABEL> <date>/
ROOT_NAME = {"zh": "出海拆解报告", "en": "Store reports"}
RUN_LABEL = {"zh": "收款体检", "en": "payments check"}
REPORT_NAME = {"zh": "%s 收款体检报告", "en": "%s payments readiness report"}
README_TEXT = {
    "zh": (
        "这是生成报告用的原始数据, 普通用户可以不看.\n"
        "报告在上一级文件夹的 PDF (找不到浏览器时是 .html) 里, "
        "整改清单和费用测算是上一级文件夹的 .csv 文件, 可以用 Excel 或 WPS 直接打开.\n"
        "这里的文件供 AI 助手复核和重新生成报告, 请不要改名或删除.\n"
    ),
    "en": (
        "Raw data used to build the report; you do not need to open it.\n"
        "The report is the PDF (or .html when no browser was found) one folder "
        "up; the fix list and fee estimate .csv files there open in Excel.\n"
        "Keep these files unchanged so the assistant can re-check the report.\n"
    ),
}

# Shipped data file with valid_as_of (relative to the skill folder).
RATES_FILE = "references/rates.json"
DATA_FILES = [RATES_FILE]
DATA_MAX_AGE_DAYS = 90

# CSV tables: file name per language and (zh header, en header, field).
CSV_TABLES = [
    {
        "source": "fix_list",
        "name": {"zh": "整改清单.csv", "en": "fix-list.csv"},
        "columns": [
            ("优先级", "Priority", "priority"),
            ("编号", "ID", "id"),
            ("检查项", "Check", "check"),
            ("发现", "Finding", "finding"),
            ("证据标签", "Evidence", "badge"),
            ("证据位置", "Evidence location", "where"),
            ("怎么改", "How to fix", "fix"),
            ("谁来改", "Who fixes it", "who"),
            ("状态", "Status", "state"),
            ("体检日期", "Checked on", "date"),
        ],
    },
    {
        "source": "fee_rows",
        "name": {"zh": "费用测算.csv", "en": "fee-estimate.csv"},
        "columns": [
            ("路径", "Route", "route"),
            ("费用项", "Fee item", "item"),
            ("费率", "Rate", "rate"),
            ("固定费", "Fixed fee", "fixed"),
            ("适用比例", "Share applied", "share"),
            ("月单量", "Orders per month", "orders"),
            ("月费用", "Monthly cost", "monthly"),
            ("年费用", "Yearly cost", "yearly"),
            ("币种", "Currency", "currency"),
            ("费率来源编号", "Rate source ID", "source_id"),
            ("取数日期", "Retrieved", "retrieved"),
            ("是否核实", "Verified?", "verified"),
        ],
    },
]

# Numbers and facts the seller typed in are shown as [你提供的] / [You provided].
BADGE_KEYS = evidence.DEFAULT_BADGE_KEYS + ("user_input",)

# Extra technical words kept out of the report body: (regex, plain hint).
EXTRA_TECH_TERMS = {
    "zh": [
        (r"products\.json", "公开商品数据"),
        (r"(?i)myshopify", "店铺后台域名, 放附录"),
        (r"meta\.json", "公开店铺信息"),
        (r"\bSDK\b", "支付脚本"),
        (r"(?i)\bgateway\b", "收款通道"),
        (r"(?i)\bdescriptor\b", "账单显示名"),
        (r"questionnaire\.json|findings\.json|fees\.json", "放附录"),
    ],
    "en": [
        (r"products\.json", "public product data"),
        (r"(?i)myshopify", "admin domain, appendix only"),
        (r"meta\.json", "public store info"),
        (r"\bSDK\b", "payment script"),
        (r"questionnaire\.json|findings\.json|fees\.json", "appendix only"),
    ],
}

# Conclusions this skill must never state: (regex, why, fix).
FORBIDDEN_TERMS = {
    "zh": [
        (
            r"保证(?:能)?(?:开通|开户|通过|过审|解冻)|包过|稳过|必过|一定(?:能)?(?:通过|开通|过审)",
            "'%s' 是在承诺通道方的审核结果, 那由通道方决定, 本报告不能保证.",
            "改成 '通道方审核时常看的项目, 先改好可以少被追问' 这类说法.",
        ),
        (
            r"不会被(?:冻结|封|拒)|不会冻结|永不冻结",
            "'%s' 是在承诺风控结果, 本报告不能保证.",
            "删掉; 改成 '能减少被追问的地方'.",
        ),
        (
            r"稳赚|保证收益|保证盈利|月入",
            "'%s' 属于收益承诺, 违反诚实红线.",
            "删掉, 只写手续费估算.",
        ),
        (
            r"最省钱的(?:方案|通道)|推荐你用|首选通道",
            "'%s' 是通道推荐排名, 本技能只并列展示费用, 不推荐通道.",
            "改成并列列出各路径的费用, 让卖家自己选.",
        ),
    ],
    "en": [
        (
            r"(?i)guarantee(?:d|s)? (?:approval|activation|to (?:be )?(?:approved|pass))"
            r"|will (?:definitely |certainly )?(?:be approved|pass review)",
            "'%s' promises the provider's review outcome, which only they decide.",
            "Say 'items providers commonly review; fixing them avoids follow-up questions'.",
        ),
        (
            r"(?i)(?:won't|will not|never) be (?:frozen|suspended|declined)",
            "'%s' promises a risk outcome this report cannot guarantee.",
            "Delete it; say 'reduces avoidable questions' instead.",
        ),
        (
            r"(?i)guaranteed (?:profit|income|returns?)",
            "'%s' is an income promise.",
            "Delete it; only state fee estimates.",
        ),
        (
            r"(?i)cheapest (?:option|provider|route)|we recommend (?:using )?\w+ as",
            "'%s' ranks providers; this skill lists fees side by side only.",
            "List each route's fees side by side and let the seller decide.",
        ),
    ],
}

# "Enabled / connected" claims need more than an icon or a script.
ENABLED_RE = {
    "zh": r"已开通|已经开通|已连接|已接入|已启用|已经启用",
    "en": r"(?i)\b(?:is|are|been) (?:enabled|activated|connected|live)\b",
}
NEGATION_RE = {
    "zh": r"不代表|不等于|不能说明|不能证明|是否|有没有|能不能|看不出|无法确认|未必|不一定",
    "en": r"(?i)\bnot\b|n't\b|\bwhether\b|\bcannot\b|\bunknown\b",
}
ENABLED_MSG = {
    "zh": (
        "这句说某种付款方式 '已开通', 依据只有 [页面显示] (图标/脚本). 图标和脚本不等于后台已开通.",
        "改成 '页面上放了 X 的图标 [页面显示], 是否开通 [查不到]'; 有后台截图才能写已开通.",
        "这句写了 '已开通' 却没有任何证据标签.",
        "补上依据: 用户截图写 [已核实], 用户口述写 [你提供的]; 只有图标就改写成 '页面显示了图标'.",
    ),
    "en": (
        "This line says a payment method is enabled, backed only by [Shown on page] (icon/script).",
        "Say 'the page shows the X icon [Shown on page]; whether it is enabled [Not found]'.",
        "This line claims something is enabled without any evidence label.",
        "Add the basis: a screenshot is [Verified], the seller's own word is [You provided].",
    ),
}


def enabled_rules(lang):
    """'Enabled / connected' claims: error when backed only by [页面显示],
    warning when carrying no badge at all. Negated wording ("图标不代表已开通")
    is exempt from both."""
    why_icon, fix_icon, why_none, fix_none = ENABLED_MSG[lang]
    claim_re = re.compile(ENABLED_RE[lang])
    neg_re = re.compile(NEGATION_RE[lang])

    def rule(ctx):
        out = []
        for ln, line in ctx.body_lines():
            if line.lstrip().startswith("#"):
                continue
            for cell in line.split("|"):
                for sentence in re.split(r"(?<=[.;!?])\s+", cell):
                    if not claim_re.search(sentence) or neg_re.search(sentence):
                        continue
                    keys = [k for k in ctx.badges_in(sentence) if k]
                    if keys and all(k == "on_page" for k in keys):
                        out.append(
                            ctx.issue(
                                "error", "icon_as_enabled", ln, line, why_icon, fix_icon
                            )
                        )
                    elif not keys:
                        out.append(
                            ctx.issue(
                                "warning",
                                "enabled_no_badge",
                                ln,
                                line,
                                why_none,
                                fix_none,
                            )
                        )
                    else:
                        continue
                    break
        return out

    return [rule]


RULES = {"zh": enabled_rules("zh"), "en": enabled_rules("en")}


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
