"""Tests for the shared library (shared/) and its per-skill copies.

Covers the bilingual mechanism (zh / en), extensible badges, pluggable
check rules, data-file freshness and the sync guard.
"""

import contextlib
import csv
import datetime
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import sync_shared  # noqa: E402
from shared import checks, datafile, evidence, fetch, i18n, output, render  # noqa: E402

EN_GOOD = (
    "# Example store teardown\n\n"
    "## At a glance\n\n"
    "- 132 products on sale [Verified]\n"
    "- Founded in 2016 [Store claims], domain registered 2015 [Verified]\n"
    "- Pricing rests on material stories [Inferred] Basis: product pages.\n"
    "- PayPal icon in the footer [Shown on page]\n\n"
    "## Prices\n\n"
    "| Role | Price | Check |\n|---|---|---|\n| Entry | 75 USD | [Verified] |\n\n"
    "## Appendix: evidence and sources\n\n"
    "| E01 | E1 | products.json | raw/products-1.json |\n\n"
    "Status: partial\n\n"
    "---\n\n### What this report does not cover\n\n- Real sales.\n"
)


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chuhai-shared-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestI18nEvidence(unittest.TestCase):
    def test_norm_lang(self):
        self.assertEqual(i18n.norm_lang(None), "zh")
        self.assertEqual(i18n.norm_lang("zh-CN"), "zh")
        self.assertEqual(i18n.norm_lang("EN_us"), "en")
        with self.assertRaises(ValueError):
            i18n.norm_lang("fr")

    def test_badge_labels(self):
        self.assertEqual(evidence.badge("verified", "zh"), "[已核实]")
        self.assertEqual(
            evidence.labels("en"),
            (
                "Verified",
                "Store claims",
                "Third-party",
                "Shown on page",
                "Inferred",
                "Conflicting",
                "Not found",
            ),
        )
        self.assertNotIn("You provided", evidence.labels("en"))
        keys = evidence.DEFAULT_BADGE_KEYS + ("user_input",)
        self.assertIn("You provided", evidence.labels("en", keys))
        self.assertEqual(evidence.badge("user_input", "zh"), "[你提供的]")
        self.assertEqual(evidence.key_of("Shown on page"), "on_page")
        with self.assertRaises(ValueError):
            evidence.labels("en", ("nope",))

    def test_weakest_link(self):
        self.assertEqual(evidence.weakest(["[Verified]", "[你提供的]"]), "user_input")
        self.assertEqual(evidence.weakest(["verified", "on_page"]), "on_page")
        self.assertEqual(evidence.weakest(["Inferred", "You provided"]), "inferred")
        self.assertEqual(evidence.weakest(["Not found", "Inferred"]), "not_found")
        self.assertIsNone(evidence.weakest(["other"]))
        order = ["not_found", "inferred", "user_input", "self_claim", "verified"]
        ranks = [evidence.STRENGTH[k] for k in order]
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(evidence.STRENGTH["self_claim"], evidence.STRENGTH["on_page"])

    def test_levels_and_status(self):
        self.assertEqual(evidence.badge_for_level("e6"), "inferred")
        self.assertEqual(evidence.badge_for_level("E2"), "self_claim")
        self.assertEqual(evidence.combine_status(["complete", "complete"]), "complete")
        self.assertEqual(evidence.combine_status(["complete", "failed"]), "partial")
        self.assertEqual(evidence.combine_status(["failed", "failed"]), "failed")
        self.assertIsNone(evidence.combine_status([]))


class TestOutput(TmpCase):
    def test_root_name_is_a_parameter(self):
        sandbox = os.path.join(self.tmp, "outputs")
        os.makedirs(sandbox)
        got = output.resolve_output_root(
            None, "Store audits", environ={}, sandbox=sandbox
        )
        self.assertEqual(got, os.path.join(sandbox, "Store audits"))
        env = {output.ENV_OUTPUT: os.path.join(self.tmp, "env")}
        self.assertEqual(
            output.resolve_output_root(None, "x", environ=env, sandbox=sandbox),
            os.path.join(self.tmp, "env"),
        )

    def test_run_dir_and_locate_en(self):
        d = datetime.date(2026, 10, 27)
        a = output.create_run_dir(self.tmp, "a.com", "audit", d)
        b = output.create_run_dir(self.tmp, "a.com", "audit", d)
        self.assertEqual(os.path.basename(a), "a.com audit 2026-10-27")
        self.assertTrue(b.endswith("(2)"))
        raw = os.path.join(a, "data")
        os.makedirs(raw)
        self.assertEqual(output.locate_run(a, "en"), (a, raw))
        self.assertEqual(output.locate_run(raw, "en"), (a, raw))
        output.write_readme(raw, "hello\n", "en")
        with open(os.path.join(raw, "README.txt"), "rb") as fh:
            self.assertEqual(fh.read(), b"\xef\xbb\xbfhello\r\n")


