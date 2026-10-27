"""Synthetic tests for competitor-change-radar (offline, stdlib unittest).

Run from the repository root:
    python tools/run_tests.py
    python -m unittest discover -s skills/competitor-change-radar/tests

Scenarios come from fixture_factory.py (fictional .example stores) and run
through the real chain: summarize -> normalize -> diff -> draft -> check ->
deliver. Network code is exercised with fake fetchers only.
"""

import contextlib
import filecmp
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
SCRIPTS = os.path.join(SKILL, "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, HERE)

import archive  # noqa: E402
import check_report  # noqa: E402
import deliver  # noqa: E402
import diff as diffmod  # noqa: E402
import draft  # noqa: E402
import fixture_factory as ff  # noqa: E402
import normalize  # noqa: E402
import radar  # noqa: E402
import skill_config as cfg  # noqa: E402
import snapshot  # noqa: E402
from _shared import checks  # noqa: E402


def quiet(fn, *args, **kw):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = fn(*args, **kw)
    return rc, out.getvalue()


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="radar-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pair(self, name):
        a, b = ff.write_pair(self.tmp, name)
        sb = normalize.write_snapshot(b)
        sa = normalize.write_snapshot(a) if a else None
        return sa, sb

    def diff(self, name):
        sa, sb = self.pair(name)
        return diffmod.compute_diff(sa, sb)


def types(d, t):
    return [e for e in d["events"] if e["type"] == t]


class ScenarioTest(Tmp):
    def test_price_drop(self):
        d = self.diff("price-drop")
        c = d["counts"]
        self.assertEqual(d["status"], "complete")
        self.assertEqual(c["price_down"], 2)  # two variants of Rain Shell
        self.assertEqual(c["products_price_down"], 1)
        self.assertEqual(c["product_added"], 2)
        self.assertEqual(c["product_surfaced"], 1)
        self.assertNotIn("Old Lantern", [e["title"] for e in types(d, "product_added")])
        self.assertGreaterEqual(c["promo_started"], 1)
        self.assertEqual(c["sold_out"], 1)
        self.assertEqual(c["product_removed"], 0)
        term = [e for e in types(d, "policy_changed") if e["field"] == "return_days"]
        self.assertEqual(
            (term[0]["before"], term[0]["after"], term[0]["severity"]), (30, 14, "high")
        )
        self.assertEqual(term[0]["badge"], "store_claim")
        # shipping protection: kept as an event, excluded from counts
        virt = [e for e in d["events"] if e["virtual"]]
        self.assertEqual([e["title"] for e in virt], ["Shipping Protection"])
        self.assertEqual(c["price_up"], 0)
        promo = types(d, "homepage_promo_changed")[0]
        self.assertIn("20% off with code fall20", promo["details"]["added"])
        self.assertEqual(promo["badge"], "page_shown")
        rain = [e for e in types(d, "price_down")]
        self.assertTrue(all(e["badge"] == "verified" for e in rain))
        self.assertEqual(rain[0]["delta_pct"], -13.64)
        tables = draft.diff_tables(d, "zh")
        self.assertIn("Shipping Protection", [r["item"] for r in tables["all_events"]])
        self.assertNotIn(
            "Shipping Protection", [r["title"] for r in tables["price_changes"]]
        )

    def test_no_change(self):
        d = self.diff("no-change")
        self.assertEqual(d["events"], [])
        self.assertEqual(sum(d["counts"].values()), 0)
        self.assertEqual(d["status"], "complete")
        md = draft.diff_md(d, {"lang": "zh", "next_due": "2026-11-03"})
        self.assertIn("本期无变化", md)

    def test_went_dark(self):
        d = self.diff("went-dark")
        self.assertEqual([e["type"] for e in d["events"]], ["store_status_changed"])
        self.assertFalse(d["comparable"]["products"])
        self.assertEqual(d["status"], "partial")
        md = draft.diff_md(d, {"lang": "zh", "next_due": "2026-11-03"})
        self.assertNotIn("全部下架", md)
        self.assertIn("[查不到]", md)

    def test_mass_missing(self):
        d = self.diff("mass-missing")
        self.assertIn("mass_removal_suspect", d["warnings"])
        self.assertEqual(d["counts"]["product_removed"], 0)
        self.assertEqual(d["counts"]["product_missing_unconfirmed"], 32)
        self.assertEqual(d["status"], "partial")

    def test_partial_pagination(self):
        d = self.diff("partial-pagination")
        self.assertFalse(d["comparable"]["removals"])
        self.assertEqual(d["counts"]["product_removed"], 0)
        self.assertEqual(d["counts"]["product_missing_unconfirmed"], 10)
        md = draft.diff_md(d, {"lang": "zh", "next_due": "2026-11-03"})
        self.assertIn("不判断下架", md)

    def test_currency_switch(self):
        d = self.diff("currency-switch")
        self.assertEqual([e["type"] for e in d["events"]], ["currency_changed"])
        self.assertFalse(d["comparable"]["prices"])

    def test_archive_baseline(self):
        d = self.diff("archive-baseline")
        self.assertEqual(d["counts"]["product_removed"], 0)
        self.assertNotIn("verified", {e["badge"] for e in d["events"]})
        self.assertEqual(types(d, "price_down")[0]["badge"], "page_shown")
        self.assertIn("archive_baseline", d["warnings"])
        self.assertEqual(d["status"], "partial")

    def test_not_shopify_baseline_is_failed(self):
        _a, sb = self.pair("not-shopify")
        self.assertEqual(sb["status"], "not_shopify")
        base = diffmod.baseline(sb)
        self.assertEqual(base["status"], "failed")
        md = draft.baseline_md(base, {"lang": "zh", "next_due": "2026-11-03"})
        self.assertIn("[查不到]", md)
        self.assertIn("完成状态: failed", md)

    def test_network_only_failure_is_named(self):
        s = ff.Snap("gone.example", "2026-10-27", ff.T_NEW)
        for key, fname in (
            ("homepage", "homepage.html"),
            ("meta_json", "meta.json"),
            ("products_json", "products-1.json"),
        ):
            s.fail(key, fname, "/", "error", None)
        offline = ff.Snap("gone2.example", "2026-10-27", ff.T_NEW)
        offline.requests = list(s.requests)
        offline.requests.append(
            {
                "key": "rdap",
                "file": "rdap.json",
                "url": "https://rdap.org/x",
                "http_status": None,
                "result": "error",
                "note": "timeout",
            }
        )
        snap = normalize.write_snapshot(s.write(self.tmp))
        self.assertEqual(
            (snap["fetch_error"], snap["status"]), ("unreachable", "blocked")
        )
        md = draft.baseline_md(
            diffmod.baseline(snap), {"lang": "zh", "next_due": "2026-11-03"}
        )
        self.assertIn("其他网站能连上", md)
        snap = normalize.write_snapshot(offline.write(self.tmp))
        self.assertEqual((snap["fetch_error"], snap["status"]), ("network", "blocked"))
        md = draft.baseline_md(
            diffmod.baseline(snap), {"lang": "zh", "next_due": "2026-11-03"}
        )
        self.assertIn("当前运行环境不能联网", md)
        self.assertIn("完成状态: failed", md)

    def test_deterministic(self):
        a, b = ff.write_pair(self.tmp, "price-drop")
        outs = []
        for _ in range(2):
            sa, sb = normalize.build_snapshot(a), normalize.build_snapshot(b)
            outs.append(diffmod.dumps(diffmod.compute_diff(sa, sb)))
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(
            diffmod.dumps(normalize.build_snapshot(b)),
            diffmod.dumps(normalize.build_snapshot(b)),
        )

    def test_thresholds_override(self):
        sa, sb = self.pair("price-drop")
        d = diffmod.compute_diff(sa, sb, thresholds={"price_summary_pct": 50})
        self.assertEqual({e["severity"] for e in types(d, "price_down")}, {"medium"})


class NormalizeTest(unittest.TestCase):
    def test_promo_patterns_documented(self):
        with open(
            os.path.join(SKILL, "references", "method.md"), encoding="utf-8"
        ) as fh:
            text = fh.read()
        block = text.split("promo-patterns:start -->")[1].split("<!--")[0]
        documented = re.findall(r"^- `(.+)`$", block, re.M)
        self.assertEqual(documented, normalize.PROMO_PATTERNS)

    def test_homepage_noise(self):
        html = "<p>Ends in 02:13:44</p><p>$95.00</p><p>Cart (3)</p><p>Shop now</p><p>Summer SALE</p>"
        other, promo = normalize.homepage_lines(html)
        self.assertEqual(other, ["shop now"])
        self.assertEqual(promo, ["summer sale"])

    def test_policy_terms(self):
        sents = normalize.sentences(
            normalize.norm_policy_text(
                "Last updated: May 1, 2026\nReturns accepted within 30 days of delivery. "
                "Free shipping on orders over $50. Orders ship in 1-3 business days. "
                "A 15% restocking fee applies. Customers are responsible for return shipping."
            )
        )
        terms = normalize.key_terms(sents)
        self.assertEqual(terms["return_days"], 30)
        self.assertEqual(terms["free_shipping_over"], "50.00")
        self.assertEqual(terms["processing_days"], "1-3")
        self.assertEqual(terms["restocking_fee_pct"], 15)
        self.assertEqual(terms["return_shipping_paid_by"], "customer")
        self.assertFalse(any("2026" in s for s in sents))

    def test_money(self):
        self.assertEqual(normalize.money("95"), "95.00")
        self.assertEqual(normalize.money(None), None)
        self.assertEqual(normalize.money("abc"), None)


class FakeFetcher(object):
    """Serves a fixture Snap by URL path; counts requests."""

    def __init__(self, snap, extra=None):
        self.delay = 0
        self.calls = []
        self.by_path = {}
        for req in snap.requests:
            path = req["url"].split(snap.domain, 1)[1]
            body = snap.files.get(req["file"])
            if isinstance(body, (dict, list)):
                body = json.dumps(body)
            self.by_path[path.split("?")[0] + self._page(path)] = (
                req["http_status"],
                body.encode("utf-8") if body is not None else b"",
            )
        self.extra = extra or {}

    @staticmethod
    def _page(path):
        m = re.search(r"[?&]page=(\d+)", path)
        return "#%s" % m.group(1) if m else ""

    def get(self, url, accept=None, headers=None):
        self.calls.append(url)
        for key, val in self.extra.items():
            if key in url:
                return dict(
                    status=val[0], body=val[1], final_url=url, elapsed_ms=1, note=""
                )
        path = url.split("://", 1)[1]
        path = path[path.index("/") :] if "/" in path else "/"
        key = path.split("?")[0] + self._page(path)
        status, body = self.by_path.get(key, (404, b"not found"))
        return dict(status=status, body=body, final_url=url, elapsed_ms=1, note="")


class StorageTest(Tmp):
    def test_capture_is_idempotent_per_day(self):
        _old, new = ff.scenario("price-drop")
        store = os.path.join(self.tmp, "雷达", new.domain)
        f1 = FakeFetcher(new)
        sid, snap, reused = snapshot.capture_live(
            new.domain, store, "zh", fetcher=f1, log=lambda *a: None
        )
        self.assertFalse(reused)
        self.assertEqual(snap["status"], "complete")
        self.assertEqual(len(snap["products"]), 11)
        self.assertFalse(any("sitemap" in u for u in f1.calls))  # products complete
        f2 = FakeFetcher(new)
        sid2, _s, reused2 = snapshot.capture_live(
            new.domain, store, "zh", fetcher=f2, log=lambda *a: None
        )
        self.assertTrue(reused2)
        self.assertEqual((sid2, f2.calls), (sid, []))
        sid3, _s, _r = snapshot.capture_live(
            new.domain,
            store,
            "zh",
            fetcher=FakeFetcher(new),
            force=True,
            log=lambda *a: None,
        )
        self.assertEqual(sid3, sid + "_2")

    def test_partial_folders_cleaned(self):
        store = os.path.join(self.tmp, "店")
        stale = os.path.join(store, "快照", ".2026-10-01.partial")
        os.makedirs(stale)
        keep = os.path.join(store, "快照", "notes")
        os.makedirs(keep)
        self.assertEqual(snapshot.clean_partials(store, "zh"), [".2026-10-01.partial"])
        self.assertTrue(os.path.isdir(keep))

    def test_pack_old_raw_keeps_data(self):
        store = os.path.join(self.tmp, "store")
        base = os.path.join(store, "快照")
        _a, b = ff.write_pair(self.tmp, "price-drop")
        for i in range(7):
            dest = os.path.join(base, "2026-10-%02d" % (10 + i))
            shutil.copytree(b, dest)
            meta = os.path.join(dest, normalize.META_FILE)
            with open(meta, encoding="utf-8") as fh:
                m = json.load(fh)
            m["captured_at_utc"] = "2026-10-%02dT08:00:00Z" % (10 + i)
            m["snapshot_id"] = os.path.basename(dest)
            with open(meta, "w", encoding="utf-8") as fh:
                json.dump(m, fh)
            normalize.write_snapshot(dest)
        packed = snapshot.pack_old_raw(store, "zh")
        self.assertEqual(packed, ["2026-10-11", "2026-10-12"])
        data = os.path.join(base, "2026-10-11", "原始数据")
        self.assertTrue(os.path.isfile(os.path.join(data, "raw.zip")))
        self.assertFalse(os.path.isdir(os.path.join(data, "raw")))
        self.assertTrue(
            os.path.isdir(os.path.join(base, "2026-10-10", "原始数据", "raw"))
        )
        snap = normalize.write_snapshot(os.path.join(base, "2026-10-11"))  # unpacks
        self.assertEqual(len(snap["products"]), 11)

    def test_teardown_import(self):
        root = os.path.join(self.tmp, "出海拆解报告")
        a, _b = ff.write_pair(self.tmp, "price-drop")
        run = os.path.join(root, "alpha-outfitters.example 对标拆解 2026-10-20")
        shutil.copytree(os.path.join(a, "原始数据"), os.path.join(run, "原始数据"))
        store = os.path.join(root, "雷达", "alpha-outfitters.example")
        sid = snapshot.import_teardown(
            root, store, "alpha-outfitters.example", "zh", log=lambda *x: None
        )
        self.assertEqual(sid, "2026-10-20-teardown")
        snap = normalize.load_snapshot(os.path.join(store, "快照", sid))
        self.assertEqual(snap["source"], "teardown_import")
        self.assertIsNone(
            snapshot.import_teardown(
                root, store, "alpha-outfitters.example", "zh", log=lambda *x: None
            )
        )

    def test_index_rebuild(self):
        store = os.path.join(self.tmp, "radar", "x")
        _a, b = ff.write_pair(self.tmp, "price-drop")
        shutil.copytree(b, os.path.join(store, "快照", "2026-10-27"))
        normalize.write_snapshot(os.path.join(store, "快照", "2026-10-27"))
        idx = snapshot.write_index(store, "zh", "x")
        self.assertEqual(idx["snapshots"][0]["status"], "complete")
        self.assertEqual(idx["cadence_days"], 7)

    def test_store_dir_normalizes_domain(self):
        env = {"CHUHAI_OUTPUT_DIR": self.tmp}
        d1, _ = snapshot.find_store_dir(
            "https://www.shop.example/products/x", "zh", environ=env
        )
        d2, _ = snapshot.find_store_dir("shop.example", "zh", environ=env)
        self.assertEqual(d1, d2)
        self.assertTrue(d1.endswith(os.path.join("雷达", "shop.example")))


class ArchiveTest(Tmp):
    def cdx(self, rows):
        return json.dumps([["timestamp", "original", "digest"]] + rows).encode()

    def test_archive_baseline_within_budget(self):
        _old, new = ff.scenario("price-drop")
        products = json.dumps({"products": ff.alpha_old()}).encode()
        home = ff.homepage(new.domain, ff.HOME_OLD).encode()
        extra = {
            "url=alpha-outfitters.example/products.json": (
                200,
                self.cdx(
                    [
                        [
                            "20260927120000",
                            "https://alpha-outfitters.example/products.json?page=2",
                            "a",
                        ],
                        [
                            "20260926120000",
                            "https://alpha-outfitters.example/products.json",
                            "b",
                        ],
                    ]
                ),
            ),
            "url=alpha-outfitters.example/&": (
                200,
                self.cdx(
                    [["20260926110000", "https://alpha-outfitters.example/", "c"]]
                ),
            ),
            "url=alpha-outfitters.example/policies/": (
                200,
                self.cdx(
                    [
                        [
                            "20260926100000",
                            "https://alpha-outfitters.example/policies/refund-policy",
                            "d",
                        ]
                    ]
                ),
            ),
            "20260926120000id_": (200, products),
            "20260926110000id_": (200, home),
            "20260926100000id_": (200, ff.policy_html(ff.REFUND_30).encode()),
        }
        f = FakeFetcher(new, extra)
        store = os.path.join(self.tmp, "store")
        import datetime

        sid, info = archive.capture_archive(
            new.domain,
            store,
            "zh",
            fetcher=f,
            today=datetime.date(2026, 10, 27),
            log=lambda *a: None,
        )
        self.assertEqual(sid, "2026-09-26-archive")
        self.assertLessEqual(len(f.calls), archive.MAX_REQUESTS)
        self.assertGreaterEqual(f.delay, archive.MIN_DELAY)
        snap = normalize.load_snapshot(os.path.join(store, "快照", sid))
        self.assertEqual(snap["source"], "archive")
        self.assertFalse(snap["coverage"]["pagination_complete"])
        self.assertIn("products-1.json", snap["archive_timestamps"])

    def test_archive_products_rate_limited_keeps_homepage(self):
        _old, new = ff.scenario("price-drop")
        home = ff.homepage(new.domain, ff.HOME_OLD).encode()
        extra = {
            "url=alpha-outfitters.example/products.json": (
                200,
                self.cdx(
                    [
                        [
                            "20260819053954",
                            "https://alpha-outfitters.example/products.json",
                            "b",
                        ]
                    ]
                ),
            ),
            "url=alpha-outfitters.example/&": (
                200,
                self.cdx(
                    [["20260824202745", "https://alpha-outfitters.example/", "c"]]
                ),
            ),
            "url=alpha-outfitters.example/policies/": (200, self.cdx([])),
            "20260819053954id_": (429, b"slow down"),
            "20260824202745id_": (200, home),
        }
        store = os.path.join(self.tmp, "store")
        import datetime

        sid, _info = archive.capture_archive(
            new.domain,
            store,
            "zh",
            fetcher=FakeFetcher(new, extra),
            today=datetime.date(2026, 10, 27),
            log=lambda *a: None,
        )
        self.assertEqual(sid, "2026-08-24-archive")
        snap = normalize.load_snapshot(os.path.join(store, "快照", sid))
        self.assertEqual((snap["status"], snap["snapshot_id"]), ("partial", sid))

    def test_archive_unreachable(self):
        class Down(object):
            delay = 0

            def get(self, url, accept=None, headers=None):
                return dict(
                    status=None, body=None, final_url=url, elapsed_ms=1, note="timeout"
                )

        sid, info = archive.capture_archive(
            "x.example",
            os.path.join(self.tmp, "s"),
            "zh",
            fetcher=Down(),
            log=lambda *a: None,
        )
        self.assertIsNone(sid)
        self.assertEqual(info["result"], "unknown")


class ArchiveOfflineTest(Tmp):
    def test_offline_notice_is_not_data(self):
        page = b"<html><head><title>Internet Archive: Temporarily Offline</title></head></html>"

        class Offline(object):
            delay = 0

            def get(self, url, accept=None, headers=None):
                return dict(status=200, body=page, final_url=url, elapsed_ms=1, note="")

        sid, info = archive.capture_archive(
            "x.example",
            os.path.join(self.tmp, "s"),
            "zh",
            fetcher=Offline(),
            log=lambda *a: None,
        )
        self.assertIsNone(sid)
        self.assertEqual(info["note"], "archive temporarily offline")

    def test_homepage_only_archive_is_not_compared_on_products(self):
        a, b = ff.write_pair(self.tmp, "archive-baseline")
        raw = os.path.join(a, "原始数据", "raw")
        os.remove(os.path.join(raw, "products-1.json"))
        sa, sb = normalize.write_snapshot(a), normalize.write_snapshot(b)
        d = diffmod.compute_diff(sa, sb)
        self.assertFalse(d["comparable"]["products"])
        md = draft.diff_md(d, {"lang": "zh", "next_due": "2026-11-03"})
        self.assertIn("这一期商品没法比", md)
        self.assertNotIn("上新 0 款", md)


FILL = {
    "zh": "- 本周: 看看 R003 [推测] 依据: 降价超过一成.",
    "en": "- This week: look at R003 [Inferred] Basis: price cut over 10%.",
}


def fill(report_dir, lang):
    raw = os.path.join(report_dir, "原始数据" if lang == "zh" else "data")
    with open(os.path.join(raw, "diff.md"), encoding="utf-8") as fh:
        text = fh.read()
    text = re.sub(r"<(?:待写|to be written)[^>]*>", FILL[lang], text)
    with open(os.path.join(raw, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(text)
    return raw


class EndToEndTest(Tmp):
    def run_demo(self, lang):
        rc, _out = quiet(
            radar.main, ["demo", "--lang", lang, "--out-root", self.tmp, "--quiet"]
        )
        self.assertEqual(rc, 0)
        store = os.path.join(self.tmp, cfg.RADAR_DIR[lang], cfg.DEMO_DOMAIN)
        reports = snapshot.load_index(store)["reports"]
        return os.path.join(store, reports[-1])

    def test_demo_and_delivery(self):
        for lang in ("zh", "en"):
            report = self.run_demo(lang)
            raw = fill(report, lang)
            rc, out = quiet(check_report.main, [report])
            self.assertEqual(rc, 0, out)
            rc, out = quiet(deliver.main, [report, "--no-open", "--html-only"])
            self.assertEqual(rc, 0, out)
            names = sorted(os.listdir(report))
            self.assertEqual(len([n for n in names if n.endswith(".csv")]), 3, names)
            self.assertEqual(len([n for n in names if n.endswith(".html")]), 1, names)
            with open(os.path.join(raw, "delivery.json"), encoding="utf-8") as fh:
                rec = json.load(fh)
            self.assertEqual(
                (rec["status"], rec["lang"], rec["check"]["errors"]),
                ("complete", lang, []),
            )
            with open(os.path.join(raw, "report.md"), encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn(cfg.DEMO_NOTE[lang].split(",")[0], text)
            body = text.split("## 附录" if lang == "zh" else "## Appendix")[0]
            for word in (
                "products.json",
                "myshopify",
                "variant_id",
                "compare_at_price",
            ):
                self.assertNotIn(word, body)

    def test_demo_rerun_overwrites_only_demo(self):
        self.run_demo("zh")
        other = os.path.join(self.tmp, "雷达", "keep.example")
        os.makedirs(other)
        report = self.run_demo("zh")
        self.assertTrue(os.path.isdir(other))
        self.assertTrue(report.endswith("变化报告 2026-10-20 至 2026-10-27"))

    def test_unfilled_draft_fails_check(self):
        report = self.run_demo("zh")
        raw = os.path.join(report, "原始数据")
        shutil.copy(os.path.join(raw, "diff.md"), os.path.join(raw, "report.md"))
        rc, out = quiet(check_report.main, [report])
        self.assertEqual(rc, 1)
        self.assertIn("占位符", out)

    def test_failed_store_report_delivers(self):
        _a, b = ff.write_pair(self.tmp, "not-shopify")
        store = os.path.join(self.tmp, "雷达", "theta-blog.example")
        shutil.copytree(b, os.path.join(store, "快照", "2026-10-27"))
        normalize.write_snapshot(os.path.join(store, "快照", "2026-10-27"))
        rc, _ = quiet(
            radar.main,
            ["diff", "theta-blog.example", "--out-root", self.tmp, "--quiet"],
        )
        self.assertEqual(rc, 0)
        report = os.path.join(store, snapshot.load_index(store)["reports"][-1])
        fill(report, "zh")
        rc, out = quiet(check_report.main, [report])
        self.assertEqual(rc, 0, out)
        rc, out = quiet(deliver.main, [report, "--no-open", "--html-only"])
        with open(
            os.path.join(report, "原始数据", "delivery.json"), encoding="utf-8"
        ) as fh:
            self.assertEqual(json.load(fh)["status"], "failed")

    def test_baseline_mentions_failed_archive(self):
        _a, b = ff.write_pair(self.tmp, "price-drop")
        store = os.path.join(self.tmp, "雷达", "alpha-outfitters.example")
        shutil.copytree(b, os.path.join(store, "快照", "2026-10-27"))
        normalize.write_snapshot(os.path.join(store, "快照", "2026-10-27"))
        with open(
            os.path.join(store, archive.ATTEMPT_FILE), "w", encoding="utf-8"
        ) as fh:
            json.dump({"result": "unknown", "note": "archive unreachable"}, fh)
        quiet(
            radar.main,
            ["diff", "alpha-outfitters.example", "--out-root", self.tmp, "--quiet"],
        )
        report = os.path.join(store, snapshot.load_index(store)["reports"][-1])
        with open(os.path.join(report, "原始数据", "diff.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("网页历史存档里一个月前的版本", text)
        self.assertIn("完成状态: partial", text)

    def test_pick_pair_prefers_complete(self):
        mk = lambda sid, st, t: (sid, sid, {"status": st, "captured_at_utc": t})  # noqa: E731
        snaps = [
            mk("2026-09-27-archive", "partial", "2026-09-27T00:00:00Z"),
            mk("2026-10-20", "complete", "2026-10-20T00:00:00Z"),
            mk("2026-10-25", "blocked", "2026-10-25T00:00:00Z"),
            mk("2026-10-27", "complete", "2026-10-27T00:00:00Z"),
        ]
        a, b = radar.pick_pair(snaps)
        self.assertEqual((a[0], b[0]), ("2026-10-20", "2026-10-27"))
        a, b = radar.pick_pair(snaps[:1] + snaps[3:])
        self.assertEqual(a[0], "2026-09-27-archive")
        a, b = radar.pick_pair(snaps[3:])
        self.assertIsNone(a)


class RulesTest(unittest.TestCase):
    BASE = (
        "# r\n\n## 一页看懂\n\n- 下次建议 2026-11-03 再跑.\n%s\n\n"
        "## 附录: x\n\n- 完成状态: partial\n\n### 本报告没有覆盖的\n"
    )

    def errors(self, line, lang="zh"):
        res = checks.check_text(self.BASE % line, cfg.profile(lang))
        return [i.kind for i in res.errors]

    def test_clean(self):
        self.assertEqual(self.errors("- 上新 2 款 [已核实]"), [])

    def test_sales_claim_needs_inferred(self):
        self.assertIn("sales_claim", self.errors("- 销量下滑了 [已核实]"))
        self.assertEqual(self.errors("- 可能在清库存 [推测] 依据: R003"), [])
        self.assertEqual(self.errors("- 价格变化推不出销量."), [])

    def test_archive_not_verified(self):
        self.assertIn(
            "archive_verified", self.errors("- 来自网页历史存档的降价 [已核实]")
        )
        self.assertEqual(self.errors("- 来自网页历史存档的降价 [页面显示]"), [])

    def test_forbidden_mass_removal(self):
        self.assertIn("forbidden_term", self.errors("- 店铺全部下架了"))

    def test_next_run_required(self):
        text = self.BASE.replace("- 下次建议 2026-11-03 再跑.\n", "") % "- x [已核实]"
        res = checks.check_text(text, cfg.profile("zh"))
        self.assertIn("no_next_run", [i.kind for i in res.errors])

    def test_tech_terms(self):
        self.assertIn("tech_term", self.errors("- 来自 products.json"))
        self.assertIn("tech_term", self.errors("- compare_at_price 变了"))


class PackagingTest(unittest.TestCase):
    def test_demo_assets_match_factory(self):
        tmp = tempfile.mkdtemp(prefix="radar-demo-")
        try:
            ff.write_demo(os.path.join(tmp, "demo"))
            cmp = filecmp.dircmp(os.path.join(tmp, "demo"), ff.DEMO_DIR)
            problems = []

            def walk(c, rel=""):
                problems.extend(
                    os.path.join(rel, x)
                    for x in c.left_only + c.right_only + c.diff_files
                )
                for name, sub in c.subdirs.items():
                    walk(sub, os.path.join(rel, name))

            walk(cmp)
            self.assertEqual(
                problems, [], "run: python tests/fixture_factory.py --write-demo"
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_vendored_teardown_scripts_identical(self):
        teardown = os.path.join(SKILL, "..", "shopify-store-teardown", "scripts")
        if not os.path.isdir(teardown):
            self.skipTest("teardown not next to this skill (zip install)")
        for name in ("collect.py", "summarize.py", "delivery.py"):
            self.assertTrue(
                filecmp.cmp(
                    os.path.join(SCRIPTS, name),
                    os.path.join(teardown, name),
                    shallow=False,
                ),
                "%s drifted from shopify-store-teardown; copy it again" % name,
            )

    def test_fixed_ending_in_templates(self):
        for lang in ("zh", "en"):
            ending = "\n".join(draft.fixed_ending(lang))
            self.assertIn(checks.i18n.text("fixed_ending", lang), ending)
            self.assertIn("competitor-change-radar", ending)
        self.assertEqual(
            check_report.FIXED_ENDING, checks.i18n.text("fixed_ending", "zh")
        )


if __name__ == "__main__":
    unittest.main()
