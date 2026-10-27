"""Synthetic-fixture tests for tracking-audit (offline, stdlib unittest).

Run from the repository root:
    python tools/run_tests.py            (all skills)
    python -m unittest discover -s skills/tracking-audit/tests

Fixtures (all fictional .example domains):
    well_tracked        Shopify, official app pixels only, consent v2, policy names platforms
    duplicated_broken   theme + app pixel same Meta ID, 2 GTM containers, same GA4 ID
                        in GTM and gtag, UA residue, TikTok placeholder, EU markets
    non_shopify         WooCommerce + GTM container with Meta code and an AW- ID
    blocked             challenge page
    unreachable         homepage network error
    consent_inventory   vendor URLs only inside consent / blocking lists (no tags)
"""

import contextlib
import csv
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "scripts"))

import audit  # noqa: E402
import check_report  # noqa: E402
import collect  # noqa: E402
import deliver  # noqa: E402
import detect  # noqa: E402
import draft  # noqa: E402
import skill_config as cfg  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures")
META_ID = "123456789012345"


def quiet(fn, *args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(*args)
    return rc, out.getvalue() + err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tracking-audit-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_fixture(self, name, lang="zh", answers=None, mode="own"):
        self.n = getattr(self, "n", 0) + 1
        run = os.path.join(self.tmp, "%s-%d %s 2026-10-27" % (name, self.n, lang))
        data = os.path.join(run, "原始数据" if lang == "zh" else "data")
        shutil.copytree(os.path.join(FIXTURES, name, "raw"), os.path.join(data, "raw"))
        if answers is not None:
            os.makedirs(os.path.join(data, "user-inputs"))
            with open(os.path.join(data, "user-inputs", "answers.json"), "w") as fh:
                json.dump(answers, fh)
        rc, out = quiet(audit.main, [run, "--lang", lang, "--mode", mode])
        self.assertEqual(rc, 0, out)
        with open(os.path.join(data, "summary.json"), encoding="utf-8") as fh:
            return run, data, json.load(fh)

    @staticmethod
    def rows(summary, cid, platform=None):
        return [
            r
            for r in summary["checks"]
            if r["id"] == cid and (platform is None or r["platform"] == platform)
        ]

    def one(self, summary, cid, platform=None):
        rows = self.rows(summary, cid, platform)
        self.assertEqual(len(rows), 1, (cid, platform, rows))
        return rows[0]


class ScenarioTest(Base):
    def test_well_tracked(self):
        _run, _data, s = self.run_fixture("well_tracked")
        self.assertEqual(s["status"], "complete")
        self.assertEqual(s["identity"]["status"], "confirmed")
        tags = {(t["platform"], t["id"]) for t in s["tags"]}
        self.assertIn(("meta", META_ID), tags)
        self.assertIn(("ga4", "G-ALPHA12345"), tags)
        self.assertIn(("google_ads", "AW-987654321"), tags)
        for cid in ("T01", "T02", "T04"):
            r = self.one(s, cid)
            self.assertEqual((r["result"], r["badge_key"]), ("pass", "on_page"))
        for cid in ("D01", "D02", "D03", "D04"):
            self.assertEqual(self.one(s, cid)["result"], "pass", cid)
        self.assertEqual(self.one(s, "P02")["result"], "pass")
        for r in s["checks"]:
            if r["id"].startswith("S"):
                self.assertEqual(r["result"], "needs_input")
        # Klaviyo app pixel + onsite script is the normal setup, not a duplicate
        self.assertFalse(self.rows(s, "D01", "klaviyo"))

    def test_tokens_never_leave_the_raw_page(self):
        run, data, s = self.run_fixture("well_tracked")
        for name in ("summary.json", "report.draft.md", "evidence.jsonl"):
            with open(os.path.join(data, name), encoding="utf-8") as fh:
                self.assertNotIn("SECRET-TOKEN", fh.read(), name)

    def test_duplicated_broken(self):
        _run, _data, s = self.run_fixture("duplicated_broken")
        d01 = {r["platform"]: r for r in self.rows(s, "D01")}
        self.assertEqual(d01["meta"]["severity"], "high")
        self.assertEqual(d01["meta"]["badge_key"], "inferred")
        self.assertIn("ga4", d01)
        d02 = self.one(s, "D02", "gtm")
        self.assertEqual((d02["result"], d02["severity"]), ("issue", "medium"))
        d03 = self.one(s, "D03")
        self.assertEqual((d03["result"], d03["severity"]), ("issue", "medium"))
        d04 = self.one(s, "D04")
        self.assertEqual(
            (d04["result"], d04["badge_key"], d04["severity"]),
            ("issue", "verified", "low"),
        )
        t10 = self.one(s, "T10", "tiktok")
        self.assertEqual((t10["result"], t10["severity"]), ("issue", "high"))
        self.assertIn("YOUR_PIXEL_ID", [p["id"] for p in s["problems"]])
        self.assertIn("eu_uk", s["regions"])
        self.assertEqual(self.one(s, "P01")["result"], "issue")
        d06 = self.one(s, "D06")
        self.assertEqual((d06["result"], d06["badge_key"]), ("issue", "inferred"))
        self.assertIn("UA-12345-1", s["legacy_ua"])

    def test_non_shopify(self):
        _run, _data, s = self.run_fixture("non_shopify")
        self.assertEqual(s["identity"]["status"], "not_shopify")
        self.assertEqual(s["identity"]["other_platform"], "WooCommerce")
        self.assertIn("not_shopify", s["warnings"])
        self.assertEqual(self.one(s, "T09")["result"], "na")
        self.assertFalse(self.rows(s, "D05"))
        t03 = self.one(s, "T03")
        self.assertIn("AW-1234567890", t03["basis"])
        self.assertEqual(t03["badge_key"], "on_page")
        t01 = self.one(s, "T01")  # Meta code inside a container: uncalibrated
        self.assertEqual((t01["result"], t01["badge_key"]), ("pass", "inferred"))
        self.assertIn("依据", t01["basis"])

    def test_blocked(self):
        _run, _data, s = self.run_fixture("blocked")
        self.assertEqual(s["status"], "partial")
        self.assertEqual(s["identity"]["status"], "unknown")
        self.assertEqual(self.one(s, "G02")["result"], "issue")
        self.assertTrue(any(w.startswith("blocked") for w in s["warnings"]))

    def test_unreachable_is_failed_short_report(self):
        run, data, s = self.run_fixture("unreachable")
        self.assertEqual(s["status"], "failed")
        self.assertIn("homepage_unavailable", s["warnings"])
        with open(os.path.join(data, "report.draft.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("首页打不开", text)
        self.assertNotIn("## 重复与过时", text)
        rc, out = quiet(
            deliver.main, [run, "--html-only", "--no-open"]
        )  # draft fallback
        self.assertEqual(rc, 0, out)
        with open(os.path.join(data, "delivery.json"), encoding="utf-8") as fh:
            rec = json.load(fh)
        self.assertEqual(rec["status"], "failed")
        self.assertTrue(rec["check"]["ok"], rec["check"])

    def unreachable_with_probe(self, probe, lang="zh"):
        run = os.path.join(self.tmp, "probe %s 2026-10-27" % lang)
        data = os.path.join(run, "原始数据" if lang == "zh" else "data")
        shutil.copytree(
            os.path.join(FIXTURES, "unreachable", "raw"), os.path.join(data, "raw")
        )
        path = os.path.join(data, "raw", "_status.json")
        with open(path, encoding="utf-8") as fh:
            status = json.load(fh)
        status["network_probe"] = probe
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(status, fh)
        rc, out = quiet(audit.main, [run, "--lang", lang, "--mode", "other"])
        self.assertEqual(rc, 0, out)
        with open(os.path.join(data, "summary.json"), encoding="utf-8") as fh:
            s = json.load(fh)
        with open(os.path.join(data, "report.draft.md"), encoding="utf-8") as fh:
            return run, data, s, fh.read(), out

    def test_network_unavailable_draft_is_deliverable(self):
        probe = {"url": collect.PROBE_URL, "http_status": None, "result": "error"}
        for lang in ("zh", "en"):
            run, data, s, text, out = self.unreachable_with_probe(probe, lang)
            self.assertEqual(s["status"], "failed")
            self.assertIn("network_unavailable", s["warnings"])
            self.assertNotIn("site_unreachable", s["warnings"])
            self.assertIn("deliver.py", out)
            self.assertIn("连不上网" if lang == "zh" else "no network access", text)
            rc, out = quiet(deliver.main, [run, "--html-only", "--no-open"])
            self.assertEqual(rc, 0, out)
            with open(os.path.join(data, "delivery.json"), encoding="utf-8") as fh:
                rec = json.load(fh)
            self.assertEqual(rec["status"], "failed")
            self.assertTrue(rec["check"]["ok"], rec["check"])
            shutil.rmtree(run)

    def test_site_unreachable_when_probe_answers(self):
        probe = {"url": collect.PROBE_URL, "http_status": 404, "result": "answered"}
        _run, _data, s, text, _out = self.unreachable_with_probe(probe)
        self.assertIn("site_unreachable", s["warnings"])
        self.assertNotIn("network_unavailable", s["warnings"])
        self.assertIn("首页打不开", text)

    def test_consent_inventory_is_not_an_install(self):
        _run, _data, s = self.run_fixture("consent_inventory")
        platforms = {t["platform"] for t in s["tags"]}
        for p in ("tiktok", "snap", "pinterest", "meta"):
            self.assertNotIn(p, platforms)

    def test_other_store_mode(self):
        _run, data, s = self.run_fixture("well_tracked", mode="other")
        self.assertEqual(s["mode"], "other")
        self.assertFalse(self.rows(s, "D05"))
        self.assertFalse(os.path.exists(os.path.join(data, "user-inputs")))
        for r in self.rows(s, "S01") + self.rows(s, "C01"):
            self.assertEqual(r["needs_user"], "")
        with open(os.path.join(data, "report.draft.md"), encoding="utf-8") as fh:
            self.assertIn("公开页面观察", fh.read())


class UserInputTest(Base):
    def test_intake_files(self):
        _run, data, s = self.run_fixture("well_tracked")
        ui = os.path.join(data, "user-inputs")
        self.assertTrue(os.path.isfile(os.path.join(ui, "请你提供.md")))
        with open(os.path.join(ui, "answers.template.json"), encoding="utf-8") as fh:
            tpl = json.load(fh)
        self.assertIn("U1-c", tpl["items"])
        self.assertIn("U1-c", s["user_inputs"]["needed"])

    def test_screenshot_verifies_and_mismatch_conflicts(self):
        good = {
            "ad_platforms": ["meta"],
            "items": {
                "U1-c": {
                    "source": "screenshot",
                    "file": "user-inputs/u1-c.png",
                    "date": "2026-10-26",
                    "ok": True,
                    "ids": {"meta": [META_ID]},
                }
            },
        }
        _run, _data, s = self.run_fixture("well_tracked", answers=good)
        c01 = self.one(s, "C01", "meta")
        self.assertEqual((c01["result"], c01["badge_key"]), ("pass", "verified"))
        self.assertIn("u1-c.png", c01["basis"])
        self.assertEqual(self.one(s, "T01")["badge_key"], "verified")
        bad = {
            "items": {
                "U1-c": dict(good["items"]["U1-c"], ids={"meta": ["999999999999999"]})
            }
        }
        _run, _data, s = self.run_fixture("well_tracked", answers=bad)
        self.assertEqual(self.one(s, "C01", "meta")["badge_key"], "conflicting")
        self.assertEqual(self.one(s, "T01")["badge_key"], "conflicting")

    def test_said_is_store_claim_and_ad_gap(self):
        ans = {
            "ad_platforms": ["meta", "tiktok"],
            "items": {"U1-b": {"source": "said", "ok": True}},
        }
        _run, _data, s = self.run_fixture("well_tracked", answers=ans)
        self.assertEqual(self.one(s, "S01")["badge_key"], "self_claim")
        t11 = self.one(s, "T11")
        self.assertEqual((t11["result"], t11["severity"]), ("issue", "high"))
        self.assertEqual(self.one(s, "T05")["severity"], "high")


class DetectTest(unittest.TestCase):
    def setUp(self):
        self.sigs = detect.load_signatures()

    def test_signature_file_fresh_and_calibration_flags(self):
        self.assertEqual(self.sigs["valid_as_of"][:4], "2026")
        statuses = {
            (s["vendor"], s["kind"]): s["status"] for s in self.sigs["signatures"]
        }
        self.assertEqual(statuses[("meta", "inline_call")], "uncalibrated")
        self.assertEqual(statuses[("meta", "shopify_app_pixel")], "calibrated")

    def test_placeholders(self):
        for bad in ("YOUR_PIXEL_ID", "XXXXXXX", "", "G-XXXXXX", "{{ pixel_id }}"):
            self.assertTrue(detect.is_placeholder(bad, self.sigs), bad)
        self.assertFalse(detect.is_placeholder("CDHD4TBC77U8IR886OAG", self.sigs))

    def test_bad_format_and_escaped_code(self):
        page = "<script>fbq(\\'init\\', \\'12345\\');ttq.load('C4D2JTMJGOC88M156KBG');</script>"
        hits, _inv, _info = detect.scan_page(page, self.sigs, "raw/x.html")
        meta = [h for h in hits if h["platform"] == "meta"][0]
        self.assertEqual(meta["problem"], "bad_format")
        tt = [h for h in hits if h["platform"] == "tiktok"][0]
        self.assertEqual(
            (tt["id"], tt["problem"], tt["calibrated"]),
            ("C4D2JTMJGOC88M156KBG", None, False),
        )

    def test_code_context(self):
        good = '<script src="https://connect.facebook.net/en_US/fbevents.js"></script>'
        bad = '{"Tag":"https://connect.facebook.net/en_US/fbevents.js","CategoryId":["C0004"]}'
        self.assertTrue(detect.scan_page(good, self.sigs, "x")[0])
        self.assertFalse(detect.scan_page(bad, self.sigs, "x")[0])

    def test_undecodable_pixel_list_falls_back_to_regex(self):
        page = (
            'webPixelsConfigList: [{"configuration":"{\\"pixel_id\\":\\"%s\\",'
            '\\"pixel_type\\":\\"facebook_pixel\\"}", broken' % META_ID
        )
        hits, _inv, info = detect.scan_page(page, self.sigs, "x")
        self.assertEqual(info["web_pixels"], "undecodable")
        self.assertIn(META_ID, [h["id"] for h in hits])

    def test_container_ids_and_regions(self):
        page = "(window,document,'script','dataLayer','GTM-ABCD12');<script src='https://www.googletagmanager.com/gtag/js?id=G-ZZZZ1111'>"
        self.assertEqual(detect.container_ids(page), (["GTM-ABCD12"], ["G-ZZZZ1111"]))
        self.assertEqual(
            detect.regions({"hreflang": ["en-gb", "en-ca"], "currency": "USD"}),
            ["ca", "eu_uk", "us"],
        )


class CollectTest(unittest.TestCase):
    def test_link_picking_skips_checkout(self):
        html = (
            '<a href="/checkouts/abc">x</a><a href="/products/mug?variant=1">m</a>'
            '<a href="https://other.example/products/x">o</a><a href="/collections/all">a</a>'
            '<a href="/collections/mugs">c</a><a href="/policies/privacy-policy">Privacy</a>'
        )
        urls = collect.links(html, "https://shop.example/", "shop.example")
        self.assertFalse(any("checkouts" in u for u in urls))
        self.assertEqual(
            collect.pick(urls, collect.PRODUCT_RE), "https://shop.example/products/mug"
        )
        self.assertEqual(
            collect.pick(urls, collect.COLLECTION_RE, skip=("/collections/all",)),
            "https://shop.example/collections/mugs",
        )
        self.assertEqual(
            collect.privacy_link(html, "https://shop.example/", "shop.example"),
            "https://shop.example/policies/privacy-policy",
        )

    def test_offline_collect_records_failures(self):
        class Dead(object):
            cert_note = ""

            def get(self, url, accept=None, headers=None):
                return {
                    "status": None,
                    "body": None,
                    "final_url": url,
                    "elapsed_ms": 1,
                    "note": "network error",
                }

        tmp = tempfile.mkdtemp(prefix="tracking-audit-test-")
        try:
            status = collect.collect("dead.example", os.path.join(tmp, "d"), Dead())
            self.assertEqual(status["requests"][0]["result"], "error")
            self.assertEqual(status["network_probe"]["result"], "error")
            self.assertTrue(
                os.path.isfile(os.path.join(tmp, "d", "raw", "_status.json"))
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class RegressionTest(Base):
    """False positives found on real stores (2026-10-27), rebuilt synthetically."""

    def test_prefixless_gtm_and_merchant_center(self):
        sigs = detect.load_signatures()
        page = "(window,document,'script','dataLayer','KV427CV');"
        hits = detect.scan_page(page, sigs, "x")[0]
        self.assertEqual(
            [(h["id"], h["problem"]) for h in hits], [("GTM-KV427CV", None)]
        )
        cfg_json = json.dumps(
            {"config": json.dumps({"google_tag_ids": ["GT-ABCD1234", "MC-ZZZZ12345"]})}
        )
        hits, _inv = detect.app_pixel_hits(
            [{"type": "APP", "apiClientId": 1780363, "configuration": cfg_json}],
            sigs,
            "x",
        )
        self.assertEqual([h["id"] for h in hits], ["GT-ABCD1234"])

    def test_script_twice_or_own_gtag_script_is_not_duplicate(self):
        run, data, _s = self.run_fixture("well_tracked")
        path = os.path.join(data, "raw", "homepage.html")
        with open(path, encoding="utf-8") as fh:
            html = fh.read()
        extra = (
            '<link rel="preload" as="script" href="https://static.klaviyo.com/onsite/js/AbC123/klaviyo.js?company_id=AbC123">'
            '<script async src="https://static.klaviyo.com/onsite/js/AbC123/klaviyo.js?company_id=AbC123"></script>'
            "<script async src='https://www.googletagmanager.com/gtag/js?id=AW-555555555'></script>"
        )
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html.replace("</head>", extra + "</head>", 1))
        with open(os.path.join(data, "raw", "container-AW-555555555.js"), "w") as fh:
            fh.write('var x={"vtp_conversionId":"AW-555555555","id":"AW-555555555"};')
        rc, out = quiet(audit.main, [run])
        self.assertEqual(rc, 0, out)
        with open(os.path.join(data, "summary.json"), encoding="utf-8") as fh:
            s = json.load(fh)
        self.assertEqual(
            [r["result"] for r in s["checks"] if r["id"] == "D01"], ["pass"]
        )


class ReportTest(Base):
    def test_drafts_pass_self_check_and_deliver_both_languages(self):
        for name in ("well_tracked", "duplicated_broken", "non_shopify", "blocked"):
            for lang in ("zh", "en"):
                run, data, _s = self.run_fixture(name, lang)
                shutil.copy(
                    os.path.join(data, "report.draft.md"),
                    os.path.join(data, "report.md"),
                )
                rc, out = quiet(check_report.main, [run, "--json"])
                self.assertEqual(rc, 0, (name, lang, out))
                rc, out = quiet(deliver.main, [run, "--html-only", "--no-open"])
                self.assertEqual(rc, 0, out)
                csv_name = cfg.CSV_TABLES[0]["name"][lang]
                with open(os.path.join(run, csv_name), encoding="utf-8-sig") as fh:
                    rows = list(csv.reader(fh))
                self.assertEqual(rows[0][0], "检查编号" if lang == "zh" else "Check ID")
                self.assertEqual(len(rows[0]), 12)
                with open(os.path.join(data, "report.md"), encoding="utf-8") as fh:
                    text = fh.read()
                body = text.split("## 附录" if lang == "zh" else "## Appendix")[0]
                for word in (
                    "fbq",
                    "gtag(",
                    "dataLayer",
                    "webPixelsConfigList",
                    "myshopify",
                    "GTM-",
                ):
                    self.assertNotIn(word, body, (name, lang, word))

    def test_rules_catch_overclaims(self):
        run, data, _s = self.run_fixture("well_tracked")
        bad = (
            "## 一页看懂\n\n- Meta 像素在正常工作 [页面显示]\n- TikTok 没装 [查不到]\n"
            "- Meta 像素已装好 [已核实]\n- 这家店不合规\n\n## 附录\n\n- 完成状态: complete\n\n"
            + draft.FIXED_ENDING["zh"]
        )
        with open(os.path.join(data, "report.md"), "w", encoding="utf-8") as fh:
            fh.write(bad)
        rc, out = quiet(check_report.main, [run, "--json"])
        self.assertEqual(rc, 1)
        res = json.loads(out)
        kinds = {e["kind"] for e in res["errors"]}
        self.assertTrue(
            {"code_as_working", "not_seen_as_absent", "forbidden_term"} <= kinds, kinds
        )
        self.assertIn("over_label", {w["kind"] for w in res["warnings"]})

    def test_fixed_ending_marker_in_both_languages(self):
        for lang in ("zh", "en"):
            self.assertIn(check_report.FIXED_ENDING, draft.FIXED_ENDING[lang])
            path = os.path.join(SKILL, "references", "report-template.%s.md" % lang)
            with open(path, encoding="utf-8") as fh:
                self.assertIn(draft.FIXED_ENDING[lang].strip(), fh.read())

    def test_csv_formula_injection(self):
        run, data, s = self.run_fixture("well_tracked")
        s["checks"][0]["basis"] = "=HYPERLINK(1)"
        with open(os.path.join(data, "summary.json"), "w", encoding="utf-8") as fh:
            json.dump(s, fh)
        shutil.copy(
            os.path.join(data, "report.draft.md"), os.path.join(data, "report.md")
        )
        quiet(deliver.main, [run, "--html-only", "--no-open"])
        with open(os.path.join(run, "体检清单.csv"), encoding="utf-8-sig") as fh:
            self.assertIn("'=HYPERLINK(1)", fh.read())


if __name__ == "__main__":
    unittest.main()
