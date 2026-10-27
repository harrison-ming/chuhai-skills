"""Failure path of shopify-store-teardown: a site that does not resolve.

1. collect.py against a host whose DNS lookup fails (mocked getaddrinfo)
   finishes in seconds, sends one lookup per host and flags the run with
   the ``site_unreachable`` warning.
2. The fixture ``tests/fixtures/unreachable`` plus the short failed report
   (``tests/golden/report-failed.md``) passes check_report with status
   ``failed`` and deliver.py turns it into a delivered file.
"""

import contextlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "skills", "shopify-store-teardown", "scripts")
sys.path.insert(0, SCRIPTS)

import check_report  # noqa: E402
import collect  # noqa: E402
import summarize  # noqa: E402

RAW_DIRNAME = "原始数据"  # 原始数据
FIXTURE = os.path.join(HERE, "fixtures", "unreachable", "raw")
FAILED_REPORT = os.path.join(HERE, "golden", "report-failed.md")


class FailurePathCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="teardown-failed-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestCollectUnresolvable(FailurePathCase):
    def test_dns_failure_is_fast_and_flagged(self):
        lookups = []

        def no_dns(host, *args, **kwargs):
            lookups.append(host)
            raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided")

        out = io.StringIO()
        t0 = time.time()
        with mock.patch("socket.getaddrinfo", no_dns), contextlib.redirect_stdout(out):
            rc = collect.main(
                ["no-such-store.example", "--depth", "L1", "--out-root", self.tmp]
            )
        elapsed = time.time() - t0
        self.assertEqual(rc, 0)
        self.assertLess(elapsed, 10)
        # one lookup per host: the store, rdap.org, web.archive.org
        self.assertEqual(
            sorted(set(lookups)),
            ["no-such-store.example", "rdap.org", "web.archive.org"],
        )
        self.assertEqual(len(lookups), 3)
        run_dir = os.path.join(self.tmp, os.listdir(self.tmp)[0])
        with open(
            os.path.join(run_dir, RAW_DIRNAME, "summary.json"), encoding="utf-8"
        ) as fh:
            summary = json.load(fh)
        self.assertIn("site_unreachable", summary["warnings"])
        self.assertEqual(summary["identity"]["status"], "unconfirmed")
        self.assertIn("failed", out.getvalue())


class TestInitOnly(FailurePathCase):
    def test_init_only_creates_run_folder_without_network(self):
        def no_network(*args, **kwargs):
            raise AssertionError("--init-only must not touch the network")

        out = io.StringIO()
        args = ["no-such-store.example", "--init-only", "--out-root", self.tmp]
        with mock.patch("socket.getaddrinfo", no_network):
            with contextlib.redirect_stdout(out):
                rc = collect.main(args)
        self.assertEqual(rc, 0)
        runs = os.listdir(self.tmp)
        self.assertEqual(len(runs), 1)
        run_dir = os.path.join(self.tmp, runs[0])
        self.assertTrue(os.path.isdir(os.path.join(run_dir, RAW_DIRNAME, "raw")))
        self.assertIn(run_dir, out.getvalue())


class TestFailedReportDelivered(FailurePathCase):
    def make_run(self):
        run_dir = os.path.join(self.tmp, "no-such-store.example run")
        data = os.path.join(run_dir, RAW_DIRNAME)
        shutil.copytree(FIXTURE, os.path.join(data, "raw"))
        summary = summarize.summarize_run(data)
        shutil.copy(FAILED_REPORT, os.path.join(data, "report.md"))
        return run_dir, summary

    def test_fixture_flags_unreachable(self):
        _run_dir, summary = self.make_run()
        self.assertIn("site_unreachable", summary["warnings"])
        self.assertIn("site_unreachable", summarize.human_digest(summary))

    def test_reachable_fixtures_not_flagged(self):
        for name in ("normal", "blocked", "not_shopify"):
            run_dir = os.path.join(self.tmp, name)
            shutil.copytree(
                os.path.join(HERE, "fixtures", name, "raw"),
                os.path.join(run_dir, "raw"),
            )
            summary = summarize.summarize_run(run_dir)
            self.assertNotIn("site_unreachable", summary["warnings"], name)

    def test_failed_report_passes_check(self):
        with open(FAILED_REPORT, encoding="utf-8") as fh:
            result = check_report.check_text(fh.read())
        self.assertTrue(result.ok, [i.as_dict() for i in result.errors])
        self.assertEqual(result.warnings, [])
        self.assertEqual(result.status, "failed")

    def test_deliver_writes_html(self):
        run_dir, _summary = self.make_run()
        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(SCRIPTS, "deliver.py"),
                run_dir,
                "--html-only",
                "--no-open",
                "--strict",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        top = [n for n in os.listdir(run_dir) if n.endswith(".html")]
        self.assertEqual(len(top), 1)
        with open(os.path.join(run_dir, top[0]), encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("failed", html)
        self.assertNotIn("products.json", html.split("附录")[0])


if __name__ == "__main__":
    unittest.main()
