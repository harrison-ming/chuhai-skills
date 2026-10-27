"""Input field registry for profit-model: keys, names, kinds, directions.

One row per input. ``kind`` decides parsing and validation; ``cons`` is the
conservative direction ("high": a higher value loses more money, "low": a
lower value loses more money, None: no direction). Names are what the CSV
"项目" column may contain (any listed alias, Chinese or English, or the key).
Python 3.8+, standard library only.
"""

import re

# (key, kind, zh name, en name, conservative direction, extra aliases)
_F = [
    # market
    ("market", "text", "销售市场", "Market", None, ["市场", "站点"]),
    ("sale_currency", "currency", "售价币种", "Sale currency", None, ["币种"]),
    (
        "fx_cny_per_unit",
        "rate",
        "汇率",
        "Exchange rate (CNY per unit)",
        "low",
        ["汇率 (1 单位售价币种 = ? 元)", "fx", "fx_rate", "exchange rate"],
    ),
    ("fx_date", "text", "汇率日期", "Exchange rate date", None, []),
    ("fx_source", "text", "汇率来源", "Exchange rate source", None, []),
    # payments
    (
        "payment_provider",
        "choice",
        "收款方式",
        "Payment provider",
        None,
        ["收款通道", "支付方式"],
    ),
    ("seller_entity", "choice", "收款主体", "Seller entity", None, ["主体"]),
    ("shopify_plan", "choice", "Shopify 套餐", "Shopify plan", None, ["套餐"]),
    (
        "payment_pct",
        "pct",
        "收款费率",
        "Payment fee %",
        "high",
        ["收款费", "支付费率", "手续费率"],
    ),
    (
        "payment_fixed",
        "money",
        "收款每笔固定费",
        "Payment fixed fee",
        "high",
        ["每笔固定费", "收款固定费", "固定手续费"],
    ),
    (
        "cross_border_pct",
        "pct",
        "跨境卡附加费",
        "Cross-border card surcharge %",
        "high",
        ["国际卡附加费", "境外卡附加费"],
    ),
    (
        "fx_conversion_pct",
        "pct",
        "结汇换汇价差",
        "Currency conversion %",
        "high",
        ["结汇价差", "换汇价差", "换汇费", "结汇成本"],
    ),
    (
        "third_party_txn_pct",
        "pct",
        "Shopify 第三方交易费",
        "Shopify third-party fee %",
        "high",
        ["第三方交易费", "第三方网关交易费"],
    ),
    # product
    ("price", "money", "售价", "Price", "low", ["销售价", "单价", "零售价"]),
    ("units_per_order", "count", "每单件数", "Units per order", None, ["件数"]),
    (
        "discount_pct",
        "pct",
        "平均折扣",
        "Average discount %",
        "high",
        ["折扣", "折扣率"],
    ),
    (
        "shipping_charged",
        "money",
        "向买家收的运费",
        "Shipping charged to buyer",
        "low",
        ["买家运费", "收取运费"],
    ),
    ("vat_pct", "pct", "增值税率", "VAT %", "high", ["增值税", "VAT"]),
    # goods
    (
        "product_cost",
        "money",
        "采购单价",
        "Product cost",
        "high",
        ["采购价", "采购成本", "进货价", "货值"],
    ),
    (
        "packaging",
        "money",
        "包装",
        "Packaging",
        "high",
        ["包装费", "包装/吊牌/说明书", "包装吊牌说明书"],
    ),
    (
        "first_mile_per_unit",
        "money",
        "头程",
        "First mile per unit",
        "high",
        ["头程运费", "头程 (每件)"],
    ),
    # duty
    (
        "duty_rate_pct",
        "pct",
        "关税合计税率",
        "Duty rate %",
        "high",
        ["关税", "关税税率", "关税率"],
    ),
    ("duty_basis", "choice", "关税计税基数", "Duty basis", None, ["计税基数"]),
    (
        "duty_fixed_per_order",
        "money",
        "每单固定税费",
        "Fixed duty per order",
        "high",
        ["每单定额税"],
    ),
    # fulfilment
    (
        "fulfil_per_order",
        "money",
        "拣货打包",
        "Pick and pack per order",
        "high",
        ["拣货", "拣货费", "海外仓拣货"],
    ),
    (
        "storage_per_unit",
        "money",
        "仓储",
        "Storage per unit",
        "high",
        ["仓储费", "仓储 (每件)"],
    ),
    (
        "last_mile_per_order",
        "money",
        "尾程运费",
        "Last mile per order",
        "high",
        ["尾程", "派送运费", "全程运费", "运费"],
    ),
    # returns
    ("return_rate_pct", "pct", "退货率", "Return rate %", "high", []),
    (
        "return_ship_per_return",
        "money",
        "每次退货运费",
        "Return shipping per return",
        "high",
        ["退货运费"],
    ),
    (
        "resale_pct",
        "pct",
        "退回货可再售比例",
        "Resellable share of returns %",
        "low",
        ["可再售比例", "退回货能再卖的比例", "再售比例"],
    ),
    ("return_mode", "choice", "退货处理方式", "Return handling", None, ["退货方式"]),
    (
        "other_variable_per_order",
        "money",
        "其他每单成本",
        "Other cost per order",
        "high",
        ["其他", "其他每单", "赠品"],
    ),
    # ads
    ("cpa", "money", "获客成本", "CPA", "high", ["每单获客成本", "广告成本", "CAC"]),
    ("roas", "ratio", "广告 ROAS", "ROAS", "low", ["广告回报率"]),
    (
        "target_margin_pct",
        "pct",
        "目标利润率",
        "Target margin %",
        None,
        ["目标净利率"],
    ),
    # fixed per month
    (
        "plan_fee_month",
        "money",
        "套餐月费",
        "Plan fee per month",
        "high",
        ["Shopify 月费", "店铺月费"],
    ),
    (
        "apps_month",
        "money",
        "插件月费",
        "Apps per month",
        "high",
        ["应用月费", "插件费"],
    ),
    (
        "3pl_min_month",
        "money",
        "海外仓月最低消费",
        "3PL monthly minimum",
        "high",
        ["3PL 月最低消费", "海外仓月费"],
    ),
    (
        "other_fixed_month",
        "money",
        "其他月固定成本",
        "Other fixed per month",
        "high",
        ["其他月固定", "其他月费"],
    ),
    # startup
    (
        "moq",
        "count",
        "首批数量",
        "First order quantity",
        None,
        ["首批量", "MOQ", "起订量"],
    ),
    (
        "min_moq",
        "count",
        "最小起订量",
        "Minimum order quantity",
        None,
        ["最省路线首批量"],
    ),
    ("sample_cost", "money", "打样费", "Samples", "high", ["样品费"]),
    ("deposit", "money", "保证金", "Deposits", "high", ["平台保证金"]),
    (
        "inbound_3pl_fee",
        "money",
        "海外仓入仓费",
        "3PL inbound fee",
        "high",
        ["入仓费", "3PL 入仓费"],
    ),
    (
        "launch_ad_budget",
        "money",
        "测款广告预算",
        "Launch ad budget",
        "high",
        ["启动广告预算"],
    ),
    (
        "runway_months",
        "months",
        "回本前月数",
        "Months before payback",
        "high",
        ["备用月数"],
    ),
    ("production_days", "days", "生产天数", "Production days", "high", []),
    ("transit_days", "days", "运输天数", "Transit days", "high", ["海运天数"]),
    (
        "supplier_deposit_pct",
        "pct",
        "工厂定金比例",
        "Supplier deposit %",
        "high",
        ["定金比例"],
    ),
    (
        "payout_delay_days",
        "days",
        "回款天数",
        "Payout delay days",
        "high",
        ["回款周期"],
    ),
    ("daily_units", "decimal", "日销件数", "Units sold per day", "low", ["日销量"]),
    (
        "competitor_price_low",
        "money",
        "竞品最低价",
        "Competitor price low",
        None,
        [],
    ),
    (
        "competitor_price_high",
        "money",
        "竞品最高价",
        "Competitor price high",
        None,
        [],
    ),
    # Pro increment (ignored by the free edition)
    ("mix_pct", "share", "销售占比", "Sales mix %", None, []),
    (
        "amazon_referral_pct",
        "pct",
        "亚马逊佣金率",
        "Amazon referral %",
        "high",
        ["亚马逊佣金"],
    ),
    (
        "fba_fee_per_unit",
        "money",
        "FBA 配送费",
        "FBA fee per unit",
        "high",
        ["FBA 费", "FBA费"],
    ),
    (
        "amazon_monthly",
        "money",
        "亚马逊月费",
        "Amazon monthly fee",
        "high",
        [],
    ),
    (
        "tiktok_referral_pct",
        "pct",
        "TikTok 佣金率",
        "TikTok referral %",
        "high",
        ["TikTok 佣金"],
    ),
    (
        "tiktok_affiliate_pct",
        "pct",
        "达人佣金率",
        "Affiliate commission %",
        "high",
        ["达人佣金"],
    ),
    (
        "channel_ad_pct",
        "pct",
        "站内广告占比",
        "On-platform ads % of sales",
        "high",
        ["站内广告"],
    ),
    (
        "repeat_orders",
        "decimal",
        "每个顾客复购次数",
        "Repeat orders per customer",
        "low",
        ["复购次数"],
    ),
    (
        "repeat_interval_days",
        "days",
        "复购间隔天数",
        "Days between repeat orders",
        "high",
        ["复购间隔"],
    ),
    ("monthly_units", "decimal", "月销件数", "Units sold per month", "low", []),
    ("cash_on_hand", "money", "手头现金", "Cash on hand", "low", ["现有资金"]),
]

