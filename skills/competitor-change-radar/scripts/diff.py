#!/usr/bin/env python3
"""Compare two normalized snapshots (radar/snapshot/1) -> radar/diff/1.

Pure and offline: only reads two ``snapshot.json`` dicts. Same input gives
byte-identical output (sorted events, sorted keys, two-decimal numbers, no
system clock). The rules and thresholds are documented in
references/method.md; the output contract in references/data-contract.md.

    events      one entry per observed change (variant level for prices)
    counts      events per type (virtual add-ons such as shipping protection
                are kept in events but excluded from counts)
    price_changes  product-level summary (lowest variant price)
    comparable  which dimensions could be compared at all

Library use: ``compute_diff(a, b, a_ref, b_ref)``; ``baseline(snap, ref)`` for
a single snapshot. CLI (debugging): python diff.py <old snapshot> <new snapshot>
Python 3.8+, standard library only.
"""

import datetime
import decimal
import difflib
import json
import os
import re
import sys

SCHEMA = "radar/diff/1"
BASELINE_SCHEMA = "radar/baseline/1"
TOOL_VERSION = "0.1.0"

DEFAULT_THRESHOLDS = {
    "price_summary_pct": 5.0,  # product-level change shown in the summary
    "discount_pp": 5.0,  # discount depth change, percentage points
    "mass_removal_ratio": 0.30,  # removed share that looks like missing data
    "mass_added_ratio": 0.50,  # added share when the old side was incomplete
    "homepage_text_lines": 5,  # non-promo homepage lines before one summary event
    "interval_short_hours": 20.0,
    "interval_long_days": 45.0,
    "sitewide_share": 0.50,  # share of comparable products moving together
    "sitewide_spread_pp": 0.50,  # max spread of their change, percentage points
    "policy_ignore_similarity": 0.98,
    "rewrite_low": 0.60,
    "rewrite_high": 0.98,
}

