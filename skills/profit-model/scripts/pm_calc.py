#!/usr/bin/env python3
"""Step 2 (offline, deterministic): inputs.normalized.json -> result.json.

CLI:
    python pm_calc.py "<run folder>" [--lang zh|en]

Pure decimal arithmetic (no binary floating point); the same input always
gives the same output. Rounding happens only for display and always leans
conservative: break-even ROAS, prices and order counts round up, maximum
CPA rounds down, other amounts round half up to 0.01, percentages to 0.1.

Exit codes: 0 complete, 2 partial, 3 blocked (no product could be computed).
Formula ids (F1-F12) match references/method.md.
Python 3.8+, standard library only.
"""

import argparse
import json
import os
import sys
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, getcontext

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pm_fields as F  # noqa: E402
from _shared import evidence, i18n, output  # noqa: E402

getcontext().prec = 28
SCHEMA = "profit-model/result/1"
SCENARIOS = ("cons", "base", "opt")
D0 = Decimal("0")
D1 = Decimal("1")
D100 = Decimal("100")
CENT = Decimal("0.01")
TENTH = Decimal("0.1")
EXIT = {"complete": 0, "partial": 2, "failed": 3}
# Inputs excluded from the sensitivity ranking: ad cost has its own section,
# the target margin is a goal, counts and choices are not "+-10%" values.
SENS_EXCLUDE = ("cpa", "roas", "target_margin_pct", "units_per_order")
SENS_KINDS = ("money", "pct", "rate")
G_KEYS = ("price", "units_per_order", "discount_pct", "shipping_charged")
PAY_KEYS = (
    "payment_provider",
    "payment_pct",
    "payment_fixed",
    "third_party_txn_pct",
    "cross_border_pct",
    "fx_conversion_pct",
)
GOODS_KEYS = ("product_cost", "packaging", "units_per_order")
LANDED_KEYS = GOODS_KEYS + (
    "first_mile_per_unit",
    "duty_rate_pct",
    "duty_basis",
    "duty_fixed_per_order",
)
RETURN_KEYS = ("return_rate_pct", "resale_pct", "return_ship_per_return", "return_mode")
CM0_KEYS = (
    G_KEYS
    + ("vat_pct",)
    + LANDED_KEYS
    + ("fulfil_per_order", "storage_per_unit", "last_mile_per_order")
    + RETURN_KEYS
    + PAY_KEYS
    + ("other_variable_per_order",)
)


# ---------------------------------------------------------------------------
# rounding
# ---------------------------------------------------------------------------


def q(d, step=CENT, mode=ROUND_HALF_UP):
    return None if d is None else d.quantize(step, rounding=mode)


def money(d):
    return None if d is None else str(q(d))


def up(d):
    return None if d is None else str(q(d, CENT, ROUND_CEILING))


def down(d):
    return None if d is None else str(q(d, CENT, ROUND_FLOOR))


def pct(d):
    """Fraction -> percent string with one decimal."""
    return None if d is None else str(q(d * D100, TENTH))


def ceil_int(d):
    return None if d is None else int(d.to_integral_value(rounding=ROUND_CEILING))


def plain(d):
    if d is None:
        return None
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def charm_price(d):
    """Smallest price ending in .99 that is not below d."""
    whole = d.to_integral_value(rounding=ROUND_FLOOR)
    cand = whole + Decimal("0.99")
    if cand < d:
        cand += D1
    return cand


# ---------------------------------------------------------------------------
# inputs -> numbers
# ---------------------------------------------------------------------------


