"""Synthetic snapshot pairs for the radar tests and the bundled demo.

Every store and product here is fictional (``.example`` domains). A scenario
is a pair of snapshots; ``write_pair(root, name)`` writes them as
``<root>/<domain>/<snapshot id>/`` folders laid out exactly like real
snapshots (``snapshot-meta.json`` + ``原始数据/raw/`` with the teardown file
names), so tests run the real summarize -> normalize -> diff chain.

Regenerate the bundled demo after changing the price-drop scenario:
    python tests/fixture_factory.py --write-demo
Python 3.8+, standard library only.
"""

import copy
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
DEMO_DIR = os.path.join(SKILL, "assets", "demo")
POLICIES = [
    "refund-policy",
    "shipping-policy",
    "privacy-policy",
    "terms-of-service",
    "contact-information",
]

T_OLD = "2026-10-20T08:00:00Z"
T_NEW = "2026-10-27T08:00:00Z"

REFUND_30 = (
    "Last updated: 2026-09-01\n"
    "We want you to love your gear. You have 30 days from delivery to return "
    "unused items for a full refund. Customers are responsible for return "
    "shipping costs. Items must be unworn with original tags attached. "
    "Refunds are issued to the original payment method within 5 business days "
    "of receiving your return. Final sale items cannot be returned. "
    "Contact our support team to start a return."
)
REFUND_14 = REFUND_30.replace("2026-09-01", "2026-10-24").replace(
    "You have 30 days from delivery", "You have 14 days from delivery"
)
SHIPPING = (
    "Last updated: 2026-08-15\n"
    "Orders are processed within 1-3 business days. Free shipping on orders "
    "over $75 within the United States. International shipping rates are "
    "calculated at checkout. You will receive a tracking number by email "
    "once your order ships."
)


def variant(vid, opts, price, compare=None, available=True, sku=None):
    return {
        "id": vid,
        "title": " / ".join(opts) if opts else "Default Title",
        "option1": opts[0] if opts else "Default Title",
        "option2": opts[1] if len(opts) > 1 else None,
        "option3": None,
        "sku": sku if sku is not None else "SKU-%d" % vid,
        "requires_shipping": True,
        "available": available,
        "price": price,
        "compare_at_price": compare,
        "updated_at": "2026-10-19T10:00:00-04:00",
    }


def product(pid, handle, title, ptype, variants, published, created=None, tags=None):
    return {
        "id": pid,
        "title": title,
        "handle": handle,
        "vendor": "Alpha",
        "product_type": ptype,
        "tags": tags or ["outdoor"],
        "created_at": created or published,
        "published_at": published,
        "updated_at": "2026-10-19T10:00:00-04:00",
        "options": [{"name": "Size", "position": 1, "values": []}],
        "images": [{"src": "https://cdn.shopify.com/s/files/x/%s.jpg?v=1" % handle}],
        "variants": variants,
    }


def alpha_old():
    p = [
        product(
            1001,
            "rain-shell",
            "Rain Shell",
            "Jackets",
            [
                variant(2001, ["M"], "110.00", "120.00"),
                variant(2002, ["L"], "110.00", "120.00"),
            ],
            "2026-05-01T09:00:00-04:00",
        ),
        product(
            1002,
            "trail-jacket",
            "Trail Jacket",
            "Jackets",
            [variant(2003, ["M"], "180.00")],
            "2026-04-10T09:00:00-04:00",
        ),
        product(
            1003,
            "wool-hat",
            "Wool Hat",
            "Accessories",
            [variant(2004, [], "35.00")],
            "2026-03-01T09:00:00-04:00",
        ),
        product(
            1004,
            "base-layer",
            "Base Layer",
            "Tops",
            [variant(2005, ["S"], "60.00"), variant(2006, ["M"], "60.00")],
            "2026-06-11T09:00:00-04:00",
        ),
        product(
            1005,
            "camp-mug",
            "Camp Mug",
            "Gear",
            [variant(2007, [], "24.00")],
            "2026-07-02T09:00:00-04:00",
        ),
        product(
            1006,
            "hiking-socks",
            "Hiking Socks",
            "Accessories",
            [variant(2008, [], "18.00")],
            "2026-08-20T09:00:00-04:00",
        ),
        product(
            1007,
            "fleece-pullover",
            "Fleece Pullover",
            "Tops",
            [variant(2009, ["M"], "89.00")],
            "2026-09-05T09:00:00-04:00",
        ),
        product(
            1008,
            "shipping-protection",
            "Shipping Protection",
            "Insurance",
            [variant(2010, [], "0.98")],
            "2025-01-01T09:00:00-05:00",
        ),
    ]
    return p