SEVERITIES = ("high", "medium", "low")
TYPE_ORDER = [
    "store_status_changed",
    "currency_changed",
    "price_sitewide_shift",
    "product_added",
    "product_removed",
    "price_down",
    "price_up",
    "promo_started",
    "promo_ended",
    "discount_deeper",
    "discount_shallower",
    "compare_at_raised",
    "sold_out",
    "restocked",
    "variant_added",
    "variant_removed",
    "policy_changed",
    "policy_added",
    "policy_removed",
    "theme_changed",
    "theme_updated",
    "app_signal_added",
    "app_signal_removed",
    "tracking_added",
    "tracking_removed",
    "markets_changed",
    "homepage_promo_changed",
    "homepage_text_changed",
    "collection_added",
    "collection_removed",
    "collection_renamed",
    "product_republished",
    "product_handle_changed",
    "product_relisted",
    "product_surfaced",
    "product_missing_unconfirmed",
]
TYPE_INDEX = {t: i for i, t in enumerate(TYPE_ORDER)}
SEVERITY = {
    "store_status_changed": "high",
    "currency_changed": "high",
    "price_sitewide_shift": "high",
    "product_added": "high",
    "product_removed": "high",
    "promo_started": "high",
    "promo_ended": "high",
    "theme_changed": "high",
    "price_down": "medium",
    "price_up": "medium",
    "policy_changed": "medium",
    "discount_deeper": "medium",
    "discount_shallower": "medium",
    "compare_at_raised": "medium",
    "sold_out": "medium",
    "restocked": "medium",
    "variant_added": "medium",
    "variant_removed": "medium",
    "policy_added": "medium",
    "policy_removed": "medium",
    "app_signal_added": "medium",
    "app_signal_removed": "medium",
    "tracking_added": "medium",
    "tracking_removed": "medium",
    "markets_changed": "medium",
    "homepage_promo_changed": "medium",
    "collection_added": "medium",
    "collection_removed": "medium",
    "collection_renamed": "low",
    "theme_updated": "low",
    "homepage_text_changed": "low",
    "product_republished": "low",
    "product_handle_changed": "low",
    "product_relisted": "low",
    "product_surfaced": "low",
    "product_missing_unconfirmed": "low",
}
PRODUCT_EVENT_TYPES = {
    "product_added",
    "product_removed",
    "product_surfaced",
    "product_missing_unconfirmed",
    "product_republished",
    "product_handle_changed",
    "product_relisted",
    "price_down",
    "price_up",
    "promo_started",
    "promo_ended",
    "discount_deeper",
    "discount_shallower",
    "compare_at_raised",
    "sold_out",
    "restocked",
    "variant_added",
    "variant_removed",
}
BAD_STATUSES = ("blocked", "not_shopify")
LIVE_SOURCES = ("live", "teardown_import")
D = decimal.Decimal


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def parse_time(value):
    """Aware datetime from an ISO string (Z or offset), date-only allowed."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def dec(value):
    if value is None:
        return None
    try:
        return D(str(value))
    except decimal.InvalidOperation:
        return None


def r2(x):
    return None if x is None else round(float(x), 2)


def pct_change(before, after):
    b, a = dec(before), dec(after)
    if b is None or a is None or b == 0:
        return None
    return r2((a - b) / b * 100)


def depth_pct(price, compare_at):
    """Discount depth in percent (0-100) when compare_at > price, else None."""
    p, c = dec(price), dec(compare_at)
    if p is None or c is None or c <= 0 or c <= p:
        return None
    return r2((1 - p / c) * 100)


def id_key(value):
    """Sort key: numeric ids by value, others after them by text."""
    text = "" if value is None else str(value)
    return (0, int(text), "") if text.isdigit() else (1, 0, text)


def norm_title(text):
    return re.sub(r"[^\w]+", " ", (text or "").lower()).strip()


def variant_label(v):
    opts = v.get("options") or []
    if opts:
        return " / ".join(opts)  # "Default Title" is dropped by normalize
    title = v.get("title") or ""
    return None if title in ("", "Default Title") else title


def lowest(product):
    vals = [dec(v.get("price")) for v in product["variants"].values()]
    vals = [x for x in vals if x is not None]
    return min(vals) if vals else None


def on_sale_depth(product):
    """Max discount depth over variants (None = not on sale)."""
    depths = [
        depth_pct(v.get("price"), v.get("compare_at_price"))
        for v in product["variants"].values()
    ]
    depths = [d for d in depths if d is not None]
    return max(depths) if depths else None


def availability(product):
    flags = [v.get("available") for v in product["variants"].values()]
    flags = [f for f in flags if isinstance(f, bool)]
    if not flags:
        return None
    return any(flags)


def money_str(d):
    return None if d is None else str(d.quantize(D("0.01")))


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------


def match_products(pa, pb, warnings):
    """Return (pairs [(ka, kb, how)], only_a [ka], only_b [kb])."""
    pairs = []
    rest_a = set(pa)
    rest_b = set(pb)
    for k in sorted(set(pa) & set(pb), key=id_key):
        if not k.startswith("handle:"):
            pairs.append((k, k, "id"))
            rest_a.discard(k)
            rest_b.discard(k)
    # handle (archive / manual data without ids, or deleted and recreated)
    ha = {}
    for k in rest_a:
        ha.setdefault(pa[k]["handle"], []).append(k)
    hb = {}
    for k in rest_b:
        hb.setdefault(pb[k]["handle"], []).append(k)
    for h in sorted(set(ha) & set(hb)):
        if h and len(ha[h]) == 1 and len(hb[h]) == 1:
            ka, kb = ha[h][0], hb[h][0]
            both_ids = not ka.startswith("handle:") and not kb.startswith("handle:")
            pairs.append((ka, kb, "relisted" if both_ids else "handle"))
            rest_a.discard(ka)
            rest_b.discard(kb)
    # vendor + title, only when one side has no ids at all
    no_ids = any(k.startswith("handle:") for k in rest_a | rest_b)
    if no_ids and rest_a and rest_b:
        ta, tb = {}, {}
        for k in rest_a:
            ta.setdefault(
                (pa[k]["vendor"].lower(), norm_title(pa[k]["title"])), []
            ).append(k)
        for k in rest_b:
            tb.setdefault(
                (pb[k]["vendor"].lower(), norm_title(pb[k]["title"])), []
            ).append(k)
        for key in sorted(set(ta) & set(tb)):
            if len(ta[key]) == 1 and len(tb[key]) == 1:
                pairs.append((ta[key][0], tb[key][0], "title"))
                rest_a.discard(ta[key][0])
                rest_b.discard(tb[key][0])
            elif "ambiguous_identity" not in warnings:
                warnings.append("ambiguous_identity")
    pairs.sort(key=lambda x: id_key(x[1]))
    return pairs, sorted(rest_a, key=id_key), sorted(rest_b, key=id_key)


def match_variants(va, vb):
    """Return (pairs [(ka, kb)], only_a, only_b) by id, then sku, then options."""
    pairs = []
    rest_a, rest_b = set(va), set(vb)
    for k in sorted(set(va) & set(vb), key=id_key):
        if not k.startswith("idx-"):
            pairs.append((k, k))
            rest_a.discard(k)
            rest_b.discard(k)

    def by(field_fn, rest, side):
        out = {}
        for k in rest:
            key = field_fn(side[k])
            if key:
                out.setdefault(key, []).append(k)
        return out

    for fn in (lambda v: v.get("sku") or None, lambda v: tuple(v.get("options") or [])):
        ma, mb = by(fn, rest_a, va), by(fn, rest_b, vb)
        for key in sorted(set(ma) & set(mb), key=str):
            if len(ma[key]) == 1 and len(mb[key]) == 1:
                pairs.append((ma[key][0], mb[key][0]))
                rest_a.discard(ma[key][0])
                rest_b.discard(mb[key][0])
    pairs.sort(key=lambda x: id_key(x[1]))
    return pairs, sorted(rest_a, key=id_key), sorted(rest_b, key=id_key)


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------


class Diff(object):
    def __init__(self, a, b, a_ref, b_ref, thresholds=None):
        self.a, self.b = a, b
        self.a_ref, self.b_ref = a_ref, b_ref
        self.t = dict(DEFAULT_THRESHOLDS)
        self.t.update(thresholds or {})
        self.events = []
        self.warnings = []
        self.errors = []
        self.archive = "archive" in (a.get("source"), b.get("source"))
        self.domain = b.get("domain") or a.get("domain")
        self.currency = (b.get("shop") or {}).get("currency") or (
            a.get("shop") or {}
        ).get("currency")
        self.comparable = {
            "products": False,
            "prices": False,
            "removals": False,
            "policies": [],
            "homepage": False,
            "collections": False,
            "markets": False,
        }
        self.price_changes = []
        self.homepage_detail = {}
        self.currency_changed = False
        self.interval_days = None
        self.t_from = None

    # -- event helpers -----------------------------------------------------

    def badge(self, kind):
        """kind: data (public product data) | page | policy."""
        if kind == "policy":
            return "store_claim"
        if kind == "page" or self.archive:
            return "page_shown"
        both_live = (
            self.a.get("source") in LIVE_SOURCES
            and self.b.get("source") in LIVE_SOURCES
        )
        return "verified" if both_live else "page_shown"

    def locator(self, ref, fname):
        if not ref:
            return None
        return "%s/%s/raw/%s" % (ref["rel"], ref["raw"], fname)

    def url(self, path=""):
        return "https://%s%s" % (self.domain, path)

    def add(self, etype, kind, fname, **fields):
        ev = {
            "type": etype,
            "severity": fields.pop("severity", None) or SEVERITY[etype],
            "product_id": None,
            "handle": None,
            "title": None,
            "variant_id": None,
            "variant": None,
            "field": None,
            "before": None,
            "after": None,
            "delta_pct": None,
            "currency": None,
            "virtual": False,
            "badge": self.badge(kind),
        }
        path = fields.pop("path", "")
        ev.update(fields)
        ev["evidence"] = {
            "from_locator": self.locator(self.a_ref, fname),
            "to_locator": self.locator(self.b_ref, fname),
            "url": self.url(path),
        }
        self.events.append(ev)
        return ev

    def product_fields(self, key, p):
        return {
            "product_id": None if key.startswith("handle:") else key,
            "handle": p["handle"],
            "title": p["title"],
            "virtual": bool(p.get("virtual")),
            "path": "/products/%s" % p["handle"] if p["handle"] else "",
        }

    # -- run -----------------------------------------------------------------

    def run(self):
        a, b = self.a, self.b
        ta, tb = (
            parse_time(a.get("captured_at_utc")),
            parse_time(b.get("captured_at_utc")),
        )
        self.interval_days = None
        if ta and tb:
            self.interval_days = r2((tb - ta).total_seconds() / 86400.0)
            hours = (tb - ta).total_seconds() / 3600.0
            if hours < 0:
                self.warnings.append("snapshots_out_of_order")
            elif hours < self.t["interval_short_hours"]:
                self.warnings.append("interval_short")
            elif self.interval_days > self.t["interval_long_days"]:
                self.warnings.append("interval_long")
        self.t_from = ta
        if self.archive:
            self.warnings.append("archive_baseline")
        sa, sb = a.get("status"), b.get("status")
        if sa != sb and (sa in BAD_STATUSES or sb in BAD_STATUSES):
            self.add(
                "store_status_changed",
                "page",
                "homepage.html",
                field="status",
                before=sa,
                after=sb,
            )
        if sa in BAD_STATUSES or sb in BAD_STATUSES:
            return self
        self.diff_markets()
        self.diff_products()
        self.diff_collections()
        self.diff_homepage()
        self.diff_tech()
        self.diff_policies()
        return self

    # -- products ------------------------------------------------------------

    def diff_products(self):
        a, b = self.a, self.b
        pa, pb = a.get("products") or {}, b.get("products") or {}
        ca, cb = a.get("coverage") or {}, b.get("coverage") or {}
        if ca.get("products") == "none" or cb.get("products") == "none":
            return
        self.comparable["products"] = True
        prices_ok = not self.currency_changed
        self.comparable["prices"] = prices_ok
        removals_ok = (
            cb.get("pagination_complete")
            and cb.get("products") == "full"
            and not self.archive
        )
        self.comparable["removals"] = bool(removals_ok)
        pairs, only_a, only_b = match_products(pa, pb, self.warnings)
        for kb in only_b:
            p = pb[kb]
            # New to the public when it was published after the old capture;
            # published earlier means the old snapshot just did not see it.
            when = parse_time(p.get("published_at")) or parse_time(p.get("created_at"))
            new = when is not None and self.t_from is not None and when >= self.t_from
            self.add(
                "product_added" if new else "product_surfaced",
                "data",
                "products-*.json",
                before=None,
                after=money_str(lowest(p)),
                currency=self.currency,
                details={
                    "published_at": p.get("published_at"),
                    "created_at": p.get("created_at"),
                    "product_type": p.get("product_type"),
                },
                **self.product_fields(kb, p),
            )
        removed = []
        for ka in only_a:
            p = pa[ka]
            ev = self.add(
                "product_removed" if removals_ok else "product_missing_unconfirmed",
                "data",
                "products-*.json",
                before=money_str(lowest(p)),
                currency=self.currency,
                details={"product_type": p.get("product_type")},
                **self.product_fields(ka, p),
            )
            if removals_ok:
                removed.append(ev)
        real_a = sum(1 for p in pa.values() if not p.get("virtual")) or len(pa)
        real_removed = [e for e in removed if not e["virtual"]]
        if real_a and len(real_removed) > self.t["mass_removal_ratio"] * real_a:
            self.warnings.append("mass_removal_suspect")
            for ev in removed:
                ev["type"] = "product_missing_unconfirmed"
                ev["severity"] = SEVERITY["product_missing_unconfirmed"]
        added = [
            e for e in self.events if e["type"] in ("product_added", "product_surfaced")
        ]
        if (
            pb
            and len(added) > self.t["mass_added_ratio"] * len(pb)
            and not ca.get("pagination_complete")
        ):
            self.warnings.append("baseline_incomplete")
        for ka, kb, how in pairs:
            self.diff_pair(ka, kb, how, pa[ka], pb[kb], prices_ok)
        if prices_ok:
            self.sitewide()

    def diff_pair(self, ka, kb, how, p1, p2, prices_ok):
        pf = self.product_fields(kb, p2)
        if how == "relisted":
            self.add(
                "product_relisted",
                "data",
                "products-*.json",
                field="product_id",
                before=ka,
                after=kb,
                **pf,
            )
        if p1["handle"] and p2["handle"] and p1["handle"] != p2["handle"]:
            self.add(
                "product_handle_changed",
                "data",
                "products-*.json",
                field="handle",
                before=p1["handle"],
                after=p2["handle"],
                **pf,
            )
        t1, t2 = parse_time(p1.get("published_at")), parse_time(p2.get("published_at"))
        if t1 and t2 and t2 > t1:
            self.add(
                "product_republished",
                "data",
                "products-*.json",
                field="published_at",
                before=p1.get("published_at"),
                after=p2.get("published_at"),
                **pf,
            )
        vpairs, only_a, only_b = match_variants(p1["variants"], p2["variants"])
        for k in only_b:
            v = p2["variants"][k]
            self.add(
                "variant_added",
                "data",
                "products-*.json",
                variant_id=None if k.startswith("idx-") else k,
                variant=variant_label(v),
                after=v.get("price"),
                currency=self.currency,
                **pf,
            )
        for k in only_a:
            v = p1["variants"][k]
            self.add(
                "variant_removed",
                "data",
                "products-*.json",
                variant_id=None if k.startswith("idx-") else k,
                variant=variant_label(v),
                before=v.get("price"),
                currency=self.currency,
                **pf,
            )
        av1, av2 = availability(p1), availability(p2)
        if av1 is True and av2 is False:
            self.add(
                "sold_out",
                "data",
                "products-*.json",
                before="available",
                after="sold_out",
                **pf,
            )
        elif av1 is False and av2 is True:
            self.add(
                "restocked",
                "data",
                "products-*.json",
                before="sold_out",
                after="available",
                **pf,
            )
        if not prices_ok:
            return
        changed = False
        raised = False
        for k1, k2 in vpairs:
            v1, v2 = p1["variants"][k1], p2["variants"][k2]
            x1, x2 = dec(v1.get("price")), dec(v2.get("price"))
            vid = None if k2.startswith("idx-") else k2
            if x1 is not None and x2 is not None and x1 != x2:
                changed = True
                self.add(
                    "price_down" if x2 < x1 else "price_up",
                    "data",
                    "products-*.json",
                    variant_id=vid,
                    variant=variant_label(v2),
                    before=v1.get("price"),
                    after=v2.get("price"),
                    delta_pct=pct_change(x1, x2),
                    severity=(
                        "high"
                        if abs(pct_change(x1, x2) or 0) >= self.t["price_summary_pct"]
                        else "medium"
                    ),
                    currency=self.currency,
                    details={
                        "sku": v2.get("sku") or None,
                        "compare_at_before": v1.get("compare_at_price"),
                        "compare_at_after": v2.get("compare_at_price"),
                        "discount_before": depth_pct(x1, v1.get("compare_at_price")),
                        "discount_after": depth_pct(x2, v2.get("compare_at_price")),
                    },
                    **pf,
                )
            c1, c2 = dec(v1.get("compare_at_price")), dec(v2.get("compare_at_price"))
            if x1 == x2 and c1 is not None and c2 is not None and c2 > c1:
                raised = True
        d1, d2 = on_sale_depth(p1), on_sale_depth(p2)
        if d1 is None and d2 is not None:
            self.add("promo_started", "data", "products-*.json", after=d2, **pf)
        elif d1 is not None and d2 is None:
            self.add("promo_ended", "data", "products-*.json", before=d1, **pf)
        elif (
            d1 is not None and d2 is not None and abs(d2 - d1) >= self.t["discount_pp"]
        ):
            self.add(
                "discount_deeper" if d2 > d1 else "discount_shallower",
                "data",
                "products-*.json",
                before=d1,
                after=d2,
                **pf,
            )
        if raised:
            self.add("compare_at_raised", "data", "products-*.json", **pf)
        l1, l2 = lowest(p1), lowest(p2)
        if changed and l1 is not None and l2 is not None:
            self.price_changes.append(
                {
                    "product_id": pf["product_id"],
                    "handle": p2["handle"],
                    "title": p2["title"],
                    "virtual": pf["virtual"],
                    "before": money_str(l1),
                    "after": money_str(l2),
                    "delta_pct": pct_change(l1, l2) if l1 != l2 else 0.0,
                    "on_sale_before": d1 is not None,
                    "on_sale_after": d2 is not None,
                    "discount_after": d2,
                    "currency": self.currency,
                }
            )

    def sitewide(self):
        """Merge a store-wide price move into one event (variant events stay)."""
        pa, pb = self.a["products"], self.b["products"]
        moves = []
        for k in pb:
            if k in pa and not pb[k].get("virtual"):
                l1, l2 = lowest(pa[k]), lowest(pb[k])
                if l1 is not None and l2 is not None and l1 > 0:
                    moves.append(float((l2 - l1) / l1 * 100))
        if not moves:
            return
        for sign in (-1, 1):
            same = sorted(m for m in moves if m * sign > 0)
            if len(same) >= self.t["sitewide_share"] * len(moves) and len(same) >= 2:
                if same[-1] - same[0] <= self.t["sitewide_spread_pp"]:
                    mid = same[len(same) // 2]
                    self.add(
                        "price_sitewide_shift",
                        "data",
                        "products-*.json",
                        delta_pct=r2(mid),
                        currency=self.currency,
                        details={"products": len(same), "comparable": len(moves)},
                    )
                    for ev in self.events:
                        if ev["type"] in ("price_down", "price_up"):
                            ev["sitewide"] = True
                    return

    # -- markets / currency ---------------------------------------------------

    def diff_markets(self):
        sa, sb = self.a.get("shop") or {}, self.b.get("shop") or {}
        self.currency_changed = False
        c1, c2 = sa.get("currency"), sb.get("currency")
        if c1 and c2 and c1 != c2:
            if sa.get("currency_source") == sb.get("currency_source") == "meta":
                self.currency_changed = True
                self.add(
                    "currency_changed",
                    "data",
                    "meta.json",
                    field="currency",
                    before=c1,
                    after=c2,
                )
                self.warnings.append("prices_skipped_currency_changed")
            else:
                self.warnings.append("currency_source_mismatch")
        n1, n2 = sa.get("ships_to_count"), sb.get("ships_to_count")
        if isinstance(n1, int) and isinstance(n2, int):
            self.comparable["markets"] = True
            if n1 != n2:
                self.add(
                    "markets_changed",
                    "data",
                    "meta.json",
                    field="ships_to_count",
                    before=n1,
                    after=n2,
                )
        if not (
            self.a["coverage"].get("homepage") and self.b["coverage"].get("homepage")
        ):
            return
        self.comparable["markets"] = True
        g1, g2 = self.a.get("signals") or {}, self.b.get("signals") or {}
        h1 = sorted({h[0] for h in g1.get("hreflang") or []})
        h2 = sorted({h[0] for h in g2.get("hreflang") or []})
        if h1 != h2:
            self.add(
                "markets_changed",
                "page",
                "homepage.html",
                field="hreflang",
                before=", ".join(h1) or None,
                after=", ".join(h2) or None,
                details={
                    "added": sorted(set(h2) - set(h1)),
                    "removed": sorted(set(h1) - set(h2)),
                },
            )
        l1, l2 = g1.get("localization_form"), g2.get("localization_form")
        if l1 and l2 and l1 != l2:
            self.add(
                "markets_changed",
                "page",
                "homepage.html",
                field="localization_form",
                before=l1,
                after=l2,
            )

    # -- collections, homepage, tech ----------------------------------------

    def diff_collections(self):
        if not (
            self.a["coverage"].get("collections")
            and self.b["coverage"].get("collections")
        ):
            return
        self.comparable["collections"] = True
        c1, c2 = self.a.get("collections") or {}, self.b.get("collections") or {}
        for h in sorted(set(c2) - set(c1)):
            self.add(
                "collection_added",
                "data",
                "collections-*.json",
                field=h,
                after=c2[h]["title"],
                path="/collections/%s" % h,
            )
        for h in sorted(set(c1) - set(c2)):
            self.add(
                "collection_removed",
                "data",
                "collections-*.json",
                field=h,
                before=c1[h]["title"],
            )
        for h in sorted(set(c1) & set(c2)):
            if c1[h]["title"] != c2[h]["title"]:
                self.add(
                    "collection_renamed",
                    "data",
                    "collections-*.json",
                    field=h,
                    before=c1[h]["title"],
                    after=c2[h]["title"],
                    path="/collections/%s" % h,
                )

    def diff_homepage(self):
        if not (
            self.a["coverage"].get("homepage") and self.b["coverage"].get("homepage")
        ):
            return
        self.comparable["homepage"] = True
        h1, h2 = self.a.get("homepage") or {}, self.b.get("homepage") or {}
        p1, p2 = set(h1.get("promo_lines") or []), set(h2.get("promo_lines") or [])
        if p1 != p2:
            added, removed = sorted(p2 - p1), sorted(p1 - p2)
            self.add(
                "homepage_promo_changed",
                "page",
                "homepage.html",
                before=" | ".join(removed) or None,
                after=" | ".join(added) or None,
                details={"added": added, "removed": removed},
            )
        o1, o2 = set(h1.get("lines") or []), set(h2.get("lines") or [])
        added, removed = sorted(o2 - o1), sorted(o1 - o2)
        self.homepage_detail = {"added": added, "removed": removed}
        if len(added) + len(removed) >= self.t["homepage_text_lines"]:
            self.add(
                "homepage_text_changed",
                "page",
                "homepage.html",
                before=len(removed),
                after=len(added),
                details={"added": added[:30], "removed": removed[:30]},
            )

    def diff_tech(self):
        if not (
            self.a["coverage"].get("homepage") and self.b["coverage"].get("homepage")
        ):
            return
        t1, t2 = self.a.get("theme") or {}, self.b.get("theme") or {}
        keys = ("name", "schema_name", "schema_version", "theme_store_id")
        if any(t1.get(k) for k in keys) and any(t2.get(k) for k in keys):
            diff = [k for k in keys if t1.get(k) != t2.get(k)]
            if diff == ["schema_version"]:
                self.add(
                    "theme_updated",
                    "page",
                    "homepage.html",
                    field="schema_version",
                    before=t1.get("schema_version"),
                    after=t2.get("schema_version"),
                    title=t2.get("schema_name") or t2.get("name"),
                )
            elif diff:
                self.add(
                    "theme_changed",
                    "page",
                    "homepage.html",
                    field=",".join(diff),
                    before=" ".join(
                        str(t1.get(k) or "") for k in ("schema_name", "schema_version")
                    ).strip()
                    or t1.get("name"),
                    after=" ".join(
                        str(t2.get(k) or "") for k in ("schema_name", "schema_version")
                    ).strip()
                    or t2.get("name"),
                    details={
                        "before": {k: t1.get(k) for k in keys},
                        "after": {k: t2.get(k) for k in keys},
                    },
                )
        g1, g2 = self.a.get("signals") or {}, self.b.get("signals") or {}
        for key, add_t, rem_t in (
            ("apps_possible", "app_signal_added", "app_signal_removed"),
            ("tracking_tags", "tracking_added", "tracking_removed"),
        ):
            s1, s2 = set(g1.get(key) or []), set(g2.get(key) or [])
            for name in sorted(s2 - s1):
                self.add(add_t, "page", "homepage.html", field=name, after=name)
            for name in sorted(s1 - s2):
                self.add(rem_t, "page", "homepage.html", field=name, before=name)

    # -- policies ------------------------------------------------------------

    def diff_policies(self):
        pa, pb = self.a.get("policies") or {}, self.b.get("policies") or {}
        ca = (self.a.get("coverage") or {}).get("policies_checked") or {}
        cb = (self.b.get("coverage") or {}).get("policies_checked") or {}
        names = sorted(set(pa) | set(pb) | set(ca) | set(cb))
        for name in names:
            fname = "policy-%s.html" % name
            path = "/policies/%s" % name
            if name in pa and name in pb:
                self.comparable["policies"].append(name)
                self.diff_policy(name, pa[name], pb[name], fname, path)
            elif name in pb and ca.get(name) == "not_found":
                self.comparable["policies"].append(name)
                self.add("policy_added", "policy", fname, field=name, path=path)
            elif name in pa and cb.get(name) == "not_found":
                self.comparable["policies"].append(name)
                self.add("policy_removed", "policy", fname, field=name, path=path)

    def diff_policy(self, name, p1, p2, fname, path):
        if p1.get("text_sha256") == p2.get("text_sha256"):
            return
        k1, k2 = p1.get("key_terms") or {}, p2.get("key_terms") or {}
        term_changes = 0
        for term in sorted(set(k1) | set(k2)):
            if k1.get(term) != k2.get(term):
                term_changes += 1
                self.add(
                    "policy_changed",
                    "policy",
                    fname,
                    severity="high",
                    title=name,
                    field=term,
                    before=k1.get(term),
                    after=k2.get(term),
                    path=path,
                )
        s1, s2 = p1.get("sentences") or [], p2.get("sentences") or []
        sm = difflib.SequenceMatcher(None, s1, s2, autojunk=False)
        added, removed, rewritten = [], [], []
        changed_chars = 0
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                continue
            old, new = list(s1[i1:i2]), list(s2[j1:j2])
            changed_chars += max(sum(len(x) for x in old), sum(len(x) for x in new))
            if op == "replace":
                used = set()
                for o in old:
                    best, best_r = None, 0.0
                    for j, n in enumerate(new):
                        if j in used:
                            continue
                        r = difflib.SequenceMatcher(None, o, n, autojunk=False).ratio()
                        if r > best_r:
                            best, best_r = j, r
                    if best is not None and self.t["rewrite_low"] <= best_r < 1.0:
                        used.add(best)
                        rewritten.append([o, new[best]])
                    else:
                        removed.append(o)
                added.extend(n for j, n in enumerate(new) if j not in used)
            else:
                removed.extend(old)
                added.extend(new)
        total = max(sum(len(x) for x in s1), sum(len(x) for x in s2), 1)
        similarity = r2(max(0.0, 1.0 - changed_chars / float(total)))
        if similarity >= self.t["policy_ignore_similarity"] and not term_changes:
            return
        if not (added or removed or rewritten):
            return
        self.add(
            "policy_changed",
            "policy",
            fname,
            severity="low" if term_changes else "medium",
            title=name,
            field="text",
            before=len(removed) + len(rewritten),
            after=len(added) + len(rewritten),
            path=path,
            details={
                "similarity": similarity,
                "added": added,
                "removed": removed,
                "rewritten": rewritten,
            },
        )

    # -- result --------------------------------------------------------------

    def result(self):
        events = sorted(self.events, key=event_key)
        for i, ev in enumerate(events, 1):
            ev["event_id"] = "R%03d" % i
        counts = dict((t, 0) for t in TYPE_ORDER)
        for ev in events:
            if not ev.get("virtual"):
                counts[ev["type"]] += 1
        real_pc = [p for p in self.price_changes if not p["virtual"]]
        counts["products_price_down"] = sum(
            1 for p in real_pc if (p["delta_pct"] or 0) < 0
        )
        counts["products_price_up"] = sum(
            1 for p in real_pc if (p["delta_pct"] or 0) > 0
        )
        ids = {}
        for ev in events:
            if (
                ev["type"] in ("price_down", "price_up")
                and ev["product_id"] is not None
            ):
                ids.setdefault(ev["product_id"], ev["event_id"])
        for p in self.price_changes:
            p["event_id"] = ids.get(p["product_id"])
        sa, sb = self.a.get("status"), self.b.get("status")
        if sa in BAD_STATUSES and sb in BAD_STATUSES:
            status = "failed"
        elif (
            sa == "complete"
            and sb == "complete"
            and self.comparable["products"]
            and not self.archive
            and not {"mass_removal_suspect", "baseline_incomplete"} & set(self.warnings)
        ):
            status = "complete"
        else:
            status = "partial"
        return {
            "schema": SCHEMA,
            "tool_version": TOOL_VERSION,
            "domain": self.domain,
            "shop_name": (self.b.get("shop") or {}).get("name")
            or (self.a.get("shop") or {}).get("name"),
            "currency": self.currency,
            "from": snap_ref(self.a),
            "to": snap_ref(self.b),
            "interval_days": self.interval_days,
            "status": status,
            "comparable": self.comparable,
            "counts": counts,
            "cadence": cadence(self.a, self.b, events, self.interval_days),
            "promo_share": {
                "before": on_sale_share(self.a),
                "after": on_sale_share(self.b),
            },
            "price_changes": sorted(
                self.price_changes,
                key=lambda p: (-abs(p["delta_pct"] or 0), id_key(p["product_id"])),
            ),
            "homepage_detail": self.homepage_detail,
            "thresholds": self.t,
            "events": events,
            "warnings": sorted(set(self.warnings), key=self.warnings.index),
            "errors": self.errors,
        }


def event_key(ev):
    return (
        SEVERITIES.index(ev["severity"]),
        TYPE_INDEX[ev["type"]],
        id_key(ev.get("product_id")),
        id_key(ev.get("variant_id")),
        str(ev.get("field") or ""),
        str(ev.get("handle") or ""),
        str(ev.get("before")),
        str(ev.get("after")),
    )


def snap_ref(s):
    return {
        "snapshot": s.get("snapshot_id"),
        "captured_at_utc": s.get("captured_at_utc"),
        "source": s.get("source"),
        "status": s.get("status"),
        "products": len(s.get("products") or {}),
        "pagination_complete": (s.get("coverage") or {}).get("pagination_complete"),
        "archive_timestamps": s.get("archive_timestamps"),
        "fetch_error": s.get("fetch_error"),
    }


def real_products(s):
    return {k: p for k, p in (s.get("products") or {}).items() if not p.get("virtual")}


def on_sale_share(s):
    prods = real_products(s)
    if not prods:
        return None
    return r2(
        100.0
        * sum(1 for p in prods.values() if on_sale_depth(p) is not None)
        / len(prods)
    )


def month_add(ym, delta):
    y, m = int(ym[:4]), int(ym[5:7])
    m += delta
    while m <= 0:
        m += 12
        y -= 1
    while m > 12:
        m -= 12
        y += 1
    return "%04d-%02d" % (y, m)


def published_by_month(s, months=6, ref_month=None):
    ref = ref_month or (s.get("captured_at_utc") or "")[:7]
    if not ref:
        return {}
    keys = [month_add(ref, -i) for i in range(months - 1, -1, -1)]
    out = dict((k, 0) for k in keys)
    for p in real_products(s).values():
        ym = (p.get("published_at") or "")[:7]
        if ym in out:
            out[ym] += 1
    return out


def iso_week(value):
    t = parse_time(value)
    if t is None:
        return None
    y, w, _d = t.isocalendar()
    return "%04d-W%02d" % (y, w)


def cadence(a, b, events, interval_days):
    added = [e for e in events if e["type"] == "product_added" and not e["virtual"]]
    by_week = {}
    for e in added:
        wk = iso_week((e.get("details") or {}).get("published_at")) or iso_week(
            (e.get("details") or {}).get("created_at")
        )
        if wk:
            by_week[wk] = by_week.get(wk, 0) + 1
    per_week = None
    if interval_days and interval_days >= 1:
        per_week = r2(len(added) / interval_days * 7)
    ref = (b.get("captured_at_utc") or "")[:7]
    return {
        "added": len(added),
        "added_per_week": per_week,
        "published_by_week": dict(sorted(by_week.items())),
        "published_by_month": {
            "before": published_by_month(a, ref_month=ref),
            "after": published_by_month(b, ref_month=ref),
        },
    }


def compute_diff(a, b, a_ref=None, b_ref=None, thresholds=None):
    return Diff(a, b, a_ref, b_ref, thresholds).run().result()


# ---------------------------------------------------------------------------
# single snapshot (baseline report)
# ---------------------------------------------------------------------------


def baseline(s, ref=None):
    """Facts one snapshot can give on its own (not changes)."""
    now = parse_time(s.get("captured_at_utc"))
    prods = real_products(s)
    recent = {"30": 0, "90": 0}
    rows = []
    domain = s.get("domain")
    for k, p in sorted(prods.items(), key=lambda kv: id_key(kv[0])):
        pub = parse_time(p.get("published_at"))
        if pub and now:
            age = (now - pub).total_seconds() / 86400.0
            if 0 <= age <= 30:
                recent["30"] += 1
            if 0 <= age <= 90:
                recent["90"] += 1
        low = lowest(p)
        cas = [dec(v.get("compare_at_price")) for v in p["variants"].values()]
        cas = [c for c in cas if c is not None]
        rows.append(
            {
                "product_id": None if k.startswith("handle:") else k,
                "title": p["title"],
                "product_type": p.get("product_type"),
                "price": money_str(low),
                "compare_at": money_str(max(cas)) if cas else None,
                "on_sale": on_sale_depth(p) is not None,
                "available": availability(p),
                "published": (p.get("published_at") or "")[:10] or None,
                "url": "https://%s/products/%s" % (domain, p["handle"])
                if p["handle"]
                else None,
            }
        )
    rows.sort(key=lambda r: (r["published"] or "", r["title"]), reverse=True)
    return {
        "schema": BASELINE_SCHEMA,
        "tool_version": TOOL_VERSION,
        "domain": domain,
        "shop_name": (s.get("shop") or {}).get("name"),
        "currency": (s.get("shop") or {}).get("currency"),
        "snapshot": snap_ref(s),
        "status": "failed" if s.get("status") in BAD_STATUSES else "partial",
        "identity_status": s.get("identity_status"),
        "products": len(prods),
        "virtual_products": len(s.get("products") or {}) - len(prods),
        "published_recent": recent,
        "published_by_month": published_by_month(s),
        "on_sale_share": on_sale_share(s),
        "promo_lines": (s.get("homepage") or {}).get("promo_lines") or [],
        "coverage": s.get("coverage"),
        "theme": s.get("theme"),
        "signals": s.get("signals"),
        "shop": s.get("shop"),
        "policies": {
            n: (v.get("key_terms") or {})
            for n, v in sorted((s.get("policies") or {}).items())
        },
        "rows": rows,
    }


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(
            "usage: python diff.py <old snapshot dir> <new snapshot dir>",
            file=sys.stderr,
        )
        return 2
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import normalize

    a, b = (normalize.load_snapshot(p) for p in argv)
    sys.stdout.write(dumps(compute_diff(a, b)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
