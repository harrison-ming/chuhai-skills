"""Polite HTTP fetching for public pages (standard library only).

- Browser-like User-Agent, gzip/deflate, a delay between requests, retries
  with backoff on network errors and 5xx (Retry-After honoured, max 15 s).
- HTTP 429 (rate limited) is respected, never worked around: the fetcher
  waits as long as Retry-After asks (delta-seconds or HTTP-date), up to
  ``max_retry_after`` per wait and ``rate_limit_budget`` in total per run,
  retries at most ``rate_limit_retries`` times, and raises that host's delay
  between requests for the rest of the run. A request that asks for a longer
  wait than allowed, or arrives after the budget is spent, returns the 429 at
  once. No User-Agent rotation, proxies or parallel requests, by design.
- A cut-off transfer (IncompleteRead, connection reset, remote disconnect;
  common behind proxies) is retried with bounded backoff, the retry asking
  for an uncompressed body.
- A host whose name does not resolve (DNS) fails at once, without retries,
  and every later request to that host is skipped, so a mistyped or
  non-existent domain finishes in seconds.
- Every response is returned as a dict with status, body, final URL, timing
  and a short note, so callers can record each request instead of aborting.
- TLS verification always stays on. Python 3.13+'s VERIFY_X509_STRICT is
  cleared (browsers accept those chains), and on Windows a certificate
  failure triggers one PowerShell request per host so Windows downloads the
  missing root, then the request is retried once.

Python 3.8+, standard library only.
"""

import datetime
import email.utils
import gzip
import http.client
import re
import socket
import ssl
import subprocess
import sys
import time
import zlib
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from . import i18n

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
ACCEPT_HTML = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
ACCEPT_JSON = "application/json,text/javascript,*/*;q=0.5"
ACCEPT_LANGUAGE = "en-US,en;q=0.9"
MAX_BYTES = 40 * 1024 * 1024
MAX_REDIRECTS = 8
WIN_ROOT_REFRESH_NOTE = "windows root certificate refresh triggered, retried once"
WIN_ROOT_REFRESH_TIMEOUT = 20
# Retries for a cut-off transfer (at least this many, whatever ``retries``)
# and the cap of one backoff sleep, in seconds.
TRANSIENT_RETRIES = 3
MAX_BACKOFF = 8.0
DNS_SKIP_NOTE = "skipped: host name did not resolve earlier"
# Cap of one Retry-After wait for 5xx answers (unchanged behaviour).
MAX_RETRY_AFTER_5XX = 15.0
# 429 defaults: cap of one wait, total wait budget of one Fetcher, the wait
# used when a 429 carries no Retry-After, and the per-host delay after a 429
# (max(current, clamp(delay * 4, RATE_LIMIT_DELAY_MIN, RATE_LIMIT_DELAY_MAX))).
MAX_RETRY_AFTER = 75.0
RATE_LIMIT_BUDGET = 240.0
RATE_LIMIT_DEFAULT_WAIT = 10.0
RATE_LIMIT_DELAY_MIN = 2.0
RATE_LIMIT_DELAY_MAX = 3.0
RATE_LIMIT_NOTE = "rate limited (429)"
SECOND_LEVEL = {
    "co.uk",
    "org.uk",
    "ac.uk",
    "com.au",
    "net.au",
    "org.au",
    "com.cn",
    "net.cn",
    "org.cn",
    "com.hk",
    "com.tw",
    "co.jp",
    "co.nz",
    "co.kr",
    "com.sg",
    "com.my",
    "com.br",
    "com.mx",
    "com.ar",
    "co.za",
    "co.in",
    "com.tr",
}

_CERT_NOTES = {
    "zh": {
        "darwin": (
            "证书校验失败. 如果 Python 是从 "
            "python.org 下载安装的, 请到 应用程序 "
            "→ Python 文件夹里双击运行一次 "
            "Install Certificates.command, 然后重试"
        ),
        "win32": (
            "证书校验失败. 请先用浏览器 "
            "(Edge 或 Chrome) 打开一次该网站, "
            "或等几分钟后重试 (Windows 会自动"
            "补齐证书)"
        ),
        "other": (
            "证书校验失败. 请更新系统证书 "
            "(例如 sudo apt install ca-certificates, "
            "或 sudo yum update ca-certificates) 后重试"
        ),
    },
    "en": {
        "darwin": (
            "Certificate verification failed. If Python came from python.org, open "
            "Applications > Python and double-click Install Certificates.command "
            "once, then retry"
        ),
        "win32": (
            "Certificate verification failed. Open the site once in Edge or Chrome, "
            "or retry in a few minutes (Windows completes its certificates "
            "automatically)"
        ),
        "other": (
            "Certificate verification failed. Update the system certificates (for "
            "example sudo apt install ca-certificates, or sudo yum update "
            "ca-certificates) and retry"
        ),
    },
}