KINDS = (
    "text",
    "currency",
    "choice",
    "money",
    "pct",
    "share",
    "rate",
    "ratio",
    "count",
    "decimal",
    "days",
    "months",
)
FIELDS = {}
ORDER = []
for _key, _kind, _zh, _en, _cons, _alias in _F:
    assert _kind in KINDS, _key
    FIELDS[_key] = {
        "key": _key,
        "kind": _kind,
        "zh": _zh,
        "en": _en,
        "cons": _cons,
        "aliases": list(_alias),
    }
    ORDER.append(_key)

PRO_KEYS = (
    "mix_pct",
    "amazon_referral_pct",
    "fba_fee_per_unit",
    "amazon_monthly",
    "tiktok_referral_pct",
    "tiktok_affiliate_pct",
    "channel_ad_pct",
    "repeat_orders",
    "repeat_interval_days",
    "monthly_units",
    "cash_on_hand",
)
# Required inputs (a SKU cannot be computed without them). fx and the
# payment provider have fallbacks, see pm_inputs.
REQUIRED = ("market", "price", "product_cost", "last_mile_per_order")
STORE_KEYS = (
    "plan_fee_month",
    "apps_month",
    "3pl_min_month",
    "other_fixed_month",
    "moq",
    "min_moq",
    "sample_cost",
    "deposit",
    "inbound_3pl_fee",
    "launch_ad_budget",
    "runway_months",
    "production_days",
    "transit_days",
    "supplier_deposit_pct",
    "payout_delay_days",
    "daily_units",
    "amazon_monthly",
    "monthly_units",
    "cash_on_hand",
)
FIXED_KEYS = ("plan_fee_month", "apps_month", "3pl_min_month", "other_fixed_month")
# Percent fields where a value between 0 and 1 is probably a fraction typed
# by mistake (8% written as 0.08). Small legit rates (0.6% fees) excluded.
FRACTION_SUSPECT = (
    "payment_pct",
    "discount_pct",
    "vat_pct",
    "duty_rate_pct",
    "return_rate_pct",
    "resale_pct",
    "target_margin_pct",
    "supplier_deposit_pct",
    "mix_pct",
    "amazon_referral_pct",
    "tiktok_referral_pct",
)