class Inputs(object):
    """Numeric view of one product in one scenario, money in sale currency."""

    def __init__(self, fields, store, fx, scen, scale=None, add=None):
        self.fields = fields
        self.store = store
        self.fx = fx
        self.scen = scen
        self.scale = scale or {}
        self.add = add or {}

    def fx_rate(self):
        v = Decimal(self.fx[self.scen] or self.fx["base"])
        if "fx_cny_per_unit" in self.scale:
            v = v * self.scale["fx_cny_per_unit"]
        return v

    def entry(self, key):
        if key in self.fields:
            return self.fields[key]
        return self.store.get(key)

    def raw(self, key):
        e = self.entry(key)
        if not e or e.get(self.scen) is None:
            return None
        v = Decimal(e[self.scen])
        if key in self.scale:
            v = v * self.scale[key]
        if key in self.add:
            v = v + self.add[key]
        return v

    def num(self, key, default=D0):
        """Money converted to sale currency, percentages as fractions."""
        v = self.raw(key)
        if v is None:
            return default
        kind = F.FIELDS[key]["kind"]
        if kind == "money":
            if (self.entry(key) or {}).get("currency") == "CNY":
                v = v / self.fx_rate()
        elif kind in ("pct", "share"):
            v = v / D100
        return v

    def choice(self, key):
        e = self.entry(key) or {}
        return e.get("value")


def order_economics(x, price=None):
    """Per-order components (formulas F1-F6). ``x`` is an Inputs."""
    u = x.num("units_per_order", D1)
    p = x.num("price") if price is None else price
    # price is the listing price of this selling form (a 2-pack's price is
    # the pack price); units_per_order only scales the goods costs
    G = p * (D1 - x.num("discount_pct")) + x.num("shipping_charged")
    N = G / (D1 + x.num("vat_pct"))
    V = u * (x.num("product_cost") + x.num("packaging"))
    first_mile = u * x.num("first_mile_per_unit")
    duty_base = N if x.choice("duty_basis") == "price" else V
    duty = duty_base * x.num("duty_rate_pct") + x.num("duty_fixed_per_order")
    L = V + first_mile + duty
    ful = x.num("fulfil_per_order") + u * x.num("storage_per_unit")
    last = x.num("last_mile_per_order")
    r = x.num("return_rate_pct")
    k = x.num("resale_pct")
    ret_ship = r * x.num("return_ship_per_return")
    pay_pct = (
        x.num("payment_pct")
        + x.num("third_party_txn_pct")
        + x.num("cross_border_pct")
        + x.num("fx_conversion_pct")
    )
    pay = G * pay_pct + x.num("payment_fixed")
    other = x.num("other_variable_per_order")
    replace = x.choice("return_mode") == "replace"
    if replace:
        refund = D0
        goods = L
        replacement = r * ((D1 - k) * L + ful + last)
    else:
        refund = N * r
        goods = L * (D1 - r * k)
        replacement = D0
    cm0 = N - refund - goods - ful - last - ret_ship - replacement - pay - other
    cpa = None
    if (x.entry("cpa") or {}).get("source") == "user":
        cpa = x.num("cpa")
    elif (x.entry("roas") or {}).get("source") == "user":
        cpa = G / x.num("roas")
    cm1 = cm0 - (cpa or D0)
    tm = x.num("target_margin_pct")
    return {
        "G": G,
        "vat": G - N,
        "N": N,
        "V": V,
        "first_mile": first_mile,
        "duty": duty,
        "L": L,
        "refund": refund,
        "goods": goods,
        "ful": ful,
        "last": last,
        "ret_ship": ret_ship,
        "replacement": replacement,
        "pay": pay,
        "other": other,
        "cm0": cm0,
        "cm0_pct_n": cm0 / N if N else None,
        "cm0_pct_g": cm0 / G if G else None,
        "roas_be": G / cm0 if cm0 > 0 else None,
        "max_cpa": cm0,
        "cpa": cpa,
        "cm1": cm1,
        "target_cpa": cm0 - tm * N,
        "target_roas": G / (cm0 - tm * N) if cm0 - tm * N > 0 else None,
    }


