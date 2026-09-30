"""Tests for delivery.py (paths, names, browser lookup) and deliver.py.

Run from the repository root:
    python -m unittest discover -s tests
"""

import base64
import contextlib
import datetime
import io
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import HTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.join(os.path.dirname(HERE), "skills", "shopify-store-teardown")
SCRIPTS = os.path.join(SKILL, "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, HERE)

import collect  # noqa: E402
import deliver  # noqa: E402
import delivery  # noqa: E402
from test_summarize import FakeStore  # noqa: E402

# 1x1 transparent PNG
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA"
    "60e6kgAAAABJRU5ErkJggg=="
)


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="store-teardown-delivery-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def mkdir(self, *parts):
        path = os.path.join(self.tmp, *parts)
        os.makedirs(path, exist_ok=True)
        return path


class TestOutputRoot(TmpCase):
    def test_cli_wins(self):
        env = {delivery.ENV_OUTPUT: os.path.join(self.tmp, "env")}
        got = delivery.resolve_output_root(
            os.path.join(self.tmp, "cli"), environ=env, sandbox=self.tmp
        )
        self.assertEqual(got, os.path.join(self.tmp, "cli"))

    def test_env_used_as_root(self):
        env = {delivery.ENV_OUTPUT: os.path.join(self.tmp, "env")}
        got = delivery.resolve_output_root(None, environ=env, sandbox=self.tmp)
        self.assertEqual(got, os.path.join(self.tmp, "env"))

    def test_blank_env_ignored_then_sandbox(self):
        sandbox = self.mkdir("outputs")
        got = delivery.resolve_output_root(
            None, environ={delivery.ENV_OUTPUT: "  "}, sandbox=sandbox
        )
        self.assertEqual(got, os.path.join(sandbox, "出海拆解报告"))

    def test_missing_sandbox_falls_to_documents(self):
        home = self.mkdir("home")
        docs = self.mkdir("home", "Documents")
        got = delivery.resolve_output_root(
            None,
            environ={},
            platform="darwin",
            home=home,
            sandbox=os.path.join(self.tmp, "nope"),
        )
        self.assertEqual(got, os.path.join(docs, "出海拆解报告"))

    def test_posix_without_documents_uses_home(self):
        home = self.mkdir("bare")
        got = delivery.documents_dir("linux", {}, home)
        self.assertEqual(got, home)

    def test_windows_profile_documents(self):
        prof = self.mkdir("win", "user")
        docs = self.mkdir("win", "user", "Documents")
        env = {"USERPROFILE": prof}
        self.assertEqual(delivery.documents_dir("win32", env, "/x"), docs)

    def test_windows_onedrive_documents(self):
        prof = self.mkdir("win2", "user")
        od = self.mkdir("win2", "OneDrive")
        docs = self.mkdir("win2", "OneDrive", "文档")
        env = {"USERPROFILE": prof, "OneDrive": od}
        self.assertEqual(delivery.documents_dir("win32", env, "/x"), docs)

    def test_windows_nothing_uses_profile(self):
        prof = self.mkdir("win3", "user")
        self.assertEqual(delivery.documents_dir("win32", {"USERPROFILE": prof}), prof)


class TestNames(TmpCase):
    def test_run_dir_increments(self):
        root = os.path.join(self.tmp, "root")
        d = datetime.date(2026, 9, 30)
        a = delivery.create_run_dir(root, "example.com", d)
        b = delivery.create_run_dir(root, "example.com", d)
        c = delivery.create_run_dir(root, "example.com", d)
        self.assertEqual(os.path.basename(a), "example.com 对标拆解 2026-09-30")
        self.assertEqual(os.path.basename(b), "example.com 对标拆解 2026-09-30 (2)")
        self.assertEqual(os.path.basename(c), "example.com 对标拆解 2026-09-30 (3)")

    def test_sanitize(self):
        self.assertEqual(
            delivery.sanitize_filename('A/B:C*D?"E<F>G|H\\I'), "A B C D E F G H I"
        )
        self.assertEqual(delivery.sanitize_filename("  ...  "), "store")
        self.assertEqual(delivery.sanitize_filename("Shop.\n"), "Shop")
        self.assertEqual(
            delivery.report_basename("", "example.com"), "example.com 对标拆解报告"
        )
        self.assertEqual(
            delivery.report_basename("Tom's: Shop", "x.com"), "Tom's Shop 对标拆解报告"
        )


