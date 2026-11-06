"""Tests for shared/fetch.py: retries on cut-off transfers, DNS fail-fast,
polite handling of 429 rate limiting and 5xx Retry-After.

A local HTTP server cuts the first responses short (IncompleteRead) or
resets the connection; DNS failures and HTTP answers are simulated with fake
openers and an injected sleep, so nothing leaves the machine and no test
really waits.
"""

import datetime
import email.message
import io
import os
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock
from urllib.error import HTTPError, URLError

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from shared import fetch  # noqa: E402

BODY = b"<html>" + b"x" * 2000 + b"</html>"


class CutHandler(BaseHTTPRequestHandler):
    """Serves BODY; the first ``fail`` requests are cut off (``mode``)."""

    state = {}

    def log_message(self, *args):  # keep test output quiet
        pass

    def do_GET(self):  # noqa: N802 - http.server naming
        st = self.state
        st["seen"].append(self.headers.get("Accept-Encoding"))
        if len(st["seen"]) <= st["fail"]:
            if st["mode"] == "reset":
                # SO_LINGER 0: close() sends RST instead of FIN
                self.connection.setsockopt(
                    socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00" * 2
                )
                self.connection.close()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(BODY)))
            self.end_headers()
            self.wfile.write(BODY[:100])
            self.wfile.flush()
            self.close_connection = True
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)