STORE_TOKENS = ("全店", "all", "*", "店铺", "store")

CHOICES = {
    "payment_provider": {
        "shopify_payments": [
            "shopify payments",
            "shopifypayments",
            "shopify 收款",
            "shopify收款",
            "shopify",
        ],
        "stripe": ["stripe"],
        "paypal_cn": [
            "paypal_cn",
            "paypal 中国",
            "paypal中国",
            "paypal 中国账户",
            "paypal中国账户",
            "paypal cn",
            "paypal china",
        ],
        "paypal_us": ["paypal_us", "paypal 美国", "paypal美国", "paypal us", "paypal"],
        "paypal_eu": [
            "paypal_eu",
            "paypal 德国",
            "paypal德国",
            "paypal de",
            "paypal eu",
            "paypal 欧洲",
        ],
        "other": ["other", "其他"],
    },
    "seller_entity": {
        "cn": ["cn", "中国大陆", "大陆", "中国", "china", "mainland"],
        "hk": ["hk", "香港", "hong kong"],
        "us": ["us", "美国", "usa"],
        "other": ["other", "其他"],
    },
    "shopify_plan": {
        "basic": ["basic", "基础版", "基础"],
        "grow": ["grow", "shopify"],
        "advanced": ["advanced", "高级版", "高级"],
        "plus": ["plus"],
        "none": ["none", "不用 shopify", "不用shopify", "无", "不用", "no"],
    },
    "duty_basis": {
        "fob": ["fob", "货值", "按货值", "采购价"],
        "price": ["price", "售价", "按售价", "直邮"],
    },
    "return_mode": {
        "refund": ["refund", "退款"],
        "replace": ["replace", "补发", "换货"],
    },
}