class TestBrowser(TmpCase):
    def test_windows_prefers_edge(self):
        pf86 = self.mkdir("pf86")
        edge = os.path.join(pf86, "Microsoft", "Edge", "Application")
        os.makedirs(edge)
        exe = os.path.join(edge, "msedge.exe")
        open(exe, "w").close()
        env = {"ProgramFiles(x86)": pf86, "ProgramFiles": self.mkdir("pf")}
        self.assertEqual(delivery.find_browser("win32", env, self.tmp), exe)

    def test_linux_which(self):
        def which(name):
            return "/usr/bin/chromium" if name == "chromium" else None

        self.assertEqual(
            delivery.find_browser("linux", {}, self.tmp, which=which),
            "/usr/bin/chromium",
        )

    def test_none(self):
        self.assertIsNone(
            delivery.find_browser("linux", {}, self.tmp, which=lambda n: None)
        )


class TestMarkdown(TmpCase):
    def render(self, md, base=None):
        conv = deliver.Converter(base or self.tmp)
        return conv.convert(md), conv

    def test_table_alignment_and_zebra_markup(self):
        md = "| 商品 | 价格 | 数量 |\n|---|---|:---:|\n| A | $100 | 3 |\n| B | $1,200.50 | 4 |"
        out, _ = self.render(md)
        self.assertIn('<th class="al-right nw">价格</th>', out)
        self.assertIn('<td class="al-right nw">$1,200.50</td>', out)
        self.assertIn('<td class="al-center nw">3</td>', out)
        self.assertIn('<th class="nw">商品</th>', out)

    def test_lists_nested_and_ordered(self):
        md = "- a\n  - a1\n  - a2\n- b\n\n3. x\n4. y"
        out, _ = self.render(md)
        self.assertIn(
            "<ul><li>a<ul><li>a1</li><li>a2</li></ul></li><li>b</li></ul>", out
        )
        self.assertIn('<ol start="3"><li>x</li><li>y</li></ol>', out)

    def test_inline(self):
        out, _ = self.render(
            "**粗** *斜* `a<b` [链接](https://x.com/a?b=1&c=2) [坏](javascript:alert(1))"
        )
        self.assertIn("<strong>粗</strong>", out)
        self.assertIn("<em>斜</em>", out)
        self.assertIn("<code>a&lt;b</code>", out)
        self.assertIn('<a href="https://x.com/a?b=1&amp;c=2">链接</a>', out)
        self.assertNotIn("javascript", out.split("坏")[0][-40:])
        self.assertNotIn('href="javascript', out)

    def test_html_escaped_but_sub_br_pass(self):
        out, _ = self.render("<script>x</script> <sub>小字</sub><br> <b>no</b>")
        self.assertIn("&lt;script&gt;", out)
        self.assertNotIn("<script>", out)
        self.assertIn("<sub>小字</sub><br>", out)
        self.assertIn("&lt;b&gt;", out)

    def test_no_false_emphasis_in_words_and_paths(self):
        # Real cases from delivered reports.
        out, _ = self.render(
            "检测到 __NEXT_DATA__ 标记, 见 raw/products-*.json, raw/product-*.html 两类文件"
        )
        self.assertNotIn("<strong>", out)
        self.assertNotIn("<em>", out)
        self.assertIn("__NEXT_DATA__", out)
        self.assertIn("raw/products-*.json, raw/product-*.html", out)
        out, _ = self.render("snake_case_name 和 a*b*c 和 2*3*4 和 -*.json 和 *.html")
        self.assertNotIn("<em>", out)
        self.assertNotIn("<strong>", out)

    def test_code_is_never_parsed(self):
        out, _ = self.render("`__NEXT_DATA__` `raw/*-*.json` `**x**` `[已核实]`")
        self.assertIn("<code>__NEXT_DATA__</code>", out)
        self.assertIn("<code>raw/*-*.json</code>", out)
        self.assertIn("<code>**x**</code>", out)
        self.assertIn("<code>[已核实]</code>", out)
        self.assertNotIn("badge", out)

    def test_emphasis_still_works_next_to_cjk(self):
        out, _ = self.render("这是**重点**内容, 也有*斜体*和 **Bold** 与 *Hello.*")
        self.assertIn("这是<strong>重点</strong>内容", out)
        self.assertIn("<em>斜体</em>", out)
        self.assertIn("<strong>Bold</strong>", out)
        self.assertIn("<em>Hello.</em>", out)

    def test_short_token_cells_do_not_wrap(self):
        md = (
            "| 编号 | 等级 | 内容 | 来源 | 时间 |\n|---|---|---|---|---|\n"
            "| E001 | E1 | 首页源码里有很长很长的一段说明文字 | raw/homepage.html | 2026-09-30 |\n"
            "| E012 | E1-E4 | 价格 | raw/products-*.json | 2026 年 3 月 |"
        )
        out, _ = self.render(md)
        for token in ("E001", "E1", "2026-09-30", "E012", "E1-E4", "2026 年 3 月"):
            self.assertIn('class="nw">%s</td>' % token, out)
        self.assertIn("<td>首页源码里有很长很长的一段说明文字</td>", out)
        self.assertNotIn('class="nw">raw/homepage.html', out)
        self.assertIn("td.nw, th.nw { white-space: nowrap;", deliver.CSS)
        self.assertIn('<th class="nw">编号</th>', out)
        self.assertIn(
            '<td class="al-right nw">105 美元</td>',
            self.render("| a | 价格 |\n|---|---|\n| x | 105 美元 |")[0],
        )

    def test_badges(self):
        out, _ = self.render(
            "[已核实] [店铺自称] [第三方说法] [页面显示] [推测] [有矛盾] [查不到] [其他]"
        )
        for cls in ("ok", "self", "third", "page", "guess", "conflict", "unknown"):
            self.assertIn('class="badge badge-%s"' % cls, out)
        self.assertIn("[其他]", out)

    def test_summary_card_appendix_and_closing(self):
        md = (
            "# 店 对标拆解报告\n\n<sub>x.com · 分析日期</sub>\n\n"
            "## 一页看懂\n\n- 结论 [已核实]\n\n## 正文\n\n文字\n\n"
            "## 附录: 证据与数据来源\n\n### 证据表\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n"
            "---\n\n### 本报告没有覆盖的\n\n- 一条\n\n<sub>由 shopify-store-teardown (chuhai-skills) 生成</sub>\n"
        )
        out, conv = self.render(md)
        self.assertEqual(conv.title, "店 对标拆解报告")
        self.assertRegex(out, r'<section class="summary-card">\s*<h2>一页看懂</h2>')
        self.assertRegex(out, r'<section class="appendix">\s*<h2>附录')
        tail = out.split('<section class="closing">')[1]
        self.assertIn("本报告没有覆盖的", tail)
        self.assertNotIn("appendix", tail)
        self.assertIn("<sub>x.com · 分析日期</sub>", out)
        doc, _w = deliver.build_html(md, self.tmp, "x", "2026-09-30")
        self.assertNotIn('doc-footer">', doc)  # template already has a footer

    def test_template_structure_renders(self):
        with open(
            os.path.join(SKILL, "references", "report-template.md"), encoding="utf-8"
        ) as fh:
            text = fh.read()
        blocks = re.findall(r"```markdown\n(.*?)\n```", text, re.S)
        self.assertGreaterEqual(len(blocks), 2)
        deep = [b for b in blocks if "一页看懂" in b and "附录" in b][-1]
        closing = [b for b in blocks if "本报告没有覆盖的" in b][0]
        doc, _w = deliver.build_html(deep + "\n\n" + closing, self.tmp, "x", "d")
        self.assertIn('<section class="summary-card">', doc)
        self.assertIn('<section class="appendix">', doc)
        self.assertIn('<section class="closing">', doc)
        self.assertIn("<table>", doc)
        self.assertNotIn("```", doc)

    def test_image_base64_and_missing(self):
        img_dir = self.mkdir("raw", "shots")
        with open(os.path.join(img_dir, "a.png"), "wb") as fh:
            fh.write(PNG_1PX)
        out, conv = self.render(
            "![首页](raw/shots/a.png)\n\n![没有](raw/none.png)", base=self.tmp
        )
        self.assertIn('src="data:image/png;base64,', out)
        self.assertIn('alt="首页"', out)
        self.assertIn("图片缺失", out)
        self.assertTrue(any("找不到图片" in w for w in conv.warnings))

    def test_cjk_soft_wrap(self):
        out, _ = self.render("第一行\n第二行\nand more")
        self.assertIn("<p>第一行第二行 and more</p>", out)


