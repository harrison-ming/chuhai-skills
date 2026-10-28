"""Thin prlctl wrapper for Windows test VMs (host side, stdlib only).

Facts this relies on (Parallels README, testing protocol section 10):
- `prlctl exec` runs as SYSTEM in session 0; it occasionally fails with
  "PrlJob_GetRetCode: Invalid argument" -> retry idempotent calls only.
- PowerShell scripts go in over stdin (`powershell -Command -`), never inline
  on the command line (quoting layers break them, and secrets must never be
  on a command line).
- Mutating calls (start/stop/snapshot-switch/snapshot) are refused for VMs in
  PROTECTED: production and the source VMs other work depends on.
"""

import base64
import json
import re
import subprocess
import time

PROTECTED = frozenset({"StellarByte", "Win11-zhCN-demo", "Windows 11 (1)", "macOS"})
TRANSPORT_ERR = re.compile(
    r"PrlJob_Get(RetCode|Result)|Invalid argument|Unable to (open|connect)|"
    r"not running|Failed to get VM",
    re.I,
)


class TransportError(RuntimeError):
    """prlctl exec did not complete (no trustworthy answer from the guest)."""


class ProtectedVM(RuntimeError):
    """A mutating operation targeted a VM that must never be changed."""


def _text(b):
    if b is None:
        return ""
    return b.decode("utf-8", "replace") if isinstance(b, bytes) else b


def ps_quote(s):
    """PowerShell single-quoted literal."""
    return "'" + str(s).replace("'", "''") + "'"


