"""Acceptance locks (testing protocol section 8.1). Stdlib only.

<lock dir>/<lock_id>.lock is created atomically with O_CREAT|O_EXCL and holds
JSON: lock_id, purpose, holder {host, pid}, run_id, skill, client,
acquired_at, heartbeat_at, expires_at (+ location for codex-acceptance-auth).
A stale lock is only cleared when it has expired AND its holder pid is gone.
The lock dir is outside STELLAR_OUTPUT on purpose (an unplugged external disk
must not silently stop a run).
"""

import datetime
import errno
import json
import os
import socket
import threading
import time

LOCK_DIR = os.path.join(
    os.path.expanduser("~"),
    "Library",
    "Application Support",
    "agent-skill-acceptance",
    "locks",
)
CODEX_AUTH_LOCK = "codex-acceptance-auth"


class LockBusy(RuntimeError):
    """Someone else holds the lock."""


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.timezone.utc
    )


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (TypeError, ValueError, OSError):
        return False
    return True


class Lock:
    def __init__(
        self,
        lock_id,
        purpose="acceptance",
        ttl=3600,
        lock_dir=None,
        heartbeat=60,
        **fields,
    ):
        self.lock_id = lock_id
        self.purpose = purpose
        self.ttl = ttl
        self.dir = lock_dir or LOCK_DIR
        self.path = os.path.join(self.dir, "%s.lock" % lock_id)
        self.fields = fields
        self.heartbeat = heartbeat
        self._stop = threading.Event()
        self._thread = None
        self.held = False

    def _payload(self, acquired=None):
        now = utcnow()
        data = {
            "lock_id": self.lock_id,
            "purpose": self.purpose,
            "holder": {"host": socket.gethostname(), "pid": os.getpid()},
            "acquired_at": iso(acquired or now),
            "heartbeat_at": iso(now),
            "expires_at": iso(now + datetime.timedelta(seconds=self.ttl)),
        }
        data.update(self.fields)
        return data

    def read(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def is_stale(self, data, now=None):
        """Expired and holder gone. Unreadable lock files are not stale (be safe)."""
        if not data:
            return False
        now = now or utcnow()
        try:
            expired = parse_iso(data["expires_at"]) < now
        except (KeyError, ValueError):
            return False
        return expired and not pid_alive((data.get("holder") or {}).get("pid"))

    def acquire(self, wait=0, poll=5):
        os.makedirs(self.dir, exist_ok=True)
        deadline = time.time() + wait
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except OSError as e:
                if e.errno != errno.EEXIST:
                    raise
                cur = self.read()
                if self.is_stale(cur):
                    os.remove(self.path)
                    continue
                if time.time() >= deadline:
                    raise LockBusy("%s held: %s" % (self.lock_id, json.dumps(cur)))
                time.sleep(poll)
                continue
            self._acquired = utcnow()
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._payload(self._acquired), fh, indent=1)
            self.held = True
            if self.heartbeat:
                self._thread = threading.Thread(target=self._beat, daemon=True)
                self._thread.start()
            return self

    def update(self, **fields):
        self.fields.update(fields)
        self._write()

    def _write(self):
        if not self.held:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._payload(self._acquired), fh, indent=1)
        os.replace(tmp, self.path)

    def _beat(self):
        while not self._stop.wait(self.heartbeat):
            try:
                self._write()
            except OSError:
                pass

    def release(self):
        self._stop.set()
        if self.held:
            cur = self.read()
            if cur and (cur.get("holder") or {}).get("pid") == os.getpid():
                os.remove(self.path)
            self.held = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False
