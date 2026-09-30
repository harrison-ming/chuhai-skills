"""Tests for check_report.py (report self-check gate) and its deliver hook."""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(
    os.path.dirname(HERE), "skills", "shopify-store-teardown", "scripts"
)
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, HERE)

import check_report  # noqa: E402
import deliver  # noqa: E402
from test_delivery import make_run  # noqa: E402

ENDING = "---\n\n### 本报告没有覆盖的\n\n- 需要浏览器才能看到的.\n"

GOOD = (
    "# 示例店 对标拆解报告\n\n"
    "<sub>https://example.com/sitemap.xml · 分析日期 2026-09-30</sub>\n\n"
    "## 一页看懂\n\n"
    "- 在售 132 款商品, 退货期 30 天 [已核实]\n"
    "- 店铺自称 2016 年创立 [店铺自称], 域名 2015 年注册 [已核实]\n"
    "- 定价靠材料故事撑住 [推测] 依据: 商品页逐项写材料.\n"
    '- 首页写着 "Why it matters" [已核实]\n\n'
    "## 价格怎么定的\n\n"
    "| 角色 | 说法 |\n|---|---|\n"
    "| 入门款 | 75 美元 [已核实] |\n"
    "| 主力款 | 刻意设计 [推测] 依据: 台阶与材料对应 |\n\n"
    "## 附录: 证据与数据来源\n\n"
    "| E01 | E1 | products.json 692 款 | raw/products-1.json |\n\n" + ENDING
)


def kinds(result):
    return [(i.severity, i.kind) for i in result.errors + result.warnings]