def alpha_new():
    p = copy.deepcopy(alpha_old())
    by = {x["handle"]: x for x in p}
    for v in by["rain-shell"]["variants"]:
        v["price"] = "95.00"
        v["updated_at"] = "2026-10-25T10:00:00-04:00"
    by["trail-jacket"]["variants"][0]["compare_at_price"] = "220.00"
    by["wool-hat"]["variants"][0]["available"] = False
    by["shipping-protection"]["variants"][0]["price"] = "1.20"
    p.extend(
        [
            product(
                1009,
                "summit-pack",
                "Summit Pack",
                "Bags",
                [variant(2011, [], "149.00")],
                "2026-10-23T09:00:00-04:00",
            ),
            product(
                1010,
                "storm-gaiter",
                "Storm Gaiter",
                "Accessories",
                [variant(2012, [], "39.00")],
                "2026-10-25T09:00:00-04:00",
            ),
            # Public since March but missed by the old capture: "surfaced".
            product(
                1011,
                "old-lantern",
                "Old Lantern",
                "Gear",
                [variant(2013, [], "45.00")],
                "2026-03-01T09:00:00-05:00",
                created="2026-02-20T09:00:00-05:00",
            ),
        ]
    )
    return p


def homepage(
    domain,
    lines,
    theme_version="15.0.0",
    apps=("judge.me",),
    tracking=True,
    hreflang=("en-us", "en-ca"),
):
    head = [
        "<!doctype html><html><head><title>%s</title>" % domain,
        '<script>Shopify.shop = "%s.myshopify.com";' % domain.split(".")[0],
        'Shopify.theme = {"name":"Dawn","id":1,"schema_name":"Dawn",'
        '"schema_version":"%s","theme_store_id":887,"role":"main"};</script>'
        % theme_version,
        '<link rel="stylesheet" href="//cdn.shopify.com/s/files/theme.css">',
    ]
    for code in hreflang:
        head.append(
            '<link rel="alternate" hreflang="%s" href="https://%s/%s">'
            % (code, domain, "" if code == "en-us" else code.split("-")[1] + "/")
        )
    for app in apps:
        head.append('<script src="https://cdn.%s/widget.js"></script>' % app)
    if tracking:
        head.append('<script src="https://connect.facebook.net/en_US/fbevents.js">')
        head.append("</script>")
    body = ["</head><body>"]
    body += ["<p>%s</p>" % line for line in lines]
    body.append(
        '<div class="footer"><p>&copy; 2026 %s. All rights reserved.</p>' % domain
    )
    body.append("</div></body></html>")
    return "\n".join(head + body) + "\n"


def policy_html(text):
    paras = "".join("<p>%s</p>" % x for x in text.split("\n"))
    return (
        '<html><body><div class="shopify-policy__container">'
        '<div class="shopify-policy__body">%s</div></div></body></html>\n' % paras
    )


def meta(domain, currency="USD", name=None, ships=40):
    return {
        "id": 12345,
        "name": name or domain.split(".")[0].replace("-", " ").title(),
        "myshopify_domain": "%s.myshopify.com" % domain.split(".")[0],
        "currency": currency,
        "country": "US",
        "ships_to_countries": ["C%02d" % i for i in range(ships)],
        "published_products_count": 0,
    }


def collections(handles):
    return {
        "collections": [
            {"id": 500 + i, "handle": h, "title": t, "products_count": 9}
            for i, (h, t) in enumerate(handles)
        ]
    }