def solve_price(x, margin, use_cpa=True):
    """Price where CM1 - margin * N = 0 (F10). Linear in price, so two
    points give the exact root. None when no price solves it."""

    def f(p):
        e = order_economics(x, p)
        cm = e["cm1"] if use_cpa else e["cm0"]
        return cm - margin * e["N"]

    p1, p2 = Decimal("10"), Decimal("100")
    f1, f2 = f(p1), f(p2)
    slope = (f2 - f1) / (p2 - p1)
    if slope <= 0:
        return None
    root = p1 - f1 / slope
    return root if root > 0 else None


# ---------------------------------------------------------------------------
# evidence for a result
# ---------------------------------------------------------------------------


def dep_entries(fields, store, fx, keys):
    out = []
    uses_cny = False
    for key in keys:
        e = fields.get(key) if key in fields else store.get(key)
        if not e:
            continue
        out.append((key, e))
        if e.get("currency") == "CNY":
            uses_cny = True
    if uses_cny:
        out.append(("fx_cny_per_unit", fx))
    return out


def evidence_for(fields, store, fx, keys):
    """(badge key, [assumption ids], [keys]) by the weakest-link rule. A
    missing duty counts as an assumption ("duty not included"), so it pulls
    results down to [推测] while the duty line itself shows [查不到]."""
    badges, ids, used = [], [], []
    for key, e in dep_entries(fields, store, fx, keys):
        src = e.get("source")
        if src == "na" or (src == "missing" and key != "duty_rate_pct"):
            continue
        used.append(key)
        badge = e.get("badge")
        if key == "duty_rate_pct" and src == "missing":
            badge = "inferred"
        if badge:
            badges.append(badge)
        aid = e.get("assumption")
        if aid and aid not in ids:
            ids.append(aid)
    weakest = evidence.weakest(badges) or "user_input"
    return weakest, sort_ids(ids), used


def sort_ids(ids):
    return sorted(set(ids), key=lambda a: int(a[1:]))


# ---------------------------------------------------------------------------
# per product
# ---------------------------------------------------------------------------

METRIC_FORMAT = {
    "G": money,
    "vat": money,
    "N": money,
    "V": money,
    "first_mile": money,
    "duty": money,
    "L": money,
    "refund": money,
    "goods": money,
    "ful": money,
    "last": money,
    "ret_ship": money,
    "replacement": money,
    "pay": money,
    "other": money,
    "cm0": money,
    "cm0_pct_n": pct,
    "cm0_pct_g": pct,
    "roas_be": up,
    "max_cpa": down,
    "cpa": money,
    "cm1": money,
    "target_cpa": down,
    "target_roas": up,
}
METRIC_DEPS = {
    "G": G_KEYS,
    "vat": G_KEYS + ("vat_pct",),
    "N": G_KEYS + ("vat_pct",),
    "V": GOODS_KEYS,
    "first_mile": ("first_mile_per_unit", "units_per_order"),
    "duty": LANDED_KEYS + G_KEYS + ("vat_pct",),
    "L": LANDED_KEYS,
    "refund": G_KEYS + ("vat_pct",) + RETURN_KEYS,
    "goods": LANDED_KEYS + RETURN_KEYS,
    "ful": ("fulfil_per_order", "storage_per_unit", "units_per_order"),
    "last": ("last_mile_per_order",),
    "ret_ship": RETURN_KEYS,
    "replacement": LANDED_KEYS + RETURN_KEYS + ("fulfil_per_order",),
    "pay": G_KEYS + PAY_KEYS,
    "other": ("other_variable_per_order",),
    "cm0": CM0_KEYS,
    "cm0_pct_n": CM0_KEYS,
    "cm0_pct_g": CM0_KEYS,
    "roas_be": CM0_KEYS,
    "max_cpa": CM0_KEYS,
    "cpa": ("cpa", "roas") + G_KEYS,
    "cm1": CM0_KEYS + ("cpa", "roas"),
    "target_cpa": CM0_KEYS + ("target_margin_pct",),
    "target_roas": CM0_KEYS + ("target_margin_pct",),
}
METRIC_FORMULA = {
    "G": "F1",
    "vat": "F1",
    "N": "F1",
    "V": "F2",
    "first_mile": "F2",
    "duty": "F2",
    "L": "F2",
    "refund": "F3",
    "goods": "F3",
    "ful": "F3",
    "last": "F3",
    "ret_ship": "F3",
    "replacement": "F3",
    "pay": "F3",
    "other": "F3",
    "cm0": "F3",
    "cm0_pct_n": "F4",
    "cm0_pct_g": "F4",
    "roas_be": "F5",
    "max_cpa": "F6",
    "cpa": "F7",
    "cm1": "F7",
    "target_cpa": "F6",
    "target_roas": "F5",
}