class VM:
    def __init__(self, name, prlctl="prlctl", run=subprocess.run, sleep=time.sleep):
        self.name = name
        self.prlctl = prlctl
        self._run = run
        self._sleep = sleep

    # -------------------------------------------------------------- guards
    def _mutable(self):
        if self.name in PROTECTED:
            raise ProtectedVM("refusing to change protected VM %r" % self.name)

    # -------------------------------------------------------------- state
    def state(self):
        r = self._run(
            [self.prlctl, "list", "-a", "-j"], capture_output=True, timeout=60
        )
        for vm in json.loads(_text(r.stdout) or "[]"):
            if vm.get("name") == self.name:
                return vm.get("status")
        return None

    def snapshot_switch(self, snap_id, timeout=900):
        self._mutable()
        r = self._run(
            [self.prlctl, "snapshot-switch", self.name, "--id", snap_id],
            capture_output=True,
            timeout=timeout,
        )
        if r.returncode != 0:
            raise RuntimeError("snapshot-switch failed: %s" % _text(r.stderr)[-400:])

    def snapshot(self, name, description="", timeout=1800):
        self._mutable()
        r = self._run(
            [self.prlctl, "snapshot", self.name, "-n", name, "-d", description],
            capture_output=True,
            timeout=timeout,
        )
        out = _text(r.stdout) + _text(r.stderr)
        if r.returncode != 0:
            raise RuntimeError("snapshot failed: %s" % out[-400:])
        m = re.search(r"\{[0-9a-f-]{36}\}", out)
        return m.group(0) if m else None

    def power(self, action, timeout=600):
        """action: start | stop | restart (graceful)."""
        self._mutable()
        r = self._run(
            [self.prlctl, action, self.name], capture_output=True, timeout=timeout
        )
        if r.returncode != 0:
            raise RuntimeError("%s failed: %s" % (action, _text(r.stderr)[-300:]))

    # -------------------------------------------------------------- exec
    def exec_raw(self, argv, data=None, timeout=120, tries=3):
        last = None
        for k in range(tries):
            try:
                r = self._run(
                    [self.prlctl, "exec", self.name] + list(argv),
                    input=data,
                    capture_output=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                last = "timeout after %ss" % timeout
                r = None
            if r is not None:
                err = _text(r.stderr)
                if r.returncode == 0 or not TRANSPORT_ERR.search(err):
                    return r
                last = err.strip()[-300:]
            self._sleep(2 * (k + 1))
        raise TransportError("prlctl exec %s failed %dx: %s" % (argv[:1], tries, last))

    def ps(self, script, timeout=120, tries=3):
        """Run a PowerShell script (fed over stdin) as SYSTEM; returns CompletedProcess.

        Output is forced to UTF-8. Only pass tries>1 for idempotent scripts.
        """
        body = "[Console]::OutputEncoding=[Text.Encoding]::UTF8\r\n" + script
        data = body.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")
        return self.exec_raw(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", "-"],
            data=data,
            timeout=timeout,
            tries=tries,
        )

    def ps_json(self, script, timeout=120, tries=3):
        """Run a script whose last stdout line is JSON; returns the parsed object."""
        r = self.ps(script, timeout=timeout, tries=tries)
        lines = [ln for ln in _text(r.stdout).splitlines() if ln.strip()]
        for ln in reversed(lines):
            ln = ln.strip()
            if ln.startswith("{") or ln.startswith("["):
                return json.loads(ln)
        raise TransportError(
            "no JSON from guest (rc=%s): %s"
            % (r.returncode, (_text(r.stdout) + _text(r.stderr))[-400:])
        )

    def read_file(self, path, timeout=120):
        """Bytes of a guest file (base64 over stdout), None if it does not exist."""
        obj = self.ps_json(
            "$p=%s; if (Test-Path -LiteralPath $p) { "
            "@{ok=$true; b64=[Convert]::ToBase64String([IO.File]::ReadAllBytes($p))}"
            " | ConvertTo-Json -Compress } else { '{\"ok\":false}' }" % ps_quote(path),
            timeout=timeout,
        )
        if not obj.get("ok"):
            return None
        return base64.b64decode(obj["b64"])

    # -------------------------------------------------------------- readiness
    def wait_ready(self, user, timeout=600, poll=5, clock=time.time):
        """Guest answers exec AND `user` owns an explorer.exe in an interactive session.

        `query user` exit codes are useless (always 1), so use CIM.
        """
        deadline = clock() + timeout
        last = None
        good = 0
        probe = (
            "$cs=(Get-CimInstance Win32_ComputerSystem).UserName; "
            "$ex=@(Get-CimInstance Win32_Process -Filter \"Name='explorer.exe'\" | "
            "ForEach-Object { $o=Invoke-CimMethod -InputObject $_ -MethodName GetOwner; "
            "@{session=$_.SessionId; user=$o.User} }); "
            "@{user=$cs; explorer=$ex; host=$env:COMPUTERNAME; "
            "utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json -Compress -Depth 4"
        )
        while clock() < deadline:
            try:
                info = self.ps_json(probe, timeout=60, tries=1)
                ex = info.get("explorer") or []
                if isinstance(ex, dict):
                    ex = [ex]
                cs_user = (info.get("user") or "").split("\\")[-1]
                if cs_user.lower() == user.lower() and any(
                    (e.get("user") or "").lower() == user.lower()
                    and (e.get("session") or 0) >= 1
                    for e in ex
                ):
                    good += 1
                    if good >= 2:  # exec right after a revert is flaky; want it stable
                        return info
                    self._sleep(2)
                    continue
                last = info
            except (TransportError, ValueError, subprocess.TimeoutExpired) as e:
                last = str(e)
            good = 0
            self._sleep(poll)
        raise TransportError("guest not ready after %ss: %s" % (timeout, last))

    # -------------------------------------------------------------- session 1
    def run_as_user(
        self, task, user, ps1, args="", timeout=60, run_level="Highest", tries=3
    ):
        """Register + start a scheduled task running `ps1` as `user` in the
        interactive session (run_level Highest = elevated, Limited = the
        user's normal token). Returns immediately; the task writes
        its own result files (the task scheduler swallows errors)."""
        if run_level not in ("Highest", "Limited"):
            raise ValueError("run_level must be Highest or Limited")
        script = (
            "$ErrorActionPreference='Stop'\n"
            "$a=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument %s\n"
            "$p=New-ScheduledTaskPrincipal -UserId %s -LogonType Interactive -RunLevel %s\n"
            "$s=New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 6) "
            "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries\n"
            "Register-ScheduledTask -TaskName %s -Action $a -Principal $p -Settings $s -Force | Out-Null\n"
            "Start-ScheduledTask -TaskName %s\n"
            "'{\"started\":true}'\n"
            % (
                ps_quote(
                    '-NoProfile -ExecutionPolicy Bypass -File "%s" %s' % (ps1, args)
                ),
                ps_quote(user),
                run_level,
                ps_quote(task),
                ps_quote(task),
            )
        )
        # Safe to retry: Register -Force replaces the task, and a second Start
        # while it already runs is ignored (MultipleInstances=IgnoreNew).
        return self.ps_json(script, timeout=timeout, tries=tries)

    def task_state(self, task):
        return self.ps_json(
            "$t=Get-ScheduledTask -TaskName %s -ErrorAction SilentlyContinue; "
            "if ($t) { $i=Get-ScheduledTaskInfo -TaskName %s; "
            "@{state=[string]$t.State; last=$i.LastTaskResult} | ConvertTo-Json -Compress }"
            " else { '{\"state\":null}' }" % (ps_quote(task), ps_quote(task)),
            timeout=60,
        )
