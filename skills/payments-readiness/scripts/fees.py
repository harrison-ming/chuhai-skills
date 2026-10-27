#!/usr/bin/env python3
"""Payment fee estimate (F group): pure calculation, no network.

Reads the shipped rate snapshot (references/rates.json, with valid_as_of)
and the questionnaire, and returns monthly / yearly fees per route:

    per order = AOV x (rate + intl share x intl surcharge
                       + fx share x fx surcharge) + fixed fee
    platform  = AOV x orders on third-party routes x Shopify third-party fee
    payouts   = withdrawal fee x withdrawals per month

Discipline (spec 3.6): every number carries its source id and retrieval
date; status=unverified rates are never used as defaults (the seller's own
rate is required); results are estimates, listed side by side, never ranked.
Only payment fees are covered: no tax, no app fees, no profit.

CLI (prints the estimate of a run folder's questionnaire as JSON):
    python fees.py "<run folder>" [--lang zh|en]
Python 3.8+, standard library only.
"""

import argparse
import json
import os
import sys
from decimal import ROUND_HALF_UP, Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import skill_config as cfg  # noqa: E402
from _shared import datafile, i18n, output  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RATES_PATH = os.path.join(HERE, "..", cfg.RATES_FILE)
CENT = Decimal("0.01")
HUNDRED = Decimal(100)
PROVIDER_LABEL = {
    "zh": {
        "shopify_payments": "Shopify Payments",
        "stripe": "Stripe",
        "paypal": "PayPal",
        "airwallex": "Airwallex",
        "card_other": "其他信用卡通道",
        "manual": "手动付款",
        "platform": "Shopify 平台",
        "payout": "提现与换汇",
    },
    "en": {
        "shopify_payments": "Shopify Payments",
        "stripe": "Stripe",
        "paypal": "PayPal",
        "airwallex": "Airwallex",
        "card_other": "Other card processor",
        "manual": "Manual payments",
        "platform": "Shopify platform",
        "payout": "Payouts and conversion",
    },
}
ITEM_LABEL = {
    "zh": {
        "processing": "交易手续费",
        "intl": "境外卡附加费",
        "fx": "换汇附加费",
        "mor": "记录卖方 (代收代缴) 附加费",
        "platform_fee": "第三方交易费 (Shopify 对非自家通道收取)",
        "withdrawal": "提现费",
        "dispute": "拒付/争议费",
        "payout_fx": "换汇到人民币",
        "payout_account": "提现账户的提现与换汇费",
    },
    "en": {
        "processing": "Processing fee",
        "intl": "International card surcharge",
        "fx": "Currency conversion surcharge",
        "mor": "Merchant of record surcharge",
        "platform_fee": "Third-party transaction fee (Shopify)",
        "withdrawal": "Withdrawal fee",
        "dispute": "Dispute / chargeback fee",
        "payout_fx": "Conversion to your home currency",
        "payout_account": "Payout account withdrawal and conversion fees",
    },
}
VERIFIED_LABEL = {
    "zh": {
        "verified": "已核实",
        "user": "用户填写",
        "unknown": "查不到",
        "inferred": "推测",
    },
    "en": {
        "verified": "Verified",
        "user": "You provided",
        "unknown": "Not found",
        "inferred": "Inferred",
    },
}
NOTES = {
    "zh": {
        "user_rate": "按你填的费率计算, 未另加境外卡或换汇附加费",
        "fixed_zero": "你没填固定费, 按 0 计",
        "unverified": "官方页面没有公开这个费率 (只在开通后的后台可见), 需要你填写合同费率",
        "no_rate": "快照里没有这个地区/通道的官方费率, 需要你填写合同费率",
        "currency": "官方费率按 %s 计价, 你的客单价是 %s, 没有换算",
        "no_share": "不知道这部分订单的占比",
        "intl_inferred": "境外卡占比按你的市场分布推算为 %s%%",
        "fx_inferred": "按顾客都用 %s 付款计, 不产生换汇",
        "plan_unknown": "需要知道你的 Shopify 套餐",
        "paypal_exempt": "以 Shopify Payments 为主通道时 PayPal 免收第三方交易费",
        "no_withdrawals": "不知道每月提现几次",
        "payout_fx": "换汇费率没填; 由你的银行或提现账户决定",
        "payout_account": "%s 的费率未核实, 本快照不提供默认值",
        "stripe_cn": "Stripe 支持国家列表不含中国大陆",
        "aov_currency": "你没说客单价币种, 按 USD 计",
        "shares_normalized": "各路径占比加起来不是 100%%, 已按比例换算",
        "dispute_unknown": "这条通道的争议费没有官方快照",
        "not_shopify": "不是 Shopify 店, 不收这项",
        "per_month": "每月 %s 次",
    },
    "en": {
        "user_rate": "Uses your rate; no extra international or conversion surcharge added",
        "fixed_zero": "No fixed fee given, counted as 0",
        "unverified": "Not published (visible only in the admin after activation); enter your contract rate",
        "no_rate": "No official rate in the snapshot for this region/provider; enter your contract rate",
        "currency": "Official rate is priced in %s but your order value is in %s; not converted",
        "no_share": "Share of these orders unknown",
        "intl_inferred": "International card share inferred from your market mix: %s%%",
        "fx_inferred": "Assumes all buyers pay in %s, so no conversion",
        "plan_unknown": "Your Shopify plan is needed",
        "paypal_exempt": "PayPal is exempt while Shopify Payments is the primary provider",
        "no_withdrawals": "Withdrawals per month unknown",
        "payout_fx": "Conversion rate not given; set by your bank or payout account",
        "payout_account": "%s fees are not verified; no default in this snapshot",
        "stripe_cn": "Stripe's supported countries do not include mainland China",
        "aov_currency": "Order value currency not given, counted as USD",
        "shares_normalized": "Route shares did not add up to 100%%; scaled proportionally",
        "dispute_unknown": "No official dispute fee in the snapshot for this route",
        "not_shopify": "Not a Shopify store, not charged",
        "per_month": "%s per month",
    },
}
UNVERIFIED_PAYOUT = ("worldfirst", "payoneer", "pingpong", "lianlian", "airwallex")
EU_UK = (
    "UK",
    "EU",
    "DE",
    "FR",
    "IT",
    "ES",
    "NL",
    "BE",
    "AT",
    "IE",
    "SE",
    "DK",
    "FI",
    "PL",
    "PT",
)


