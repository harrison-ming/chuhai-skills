"""Acceptance locks (testing protocol section 8.1). Stdlib only.

<lock dir>/<lock_id>.lock is created atomically with O_CREAT|O_EXCL and holds
JSON: lock_id, purpose, holder {host, pid, pid_started_at, host_boot_at},
run_id, skill, client, acquired_at, heartbeat_at, expires_at (+ location for
codex-acceptance-auth). A stale lock is only cleared when it has expired AND
its holder is gone. "Gone" does not trust the pid alone: after a host reboot
the pid can belong to an unrelated process, so the host boot time and the
process start time recorded in the lock must also still match.
The lock dir is outside STELLAR_OUTPUT on purpose (an unplugged external disk
must not silently stop a run).
"""

import datetime
import errno
import json
import os
import re
import socket
import subprocess
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


def host_boot_at():
    """Host boot time as ISO UTC, or None when it cannot be read."""
    try:
        out = subprocess.run(
            ["sysctl", "-n", "kern.boottime"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"sec\s*=\s*(\d+)", out or "")
    if not m:
        return None
    return iso(datetime.datetime.fromtimestamp(int(m.group(1)), datetime.timezone.utc))


def pid_started_at(pid):
    """Start time of a running process as ISO UTC, or None."""
    try:
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(int(pid))],
            capture_output=True,
            text=True,
            timeout=10,
            env=dict(os.environ, LC_ALL="C", TZ="UTC"),
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return None
    if not out:
        return None
    try:
        dt = datetime.datetime.strptime(" ".join(out.split()), "%a %b %d %H:%M:%S %Y")
    except ValueError:
        return None
    return iso(dt.replace(tzinfo=datetime.timezone.utc))


def holder_gone(holder, boot_at=None, started_at=None):
    """True when the process that wrote the lock no longer exists.

    boot_at / started_at are injectable for tests; by default they are read
    from the live system. Old locks without the new fields fall back to the
    pid check only.
    """
    holder = holder or {}
    pid = holder.get("pid")
    if not pid_alive(pid):
        return True
    rec_boot = holder.get("host_boot_at")
    cur_boot = boot_at if boot_at is not None else host_boot_at()
    if rec_boot and cur_boot and rec_boot != cur_boot:
        return True
    rec_start = holder.get("pid_started_at")
    if rec_start:
        cur_start = started_at if started_at is not None else pid_started_at(pid)
        if cur_start and cur_start != rec_start:
            return True
    return False


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
            "holder": self._holder(),
            "acquired_at": iso(acquired or now),
            "heartbeat_at": iso(now),
            "expires_at": iso(now + datetime.timedelta(seconds=self.ttl)),
        }
        data.update(self.fields)
        return data

    def _holder(self):
        if not hasattr(self, "_holder_cache"):
            self._holder_cache = {
                "host": socket.gethostname(),
                "pid": os.getpid(),
                "pid_started_at": pid_started_at(os.getpid()),
                "host_boot_at": host_boot_at(),
            }
        return dict(self._holder_cache)

    def read(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def is_stale(self, data, now=None):
        """Expired and holder gone (see holder_gone). Unreadable lock files are not stale (be safe)."""
        if not data:
            return False
        now = now or utcnow()
        try:
            expired = parse_iso(data["expires_at"]) < now
        except (KeyError, ValueError):
            return False
        return expired and holder_gone(data.get("holder"))

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
