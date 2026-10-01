"""Tests for collect.py TLS handling (context flags, Windows root refresh).

Run from the repository root:
    python -m unittest discover -s tests
"""

import contextlib
import os
import ssl
import subprocess
import sys
import unittest
from unittest import mock
from urllib.error import URLError

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(
    os.path.dirname(HERE), "skills", "shopify-store-teardown", "scripts"
)
sys.path.insert(0, SCRIPTS)

import collect  # noqa: E402


def cert_error():
    exc = ssl.SSLCertVerificationError(
        1, "certificate verify failed: self-signed certificate in certificate chain"
    )
    return URLError(exc)


_REAL_MAKE_CONTEXT = collect.make_ssl_context


def real_context():
    # sys.platform is patched to win32 in some tests; build the context the
    # host way (Windows-only load_default_certs APIs are absent elsewhere).
    with mock.patch.object(sys, "platform", HOST_PLATFORM):
        return _REAL_MAKE_CONTEXT()


HOST_PLATFORM = sys.platform


class FakeResp(object):
    def __init__(self, url, body=b'{"ok": true}'):
        self.url = url
        self.body = body
        self.headers = {}

    def getcode(self):
        return 200

    def geturl(self):
        return self.url

    def read(self, n=-1):
        return self.body

    def close(self):
        pass


class FakeOpener(object):
    """Fails with a certificate error for the first ``fail`` opens."""

    def __init__(self, state):
        self.state = state

    def open(self, req, timeout=None):
        self.state["opens"] += 1
        if self.state["opens"] <= self.state["fail"]:
            raise cert_error()
        return FakeResp(req.full_url)