def cert_note(platform=None, lang=None):
    """Plain-language hint for a certificate verification failure."""
    platform = platform or sys.platform
    notes = _CERT_NOTES[i18n.norm_lang(lang)]
    if platform == "darwin":
        return notes["darwin"]
    if platform == "win32":
        return notes["win32"]
    return notes["other"]


# Fixed at import time (the platform does not change while running).
CERT_NOTES = dict((code, cert_note(lang=code)) for code in i18n.LANGS)
CERT_NOTE = CERT_NOTES[i18n.DEFAULT_LANG]


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_input(raw):
    """Return (scheme, host) from a domain or any URL."""
    raw = raw.strip()
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", raw):
        raw = "https://" + raw
    parts = urlsplit(raw)
    host = (parts.netloc or "").split("@")[-1].lower().rstrip(".")
    if not host:
        raise ValueError("cannot parse host from %r" % raw)
    scheme = (
        parts.scheme.lower() if parts.scheme.lower() in ("http", "https") else "https"
    )
    return scheme, host


def strip_www(host):
    host = host.split(":")[0]
    return host[4:] if host.startswith("www.") else host


def registrable_domain(host):
    """Very small public-suffix approximation; None for IPs / localhost."""
    host = strip_www(host)
    if re.match(r"^[\d.]+$", host) or "." not in host or host.endswith(".local"):
        return None
    labels = host.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