def sku_result(sku, store, fx, currency):
    fields = sku["fields"]
    out = {"name": sku["name"], "market": sku["market"], "blocked": sku["blocked"]}
    if sku["blocked"]:
        return out
    scen = {}
    for s in SCENARIOS:
        e = order_economics(Inputs(fields, store, fx, s))
        scen[s] = e
    out["scenarios"] = dict(
        (s, dict((m, METRIC_FORMAT[m](v)) for m, v in scen[s].items()))
        for s in SCENARIOS
    )
    out["scenarios_raw"] = dict(
        (s, dict((m, plain(v)) for m, v in scen[s].items())) for s in SCENARIOS
    )
    metrics = {}
    for m in METRIC_FORMAT:
        badge, ids, used = evidence_for(fields, store, fx, METRIC_DEPS[m])
        metrics[m] = {
            "value": out["scenarios"]["base"][m],
            "unit": "pct"
            if m.endswith("_pct_n") or m.endswith("_pct_g")
            else ("x" if "roas" in m else currency),
            "badge": badge,
            "basis": ids,
            "depends_on": used,
            "formula_id": METRIC_FORMULA[m],
        }
    out["metrics"] = metrics
    out["has_ads"] = scen["base"]["cpa"] is not None
    out["diffs"] = scenario_diffs(fields, fx)
    out["price"] = price_block(fields, store, fx, scen["base"])
    out["sensitivity"] = sensitivity(fields, store, fx)
    out["duty_line"] = duty_line(fields, store, fx)
    base = scen["base"]
    out["below_landed"] = Inputs(fields, store, fx, "base").num("price") < base["L"]
    return out


def scenario_diffs(fields, fx):
    """Per scenario, the inputs whose value differs from the base."""
    out = {"cons": [], "opt": []}
    items = list(fields.items()) + [("fx_cny_per_unit", fx)]
    for key, e in items:
        if e.get("base") is None:
            continue
        for s in ("cons", "opt"):
            if e.get(s) is not None and Decimal(e[s]) != Decimal(e["base"]):
                out[s].append(
                    {
                        "key": key,
                        "value": e[s],
                        "base": e["base"],
                        "currency": e.get("currency"),
                    }
                )
    return out


def price_block(fields, store, fx, base):
    x = Inputs(fields, store, fx, "base")
    has_ads = base["cpa"] is not None
    margin = x.num("target_margin_pct")
    floor = solve_price(x, D0, use_cpa=True)
    target = solve_price(x, margin, use_cpa=True)
    current = x.num("price")
    badge, ids, used = evidence_for(
        fields, store, fx, CM0_KEYS + ("cpa", "roas", "target_margin_pct")
    )
    return {
        "current": money(current),
        "floor": up(floor),
        "floor_raw": plain(floor),
        "target": up(target),
        "target_raw": plain(target),
        "target_display": str(charm_price(q(target, CENT, ROUND_CEILING)))
        if target is not None
        else None,
        "gap_to_floor": money(current - q(floor, CENT, ROUND_CEILING))
        if floor is not None
        else None,
        "with_ads": has_ads,
        "margin_pct": plain(margin * D100),
        "badge": badge,
        "basis": ids,
        "competitor_low": (store.get("competitor_price_low") or {}).get("base"),
        "competitor_high": (store.get("competitor_price_high") or {}).get("base"),
    }