def D(value):
    return Decimal(str(value))


def money(value):
    return None if value is None else value.quantize(CENT, rounding=ROUND_HALF_UP)


def fmt_pct(value):
    if value is None:
        return ""
    s = ("%.4f" % float(value)).rstrip("0").rstrip(".")
    return s + "%"


def fmt_num(value):
    if value is None:
        return ""
    d = D(value)
    if d == d.to_integral_value():
        return str(int(d))
    return str(money(d))


def load_rates(path=None):
    return datafile.load_json(path or RATES_PATH)


def index_rates(data):
    return dict((r["id"], r) for r in data.get("rates", []))


def account_region(q):
    country = q.get("entity_country")
    return country if country in ("CN", "HK", "US", "CA", "UK") else None


class Ctx(object):
    def __init__(self, q, data, lang, is_shopify, plan, orders):
        self.q = q
        self.lang = i18n.norm_lang(lang)
        self.rates = index_rates(data)
        self.n = NOTES[self.lang]
        self.region = account_region(q)
        self.is_shopify = is_shopify
        self.plan = plan
        self.orders = orders
        self.rows = []
        self.unknown = []
        self.assumptions = []
        self.sources = []

    def rate(self, rid):
        r = self.rates.get(rid)
        if r and r.get("status") == "verified":
            return r
        return None

    def row(self, route, item, **kw):
        row = {
            "route_key": route,
            "item_key": item,
            "route": PROVIDER_LABEL[self.lang].get(route, route),
            "item": ITEM_LABEL[self.lang][item],
            "pct": kw.get("pct"),
            "fixed": kw.get("fixed"),
            "share": kw.get("share"),
            "orders": kw.get("orders"),
            "monthly": money(kw["monthly"]) if kw.get("monthly") is not None else None,
            "currency": kw.get("currency") or "",
            "source_id": kw.get("source_id") or "",
            "retrieved": kw.get("retrieved") or "",
            "status": kw.get("status", "unknown"),
            "note": kw.get("note") or "",
        }
        if row["monthly"] is None:
            self.unknown.append(row["route"] + " / " + row["item"])
        if row["source_id"] and row["source_id"] not in self.sources:
            self.sources.append(row["source_id"])
        self.rows.append(row)
        return row