class TestRules(unittest.TestCase):
    def check(self, md):
        return check_report.check_text(md)

    def test_clean_report_passes(self):
        r = self.check(GOOD)
        self.assertTrue(r.ok, kinds(r))
        self.assertEqual(r.warnings, [], kinds(r))

    def test_missing_summary_appendix_ending(self):
        r = self.check("# t\n\n## 价格\n\n- 132 款 [已核实]\n")
        got = {i.kind for i in r.errors}
        self.assertEqual(got, {"no_summary", "no_appendix", "no_ending"})
        self.assertFalse(r.ok)

    def test_summary_must_be_first(self):
        md = GOOD.replace("## 一页看懂", "## 前言\n\n文字\n\n## 一页看懂", 1)
        self.assertIn(("error", "summary_not_first"), kinds(self.check(md)))

    def test_tech_terms_before_appendix(self):
        md = GOOD.replace(
            "- 在售 132 款商品",
            "- products.json 显示, E1 证据, L1 档, hreflang 设置, 在售 132 款商品",
        )
        r = self.check(md)
        terms = [i.why for i in r.errors if i.kind == "tech_term"]
        self.assertEqual(len(terms), 4, terms)
        self.assertEqual(r.errors[0].line, 7)
        # the appendix and URLs may use them freely
        self.assertTrue(self.check(GOOD).ok)

    def test_explained_term_is_only_a_warning(self):
        md = GOOD.replace(
            "- 在售 132 款商品",
            "- 网页历史存档 (Wayback, 也就是第三方存档) 在售 132 款",
        )
        r = self.check(md)
        self.assertTrue(r.ok)
        self.assertIn(("warning", "tech_term"), kinds(r))

    def test_soft_terms_warn(self):
        md = GOOD.replace("- 在售 132 款商品", "- 后台 tag 显示在售 132 款商品")
        r = self.check(md)
        self.assertTrue(r.ok)
        self.assertIn(("warning", "soft_tech_term"), kinds(r))

    def test_old_labels(self):
        md = GOOD + "\n- 旧写法 [事实] 和 [推断]\n"
        r = self.check(md)
        olds = [i for i in r.errors if i.kind == "old_label"]
        self.assertEqual(len(olds), 2)
        self.assertIn("[已核实]", olds[0].fix)

    def test_guess_needs_basis(self):
        md = GOOD.replace("[推测] 依据: 商品页逐项写材料.", "[推测]")
        r = self.check(md)
        self.assertEqual([i.kind for i in r.errors], ["guess_no_basis"])
        # continuation line counts as the same item
        md2 = GOOD.replace(
            "[推测] 依据: 商品页逐项写材料.", "[推测]\n  依据: 商品页逐项写材料."
        )
        self.assertTrue(self.check(md2).ok)
        # table cell without basis
        md3 = GOOD.replace("刻意设计 [推测] 依据: 台阶与材料对应", "刻意设计 [推测]")
        self.assertIn(("error", "guess_no_basis"), kinds(self.check(md3)))

    def test_over_label_judgment(self):
        md = GOOD.replace(
            "- 在售 132 款商品, 退货期 30 天 [已核实]",
            "- 定价核心是一款一价, 正价款基本不打折 [已核实]",
        )
        r = self.check(md)
        self.assertTrue(r.ok)
        over = [i for i in r.warnings if i.kind == "over_label"]
        self.assertEqual(len(over), 1)
        self.assertIn("核心", over[0].why)
        self.assertIn("[推测]", over[0].fix)
        self.assertEqual(r.over_label_count(), 1)

    def test_over_label_ignores_quotes_and_other_badges(self):
        md = GOOD.replace(
            '- 首页写着 "Why it matters" [已核实]',
            '- 首页写着 "订单可能要 30 天才发货, 所以请耐心" [已核实]\n'
            "- 主要靠材料 [推测] 依据: 商品页.",
        )
        self.assertEqual(self.check(md).warnings, [])

    def test_over_label_segments_between_badges(self):
        md = GOOD.replace(
            "- 在售 132 款商品, 退货期 30 天 [已核实]",
            "- 最大的风险在履约 [推测] 依据: 公告. 公告写 30 天发货 [已核实]",
        )
        self.assertEqual(self.check(md).warnings, [])
        md2 = GOOD.replace(
            "- 在售 132 款商品, 退货期 30 天 [已核实]",
            "- [已核实] 这说明它很成功",
        )
        self.assertIn(("warning", "over_label"), kinds(self.check(md2)))

    def test_mixed_source(self):
        md = GOOD.replace(
            "- 在售 132 款商品, 退货期 30 天 [已核实]",
            "- 品牌自称 2016 年创立, 域名 2015 年注册 [已核实]",
        )
        self.assertIn(("warning", "mixed_source"), kinds(self.check(md)))

    def test_fullwidth_is_warning(self):
        md = GOOD.replace("在售 132 款商品, 退货期", "在售 132 款商品\uff0c退货期")
        r = self.check(md)
        self.assertTrue(r.ok)
        self.assertIn(("warning", "fullwidth"), kinds(r))

    def test_code_fences_ignored(self):
        md = GOOD.replace(
            "## 价格怎么定的", "```\nproducts.json [事实]\n```\n\n## 价格怎么定的"
        )
        self.assertTrue(self.check(md).ok)

    def test_format_mentions_line_why_fix(self):
        md = GOOD.replace("[推测] 依据: 商品页逐项写材料.", "[推测]")
        text = check_report.format_result(self.check(md), "r.md")
        self.assertIn("第 9 行", text)
        self.assertIn("为什么", text)
        self.assertIn("怎么改", text)
        self.assertIn("未通过", text)
        self.assertIn("通过", check_report.format_result(self.check(GOOD)))


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="store-teardown-check-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, argv):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = check_report.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_paths_and_exit_codes(self):
        run, raw = make_run(self.tmp, GOOD)
        for path in (run, raw, os.path.join(raw, "report.md")):
            rc, out, _ = self.run_cli([path])
            self.assertEqual(rc, 0, out)
        rc, out, _ = self.run_cli([run, "--json"])
        self.assertIn('"ok": true', out)
        with open(os.path.join(raw, "report.md"), "w", encoding="utf-8") as fh:
            fh.write("# t\n")
        self.assertEqual(self.run_cli([run])[0], 1)
        self.assertEqual(self.run_cli([os.path.join(self.tmp, "nope")])[0], 2)

    def call_deliver(self, argv):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with mock.patch.object(deliver.delivery, "find_browser", return_value=None):
                rc = deliver.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_deliver_warns_but_still_builds(self):
        run, _raw = make_run(self.tmp, "# t\n\n## 一页看懂\n\n- x [推测]\n")
        rc, out, _ = self.call_deliver([run, "--no-open"])
        self.assertEqual(rc, 0)
        self.assertIn("硬错误", out)
        self.assertIn("文件照常生成", out)
        self.assertTrue(
            os.path.isfile(os.path.join(run, "Example Store 对标拆解报告.html"))
        )

    def test_deliver_strict_refuses(self):
        run, _raw = make_run(self.tmp, "# t\n\n## 一页看懂\n\n- x [推测]\n")
        rc, _out, err = self.call_deliver([run, "--no-open", "--strict"])
        self.assertEqual(rc, 1)
        self.assertIn("--strict", err)
        self.assertFalse(
            os.path.exists(os.path.join(run, "Example Store 对标拆解报告.html"))
        )

    def test_deliver_strict_passes_clean_report(self):
        run, _raw = make_run(self.tmp, GOOD)
        rc, out, _ = self.call_deliver([run, "--no-open", "--strict"])
        self.assertEqual(rc, 0)
        self.assertNotIn("硬错误", out)


if __name__ == "__main__":
    unittest.main()