def sensitivity(fields, store, fx):
    """Every numeric input +-10% (relative), others at base; change in CM1."""
    base = order_economics(Inputs(fields, store, fx, "base"))["cm1"]
    rows = []
    items = list(fields.items()) + [("fx_cny_per_unit", fx)]
    for key, e in items:
        kind = F.FIELDS[key]["kind"]
        if key in SENS_EXCLUDE or key in F.PRO_KEYS or kind not in SENS_KINDS:
            continue
        if e.get("source") in ("na", "missing") or e.get("base") is None:
            continue
        if Decimal(e["base"]) == 0:
            continue
        res = {}
        for tag, factor in (("up", Decimal("1.1")), ("down", Decimal("0.9"))):
            x = Inputs(fields, store, fx, "base", scale={key: factor})
            res[tag] = order_economics(x)["cm1"] - base
        impact = max(abs(res["up"]), abs(res["down"]))
        if impact == 0:
            continue
        rows.append(
            {
                "key": key,
                "lever": key == "price",
                "delta_up": money(res["up"]),
                "delta_down": money(res["down"]),
                "cm1_up": money(base + res["up"]),
                "cm1_down": money(base + res["down"]),
                "impact": money(impact),
                "_impact": impact,
            }
        )
    rows.sort(key=lambda r: (-r["_impact"], F.ORDER.index(r["key"])))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
        del r["_impact"]
    return rows


def duty_line(fields, store, fx):
    """Change in CM1 when the duty rate rises by 10 percentage points."""
    x0 = Inputs(fields, store, fx, "base")
    x1 = Inputs(fields, store, fx, "base", add={"duty_rate_pct": Decimal("10")})
    delta = order_economics(x1)["cm1"] - order_economics(x0)["cm1"]
    missing = fields["duty_rate_pct"]["source"] == "missing"
    return {
        "delta": money(delta),
        "loss": up(-delta) if delta < 0 else money(-delta),
        "duty_missing": missing,
        "basis": fields["duty_basis"].get("value"),
    }


# ---------------------------------------------------------------------------
# store level
# ---------------------------------------------------------------------------


def store_result(norm, skus_out):
    store = norm["store"]
    fx = norm["fx"]
    currency = norm["currency"]
    first = next((s for s in norm["skus"] if not s["blocked"]), None)
    if first is None:
        return {}
    x = Inputs(first["fields"], store, fx, "base")
    fixed_items = []
    total = D0
    badges, ids = [], []
    for key in F.FIXED_KEYS:
        e = store.get(key) or {}
        v = None
        if e.get("base") is not None:
            v = x.num(key)
            total += v
        fixed_items.append(
            {
                "key": key,
                "value": money(v),
                "badge": e.get("badge"),
                "source": e.get("source"),
                "assumption": e.get("assumption"),
            }
        )
        if e.get("source") != "na" and e.get("badge"):
            badges.append(e["badge"])
        if e.get("assumption"):
            ids.append(e["assumption"])
    fix_badge = evidence.weakest(badges) or "user_input"
    head = next(s for s in skus_out if not s["blocked"])
    m = head["metrics"]
    cm0 = Decimal(head["scenarios_raw"]["base"]["cm0"])
    tiers = []
    if head["has_ads"]:
        cpa = Decimal(head["scenarios_raw"]["base"]["cpa"])
        tiers.append(("user", cpa))
    if cm0 > 0:
        tiers += [("zero", D0), ("half", cm0 / 2), ("full", cm0)]
    else:
        tiers += [("zero", D0)]
    breakeven = []
    for tag, cpa in tiers:
        cm1 = cm0 - cpa
        units = (
            ceil_int(total / cm1) if cm1 > 0 and total > 0 else (0 if cm1 > 0 else None)
        )
        breakeven.append(
            {"tier": tag, "cpa": money(cpa), "cm1": money(cm1), "units": units}
        )
    badge = evidence.weakest([fix_badge, m["cm1"]["badge"]]) or fix_badge
    return {
        "fixed": {
            "items": fixed_items,
            "total": money(total),
            "badge": fix_badge,
            "basis": ids,
        },
        "breakeven": breakeven,
        "breakeven_badge": badge,
        "breakeven_basis": sort_ids(ids + m["cm1"]["basis"]),
        "startup": startup(first["fields"], store, fx, total),
        "ccc": cash_cycle(store),
        "currency": currency,
    }