def base_rate(ctx, g):
    """(pct, fixed, currency, rate record or None, status, note, surcharges).

    surcharges: [(item_key, pct, kind, record)] with kind intl | fx | all.
    """
    p, region, plan = g["provider"], ctx.region, ctx.plan
    if g.get("pct") is not None:
        note = ctx.n["user_rate"]
        if g.get("fixed") in (None,):
            note += "; " + ctx.n["fixed_zero"]
        return (D(g["pct"]), D(g.get("fixed") or 0), None, None, "user", note, [])
    rid, extra = None, []
    if p == "paypal" and region == "CN":
        rid = "paypal-cn-cross-border"
    elif p == "paypal" and region == "US":
        rid = "paypal-us-checkout"
        extra = [
            ("intl", "paypal-us-international", "intl"),
            ("fx", "paypal-us-fx", "fx"),
        ]
    elif p == "stripe" and region == "US":
        rid = "stripe-us-domestic"
        extra = [
            ("intl", "stripe-us-international", "intl"),
            ("fx", "stripe-us-fx", "fx"),
        ]
    elif p == "stripe" and region == "CA":
        rid = "stripe-ca-domestic"
        extra = [
            ("intl", ("stripe-ca-surcharges", "international_card"), "intl"),
            ("fx", ("stripe-ca-surcharges", "currency_conversion"), "fx"),
        ]
    elif (
        p == "shopify_payments"
        and region in ("US", "CA")
        and plan
        in (
            "basic",
            "grow",
            "advanced",
            "plus",
        )
    ):
        rid = "shopify-payments-%s-online-standard-%s" % (region.lower(), plan)
        if region == "US":
            extra = [
                ("intl", "shopify-payments-us-international-surcharge", "intl"),
                ("fx", "shopify-payments-fx-us", "fx"),
            ]
        else:
            extra = [
                ("intl", "ca-amex-intl-delta", "intl"),
                ("fx", "shopify-payments-fx-other", "fx"),
            ]
    if p == "shopify_payments" and region == "CN":
        return (None, None, None, None, "unknown", ctx.n["unverified"], [])
    if (
        p == "shopify_payments"
        and region in ("US", "CA")
        and plan
        not in (
            "basic",
            "grow",
            "advanced",
            "plus",
        )
    ):
        return (None, None, None, None, "unknown", ctx.n["plan_unknown"], [])
    rec = ctx.rate(rid) if rid else None
    if rec is None:
        note = ctx.n["no_rate"]
        if p == "stripe" and region == "CN":
            note = ctx.n["stripe_cn"] + "; " + note
        return (None, None, None, None, "unknown", note, [])
    value = rec["value"]
    pct, fixed = D(value["pct"]), D(value.get("fixed", 0))
    surcharges = []
    for item, ref, kind in extra:
        if ref == "ca-amex-intl-delta":
            other = ctx.rate("shopify-payments-ca-online-amex-intl-%s" % plan)
            if other:
                surcharges.append((item, D(other["value"]["pct"]) - pct, kind, other))
            continue
        if isinstance(ref, tuple):
            r = ctx.rate(ref[0])
            if r:
                surcharges.append((item, D(r["value"][ref[1]]), kind, r))
            continue
        r = ctx.rate(ref)
        if r:
            surcharges.append((item, D(r["value"]), kind, r))
    if p == "stripe" and ctx.q.get("mor") == "stripe_managed_payments":
        r = ctx.rate("stripe-managed-payments")
        if r:
            surcharges.append(("mor", D(r["value"]), "all", r))
    return (pct, fixed, rec.get("currency"), rec, "verified", "", surcharges)


def intl_share(ctx, currency_region):
    q = ctx.q
    if q.get("intl_card_share") is not None:
        return D(q["intl_card_share"]), "user", ""
    mix = q.get("market_mix") or {}
    known = dict((k, v) for k, v in mix.items() if v is not None)
    if known and currency_region:
        total = sum(D(v) for v in known.values())
        if total > 0:
            home = sum(D(v) for k, v in known.items() if k == currency_region)
            share = (total - home) / total * HUNDRED
            share = share.quantize(Decimal("0.1"))
            return share, "inferred", ctx.n["intl_inferred"] % fmt_num(share)
    return None, "unknown", ctx.n["no_share"]


def fx_share(ctx, settle_currency, aov_currency):
    q = ctx.q
    if q.get("fx_share") is not None:
        return D(q["fx_share"]), "user", ""
    if settle_currency and aov_currency and settle_currency == aov_currency:
        return D(0), "inferred", ctx.n["fx_inferred"] % aov_currency
    return None, "unknown", ctx.n["no_share"]


