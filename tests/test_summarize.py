"""Tests for shopify-store-teardown scripts (summarize.py and collect.py).

All fixtures are synthetic (fictional store example-store.com).

Run from the repository root:
    python -m unittest discover -s tests
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures")
SCRIPTS = os.path.join(
    os.path.dirname(HERE), "skills", "shopify-store-teardown", "scripts"
)
sys.path.insert(0, SCRIPTS)

import collect  # noqa: E402
import summarize  # noqa: E402


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="store-teardown-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_fixture(self, name, domain=None):
        dst = os.path.join(self.tmp, "example-store.com", name)
        shutil.copytree(os.path.join(FIXTURES, name), dst)
        summary = summarize.summarize_run(dst, domain)
        with open(os.path.join(dst, "summary.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["schema"], summarize.SCHEMA)
        return dst, summary


class TestHelpers(unittest.TestCase):
    def test_percentile(self):
        vals = [1.0, 2.0, 3.0, 4.0]
        self.assertEqual(summarize.percentile(vals, 0.0), 1.0)
        self.assertEqual(summarize.percentile(vals, 1.0), 4.0)
        self.assertAlmostEqual(summarize.percentile(vals, 0.5), 2.5)
        self.assertIsNone(summarize.percentile([], 0.5))

    def test_lenient_json_markdown(self):
        obj, state, _ = summarize.parse_json_lenient('prose\n```json\n{"a": 1}\n```')
        self.assertEqual(obj, {"a": 1})
        self.assertEqual(state, "extracted")

    def test_lenient_json_truncated_salvage(self):
        text = '{"products": [{"id": 1}, {"id": 2}, {"id": 3, "ti'
        obj, state, _ = summarize.parse_json_lenient(text, "products")
        self.assertEqual(state, "salvaged")
        self.assertEqual([p["id"] for p in obj["products"]], [1, 2])

    def test_lenient_json_html(self):
        obj, state, _ = summarize.parse_json_lenient(
            "<html><title>Just a moment...</title></html>"
        )
        self.assertIsNone(obj)
        self.assertEqual(state, "blocked")

    def test_best_selling_skips_template_garbage(self):
        src = (
            '<a href="/products/a-1">x</a><a href="/products/${item.handle}`">y</a>'
            '<a href="/products/a-1">dup</a><a href="/products/b.js">z</a>'
            '<a href="/products/b-2?variant=1">w</a>'
        )
        self.assertEqual(summarize.best_selling_handles(src), ["a-1", "b-2"])

    def test_payment_labels_and_frameworks(self):
        src = (
            '<div class="footer-payment"><span class="payment-methods_method" '
            'aria-label="Apple Pay"></span></div><script id="__NEXT_DATA__"></script>'
        )
        hs = summarize.build_html_signals([("homepage.html", src)])
        self.assertEqual(hs["payment_icons_observed"], ["apple-pay"])
        self.assertEqual(hs["frameworks_possible"], ["next.js"])

    def test_normalize_input(self):
        self.assertEqual(
            collect.normalize_input("example.com"), ("https", "example.com")
        )
        self.assertEqual(
            collect.normalize_input("https://www.Example.com/products/x?y=1"),
            ("https", "www.example.com"),
        )
        self.assertEqual(collect.strip_www("www.example.com"), "example.com")

    def test_registrable_domain(self):
        self.assertEqual(
            collect.registrable_domain("shop.example.co.uk"), "example.co.uk"
        )
        self.assertEqual(collect.registrable_domain("www.example.com"), "example.com")
        self.assertEqual(
            collect.registrable_domain("a.b.example.com.au"), "example.com.au"
        )
        self.assertIsNone(collect.registrable_domain("127.0.0.1"))


class TestSummarizeFixtures(FixtureCase):
    def test_normal_store(self):
        dst, s = self.run_fixture("normal")
        self.assertEqual(s["domain"], "example-store.com")
        self.assertEqual(s["identity"]["status"], "confirmed")
        names = {sig["name"] for sig in s["identity"]["signals"]}
        self.assertEqual(
            names,
            {
                "meta_myshopify_domain",
                "products_json_structure",
                "homepage_shopify_markers",
                "sitemap_products_index",
            },
        )
        self.assertEqual(s["theme"]["name"], "Dawn")
        self.assertEqual(s["theme"]["theme_store_id"], 887)
        self.assertEqual(s["theme"]["schema_version"], "15.0.0")
        self.assertEqual(s["shop"]["ships_to_count"], 3)
        p = s["products"]
        self.assertEqual(p["count"], 3)
        self.assertEqual(p["count_source"], "products_json")
        self.assertNotIn("count_is_lower_bound", p)
        self.assertEqual(p["sitemap_product_urls"], 3)
        self.assertEqual(p["collections_all_products_count"], 3)
        self.assertEqual(p["price"]["min"], 18.0)
        self.assertEqual(p["price"]["median"], 120.0)
        self.assertEqual(p["price"]["max"], 240.0)
        self.assertEqual(p["price"]["currency"], "USD")
        self.assertEqual(
            [(b["from"], b["to"], b["count"]) for b in p["price_bands"]],
            [(10, 25, 1), (100, 200, 1), (200, 500, 1)],
        )
        self.assertAlmostEqual(p["on_sale_share"], 0.3333, places=4)
        self.assertAlmostEqual(p["discount_depth_median"], 0.2, places=4)
        self.assertEqual(p["variants"]["max"], 3)
        self.assertEqual(p["options_top"], [["Size", 2]])
        self.assertEqual(p["created_first"], "2023-05-02")
        self.assertEqual(p["published_by_month"], {"2023-05": 1, "2024-02": 2})
        self.assertEqual(p["availability"], {"available": 2, "sold_out": 1})
        self.assertEqual(
            s["best_selling_top"], ["rain-jacket", "trail-runner", "wool-sock"]
        )
        self.assertEqual(s["offsite"]["rdap"]["registered"], "2017-11-02")
        self.assertEqual(s["offsite"]["rdap"]["registrar"], "Example Registrar Inc.")
        self.assertEqual(s["offsite"]["wayback"]["first_capture"], "2017-12")
        self.assertEqual(
            s["offsite"]["wayback"]["captures_by_year"],
            {"2017": 1, "2018": 1, "2024": 1},
        )
        pol = s["policies"]
        self.assertEqual(pol["refund-policy"]["result"], "ok")
        self.assertGreater(pol["refund-policy"]["chars"], 400)
        self.assertEqual(pol["privacy-policy"]["result"], "not_found")
        self.assertEqual(pol["terms-of-service"]["result"], "not_checked")
        self.assertIn("policy_near_empty:shipping-policy", s["warnings"])
        with open(os.path.join(dst, "raw", "policy-refund-policy.txt")) as fh:
            txt = fh.read()
        self.assertNotIn("Footer links", txt)
        hs = s["html_signals"]
        apps = {a["name"] for a in hs["apps_possible"]}
        self.assertTrue({"Judge.me", "Klaviyo", "PageFly"} <= apps)
        self.assertEqual(
            hs["payment_icons_observed"], ["visa", "paypal", "shopify-pay"]
        )
        self.assertEqual(hs["hreflang"][0], ["en-us", "https://example-store.com/"])
        self.assertTrue({"meta_pixel", "ga4", "klaviyo"} <= set(hs["tracking_tags"]))
        self.assertEqual(hs["localization_selector"], "present")
        self.assertNotIn("collections_count_mismatch", s["warnings"])
        self.assertEqual(s["errors"], [])
        # products.tsv
        with open(os.path.join(dst, "products.tsv"), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(len(lines), 4)
        header = lines[0].split("\t")
        row = dict(zip(header, lines[1].split("\t")))
        self.assertEqual(row["handle"], "trail-runner")
        self.assertEqual(row["price_min"], "120")
        self.assertEqual(row["compare_at_max"], "150")
        self.assertEqual(row["url"], "https://example-store.com/products/trail-runner")
        # evidence.jsonl
        with open(os.path.join(dst, "evidence.jsonl"), encoding="utf-8") as fh:
            ev = [json.loads(line) for line in fh]
        self.assertEqual(ev[0]["evidence_id"], "E001")
        levels = {e["kind"]: e["level"] for e in ev}
        self.assertEqual(levels["domain_registration"], "E4")
        self.assertEqual(levels["theme"], "E1")
        self.assertTrue(all(e["locator"].startswith("raw/") for e in ev))
        endpoint_ev = {e["locator"]: e["level"] for e in ev if e["kind"] == "endpoint"}
        self.assertEqual(endpoint_ev["raw/meta.json"], "E1")
        self.assertEqual(endpoint_ev["raw/wayback.json"], "E4")
        self.assertIn("raw/policy-refund-policy.html", endpoint_ev)

    def test_sitemap_localized_maps_deduped(self):
        dst = os.path.join(self.tmp, "example-store.com", "loc")
        shutil.copytree(os.path.join(FIXTURES, "normal"), dst)
        src = os.path.join(dst, "raw", "sitemap-products-1.xml")
        with open(src, encoding="utf-8") as fh:
            xml = fh.read().replace(
                "example-store.com/products", "example-store.com/es-US/products"
            )
        with open(os.path.join(dst, "raw", "sitemap-products-2.xml"), "w") as fh:
            fh.write(xml)
        s = summarize.summarize_run(dst)
        self.assertEqual(s["products"]["sitemap_product_urls"], 3)
        self.assertNotIn("sitemap_count_mismatch", s["warnings"])

    def test_blocked_endpoints(self):
        _dst, s = self.run_fixture("blocked")
        self.assertEqual(s["identity"]["status"], "unconfirmed")
        self.assertEqual(s["endpoints"]["meta_json"]["result"], "blocked")
        self.assertEqual(s["endpoints"]["products_json"]["result"], "blocked")
        self.assertEqual(s["endpoints"]["homepage"]["result"], "ok")
        self.assertEqual(s["endpoints"]["rdap"]["result"], "not_checked")
        self.assertIsNone(s["products"]["count"])
        self.assertEqual(s["theme"]["source"], "not_observed")

    def test_collections_mismatch(self):
        _dst, s = self.run_fixture("collections_mismatch")
        self.assertEqual(s["domain"], "example-store.com")
        self.assertEqual(s["products"]["count"], 3)
        self.assertEqual(s["products"]["collections_all_products_count"], 50)
        self.assertIn("collections_count_mismatch", s["warnings"])
        self.assertEqual(s["identity"]["status"], "confirmed")
        self.assertEqual(s["endpoints"]["homepage"]["result"], "not_checked")

    def test_truncated_markdown(self):
        _dst, s = self.run_fixture("truncated_markdown", domain="example-store.com")
        self.assertEqual(s["shop"]["name"], "Example Store")
        ep = s["endpoints"]["products_json"]
        self.assertEqual(ep["result"], "ok")
        self.assertTrue(ep["truncated"])
        self.assertEqual(s["products"]["count"], 2)
        self.assertTrue(s["products"]["count_is_lower_bound"])
        self.assertIn("products_json_truncated", s["warnings"])
        self.assertEqual(s["endpoints"]["wayback"]["result"], "error")
        self.assertTrue(any("wayback.json" in e for e in s["errors"]))
        self.assertEqual(s["identity"]["status"], "confirmed")

    def test_not_shopify(self):
        _dst, s = self.run_fixture("not_shopify")
        self.assertEqual(s["identity"]["status"], "not_shopify")
        self.assertEqual(s["identity"]["signals"], [])

    def test_empty_products(self):
        _dst, s = self.run_fixture("empty_products")
        self.assertEqual(s["products"]["count"], 0)
        self.assertIsNone(s["products"]["price"]["median"])
        self.assertEqual(s["products"]["price_bands"], [])
        self.assertIn("products_json_empty", s["warnings"])
        self.assertEqual(s["identity"]["status"], "confirmed")
        names = [sig["name"] for sig in s["identity"]["signals"]]
        self.assertNotIn("products_json_structure", names)

    def test_empty_raw_dir(self):
        dst = os.path.join(self.tmp, "example-store.com", "empty")
        os.makedirs(os.path.join(dst, "raw"))
        s = summarize.summarize_run(dst)
        self.assertEqual(s["identity"]["status"], "unconfirmed")
        self.assertEqual(s["endpoints"]["meta_json"]["result"], "not_checked")


# ---------------------------------------------------------------------------
# collect.py against a local fake store
# ---------------------------------------------------------------------------

FAKE_PRODUCTS = [
    {
        "id": i,
        "handle": "item-%d" % i,
        "title": "Item %d" % i,
        "product_type": "Widget",
        "vendor": "Example Brand",
        "tags": ["demo"],
        "created_at": "2025-01-0%dT00:00:00Z" % i,
        "published_at": "2025-01-0%dT00:00:00Z" % i,
        "options": [{"name": "Title", "values": ["Default Title"]}],
        "variants": [{"id": i * 10, "price": "%d.00" % (i * 10), "available": True}],
    }
    for i in range(1, 4)
]


class FakeStore(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def send(self, code, body, ctype="text/html"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path
        if path == "/":
            self.send(
                200,
                '<html><head><script>Shopify.shop = "fake.myshopify.com";'
                'Shopify.theme = {"name":"Fake","schema_name":"Dawn","role":"main"};'
                "</script></head><body><a href='/products/item-2'>x</a></body></html>",
            )
        elif path == "/meta.json":
            self.send(
                200,
                json.dumps({"name": "Fake", "myshopify_domain": "fake.myshopify.com"}),
                "application/json",
            )
        elif path.startswith("/products.json"):
            body = {"products": FAKE_PRODUCTS if "page=1" in path else []}
            self.send(200, json.dumps(body), "application/json")
        elif path.startswith("/collections.json"):
            self.send(403, "Forbidden")
        elif path == "/sitemap.xml":
            self.send(404, "not found")
        elif path.startswith("/collections/all"):
            self.send(
                200, "<a href='/products/item-3'>a</a><a href='/products/item-1'>b</a>"
            )
        elif path == "/policies/refund-policy":
            self.send(
                200, '<div class="shopify-policy__body"><p>%s</p></div>' % ("x " * 150)
            )
        elif path.startswith("/products/item-"):
            self.send(200, "<html><body>Product %s</body></html>" % path)
        elif path == "/pages/about-us":
            self.send(200, "<html><body>About</body></html>")
        else:
            self.send(404, "not found")


# A store that refuses bulk product data with 429 (Retry-After: 60) but
# serves its homepage, meta.json, sitemap and product pages normally.
RL_HANDLES = ["shipping-protection"] + [
    "%s-%d" % (kind, i)
    for kind in ("trail-runner", "wool-sock", "rain-jacket")
    for i in range(1, 7)
]


def rl_price(handle):
    """Deterministic price of a rate-limited fake store product."""
    if handle == "shipping-protection":
        return 2.5
    kind, num = handle.rsplit("-", 1)
    base = {"trail-runner": 120.0, "wool-sock": 18.0, "rain-jacket": 210.0}[kind]
    return base + 5 * int(num)


def rl_product_page(handle):
    price = rl_price(handle)
    num = int(handle.rsplit("-", 1)[1]) if handle[-1].isdigit() else 0
    title = handle.replace("-", " ").title()
    if num % 3 == 0:
        # no JSON-LD: Open Graph price tags only
        head = (
            '<meta property="og:title" content="%s">'
            '<meta property="og:price:amount" content="%.2f">'
            '<meta property="og:price:currency" content="USD">' % (title, price)
        )
    else:
        ld = {
            "@context": "http://schema.org/",
            "@type": "Product",
            "name": title,
            "offers": [
                {
                    "@type": "Offer",
                    "price": "%.2f" % price,
                    "priceCurrency": "USD",
                    "availability": "http://schema.org/"
                    + ("OutOfStock" if num == 4 else "InStock"),
                },
                {
                    "@type": "Offer",
                    "price": "%.2f" % (price + 10),
                    "priceCurrency": "USD",
                    "availability": "http://schema.org/InStock",
                },
            ],
        }
        head = '<script type="application/ld+json">%s</script>' % json.dumps(ld)
    return (
        "<!doctype html><html><head><title>%s</title>%s"
        '<script>Shopify.shop = "rl-store.myshopify.com";</script></head>'
        "<body><h1>%s</h1></body></html>" % (title, head, title)
    )


class RateLimitedStore(FakeStore):
    seen = []  # request paths, for tests

    def send_429(self):
        data = b"Too Many Requests"
        self.send_response(429)
        self.send_header("Retry-After", "60")
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path
        self.seen.append(path)
        host = "http://%s" % self.headers.get("Host")
        if path == "/":
            self.send(
                200,
                '<html><head><script>Shopify.shop = "rl-store.myshopify.com";'
                'Shopify.theme = {"name":"Dawn","schema_name":"Dawn",'
                '"schema_version":"15.0.0","role":"main"};</script></head>'
                "<body><a href='/products/rain-jacket-2'>x</a></body></html>",
            )
        elif path == "/meta.json":
            self.send(
                200,
                json.dumps(
                    {
                        "name": "RL Store",
                        "myshopify_domain": "rl-store.myshopify.com",
                        "currency": "USD",
                        "published_products_count": len(RL_HANDLES),
                    }
                ),
                "application/json",
            )
        elif path.startswith(("/products.json", "/collections.json")):
            self.send_429()
        elif path == "/sitemap.xml":
            self.send(
                200,
                "<sitemapindex><sitemap><loc>%s/sitemap_products_1.xml?from=1"
                "&amp;to=99</loc></sitemap></sitemapindex>" % host,
                "application/xml",
            )
        elif path.startswith("/sitemap_products_1.xml"):
            urls = "".join(
                "<url><loc>%s/products/%s</loc><lastmod>2026-%02d-15T10:00:00Z"
                "</lastmod></url>" % (host, h, 6 + i % 3)
                for i, h in enumerate(RL_HANDLES)
            )
            self.send(
                200, "<urlset><url><loc>%s/</loc></url>%s</urlset>" % (host, urls)
            )
        elif path.startswith("/collections/all"):
            self.send(
                200,
                "<a href='/products/wool-sock-4'>a</a>"
                "<a href='/products/trail-runner-3'>b</a>",
            )
        elif path.startswith("/products/"):
            handle = path.split("/products/", 1)[1]
            if handle in RL_HANDLES:
                self.send(200, rl_product_page(handle))
            else:
                self.send(404, "not found")
        else:
            self.send(404, "not found")


def fake_product(handle, price, title=None, ptype="Shoes", **variant):
    v = {"price": "%.2f" % price, "available": True}
    v.update(variant)
    return {
        "handle": handle,
        "title": title or handle,
        "product_type": ptype,
        "variants": [v],
    }


class TestL1Samples(unittest.TestCase):
    def test_roles_by_price_and_best_selling(self):
        products = [
            fake_product("p%d" % i, float(p))
            for i, p in enumerate([20, 30, 40, 60, 80, 95, 100, 110, 120, 150, 300])
        ]
        best = ["p10", "p7", "p5"]  # p10 is premium; p7 (100) is inside P25-P75
        picks = collect.select_l1_samples(products, best)
        roles = {x["role"]: x["handle"] for x in picks}
        self.assertEqual([x["role"] for x in picks], ["entry", "main", "premium"])
        self.assertEqual(roles["premium"], "p10")
        self.assertEqual(roles["main"], "p7")
        self.assertEqual(
            roles["entry"], "p2"
        )  # 40 and 60 tie around P25 = 50; cheaper wins
        self.assertEqual(len({x["handle"] for x in picks}), 3)

    def test_main_falls_back_to_median(self):
        products = [
            fake_product("p%d" % i, float(p))
            for i, p in enumerate([10, 20, 30, 40, 50])
        ]
        roles = {x["role"]: x for x in collect.select_l1_samples(products, [])}
        self.assertEqual(roles["main"]["handle"], "p2")
        self.assertEqual(roles["main"]["price"], 30.0)

    def test_excludes_virtual_and_cheap_items(self):
        products = [
            fake_product("shipping-protection", 500.0, "Shipping Protection"),
            fake_product("route-insurance", 400.0, "Route Package Protection"),
            fake_product("gift-card", 300.0, "Gift Card", "Gift Card"),
            fake_product("digital", 250.0, "Digital Thing", requires_shipping=False),
            fake_product("return-cover", 200.0, "退货险"),
            fake_product("sock", 3.0),
            fake_product("runner", 100.0),
            fake_product("lounger", 80.0),
            fake_product("slipper", 60.0),
        ]
        picks = collect.select_l1_samples(products, ["shipping-protection"])
        handles = {x["handle"] for x in picks}
        self.assertEqual(handles, {"runner", "lounger", "slipper"})
        self.assertEqual(
            [x for x in picks if x["role"] == "premium"][0]["handle"], "runner"
        )

    def test_prefers_available_and_handles_small_pools(self):
        sold = fake_product("sold-out", 999.0)
        sold["variants"][0]["available"] = False
        products = [sold] + [fake_product("a%d" % i, 10.0 * (i + 1)) for i in range(3)]
        picks = collect.select_l1_samples(products, [])
        self.assertNotIn("sold-out", [x["handle"] for x in picks])
        self.assertEqual(len(collect.select_l1_samples([fake_product("x", 9)])), 1)
        self.assertEqual(collect.select_l1_samples([]), [])

    def test_samples_record_basis(self):
        products = [
            fake_product("p%d" % i, float(p))
            for i, p in enumerate([20, 30, 40, 60, 80, 95, 100, 110, 120, 150, 300])
        ]
        products.append(fake_product("returns-cover", 1.0, "Free Returns Coverage"))
        roles = {x["role"]: x for x in collect.select_l1_samples(products, ["p7"])}
        self.assertEqual(roles["entry"]["basis"], "closest_to_p25_excluding_virtual")
        self.assertEqual(roles["entry"]["basis_value"], 50.0)  # P25 of 11 real
        self.assertEqual(roles["main"]["basis"], collect.SAMPLE_BASIS["main"])
        self.assertEqual(roles["premium"]["basis_value"], 300.0)
        self.assertEqual(roles["entry"]["basis_pool"], "in_stock_real_products")
        fallback = {
            x["role"]: x
            for x in collect.select_l1_samples(
                [fake_product("q%d" % i, 10.0 * (i + 1)) for i in range(5)], []
            )
        }
        self.assertEqual(
            fallback["main"]["basis"], "closest_to_median_excluding_virtual"
        )

    def test_price_excluding_virtual_matches_sample_rule(self):
        products = [
            fake_product("a", 62.0),
            fake_product("b", 75.0),
            fake_product("c", 100.0),
            fake_product("d", 120.0),
            fake_product("cover", 0.8, "Free Returns Coverage"),
            fake_product("card", 50.0, "Gift Card", "Gift Card"),
            fake_product("sock", 3.0),
        ]
        stats, _rows = summarize.product_stats(products, "USD", "https://x.com")
        pv = stats["price_excluding_virtual"]
        self.assertEqual(pv["count"], 4)
        self.assertEqual(pv["excluded"], 3)
        self.assertEqual((pv["min"], pv["max"]), (62.0, 120.0))
        self.assertEqual(pv["median"], 87.5)
        self.assertEqual(stats["price"]["min"], 0.8)  # raw stats keep everything
        entry = [
            x for x in collect.select_l1_samples(products, []) if x["role"] == "entry"
        ][0]
        self.assertEqual(entry["basis_value"], pv["p25"])  # same basis
        # sold-out cheap items: sample pool is in-stock only, see in_stock stats
        for h, price in (("old1", 20.0), ("old2", 25.0), ("old3", 30.0)):
            sold = fake_product(h, price)
            sold["variants"][0]["available"] = False
            products.append(sold)
        stats, _rows = summarize.product_stats(products, "USD", "https://x.com")
        pv = stats["price_excluding_virtual"]
        entry = [
            x for x in collect.select_l1_samples(products, []) if x["role"] == "entry"
        ][0]
        self.assertEqual(pv["count"], 7)
        self.assertEqual(pv["in_stock"]["count"], 4)
        self.assertNotEqual(pv["p25"], pv["in_stock"]["p25"])
        self.assertEqual(entry["basis_pool"], "in_stock_real_products")
        self.assertEqual(entry["basis_value"], pv["in_stock"]["p25"])

    def test_pick_collection_and_slug(self):
        cols = [
            {"handle": "all", "products_count": 9},
            {"handle": "empty", "products_count": 0},
            {"handle": "mens-shoes", "products_count": 12},
        ]
        self.assertEqual(collect.pick_collection(cols), "mens-shoes")
        self.assertIsNone(collect.pick_collection([{"handle": "all"}]))
        self.assertEqual(collect.safe_slug("a/b c?"), "a-b-c")

    def test_summarize_reads_samples_from_file_names(self):
        tmp = tempfile.mkdtemp(prefix="store-teardown-test-")
        self.addCleanup(shutil.rmtree, tmp, True)
        run = os.path.join(tmp, "x.com 对标拆解 2026-09-30")
        data = os.path.join(run, "原始数据")
        os.makedirs(os.path.join(data, "raw"))
        with open(os.path.join(data, "raw", "products-1.json"), "w") as fh:
            json.dump({"products": [fake_product("wool", 95.0)]}, fh)
        with open(os.path.join(data, "raw", "page-product-wool.html"), "w") as fh:
            fh.write("<html>wool</html>")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = summarize.main([run, "--quiet"])  # top-level run folder
        self.assertEqual(rc, 0)
        with open(os.path.join(data, "summary.json"), encoding="utf-8") as fh:
            s = json.load(fh)
        self.assertEqual(s["tool_version"], "0.2.3")
        self.assertEqual(s["domain"], "x.com")
        self.assertEqual(
            s["l1_samples"],
            [
                {
                    "handle": "wool",
                    "role": None,
                    "price": 95.0,
                    "file": "raw/page-product-wool.html",
                    "result": "ok",
                }
            ],
        )


class TestCollectLocal(FixtureCase):
    def test_sitemap_child_budget(self):
        class FakeFetcher(object):
            def __init__(self):
                self.urls = []

            def get(self, url, _accept):
                self.urls.append(url)
                if url.endswith("/sitemap.xml"):
                    body = (
                        "<sitemapindex>"
                        + "".join(
                            "<sitemap><loc>https://example.com/sitemap_products_%d.xml</loc></sitemap>"
                            % i
                            for i in range(5)
                        )
                        + "</sitemapindex>"
                    ).encode("utf-8")
                else:
                    body = b"<urlset></urlset>"
                return {
                    "status": 200,
                    "body": body,
                    "final_url": url,
                    "elapsed_ms": 1,
                    "note": "",
                }

        out = os.path.join(self.tmp, "budget")
        fetcher = FakeFetcher()
        col = collect.Collector("https://example.com", out, "L0", 1, 2, True, fetcher)
        os.makedirs(col.raw)
        col.collect_sitemap()
        self.assertEqual(len(fetcher.urls), 3)
        self.assertTrue(os.path.isfile(os.path.join(col.raw, "sitemap-products-2.xml")))
        self.assertFalse(
            os.path.exists(os.path.join(col.raw, "sitemap-products-3.xml"))
        )

    def test_private_network_target_is_rejected(self):
        with mock.patch.object(
            collect.socket,
            "getaddrinfo",
            return_value=[(None, None, None, None, ("127.0.0.1", 443))],
        ):
            with self.assertRaisesRegex(ValueError, "private, local or reserved"):
                collect.ensure_public_url("https://internal.example/")

    def test_mixed_public_and_private_dns_is_rejected(self):
        answers = [
            (None, None, None, None, ("93.184.216.34", 443)),
            (None, None, None, None, ("fd00::1", 443, 0, 0)),
        ]
        with mock.patch.object(collect.socket, "getaddrinfo", return_value=answers):
            with self.assertRaisesRegex(ValueError, "private, local or reserved"):
                collect.ensure_public_url("https://mixed.example/")

    def test_redirect_to_private_network_is_rejected(self):
        answers = [(None, None, None, None, ("169.254.169.254", 80))]
        with mock.patch.object(collect.socket, "getaddrinfo", return_value=answers):
            with self.assertRaises(collect.URLError):
                collect.PublicOnlyRedirect().redirect_request(
                    None, None, 302, "Found", {}, "http://169.254.169.254/latest"
                )

    def test_public_only_skips_cross_origin_sitemap(self):
        class FakeFetcher(object):
            def __init__(self):
                self.urls = []

            def get(self, url, _accept):
                self.urls.append(url)
                body = (
                    "<sitemapindex>"
                    "<loc>https://example.com/sitemap_products_1.xml</loc>"
                    "<loc>https://evil.example/sitemap_products_2.xml</loc>"
                    "</sitemapindex>"
                    if url.endswith("/sitemap.xml")
                    else "<urlset></urlset>"
                ).encode("utf-8")
                return {
                    "status": 200,
                    "body": body,
                    "final_url": url,
                    "elapsed_ms": 1,
                    "note": "",
                }

        out = os.path.join(self.tmp, "same-origin")
        fetcher = FakeFetcher()
        col = collect.Collector(
            "https://example.com", out, "L0", 1, 8, True, fetcher, True
        )
        os.makedirs(col.raw)
        with mock.patch.object(collect, "ensure_public_url", return_value=True):
            col.collect_sitemap()
        self.assertEqual(
            fetcher.urls,
            [
                "https://example.com/sitemap.xml",
                "https://example.com/sitemap_products_1.xml",
            ],
        )

    def test_sitemap_product_handles_are_capped(self):
        out = os.path.join(self.tmp, "sitemap-cap")
        raw = os.path.join(out, "raw")
        os.makedirs(raw)
        with open(os.path.join(raw, "sitemap.xml"), "w", encoding="utf-8") as fh:
            fh.write(
                "<sitemapindex><loc>https://example.com/sitemap_products_1.xml</loc></sitemapindex>"
            )
        with open(
            os.path.join(raw, "sitemap-products-1.xml"), "w", encoding="utf-8"
        ) as fh:
            fh.write(
                "<urlset>"
                + "".join(
                    "<loc>https://example.com/products/p%d</loc>" % i
                    for i in range(2100)
                )
                + "</urlset>"
            )
        result = summarize.summarize_run(out, "example.com")
        self.assertEqual(result["products"]["sitemap_product_urls"], 2000)
        self.assertTrue(result["endpoints"]["sitemap_products"]["truncated"])
        self.assertIn("sitemap_products_truncated_at_2000", result["warnings"])

    def test_collect_fake_store(self):
        server = HTTPServer(("127.0.0.1", 0), FakeStore)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            out = os.path.join(self.tmp, "run")
            buf = io.StringIO()
            with (
                contextlib.redirect_stdout(buf),
                mock.patch.object(collect, "ensure_public_url", return_value=True),
            ):
                rc = collect.main(
                    [
                        "http://127.0.0.1:%d/products/whatever" % port,
                        "--out",
                        out,
                        "--depth",
                        "L1",
                        "--delay",
                        "0",
                        "--timeout",
                        "5",
                        "--skip-offsite",
                    ]
                )
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(rc, 0)
        self.assertIn("deliver.py", buf.getvalue())
        raw = os.path.join(out, "raw")
        for name in (
            "homepage.html",
            "meta.json",
            "products-1.json",
            "products-2.json",
        ):
            self.assertTrue(os.path.isfile(os.path.join(raw, name)), name)
        self.assertFalse(os.path.exists(os.path.join(raw, "collections-1.json")))
        self.assertTrue(os.path.isfile(os.path.join(raw, "page-about.html")))
        with open(os.path.join(raw, "_status.json"), encoding="utf-8") as fh:
            status = json.load(fh)
        by_file = {r["file"]: r for r in status["requests"]}
        self.assertEqual(by_file["collections-1.json"]["result"], "blocked")
        self.assertEqual(by_file["sitemap.xml"]["result"], "not_found")
        self.assertEqual(by_file["policy-shipping-policy.html"]["result"], "not_found")
        with open(os.path.join(out, "summary.json"), encoding="utf-8") as fh:
            s = json.load(fh)
        self.assertEqual(s["identity"]["status"], "confirmed")
        self.assertEqual(s["products"]["count"], 3)
        self.assertEqual(s["theme"]["name"], "Fake")
        self.assertEqual(s["best_selling_top"], ["item-3", "item-1"])
        self.assertEqual(s["endpoints"]["collections_json"]["result"], "blocked")
        self.assertEqual(s["policies"]["refund-policy"]["result"], "ok")
        # L1: main collection page + three sample product pages
        self.assertTrue(os.path.isfile(os.path.join(raw, "page-collection-all.html")))
        for h in ("item-1", "item-2", "item-3"):
            self.assertTrue(
                os.path.isfile(os.path.join(raw, "page-product-%s.html" % h)), h
            )
        roles = {x["role"]: x for x in s["l1_samples"]}
        self.assertEqual(sorted(roles), ["entry", "main", "premium"])
        self.assertEqual(roles["premium"]["handle"], "item-3")
        self.assertEqual(roles["premium"]["price"], 30.0)
        self.assertEqual(roles["main"]["file"], "raw/page-product-item-2.html")
        self.assertTrue(all(x["result"] == "ok" for x in s["l1_samples"]))
        self.assertTrue(all("basis" in x for x in s["l1_samples"]))
        self.assertIn("l1_samples", status)
        self.assertIn("price_excluding_virtual", s["products"])


class TestProductPageParse(unittest.TestCase):
    def test_json_ld_offers(self):
        src = rl_product_page("trail-runner-1")
        facts = summarize.parse_product_page(src)
        self.assertEqual(facts["price"], 125.0)
        self.assertEqual(facts["price_max"], 135.0)
        self.assertEqual(facts["currency"], "USD")
        self.assertIs(facts["available"], True)
        self.assertIsNone(facts["compare_at"])
        self.assertEqual(facts["title"], "Trail Runner 1")
        self.assertEqual(facts["price_from"], "json_ld")
        sold = summarize.parse_product_page(rl_product_page("trail-runner-4"))
        self.assertIs(sold["available"], True)  # one variant still in stock

    def test_json_ld_graph_aggregate_and_strikethrough(self):
        ld = {
            "@graph": [
                {"@type": "Organization", "name": "X"},
                {
                    "@type": ["Product"],
                    "name": "Sofa",
                    "offers": {
                        "@type": "AggregateOffer",
                        "lowPrice": "1,299.00",
                        "highPrice": 1899,
                        "priceCurrency": "eur",
                        "availability": "https://schema.org/OutOfStock",
                    },
                },
            ]
        }
        src = '<script type="application/ld+json">%s</script>' % json.dumps(ld)
        facts = summarize.parse_product_page(src)
        self.assertEqual((facts["price"], facts["price_max"]), (1299.0, 1899.0))
        self.assertEqual(facts["currency"], "EUR")
        self.assertIs(facts["available"], False)
        offer = {
            "@type": "Product",
            "name": "Mug",
            "offers": {
                "@type": "Offer",
                "price": 12,
                "priceSpecification": [
                    {"@type": "UnitPriceSpecification", "price": 12},
                    {
                        "@type": "UnitPriceSpecification",
                        "priceType": "https://schema.org/StrikethroughPrice",
                        "price": 20,
                    },
                ],
            },
        }
        src = "<script type='application/ld+json'>%s</script>" % json.dumps(offer)
        facts = summarize.parse_product_page(src)
        self.assertEqual((facts["price"], facts["compare_at"]), (12.0, 20.0))
        self.assertIsNone(facts["available"])

    def test_meta_price_fallback(self):
        facts = summarize.parse_product_page(rl_product_page("wool-sock-3"))
        self.assertEqual(facts["price"], 33.0)
        self.assertEqual(facts["currency"], "USD")
        self.assertEqual(facts["price_from"], "meta")
        self.assertEqual(facts["title"], "Wool Sock 3")
        src = (
            '<meta content="1,050.5" property="product:price:amount">'
            "<title>Lamp &amp; Co</title>"
            '<script type="application/ld+json">{broken</script>'
        )
        facts = summarize.parse_product_page(src)
        self.assertEqual(facts["price"], 1050.5)
        self.assertIsNone(facts["currency"])
        self.assertEqual(facts["title"], "Lamp & Co")

    def test_nested_main_entity_product(self):
        ld = {
            "@type": "WebPage",
            "mainEntity": {
                "@type": "Product",
                "name": "Deep",
                "offers": {"@type": "Offer", "price": "42.00", "priceCurrency": "CAD"},
            },
        }
        src = '<script type="application/ld+json">%s</script>' % json.dumps(ld)
        facts = summarize.parse_product_page(src)
        self.assertEqual((facts["price"], facts["currency"]), (42.0, "CAD"))
        # depth-bounded: a pathologically deep document does not hang
        deep = {"@type": "Product", "offers": {"price": 1}}
        for _ in range(500):
            deep = {"x": deep}
        self.assertIsNone(
            summarize._from_json_ld(
                '<script type="application/ld+json">%s</script>' % json.dumps(deep)
            )
        )

    def test_several_products_prefer_handle(self):
        def product(handle, price):
            return {
                "@type": "Product",
                "name": handle,
                "url": "https://shop.example/products/%s" % handle,
                "offers": {"@type": "Offer", "price": price, "priceCurrency": "USD"},
            }

        page = '<script type="application/ld+json">%s</script>' % json.dumps(
            [product("cheap-add-on", 9), product("main-item", 120)]
        )
        facts = summarize.parse_product_page(page, "main-item")
        self.assertEqual((facts["price"], facts["title"]), (120.0, "main-item"))
        # several priced, none matching: no guess; meta price when present
        self.assertIsNone(summarize.parse_product_page(page, "other"))
        self.assertIsNone(summarize.parse_product_page(page))
        meta = page + '<meta property="og:price:amount" content="99.00">'
        facts = summarize.parse_product_page(meta, "other")
        self.assertEqual((facts["price"], facts["price_from"]), (99.0, "meta"))
        # current product unpriced + priced recommendation: never borrow it
        current = product("main-item", 0)
        del current["offers"]
        page = '<script type="application/ld+json">%s</script>' % json.dumps(
            [current, product("recommended", 55)]
        )
        self.assertIsNone(summarize.parse_product_page(page, "main-item"))
        self.assertIsNone(summarize.parse_product_page(page))
        facts = summarize.parse_product_page(
            page + '<meta property="og:price:amount" content="80">', "main-item"
        )
        self.assertEqual((facts["price"], facts["price_from"]), (80.0, "meta"))
        # offer url match, ProductGroup variants count as one product
        group = {
            "@type": "ProductGroup",
            "name": "Tee",
            "hasVariant": [
                {
                    "@type": "Product",
                    "offers": {
                        "price": p,
                        "url": "/products/tee?variant=%d" % p,
                    },
                }
                for p in (20, 25)
            ],
        }
        src = '<script type="application/ld+json">%s</script>' % json.dumps(group)
        facts = summarize.parse_product_page(src, "tee")
        self.assertEqual((facts["price"], facts["price_max"]), (20.0, 25.0))
        facts = summarize.parse_product_page(src)
        self.assertEqual((facts["price"], facts["price_max"]), (20.0, 25.0))

    def test_group_with_referenced_variants(self):
        graph = {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "ProductGroup",
                    "@id": "https://shop.example/products/tee#group",
                    "name": "Tee",
                    "url": "https://shop.example/products/tee",
                    "hasVariant": [{"@id": "#red"}, {"@id": "#blue"}],
                },
                {
                    "@type": "Product",
                    "@id": "#red",
                    "offers": {
                        "@type": "Offer",
                        "price": "20.00",
                        "priceCurrency": "USD",
                    },
                },
                {
                    "@type": "Product",
                    "@id": "#blue",
                    "offers": {
                        "@type": "Offer",
                        "price": "26.00",
                        "priceCurrency": "USD",
                    },
                },
            ],
        }
        src = '<script type="application/ld+json">%s</script>' % json.dumps(graph)
        for handle in ("tee", None, "other"):
            facts = summarize.parse_product_page(src, handle)
            self.assertIsNotNone(facts, handle)
            self.assertEqual((facts["price"], facts["price_max"]), (20.0, 26.0))
            self.assertEqual(facts["title"], "Tee")

    def test_no_price(self):
        self.assertIsNone(summarize.parse_product_page("<html>hi</html>"))
        self.assertIsNone(summarize.parse_product_page(""))
        ld = '<script type="application/ld+json">{"@type": "Product"}</script>'
        self.assertIsNone(summarize.parse_product_page(ld))

    def test_sitemap_entries_and_spread(self):
        xml = (
            "<urlset><url><loc>https://x.com/</loc></url>"
            "<url><loc>https://x.com/products/a</loc>"
            "<lastmod>2026-07-01T00:00:00Z</lastmod>"
            "<image:image><image:loc>https://cdn/x.jpg</image:loc></image:image>"
            "</url><url><loc>https://x.com/es/products/b?v=1</loc></url></urlset>"
        )
        self.assertEqual(
            summarize.sitemap_product_entries(xml),
            [("a", "2026-07-01T00:00:00Z"), ("b", None)],
        )
        handles = ["gift-card"] + ["p%d" % i for i in range(10)]
        self.assertEqual(collect.spread_sample(handles, 20), handles[1:])
        picks = collect.spread_sample(handles, 4)
        self.assertEqual(picks, ["p1", "p3", "p6", "p8"])
        self.assertEqual(picks, collect.spread_sample(handles, 4))  # deterministic
        self.assertEqual(collect.spread_sample(handles, 0), [])


class RateLimitedCase(FixtureCase):
    def serve(self, handler):
        handler.seen = []
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return "http://127.0.0.1:%d/" % server.server_address[1]

    def collect(self, url, depth="L1", **kw):
        self.sleeps = []
        fetcher = collect.Fetcher(timeout=5, delay=0, sleep=self.sleeps.append)
        out = os.path.join(self.tmp, "rl-run")
        with mock.patch.object(collect, "ensure_public_url", return_value=True):
            col = collect.Collector(url, out, depth, 100, 50, True, fetcher, **kw)
            col.run()
        return col, out


class TestRateLimitedCollect(RateLimitedCase):
    def test_falls_back_to_sitemap_page_sample(self):
        col, out = self.collect(self.serve(RateLimitedStore))
        seen = RateLimitedStore.seen
        # products.json: first try + 2 polite retries after Retry-After 60
        self.assertEqual(sum(1 for p in seen if p.startswith("/products.json")), 3)
        self.assertEqual(self.sleeps.count(60.0), 2)
        self.assertFalse(any(p.startswith("/collections.json") for p in seen))
        page_hits = [p for p in seen if p.startswith("/products/")]
        self.assertEqual(len(page_hits), 12)
        self.assertEqual(len(set(page_hits)), 12)  # L1 samples not refetched
        self.assertNotIn("/products/shipping-protection", page_hits)
        # at least 1.5 s between sampled pages (host delay raised after 429)
        self.assertTrue(all(s >= 1.4 for s in self.sleeps if s < 60), self.sleeps)
        summary = summarize.summarize_run(out)
        p = summary["products"]
        cov = summary["coverage"]
        self.assertEqual(p["price_source"], "product_page_sample")
        self.assertEqual(p["price_sample_size"], 12)
        self.assertEqual(p["price_population"], 19)
        self.assertEqual(p["count_source"], "sitemap")
        self.assertIsNone(p["on_sale_share"])
        self.assertIsNone(p["discount_depth_median"])
        self.assertIn("on_sale_share", p["unavailable_reasons"])
        self.assertEqual(p["price"]["min"], 23.0)
        self.assertEqual(p["price"]["max"], 240.0)
        self.assertEqual(
            p["sitemap_lastmod_by_month"], {"2026-06": 7, "2026-07": 6, "2026-08": 6}
        )
        self.assertEqual(cov["products_json"], "rate_limited")
        self.assertEqual(cov["price_source"], "product_page_sample")
        self.assertFalse(cov["discount_data"])
        self.assertFalse(cov["new_product_cadence"])
        self.assertEqual(cov["rate_limited_endpoints"], ["products_json"])
        for w in ("products_json_rate_limited", "prices_from_page_sample"):
            self.assertIn(w, summary["warnings"])
        self.assertNotIn("price_sample_small", summary["warnings"])
        roles = {x["role"]: x for x in summary["l1_samples"]}
        self.assertEqual(sorted(roles), ["entry", "main", "premium"])
        self.assertTrue(
            all(x["basis_pool"] == "product_page_sample" for x in roles.values())
        )
        self.assertEqual(roles["premium"]["handle"], "rain-jacket-6")
        with open(os.path.join(out, "products.tsv"), encoding="utf-8") as fh:
            rows = fh.read().splitlines()
        self.assertEqual(rows[0].split("\t"), summarize.TSV_HEADER)
        self.assertEqual(len(rows), 13)
        self.assertTrue(all(r.split("\t")[-1] == "product_page" for r in rows[1:]))
        digest = summarize.human_digest(summary)
        self.assertIn("429", digest)
        self.assertIn("价格基于 12 个商品页抽样 (共约 19 款)", digest)
        with open(os.path.join(out, "raw", "_status.json"), encoding="utf-8") as fh:
            status = json.load(fh)
        self.assertEqual(status["rate_limit"]["waited_seconds"], 120.0)
        self.assertEqual(status["price_sample"]["requested"], 12)

    def test_sampling_stops_at_first_429(self):
        class Flaky(RateLimitedStore):
            def do_GET(self):
                pages = [p for p in self.seen if p.startswith("/products/")]
                if self.path.startswith("/products/") and len(pages) >= 2:
                    self.seen.append(self.path)
                    return self.send_429()
                return RateLimitedStore.do_GET(self)

        col, out = self.collect(self.serve(Flaky), depth="L0", price_sample=6)
        self.assertEqual(col.sample_info["stopped"], "rate_limited")
        self.assertEqual(col.sample_info["requested"], 3)
        summary = summarize.summarize_run(out)
        self.assertEqual(summary["products"]["price_sample_size"], 2)
        self.assertIn("price_sample_small", summary["warnings"])
        self.assertEqual(
            summary["endpoints"]["product_page_sample"]["stopped"], "rate_limited"
        )
        self.assertEqual(
            summary["coverage"]["rate_limited_endpoints"],
            ["products_json", "product_page_sample"],
        )
        self.assertEqual(summary["l1_samples"], [])  # L0: page sample only

    def test_sample_off_and_working_products_untouched(self):
        col, out = self.collect(self.serve(RateLimitedStore), "L0", price_sample=0)
        self.assertFalse(any(p.startswith("/products/") for p in RateLimitedStore.seen))
        summary = summarize.summarize_run(out)
        self.assertIsNone(summary["products"]["price_source"])
        self.assertIn("价格: 本次没有取到", summarize.human_digest(summary))
        col, out = self.collect(self.serve(FakeStore), "L0")
        self.assertEqual(col.page_samples, [])
        self.assertIsNone(col.sample_info)

    def test_rate_limited_fixture(self):
        _dst, s = self.run_fixture("rate_limited")
        self.assertEqual(s["coverage"]["products_json"], "rate_limited")
        self.assertEqual(s["products"]["price_source"], "product_page_sample")
        self.assertEqual(s["products"]["price"]["median"], 137.5)
        self.assertEqual(s["endpoints"]["collections_json"]["result"], "not_checked")
        self.assertEqual(s["endpoints"]["products_json"]["result"], "blocked")

    def test_blocked_fixture_keeps_semantics(self):
        _dst, s = self.run_fixture("blocked")
        self.assertEqual(s["endpoints"]["products_json"]["result"], "blocked")
        self.assertEqual(s["coverage"]["products_json"], "rate_limited")
        self.assertIsNone(s["coverage"]["price_source"])
        self.assertNotIn("prices_from_page_sample", s["warnings"])

    def test_saved_product_pages_without_status(self):
        dst = os.path.join(self.tmp, "example-store.com", "manual")
        os.makedirs(os.path.join(dst, "raw"))
        for h in ("trail-runner-1", "wool-sock-3"):
            with open(os.path.join(dst, "raw", "page-product-%s.html" % h), "w") as fh:
                fh.write(rl_product_page(h))
        s = summarize.summarize_run(dst, "example-store.com")
        self.assertEqual(s["products"]["price_source"], "product_page_sample")
        self.assertEqual(s["endpoints"]["product_page_sample"]["source"], "saved_files")
        self.assertEqual(s["coverage"]["products_json"], "not_checked")
        self.assertEqual(len(s["l1_samples"]), 2)  # mode B keeps file-name samples


class ScriptFetcher(object):
    """Fake fetcher: path -> status (429 / 200); records every URL."""

    def __init__(self, base, answers):
        self.base = base
        self.answers = answers
        self.urls = []

    def get(self, url, _accept):
        self.urls.append(url)
        path = url[len(self.base) :] if url.startswith(self.base) else url
        status = 200
        for prefix, code in self.answers:
            if path.startswith(prefix):
                status = code
                break
        if status == 429:
            return {
                "status": 429,
                "body": b"slow down",
                "final_url": url,
                "elapsed_ms": 1,
                "note": "rate limited (429), wait budget exhausted",
                "rate_limited": True,
            }
        if path == "/sitemap.xml":
            body = (
                "<sitemapindex><loc>%s/sitemap_products_1.xml</loc>"
                "<loc>%s/sitemap_products_2.xml</loc></sitemapindex>"
                % (self.base, self.base)
            )
        elif path.startswith("/sitemap_products"):
            body = "<urlset><url><loc>%s/products/a-1</loc></url></urlset>" % self.base
        elif path.endswith(".json"):
            body = "{}"
        else:
            body = "<html>Shopify page %s</html>" % path
        return {
            "status": 200,
            "body": body.encode("utf-8"),
            "final_url": url,
            "elapsed_ms": 1,
            "note": "",
        }


CATALOGUE_PATHS = (
    "/products.json",
    "/collections.json",
    "/sitemap_products",
    "/products/",
    "/collections/",
)


class TestRateLimitStops(FixtureCase):
    base = "https://shop.example"

    def run_collect(self, answers, depth="L1", skip_offsite=True):
        f = ScriptFetcher(self.base, answers)
        out = os.path.join(self.tmp, "stop-%d" % len(os.listdir(self.tmp)))
        col = collect.Collector(self.base, out, depth, 100, 50, skip_offsite, f)
        col.run()
        return col, f

    def paths(self, urls):
        return [u[len(self.base) :] for u in urls if u.startswith(self.base)]

    def test_catalogue_429_stops_catalogue_requests(self):
        col, f = self.run_collect(
            [("/products.json", 429), ("/sitemap_products_1", 429)]
        )
        paths = self.paths(f.urls)
        first = paths.index("/sitemap_products_1.xml")
        after = paths[first + 1 :]
        self.assertFalse(
            [p for p in after if p.startswith(CATALOGUE_PATHS)], after
        )  # zero further catalogue requests
        self.assertNotIn("/collections.json?limit=250&page=1", paths)
        self.assertIn("/policies/refund-policy", after)  # plain pages still once
        skipped = [r for r in col.requests if r["result"] == "not_checked"]
        notes = {r["key"]: r["note"] for r in skipped}
        self.assertEqual(notes["sitemap_products"], collect.SKIP_CATALOGUE_NOTE)
        self.assertEqual(notes["best_selling"], collect.SKIP_CATALOGUE_NOTE)
        self.assertEqual(notes["page_collection"], collect.SKIP_CATALOGUE_NOTE)
        self.assertEqual(notes["collections_json"], collect.SKIP_BULK_NOTE)
        self.assertTrue(col.catalogue_limited)

    def test_plain_page_429_stops_the_host_not_offsite(self):
        col, f = self.run_collect(
            [("/products.json", 429), ("/policies/refund-policy", 429)],
            skip_offsite=False,
        )
        paths = self.paths(f.urls)
        idx = paths.index("/policies/refund-policy")
        self.assertEqual(paths[idx + 1 :], [])  # nothing more to the store
        self.assertTrue(any("rdap.org" in u for u in f.urls))
        self.assertTrue(any("web.archive.org" in u for u in f.urls))
        skipped = [r for r in col.requests if r["note"] == collect.SKIP_HOST_NOTE]
        self.assertTrue(any(r["key"] == "page_about" for r in skipped))

    def test_product_page_429_stops_l1_pages(self):
        col, f = self.run_collect([("/products.json", 429), ("/products/a-1", 429)])
        paths = self.paths(f.urls)
        self.assertEqual(paths.count("/products/a-1"), 1)
        after = paths[paths.index("/products/a-1") + 1 :]
        self.assertFalse([p for p in after if p.startswith(CATALOGUE_PATHS)], after)
        self.assertEqual(col.sample_info["stopped"], "rate_limited")


if __name__ == "__main__":
    unittest.main()