def _item(key, value, badge, note=None, fx_rate=None, assumption=None):
    return {
        "key": key,
        "value": money(value),
        "cny": money(value * fx_rate) if value is not None and fx_rate else None,
        "badge": badge,
        "note": note,
        "assumption": assumption,
    }


def startup(fields, store, fx, fixed_total):
    """Startup cash list (F9): main route and cheapest route."""
    x = Inputs(fields, store, fx, "base")
    rate = x.fx_rate()

    def ent(key):
        return fields.get(key) or store.get(key) or {}

    def known(key):
        return ent(key).get("source") not in (None, "missing")

    unit_goods = x.num("product_cost") + x.num("packaging")
    unit_fm = x.num("first_mile_per_unit")
    duty_rate = x.num("duty_rate_pct")
    duty_known = ent("duty_rate_pct").get("source") != "missing"
    price_basis = x.choice("duty_basis") == "price"
    runway = x.raw("runway_months") or Decimal("3")
    fixed_3pl = x.num("3pl_min_month")
    routes = {}
    for route, qty_key in (("main", "moq"), ("lean", "min_moq")):
        items = []
        qty = x.raw(qty_key) if known(qty_key) else None
        b_qty = ent(qty_key).get("badge") if qty is not None else "not_found"
        goods = qty * unit_goods if qty is not None else None
        items.append(
            _item(
                "goods",
                goods,
                evidence.weakest(
                    [b_qty, ent("product_cost").get("badge", "not_found")]
                ),
                qty_key,
                rate,
            )
        )
        if route == "main":
            fm = qty * unit_fm if qty is not None else None
            items.append(
                _item(
                    "first_mile",
                    fm,
                    evidence.weakest(
                        [b_qty, ent("first_mile_per_unit").get("badge", "not_found")]
                    ),
                    None,
                    rate,
                )
            )
            if price_basis:
                items.append(_item("duty", D0, "inferred", "duty_per_order", rate))
            elif duty_known and goods is not None:
                items.append(
                    _item(
                        "duty",
                        goods * duty_rate,
                        evidence.weakest([b_qty, ent("duty_rate_pct").get("badge")]),
                        None,
                        rate,
                    )
                )
            else:
                items.append(
                    _item(
                        "duty",
                        None,
                        "not_found",
                        "duty_missing" if not duty_known else qty_key,
                    )
                )
        for key in ("sample_cost", "deposit"):
            v = x.num(key) if known(key) else None
            items.append(_item(key, v, ent(key).get("badge", "not_found"), None, rate))
        if route == "main":
            v = x.num("inbound_3pl_fee") if known("inbound_3pl_fee") else None
            items.append(
                _item(
                    "inbound_3pl_fee",
                    v,
                    ent("inbound_3pl_fee").get("badge", "not_found"),
                    None,
                    rate,
                )
            )
            fixed = fixed_total * runway
        else:
            fixed = (fixed_total - fixed_3pl) * runway
        items.append(
            _item(
                "fixed_runway",
                fixed,
                evidence.weakest([ent("runway_months").get("badge", "inferred")]),
                plain(runway),
                rate,
                ent("runway_months").get("assumption"),
            )
        )
        v = x.num("launch_ad_budget") if known("launch_ad_budget") else None
        items.append(
            _item(
                "launch_ad_budget",
                v,
                ent("launch_ad_budget").get("badge", "not_found"),
                None,
                rate,
            )
        )
        total = sum((Decimal(i["value"]) for i in items if i["value"] is not None), D0)
        missing = [i["key"] for i in items if i["value"] is None]
        routes[route] = {
            "items": items,
            "total_known": money(total),
            "total_known_cny": money(total * rate),
            "missing": missing,
            "qty": plain(qty),
            "badge": evidence.weakest([i["badge"] for i in items if i["badge"]]),
        }
    return routes


