#!/usr/bin/env python3
"""Payments questionnaire: validate, fill defaults, write questionnaire.json.

Every field is optional; a missing answer is "unknown" (never guessed).
Answers are what the seller told us, shown in the report as [你提供的].

CLI (merges into <raw-data>/questionnaire.json, then reruns compute.py):
    python questionnaire.py "<run folder>" [--lang zh|en]
        [--set key=value ...]           e.g. --set entity_country=CN --set aov=45
        [--gateway SPEC ...]            current setup, one per payment route
        [--scenario "NAME=SPEC;SPEC"]   Pro: alternative setups to compare
        [--file answers.json] [--no-compute] [--show]

Gateway SPEC: ``provider:share[:pct+fixed[:withdraw_fee]]``, share and pct in
percent, fixed and withdraw_fee in the order currency. Examples:
    paypal:40                    PayPal, 40 % of orders, official rate
    card_other:60:3.5+0.30       another card processor at 3.5 % + 0.30
    stripe:100:2.9+0.30          Stripe with the seller's contract rate
Providers: shopify_payments, stripe, paypal, airwallex, card_other, manual.
Python 3.8+, standard library only.
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shared import i18n, output  # noqa: E402

FILE_NAME = "questionnaire.json"
SCHEMA = "payments-readiness/questionnaire/1"

CHOICES = {
    "entity_country": ("CN", "HK", "US", "CA", "UK", "OTHER"),
    "entity_type": ("company", "sole_proprietor", "individual"),
    "payout_account": (
        "cn_bank",
        "worldfirst",
        "payoneer",
        "pingpong",
        "lianlian",
        "airwallex",
        "mercury",
        "us_bank",
        "other",
        "none",
    ),
    "shopify_plan": ("basic", "grow", "advanced", "plus", "none"),
    "mor": ("no", "shopify_managed_markets", "stripe_managed_payments", "other"),
}
PROVIDERS = (
    "shopify_payments",
    "stripe",
    "paypal",
    "airwallex",
    "card_other",
    "manual",
)
ALIASES = {
    "entity_country": {
        "china": "CN",
        "mainland": "CN",
        "大陆": "CN",
        "中国": "CN",
        "中国大陆": "CN",
        "hongkong": "HK",
        "hong_kong": "HK",
        "香港": "HK",
        "usa": "US",
        "us_llc": "US",
        "美国": "US",
        "美国llc": "US",
        "canada": "CA",
        "加拿大": "CA",
        "gb": "UK",
        "英国": "UK",
    },
    "provider": {
        "shopify": "shopify_payments",
        "sp": "shopify_payments",
        "shoppay": "shopify_payments",
        "card": "card_other",
        "other": "card_other",
        "third_party_card": "card_other",
        "credit_card": "card_other",
        "cod": "manual",
        "bank_transfer": "manual",
    },
    "shopify_plan": {"shopify": "grow", "starter": "basic"},
    "payout_account": {
        "bank": "cn_bank",
        "国内银行": "cn_bank",
        "万里汇": "worldfirst",
    },
}
NUMBERS = (
    "aov",
    "monthly_orders",
    "intl_card_share",
    "fx_share",
    "withdrawals_per_month",
    "payout_fx_pct",
    "refund_rate",
    "dispute_rate",
    "ship_days",
)
BOOLS = ("sells_to_cn", "subscription", "preorder")
TEXTS = ("category", "payout_currency", "aov_currency", "store_name", "notes")
LISTS = ("market_mix",)
ALL_KEYS = tuple(CHOICES) + NUMBERS + BOOLS + TEXTS + LISTS + ("gateways", "scenarios")

GATEWAY_RE = re.compile(
    r"^\s*(?P<provider>[A-Za-z_]+)\s*:\s*(?P<share>[\d.]+)\s*%?"
    r"(?::\s*(?P<pct>[\d.]+)\s*%?\s*(?:\+\s*(?P<fixed>[\d.]+))?)?"
    r"(?::\s*(?P<wd>[\d.]+))?\s*$"
)


class QuestionnaireError(ValueError):
    pass


def empty():
    q = {"schema": SCHEMA, "answered": []}
    for key in ALL_KEYS:
        q[key] = None
    q["gateways"] = []
    q["scenarios"] = []
    q["market_mix"] = {}
    return q


def _num(key, value):
    if value in (None, "", "unknown"):
        return None
    try:
        v = float(str(value).replace(",", "").replace("%", "").strip())
    except ValueError:
        raise QuestionnaireError("%s: not a number: %r" % (key, value))
    if v < 0:
        raise QuestionnaireError("%s: must not be negative" % key)
    if (
        key in ("intl_card_share", "fx_share", "refund_rate", "dispute_rate")
        and v > 100
    ):
        raise QuestionnaireError("%s: percent must be 0-100" % key)
    return int(v) if v.is_integer() and key in ("monthly_orders", "ship_days") else v


def _bool(key, value):
    if value in (None, "", "unknown"):
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "y", "是", "有"):
        return True
    if s in ("0", "false", "no", "n", "否", "没有", "无"):
        return False
    raise QuestionnaireError("%s: use yes/no" % key)


def _choice(key, value):
    if value in (None, "", "unknown"):
        return None
    raw = str(value).strip()
    s = raw.lower().replace(" ", "_").replace("-", "_")
    s = ALIASES.get(key, {}).get(s, ALIASES.get(key, {}).get(raw, s))
    for opt in CHOICES[key]:
        if s.lower() == opt.lower():
            return opt
    raise QuestionnaireError(
        "%s: %r is not one of %s" % (key, value, ", ".join(CHOICES[key]))
    )


def provider_of(value):
    s = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    s = ALIASES["provider"].get(s, s)
    if s not in PROVIDERS:
        raise QuestionnaireError(
            "gateway provider %r is not one of %s" % (value, ", ".join(PROVIDERS))
        )
    return s


def parse_gateway(spec):
    """'paypal:40' / 'card_other:60:3.5+0.30[:35]' -> gateway dict."""
    if isinstance(spec, dict):
        g = dict(spec)
        g["provider"] = provider_of(g.get("provider"))
        for k in ("share", "pct", "fixed", "withdraw_fee"):
            g[k] = _num("gateway." + k, g.get(k))
        return g
    m = GATEWAY_RE.match(str(spec))
    if not m:
        raise QuestionnaireError(
            "gateway %r: use provider:share[:pct+fixed[:withdraw_fee]]" % spec
        )
    pct = m.group("pct")
    return {
        "provider": provider_of(m.group("provider")),
        "share": float(m.group("share")),
        "pct": float(pct) if pct else None,
        "fixed": float(m.group("fixed"))
        if m.group("fixed")
        else (0.0 if pct else None),
        "withdraw_fee": float(m.group("wd")) if m.group("wd") else None,
    }


def parse_market_mix(value):
    """'US:70,CA:30' or {'US': 70} -> {'US': 70.0}."""
    if value in (None, "", "unknown"):
        return {}
    if isinstance(value, dict):
        items = value.items()
    else:
        items = []
        for part in re.split(r"[,;]\s*", str(value)):
            if not part.strip():
                continue
            if ":" not in part:
                items.append((part, 100.0 if "," not in str(value) else None))
                continue
            k, v = part.split(":", 1)
            items.append((k, v))
    out = {}
    for k, v in items:
        code = str(k).strip().upper()
        code = "UK" if code == "GB" else code
        out[code] = _num("market_mix", v) if v is not None else None
    return out


def set_value(q, key, value):
    if key not in ALL_KEYS:
        raise QuestionnaireError(
            "unknown field %r (known: %s)" % (key, ", ".join(ALL_KEYS))
        )
    if key in CHOICES:
        q[key] = _choice(key, value)
    elif key in NUMBERS:
        q[key] = _num(key, value)
    elif key in BOOLS:
        q[key] = _bool(key, value)
    elif key == "market_mix":
        q[key] = parse_market_mix(value)
    elif key == "gateways":
        q[key] = [parse_gateway(g) for g in (value or [])]
    elif key == "scenarios":
        q[key] = [
            {
                "name": str(s.get("name") or "scenario %d" % (i + 1)),
                "gateways": [parse_gateway(g) for g in s.get("gateways") or []],
                "shopify_plan": _choice("shopify_plan", s.get("shopify_plan"))
                if s.get("shopify_plan")
                else None,
            }
            for i, s in enumerate(value or [])
        ]
    else:
        q[key] = None if value in (None, "", "unknown") else str(value).strip()
    if key in ("aov_currency", "payout_currency") and q[key]:
        q[key] = q[key].upper().replace("RMB", "CNY")
    if q[key] not in (None, [], {}) and key not in q["answered"]:
        q["answered"].append(key)
    return q


def normalize(data):
    """Validated questionnaire from any dict (unknown keys are rejected)."""
    q = empty()
    for key, value in (data or {}).items():
        if key in ("schema", "answered"):
            continue
        set_value(q, key, value)
    return q


def parse_scenario(text):
    """'NAME=SPEC;SPEC[;plan=grow]' -> scenario dict (Pro comparisons)."""
    if "=" not in text:
        raise QuestionnaireError("scenario: use NAME=provider:share;provider:share")
    name, rest = text.split("=", 1)
    gws, plan = [], None
    for part in rest.split(";"):
        part = part.strip()
        if not part:
            continue
        if part.lower().startswith("plan="):
            plan = _choice("shopify_plan", part.split("=", 1)[1])
            continue
        gws.append(parse_gateway(part))
    return {"name": name.strip(), "gateways": gws, "shopify_plan": plan}


def load(data_dir):
    path = os.path.join(data_dir, FILE_NAME)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8-sig") as fh:
        return normalize(json.load(fh))


def save(data_dir, q):
    path = os.path.join(data_dir, FILE_NAME)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(q, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return path


def main(argv=None):
    output.safe_console()
    ap = argparse.ArgumentParser(description="Write the payments questionnaire.")
    ap.add_argument("run_dir", help="run folder or its raw-data folder")
    ap.add_argument("--lang", choices=i18n.LANGS)
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--gateway", action="append", default=[], metavar="SPEC")
    ap.add_argument("--scenario", action="append", default=[], metavar="NAME=SPECS")
    ap.add_argument("--file", help="JSON file with answers to merge")
    ap.add_argument("--no-compute", action="store_true")
    ap.add_argument("--show", action="store_true", help="print the result")
    args = ap.parse_args(argv)
    lang = args.lang
    data_dir = None
    for code in [lang] if lang else list(i18n.LANGS):
        run, raw = output.locate_run(args.run_dir, code)
        if os.path.isdir(raw):
            data_dir, lang = raw, code
            break
    if data_dir is None:
        print(
            "error: no run folder at %s (run collect.py first)" % args.run_dir,
            file=sys.stderr,
        )
        return 2
    try:
        q = load(data_dir) or empty()
        if args.file:
            with open(args.file, encoding="utf-8-sig") as fh:
                for k, v in json.load(fh).items():
                    if k not in ("schema", "answered"):
                        set_value(q, k, v)
        for item in args.set:
            if "=" not in item:
                raise QuestionnaireError("--set needs KEY=VALUE, got %r" % item)
            k, v = item.split("=", 1)
            set_value(q, k.strip(), v.strip())
        if args.gateway:
            set_value(q, "gateways", [parse_gateway(g) for g in args.gateway])
        if args.scenario:
            q["scenarios"] = [parse_scenario(s) for s in args.scenario]
            if "scenarios" not in q["answered"]:
                q["answered"].append("scenarios")
    except (QuestionnaireError, OSError, ValueError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    path = save(data_dir, q)
    print("questionnaire: %s (answered: %s)" % (path, ", ".join(q["answered"]) or "-"))
    if args.show:
        print(json.dumps(q, ensure_ascii=False, indent=2))
    if args.no_compute:
        return 0
    import compute  # noqa: E402 (late: compute imports this module)

    return compute.main([data_dir, "--lang", lang])


if __name__ == "__main__":
    sys.exit(main())