class Snap(object):
    """One snapshot to write. ``files`` maps raw file name -> str or dict."""

    def __init__(self, domain, sid, captured, source="live"):
        self.domain = domain
        self.sid = sid
        self.captured = captured
        self.source = source
        self.files = {}
        self.requests = []
        self.meta_extra = {}

    def ok(self, key, fname, content, path):
        self.files[fname] = content
        self.requests.append(
            {
                "key": key,
                "file": fname,
                "url": "https://%s%s" % (self.domain, path),
                "http_status": 200,
                "result": "ok",
            }
        )

    def fail(self, key, fname, path, result, status):
        self.requests.append(
            {
                "key": key,
                "file": fname,
                "url": "https://%s%s" % (self.domain, path),
                "http_status": status,
                "result": result,
                "note": "HTTP %s" % status if status else "",
            }
        )

    def write(self, root):
        snap_dir = os.path.join(root, self.domain, self.sid)
        raw = os.path.join(snap_dir, "原始数据", "raw")
        os.makedirs(raw)
        for fname, content in sorted(self.files.items()):
            with open(os.path.join(raw, fname), "w", encoding="utf-8") as fh:
                if isinstance(content, (dict, list)):
                    json.dump(content, fh, ensure_ascii=False, indent=1)
                else:
                    fh.write(content)
        status = {
            "schema": "store-teardown/status/1",
            "domain": self.domain,
            "base_url": "https://%s" % self.domain,
            "depth": "radar",
            "started_at_utc": self.captured,
            "finished_at_utc": self.captured,
            "requests": self.requests,
        }
        with open(os.path.join(raw, "_status.json"), "w", encoding="utf-8") as fh:
            json.dump(status, fh, ensure_ascii=False, indent=1)
        meta_doc = {
            "domain": self.domain,
            "snapshot_id": self.sid,
            "captured_at_utc": self.captured,
            "source": self.source,
        }
        meta_doc.update(self.meta_extra)
        with open(
            os.path.join(snap_dir, "snapshot-meta.json"), "w", encoding="utf-8"
        ) as fh:
            json.dump(meta_doc, fh, ensure_ascii=False, indent=1)
        return snap_dir


def store_snap(
    domain,
    sid,
    captured,
    products,
    home_lines,
    refund=REFUND_30,
    shipping=SHIPPING,
    currency="USD",
    pages=None,
    cols=(("all", "All"), ("jackets", "Jackets")),
    source="live",
    theme_version="15.0.0",
):
    s = Snap(domain, sid, captured, source)
    if source != "archive":
        s.ok("meta_json", "meta.json", meta(domain, currency), "/meta.json")
    page_list = pages if pages is not None else [products, []]
    for i, items in enumerate(page_list, 1):
        s.ok(
            "products_json",
            "products-%d.json" % i,
            {"products": items},
            "/products.json?limit=250&page=%d" % i,
        )
    if source != "archive":
        s.ok(
            "collections_json",
            "collections-1.json",
            collections(cols),
            "/collections.json",
        )
    s.ok("homepage", "homepage.html", homepage(domain, home_lines, theme_version), "/")
    for name in POLICIES:
        key = "policy_" + name.replace("-", "_")
        text = {"refund-policy": refund, "shipping-policy": shipping}.get(name)
        if text:
            s.ok(key, "policy-%s.html" % name, policy_html(text), "/policies/%s" % name)
        elif source != "archive":
            s.fail(
                key, "policy-%s.html" % name, "/policies/%s" % name, "not_found", 404
            )
    return s


HOME_OLD = [
    "Gear for every trail",
    "Free shipping on orders over $75",
    "Shop the new arrivals",
    "Ends in 02:13:44",
    "Built to last since 2015",
]
HOME_NEW = [
    "Gear for every trail",
    "Free shipping on orders over $75",
    "20% OFF with code FALL20",
    "Shop the new arrivals",
    "Ends in 00:41:07",
    "Built to last since 2015",
]


def many_products(n, start=3000, price="50.00"):
    return [
        product(
            start + i,
            "item-%02d" % i,
            "Item %02d" % i,
            "Gear",
            [variant(start + 1000 + i, [], price)],
            "2026-0%d-01T09:00:00-04:00" % (1 + i % 8),
        )
        for i in range(n)
    ]