TSV = (
    "product_id\thandle\turl\ttitle\tproduct_type\tvendor\ttags\tprice_min\tprice_max\t"
    "compare_at_max\tcurrency\tvariant_count\toptions\tavailable\tcreated_at\t"
    "published_at\tsource\n"
    '1\ta\thttps://x.com/products/a\t=HYPERLINK("http://evil")\tShoes\tV\t\t100\t110\t'
    "150\tUSD\t3\tSize\ttrue\t2024-02-01T10:00:00-08:00\t2026-06-01T00:00:00Z\t"
    "products_json\n"
    '2\tb\thttps://x.com/products/b\t"Quoted" -tee\t@type\tV\t\t-5\t20\t\tUSD\t1\t\t'
    "false\t\t2025-01-01T00:00:00Z\tsitemap\n"
)


def make_run(root, report_md, tsv=True, shop="Example: Store"):
    run = os.path.join(root, "example.com 对标拆解 2026-09-30")
    raw = os.path.join(run, "原始数据")
    os.makedirs(os.path.join(raw, "raw"))
    with open(os.path.join(raw, "summary.json"), "w", encoding="utf-8") as fh:
        fh.write('{"domain": "example.com", "shop": {"name": "%s"}}' % shop)
    if report_md is not None:
        with open(os.path.join(raw, "report.md"), "w", encoding="utf-8") as fh:
            fh.write(report_md)
    if tsv:
        with open(os.path.join(raw, "products.tsv"), "w", encoding="utf-8") as fh:
            fh.write(TSV)
    return run, raw


