"""Tests for shared/fetch.py: retries on cut-off transfers, DNS fail-fast.

A local HTTP server cuts the first responses short (IncompleteRead) or
resets the connection; DNS failures are simulated with mocks, so nothing
leaves the machine.
"""

import os
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock
from urllib.error import URLError

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


if __name__ == "__main__":
    unittest.main()