CURRENCIES = ("USD", "EUR", "GBP", "CAD", "AUD")
CURRENCY_ALIASES = {
    "CNY": ["cny", "rmb", "元", "人民币", "¥", "￥"],
    "USD": ["usd", "$", "us$", "美元", "美金"],
    "EUR": ["eur", "€", "欧元"],
    "GBP": ["gbp", "£", "英镑"],
    "CAD": ["cad", "ca$", "加元", "加币"],
    "AUD": ["aud", "au$", "澳元"],
}
MARKETS = {
    "US": ["us", "usa", "美国", "美国站", "united states"],
    "UK": ["uk", "gb", "英国", "united kingdom"],
    "CA": ["ca", "加拿大", "canada"],
    "AU": ["au", "澳大利亚", "澳洲", "australia"],
    "EU-DE": ["eu-de", "de", "德国", "germany"],
    "EU-FR": ["eu-fr", "fr", "法国", "france"],
    "EU-IT": ["eu-it", "it", "意大利", "italy"],
    "EU-ES": ["eu-es", "es", "西班牙", "spain"],
    "EU-NL": ["eu-nl", "nl", "荷兰", "netherlands"],
}
MARKET_CURRENCY = {"US": "USD", "UK": "GBP", "CA": "CAD", "AU": "AUD"}
# Markets where prices include VAT and a VAT rate is required.
VAT_MARKET_PREFIX = ("EU-", "UK")

_PUNCT_RE = re.compile("[\\s():/_\\-.\uff08\uff09\uff1a]+")


def norm(text):
    """Lower-case, no spaces or brackets: the lookup form of a name."""
    return _PUNCT_RE.sub("", str(text or "").strip().lower())


_NAME_INDEX = {}
for _key in ORDER:
    _f = FIELDS[_key]
    for _name in [_key, _f["zh"], _f["en"]] + _f["aliases"]:
        _NAME_INDEX.setdefault(norm(_name), _key)


def field_key(item):
    """Field key for a CSV "项目" cell (Chinese name, English name or key)."""
    return _NAME_INDEX.get(norm(item))


def name(key, lang):
    return FIELDS[key]["zh" if lang == "zh" else "en"]


def choice_value(key, text):
    """Canonical choice for free text, or None."""
    t = norm(text)
    if not t:
        return None
    for value, aliases in CHOICES[key].items():
        if t == norm(value) or any(t == norm(a) for a in aliases):
            return value
    return None


def currency_code(text):
    t = norm(text)
    if not t:
        return None
    for code, aliases in CURRENCY_ALIASES.items():
        if t == norm(code) or any(t == norm(a) for a in aliases):
            return code
    return None


def market_code(text):
    t = norm(text)
    for code, aliases in MARKETS.items():
        if t == norm(code) or any(t == norm(a) for a in aliases):
            return code
    raw = str(text or "").strip().upper()
    if re.match(r"^EU-[A-Z]{2}$", raw):
        return raw
    return None


def market_currency(market):
    if not market:
        return None
    if market.startswith("EU-"):
        return "EUR"
    return MARKET_CURRENCY.get(market)


def needs_vat(market):
    return bool(market) and market.startswith(VAT_MARKET_PREFIX)