class TestRender(TmpCase):
    def test_en_document(self):
        doc, _ = render.build_html(EN_GOOD, self.tmp, "x", "2026-10-27", "demo", "en")
        self.assertIn('<html lang="en">', doc)
        self.assertIn('<section class="summary-card">', doc)
        self.assertIn('<section class="appendix">', doc)
        self.assertIn('<section class="closing">', doc)
        for cls in ("ok", "self", "guess", "page"):
            self.assertIn('class="badge badge-%s"' % cls, doc)
        self.assertIn("Generated by demo · 2026-10-27", doc)
        self.assertIn('<td class="al-right nw">75 USD</td>', doc)

    def test_zh_badges_not_rendered_in_en(self):
        out = render.Converter(self.tmp, "en").convert("[已核实] [Verified]")
        self.assertEqual(out.count("badge-ok"), 1)

    def test_user_input_badge_opt_in(self):
        keys = evidence.DEFAULT_BADGE_KEYS + ("user_input",)
        doc, _ = render.build_html(
            "[You provided] 30%", self.tmp, "t", "d", "g", "en", keys
        )
        self.assertIn("badge-user", doc)
        self.assertIn(".badge-user {", doc)
        plain, _ = render.build_html("[You provided]", self.tmp, "t", "d", "g", "en")
        self.assertNotIn("badge-user", plain)
        self.assertEqual(render.css_for(), render.CSS)

    def test_csv_and_multi(self):
        tables = [
            ("Products.csv", ["Name", "Price"], [["=cmd", 1.5], [None, True]]),
            ("Skipped.csv", ["A"], None),
        ]
        done = render.write_csvs(self.tmp, tables)
        self.assertEqual([os.path.basename(p) for p, _n in done], ["Products.csv"])
        with open(done[0][0], "rb") as fh:
            self.assertTrue(fh.read().startswith(b"\xef\xbb\xbf"))
        with open(done[0][0], encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(rows, [["Name", "Price"], ["'=cmd", "1.5"], ["", "true"]])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "Skipped.csv")))

    def test_publish_fallback_reason_en(self):
        html_path = os.path.join(self.tmp, "report.html")
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write("<html></html>")
        path, kind, why = render.publish(
            html_path,
            self.tmp,
            self.tmp,
            "Audit",
            find_browser=lambda: None,
            lang="en",
        )
        self.assertEqual(kind, "HTML")
        self.assertTrue(path.endswith("Audit.html"))
        self.assertIn("no Chrome / Edge", why)

    def publish_exit0(self, lang, elevated):
        html_path = os.path.join(self.tmp, "report.html")
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write("<html></html>")

        def printer(browser, src, pdf):  # no lang: note comes back in zh
            return False, "%s 0" % render.BROWSER_EXIT_NOTE

        return render.publish(
            html_path,
            self.tmp,
            self.tmp,
            "R",
            find_browser=lambda: "/fake/edge",
            printer=printer,
            lang=lang,
            log=lambda _m: None,
            elevated=elevated,
        )

    def test_publish_exit_reason_admin_and_service(self):
        _p, kind, why = self.publish_exit0("zh", True)
        self.assertEqual(kind, "HTML")
        self.assertIn("以管理员身份运行", why)
        self.assertNotIn("后台服务", why)
        _p, _k, why = self.publish_exit0("zh", False)
        self.assertIn("后台服务", why)
        _p, _k, why = self.publish_exit0("en", True)
        self.assertIn("run as administrator", why)
        self.assertIn("browser exit code 0", why)
        _p, _k, why = self.publish_exit0("en", False)
        self.assertIn("background services, browser exit code 0", why)
        self.assertNotIn(render.BROWSER_EXIT_NOTE, why)

    def test_is_elevated_false_off_windows(self):
        if os.name != "nt":
            self.assertFalse(render.is_elevated())

    def test_publish_pdf(self):
        html_path = os.path.join(self.tmp, "report.html")
        open(html_path, "w").close()

        def printer(browser, src, pdf):
            with open(pdf, "wb") as fh:
                fh.write(b"%PDF" + b"x" * 2000)
            return True, ""

        logs = []
        path, kind, _why = render.publish(
            html_path,
            self.tmp,
            self.tmp,
            "R",
            find_browser=lambda: "/fake/chrome",
            printer=printer,
            log=logs.append,
        )
        self.assertEqual(kind, "PDF")
        self.assertTrue(os.path.isfile(path))
        self.assertIn("/fake/chrome", logs[0])


