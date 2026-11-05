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

NAME = "competitor-change-radar"
TOOL_VERSION = "0.1.0"  # keep equal to SKILL.md metadata.version
TITLE = {"zh": "竞品变化雷达", "en": "Competitor change radar"}

# Delivery: <root>/<RADAR_DIR>/<domain>/{radar.json, <SNAP_DIR>/, report dirs}
# <root> = --out-root > $CHUHAI_OUTPUT_DIR > cloud sandbox > Documents/<ROOT_NAME>.
# The zh root is shared with shopify-store-teardown so its runs can be
# imported as a baseline.
ROOT_NAME = {"zh": "出海拆解报告", "en": "Store teardowns"}
RADAR_DIR = {"zh": "雷达", "en": "radar"}
SNAP_DIR = {"zh": "快照", "en": "snapshots"}
# Scheduled (OS scheduler) runs cannot write into Documents on macOS without
# Full Disk Access, so they use this home-level root; interactive runs look
# here too when the normal root has no history for a store.
SCHEDULED_ROOT = os.path.join("~", "出海拆解报告")
DIFF_DIR = {"zh": "变化报告 %s 至 %s", "en": "Changes %s to %s"}
BASELINE_DIR = {"zh": "基线报告 %s", "en": "Baseline %s"}
REPORT_NAME = {"zh": "%s 竞品变化报告", "en": "%s competitor change report"}
BASELINE_REPORT_NAME = {"zh": "%s 竞品基线报告", "en": "%s competitor baseline report"}
SNAPSHOT_NAME = {"zh": "本次公开快照.json", "en": "current-public-snapshot.json"}
DEMO_DOMAIN = "demo-outdoors.example"
DEMO_NOTE = {"zh": "演示数据, 非真实店铺", "en": "Demo data, not a real store"}
CADENCE_DAYS = 7
README_TEXT = {
    "zh": (
        "这是生成竞品变化报告用的数据, 普通用户可以不看.\n"
        "报告在上一级文件夹的 PDF (找不到浏览器时是 .html) 里, "
        "表格是上一级文件夹的 .csv 文件, 可以用 Excel 或 WPS 直接打开.\n"
        '上一级的 "本次公开快照.json" 可以在下次运行时重新上传用于比较.\n'
        "这里的 diff.json, diff.md, report.md, snapshot.json 供 AI 助手复核和重新生成报告, "
        "两次快照的原始数据在店铺文件夹的 快照/ 下, 请不要改名或删除.\n"
    ),
    "en": (
        "Data used to build the competitor change report; you do not need to "
        "open it.\nThe report is the PDF (or .html when no browser was found) "
        "one folder up; the .csv tables there open in Excel.\n"
        "current-public-snapshot.json one folder up can be uploaded again for "
        "a future comparison. diff.json, diff.md, report.md and snapshot.json "
        "let the assistant re-check the "
        "report; the raw snapshots live in the store folder under snapshots/. "
        "Keep these files unchanged.\n"
    ),
}

# CSV tables written by deliver.py from <raw-data>/tables.json. "source" is the
# key in tables.json; tables without rows there are skipped (a diff report has
# the first three, a baseline report only the last).
CSV_TABLES = [
    {
        "source": "price_changes",
        "name": {"zh": "价格变动.csv", "en": "price-changes.csv"},
        "columns": [
            ("商品", "Product", "title"),
            ("变体", "Variant", "variant"),
            ("SKU", "SKU", "sku"),
            ("原价", "Old price", "before"),
            ("现价", "New price", "after"),
            ("变化额", "Change", "delta"),
            ("变化%", "Change %", "delta_pct"),
            ("原划线价", "Old compare-at", "compare_before"),
            ("现划线价", "New compare-at", "compare_after"),
            ("原折扣%", "Old discount %", "discount_before"),
            ("现折扣%", "New discount %", "discount_after"),
            ("币种", "Currency", "currency"),
            ("网址", "URL", "url"),
            ("事件编号", "Event", "event_id"),
        ],
    },
    {
        "source": "product_changes",
        "name": {"zh": "上新下架.csv", "en": "new-and-removed.csv"},
        "columns": [
            ("变化类型", "Change", "change"),
            ("商品", "Product", "title"),
            ("类型", "Type", "product_type"),
            ("价格", "Price", "price"),
            ("发布日期", "Published", "published"),
            ("网址", "URL", "url"),
            ("事件编号", "Event", "event_id"),
        ],
    },
    {
        "source": "all_events",
        "name": {"zh": "全部变化.csv", "en": "all-changes.csv"},
        "columns": [
            ("事件编号", "Event", "event_id"),
            ("变化", "Change", "label"),
            ("重要程度", "Severity", "severity"),
            ("商品或对象", "Item", "item"),
            ("之前", "Before", "before"),
            ("之后", "After", "after"),
            ("变化%", "Change %", "delta_pct"),
            ("证据", "Evidence", "badge"),
            ("网址", "URL", "url"),
        ],
    },
    {
        "source": "current_products",
        "name": {"zh": "当前商品清单.csv", "en": "current-products.csv"},
        "columns": [
            ("商品", "Product", "title"),
            ("类型", "Type", "product_type"),
            ("最低价", "Lowest price", "price"),
            ("划线价", "Compare-at", "compare_at"),
            ("在打折", "On sale", "on_sale"),
            ("有货", "In stock", "available"),
            ("发布日期", "Published", "published"),
            ("币种", "Currency", "currency"),
            ("网址", "URL", "url"),
        ],
    },
]