def cash_cycle(store):
    """Cash conversion cycle (F11): DIO + DSO - DPO, in days."""

    def val(key):
        e = store.get(key) or {}
        return (
            Decimal(e["base"])
            if e.get("base") is not None and e.get("source") not in ("missing",)
            else None
        )

    transit, moq, daily = val("transit_days"), val("moq"), val("daily_units")
    payout, prod, dep = (
        val("payout_delay_days"),
        val("production_days"),
        val("supplier_deposit_pct"),
    )
    dio = (
        transit + (moq / daily) / 2
        if None not in (transit, moq, daily) and daily
        else None
    )
    dso = payout
    dpo = -(prod * dep / D100) if None not in (prod, dep) else None
    ccc = dio + dso - dpo if None not in (dio, dso, dpo) else None
    return {
        "dio": money(dio),
        "dso": money(dso),
        "dpo": money(dpo),
        "ccc": money(ccc),
        "known": dict(
            (k, plain(v))
            for k, v in (
                ("transit_days", transit),
                ("moq", moq),
                ("daily_units", daily),
                ("payout_delay_days", payout),
                ("production_days", prod),
                ("supplier_deposit_pct", dep),
            )
            if v is not None
        ),
    }


# ---------------------------------------------------------------------------
# entry
# ---------------------------------------------------------------------------


def compute(norm):
    fx = norm["fx"]
    skus = [sku_result(s, norm["store"], fx, norm["currency"]) for s in norm["skus"]]
    computed = [s for s in skus if not s["blocked"]]
    status = norm["status"]
    if not computed:
        status = "failed"
    issues = list(norm["issues"])
    for s in computed:
        if s["below_landed"]:
            issues.append(
                {
                    "level": "warning",
                    "code": "price_below_landed",
                    "sku": s["name"],
                    "key": "price",
                    "params": {},
                }
            )
    result = {
        "schema": SCHEMA,
        "status": status,
        "exit_code": EXIT[status],
        "edition": norm.get("edition"),
        "market": norm["market"],
        "currency": norm["currency"],
        "fx": fx,
        "skus": skus,
        "store": store_result(norm, skus) if computed else {},
        "assumptions": norm["assumptions"],
        "issues": issues,
        "flags": norm["flags"],
        "fees_used": norm["fees_used"],
        "fee_hints": norm["fee_hints"],
        "fee_table_as_of": norm.get("fee_table_as_of"),
    }
    try:
        import pm_pro  # noqa: F401  (present only in the Pro edition)
    except ImportError:
        pm_pro = None
    if pm_pro is not None and computed:
        result["pro"] = pm_pro.compute(norm, result)
    return result


def main(argv=None):
    output.safe_console()
    ap = argparse.ArgumentParser(description="Compute result.json from inputs.")
    ap.add_argument("path", help="run folder or its raw-data folder")
    ap.add_argument("--lang", default="zh", choices=i18n.LANGS)
    args = ap.parse_args(argv)
    _run, raw = output.locate_run(args.path, args.lang)
    src = os.path.join(raw, "inputs.normalized.json")
    if not os.path.isfile(src):
        print("error: %s not found, run pm_inputs.py first" % src, file=sys.stderr)
        return 1
    with open(src, encoding="utf-8") as fh:
        norm = json.load(fh)
    result = compute(norm)
    with open(os.path.join(raw, "result.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("status: %s" % result["status"])
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