class TestChecks(unittest.TestCase):
    def kinds(self, result):
        return sorted({i.kind for i in result.errors + result.warnings})

    def test_en_clean_report(self):
        r = checks.check_text(EN_GOOD, checks.Profile(lang="en"))
        self.assertTrue(r.ok, [i.as_dict() for i in r.errors])
        self.assertEqual(r.warnings, [])
        self.assertEqual(r.status, "partial")

    def test_en_rules(self):
        md = (
            "# t\n\n## Prices\n\n"
            "- Pricing is a key strategy [Verified]\n"
            "- The store claims worldwide shipping [Verified]\n"
            "- Uses JSON feeds [Inferred]\n"
        )
        p = checks.Profile(lang="en", require_status=True)
        r = checks.check_text(md, p)
        self.assertEqual(
            {i.kind for i in r.errors},
            {
                "no_summary",
                "no_appendix",
                "no_ending",
                "no_status",
                "guess_no_basis",
                "tech_term",
            },
        )
        self.assertEqual({i.kind for i in r.warnings}, {"over_label", "mixed_source"})
        text = checks.format_result(r, p)
        self.assertIn("Report self-check", text)
        self.assertIn("Result: failed", text)

    def test_status_detection(self):
        self.assertEqual(checks.report_status("完成状态: complete"), "complete")
        hint = "- 完成状态 complete/partial/failed"
        self.assertIsNone(checks.report_status(hint))

    def test_forbidden_terms_and_rules(self):
        payments = checks.claim_badge_rule(
            r"已开通",
            "icon_as_enabled",
            "why",
            "fix",
            forbidden=("on_page",),
        )
        p = checks.Profile(
            lang="zh",
            forbidden_terms=[(r"合规", "结论不能写 '%s'.", "fix")],
            rules=[payments],
        )
        md = (
            "## 一页看懂\n\n"
            "- PayPal 已开通 [页面显示]\n"
            "- Stripe 已开通 [已核实]\n"
            "- 政策页合规\n\n"
            "## 附录\n\n- 政策页合规 PayPal 已开通 [页面显示]\n\n"
            "本报告没有覆盖的\n"
        )
        r = checks.check_text(md, p)
        got = [(i.kind, i.line) for i in r.errors]
        self.assertEqual(got, [("icon_as_enabled", 3), ("forbidden_term", 5)])
        self.assertIn("合规", r.errors[1].why)

    def test_user_input_badge_is_known_to_profile(self):
        keys = evidence.DEFAULT_BADGE_KEYS + ("user_input",)
        p = checks.Profile(lang="en", badge_keys=keys)
        self.assertIn("You provided", p.badges)
        segs = list(checks.claim_segments("- margin 30% [You provided]", p.badge_re))
        self.assertEqual(segs, [("You provided", "margin 30%")])

    def test_cli_json_has_status_and_lang(self):
        tmp = tempfile.mkdtemp(prefix="chuhai-shared-")
        self.addCleanup(shutil.rmtree, tmp, True)
        raw = os.path.join(tmp, "data")
        os.makedirs(raw)
        with open(os.path.join(raw, "report.md"), "w", encoding="utf-8") as fh:
            fh.write(EN_GOOD)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = checks.main([tmp, "--json"], checks.Profile(lang="en"))
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual((data["status"], data["lang"]), ("partial", "en"))


class TestNoOpen(unittest.TestCase):
    def test_no_open_env(self):
        for value in ("1", "true", "YES"):
            self.assertFalse(
                output.can_open_gui("/x", "darwin", {output.NO_OPEN_ENV: value})
            )
        self.assertTrue(output.can_open_gui("/x", "darwin", {output.NO_OPEN_ENV: "0"}))


class TestFetchAndData(unittest.TestCase):
    def test_cert_notes(self):
        self.assertIn("Install Certificates", fetch.cert_note("darwin", "en"))
        self.assertEqual(fetch.CERT_NOTE, fetch.CERT_NOTES["zh"])
        f = fetch.Fetcher(delay=0, lang="en")
        self.assertEqual(f.cert_note, fetch.CERT_NOTES["en"])

    def test_status_entry(self):
        e = fetch.status_entry(
            "k", "u", {"status": None, "body": None, "note": ""}, "error"
        )
        self.assertEqual((e["note"], e["bytes"]), ("network error", 0))

    def test_freshness(self):
        today = datetime.date(2026, 10, 27)
        fresh = datafile.freshness({"valid_as_of": "2026-10-01"}, today=today)
        self.assertEqual((fresh["age_days"], fresh["stale"]), (26, False))
        stale = datafile.freshness(
            {"valid_as_of": "2026-01-01"}, today=today, lang="en"
        )
        self.assertTrue(stale["stale"])
        self.assertIn("over 90 days", stale["note"])
        self.assertIsNone(datafile.freshness({}, today=today)["stale"])
        self.assertTrue(
            datafile.freshness({"valid_as_of": "2026-10-01"}, 7, today)["stale"]
        )


class TestSync(TmpCase):
    def test_every_skill_copy_in_sync(self):
        for dest in sync_shared.targets():
            self.assertEqual(sync_shared.diff(dest), [], dest)

    def test_drift_detected(self):
        dest = os.path.join(self.tmp, "_shared")
        sync_shared.sync(dest)
        self.assertEqual(sync_shared.diff(dest), [])
        with open(os.path.join(dest, "render.py"), "a") as fh:
            fh.write("# local edit\n")
        open(os.path.join(dest, "extra.py"), "w").close()
        os.remove(os.path.join(dest, "i18n.py"))
        self.assertEqual(
            sorted(sync_shared.diff(dest)),
            ["changed render.py", "extra extra.py", "missing i18n.py"],
        )


if __name__ == "__main__":
    unittest.main()
