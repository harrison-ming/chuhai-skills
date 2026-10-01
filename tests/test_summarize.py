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
        self.assertEqual(s["tool_version"], "0.2.1")
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
    def test_collect_fake_store(self):
        server = HTTPServer(("127.0.0.1", 0), FakeStore)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            out = os.path.join(self.tmp, "run")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
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


if __name__ == "__main__":
    unittest.main()