class TestSslContext(unittest.TestCase):
    def test_strict_flag_cleared_but_verification_kept(self):
        ctx = collect.make_ssl_context()
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)
        strict = getattr(ssl, "VERIFY_X509_STRICT", 0)
        if strict:
            self.assertFalse(ctx.verify_flags & strict)

    def test_fetcher_uses_relaxed_context(self):
        f = collect.Fetcher(delay=0)
        self.assertEqual(f.ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(f.ctx.check_hostname)

    def test_cert_note_per_platform(self):
        self.assertIn("Install Certificates", collect.cert_note("darwin"))
        self.assertIn("浏览器", collect.cert_note("win32"))
        self.assertIn("ca-certificates", collect.cert_note("linux"))

    def test_site_root(self):
        self.assertEqual(
            collect.site_root("https://web.archive.org/cdx/search?url=x"),
            "https://web.archive.org/",
        )
        self.assertIsNone(collect.site_root("https://a'b.com/"))
        self.assertIsNone(collect.site_root("ftp://example.com/"))


class TestWindowsRootRefresh(unittest.TestCase):
    def patches(self, state, platform=None, run=None):
        """ExitStack with the opener / context (and optionally platform, run)
        patched; kept as one stack so the file stays Python 3.8 syntax."""

        def fake_build(*handlers):
            state["builds"] += 1
            return FakeOpener(state)

        stack = contextlib.ExitStack()
        stack.enter_context(
            mock.patch.object(collect, "build_opener", side_effect=fake_build)
        )
        stack.enter_context(
            mock.patch.object(collect, "make_ssl_context", side_effect=real_context)
        )
        if platform is not None:
            stack.enter_context(mock.patch.object(collect.sys, "platform", platform))
        if run is not None:
            stack.enter_context(mock.patch.object(collect.subprocess, "run", run))
        return stack

    def make_fetcher(self, fail):
        state = {"opens": 0, "fail": fail, "builds": 0}
        with self.patches(state):
            f = collect.Fetcher(delay=0, retries=2)
        return f, state

    def get(self, f, state, platform, url="https://example-store.com/meta.json"):
        run = mock.Mock(return_value=None)
        with self.patches(state, platform, run):
            r = f.get(url, collect.ACCEPT_JSON)
        return r, run

    def test_windows_refreshes_once_and_retries(self):
        f, state = self.make_fetcher(fail=1)
        r, run = self.get(f, state, "win32")
        self.assertEqual(r["status"], 200)
        self.assertEqual(run.call_count, 1)
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[0], "powershell")
        self.assertIn("-NonInteractive", cmd)
        self.assertIn("'https://example-store.com/'", cmd[-1])
        self.assertEqual(run.call_args[1]["timeout"], collect.WIN_ROOT_REFRESH_TIMEOUT)
        self.assertEqual(state["opens"], 2)
        self.assertEqual(state["builds"], 2)  # context rebuilt after refresh
        self.assertIn(collect.WIN_ROOT_REFRESH_NOTE, r["note"])
        self.assertNotIn(collect.CERT_NOTE, r["note"])

    def test_windows_refresh_at_most_once_per_host(self):
        f, state = self.make_fetcher(fail=99)
        r, run = self.get(f, state, "win32")
        self.assertIsNone(r["status"])
        self.assertIn(collect.CERT_NOTE, r["note"])
        self.assertEqual(run.call_count, 1)
        self.assertEqual(state["opens"], 2)  # original + one retry, no more
        r2, run2 = self.get(f, state, "win32", "https://example-store.com/x.json")
        self.assertEqual(run2.call_count, 0)
        self.assertIsNone(r2["status"])

    def test_non_windows_never_calls_powershell(self):
        for platform in ("darwin", "linux"):
            f, state = self.make_fetcher(fail=1)
            r, run = self.get(f, state, platform)
            self.assertEqual(run.call_count, 0)
            self.assertIsNone(r["status"])
            self.assertEqual(state["opens"], 1)

    def test_missing_powershell_is_skipped(self):
        f, state = self.make_fetcher(fail=1)
        run = mock.Mock(side_effect=FileNotFoundError("powershell"))
        with self.patches(state, "win32", run):
            r = f.get("https://example-store.com/", collect.ACCEPT_HTML)
        self.assertEqual(run.call_count, 1)
        self.assertIsNone(r["status"])
        self.assertEqual(state["opens"], 1)

    def run_main(self, fail):
        """Run collect.main on win32 with a fake opener; return stdout + status."""
        import io
        import json
        import shutil
        import tempfile

        state = {"opens": 0, "fail": fail, "builds": 0}
        tmp = tempfile.mkdtemp(prefix="store-teardown-tls-")
        self.addCleanup(shutil.rmtree, tmp, True)
        out = os.path.join(tmp, "raw")
        run = mock.Mock(return_value=None)
        buf = io.StringIO()
        with self.patches(state, "win32", run), contextlib.redirect_stdout(buf):
            collect.main(
                [
                    "example-store.com",
                    "--depth",
                    "L0",
                    "--skip-offsite",
                    "--no-summarize",
                    "--delay",
                    "0",
                    "--out",
                    out,
                ]
            )
        status = []
        for root, _dirs, files in os.walk(out):
            if "_status.json" in files:
                with open(os.path.join(root, "_status.json"), encoding="utf-8") as fh:
                    status = json.load(fh).get("requests", [])
        return buf.getvalue(), status

    def test_retry_success_prints_no_cert_hint(self):
        stdout, status = self.run_main(fail=1)
        self.assertNotIn(collect.CERT_NOTE, stdout)
        self.assertNotIn("提示:", stdout)
        self.assertTrue(status)
        self.assertIn(collect.WIN_ROOT_REFRESH_NOTE, status[0]["note"])
        self.assertEqual(collect.cert_failures(status), [])

    def test_final_failure_prints_cert_hint(self):
        stdout, status = self.run_main(fail=99)
        self.assertIn("提示: %s" % collect.CERT_NOTE, stdout)
        self.assertTrue(collect.cert_failures(status))

    def test_cert_failures_only_counts_failed_requests(self):
        ok = {"http_status": 200, "note": collect.WIN_ROOT_REFRESH_NOTE}
        stale = {"http_status": 200, "note": collect.CERT_NOTE}
        bad = {"http_status": None, "note": collect.CERT_NOTE}
        self.assertEqual(collect.cert_failures([ok, stale, bad]), [bad])

    def test_refresh_timeout_counts_as_ran(self):
        run = mock.Mock(side_effect=subprocess.TimeoutExpired("powershell", 20))
        self.assertTrue(collect.refresh_windows_roots("https://a.com/x", run=run))


if __name__ == "__main__":
    unittest.main()