def route_shares(ctx, gateways):
    shares = [g.get("share") for g in gateways]
    if len(gateways) == 1 and shares[0] is None:
        return [D(100)]
    if any(s is None for s in shares):
        return None
    total = sum(D(s) for s in shares)
    if total <= 0:
        return None
    if total != HUNDRED:
        ctx.assumptions.append(ctx.n["shares_normalized"])
        return [D(s) / total * HUNDRED for s in shares]
    return [D(s) for s in shares]


def is_shopify_store(q, identity):
    plan = q.get("shopify_plan")
    if plan == "none":
        return False
    if plan:
        return True
    if identity in ("confirmed", "probable"):
        return True
    if identity == "not_shopify":
        return False
    return None


def compute(
    q,
    data=None,
    lang="zh",
    identity=None,
    gateways=None,
    plan=None,
    monthly_orders=None,
    intl_override=None,
    today=None,
):
    """Fee estimate for one setup. Returns a dict (see module docstring)."""
    data = data or load_rates()
    lang = i18n.norm_lang(lang)
    gateways = list(q.get("gateways") or []) if gateways is None else list(gateways)
    plan = plan or q.get("shopify_plan")
    orders = monthly_orders if monthly_orders is not None else q.get("monthly_orders")
    if intl_override is not None:
        q = dict(q, intl_card_share=intl_override)
    ctx = Ctx(q, data, lang, is_shopify_store(q, identity), plan, orders)
    fresh = datafile.freshness(data, cfg.DATA_MAX_AGE_DAYS, today=today, lang=lang)
    result = {
        "status": "not_computed",
        "missing": [],
        "currency": None,
        "aov": q.get("aov"),
        "monthly_orders": orders,
        "monthly_sales": None,
        "monthly_total": None,
        "yearly_total": None,
        "pct_of_sales": None,
        "rows": [],
        "unknown_items": [],
        "assumptions": [],
        "sources_used": [],
        "rates_valid_as_of": fresh["valid_as_of"],
        "rates_stale": fresh["stale"],
        "rates_note": fresh["note"],
        "plan_fee_reference": None,
    }
    for key, val in (("aov", q.get("aov")), ("monthly_orders", orders)):
        if val in (None, 0):
            result["missing"].append(key)
    if not gateways:
        result["missing"].append("gateways")
    if result["missing"]:
        return result
    shares = route_shares(ctx, gateways)
    if shares is None:
        result["missing"].append("gateway_shares")
        return result
    aov_currency = q.get("aov_currency")
    if not aov_currency:
        aov_currency = "USD"
        ctx.assumptions.append(ctx.n["aov_currency"])
    aov, total_orders = D(q["aov"]), D(orders)
    sp_primary = any(g["provider"] == "shopify_payments" for g in gateways)
    platform_orders, platform_share = D(0), D(0)

    for g, share in zip(gateways, shares):
        route = g["provider"]
        n_orders = total_orders * share / HUNDRED
        pct, fixed, rate_cur, rec, status, note, surcharges = base_rate(ctx, g)
        cur_ok = rate_cur is None or rate_cur == aov_currency
        if pct is not None and not cur_ok:
            status, note = "unknown", ctx.n["currency"] % (rate_cur, aov_currency)
        monthly = None
        if pct is not None and cur_ok:
            monthly = n_orders * (aov * pct / HUNDRED + fixed)
        ctx.row(
            route,
            "processing",
            pct=fmt_pct(pct) if pct is not None else "",
            fixed=fmt_num(fixed) if fixed is not None else "",
            share=fmt_pct(share),
            orders=fmt_num(n_orders),
            monthly=monthly,
            currency=aov_currency,
            source_id=rec["id"] if rec else ("user" if status == "user" else ""),
            retrieved=rec.get("retrieved") if rec else "",
            status=status,
            note=note,
        )
        if cur_ok:
            settle = rate_cur or aov_currency
            region = {"USD": "US", "CAD": "CA"}.get(settle)
            for item, s_pct, kind, s_rec in surcharges:
                if kind == "intl":
                    sh, sh_status, sh_note = intl_share(ctx, region)
                elif kind == "fx":
                    sh, sh_status, sh_note = fx_share(ctx, settle, aov_currency)
                else:
                    sh, sh_status, sh_note = D(100), "verified", ""
                if sh is not None and sh == 0:
                    if sh_note:
                        ctx.assumptions.append(
                            "%s / %s: %s"
                            % (
                                PROVIDER_LABEL[lang][route],
                                ITEM_LABEL[lang][item],
                                sh_note,
                            )
                        )
                    continue
                amount = None
                if sh is not None:
                    amount = n_orders * aov * s_pct / HUNDRED * sh / HUNDRED
                row_status = (
                    "verified" if sh_status in ("verified", "user") else sh_status
                )
                ctx.row(
                    route,
                    item,
                    pct=fmt_pct(s_pct),
                    share=fmt_pct(sh) if sh is not None else "",
                    orders=fmt_num(n_orders),
                    monthly=amount,
                    currency=aov_currency,
                    source_id=s_rec["id"],
                    retrieved=s_rec.get("retrieved"),
                    status=row_status,
                    note=sh_note,
                )
        # platform third-party fee
        exempt = route in ("shopify_payments", "manual")
        if route == "paypal" and sp_primary:
            exempt = True
            ctx.assumptions.append(ctx.n["paypal_exempt"])
        if not exempt:
            platform_orders += n_orders
            platform_share += share
        # withdrawals
        withdraw_fee, w_rec = None, None
        if g.get("withdraw_fee") is not None:
            withdraw_fee = D(g["withdraw_fee"])
        elif route == "paypal" and ctx.region == "CN":
            w_rec = ctx.rate("paypal-cn-withdrawal")
            withdraw_fee = D(w_rec["value"]) if w_rec else None
        if withdraw_fee is not None:
            count = g.get("withdrawals")
            count = count if count is not None else q.get("withdrawals_per_month")
            w_cur = w_rec.get("currency") if w_rec else aov_currency
            amount = None
            w_note = ""
            if count is None:
                w_note = ctx.n["no_withdrawals"]
            elif w_cur != aov_currency:
                w_note = ctx.n["currency"] % (w_cur, aov_currency)
            else:
                amount = withdraw_fee * D(count)
            ctx.row(
                route,
                "withdrawal",
                fixed=fmt_num(withdraw_fee),
                orders="",
                share=(ctx.n["per_month"] % fmt_num(count))
                if count is not None
                else "",
                monthly=amount,
                currency=w_cur,
                source_id=w_rec["id"] if w_rec else "user",
                retrieved=w_rec.get("retrieved") if w_rec else "",
                status=("verified" if w_rec else "user")
                if amount is not None
                else "unknown",
                note=w_note,
            )
        # disputes (only when the seller gave a dispute rate)
        if q.get("dispute_rate") is not None:
            fee_id = {
                ("stripe", "US"): ("stripe-us-dispute", "received"),
                ("paypal", "CN"): ("paypal-cn-chargeback", None),
                ("paypal", "US"): ("paypal-us-chargeback", None),
            }.get((route, ctx.region))
            d_rec = ctx.rate(fee_id[0]) if fee_id else None
            amount, fee = None, None
            if d_rec and d_rec.get("currency") == aov_currency:
                fee = D(d_rec["value"][fee_id[1]] if fee_id[1] else d_rec["value"])
                amount = n_orders * D(q["dispute_rate"]) / HUNDRED * fee
            ctx.row(
                route,
                "dispute",
                fixed=fmt_num(fee) if fee is not None else "",
                share=fmt_pct(q["dispute_rate"]),
                orders=fmt_num(n_orders),
                monthly=amount,
                currency=aov_currency,
                source_id=d_rec["id"] if d_rec else "",
                retrieved=d_rec.get("retrieved") if d_rec else "",
                status="verified" if amount is not None else "unknown",
                note="" if amount is not None else ctx.n["dispute_unknown"],
            )

    # Shopify third-party transaction fee
    if ctx.is_shopify is not False and platform_orders > 0:
        rec = ctx.rate("shopify-3p-fee-%s" % plan) if plan else None
        if ctx.is_shopify is None and not plan:
            pass  # platform unknown and no plan: say nothing rather than guess
        elif rec is None:
            ctx.row(
                "platform",
                "platform_fee",
                share=fmt_pct(platform_share),
                orders=fmt_num(platform_orders),
                currency=aov_currency,
                status="unknown",
                note=ctx.n["plan_unknown"],
            )
        else:
            fee_pct = D(rec["value"])
            ctx.row(
                "platform",
                "platform_fee",
                pct=fmt_pct(fee_pct),
                share=fmt_pct(platform_share),
                orders=fmt_num(platform_orders),
                monthly=platform_orders * aov * fee_pct / HUNDRED,
                currency=aov_currency,
                source_id=rec["id"],
                retrieved=rec.get("retrieved"),
                status="verified",
            )
    # payout account fees and conversion to the home currency
    payout = q.get("payout_account")
    if payout in UNVERIFIED_PAYOUT:
        ctx.row(
            "payout",
            "payout_account",
            currency=aov_currency,
            status="unknown",
            note=ctx.n["payout_account"] % payout,
        )
    home_cny = (
        ctx.region == "CN" or q.get("payout_currency") == "CNY" or payout == "cn_bank"
    )
    sales = total_orders * aov
    if home_cny and aov_currency != "CNY":
        fx = q.get("payout_fx_pct")
        ctx.row(
            "payout",
            "payout_fx",
            pct=fmt_pct(fx) if fx is not None else "",
            share="100%",
            monthly=sales * D(fx) / HUNDRED if fx is not None else None,
            currency=aov_currency,
            source_id="user" if fx is not None else "",
            status="user" if fx is not None else "unknown",
            note="" if fx is not None else ctx.n["payout_fx"],
        )
    # plan monthly fee: reference only, not in the total
    if plan in ("basic", "grow", "advanced", "plus") and ctx.region in ("US", "CA"):
        rec = ctx.rate("shopify-plan-monthly-%s" % ctx.region.lower())
        if rec:
            key = plan if plan != "plus" else "plus_from"
            result["plan_fee_reference"] = {
                "plan": plan,
                "monthly": rec["value"].get(key),
                "currency": rec.get("currency"),
                "source_id": rec["id"],
            }

    known = [r["monthly"] for r in ctx.rows if r["monthly"] is not None]
    total = sum(known, Decimal(0))
    for r in ctx.rows:
        r["yearly"] = money(r["monthly"] * 12) if r["monthly"] is not None else None
    result.update(
        {
            "status": "partial" if ctx.unknown else "computed",
            "currency": aov_currency,
            "monthly_sales": money(sales),
            "monthly_total": money(total),
            "yearly_total": money(total * 12),
            "pct_of_sales": money(total / sales * HUNDRED) if sales else None,
            "rows": ctx.rows,
            "unknown_items": ctx.unknown,
            "assumptions": dedupe(ctx.assumptions),
            "sources_used": ctx.sources,
        }
    )
    return result


