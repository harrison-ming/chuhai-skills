#!/usr/bin/env python3
"""Step 1 (offline): read inputs.csv, validate, fill visible defaults.

CLI:
    python pm_inputs.py "<run folder>" [--lang zh|en] [--fx-json fx.json]

Reads ``<raw-data>/inputs.csv`` (UTF-8 with or without BOM; long format, one
input per row: 商品, 项目, 基准, 保守, 乐观, 单位, 来源说明 - English headers
product, item, base, conservative, optimistic, unit, note also work) and
writes ``<raw-data>/inputs.normalized.json``. Every value carries its
source: user / table / default / derived / ecb / missing / na. Nothing is
filled silently: each default becomes an assumption with an id (A1, A2 ...).

Validation levels: "block" (that product cannot be computed), "error"
(value treated as missing, then the missing rule applies), "warning"
(computed as given, listed under "please check").
Python 3.8+, standard library only.
"""

import argparse
import csv
import io
import json
import os
import re
import sys
from decimal import Decimal, InvalidOperation

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edition  # noqa: E402
import pm_fees  # noqa: E402
import pm_fields as F  # noqa: E402
from _shared import i18n, output  # noqa: E402

SCHEMA = "profit-model/inputs/1"
COLS = {
    "product": ("商品", "product", "sku"),
    "item": ("项目", "item", "field", "key"),
    "base": ("基准", "base", "value", "值"),
    "cons": ("保守", "conservative", "low"),
    "opt": ("乐观", "optimistic", "high"),
    "unit": ("单位", "unit", "currency"),
    "note": ("来源说明", "note", "source", "说明"),
}
SCEN = ("cons", "base", "opt")
FX_RANGE = {"USD": (Decimal("5"), Decimal("9")), "EUR": (Decimal("6"), Decimal("10"))}
RETURN_TRIAL = {"cons": "20", "base": "10", "opt": "5"}
TARGET_MARGIN_DEFAULT = "15"
RUNWAY_DEFAULT = "3"
ZERO_DEFAULT_KEYS = (
    "discount_pct",
    "shipping_charged",
    "packaging",
    "first_mile_per_unit",
    "duty_fixed_per_order",
    "fulfil_per_order",
    "storage_per_unit",
    "other_variable_per_order",
    "cross_border_pct",
    "fx_conversion_pct",
    "apps_month",
    "3pl_min_month",
    "other_fixed_month",
)
STARTUP_KEYS = (
    "moq",
    "min_moq",
    "sample_cost",
    "deposit",
    "inbound_3pl_fee",
    "launch_ad_budget",
    "production_days",
    "transit_days",
    "supplier_deposit_pct",
    "payout_delay_days",
    "daily_units",
    "competitor_price_low",
    "competitor_price_high",
) + F.PRO_KEYS
NUM_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")
THOUSANDS_RE = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")
SUFFIXES = ("%", "倍", "x", "X", "天", "件", "个月", "月", "个", "单")


# ---------------------------------------------------------------------------
# CSV reading
# ---------------------------------------------------------------------------


def _col_index(header):
    idx = {}
    for i, h in enumerate(header):
        hn = F.norm(h)
        for col, names in COLS.items():
            if col not in idx and any(hn == F.norm(n) for n in names):
                idx[col] = i
    return idx


