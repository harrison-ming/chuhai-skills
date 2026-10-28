"""Per-round file transfer between host and guest over the Parallels shared net.

One threaded HTTP server, bound to the host side of the shared network
(10.211.55.2), on a random port, for the duration of one round only:
  GET  /get/<name>          files from the round's serve dir (delivery)
  PUT  /put/<token>/<path>  files into the round's collect dir (evidence)
The PUT token is a per-round random string, so a stray client on the shared
net cannot write into the evidence dir by guessing the URL. Paths are
normalised and confined to the collect dir. Stdlib only.
"""

import os
import posixpath
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

HOST_SHARED_IP = "10.211.55.2"
MAX_PUT = 512 * 1024 * 1024


def safe_join(root, rel):
    """Join a guest-supplied relative path under root, or None if it escapes."""
    rel = urllib.parse.unquote(rel).replace("\\", "/")
    if ".." in rel.split("/"):
        return None  # reject, do not silently clamp
    norm = posixpath.normpath("/" + rel).lstrip("/")
    if not norm or norm == "." or norm.startswith(".."):
        return None
    parts = norm.split("/")
    if any(p in ("", ".", "..") or ":" in p for p in parts):
        return None
    path = os.path.join(root, *parts)
    root_real = os.path.realpath(root)
    if not os.path.realpath(path).startswith(root_real + os.sep):
        return None
    return path


class _Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _handler(serve_dir, collect_dir, token, received):
    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # keep stderr quiet
            pass

        def _send(self, code, body=b""):
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if not path.startswith("/get/"):
                return self._send(404)
            target = safe_join(serve_dir, path[len("/get/") :])
            if not target or not os.path.isfile(target):
                return self._send(404)
            size = os.path.getsize(target)
            self.send_response(200)
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            with open(target, "rb") as fh:
                while True:
                    chunk = fh.read(1 << 20)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

        def do_PUT(self):
            path = urllib.parse.urlparse(self.path).path
            prefix = "/put/%s/" % token
            if not path.startswith(prefix):
                return self._send(403)
            target = safe_join(collect_dir, path[len(prefix) :])
            try:
                n = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                n = -1
            if not target or n < 0 or n > MAX_PUT:
                return self._send(400)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            tmp = target + ".part"
            with open(tmp, "wb") as fh:
                left = n
                while left:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    fh.write(chunk)
                    left -= len(chunk)
            if left:
                os.remove(tmp)
                return self._send(400)
            os.replace(tmp, target)
            received.append(os.path.relpath(target, collect_dir))
            self._send(201, b"ok")

    return H


class RoundServer:
    """Context manager: start on enter, always stop on exit."""

    def __init__(self, serve_dir, collect_dir, bind=HOST_SHARED_IP, port=0):
        self.serve_dir = serve_dir
        self.collect_dir = collect_dir
        self.token = secrets.token_hex(16)
        self.received = []
        self._bind, self._port = bind, port
        self.httpd = None
        self.thread = None

    @property
    def base(self):
        host, port = self.httpd.server_address[:2]
        return "http://%s:%d" % (host, port)

    @property
    def get_base(self):
        return self.base + "/get"

    @property
    def put_base(self):
        return "%s/put/%s" % (self.base, self.token)

    def start(self):
        os.makedirs(self.serve_dir, exist_ok=True)
        os.makedirs(self.collect_dir, exist_ok=True)
        handler = _handler(self.serve_dir, self.collect_dir, self.token, self.received)
        self.httpd = _Server((self._bind, self._port), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False