def pdf_target(cmd):
    return [a for a in cmd if a.startswith("--print-to-pdf=")][0].split("=", 1)[1]


def write_pdf(path):
    with open(path, "wb") as fh:
        fh.write(b"%PDF-1.4\n" + b"0" * 2048 + b"\n%%EOF\n")


class FakeProc(object):
    """Minimal stand-in for subprocess.Popen."""

    pid = None

    def __init__(self, returncode=0, running=False):
        self.returncode = None if running else returncode
        self.stopped = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.stopped = True
        self.returncode = -15

    kill = terminate

    def wait(self, timeout=None):
        return self.returncode


class TestDeliver(TmpCase):
    def call(self, argv):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = deliver.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_csv(self):
        run, raw = make_run(self.tmp, "# t\n")
        path = os.path.join(self.tmp, "o.csv")
        n = deliver.write_products_csv(os.path.join(raw, "products.tsv"), path)
        self.assertEqual(n, 2)
        with open(path, "rb") as fh:
            data = fh.read()
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        text = data.decode("utf-8-sig")
        lines = text.splitlines()
        self.assertEqual(
            lines[0],
            "商品名称,类型,最低价,最高价,原价,币种,是否有货,规格数,首次上架时间,商品链接",
        )
        self.assertTrue(
            lines[1].startswith(
                '"\'=HYPERLINK(""http://evil"")",Shoes,100,110,150,USD,是,3,2024-02-01,'
            )
        )
        self.assertIn("'@type", lines[2])
        self.assertIn(",-5,", lines[2])  # plain numbers untouched
        self.assertIn(",否,", lines[2])
        self.assertIn(",2025-01-01,", lines[2])  # falls back to published_at

    def test_missing_report_is_fatal(self):
        run, _raw = make_run(self.tmp, None)
        rc, _out, err = self.call([run, "--no-open", "--html-only"])
        self.assertEqual(rc, 1)
        self.assertIn("report.md", err)

    def test_html_fallback_when_no_browser(self):
        run, raw = make_run(self.tmp, "# 报告\n\n## 一页看懂\n\n- 好 [推测]\n")
        stale = os.path.join(run, "Example Store 对标拆解报告.pdf")
        open(stale, "wb").close()
        with mock.patch.object(deliver.delivery, "find_browser", return_value=None):
            rc, out, _err = self.call([run, "--no-open"])
        self.assertEqual(rc, 0)
        html_top = os.path.join(run, "Example Store 对标拆解报告.html")
        self.assertTrue(os.path.isfile(html_top))
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.isfile(os.path.join(raw, "report.html")))
        self.assertTrue(os.path.isfile(os.path.join(raw, "说明.txt")))
        self.assertTrue(os.path.isfile(os.path.join(run, "商品清单.csv")))
        self.assertIn("另存为 PDF", out)
        self.assertIn("没找到", out)

    def test_pdf_success_with_fake_browser(self):
        run, raw = make_run(self.tmp, "# 报告\n\n正文\n", tsv=False)
        seen = []

        def popen(cmd, **kw):
            seen.append(cmd)
            write_pdf(pdf_target(cmd))
            return FakeProc(returncode=0)

        orig = deliver.print_pdf

        def pp(browser, html_path, pdf_path):
            return orig(browser, html_path, pdf_path, popen=popen, poll_interval=0)

        find = mock.patch.object(
            deliver.delivery, "find_browser", return_value="/fake/chrome"
        )
        with find, mock.patch.object(deliver, "print_pdf", side_effect=pp):
            rc, out, _err = self.call([run, "--no-open"])
        self.assertEqual(rc, 0)
        self.assertIn("--headless=new", seen[0])
        self.assertTrue(any(a.startswith("--user-data-dir=") for a in seen[0]))
        self.assertIn("--no-pdf-header-footer", seen[0])
        self.assertTrue(
            os.path.isfile(os.path.join(run, "Example Store 对标拆解报告.pdf"))
        )
        self.assertFalse(os.path.exists(os.path.join(raw, "_render.pdf")))
        self.assertIn("报告 (PDF)", out)
        self.assertNotIn("products.tsv", out)  # silent without --verbose
        self.assertNotIn("商品清单 (", out)

    def test_browser_exit_code_falls_back_with_plain_message(self):
        run, raw = make_run(self.tmp, "# 报告\n\n正文\n", tsv=False)
        orig = deliver.print_pdf

        def pp(browser, html_path, pdf_path):
            return orig(
                browser,
                html_path,
                pdf_path,
                popen=lambda cmd, **kw: FakeProc(returncode=1002),
                poll_interval=0,
            )

        find = mock.patch.object(
            deliver.delivery, "find_browser", return_value="/fake/edge"
        )
        with find, mock.patch.object(deliver, "print_pdf", side_effect=pp):
            rc, out, _err = self.call([run, "--no-open"])
        self.assertEqual(rc, 0)
        self.assertTrue(
            os.path.isfile(os.path.join(run, "Example Store 对标拆解报告.html"))
        )
        self.assertIn("没法打印 PDF", out)
        self.assertIn("后台服务", out)
        self.assertIn("另存为 PDF", out)

    def test_browser_that_never_exits_is_stopped(self):
        pdf = os.path.join(self.tmp, "o.pdf")
        procs = []

        def popen(cmd, **kw):
            write_pdf(pdf_target(cmd))
            procs.append(FakeProc(running=True))
            return procs[-1]

        ok, _note = deliver.print_pdf(
            "/b", os.path.join(self.tmp, "a.html"), pdf, popen=popen, poll_interval=0
        )
        self.assertTrue(ok)
        self.assertEqual(len(procs), 1)
        self.assertTrue(procs[0].stopped)

    def test_print_pdf_retries_old_headless(self):
        pdf = os.path.join(self.tmp, "o.pdf")
        seen = []

        def popen(cmd, **kw):
            seen.append(cmd[1])
            if cmd[1] == "--headless":
                write_pdf(pdf_target(cmd))
                return FakeProc(returncode=0)
            return FakeProc(returncode=1)

        ok, _note = deliver.print_pdf(
            "/b", os.path.join(self.tmp, "a.html"), pdf, popen=popen, poll_interval=0
        )
        self.assertTrue(ok)
        self.assertEqual(seen, ["--headless=new", "--headless"])

    def test_print_pdf_rejects_tiny_output(self):
        pdf = os.path.join(self.tmp, "o.pdf")

        def popen(cmd, **kw):
            with open(pdf_target(cmd), "wb") as fh:
                fh.write(b"%PDF tiny")
            return FakeProc(returncode=0)

        ok, _note = deliver.print_pdf(
            "/b", os.path.join(self.tmp, "a.html"), pdf, popen=popen, poll_interval=0
        )
        self.assertFalse(ok)
        self.assertFalse(os.path.exists(pdf))

    def test_print_pdf_timeout(self):
        pdf = os.path.join(self.tmp, "o.pdf")
        ok, note = deliver.print_pdf(
            "/b",
            os.path.join(self.tmp, "a.html"),
            pdf,
            timeout=0,
            popen=lambda cmd, **kw: FakeProc(running=True),
            poll_interval=0,
        )
        self.assertFalse(ok)
        self.assertIn("超时", note)

    def test_title_overrides_file_name(self):
        run, raw = make_run(self.tmp, "正文没有一级标题\n", tsv=False)
        with mock.patch.object(deliver.delivery, "find_browser", return_value=None):
            rc, out, _err = self.call([run, "--no-open", "--title", "多店对比报告"])
        self.assertEqual(rc, 0)
        top = os.path.join(run, "多店对比报告.html")
        self.assertTrue(os.path.isfile(top))
        self.assertFalse(
            os.path.exists(os.path.join(run, "Example Store 对标拆解报告.html"))
        )
        with open(top, encoding="utf-8") as fh:
            self.assertIn("<title>多店对比报告</title>", fh.read())
        self.assertIn("多店对比报告.html", out)

    def test_title_is_sanitized_and_blank_ignored(self):
        run, _raw = make_run(self.tmp, "# r\n", tsv=False)
        with mock.patch.object(deliver.delivery, "find_browser", return_value=None):
            rc, _out, _err = self.call([run, "--no-open", "--title", 'a/b:c?"'])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.isfile(os.path.join(run, "a b c.html")))
            rc, _out, _err = self.call([run, "--no-open", "--title", "  "])
            self.assertEqual(rc, 0)
        self.assertTrue(
            os.path.isfile(os.path.join(run, "Example Store 对标拆解报告.html"))
        )

    def test_readme_by_folder_type(self):
        run, raw = make_run(self.tmp, "# t\n")
        delivery.write_readme(raw)
        with open(os.path.join(raw, "说明.txt"), encoding="utf-8-sig") as fh:
            single = fh.read()
        self.assertIn("report.html", single)
        self.assertIn("对标拆解报告", single)
        with open(os.path.join(raw, "compare.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")
        rc, out, _err = self.call([run, "--no-open", "--html-only"])
        self.assertEqual(rc, 0)
        with open(os.path.join(raw, "说明.txt"), encoding="utf-8-sig") as fh:
            text = fh.read()
        self.assertIn("这是生成多店对比报告用的数据, 普通用户可以不看", text)

    def test_missing_tsv_is_silent_unless_verbose(self):
        run, raw = make_run(self.tmp, "# t\n", tsv=False)
        rc, out, _err = self.call([run, "--no-open", "--html-only"])
        self.assertEqual(rc, 0)
        self.assertNotIn("products.tsv", out)
        rc, out, _err = self.call([run, "--no-open", "--html-only", "--verbose"])
        self.assertIn("products.tsv", out)

    def test_accepts_raw_folder_path(self):
        run, raw = make_run(self.tmp, "# r\n", tsv=False)
        self.assertEqual(deliver.locate_run(raw), (run, raw))
        self.assertEqual(deliver.locate_run(run), (run, raw))


class TestCollectDefaultPath(TmpCase):
    def serve(self):
        server = HTTPServer(("127.0.0.1", 0), FakeStore)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def run_collect(self, port, extra):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = collect.main(
                ["http://127.0.0.1:%d/" % port, "--delay", "0", "--timeout", "5"]
                + ["--skip-offsite", "--no-summarize"]
                + extra
            )
        return rc, buf.getvalue()

    def test_out_root_and_increment(self):
        port = self.serve()
        root = os.path.join(self.tmp, "出海拆解报告")
        rc, out = self.run_collect(port, ["--out-root", root])
        self.assertEqual(rc, 0)
        name = "127.0.0.1 对标拆解 %s" % datetime.date.today().isoformat()
        raw = os.path.join(root, name, "原始数据")
        self.assertTrue(os.path.isfile(os.path.join(raw, "raw", "meta.json")))
        self.assertTrue(os.path.isfile(os.path.join(raw, "说明.txt")))
        self.assertIn("deliver.py", out)
        self.assertIn(os.path.join(root, name), out)
        rc, _out = self.run_collect(port, ["--out-root", root])
        self.assertTrue(
            os.path.isdir(os.path.join(root, name + " (2)", "原始数据", "raw"))
        )

    def test_env_root(self):
        port = self.serve()
        root = os.path.join(self.tmp, "envroot")
        with mock.patch.dict(os.environ, {delivery.ENV_OUTPUT: root}):
            rc, _out = self.run_collect(port, [])
        self.assertEqual(rc, 0)
        self.assertEqual(len(os.listdir(root)), 1)


if __name__ == "__main__":
    unittest.main()
