"""Public product data helpers: sampling (entry / main / premium, same idea
as the teardown L1 samples), test-product and subscription detection.

Input is the Shopify ``/products.json`` list, or rows from a product export
CSV converted by paste_ingest.py into the same shape.
Python 3.8+, standard library only.
"""

import re

NON_PRODUCT_RE = re.compile(
    r"protect|insurance|guarantee|gift[\s-]*card|e-?gift|warranty|shipping|"
    r"\broute\b|donation|\btips?\b",
    re.I,
)
TEST_RE = re.compile(
    r"\b(test|testing|demo|sample product|dummy|do not buy)\b|测试|样品",
    re.I,
)
SUBSCRIPTION_RE = re.compile(
    r"subscri|recurring|auto[- ]?renew|every \d+ (day|week|month)s?|"
    r"订阅|自动续费",
    re.I,
)
# Stricter on page text: "subscribe to our newsletter" must not count.
PAGE_SUB_RE = re.compile(
    r"subscribe (?:&|and) save|subscription plan|auto[- ]?renew|"
    r"deliver(?:ed|y)? every \d+",
    re.I,
)
MIN_REAL_PRICE = 5.0


def to_float(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def variants(product):
    return [v for v in (product or {}).get("variants") or [] if isinstance(v, dict)]


def lowest_price(product):
    vals = [
        x
        for x in (to_float(v.get("price")) for v in variants(product))
        if x is not None
    ]
    return min(vals) if vals else None


def is_real(product, price):
    if price is None or price < MIN_REAL_PRICE:
        return False
    text = " ".join(
        str(product.get(k) or "") for k in ("handle", "title", "product_type")
    )
    if NON_PRODUCT_RE.search(text):
        return False
    vs = variants(product)
    return not (vs and all(v.get("requires_shipping") is False for v in vs))


def in_stock(product):
    return any(v.get("available") is not False for v in variants(product))


def samples(products, limit=3):
    """[(role, handle, price)] entry ~P25, main ~median, premium = max."""
    pool = []
    for p in products or []:
        price = lowest_price(p)
        if p.get("handle") and is_real(p, price):
            pool.append((price, p["handle"], in_stock(p)))
    avail = [x for x in pool if x[2]]
    if len(avail) >= 3:
        pool = avail
    if not pool:
        return []
    pool.sort()
    n = len(pool)
    picks = [("premium", pool[-1]), ("main", pool[n // 2]), ("entry", pool[n // 4])]
    out, seen = [], set()
    for role, item in picks:
        if item[1] not in seen:
            seen.add(item[1])
            out.append((role, item[1], item[0]))
    return out[:limit]


def test_products(products):
    """[(handle, title, price, why)] for test-looking items."""
    out = []
    for p in products or []:
        title = str(p.get("title") or "")
        price = lowest_price(p)
        why = None
        if TEST_RE.search(title) or TEST_RE.search(str(p.get("handle") or "")):
            why = "title"
        elif price is not None and price <= 0.01 and not NON_PRODUCT_RE.search(title):
            if not all(v.get("requires_shipping") is False for v in variants(p)):
                why = "price"
        if why:
            out.append((p.get("handle"), title, price, why))
    return out


def has_subscription(products, texts=()):
    for p in products or []:
        if p.get("selling_plan_groups") or p.get("requires_selling_plan"):
            return True
        blob = " ".join(str(p.get(k) or "") for k in ("title", "tags", "product_type"))
        if SUBSCRIPTION_RE.search(blob):
            return True
    return any(PAGE_SUB_RE.search(t or "") for t in texts)
