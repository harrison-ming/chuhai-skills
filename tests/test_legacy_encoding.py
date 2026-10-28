"""Chinese Windows with an older Python (no UTF-8 mode): code page 936.

Found on the 2026-10-27 clean-VM round (Windows 11 zh-CN + python.org
CPython 3.12, PYTHONUTF8 unset). Python 3.15 defaults to UTF-8 mode and hides
all of this. Simulated here without Windows:

- files other programs wrote: Windows PowerShell 5.1 ``Set-Content`` (ANSI,
  cp936) and ``Out-File`` (UTF-16 LE with BOM), Excel "CSV" (cp936);
- a console / pipe that can only encode GBK: PYTHONIOENCODING=gbk makes
  sys.stdout strict GBK, which is what Python 3.12 gives a pipe on that box.

Run from the repository root:
    python -m unittest discover -s tests
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SKILLS = os.path.join(ROOT, "skills")
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from shared import output  # noqa: E402
from test_check_report import GOOD  # noqa: E402
from test_delivery import make_run  # noqa: E402

# characters code page 936 cannot encode, next to plain Chinese
STRESS = "价格 £12.50 / ¥80, 已核对 ✓, 韩文 한국어"
TEARDOWN = os.path.join(SKILLS, "shopify-store-teardown", "scripts")


def gbk_env():
    env = dict(os.environ)
    env.pop("PYTHONUTF8", None)
    env["PYTHONIOENCODING"] = "gbk"
    env["CHUHAI_NO_OPEN"] = "1"
    return env


class DecodeText(unittest.TestCase):
    def test_utf8_plain_and_bom(self):
        self.assertEqual(output.decode_text("报告 £".encode("utf-8")), "报告 £")
        self.assertEqual(output.decode_text(b"\xef\xbb\xbf" + "报告".encode()), "报告")

    def test_utf16_from_out_file(self):
        self.assertEqual(output.decode_text("报告 ✓".encode("utf-16")), "报告 ✓")
        data = b"\xff\xfe" + "# 报告".encode("utf-16-le")
        self.assertEqual(output.decode_text(data), "# 报告")

    def test_ansi_cp936(self):
        with mock.patch("locale.getpreferredencoding", return_value="cp936"):
            self.assertEqual(output.decode_text("一页看懂".encode("gbk")), "一页看懂")
        # not the machine's code page (e.g. a UTF-8 Mac): GB18030 still wins
        with mock.patch("locale.getpreferredencoding", return_value="UTF-8"):
            self.assertEqual(output.decode_text("一页看懂".encode("gbk")), "一页看懂")

    def test_ansi_cp1252_on_english_windows(self):
        with mock.patch("locale.getpreferredencoding", return_value="cp1252"):
            self.assertEqual(output.decode_text("café £5".encode("cp1252")), "café £5")

    def test_never_raises(self):
        self.assertIsInstance(output.decode_text(b"\x81\xff\xfe\x00"), str)


class ReportWrittenByPowerShell(unittest.TestCase):
    """check_report.py and deliver.py read a report.md that is not UTF-8."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="legacy-enc-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, script, *args):
        p = subprocess.run(
            [sys.executable, os.path.join(TEARDOWN, script)] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=gbk_env(),
            timeout=120,
        )
        err = p.stderr.decode("gbk", "replace")
        self.assertNotIn("Traceback", err, err)
        return p.returncode, p.stdout.decode("gbk", "replace") + err

    def make(self, encoding):
        md = GOOD.replace(
            "## 价格怎么定的", "## 价格怎么定的\n\n- " + STRESS + " [推测] 依据: 测试"
        )
        run, raw = make_run(self.tmp, None)
        data = md.encode(encoding, errors="replace")
        if encoding == "utf-16-le":
            data = b"\xff\xfe" + data
        with open(os.path.join(raw, "report.md"), "wb") as fh:
            fh.write(data)
        return run

    def test_check_report_gbk_and_utf16(self):
        for enc in ("gbk", "utf-16-le"):
            run = self.make(enc)
            rc, out = self.run_cli("check_report.py", run)
            self.assertEqual(rc, 0, (enc, out))
            shutil.rmtree(run)

    def test_deliver_gbk_report(self):
        run = self.make("gbk")
        rc, out = self.run_cli("deliver.py", run, "--html-only", "--no-open")
        self.assertEqual(rc, 0, out)
        html = [f for f in os.listdir(run) if f.endswith(".html")]
        self.assertEqual(len(html), 1, os.listdir(run))
        with open(os.path.join(run, html[0]), encoding="utf-8") as fh:
            self.assertIn("一页看懂", fh.read())


class GbkConsole(unittest.TestCase):
    """Every skill's check_report prints findings that quote the report line;
    a strict GBK stdout must not crash on £ ✓ 한."""

    def test_check_report_prints_unencodable_quotes(self):
        tmp = tempfile.mkdtemp(prefix="legacy-console-")
        self.addCleanup(shutil.rmtree, tmp, True)
        md = os.path.join(tmp, "report.md")
        with open(md, "w", encoding="utf-8") as fh:
            # no status, no ending: findings quote these lines back
            fh.write("# 报告 " + STRESS + "\n\n## 一页看懂\n\n- " + STRESS + " 稳赚\n")
        for name in sorted(os.listdir(SKILLS)):
            script = os.path.join(SKILLS, name, "scripts", "check_report.py")
            if name.startswith("_") or not os.path.isfile(script):
                continue
            p = subprocess.run(
                [sys.executable, script, md],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=gbk_env(),
                timeout=120,
            )
            err = p.stderr.decode("gbk", "replace")
            self.assertNotIn("Traceback", err, (name, err))
            self.assertIn(p.returncode, (0, 1), (name, err))


if __name__ == "__main__":
    unittest.main()