class LimitedRedirect(HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS


def make_ssl_context():
    """Default verifying context, minus VERIFY_X509_STRICT.

    Python 3.13+ turns on VERIFY_X509_STRICT by default, which rejects some
    public CA chains that browsers accept (e.g. "Basic Constraints of CA cert
    not marked critical"). Clearing only that flag is a compatibility fix, not
    a downgrade: chain verification (CERT_REQUIRED) and hostname checking stay
    on. On Windows the context reads the system store via load_default_certs,
    so a fresh context also picks up roots Windows downloaded meanwhile.
    """
    ctx = ssl.create_default_context()
    strict = getattr(ssl, "VERIFY_X509_STRICT", None)
    if strict is not None:
        ctx.verify_flags &= ~strict
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def site_root(url):
    """``scheme://host[:port]/`` of url, or None when it is unsafe to quote."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return None
    if not re.match(r"^[A-Za-z0-9.\-]+(:\d+)?$", parts.netloc or ""):
        return None
    return "%s://%s/" % (parts.scheme, parts.netloc)


def refresh_windows_roots(url, run=None):
    """Ask Windows to fetch missing root certificates for url's site.

    A fresh Windows install downloads trusted roots on demand (AuthRoot
    auto-update) only when a Windows TLS client meets them; OpenSSL never
    triggers that. One PowerShell HEAD request does. Returns True when
    PowerShell ran (whatever the HTTP result), False when it is unavailable.
    """
    root = site_root(url)
    if root is None:
        return False
    run = run or subprocess.run
    script = (
        "try { Invoke-WebRequest -UseBasicParsing -Method Head -Uri '%s' "
        "-TimeoutSec 15 | Out-Null } catch {}" % root
    )
    try:
        run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=WIN_ROOT_REFRESH_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return True  # it ran; the roots may still have been fetched
    except (OSError, ValueError):
        return False
    return True


def decode_body(body, headers):
    enc = ""
    if hasattr(headers, "get"):
        enc = (headers.get("Content-Encoding") or "").lower()
    try:
        if "gzip" in enc or body[:2] == b"\x1f\x8b":
            return gzip.decompress(body)
        if "deflate" in enc:
            try:
                return zlib.decompress(body)
            except zlib.error:
                return zlib.decompress(body, -zlib.MAX_WBITS)
    except (OSError, zlib.error, EOFError):
        return body
    return body


def is_cert_error(err):
    reason = getattr(err, "reason", err)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return True
    return "CERTIFICATE_VERIFY_FAILED" in str(err)


def is_dns_error(err):
    """True when the host name could not be resolved (not a temporary DNS
    hiccup: EAI_AGAIN keeps the normal retries)."""
    reason = getattr(err, "reason", err)
    if not isinstance(reason, socket.gaierror):
        return False
    return reason.errno != getattr(socket, "EAI_AGAIN", object())


def is_transient_error(err):
    """A transfer cut off midway: worth retrying, ideally uncompressed."""
    reason = getattr(err, "reason", err)
    return isinstance(
        reason,
        (
            http.client.IncompleteRead,
            http.client.RemoteDisconnected,
            ConnectionResetError,
            ConnectionAbortedError,
            BrokenPipeError,
        ),
    )


def check_length(body, headers):
    """Raise IncompleteRead when fewer bytes arrived than Content-Length says.

    ``HTTPResponse.read(amt)`` returns a short body silently when the
    connection drops, so a cut-off page would otherwise pass as complete.
    """
    raw = headers.get("Content-Length") if hasattr(headers, "get") else None
    try:
        expected = int(raw)
    except (TypeError, ValueError):
        return
    if len(body) < expected <= MAX_BYTES:
        raise http.client.IncompleteRead(body, expected - len(body))


def backoff(attempt):
    """Sleep before retry number ``attempt`` (1-based): 1.5, 3, 6, 8, 8 ..."""
    return min(1.5 * (2 ** (attempt - 1)), MAX_BACKOFF)


def parse_retry_after(value, now=None):
    """Seconds asked by a Retry-After header, or None when absent/invalid.

    Accepts delta-seconds ("60") and an HTTP-date ("Wed, 21 Oct 2026
    07:28:00 GMT"); a date in the past gives 0. ``now`` (aware datetime) is
    for tests.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if re.match(r"^\d+(?:\.\d+)?$", text):
        return max(0.0, float(text))
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return max(0.0, (when - now).total_seconds())


def fmt_seconds(value):
    """Seconds as a short note string: 60 -> "60s", 1.5 -> "1.5s"."""
    return ("%.1f" % value).rstrip("0").rstrip(".") + "s"


def describe_error(err, note_for_cert=None):
    if is_cert_error(err):
        return note_for_cert or CERT_NOTE
    reason = getattr(err, "reason", err)
    if isinstance(reason, socket.timeout) or "timed out" in str(reason):
        return "timeout"
    return "%s: %s" % (type(reason).__name__, reason)


def cert_failures(requests, note_for_cert=None):
    """Requests (status dicts) that finally failed on certificate verification.

    A request that succeeded after the Windows root refresh is not counted;
    its note only carries WIN_ROOT_REFRESH_NOTE for traceability.
    """
    note_for_cert = note_for_cert or CERT_NOTE
    return [
        r
        for r in requests
        if r.get("http_status") is None and note_for_cert in (r.get("note") or "")
    ]


class Fetcher(object):
    """Sequential polite fetcher. ``get`` never raises for network problems.

    ``retries`` bounds retries after network errors and 5xx answers.
    ``rate_limit_retries`` bounds retries after HTTP 429 (default: same as
    ``retries``, so ``retries=0`` callers keep never retrying); each wait
    follows Retry-After, is at most ``max_retry_after`` seconds, and all 429
    waits of this Fetcher together stay within ``rate_limit_budget`` seconds.
    ``sleep`` replaces ``time.sleep`` (tests).

    Subclasses may override ``_build_opener`` (it must set ``self.ctx`` and
    ``self.opener``), e.g. to add handlers.
    """

    def __init__(
        self,
        timeout=20.0,
        delay=0.5,
        retries=2,
        user_agent=USER_AGENT,
        accept_language=ACCEPT_LANGUAGE,
        lang=None,
        rate_limit_retries=None,
        max_retry_after=MAX_RETRY_AFTER,
        rate_limit_budget=RATE_LIMIT_BUDGET,
        sleep=None,
    ):
        self.timeout = timeout
        self.delay = delay
        self.retries = retries
        self.user_agent = user_agent
        self.accept_language = accept_language
        self.cert_note = CERT_NOTES[i18n.norm_lang(lang)]
        self.rate_limit_retries = max(
            0, int(retries if rate_limit_retries is None else rate_limit_retries)
        )
        self.max_retry_after = max(0.0, float(max_retry_after))
        self.rate_limit_budget = max(0.0, float(rate_limit_budget))
        self.rate_limit_waited = 0.0  # seconds spent waiting on 429s so far
        self._sleep_fn = sleep
        self._host_delay = {}  # host -> raised delay after a 429
        self._rate_limited = {}  # host -> number of 429 answers
        self._refreshed_hosts = set()
        self._dns_failed = {}  # host -> note of the resolution failure
        self._build_opener()
        self._last = 0.0

    def _build_opener(self):
        self.ctx = make_ssl_context()  # verification stays on
        self.opener = build_opener(HTTPSHandler(context=self.ctx), LimitedRedirect())

    def _try_cert_refresh(self, url):
        """Windows only, at most once per host: refresh roots, rebuild context."""
        if sys.platform != "win32":
            return False
        host = (urlsplit(url).hostname or "").lower()
        if not host or host in self._refreshed_hosts:
            return False
        self._refreshed_hosts.add(host)
        if not refresh_windows_roots(url):
            return False
        self._build_opener()
        return True

    def _sleep(self, seconds):
        if seconds <= 0:
            return
        if self._sleep_fn is not None:
            self._sleep_fn(seconds)
        else:
            time.sleep(seconds)

    def host_delay(self, host):
        """Seconds between requests to host (raised after a 429)."""
        return max(self.delay, self._host_delay.get(host, 0.0))

    def _sleep_polite(self, host=None):
        wait = self.host_delay(host) - (time.time() - self._last)
        if wait > 0:
            self._sleep(wait)

    def _slow_down(self, host):
        """After a 429: keep a larger delay for this host for the whole run."""
        raised = min(max(self.delay * 4, RATE_LIMIT_DELAY_MIN), RATE_LIMIT_DELAY_MAX)
        self._host_delay[host] = max(self._host_delay.get(host, 0.0), raised)

    def rate_limited_hosts(self):
        """{host: number of 429 answers} seen so far (a copy)."""
        return dict(self._rate_limited)

    def rate_limit_budget_left(self):
        return max(0.0, self.rate_limit_budget - self.rate_limit_waited)

    def get(self, url, accept=ACCEPT_HTML, headers=None):
        """Return dict(status, body(bytes|None), final_url, elapsed_ms, note).

        After any 429 the dict also carries ``rate_limited: True``.
        """
        host = (urlsplit(url).hostname or "").lower()
        if host in self._dns_failed:
            return {
                "status": None,
                "body": None,
                "final_url": url,
                "elapsed_ms": 0,
                "note": "%s; %s" % (self._dns_failed[host], DNS_SKIP_NOTE),
            }
        attempt = 0  # retries after network errors / 5xx
        rl_attempt = 0  # retries after 429
        rl_waited = 0.0
        rl_seen = False
        rl_stop = ""  # why a 429 was not retried
        refreshed = False
        identity = False  # ask for an uncompressed body after a cut-off
        while True:
            # Per-attempt note: a cert error that a retry fixed must not leak
            # into the final result (only the refresh marker is kept).
            note = ""
            self._sleep_polite(host)
            t0 = time.time()
            status, body, final_url, resp_headers, err = None, None, url, {}, None
            req_headers = {
                "User-Agent": self.user_agent,
                "Accept": accept,
                "Accept-Language": self.accept_language,
                "Accept-Encoding": "identity" if identity else "gzip, deflate",
            }
            if headers:
                req_headers.update(headers)
            req = Request(url, headers=req_headers)
            try:
                resp = self.opener.open(req, timeout=self.timeout)
                status = resp.getcode()
                final_url = resp.geturl()
                resp_headers = resp.headers
                body = resp.read(MAX_BYTES + 1)
                resp.close()
                check_length(body, resp_headers)
            except HTTPError as e:
                status = e.code
                final_url = e.geturl() or url
                resp_headers = e.headers or {}
                try:
                    body = e.read(MAX_BYTES + 1)
                except Exception:  # noqa: BLE001
                    body = b""
                finally:
                    e.close()
            except (
                URLError,
                http.client.HTTPException,
                socket.timeout,
                ssl.SSLError,
                ConnectionError,
                OSError,
            ) as e:
                err = e
            elapsed = int((time.time() - t0) * 1000)
            self._last = time.time()
            if body is not None:
                body = decode_body(body, resp_headers)
                if len(body) > MAX_BYTES:
                    body = body[:MAX_BYTES]
                    note = "body truncated at %d bytes" % MAX_BYTES
            if err is not None:
                note = describe_error(err, self.cert_note)
                if refreshed:
                    note += "; " + WIN_ROOT_REFRESH_NOTE
                if is_cert_error(err) and not refreshed and self._try_cert_refresh(url):
                    refreshed = True
                    continue
                dns = is_dns_error(err)
                transient = is_transient_error(err)
                limit = (
                    max(self.retries, TRANSIENT_RETRIES) if transient else self.retries
                )
                if is_cert_error(err) or dns or attempt >= limit:
                    if dns and host:
                        self._dns_failed[host] = note
                    if rl_seen:
                        note += "; " + self._rl_note(rl_waited, rl_attempt, "")
                    if attempt:
                        note += "; retried %d" % attempt
                    return {
                        "status": None,
                        "body": None,
                        "final_url": url,
                        "elapsed_ms": elapsed,
                        "note": note,
                    }
                attempt += 1
                identity = identity or transient
                self._sleep(backoff(attempt))
                continue
            ra = (
                parse_retry_after(resp_headers.get("Retry-After"))
                if hasattr(resp_headers, "get")
                else None
            )
            if status == 429:
                rl_seen = True
                # The answer may come from another host after a redirect
                # (e.g. bare domain -> www): slow down both.
                final_host = (urlsplit(final_url or url).hostname or "").lower()
                for h in {host, final_host} - {""}:
                    self._rate_limited[h] = self._rate_limited.get(h, 0) + 1
                    self._slow_down(h)
                wait = (
                    ra if ra is not None else RATE_LIMIT_DEFAULT_WAIT * (rl_attempt + 1)
                )
                if rl_attempt >= self.rate_limit_retries:
                    rl_stop = "still limited"
                elif wait > self.max_retry_after:
                    # Retrying before the time the site asked for is not
                    # polite; give up instead of waiting a shorter time.
                    rl_stop = "asked to wait %s, more than the %s limit" % (
                        fmt_seconds(wait),
                        fmt_seconds(self.max_retry_after),
                    )
                elif wait > self.rate_limit_budget_left():
                    rl_stop = "wait budget exhausted"
                else:
                    rl_attempt += 1
                    rl_waited += wait
                    self.rate_limit_waited += wait
                    self._sleep(wait)
                    continue
            elif status is not None and status >= 500 and attempt < self.retries:
                attempt += 1
                wait = backoff(attempt)
                if ra is not None:
                    wait = min(ra, MAX_RETRY_AFTER_5XX)
                self._sleep(wait)
                continue
            if refreshed:
                note = (note + "; " if note else "") + WIN_ROOT_REFRESH_NOTE
            if rl_seen:
                rl_note = self._rl_note(rl_waited, rl_attempt, rl_stop)
                note = (note + "; " if note else "") + rl_note
            if attempt:
                note = (note + "; " if note else "") + "retried %d" % attempt
            out = {
                "status": status,
                "body": body,
                "final_url": final_url,
                "elapsed_ms": elapsed,
                "note": note,
            }
            if rl_seen:
                out["rate_limited"] = True
            return out

    @staticmethod
    def _rl_note(waited, retried, stop):
        """'rate limited (429), waited 60s, retried 1[, <why stopped>]'."""
        parts = [RATE_LIMIT_NOTE]
        if waited:
            parts.append("waited %s" % fmt_seconds(waited))
        if retried:
            parts.append("retried %d" % retried)
        if stop:
            parts.append(stop)
        return ", ".join(parts)


def status_entry(key, url, response, result, fname=None, extra_note=""):
    """One request record for ``_status.json`` (same shape as teardown)."""
    note = "; ".join(n for n in (response.get("note"), extra_note) if n)
    if response.get("status") is None and not note:
        note = "network error"
    return {
        "key": key,
        "file": fname,
        "url": url,
        "final_url": response.get("final_url"),
        "http_status": response.get("status"),
        "result": result,
        "bytes": len(response.get("body") or b""),
        "elapsed_ms": response.get("elapsed_ms"),
        "note": note,
    }
