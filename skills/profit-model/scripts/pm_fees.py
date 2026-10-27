"""Built-in fee table (data/fee_tables.json): lookups and freshness.

Every entry carries ``retrieved`` (when it was checked) and ``status``
(verified / unverified / conflict). Badge rules:

  verified and <= stale_downgrade_days old -> "verified"
  verified but older                        -> "inferred" (re-check needed)
  unverified                                -> "inferred", never a default
  conflict                                  -> "conflicting"

Older than stale_warn_days adds a warning. User values always win over the
table; the table only fills gaps and is shown with its dates.
Python 3.8+, standard library only.
"""

import datetime
import json
import os
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
TABLE_PATH = os.path.join(HERE, "..", "data", "fee_tables.json")


def load(path=None):
    with open(path or TABLE_PATH, encoding="utf-8-sig") as fh:
        return json.load(fh)


def parse_date(text):
    try:
        return datetime.datetime.strptime(str(text)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


class FeeTable(object):
    def __init__(self, data, today=None):
        self.data = data
        self.today = today or datetime.date.today()
        self.warn_days = int(data.get("stale_warn_days", 90))
        self.downgrade_days = int(data.get("stale_downgrade_days", 180))
        self.by_id = {}
        for group in ("fees", "refund_rules", "pointers"):
            for entry in data.get(group, []):
                self.by_id[entry["id"]] = entry

    def get(self, entry_id):
        return self.by_id.get(entry_id)

    def age(self, entry):
        day = parse_date(entry.get("retrieved"))
        return None if day is None else (self.today - day).days

    def badge(self, entry):
        status = entry.get("status")
        if status == "conflict":
            return "conflicting"
        if status != "verified":
            return "inferred"
        age = self.age(entry)
        if age is None or age > self.downgrade_days:
            return "inferred"
        return "verified"

    def stale_level(self, entry):
        """None, "warn" (> warn days) or "downgrade" (> downgrade days)."""
        age = self.age(entry)
        if age is None:
            return "downgrade"
        if age > self.downgrade_days:
            return "downgrade"
        if age > self.warn_days:
            return "warn"
        return None

    def usable_default(self, entry):
        """Unverified entries never fill a gap."""
        return entry is not None and entry.get("status") in ("verified", "conflict")

    # -- lookups ---------------------------------------------------------

    def plan_fee(self, plan, market):
        """(Decimal monthly fee, currency, entry) or None."""
        if market != "US" or plan not in ("basic", "grow", "advanced", "plus"):
            return None
        entry = self.get("shopify-plan-us-%s" % plan)
        if not self.usable_default(entry):
            return None
        value = entry["value"]
        amount = value.get("monthly", value.get("monthly_from"))
        return Decimal(str(amount)), entry.get("currency", "USD"), entry

    def third_party_fee(self, plan):
        """(Decimal percent, entry) or None."""
        if plan not in ("basic", "grow", "advanced", "plus"):
            return None
        entry = self.get("shopify-3p-fee-%s" % plan)
        if not self.usable_default(entry):
            return None
        return Decimal(str(entry["value"])), entry

    def payment_fee(self, provider, market, plan, currency):
        """(pct, fixed, entry) for a provider in a market, or None.

        Only the standard online rate of the provider's own region; a
        fixed fee in another currency than the sale currency is not used.
        """
        entry_id = None
        if provider == "shopify_payments" and market == "US":
            entry_id = "shopify-payments-us-online-standard-%s" % (
                plan if plan in ("basic", "grow", "advanced", "plus") else "basic"
            )
        elif provider == "stripe" and market == "US":
            entry_id = "stripe-us-domestic"
        elif provider == "stripe" and market and market.startswith("EU-"):
            entry_id = "stripe-ie-standard-eea"
        elif provider == "paypal_us" and market == "US":
            entry_id = "paypal-us-checkout"
        elif provider == "paypal_cn":
            entry_id = "paypal-cn-cross-border"
        elif provider == "paypal_eu" and market and market.startswith("EU-"):
            entry_id = "paypal-de-checkout"
        entry = self.get(entry_id) if entry_id else None
        if not self.usable_default(entry):
            return None
        value = entry["value"]
        fee_currency = entry.get("currency", "USD")
        if fee_currency != currency:
            return None
        return Decimal(str(value["pct"])), Decimal(str(value["fixed"])), entry

    def hint_entries(self, provider, market):
        """Entries shown as hints for surcharges that default to 0."""
        ids = []
        if provider == "shopify_payments":
            ids += ["shopify-payments-us-international-surcharge"]
            ids += [
                "shopify-payments-fx-us"
                if market == "US"
                else "shopify-payments-fx-other"
            ]
        elif provider == "stripe" and market == "US":
            ids += ["stripe-us-international", "stripe-us-fx"]
        elif provider == "paypal_us":
            ids += ["paypal-us-international", "paypal-us-fx"]
        elif provider == "paypal_cn":
            ids += ["paypal-cn-fx"]
        return [self.get(i) for i in ids if self.get(i)]

    def refund_rule(self, provider):
        ids = {
            "shopify_payments": "refund-shopify-payments-card-fee",
            "stripe": "refund-stripe-fee",
            "paypal_us": "refund-paypal-us-fee",
        }
        return self.get(ids.get(provider, ""))


def value_text(entry):
    """Short human text for an entry value (numbers kept as in the table)."""
    value = entry.get("value")
    unit = entry.get("unit", "")
    cur = entry.get("currency", "")
    if isinstance(value, dict):
        if "pct" in value and "fixed" in value:
            return "%s%% + %s %s" % (value["pct"], value["fixed"], cur)
        if "monthly" in value:
            return "%s %s" % (value["monthly"], cur)
        if "monthly_from" in value:
            return ">= %s %s" % (value["monthly_from"], cur)
        if "base" in value and "conservative" in value:
            return "%s%% / %s%%" % (value["base"], value["conservative"])
        if "pct" in value and "min_fee" in value:
            return "%s%% (min %s %s)" % (value["pct"], value["min_fee"], cur)
        return ", ".join(
            "%s=%s" % (k, v)
            for k, v in sorted(value.items())
            if not isinstance(v, (list, dict))
        )
    if isinstance(value, bool):
        return "yes" if value else "no"
    if unit == "percent":
        return "%s%%" % value
    if value is None:
        return ""
    return "%s %s" % (value, cur) if cur else str(value)
