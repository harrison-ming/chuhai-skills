"""Local HTTP server for the synthetic stores in fixture_sites.py.

Serves one site at a time on 127.0.0.1, records request paths, replaces
"{B}" with its own base URL. Unknown paths answer 404 unless the site has a
"*" route.
Python 3.8+, standard library only.
"""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class Store(object):
    def __init__(self, site):
        self.site = site
        self.paths = []
        handler = self._handler()
        self.httpd = HTTPServer(("127.0.0.1", 0), handler)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()

    def _handler(self):
        store = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                store.paths.append(path)
                site = store.site
                key = path if path in site else path.rstrip("/") or "/"
                entry = site.get(key, site.get(path + "/", site.get("*")))
                if entry is None:
                    entry = (404, {}, "<html><body>Not found</body></html>")
                if isinstance(entry, str):
                    entry = (200, {}, entry)
                status, headers, body = entry
                body = body.replace("{B}", store.base).encode("utf-8")
                self.send_response(status)
                ctype = (
                    "application/json"
                    if path.endswith((".json", ".js"))
                    else "text/html"
                )
                self.send_header("Content-Type", ctype + "; charset=utf-8")
                for k, v in headers.items():
                    self.send_header(k, v.replace("{B}", store.base))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return H
