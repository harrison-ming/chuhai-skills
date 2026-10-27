"""Unit tests: password page, hreflang rules, claim extraction, crawler
limits, markets, self-check rules (no legal conclusions, fixed ending).
Python 3.8+, standard library only.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(SKILL, "scripts"), HERE]

import crawl  # noqa: E402
import draft_report  # noqa: E402
import extract_claims  # noqa: E402
import hreflang  # noqa: E402
import markets  # noqa: E402
import pages  # noqa: E402
import ruleset  # noqa: E402
import skill_config as cfg  # noqa: E402
from _shared import checks  # noqa: E402


class PasswordTest(unittest.TestCase):
    def test_detection(self):
        self.assertTrue(pages.is_password_page("", "https://a.example/password"))
        self.assertTrue(
            pages.is_password_page('<form method="post" action="/password">')
        )
        self.assertTrue(pages.is_password_page('<body class="template-password">'))
        self.assertFalse(
            pages.is_password_page(
                "<html><body>shop</body></html>", "https://a.example/"
            )
        )


class HreflangTest(unittest.TestCase):
    def v(self, url, pairs):
        return {"url": url, "final_url": url, "pairs": pairs}

    def test_codes(self):
        self.assertIsNone(hreflang.code_problem("en-GB"))
        self.assertIsNone(hreflang.code_problem("x-default"))
        self.assertIsNone(hreflang.code_problem("zh-Hant-TW"))
        self.assertIn("bad_region", hreflang.code_problem("en-UK"))
        self.assertIn("bad_language", hreflang.code_problem("jp"))
        self.assertEqual(hreflang.code_problem("english"), "bad_format")

    def test_clean_set_passes(self):
        pairs = [
            ["en-us", "https://s.example/"],
            ["de-de", "https://s.example/de"],
            ["x-default", "https://s.example/"],
        ]
        res = hreflang.check(
            [self.v("https://s.example/", pairs), self.v("https://s.example/de", pairs)]
        )
        self.assertEqual(res["status"], "pass", res["problems"])

    def test_problems(self):
        home = [
            ["en-us", "https://s.example/"],
            ["de-de", "https://s.example/de"],
            ["fr", "/fr"],
        ]
        de = [["de-de", "https://s.example/de"]]
        res = hreflang.check(
            [self.v("https://s.example/", home), self.v("https://s.example/de", de)]
        )
        kinds = {p["kind"] for p in res["problems"]}
        self.assertTrue(
            {"relative_url", "no_x_default", "no_return_link"} <= kinds, kinds
        )
        res = hreflang.check(
            [self.v("https://s.example/", [["de", "https://s.example/de"]])]
        )
        self.assertIn("no_self_reference", {p["kind"] for p in res["problems"]})
        self.assertEqual(
            hreflang.check([self.v("https://s.example/", [])])["status"], "absent"
        )


class ClaimsTest(unittest.TestCase):
    def topics(self, text):
        return [
            (c["topic"], c["value"])
            for c in extract_claims.claims_from_text(text, "policy:x", "u")
        ]

    def test_warranty_is_not_return_window(self):
        got = self.topics("All products come with a 30-day warranty against defects.")
        self.assertNotIn("return_days", [t for t, _v in got])
        got = self.topics("产品享有 30 天质保.")
        self.assertNotIn("return_days", [t for t, _v in got])

    def test_return_and_refund(self):
        got = self.topics(
            "You can return items within 30 days of delivery. "
            "Refunds are processed within 10 business days."
        )
        self.assertIn(("return_days", 30), got)
        self.assertIn(("return_start", "delivery"), got)
        self.assertIn(("refund_days", 10), got)
        self.assertNotIn(("return_days", 10), got)

    def test_start_date_near_day_count(self):
        got = self.topics(
            "Return it within 120 days from your delivery date for a full "
            "refund of the purchase price."
        )
        self.assertIn(("return_start", "delivery"), got)
        got = self.topics(
            "Refunds will only be applied to the original form of payment."
        )
        self.assertNotIn("return_start", [t for t, _v in got])
        got = self.topics(
            "Die Retourenfrist betr\u00e4gt 100 Tage und beginnt mit dem Bestelldatum."
        )
        self.assertIn(("return_start", "purchase"), got)

    def test_withdrawal_right_is_separate_topic(self):
        a = extract_claims.claims_from_text(
            "Sie haben das Recht, binnen vierzehn Tagen diesen Vertrag zu widerrufen.",
            "policy:refund-policy",
            "u1",
        )
        b = extract_claims.claims_from_text(
            "Our extended return window is 100 days.", "faq", "u2"
        )
        self.assertEqual(extract_claims.conflicts(a + b), [])

    def test_conflict_needs_two_sources(self):
        a = extract_claims.claims_from_text("60-day returns!", "announcement", "u1")
        b = extract_claims.claims_from_text(
            "Returns within 14 days of delivery.", "policy:refund-policy", "u2"
        )
        self.assertEqual(
            [c["topic"] for c in extract_claims.conflicts(a + b)], ["return_days"]
        )
        same = extract_claims.claims_from_text(
            "Returns within 14 days. Or 30 days for members.",
            "policy:refund-policy",
            "u",
        )
        self.assertEqual(extract_claims.conflicts(same), [])

    def test_delivery_ranges_overlap_is_not_conflict(self):
        a = extract_claims.claims_from_text(
            "Delivery takes 5-10 business days.", "faq", "u"
        )
        b = extract_claims.claims_from_text(
            "Delivery takes 7-12 business days.", "pdp", "u"
        )
        self.assertEqual(extract_claims.conflicts(a + b), [])


class CrawlTest(unittest.TestCase):
    def test_limits(self):
        host = "shop.example"
        links = "".join(
            '<a href="/pages/info-%d">Info %d</a>' % (i, i) for i in range(80)
        )
        home = (
            "<html><body><header>%s</header><a href='https://other.example/returns'>Returns</a>"
            "<footer><a href='/returns'>Returns</a></footer></body></html>" % links
        )
        calls = []

        def fetch_page(url):
            calls.append(url)
            depth_links = '<a href="%s/deeper-faq">FAQ</a>' % url
            return "<html><body>%s</body></html>" % depth_links, url, "ok"

        rows = crawl.crawl(
            "https://shop.example/", home, host, fetch_page, ["/private"]
        )
        self.assertLessEqual(len(rows), crawl.MAX_PAGES)
        self.assertTrue(all("other.example" not in u for u in calls))
        self.assertTrue(all(r["depth"] <= crawl.MAX_DEPTH for r in rows))
        self.assertEqual(rows[0]["role"], "refund")  # policy links first

    def test_robots(self):
        self.assertTrue(crawl.blocks_all("User-agent: *\nDisallow: /\n"))
        self.assertFalse(crawl.blocks_all("User-agent: Googlebot\nDisallow: /\n"))
        self.assertFalse(crawl.allowed("https://a.example/wp-admin/x", ["/wp-admin/"]))


class MarketsTest(unittest.TestCase):
    def test_parse_and_groups(self):
        self.assertEqual(markets.parse_markets("US, UK, 德国"), ["US", "GB", "DE"])
        self.assertEqual(markets.groups(["DE", "GB"]), {"DE", "EU", "UK"})
        self.assertTrue(markets.applies(["EU"], ["FR"]))
        self.assertFalse(markets.applies(["AU"], ["US"]))
        self.assertEqual(
            markets.default_markets(None, ["DE", "ZZ"]), (["DE", "US"], "site")
        )


class RulesTest(unittest.TestCase):
    def test_catalogue(self):
        data, rules = ruleset.load_rules()
        self.assertEqual(len(rules), len(data["rules"]))
        for r in data["rules"]:
            for s in r.get("src") or []:
                self.assertIn(s, data["sources"], r["id"])
            if r.get("exp") and not r.get("p0_ok"):
                self.assertNotEqual(r["lv"], "P0", r["id"])

    def test_experience_items_never_p0(self):
        self.assertFalse(ruleset.p0_allowed({"lv": "P0", "exp": True, "src": []}, {}))
        self.assertEqual(ruleset.demote("P0"), "P1")


class SelfCheckTest(unittest.TestCase):
    def report(self, body, lang="zh"):
        head = "## 一页看懂\n\n" if lang == "zh" else "## At a glance\n\n"
        app = (
            "\n\n## 附录\n\n- 完成状态: partial\n\n"
            if lang == "zh"
            else "\n\n## Appendix\n\n- Status: partial\n\n"
        )
        return head + body + app + draft_report.fixed_ending(lang)

    def kinds(self, text, lang="zh"):
        res = checks.check_text(text, cfg.profile(lang))
        return {e.kind for e in res.errors}

    def test_no_legal_conclusions(self):
        self.assertIn(
            "forbidden_term", self.kinds(self.report("- 店铺已合规 [已核实]"))
        )
        self.assertIn(
            "forbidden_term",
            self.kinds(self.report("- The store is compliant [Verified]", "en"), "en"),
        )
        self.assertEqual(
            self.kinds(self.report("- 退货政策写了 30 天 [已核实]")), set()
        )

    def test_requirement_gap_must_be_inferred(self):
        line = "- 与欧盟的官方要求不一致 [已核实]"
        self.assertIn("legal_needs_inferred", self.kinds(self.report(line)))

    def test_fixed_ending_has_not_legal_advice(self):
        self.assertIn("不是法律意见", draft_report.fixed_ending("zh"))
        self.assertIn("not legal advice", draft_report.fixed_ending("en"))
        self.assertIn(
            "no_ending",
            self.kinds("## 一页看懂\n\n- x\n\n## 附录\n\n- 完成状态: partial\n"),
        )


if __name__ == "__main__":
    unittest.main()
