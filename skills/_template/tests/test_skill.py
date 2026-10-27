"""Synthetic-fixture tests for __SKILL_NAME__ (offline, stdlib unittest).

Run from the repository root:
    python tools/run_tests.py            (all skills)
    python -m unittest discover -s skills/__SKILL_NAME__/tests

Covers the three required scenarios: normal, blocked, not the target
platform; plus both report languages through check_report and deliver.
"""

import contextlib
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
sys.path.insert(0, os.path.join(SKILL, "scripts"))

import check_report  # noqa: E402
import compute  # noqa: E402
import deliver  # noqa: E402
import skill_config as cfg  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures")


def fixed_ending(lang):
    path = os.path.join(SKILL, "references", "report-template.%s.md" % lang)
    with open(path, encoding="utf-8") as fh:
        blocks = re.findall(r"```markdown\n(.*?)\n```", fh.read(), re.S)
    return blocks[-1]


REPORT = {
    "zh": (
        "# 示例 报告\n\n## 一页看懂\n\n- 首页能打开 [已核实]\n"
        "- 定价偏高 [推测] 依据: 首页价格.\n\n"
        "## 附录: 证据与数据来源\n\n- 完成状态: complete\n\n"
    ),
    "en": (
        "# Sample report\n\n## At a glance\n\n- Homepage reachable [Verified]\n"
        "- Prices look high [Inferred] Basis: homepage prices.\n\n"
        "## Appendix: evidence and sources\n\n- Status: complete\n\n"
    ),
}


def quiet(fn, *args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(*args)
    return rc, out.getvalue() + err.getvalue()


class SkillTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="%s-test-" % cfg.NAME)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_fixture(self, name, lang="zh"):
        run = os.path.join(self.tmp, "%s %s 2026-10-27" % (name, lang))
        data = os.path.join(run, "原始数据" if lang == "zh" else "data")
        shutil.copytree(os.path.join(FIXTURES, name, "raw"), os.path.join(data, "raw"))
        rc, _out = quiet(compute.main, [run, "--lang", lang])
        self.assertEqual(rc, 0)
        with open(os.path.join(data, "summary.json"), encoding="utf-8") as fh:
            return run, data, json.load(fh)

    def test_normal(self):
        _run, _data, s = self.run_fixture("normal")
        self.assertEqual(s["status"], "complete")
        self.assertIs(s["is_target"], True)
        self.assertEqual(s["warnings"], [])
        self.assertEqual(s["checks"][1]["note"], "Alpha Outfitters")

    def test_blocked(self):
        _run, _data, s = self.run_fixture("blocked")
        self.assertEqual(s["status"], "failed")
        self.assertEqual(s["is_target"], "unknown")
        self.assertEqual(s["endpoints"]["homepage"]["result"], "blocked")
        self.assertIn("homepage_unavailable", s["warnings"])

    def test_not_target(self):
        _run, _data, s = self.run_fixture("not_target")
        self.assertEqual(s["status"], "partial")
        self.assertIs(s["is_target"], False)
        self.assertIn("not_target_platform", s["warnings"])

    def test_check_and_deliver_both_languages(self):
        for lang in ("zh", "en"):
            run, data, _s = self.run_fixture("normal", lang)
            with open(os.path.join(data, "report.md"), "w", encoding="utf-8") as fh:
                fh.write(REPORT[lang] + fixed_ending(lang))
            rc, out = quiet(check_report.main, [run])
            self.assertEqual(rc, 0, out)
            rc, out = quiet(deliver.main, [run, "--html-only", "--no-open"])
            self.assertEqual(rc, 0, out)
            with open(os.path.join(data, "delivery.json"), encoding="utf-8") as fh:
                rec = json.load(fh)
            self.assertEqual((rec["lang"], rec["status"]), (lang, "complete"))
            self.assertTrue(rec["check"]["ok"])
            self.assertTrue(rec["report"].endswith(".html"))
            csv_name = cfg.CSV_TABLES[0]["name"][lang]
            self.assertEqual(rec["csv"], [csv_name])
            with open(os.path.join(run, csv_name), "rb") as fh:
                head = fh.read().decode("utf-8")
            self.assertTrue(head.startswith("﻿"))
            self.assertIn("检查项" if lang == "zh" else "Item", head)

    def test_check_catches_missing_status_and_basis(self):
        run, data, _s = self.run_fixture("normal")
        with open(os.path.join(data, "report.md"), "w", encoding="utf-8") as fh:
            fh.write("## 一页看懂\n\n- 价格偏高 [推测]\n")
        rc, out = quiet(check_report.main, [run, "--json"])
        self.assertEqual(rc, 1)
        kinds = {e["kind"] for e in json.loads(out)["errors"]}
        self.assertTrue({"no_status", "guess_no_basis", "no_ending"} <= kinds)


if __name__ == "__main__":
    unittest.main()