def read_rows(path):
    """[{product, item, base, cons, opt, unit, note, line}] from a CSV file."""
    with open(path, "rb") as fh:
        data = fh.read()
    for enc in ("utf-8-sig", "gb18030"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("utf-8", errors="replace")
    sample = text[:2000]
    delim = "\t" if sample.count("\t") > sample.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = []
    header = None
    for line_no, cells in enumerate(reader, 1):
        if not any(c.strip() for c in cells):
            continue
        if header is None:
            idx = _col_index(cells)
            if "item" in idx and "base" in idx:
                header = idx
            continue
        if cells[0].strip().startswith("#"):
            continue

        def cell(col, cells=cells):
            i = header.get(col)
            return cells[i].strip() if i is not None and i < len(cells) else ""

        row = dict((c, cell(c)) for c in COLS)
        if not row["item"]:
            continue
        row["line"] = line_no
        rows.append(row)
    if header is None:
        raise ValueError("no header row with 项目 and 基准 columns")
    return rows


# ---------------------------------------------------------------------------
# cell parsing
# ---------------------------------------------------------------------------


def parse_number(text):
    """(Decimal or None, implied currency or None, ok).

    Empty -> (None, None, True). Strips currency words and symbols, a
    trailing % or unit word, and thousands separators.
    """
    t = str(text or "").strip()
    if not t:
        return None, None, True
    implied = None
    for code, aliases in F.CURRENCY_ALIASES.items():
        for a in sorted(aliases + [code.lower()], key=len, reverse=True):
            low = t.lower()
            if low.startswith(a.lower()):
                t, implied = t[len(a) :].strip(), code
                break
            if low.endswith(a.lower()):
                t, implied = t[: -len(a)].strip(), code
                break
        if implied:
            break
    for s in SUFFIXES:
        if t.endswith(s):
            t = t[: -len(s)].strip()
            break
    if THOUSANDS_RE.match(t):
        t = t.replace(",", "")
    if not NUM_RE.match(t):
        return None, implied, False
    try:
        return Decimal(t), implied, True
    except InvalidOperation:
        return None, implied, False


def dstr(d):
    """Plain string for a Decimal (no exponent)."""
    if d is None:
        return None
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


# ---------------------------------------------------------------------------
# normalizer
# ---------------------------------------------------------------------------


class Normalizer(object):
    def __init__(self, rows, fees, fx_fetched=None, product_hint=None):
        self.rows = rows
        self.fees = fees
        self.fx_fetched = fx_fetched
        self.product_hint = product_hint
        self.issues = []
        self.assumptions = []
        self.fees_used = []
        self.fee_hints = []
        self.flags = set()

    # -- bookkeeping -----------------------------------------------------

    def issue(self, level, code, sku=None, key=None, **params):
        rec = {"level": level, "code": code, "sku": sku, "key": key}
        rec["params"] = dict((k, v) for k, v in params.items())
        if rec not in self.issues:
            self.issues.append(rec)
        return rec

    def assume(self, sku, key, code, badge, **params):
        for a in self.assumptions:
            if (a["sku"], a["key"], a["code"]) == (sku, key, code):
                return a["id"]
        aid = "A%d" % (len(self.assumptions) + 1)
        self.assumptions.append(
            {
                "id": aid,
                "sku": sku,
                "key": key,
                "code": code,
                "badge": badge,
                "params": params,
            }
        )
        return aid

    def use_fee(self, entry):
        if entry and entry["id"] not in self.fees_used:
            self.fees_used.append(entry["id"])
            level = self.fees.stale_level(entry)
            if level == "warn":
                self.issue(
                    "warning",
                    "fee_stale_warn",
                    key=entry["id"],
                    days=self.fees.age(entry),
                )
            elif level == "downgrade":
                self.issue(
                    "warning",
                    "fee_stale_downgrade",
                    key=entry["id"],
                    days=self.fees.age(entry),
                )
                self.flags.add("stale_fee")

    # -- grouping --------------------------------------------------------

    def group(self):
        store, skus, order = {}, {}, []
        for row in self.rows:
            key = F.field_key(row["item"])
            if key is None:
                self.issue("warning", "unknown_item", raw=row["item"], line=row["line"])
                continue
            prod = row["product"]
            is_store = (not prod) or F.norm(prod) in [F.norm(t) for t in F.STORE_TOKENS]
            target = store if is_store else skus.setdefault(prod, {})
            if not is_store and prod not in order:
                order.append(prod)
            if key in target:
                self.issue(
                    "warning",
                    "duplicate",
                    sku=None if is_store else prod,
                    key=key,
                    line=row["line"],
                )
            target[key] = row
        if not order:
            order = [self.product_hint or "商品"]
            skus[order[0]] = {}
        return store, skus, order

    # -- main ------------------------------------------------------------

    def run(self):
        store_rows, sku_rows, order = self.group()
        if len(order) > edition.MAX_SKUS:
            self.issue(
                "warning",
                "too_many_skus",
                n=len(order),
                max=edition.MAX_SKUS,
                dropped=order[edition.MAX_SKUS :],
            )
            order = order[: edition.MAX_SKUS]
        merged = {}
        for name in order:
            m = dict(store_rows)
            m.update(sku_rows.get(name, {}))
            merged[name] = m
        heads = []
        for name in order:
            market = self.market(merged[name], name)
            heads.append((market, self.currency(merged[name], market, name)))
        currency = next((c for _m, c in heads if c), None)
        fx = self.fx(store_rows, merged, order, currency)
        skus = []
        for name, (market, sku_cur) in zip(order, heads):
            skus.append(self.sku(name, merged[name], market, sku_cur, currency))
        store = self.store(store_rows, merged, order, currency, fx, skus)
        computable = [s for s in skus if not s["blocked"]]
        if not computable:
            status = "failed"
        elif len(computable) < len(skus) or self.flags & {
            "duty_missing",
            "return_trial",
            "fx_ecb",
            "stale_fee",
            "payment_entity_assumed",
        }:
            status = "partial"
        else:
            status = "complete"
        return {
            "schema": SCHEMA,
            "edition": edition.EDITION,
            "market": market,
            "currency": currency,
            "fx": fx,
            "skus": skus,
            "store": store,
            "assumptions": self.assumptions,
            "issues": self.issues,
            "flags": sorted(self.flags),
            "fees_used": self.fees_used,
            "fee_hints": self.fee_hints,
            "fee_table_as_of": self.fees.data.get("valid_as_of"),
            "status": status,
        }

    # -- global fields ---------------------------------------------------

    def market(self, rows, sku):
        row = rows.get("market")
        if row is None or not row["base"]:
            self.issue("block", "missing_required", sku=sku, key="market")
            return None
        code = F.market_code(row["base"])
        if code is None:
            self.issue(
                "block", "market_unknown", sku=sku, key="market", raw=row["base"]
            )
        return code

    def currency(self, rows, market, sku):
        row = rows.get("sale_currency")
        if row is not None and row["base"]:
            code = F.currency_code(row["base"])
            if code not in F.CURRENCIES:
                self.issue(
                    "block",
                    "currency_unsupported",
                    sku=sku,
                    key="sale_currency",
                    raw=row["base"],
                )
                return None
            return code
        code = F.market_currency(market)
        if code:
            self.assume(
                sku, "sale_currency", "currency_from_market", "inferred", currency=code
            )
        return code

    def fx(self, store_rows, merged, order, currency):
        row = store_rows.get("fx_cny_per_unit")
        if row is None:
            for name in order:
                if "fx_cny_per_unit" in merged[name]:
                    row = merged[name]["fx_cny_per_unit"]
                    break
        info = {
            "base": None,
            "cons": None,
            "opt": None,
            "source": "missing",
            "badge": "not_found",
            "date": None,
            "source_text": None,
            "url": None,
            "ranged": False,
        }
        date_row = store_rows.get("fx_date") or merged[order[0]].get("fx_date")
        src_row = store_rows.get("fx_source") or merged[order[0]].get("fx_source")
        value = None
        if row is not None and row["base"]:
            value, _imp, ok = parse_number(row["base"])
            if not ok or value is None or value <= 0:
                self.issue(
                    "error", "fx_invalid", key="fx_cny_per_unit", raw=row["base"]
                )
                value = None
        if value is not None:
            info.update(source="user", badge="user_input", base=dstr(value))
            info["date"] = date_row["base"] if date_row and date_row["base"] else None
            info["source_text"] = (
                src_row["base"]
                if src_row and src_row["base"]
                else (row["note"] or None)
            )
            scen = self.scenarios(None, "fx_cny_per_unit", row, value)
            info.update(cons=scen["cons"], opt=scen["opt"], ranged=scen["ranged"])
            if not info["date"]:
                self.issue("info", "fx_no_date", key="fx_cny_per_unit")
        elif self.fx_fetched and self.fx_fetched.get("rates", {}).get(currency):
            rate = Decimal(str(self.fx_fetched["rates"][currency]))
            info.update(
                source="ecb",
                badge="verified",
                base=dstr(rate),
                cons=dstr(rate),
                opt=dstr(rate),
                date=self.fx_fetched.get("date"),
                url=self.fx_fetched.get("url"),
                source_text="ECB euro foreign exchange reference rates",
            )
            self.flags.add("fx_ecb")
            self.assume(
                None,
                "fx_cny_per_unit",
                "fx_ecb",
                "verified",
                value=dstr(rate),
                date=info["date"],
                url=info["url"],
            )
        else:
            self.issue("block", "fx_missing", key="fx_cny_per_unit")
        if info["base"] and currency in FX_RANGE:
            lo, hi = FX_RANGE[currency]
            for col in SCEN:
                v = Decimal(info[col])
                if v < lo or v > hi:
                    self.issue(
                        "warning",
                        "fx_out_of_range",
                        key="fx_cny_per_unit",
                        value=info[col],
                        lo=dstr(lo),
                        hi=dstr(hi),
                        currency=currency,
                    )
                    break
        return info

    # -- one product -----------------------------------------------------

    def sku(self, name, rows, market, sku_cur, currency):
        if sku_cur and currency and sku_cur != currency:
            self.issue(
                "block",
                "currency_mismatch",
                sku=name,
                key="sale_currency",
                currency=sku_cur,
                first=currency,
            )
        skip = ("market", "sale_currency", "fx_cny_per_unit", "fx_date", "fx_source")
        fields = {}
        for key in F.ORDER:
            if key in skip or key in F.STORE_KEYS:
                continue
            fields[key] = self.field(name, key, rows.get(key), currency)
        self.fill_defaults(name, fields, rows, market, currency)
        blocked = any(
            i["level"] == "block" and i["sku"] in (name, None) for i in self.issues
        )
        return {"name": name, "market": market, "blocked": blocked, "fields": fields}

    def field(self, sku, key, row, currency):
        """Parse one input row (or None) into an entry; no defaults yet."""
        f = F.FIELDS[key]
        entry = {
            "source": "missing",
            "badge": "not_found",
            "base": None,
            "cons": None,
            "opt": None,
            "currency": None,
            "ranged": False,
            "note": "",
            "raw": None,
            "assumption": None,
            "table_id": None,
        }
        if row is None or not row["base"]:
            if row is not None:
                entry["note"] = row["note"]
            return entry
        entry["note"] = row["note"]
        entry["raw"] = row["base"]
        kind = f["kind"]
        if kind == "choice":
            val = F.choice_value(key, row["base"])
            if val is None:
                self.issue("error", "choice_invalid", sku=sku, key=key, raw=row["base"])
                return entry
            entry.update(source="user", badge="user_input", value=val)
            return entry
        if kind in ("text", "currency"):
            entry.update(source="user", badge="user_input", value=row["base"])
            return entry
        value, implied, ok = parse_number(row["base"])
        if not ok:
            level = "block" if kind == "money" else "error"
            self.issue(level, "not_number", sku=sku, key=key, raw=row["base"])
            return entry
        if kind == "money":
            cur = F.currency_code(row["unit"]) if row["unit"] else None
            cur = cur or implied
            if cur is None:
                self.issue(
                    "block", "currency_missing", sku=sku, key=key, raw=row["base"]
                )
                return entry
            if cur != "CNY" and cur != currency:
                self.issue(
                    "block",
                    "currency_unsupported",
                    sku=sku,
                    key=key,
                    raw="%s %s" % (row["base"], row["unit"] or cur),
                )
                return entry
            entry["currency"] = cur
            if key == "price" and value <= 0:
                self.issue(
                    "block", "price_nonpositive", sku=sku, key=key, raw=row["base"]
                )
                return entry
            if value < 0:
                if key == "product_cost":
                    self.issue(
                        "block", "cost_negative", sku=sku, key=key, raw=row["base"]
                    )
                else:
                    self.issue("error", "negative", sku=sku, key=key, raw=row["base"])
                return entry
        if not self.valid(sku, key, kind, value, row["base"]):
            return entry
        scen = self.scenarios(sku, key, row, value)
        entry.update(
            source="user",
            badge="user_input",
            base=dstr(value),
            cons=scen["cons"],
            opt=scen["opt"],
            ranged=scen["ranged"],
        )
        if kind in ("pct", "share") and key in F.FRACTION_SUSPECT and 0 < value < 1:
            self.issue(
                "warning",
                "fraction_suspect",
                sku=sku,
                key=key,
                raw=row["base"],
                pct=dstr(value * 100),
            )
        return entry

    def valid(self, sku, key, kind, value, raw):
        bad = None
        if kind == "pct" and (value < 0 or value >= 100):
            bad = "pct_out_of_range"
        elif kind == "share" and (value < 0 or value > 100):
            bad = "pct_out_of_range"
        elif kind == "count" and (value <= 0 or value != value.to_integral_value()):
            bad = "count_invalid"
        elif kind in ("days",) and value < 0:
            bad = "days_negative"
        elif kind in ("rate", "ratio", "months") and value <= 0:
            bad = "not_positive"
        elif kind == "decimal" and value < 0:
            bad = "negative"
        if bad:
            self.issue("error", bad, sku=sku, key=key, raw=raw)
            return False
        return True

    def scenarios(self, sku, key, row, base):
        out = {"cons": dstr(base), "opt": dstr(base), "ranged": False}
        kind = F.FIELDS[key]["kind"]
        for col in ("cons", "opt"):
            text = row.get(col) or ""
            if not text:
                continue
            value, _imp, ok = parse_number(text)
            if (
                not ok
                or value is None
                or (kind in ("pct",) and (value < 0 or value >= 100))
                or (kind in ("rate", "ratio") and value <= 0)
                or value < 0
            ):
                self.issue(
                    "warning", "scenario_invalid", sku=sku, key=key, col=col, raw=text
                )
                continue
            out[col] = dstr(value)
            out["ranged"] = True
        direction = F.FIELDS[key]["cons"]
        c, o = Decimal(out["cons"]), Decimal(out["opt"])
        if direction == "high" and (c < base or o > base):
            self.issue("warning", "direction_reversed", sku=sku, key=key)
        if direction == "low" and (c > base or o < base):
            self.issue("warning", "direction_reversed", sku=sku, key=key)
        return out

    # -- defaults --------------------------------------------------------

    def set_default(
        self,
        sku,
        fields,
        key,
        value,
        code,
        badge="inferred",
        currency=None,
        scen=None,
        **params,
    ):
        e = fields[key]
        e["source"] = "default"
        e["badge"] = badge
        e["base"] = value
        e["cons"] = (scen or {}).get("cons", value)
        e["opt"] = (scen or {}).get("opt", value)
        e["currency"] = currency
        e["assumption"] = self.assume(sku, key, code, badge, value=value, **params)

    def from_table(
        self,
        sku,
        fields,
        key,
        value,
        entry,
        currency=None,
        assumed=None,
        code="table_value",
    ):
        e = fields[key]
        badge = self.fees.badge(entry)
        if assumed:
            badge = "inferred" if badge == "verified" else badge
        e.update(
            source="table",
            badge=badge,
            base=value,
            cons=value,
            opt=value,
            currency=currency,
            table_id=entry["id"],
        )
        e["assumption"] = self.assume(
            sku,
            key,
            code,
            badge,
            value=value,
            table_id=entry["id"],
            retrieved=entry.get("retrieved"),
            assumed=assumed or "",
        )
        self.use_fee(entry)

    def fill_defaults(self, sku, fields, rows, market, currency):
        fe = self.fees
        # required
        for key in F.REQUIRED:
            if key == "market":
                continue
            if fields[key]["source"] == "missing" and not self.has_block(sku, key):
                self.issue("block", "missing_required", sku=sku, key=key)
        # choices with defaults
        if fields["shopify_plan"]["source"] == "missing":
            fields["shopify_plan"].update(
                source="default", badge="inferred", value="basic"
            )
            fields["shopify_plan"]["assumption"] = self.assume(
                sku, "shopify_plan", "plan_default", "inferred"
            )
        plan = fields["shopify_plan"]["value"]
        if fields["return_mode"]["source"] == "missing":
            fields["return_mode"].update(
                source="default", badge="inferred", value="refund"
            )
            fields["return_mode"]["assumption"] = self.assume(
                sku, "return_mode", "return_mode_default", "inferred"
            )
        if fields["duty_basis"]["source"] == "missing":
            fields["duty_basis"].update(source="default", badge="inferred", value="fob")
            fields["duty_basis"]["assumption"] = self.assume(
                sku, "duty_basis", "duty_basis_default", "inferred"
            )
        provider_e = fields["payment_provider"]
        has_rate = fields["payment_pct"]["source"] == "user"
        if provider_e["source"] == "missing":
            if has_rate:
                provider_e.update(source="derived", badge="inferred", value="other")
                provider_e["assumption"] = self.assume(
                    sku, "payment_provider", "provider_unknown", "inferred"
                )
            else:
                self.issue(
                    "block", "payment_provider_missing", sku=sku, key="payment_provider"
                )
        provider = provider_e.get("value")
        entity = fields["seller_entity"].get("value")
        # payment fee
        if fields["payment_pct"]["source"] == "missing" and provider:
            hit = fe.payment_fee(provider, market, plan, currency)
            if provider == "shopify_payments" and entity == "cn":
                hit = None
                self.issue("block", "payment_cn_rate", sku=sku, key="payment_pct")
            elif hit is None:
                self.issue(
                    "block",
                    "payment_rate_missing",
                    sku=sku,
                    key="payment_pct",
                    provider=provider,
                )
            if hit:
                pct, fixed, entry = hit
                assumed = None
                if provider == "shopify_payments" and entity is None:
                    assumed = "entity_us"
                    self.flags.add("payment_entity_assumed")
                if (
                    fields["shopify_plan"]["source"] == "default"
                    and provider == "shopify_payments"
                ):
                    assumed = assumed or "plan_basic"
                self.from_table(
                    sku, fields, "payment_pct", dstr(pct), entry, assumed=assumed
                )
                if fields["payment_fixed"]["source"] == "missing":
                    self.from_table(
                        sku,
                        fields,
                        "payment_fixed",
                        dstr(fixed),
                        entry,
                        currency=entry.get("currency"),
                        assumed=assumed,
                    )
        elif fields["payment_pct"]["source"] == "user" and provider:
            hit = fe.payment_fee(provider, market, plan, currency)
            if hit:
                pct, fixed, entry = hit
                same = Decimal(fields["payment_pct"]["base"]) == pct
                fixed_e = fields["payment_fixed"]
                if fixed_e["source"] == "user" and fixed_e["currency"] == currency:
                    same = same and Decimal(fixed_e["base"]) == fixed
                self.assume(
                    sku,
                    "payment_pct",
                    "rate_matches_table" if same else "rate_differs_table",
                    fe.badge(entry),
                    table_id=entry["id"],
                    retrieved=entry.get("retrieved"),
                    table_value=pm_fees.value_text(entry),
                )
                self.use_fee(entry)
        if fields["payment_fixed"]["source"] == "missing":
            self.set_default(
                sku, fields, "payment_fixed", "0", "zero_default", currency=currency
            )
        # Shopify third-party fee
        tp = fields["third_party_txn_pct"]
        if tp["source"] == "missing":
            if provider == "shopify_payments":
                tp.update(source="na", badge=None, base="0", cons="0", opt="0")
                entry = fe.get("shopify-3p-fee-basic")
                tp["assumption"] = self.assume(
                    sku,
                    "third_party_txn_pct",
                    "third_party_sp",
                    fe.badge(entry) if entry else "inferred",
                    retrieved=entry.get("retrieved") if entry else None,
                )
            elif plan == "none":
                tp.update(source="na", badge=None, base="0", cons="0", opt="0")
            elif provider in ("stripe", "paypal_cn", "paypal_us", "paypal_eu"):
                hit = fe.third_party_fee(plan)
                if hit:
                    assumed = (
                        "plan_basic"
                        if fields["shopify_plan"]["source"] == "default"
                        else None
                    )
                    self.from_table(
                        sku,
                        fields,
                        "third_party_txn_pct",
                        dstr(hit[0]),
                        hit[1],
                        assumed=assumed,
                    )
            elif provider:
                self.set_default(
                    sku, fields, "third_party_txn_pct", "0", "third_party_unknown"
                )
        # VAT
        vat = fields["vat_pct"]
        if vat["source"] == "missing":
            if F.needs_vat(market):
                self.issue("block", "vat_missing", sku=sku, key="vat_pct")
            else:
                vat.update(source="na", badge=None, base="0", cons="0", opt="0")
        # zero defaults
        for key in ZERO_DEFAULT_KEYS:
            if key in fields and fields[key]["source"] == "missing":
                kind = F.FIELDS[key]["kind"]
                cur = currency if kind == "money" else None
                hints = []
                if key in ("cross_border_pct", "fx_conversion_pct") and provider:
                    for entry in fe.hint_entries(provider, market):
                        if ("fx" in entry["id"]) != (key == "fx_conversion_pct"):
                            continue
                        if entry["id"] not in self.fee_hints:
                            self.fee_hints.append(entry["id"])
                        hints.append(entry["id"])
                self.set_default(
                    sku, fields, key, "0", "zero_default", currency=cur, hints=hints
                )
        if fields["units_per_order"]["source"] == "missing":
            self.set_default(sku, fields, "units_per_order", "1", "units_default")
        # duty
        if fields["duty_rate_pct"]["source"] == "missing":
            d = fields["duty_rate_pct"]
            d.update(base="0", cons="0", opt="0")
            d["assumption"] = self.assume(
                sku, "duty_rate_pct", "duty_missing", "not_found"
            )
            self.flags.add("duty_missing")
        # returns
        if fields["return_rate_pct"]["source"] == "missing":
            self.set_default(
                sku,
                fields,
                "return_rate_pct",
                RETURN_TRIAL["base"],
                "return_trial",
                scen=RETURN_TRIAL,
            )
            self.flags.add("return_trial")
        if fields["return_ship_per_return"]["source"] == "missing":
            lm = fields["last_mile_per_order"]
            if lm["source"] == "user":
                e = fields["return_ship_per_return"]
                e.update(
                    source="default",
                    badge="inferred",
                    base=lm["base"],
                    cons=lm["cons"],
                    opt=lm["opt"],
                    currency=lm["currency"],
                )
                e["assumption"] = self.assume(
                    sku, "return_ship_per_return", "return_ship_default", "inferred"
                )
        if fields["resale_pct"]["source"] == "missing":
            self.set_default(sku, fields, "resale_pct", "0", "resale_default")
        # ads
        cpa, roas = fields["cpa"], fields["roas"]
        if cpa["source"] == "user" and roas["source"] == "user":
            self.issue("warning", "both_cpa_roas", sku=sku, key="cpa")
        if cpa["source"] != "user" and roas["source"] != "user":
            self.assume(sku, "cpa", "no_ads", "not_found")
        if fields["target_margin_pct"]["source"] == "missing":
            self.set_default(
                sku,
                fields,
                "target_margin_pct",
                TARGET_MARGIN_DEFAULT,
                "target_margin_default",
            )

    def has_block(self, sku, key):
        return any(
            i["level"] == "block" and i["sku"] == sku and i["key"] == key
            for i in self.issues
        )

    # -- store level -----------------------------------------------------

    def store(self, store_rows, merged, order, currency, fx, skus):
        """Store-level inputs: from 全店 rows, else the first product that has
        them (a warning when products disagree)."""
        rows = {}
        for key in F.STORE_KEYS:
            if key in store_rows:
                rows[key] = store_rows[key]
                continue
            found = [merged[n][key] for n in order if key in merged[n]]
            if found:
                rows[key] = found[0]
                if len(set(r["base"] for r in found)) > 1:
                    self.issue("warning", "store_value_conflict", key=key)
        fields = {}
        for key in F.STORE_KEYS:
            fields[key] = self.field(None, key, rows.get(key), currency)
        plan_e = skus[0]["fields"]["shopify_plan"] if skus else {}
        plan = plan_e.get("value")
        market = skus[0]["market"] if skus else None
        if fields["plan_fee_month"]["source"] == "missing":
            hit = self.fees.plan_fee(plan, market)
            if plan == "none":
                fields["plan_fee_month"].update(
                    source="na",
                    badge=None,
                    base="0",
                    cons="0",
                    opt="0",
                    currency=currency,
                )
            elif hit and hit[1] == currency:
                assumed = "plan_basic" if plan_e.get("source") == "default" else None
                self.from_table(
                    None,
                    fields,
                    "plan_fee_month",
                    dstr(hit[0]),
                    hit[2],
                    currency=hit[1],
                    assumed=assumed,
                )
            else:
                fields["plan_fee_month"]["assumption"] = self.assume(
                    None, "plan_fee_month", "plan_fee_missing", "not_found"
                )
        for key in ("apps_month", "3pl_min_month", "other_fixed_month"):
            if fields[key]["source"] == "missing":
                self.set_default(
                    None, fields, key, "0", "zero_default", currency=currency
                )
        if fields["runway_months"]["source"] == "missing":
            self.set_default(
                None, fields, "runway_months", RUNWAY_DEFAULT, "runway_default"
            )
        return fields


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def normalize(csv_path, fees=None, fx_fetched=None, product_hint=None):
    fees = fees or pm_fees.FeeTable(pm_fees.load())
    rows = read_rows(csv_path)
    return Normalizer(rows, fees, fx_fetched, product_hint).run()


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def main(argv=None):
    output.safe_console()
    ap = argparse.ArgumentParser(description="Validate inputs.csv and fill defaults.")
    ap.add_argument("path", help="run folder or its raw-data folder")
    ap.add_argument("--lang", default="zh", choices=i18n.LANGS)
    ap.add_argument("--fx-json", help="exchange rates written by pm_fx.py")
    args = ap.parse_args(argv)
    _run, raw = output.locate_run(args.path, args.lang)
    csv_path = os.path.join(raw, "inputs.csv")
    if not os.path.isfile(csv_path):
        print("error: %s not found" % csv_path, file=sys.stderr)
        return 1
    fx = None
    fx_path = args.fx_json or os.path.join(raw, "fx.json")
    if os.path.isfile(fx_path):
        with open(fx_path, encoding="utf-8") as fh:
            fx = json.load(fh)
    try:
        data = normalize(csv_path, fx_fetched=fx)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    write_json(os.path.join(raw, "inputs.normalized.json"), data)
    print(
        "status: %s, products: %d, issues: %d"
        % (data["status"], len(data["skus"]), len(data["issues"]))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