def scenario(name, domain=None):
    """Return (old Snap, new Snap) for a named scenario."""
    if name == "price-drop":
        d = domain or "alpha-outfitters.example"
        return (
            store_snap(d, "2026-10-20", T_OLD, alpha_old(), HOME_OLD),
            store_snap(d, "2026-10-27", T_NEW, alpha_new(), HOME_NEW, refund=REFUND_14),
        )
    if name == "no-change":
        d = domain or "beta-home.example"
        old = alpha_old()
        new = copy.deepcopy(old)
        for p in new:
            p["updated_at"] = "2026-10-26T11:00:00-04:00"
            p["images"][0]["src"] += "&v=99"
            p["variants"].reverse()
            p["tags"] = list(reversed(p["tags"]))
        return (
            store_snap(d, "2026-10-20", T_OLD, old, HOME_OLD),
            store_snap(
                d,
                "2026-10-27",
                T_NEW,
                new,
                [x.replace("02:13:44", "05:59:01") for x in HOME_OLD],
                refund=REFUND_30.replace("2026-09-01", "2026-10-25"),
                shipping=SHIPPING.replace("2026-08-15", "2026-10-26"),
            ),
        )
    if name == "went-dark":
        d = domain or "gamma-minimal.example"
        old = store_snap(d, "2026-10-20", T_OLD, alpha_old(), HOME_OLD)
        new = Snap(d, "2026-10-27", T_NEW)
        new.files["homepage.html"] = (
            "<html><head><title>Just a moment...</title></head>"
            "<body>Checking your browser. cf-chl</body></html>\n"
        )
        new.requests.append(
            {
                "key": "homepage",
                "file": "homepage.html",
                "url": "https://%s/" % d,
                "http_status": 200,
                "result": "ok",
            }
        )
        new.fail("meta_json", "meta.json", "/meta.json", "blocked", 403)
        new.fail("products_json", "products-1.json", "/products.json", "blocked", 403)
        return old, new
    if name == "mass-missing":
        d = domain or "delta-gear.example"
        old = many_products(40)
        return (
            store_snap(d, "2026-10-20", T_OLD, old, HOME_OLD),
            store_snap(d, "2026-10-27", T_NEW, old[:8], HOME_OLD),
        )
    if name == "partial-pagination":
        d = domain or "zeta-packs.example"
        old = many_products(40)
        return (
            store_snap(d, "2026-10-20", T_OLD, old, HOME_OLD),
            store_snap(d, "2026-10-27", T_NEW, old[:30], HOME_OLD, pages=[old[:30]]),
        )
    if name == "currency-switch":
        d = domain or "epsilon-wear.example"
        old = alpha_old()
        new = copy.deepcopy(old)
        for p in new:
            for v in p["variants"]:
                v["price"] = "%.2f" % (float(v["price"]) * 1.37)
        return (
            store_snap(d, "2026-10-20", T_OLD, old, HOME_OLD),
            store_snap(d, "2026-10-27", T_NEW, new, HOME_OLD, currency="CAD"),
        )
    if name == "archive-baseline":
        d = domain or "eta-trail.example"
        allp = many_products(40)
        old = store_snap(
            d,
            "2026-09-27-archive",
            "2026-09-27T12:00:00Z",
            allp[:30],
            HOME_OLD,
            pages=[allp[:30]],
            source="archive",
        )
        old.meta_extra["archive_timestamps"] = {
            "products-1.json": "20260927120000",
            "homepage.html": "20260927110000",
        }
        new_p = copy.deepcopy(allp)
        new_p[0]["variants"][0]["price"] = "40.00"
        return old, store_snap(d, "2026-10-27", T_NEW, new_p, HOME_OLD)
    if name == "not-shopify":
        d = domain or "theta-blog.example"
        s = Snap(d, "2026-10-27", T_NEW)
        s.ok(
            "homepage",
            "homepage.html",
            "<html><body><p>Just a blog</p></body></html>\n",
            "/",
        )
        s.fail("meta_json", "meta.json", "/meta.json", "not_found", 404)
        s.fail("products_json", "products-1.json", "/products.json", "not_found", 404)
        s.fail("sitemap", "sitemap.xml", "/sitemap.xml", "not_found", 404)
        return None, s
    raise KeyError(name)


SCENARIOS = [
    "price-drop",
    "no-change",
    "went-dark",
    "mass-missing",
    "partial-pagination",
    "currency-switch",
    "archive-baseline",
    "not-shopify",
]


def write_pair(root, name, domain=None):
    """Write a scenario under root; returns (old snapshot dir or None, new dir)."""
    old, new = scenario(name, domain)
    return (old.write(root) if old else None), new.write(root)


def write_demo(dest=DEMO_DIR):
    """Bundle the price-drop pair as the offline demo (raw files only)."""
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    old, new = scenario("price-drop", "demo-outdoors.example")
    for snap in (old, new):
        snap.write(dest)
    # write() nests under <domain>/; the demo keeps snapshots at the top level
    inner = os.path.join(dest, "demo-outdoors.example")
    for name in os.listdir(inner):
        shutil.move(os.path.join(inner, name), os.path.join(dest, name))
    os.rmdir(inner)
    return dest


if __name__ == "__main__":
    if "--write-demo" in sys.argv:
        print("wrote %s" % write_demo())
    else:
        print(__doc__)