def dedupe(items):
    out = []
    for x in items:
        if x not in out:
            out.append(x)
    return out


def jsonable(obj):
    """Decimals -> float for JSON (money already rounded to cents)."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return dict((k, jsonable(v)) for k, v in obj.items())
    if isinstance(obj, list):
        return [jsonable(v) for v in obj]
    return obj


def csv_rows(fees, lang):
    """Rows for 费用测算.csv (skill_config.CSV_TABLES fee_rows)."""
    vl = VERIFIED_LABEL[i18n.norm_lang(lang)]
    out = []
    for r in fees.get("rows", []):
        out.append(
            {
                "route": r["route"],
                "item": r["item"],
                "rate": r["pct"],
                "fixed": r["fixed"],
                "share": r["share"],
                "orders": r["orders"],
                "monthly": "" if r["monthly"] is None else "%.2f" % r["monthly"],
                "yearly": "" if r.get("yearly") is None else "%.2f" % r["yearly"],
                "currency": r["currency"],
                "source_id": r["source_id"],
                "retrieved": r["retrieved"],
                "verified": vl.get(r["status"], r["status"]),
            }
        )
    if fees.get("monthly_total") is not None:
        total_label = "合计 (已知项)" if lang == "zh" else "Total (known items)"
        out.append(
            {
                "route": total_label,
                "item": "",
                "rate": "",
                "fixed": "",
                "share": "",
                "orders": fmt_num(fees["monthly_orders"]),
                "monthly": "%.2f" % fees["monthly_total"],
                "yearly": "%.2f" % fees["yearly_total"],
                "currency": fees["currency"],
                "source_id": "",
                "retrieved": fees.get("rates_valid_as_of") or "",
                "verified": "",
            }
        )
    return out


def main(argv=None):
    output.safe_console()
    ap = argparse.ArgumentParser(description="Fee estimate from questionnaire.json.")
    ap.add_argument("run_dir")
    ap.add_argument("--lang", default="zh", choices=i18n.LANGS)
    args = ap.parse_args(argv)
    import questionnaire  # noqa: E402

    _run, raw = output.locate_run(args.run_dir, args.lang)
    q = questionnaire.load(raw) or questionnaire.empty()
    print(
        json.dumps(jsonable(compute(q, lang=args.lang)), ensure_ascii=False, indent=2)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