BADGE_KEYS = evidence.DEFAULT_BADGE_KEYS
# Extra technical words kept out of the report body: (regex, plain hint).
_TECH_ZH = [
    (r"(?<![A-Za-z0-9])L[012](?![0-9])", "改成 快速核验 / 深度拆解"),
    (r"products\.json|collections\.json|meta\.json", "店铺公开的商品数据"),
    (r"(?:diff|snapshot|radar|tables|run)\.json|diff\.md", "放附录"),
    (r"summary\.json|evidence\.jsonl|products\.tsv", "放附录"),
    (r"compare_at(?:_price)?", "划线价"),
    (r"variant_id|product_id", "放附录"),
    (r"published_at|created_at|updated_at", "发布日期 / 创建日期"),
    (r"myshopify", "店铺在 Shopify 上的原始域名"),
    (r"Shopify\.theme", "店铺用的主题"),
    (r"hreflang", "多国站点设置"),
    (r"web\.archive\.org|Wayback|\bCDX\b", "网页历史存档"),
    (r"pagination", "翻页"),
]
_TECH_EN = [
    (r"(?<![A-Za-z0-9])L[012](?![0-9])", "say quick check / deep teardown"),
    (r"products\.json|collections\.json|meta\.json", "the store's public product data"),
    (r"(?:diff|snapshot|radar|tables|run)\.json|diff\.md", "move to the appendix"),
    (r"summary\.json|evidence\.jsonl|products\.tsv", "move to the appendix"),
    (r"compare_at(?:_price)?", "compare-at price"),
    (r"variant_id|product_id", "move to the appendix"),
    (r"published_at|created_at|updated_at", "publish / creation date"),
    (r"myshopify", "the store's original Shopify domain"),
    (r"Shopify\.theme", "the store's theme"),
    (r"hreflang", "country / language sites"),
    (r"web\.archive\.org|\bCDX\b", "web archive"),
]
EXTRA_TECH_TERMS = {"zh": _TECH_ZH, "en": _TECH_EN}

# Conclusions the skill must never state: (regex, why with %s, fix).
FORBIDDEN_TERMS = {
    "zh": [
        (
            r"全部下架|全店下架|清空了商品",
            "'%s' 是结论, 公开数据拿不到时只能说没查到, 不能说下架.",
            "改成 '这次没能读到商品数据 [查不到]', 下次再比.",
        ),
    ],
    "en": [
        (
            r"(?i)removed all products|delisted everything",
            "'%s' is a conclusion; missing data is not a removal.",
            "Say 'product data could not be read this time [Not found]'.",
        ),
    ],
}

PLACEHOLDER_RE = r"<待写|<TODO|TODO>|<to be written"
SALES_RE = {
    "zh": r"销量|卖得(?:好|不好|差)|爆单|爆款|清库存|清仓甩卖|利润",
    "en": r"(?i)\bsales (?:volume|are|dropped|rose)|selling (?:well|badly)|clearing (?:out )?stock|profit",
}
ARCHIVE_RE = {"zh": r"存档", "en": r"(?i)\barchive"}
NEXT_RUN_RE = {"zh": r"下次建议", "en": r"(?i)next suggested run"}


def _placeholder_rule(lang):
    regex = re.compile(PLACEHOLDER_RE)
    why = {
        "zh": "还有没写完的占位符 '%s'.",
        "en": "A placeholder '%s' is still there.",
    }[lang]
    fix = {
        "zh": "按 diff.md 的数据写完这一处, 删掉占位符.",
        "en": "Write this part from diff.md and remove the placeholder.",
    }[lang]

    def rule(ctx):
        out = []
        for ln, line in ctx.lines:
            m = regex.search(line)
            if m:
                out.append(
                    ctx.issue("error", "placeholder", ln, line, why % m.group(0), fix)
                )
        return out

    return rule


def _next_run_rule(lang):
    regex = re.compile(NEXT_RUN_RE[lang])
    why = {
        "zh": "'一页看懂' 没写下次建议什么时候再跑.",
        "en": "'At a glance' does not say when to run again.",
    }[lang]
    fix = {
        "zh": "在 '一页看懂' 末尾加一行: 下次建议 <日期> 再跑.",
        "en": "Add a line to 'At a glance': Next suggested run: <date>.",
    }[lang]

    def rule(ctx):
        if any(regex.search(s) for _ln, s in ctx.body_lines()):
            return []
        return [ctx.issue("error", "no_next_run", 0, "", why, fix)]

    return rule


RULES = {
    lang: [
        _placeholder_rule(lang),
        _next_run_rule(lang),
        checks.claim_badge_rule(
            SALES_RE[lang],
            "sales_claim",
            {
                "zh": "价格和上新变化推不出销量或利润, 这类说法只能是 [推测] 加依据.",
                "en": "Price and listing changes say nothing certain about sales; "
                "such a statement must be [Inferred] with a Basis.",
            }[lang],
            {
                "zh": "改成 '... [推测] 依据: <事件编号>', 或删掉.",
                "en": "Write '... [Inferred] Basis: <event id>', or drop it.",
            }[lang],
            forbidden=("verified", "on_page", "self_claim", "third_party"),
        ),
        checks.claim_badge_rule(
            ARCHIVE_RE[lang],
            "archive_verified",
            {
                "zh": "来自网页历史存档的变化最高只能标 [页面显示].",
                "en": "Changes based on the web archive are at most [Shown on page].",
            }[lang],
            {
                "zh": "把 [已核实] 改成 [页面显示], 并注明 (来自网页历史存档).",
                "en": "Use [Shown on page] and add (from the web archive).",
            }[lang],
            forbidden=("verified",),
        ),
    ]
    for lang in ("zh", "en")
}
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
