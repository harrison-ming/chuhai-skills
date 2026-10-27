"""Synthetic-fixture tests for payments-readiness (offline, stdlib unittest).

Run from the repository root:
    python tools/run_tests.py
    python -m unittest discover -s skills/payments-readiness/tests

Scenarios (spec 7.1): healthy, broken, non_shopify, blocked, offline_quote;
plus every rule positive / negative, the fee golden example, the collector
with a fake network, the report self-check rules and delivery in zh / en.
"""

import contextlib
import datetime
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

import check_report  # noqa: E402
import collect  # noqa: E402
import compute  # noqa: E402
import deliver  # noqa: E402
import draft  # noqa: E402
import fees  # noqa: E402
import pr_checks  # noqa: E402
import questionnaire as Q  # noqa: E402
import signals  # noqa: E402
import skill_config as cfg  # noqa: E402
from _shared import checks  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures")
TODAY = datetime.date(2026, 10, 27)


def quiet(fn, *args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(*args)
    return rc, out.getvalue() + err.getvalue()


def golden_q(**extra):
    q = Q.normalize(
        {
            "entity_country": "CN",
            "shopify_plan": "basic",
            "aov": 45,
            "aov_currency": "USD",
            "monthly_orders": 300,
            "withdrawals_per_month": 1,
            "payout_account": "cn_bank",
            "gateways": ["paypal:40", "card_other:60:3.5+0.30"],
        }
    )
    for k, v in extra.items():
        Q.set_value(q, k, v)
    return q


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="payments-readiness-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_fixture(self, name, lang="zh"):
        label = cfg.RUN_LABEL[lang]
        run = os.path.join(self.tmp, "%s %s 2026-10-27" % (name, label))
        data = os.path.join(run, "原始数据" if lang == "zh" else "data")
        shutil.copytree(os.path.join(FIXTURES, name, "raw"), os.path.join(data, "raw"))
        qf = os.path.join(FIXTURES, name, "questionnaire.json")
        if os.path.isfile(qf):
            shutil.copy(qf, data)
        rc, out = quiet(compute.main, [run, "--lang", lang])
        self.assertEqual(rc, 0, out)
        res = {"run": run, "data": data}
        for key in ("summary", "findings", "fees"):
            path = os.path.join(data, key + ".json")
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as fh:
                    res[key] = json.load(fh)
        res["by_id"] = {}
        for f in res["findings"]:
            res["by_id"].setdefault(f["id"], []).append(f)
        return res

    def issue(self, res, fid):
        return [f for f in res["by_id"].get(fid, []) if f["result"] == "issue"]

    def result(self, res, fid):
        return [f["result"] for f in res["by_id"].get(fid, [])]


class ScenarioTest(Base):
    def test_healthy(self):
        r = self.run_fixture("healthy")
        s = r["summary"]
        self.assertEqual(s["status"], "complete")
        self.assertEqual((s["counts"]["P0"], s["counts"]["P1"]), (0, 0), r["findings"])
        self.assertEqual(s["identity"]["status"], "confirmed")
        for row in r["fees"]["rows"]:
            self.assertIn(row["status"], ("verified", "user", "unknown", "inferred"))
            if row["status"] == "verified":
                self.assertTrue(row["source_id"])
        # Shopify Payments primary: no third-party fee row on PayPal
        self.assertFalse(
            [x for x in r["fees"]["rows"] if x["item_key"] == "platform_fee"]
        )
        for fid in (
            "S01",
            "S06",
            "P01",
            "P02",
            "P03",
            "P04",
            "P05",
            "P07",
            "T01",
            "T02",
            "T03",
            "T04",
            "E04",
            "E05",
        ):
            self.assertIn("ok", self.result(r, fid), fid)

    def test_broken(self):
        r = self.run_fixture("broken")
        for fid in (
            "P05",
            "T01",
            "S06",
            "M03",
            "E01",
            "E02",
            "E04",
            "P04",
            "P02",
            "T04",
        ):
            self.assertTrue(self.issue(r, fid), fid)
        self.assertEqual(self.issue(r, "E02")[0]["badge"], "conflicting")
        self.assertEqual(self.issue(r, "S06")[0]["badge"], "conflicting")
        self.assertEqual(self.issue(r, "P05")[0]["badge"], "conflicting")
        self.assertIn("14", self.issue(r, "P05")[0]["finding"])
        # unverified China Shopify Payments rate is never used as a default
        sp = [x for x in r["fees"]["rows"] if x["route_key"] == "shopify_payments"][0]
        self.assertIsNone(sp["monthly"])
        self.assertEqual(sp["status"], "unknown")
        with open(os.path.join(r["data"], "report-draft.md"), encoding="utf-8") as fh:
            text = fh.read()
        body = text.split("## 附录")[0]
        self.assertNotIn("已开通 [", body)

    def test_non_shopify(self):
        r = self.run_fixture("non_shopify")
        s = r["summary"]
        self.assertEqual(s["status"], "partial")
        self.assertEqual(s["identity"]["status"], "not_shopify")
        self.assertIn("not_shopify", s["warnings"])
        self.assertEqual(r["by_id"]["S02"][0]["badge"], "inferred")
        self.assertIn("Stripe", [x["name"] for x in s["signals"]["payment_scripts"]])
        self.assertIn("ok", self.result(r, "T01"))
        p01 = self.issue(r, "P01")[0]
        self.assertIn("配送政策", p01["finding"])
        self.assertNotIn("联系信息页", p01["finding"])
        self.assertIn("ok", self.result(r, "P03") + self.result(r, "P05"))

    def test_blocked(self):
        r = self.run_fixture("blocked")
        s = r["summary"]
        self.assertEqual(s["status"], "failed")
        self.assertIn("homepage_unavailable", s["warnings"])
        self.assertIn("blocked", s["warnings"])
        self.assertEqual(self.result(r, "S01"), ["unknown"])
        self.assertEqual([f for f in r["findings"] if f["result"] == "issue"], [])
        self.assertTrue(s["fix_list"])  # CSV still has the open items

    def test_offline_quote_golden(self):
        r = self.run_fixture("offline_quote")
        f = r["fees"]
        self.assertEqual(
            (f["monthly_total"], f["yearly_total"], f["pct_of_sales"]),
            (916.10, 10993.20, 6.79),
        )
        self.assertEqual(r["summary"]["status"], "partial")
        fx = [x for x in f["rows"] if x["item_key"] == "payout_fx"][0]
        self.assertEqual(fx["status"], "unknown")
        total = r["summary"]["fee_rows"][-1]
        self.assertEqual(total["yearly"], "10993.20")


class FeesTest(unittest.TestCase):
    def rows(self, res, key):
        return [r for r in res["rows"] if r["item_key"] == key]

    def test_golden(self):
        res = fees.compute(golden_q(), today=TODAY)
        self.assertEqual(str(res["monthly_total"]), "916.10")
        self.assertEqual(str(res["yearly_total"]), "10993.20")
        self.assertEqual(str(res["pct_of_sales"]), "6.79")
        amounts = sorted(
            str(r["monthly"]) for r in res["rows"] if r["monthly"] is not None
        )
        self.assertEqual(amounts, ["270.00", "273.60", "337.50", "35.00"])
        self.assertFalse(res["rates_stale"])

    def test_unverified_rate_rejected_and_user_rate_used(self):
        q = golden_q(gateways=["shopify_payments:100"])
        res = fees.compute(q, today=TODAY)
        sp = self.rows(res, "processing")[0]
        self.assertIsNone(sp["monthly"])
        self.assertEqual(sp["status"], "unknown")
        q = golden_q(gateways=["shopify_payments:100:3.2+0.30"])
        sp = self.rows(fees.compute(q, today=TODAY), "processing")[0]
        self.assertEqual(sp["status"], "user")
        self.assertEqual(str(sp["monthly"]), "522.00")  # 300 x (1.44 + 0.30)

    def test_us_stripe_surcharges_and_inferred_intl(self):
        q = Q.normalize(
            {
                "entity_country": "US",
                "shopify_plan": "grow",
                "aov": 100,
                "aov_currency": "USD",
                "monthly_orders": 100,
                "market_mix": "US:80,CA:20",
                "gateways": ["stripe:100"],
            }
        )
        res = fees.compute(q, today=TODAY)
        self.assertEqual(str(self.rows(res, "processing")[0]["monthly"]), "320.00")
        intl = self.rows(res, "intl")[0]
        self.assertEqual((str(intl["monthly"]), intl["status"]), ("30.00", "inferred"))
        self.assertFalse(self.rows(res, "fx"))  # USD buyers, USD settlement
        self.assertEqual(str(self.rows(res, "platform_fee")[0]["monthly"]), "100.00")

    def test_paypal_exempt_with_shopify_payments(self):
        q = Q.normalize(
            {
                "entity_country": "US",
                "shopify_plan": "basic",
                "aov": 50,
                "monthly_orders": 10,
                "aov_currency": "USD",
                "gateways": ["shopify_payments:80", "paypal:20"],
            }
        )
        res = fees.compute(q, today=TODAY)
        self.assertFalse(self.rows(res, "platform_fee"))
        self.assertTrue(res["assumptions"])

    def test_currency_mismatch_and_missing_inputs(self):
        q = Q.normalize(
            {
                "entity_country": "CA",
                "aov": 50,
                "aov_currency": "USD",
                "monthly_orders": 10,
                "gateways": ["stripe:100"],
                "shopify_plan": "none",
            }
        )
        res = fees.compute(q, today=TODAY)
        self.assertEqual(self.rows(res, "processing")[0]["status"], "unknown")
        self.assertFalse(self.rows(res, "platform_fee"))
        res = fees.compute(Q.normalize({"aov": 10}), today=TODAY)
        self.assertEqual(res["status"], "not_computed")
        self.assertIn("monthly_orders", res["missing"])

    def test_stale_snapshot(self):
        res = fees.compute(golden_q(), today=TODAY + datetime.timedelta(days=120))
        self.assertTrue(res["rates_stale"])

    def test_csv_rows_have_sources(self):
        rows = fees.csv_rows(fees.compute(golden_q(), today=TODAY), "zh")
        self.assertEqual(rows[-1]["monthly"], "916.10")
        for r in rows[:-1]:
            self.assertIn(r["verified"], ("已核实", "用户填写", "查不到"))

    def test_shipped_rates_are_dated(self):
        data = fees.load_rates()
        self.assertEqual(data["valid_as_of"], "2026-10-27")
        for r in data["rates"]:
            self.assertIn(r["status"], ("verified", "unverified", "conflict"), r["id"])
            if r["status"] == "verified":
                self.assertTrue(r["source_url"].startswith("https://"), r["id"])


class QuestionnaireTest(Base):
    def test_parse_gateway_and_aliases(self):
        g = Q.parse_gateway("card:60:3.5+0.30:35")
        self.assertEqual(
            (g["provider"], g["share"], g["pct"], g["fixed"], g["withdraw_fee"]),
            ("card_other", 60.0, 3.5, 0.3, 35.0),
        )
        q = Q.empty()
        Q.set_value(q, "entity_country", "大陆")
        Q.set_value(q, "market_mix", "US:70,gb:30")
        self.assertEqual(q["entity_country"], "CN")
        self.assertEqual(q["market_mix"], {"US": 70, "UK": 30})
        with self.assertRaises(Q.QuestionnaireError):
            Q.set_value(q, "shopify_plan", "gold")
        with self.assertRaises(Q.QuestionnaireError):
            Q.parse_gateway("bitcoin:10")

    def test_cli_writes_and_recomputes(self):
        r = self.run_fixture("blocked")
        rc, out = quiet(
            Q.main,
            [
                r["run"],
                "--set",
                "aov=45",
                "--set",
                "monthly_orders=300",
                "--set",
                "entity_country=CN",
                "--set",
                "shopify_plan=basic",
                "--gateway",
                "paypal:100",
            ],
        )
        self.assertEqual(rc, 0, out)
        with open(os.path.join(r["data"], "fees.json"), encoding="utf-8") as fh:
            self.assertNotEqual(json.load(fh)["status"], "not_computed")
        rc, out = quiet(Q.main, [r["run"], "--set", "nonsense=1"])
        self.assertEqual(rc, 2)


def facts(**over):
    f = {
        "mode": "online",
        "endpoints": {
            "homepage": {
                "result": "ok",
                "url": "https://x.example/",
                "final_url": "https://x.example/",
            }
        },
        "home_raw": "",
        "home_text": "",
        "identity": "confirmed",
        "markers": [],
        "meta": None,
        "shop_name": "X",
        "base_currency": "USD",
        "meta_country": None,
        "ships_to": None,
        "market": {"country": None, "currency_active": None},
        "product_stats": {"count": 0},
        "policies": {},
        "policies_seen": False,
        "cart": {"state": "missing", "checkout_button": False, "empty": False},
        "product_pages": [],
        "contact": None,
        "faq": None,
        "other_texts": [],
        "icons": [],
        "payment_signatures": [],
        "apps": [],
        "hreflang": [],
        "localization": False,
    }
    f.update(over)
    return f


def pol(text):
    return {
        "state": "ok",
        "text": text,
        "raw": "<p>%s</p>" % text,
        "chars": len(text),
        "file": "x",
    }


class RuleTest(unittest.TestCase):
    def ids(self, f, q=None, result="issue"):
        return {
            x["id"]
            for x in pr_checks.evaluate(f, q or {}, "zh")
            if x["result"] == result
        }

    def test_s01_http_vs_https(self):
        ok = facts()
        bad = facts(
            endpoints={
                "homepage": {
                    "result": "ok",
                    "url": "http://x.example/",
                    "final_url": "http://x.example/",
                }
            }
        )
        self.assertNotIn("S01", self.ids(ok))
        self.assertIn("S01", self.ids(bad))

    def test_s03_cart_404_only_on_shopify(self):
        cart404 = {"state": "not_found", "checkout_button": False, "empty": False}
        theme = ["cdn.shopify.com", "shopify.theme"]
        self.assertIn("S03", self.ids(facts(cart=cart404, markers=theme)))
        # headless storefronts keep their cart elsewhere: not an issue
        self.assertNotIn(
            "S03", self.ids(facts(cart=cart404, markers=["cdn.shopify.com"]))
        )
        empty = {"state": "ok", "checkout_button": False, "empty": True}
        self.assertNotIn("S03", self.ids(facts(cart=empty)))

    def test_s06_conflicts(self):
        q = Q.normalize({"gateways": ["paypal:100"]})
        self.assertIn("S06", self.ids(facts(icons=["visa", "paypal"]), q))
        self.assertNotIn("S06", self.ids(facts(icons=["paypal"]), q))

    def test_s08_bnpl_markets(self):
        sig = [
            {"name": "Klarna", "pattern": "klarna.com", "source": "raw/homepage.html"}
        ]
        self.assertIn(
            "S08",
            self.ids(
                facts(payment_signatures=sig),
                Q.normalize({"market_mix": "US:50,DE:50"}),
            ),
        )
        self.assertNotIn(
            "S08",
            self.ids(
                facts(payment_signatures=sig), Q.normalize({"market_mix": "US:100"})
            ),
        )

    def test_s09_payment_statement(self):
        self.assertIn(
            "S09",
            self.ids(facts(policies={"privacy-policy": pol("We use your email.")})),
        )
        self.assertNotIn(
            "S09",
            self.ids(
                facts(
                    policies={
                        "privacy-policy": pol(
                            "Payment information is handled by our payment processor."
                        )
                    }
                )
            ),
        )

    def test_p01_missing_refund_is_p0(self):
        f = facts(policies={"refund-policy": {"state": "not_found", "text": ""}})
        p01 = [
            x
            for x in pr_checks.evaluate(f, {}, "zh")
            if x["id"] == "P01" and x["result"] == "issue"
        ][0]
        self.assertEqual(p01["severity"], "P0")
        f = facts(policies={"refund-policy": {"state": "blocked", "text": ""}})
        self.assertNotIn("P01", self.ids(f))

    def test_p02_template_placeholder(self):
        text = "Returns within 30 days at [your company name]. " * 10
        self.assertIn("P02", self.ids(facts(policies={"refund-policy": pol(text)})))
        self.assertNotIn(
            "P02",
            self.ids(
                facts(policies={"refund-policy": pol("Returns within 30 days. " * 12)})
            ),
        )

    def test_p03_refund_elements(self):
        self.assertIn(
            "P03",
            self.ids(
                facts(
                    policies={
                        "refund-policy": pol("We are happy to help with refunds.")
                    }
                )
            ),
        )
        full = (
            "Returns within 30 days. Items must be unused. Refund to the original payment method "
            "within 5 business days. Return shipping is paid by us with a prepaid label."
        )
        self.assertNotIn("P03", self.ids(facts(policies={"refund-policy": pol(full)})))

    def test_p04_ships_to_conflict(self):
        text = "Processing time 2 days. Delivery time 5 business days. Free shipping. We offer international shipping worldwide."
        self.assertIn(
            "P04",
            self.ids(facts(policies={"shipping-policy": pol(text)}, ships_to=["US"])),
        )
        self.assertNotIn(
            "P04",
            self.ids(
                facts(
                    policies={"shipping-policy": pol(text)}, ships_to=["US", "CA", "GB"]
                )
            ),
        )

    def test_p05_window_conflict(self):
        f = facts(
            policies={
                "refund-policy": pol("Returns accepted within 30 days of delivery.")
            },
            other_texts=[("page-product-a.html", "Free 60-day returns")],
        )
        self.assertIn("P05", self.ids(f))
        f["other_texts"] = [("page-product-a.html", "Free 30-day returns")]
        self.assertNotIn("P05", self.ids(f))

    def test_p06_subscription_needs_cancel(self):
        apps = [
            {
                "name": "ReCharge",
                "kind": "subscription",
                "pattern": "x",
                "source": "raw/homepage.html",
            }
        ]
        self.assertIn(
            "P06",
            self.ids(facts(apps=apps, policies={"terms-of-service": pol("Terms.")})),
        )
        self.assertNotIn(
            "P06",
            self.ids(
                facts(
                    apps=apps,
                    policies={"terms-of-service": pol("You can cancel anytime.")},
                )
            ),
        )

    def test_p07_statement_name(self):
        self.assertIn(
            "P07", self.ids(facts(policies={"terms-of-service": pol("Terms of use.")}))
        )
        self.assertNotIn(
            "P07",
            self.ids(
                facts(
                    policies={
                        "terms-of-service": pol(
                            "The charge will appear on your statement as XSHOP."
                        )
                    }
                )
            ),
        )

    def test_p08_promo_terms(self):
        self.assertIn("P08", self.ids(facts(home_text="Get 20% off today")))
        self.assertNotIn(
            "P08", self.ids(facts(home_text="Get 20% off today. Exclusions apply."))
        )
        popup = [
            {"name": "Klaviyo forms", "kind": "popup", "pattern": "x", "source": "x"}
        ]
        self.assertNotIn(
            "P08", self.ids(facts(apps=popup, home_text="Sign up for news"))
        )

    def test_m01_currency_code(self):
        pages = [("page-product-a.html", "<p>$45.00</p>")]
        self.assertIn(
            "M01", self.ids(facts(product_pages=pages, ships_to=["US", "CA"]))
        )
        self.assertNotIn(
            "M01",
            self.ids(
                facts(
                    product_pages=[("p.html", "<p>$45.00 USD</p>")],
                    ships_to=["US", "CA"],
                )
            ),
        )

    def test_m03_converter(self):
        apps = [
            {
                "name": "Doubly currency converter",
                "kind": "currency",
                "pattern": "doubly",
                "source": "raw/homepage.html",
            }
        ]
        self.assertIn("M03", self.ids(facts(apps=apps)))
        self.assertNotIn("M03", self.ids(facts(localization=True)))

    def test_m04_markets_vs_ships_to(self):
        q = Q.normalize({"market_mix": "US:50,DE:50"})
        self.assertIn("M04", self.ids(facts(ships_to=["US"]), q))
        self.assertNotIn("M04", self.ids(facts(ships_to=["US", "DE"]), q))

    def test_t01_contact_methods(self):
        home = '<a href="mailto:a@x.example">mail</a> Phone: <a href="tel:+16045550199">call</a>'
        self.assertNotIn("T01", self.ids(facts(home_raw=home)))
        t01 = [
            x
            for x in pr_checks.evaluate(facts(home_raw="<p>hello</p>"), {}, "zh")
            if x["id"] == "T01"
        ][0]
        self.assertEqual(t01["severity"], "P0")

    def test_t02_name_conflict(self):
        f = facts(
            contact=(
                "page-contact.html",
                "<p>Alpha Trading Limited, 1 Main Street</p>",
            ),
            policies={"terms-of-service": pol("Operated by Beta Holdings LLC.")},
        )
        self.assertIn("T02", self.ids(f))
        f = facts(
            contact=(
                "page-contact.html",
                "<p>Alpha Trading Limited, 12 Main Street</p>",
            )
        )
        self.assertIn(
            "ok",
            [x["result"] for x in pr_checks.evaluate(f, {}, "zh") if x["id"] == "T02"],
        )

    def test_t03_country(self):
        self.assertIn(
            "T03",
            self.ids(facts(meta_country="CN"), Q.normalize({"entity_country": "US"})),
        )
        self.assertNotIn(
            "T03",
            self.ids(facts(meta_country="US"), Q.normalize({"entity_country": "US"})),
        )

    def test_t04_descriptions(self):
        self.assertIn(
            "T04",
            self.ids(
                facts(
                    product_stats={
                        "count": 5,
                        "desc_chars_median": 20,
                        "images_median": 1,
                    }
                )
            ),
        )
        self.assertNotIn(
            "T04",
            self.ids(
                facts(
                    product_stats={
                        "count": 5,
                        "desc_chars_median": 400,
                        "images_median": 4,
                    }
                )
            ),
        )

    def test_e_group(self):
        cn_sp = Q.normalize(
            {
                "entity_country": "CN",
                "gateways": ["shopify_payments:100"],
                "payout_account": "worldfirst",
            }
        )
        ids = self.ids(facts(), cn_sp)
        self.assertIn("E01", ids)
        self.assertIn("E05", ids)
        self.assertNotIn("E04", ids)
        hk = Q.normalize(
            {
                "entity_country": "HK",
                "gateways": ["stripe:60", "paypal:40"],
                "payout_account": "mercury",
                "category": "CBD oil",
                "market_mix": "UK:100",
            }
        )
        ids = self.ids(facts(), hk)
        self.assertNotIn("E01", ids)
        self.assertNotIn("E02", ids)
        self.assertTrue({"E03", "E06", "E07"} <= ids)
        self.assertIn(
            "E02",
            self.ids(
                facts(),
                Q.normalize({"entity_country": "CN", "gateways": ["stripe:100"]}),
            ),
        )
        self.assertIn(
            "E04",
            self.ids(
                facts(),
                Q.normalize({"gateways": ["paypal:100"], "payout_account": "none"}),
            ),
        )
        unknown = self.ids(facts(), {}, "unknown")
        self.assertIn("E00", unknown)


class RealDataRegressionTest(unittest.TestCase):
    """Patterns seen on real stores (2026-10-27 runs), kept as synthetic text."""

    def test_legal_names_trailing_run(self):
        names = signals.legal_names
        self.assertEqual(
            names("by certified mail addressed to AB Dnam, LLC"), ["AB Dnam, LLC"]
        )
        self.assertEqual(names("CA 90211. AB Dnam, LLC"), ["AB Dnam, LLC"])
        self.assertEqual(names("Limited"), [])

    def test_js_rendered_policy_is_unknown_not_short(self):
        shell = (
            "<html>"
            + "<script>var a=1;</script>" * 2000
            + "<main>Redirecting...</main></html>"
        )
        self.assertTrue(signals.looks_js_rendered(shell, signals.main_text(shell)))
        empty_policy = (
            '<div class="shopify-policy__body"><div class="rte"> </div></div>'
            + " " * 30000
        )
        self.assertFalse(signals.looks_js_rendered(empty_policy, ""))

    def test_marketing_copy_is_not_a_converter_app(self):
        src = [("homepage.html", "anker maggo-doubly fast in a snap")]
        self.assertEqual(signals.match_signatures(src, signals.APP_SIGNATURES), [])

    def test_escaped_json_links(self):
        src = (
            '<a href=\\"https:\\/\\/www.shop.example\\/cms?name=returns\\">Returns</a>'
        )
        found = signals.discover_links(src, "www.shop.example")
        self.assertEqual(
            found.get("refund-policy"), "https://www.shop.example/cms?name=returns"
        )


class CollectTest(Base):
    def test_classify_and_pick(self):
        self.assertEqual(collect.classify(404, b"", "html")[0], "not_found")
        self.assertEqual(collect.classify(403, b"x", "html")[0], "blocked")
        self.assertEqual(
            collect.classify(200, b"<html>captcha</html>", "json")[0], "blocked"
        )
        products = [
            {"handle": "h%d" % i, "title": "Bag", "variants": [{"price": str(10 * i)}]}
            for i in range(1, 8)
        ]
        products.append(
            {
                "handle": "shipping-protection",
                "title": "Shipping Protection",
                "variants": [{"price": "50"}],
            }
        )
        self.assertEqual(collect.pick_products(products), ["h1", "h4", "h7"])

    def test_collector_with_fake_network(self):
        home = (
            '<html><head><script>Shopify.theme = {"name":"x"};</script>'
            '<link href="https://cdn.shopify.com/a.css"></head><body><a href="/pages/contact">Contact</a></body></html>'
        )
        pages = {
            "https://shop.example/": (200, home),
            "https://shop.example/meta.json": (
                200,
                '{"name":"Shop","myshopify_domain":"s.myshopify.com"}',
            ),
            "https://shop.example/products.json?limit=250": (
                200,
                '{"products":[{"handle":"bag","title":"Bag","variants":[{"price":"20"}]}]}',
            ),
            "https://shop.example/policies/refund-policy": (
                200,
                "<html>Refunds within 30 days</html>",
            ),
            "https://shop.example/cart": (200, "<html>Your cart is empty</html>"),
            "https://shop.example/products/bag": (200, "<html>Bag $20</html>"),
            "https://shop.example/pages/contact": (200, "<html>mail us</html>"),
        }
        urls = []

        class FakeFetcher(object):
            def get(self, url, accept=None, headers=None):
                urls.append(url)
                status, body = pages.get(url, (404, ""))
                return {
                    "status": status,
                    "body": body.encode("utf-8"),
                    "final_url": url,
                    "elapsed_ms": 1,
                    "note": "",
                }

        data = os.path.join(self.tmp, "run", "原始数据")
        col = collect.Collector("shop.example", data, FakeFetcher())
        col.run()
        raw = os.path.join(data, "raw")
        self.assertTrue(os.path.isfile(os.path.join(raw, "page-product-bag.html")))
        self.assertTrue(os.path.isfile(os.path.join(raw, "page-contact.html")))
        self.assertFalse(any("checkout" in u for u in urls))
        self.assertEqual(len(urls), len(set(urls)))
        with open(os.path.join(raw, "_status.json"), encoding="utf-8") as fh:
            st = json.load(fh)
        self.assertEqual(st["mode"], "online")

    def test_checkout_regex(self):
        for path in ("/checkouts/cn/abc", "/en-ca/checkout", "/checkout?step=1"):
            self.assertTrue(collect.CHECKOUT_RE.search(path), path)
        self.assertFalse(collect.CHECKOUT_RE.search("/products/checkout-bag"))

    def test_checkout_redirect_not_saved(self):
        class Redirecting(object):
            def get(self, url, accept=None, headers=None):
                final = (
                    "https://shop.example/checkouts/abc"
                    if url.endswith("/cart")
                    else url
                )
                return {
                    "status": 200,
                    "body": b"<html>ok</html>",
                    "final_url": final,
                    "elapsed_ms": 1,
                    "note": "",
                }

        data = os.path.join(self.tmp, "r2", "原始数据")
        collect.Collector("shop.example", data, Redirecting()).run()
        self.assertFalse(os.path.isfile(os.path.join(data, "raw", "cart.html")))

    def test_offline_and_manual_mode(self):
        out = os.path.join(self.tmp, "off", "原始数据")
        rc, msg = quiet(collect.main, ["--offline", "新店", "--out", out])
        self.assertEqual(rc, 0, msg)
        with open(os.path.join(out, "summary.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["mode"], "offline")
        with open(os.path.join(out, "raw", "homepage.md"), "w", encoding="utf-8") as fh:
            fh.write("Welcome to our store. Contact hello@shop.example")
        rc, msg = quiet(compute.main, [os.path.dirname(out)])
        with open(os.path.join(out, "summary.json"), encoding="utf-8") as fh:
            s = json.load(fh)
        self.assertEqual((s["mode"], s["status"]), ("manual", "partial"))
        self.assertIn("manual_collection", s["warnings"])
        self.assertEqual(s["identity"]["status"], "unconfirmed")
        rc, _ = quiet(collect.main, [])
        self.assertEqual(rc, 2)


class ManualAfterNetworkErrorTest(Base):
    def test_online_run_filled_by_hand(self):
        r = self.run_fixture("blocked")
        raw = os.path.join(r["data"], "raw")
        with open(os.path.join(raw, "homepage.md"), "w", encoding="utf-8") as fh:
            fh.write("# Shop\nFree 30-day returns. Email hi@shop.example")
        rc, _ = quiet(compute.main, [r["run"]])
        with open(os.path.join(r["data"], "summary.json"), encoding="utf-8") as fh:
            s = json.load(fh)
        self.assertEqual(s["mode"], "manual")
        self.assertEqual(s["identity"]["status"], "unconfirmed")
        self.assertNotEqual(s["status"], "failed")


class ReportCheckTest(Base):
    def check(self, text, lang="zh"):
        return checks.check_text(text, cfg.profile(lang))

    def wrap(self, body, lang="zh"):
        head = "# t\n\n## 一页看懂\n\n" if lang == "zh" else "# t\n\n## At a glance\n\n"
        tail = (
            "\n\n## 附录 A\n\n- 完成状态: complete\n\n"
            if lang == "zh"
            else "\n\n## Appendix A\n\n- Status: complete\n\n"
        )
        return head + body + tail + draft.fixed_ending(lang)

    def kinds(self, res):
        return {i.kind for i in res.errors}, {i.kind for i in res.warnings}

    def test_icon_as_enabled_is_error(self):
        errs, _ = self.kinds(self.check(self.wrap("- PayPal 已开通 [页面显示]")))
        self.assertIn("icon_as_enabled", errs)
        errs, _ = self.kinds(
            self.check(self.wrap("- 页面有 PayPal 图标, 图标不代表已开通 [页面显示]"))
        )
        self.assertNotIn("icon_as_enabled", errs)
        errs, _ = self.kinds(self.check(self.wrap("- PayPal 已开通 [你提供的]")))
        self.assertNotIn("icon_as_enabled", errs)
        _, warns = self.kinds(self.check(self.wrap("- PayPal 已开通")))
        self.assertIn("enabled_no_badge", warns)
        errs, _ = self.kinds(
            self.check(self.wrap("- PayPal is enabled [Shown on page]", "en"), "en")
        )
        self.assertIn("icon_as_enabled", errs)

    def test_forbidden_promises_and_tech_terms(self):
        for bad in (
            "- 改完保证开通 Stripe",
            "- 这样就不会被冻结",
            "- 最省钱的方案是 PayPal",
            "- 看 products.json 的数据",
        ):
            res = self.check(self.wrap(bad))
            self.assertFalse(res.ok, bad)
        res = self.check(self.wrap("- You are guaranteed approval", "en"), "en")
        self.assertFalse(res.ok)

    def test_all_drafts_pass_both_languages(self):
        for name in ("healthy", "broken", "non_shopify", "blocked", "offline_quote"):
            for lang in ("zh", "en"):
                r = self.run_fixture(name, lang)
                with open(
                    os.path.join(r["data"], "report-draft.md"), encoding="utf-8"
                ) as fh:
                    res = self.check(fh.read(), lang)
                self.assertEqual(
                    [(i.kind, i.text) for i in res.errors], [], (name, lang)
                )
                self.assertEqual(res.status, r["summary"]["status"])


class DeliverTest(Base):
    def test_deliver_both_languages(self):
        for lang in ("zh", "en"):
            r = self.run_fixture("offline_quote", lang)
            shutil.copy(
                os.path.join(r["data"], "report-draft.md"),
                os.path.join(r["data"], "report.md"),
            )
            rc, out = quiet(check_report.main, [r["run"]])
            self.assertEqual(rc, 0, out)
            rc, out = quiet(deliver.main, [r["run"], "--html-only", "--no-open"])
            self.assertEqual(rc, 0, out)
            with open(os.path.join(r["data"], "delivery.json"), encoding="utf-8") as fh:
                rec = json.load(fh)
            self.assertEqual((rec["lang"], rec["status"]), (lang, "partial"))
            self.assertTrue(rec["check"]["ok"])
            names = [t["name"][lang] for t in cfg.CSV_TABLES]
            self.assertEqual(sorted(rec["csv"]), sorted(names))
            with open(os.path.join(r["run"], names[1]), encoding="utf-8-sig") as fh:
                text = fh.read()
            self.assertIn("916.10", text)
            self.assertIn("10993.20", text)
            with open(os.path.join(r["run"], names[0]), "rb") as fh:
                self.assertTrue(fh.read().startswith(b"\xef\xbb\xbf"))

    def test_failed_run_still_delivers(self):
        r = self.run_fixture("blocked")
        shutil.copy(
            os.path.join(r["data"], "report-draft.md"),
            os.path.join(r["data"], "report.md"),
        )
        rc, out = quiet(deliver.main, [r["run"], "--html-only", "--no-open"])
        self.assertEqual(rc, 0, out)
        with open(os.path.join(r["data"], "delivery.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