class ServerCase(unittest.TestCase):
    def start(self, fail, mode="cut"):
        CutHandler.state = {"seen": [], "fail": fail, "mode": mode}
        self.server = HTTPServer(("127.0.0.1", 0), CutHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%d/" % self.server.server_address[1]
        self.sleeps = []
        patcher = mock.patch.object(fetch.time, "sleep", self.sleeps.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        server = getattr(self, "server", None)
        if server is not None:
            server.shutdown()
            server.server_close()


class TestTransientRetry(ServerCase):
    def test_incomplete_read_retried_uncompressed(self):
        self.start(fail=2)
        r = fetch.Fetcher(delay=0, retries=2, timeout=5).get(self.url)
        self.assertEqual(r["status"], 200)
        self.assertEqual(r["body"], BODY)
        self.assertIn("retried 2", r["note"])
        seen = CutHandler.state["seen"]
        self.assertEqual(seen[0], "gzip, deflate")
        self.assertEqual(seen[1:], ["identity", "identity"])

    def test_cut_off_gets_at_least_transient_retries(self):
        # retries=0 still retries a cut-off transfer TRANSIENT_RETRIES times
        self.start(fail=fetch.TRANSIENT_RETRIES)
        r = fetch.Fetcher(delay=0, retries=0, timeout=5).get(self.url)
        self.assertEqual(r["status"], 200)
        self.assertEqual(len(CutHandler.state["seen"]), fetch.TRANSIENT_RETRIES + 1)

    def test_retries_are_bounded(self):
        self.start(fail=100)
        r = fetch.Fetcher(delay=0, retries=1, timeout=5).get(self.url)
        self.assertIsNone(r["status"])
        self.assertIn("IncompleteRead", r["note"])
        self.assertIn("retried %d" % fetch.TRANSIENT_RETRIES, r["note"])
        self.assertEqual(len(CutHandler.state["seen"]), fetch.TRANSIENT_RETRIES + 1)
        self.assertTrue(all(s <= fetch.MAX_BACKOFF for s in self.sleeps))

    def test_connection_reset_retried(self):
        self.start(fail=1, mode="reset")
        r = fetch.Fetcher(delay=0, retries=2, timeout=5).get(self.url)
        self.assertEqual(r["status"], 200)
        self.assertEqual(len(CutHandler.state["seen"]), 2)

    def test_backoff_capped(self):
        self.assertEqual(fetch.backoff(1), 1.5)
        self.assertEqual(fetch.backoff(2), 3.0)
        self.assertEqual(fetch.backoff(10), fetch.MAX_BACKOFF)


class FakeOpener(object):
    def __init__(self, errors):
        self.errors = errors
        self.urls = []

    def open(self, req, timeout=None):
        self.urls.append(req.full_url)
        raise self.errors(req.full_url)


class TestDnsFailFast(unittest.TestCase):
    def fetcher(self, errno):
        f = fetch.Fetcher(delay=0, retries=2)

        def err(url):
            if "other.example" in url:
                return URLError(ConnectionRefusedError(61, "refused"))
            return URLError(socket.gaierror(errno, "nodename nor servname provided"))

        f.opener = FakeOpener(err)
        return f

    def test_unresolvable_host_fails_once_then_skips(self):
        f = self.fetcher(getattr(socket, "EAI_NONAME", 8))
        with mock.patch.object(fetch.time, "sleep") as sleep:
            first = f.get("https://no-such-store.example/")
            later = [
                f.get("https://no-such-store.example/meta.json") for _ in range(10)
            ]
        self.assertIsNone(first["status"])
        self.assertIn("gaierror", first["note"])
        self.assertNotIn("retried", first["note"])
        self.assertEqual(f.opener.urls, ["https://no-such-store.example/"])
        sleep.assert_not_called()
        for r in later:
            self.assertIsNone(r["status"])
            self.assertEqual(r["elapsed_ms"], 0)
            self.assertIn(fetch.DNS_SKIP_NOTE, r["note"])

    def test_other_hosts_still_requested(self):
        f = self.fetcher(getattr(socket, "EAI_NONAME", 8))
        with mock.patch.object(fetch.time, "sleep"):
            f.get("https://no-such-store.example/")
            f.get("https://other.example/")
        self.assertEqual(len(f.opener.urls), 1 + 3)  # refused: normal retries

    def test_temporary_dns_failure_keeps_retries(self):
        f = self.fetcher(socket.EAI_AGAIN)
        with mock.patch.object(fetch.time, "sleep"):
            r = f.get("https://no-such-store.example/")
            f.get("https://no-such-store.example/x")
        self.assertIn("retried 2", r["note"])
        self.assertEqual(len(f.opener.urls), 6)  # not cached as dead

    def test_error_classifiers(self):
        self.assertTrue(fetch.is_dns_error(URLError(socket.gaierror(8, "x"))))
        self.assertFalse(fetch.is_dns_error(URLError(socket.timeout("t"))))
        import http.client

        self.assertTrue(fetch.is_transient_error(http.client.IncompleteRead(b"")))
        self.assertTrue(
            fetch.is_transient_error(URLError(http.client.RemoteDisconnected("x")))
        )
        self.assertTrue(fetch.is_transient_error(URLError(ConnectionResetError())))
        self.assertFalse(fetch.is_transient_error(URLError(socket.timeout("t"))))


class FakeResponse(object):
    def __init__(self, url, body=b"ok"):
        self.url = url
        self.body = body
        self.headers = email.message.Message()

    def getcode(self):
        return 200

    def geturl(self):
        return self.url

    def read(self, _n=None):
        return self.body

    def close(self):
        pass


class ScriptedOpener(object):
    """Answers each request with the next (status, headers) of ``script``;
    the last entry repeats. Status 200 returns a normal response."""

    def __init__(self, script):
        self.script = list(script)
        self.urls = []

    def open(self, req, timeout=None):
        self.urls.append(req.full_url)
        idx = min(len(self.urls) - 1, len(self.script) - 1)
        status, hdrs = self.script[idx]
        if status == 200:
            return FakeResponse(req.full_url)
        msg = email.message.Message()
        for k, v in (hdrs or {}).items():
            msg[k] = v
        raise HTTPError(req.full_url, status, "x", msg, io.BytesIO(b"slow down"))


class TestRateLimit(unittest.TestCase):
    def fetcher(self, script, **kw):
        self.sleeps = []
        kw.setdefault("delay", 0)
        f = fetch.Fetcher(sleep=self.sleeps.append, **kw)
        f.opener = ScriptedOpener(script)
        return f

    def test_numeric_retry_after_is_waited(self):
        f = self.fetcher([(429, {"Retry-After": "60"}), (200, None)])
        r = f.get("https://shop.example/products.json")
        self.assertEqual(r["status"], 200)
        self.assertTrue(r["rate_limited"])
        self.assertIn(60.0, self.sleeps)
        self.assertIn("rate limited (429), waited 60s, retried 1", r["note"])
        self.assertEqual(f.rate_limited_hosts(), {"shop.example": 1})
        self.assertEqual(f.rate_limit_waited, 60.0)

    def test_wait_is_capped_never_shortened(self):
        # asks for more than max_retry_after: give up instead of retrying early
        f = self.fetcher([(429, {"Retry-After": "600"})], max_retry_after=75)
        r = f.get("https://shop.example/products.json")
        self.assertEqual(r["status"], 429)
        self.assertEqual(len(f.opener.urls), 1)
        self.assertTrue(all(s <= 75 for s in self.sleeps))
        self.assertIn("more than the 75s limit", r["note"])

    def test_still_limited_after_retries(self):
        f = self.fetcher([(429, {"Retry-After": "5"})], rate_limit_retries=2)
        r = f.get("https://shop.example/x")
        self.assertEqual(r["status"], 429)
        self.assertEqual(len(f.opener.urls), 3)
        self.assertEqual(self.sleeps.count(5.0), 2)
        self.assertIn("waited 10s, retried 2, still limited", r["note"])

    def test_http_date_retry_after(self):
        now = datetime.datetime(2026, 11, 6, 12, 0, 0, tzinfo=datetime.timezone.utc)
        later = "Fri, 06 Nov 2026 12:00:30 GMT"
        self.assertEqual(fetch.parse_retry_after(later, now), 30.0)
        past = "Fri, 06 Nov 2026 11:00:00 GMT"
        self.assertEqual(fetch.parse_retry_after(past, now), 0.0)
        self.assertEqual(fetch.parse_retry_after("12", now), 12.0)
        self.assertIsNone(fetch.parse_retry_after("soon", now))
        self.assertIsNone(fetch.parse_retry_after("", now))
        self.assertIsNone(fetch.parse_retry_after(None, now))
        # end to end: a date 20 s ahead is waited (about) 20 s
        when = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            seconds=20
        )
        header = when.strftime("%a, %d %b %Y %H:%M:%S GMT")
        f = self.fetcher([(429, {"Retry-After": header}), (200, None)])
        r = f.get("https://shop.example/x")
        self.assertEqual(r["status"], 200)
        waits = [s for s in self.sleeps if s > 10]
        self.assertEqual(len(waits), 1)
        self.assertTrue(17 <= waits[0] <= 20, waits)

    def test_budget_exhaustion_returns_immediately(self):
        f = self.fetcher(
            [(429, {"Retry-After": "50"})], rate_limit_budget=120, rate_limit_retries=5
        )
        first = f.get("https://shop.example/a")
        self.assertEqual(first["status"], 429)
        self.assertEqual(self.sleeps.count(50.0), 2)  # 100 of 120 s used
        self.assertIn("wait budget exhausted", first["note"])
        before = len(f.opener.urls)
        self.sleeps[:] = []
        later = f.get("https://shop.example/b")
        self.assertEqual(later["status"], 429)
        self.assertEqual(len(f.opener.urls), before + 1)  # one request, no retry
        self.assertNotIn(50.0, self.sleeps)
        self.assertIn("rate limited (429), wait budget exhausted", later["note"])
        self.assertLessEqual(f.rate_limit_waited, 120)

    def test_per_host_delay_raised_after_429(self):
        f = self.fetcher(
            [(429, {"Retry-After": "1"}), (200, None)], delay=0.5, rate_limit_retries=1
        )
        self.assertEqual(f.host_delay("shop.example"), 0.5)
        f.get("https://shop.example/a")
        self.assertEqual(f.host_delay("shop.example"), fetch.RATE_LIMIT_DELAY_MIN)
        self.assertEqual(f.host_delay("other.example"), 0.5)
        f.opener = ScriptedOpener([(200, None)])
        self.sleeps[:] = []
        f.get("https://shop.example/b")
        # the polite pause before the next request uses the raised delay
        self.assertTrue(self.sleeps and self.sleeps[0] > 1.5, self.sleeps)

    def test_redirected_429_slows_final_host(self):
        f = self.fetcher([(200, None)], delay=0.5, rate_limit_retries=0)

        class Redirected(ScriptedOpener):
            def open(self, req, timeout=None):
                self.urls.append(req.full_url)
                if len(self.urls) == 1:
                    msg = email.message.Message()
                    msg["Retry-After"] = "5"
                    final = "https://www.shop.example/products.json"
                    raise HTTPError(final, 429, "x", msg, io.BytesIO(b""))
                return FakeResponse(req.full_url)

        f.opener = Redirected([])
        r = f.get("https://shop.example/products.json")
        self.assertEqual(r["status"], 429)
        self.assertEqual(f.host_delay("www.shop.example"), fetch.RATE_LIMIT_DELAY_MIN)
        self.assertEqual(f.host_delay("shop.example"), fetch.RATE_LIMIT_DELAY_MIN)
        self.assertIn("www.shop.example", f.rate_limited_hosts())
        self.sleeps[:] = []
        f.get("https://www.shop.example/sitemap.xml")
        self.assertTrue(self.sleeps and self.sleeps[0] > 1.5, self.sleeps)

    def test_delay_raise_is_bounded(self):
        f = self.fetcher([(429, {})], delay=10, rate_limit_retries=0)
        f.get("https://shop.example/a")
        self.assertEqual(f.host_delay("shop.example"), 10)  # own delay kept
        g = self.fetcher([(429, {})], delay=0.5, rate_limit_retries=0)
        g.get("https://shop.example/a")
        self.assertLessEqual(g.host_delay("shop.example"), fetch.RATE_LIMIT_DELAY_MAX)

    def test_missing_retry_after_uses_default_wait(self):
        f = self.fetcher([(429, {}), (200, None)], rate_limit_retries=1)
        r = f.get("https://shop.example/a")
        self.assertEqual(r["status"], 200)
        self.assertIn(fetch.RATE_LIMIT_DEFAULT_WAIT, self.sleeps)

    def test_retries_zero_keeps_old_behaviour(self):
        f = self.fetcher([(429, {"Retry-After": "1"})], retries=0)
        r = f.get("https://shop.example/a")
        self.assertEqual(r["status"], 429)
        self.assertEqual(len(f.opener.urls), 1)

    def test_5xx_unchanged_with_date_retry_after(self):
        f = self.fetcher([(503, {"Retry-After": "120"}), (200, None)])
        r = f.get("https://shop.example/a")
        self.assertEqual(r["status"], 200)
        self.assertIn(fetch.MAX_RETRY_AFTER_5XX, self.sleeps)  # 5xx cap stays 15 s
        self.assertIn("retried 1", r["note"])
        self.assertNotIn("rate_limited", r)
        self.assertEqual(f.rate_limited_hosts(), {})
        g = self.fetcher([(500, {})], retries=2)
        r = g.get("https://shop.example/a")
        self.assertEqual(r["status"], 500)
        self.assertEqual(len(g.opener.urls), 3)
        self.assertEqual([s for s in self.sleeps if s > 0], [1.5, 3.0])


if __name__ == "__main__":
    unittest.main()
