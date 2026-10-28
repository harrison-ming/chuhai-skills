"""Tests for the L2 VM runner host side (tools/acceptance/vm/) and the clean L1
runners in tools/acceptance/run.py. No VM, no network, no real credentials:
prlctl and Bitwarden are replaced by fakes. Python 3.8+, stdlib only."""

import base64
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ACC = os.path.join(ROOT, "tools", "acceptance")
sys.path.insert(0, os.path.join(ACC, "vm"))
sys.path.insert(0, ACC)

import creds  # noqa: E402
import locks  # noqa: E402
import prl  # noqa: E402
import results  # noqa: E402
import run as host_run  # noqa: E402
import run_vm  # noqa: E402
import steps  # noqa: E402
import transfer  # noqa: E402

SKILLS = [
    "shopify-store-teardown",
    "payments-readiness",
    "tracking-audit",
    "launch-readiness",
    "profit-model",
    "competitor-change-radar",
]
FAKE_TOKEN = "sk-ant-oat01-" + "x" * 40


def load_acc(name):
    with open(
        os.path.join(ROOT, "skills", name, "acceptance.json"), encoding="utf-8"
    ) as fh:
        return json.load(fh)


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vmacc-", dir=HERE)
        self.addCleanup(shutil.rmtree, self.tmp, True)


# ---------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------


class StepsTest(unittest.TestCase):
    def test_derive_simple_and_quoted_code(self):
        out = steps.derive(
            [
                '{python} -c "import a; b(1) | c" {skill_dir}',
                "python3 {skill_dir}/scripts/x.py {domain} --out-root {out_root}",
            ]
        )
        self.assertEqual(
            out[0]["argv"], ["{python}", "-c", "import a; b(1) | c", "{skill_dir}"]
        )
        self.assertEqual(out[1]["argv"][0], "{python}")

    def test_derive_rejects_shell_syntax(self):
        for cmd in (
            '[ -z "{domain}" ] || {python} x.py',
            "{python} a.py && {python} b.py",
            'if [ -n "{domain}" ]; then x; fi',
            '{python} q.py "$(ls -d {out_root}/*/ | head -1)"',
        ):
            with self.assertRaises(steps.StepsError, msg=cmd):
                steps.derive([cmd])

    def test_every_skill_has_portable_steps(self):
        for name in SKILLS:
            acc = load_acc(name)
            got = steps.steps_for(acc)
            self.assertTrue(got, name)
            for s in got:
                self.assertTrue(s["argv"], name)
                for a in s["argv"]:
                    self.assertNotIn("$(", a, name)

    def test_when_conditions(self):
        acc = {
            "script_steps": [
                {"argv": ["{python}", "a", "{domain}"], "when": "domain"},
                {"argv": ["{python}", "b"], "when": "no_domain"},
                {"argv": ["{python}", "c"], "when": "lang:en"},
                {"argv": ["{python}", "d"], "when": "case:x"},
                {"argv": ["{python}", "e"]},
            ]
        }
        got = steps.plan_for_case(acc, {"id": "x", "lang": "en"}, "shop.com")
        self.assertEqual([s["argv"][1] for s in got], ["a", "c", "d", "e"])
        self.assertEqual(got[0]["argv"][2], "shop.com")
        got = steps.plan_for_case(acc, {"id": "y"}, "")
        self.assertEqual([s["argv"][1] for s in got], ["b", "e"])
        with self.assertRaises(steps.StepsError):
            steps.applies({"when": "weird"}, {}, "")

    def test_payments_offline_uses_first_out_subdir(self):
        acc = load_acc("payments-readiness")
        case = [c for c in acc["cases"] if c["id"] == "offline_quote"][0]
        got = steps.plan_for_case(acc, case, host_run.case_domain(case))
        self.assertEqual(len(got), 2)
        self.assertIn("{first_out_subdir}", got[1]["argv"])


# ---------------------------------------------------------------------------
# transfer
# ---------------------------------------------------------------------------


class TransferTest(Tmp):
    def test_safe_join(self):
        root = os.path.join(self.tmp, "r")
        os.makedirs(root)
        self.assertEqual(
            transfer.safe_join(root, "a/%E4%B8%AD%20b.txt"),
            os.path.join(root, "a", "中 b.txt"),
        )
        for bad in ("../x", "a/../../x", "", ".", "c:/x", "a/c:x"):
            self.assertIsNone(transfer.safe_join(root, bad), bad)
        self.assertEqual(
            transfer.safe_join(root, "/abs/x"), os.path.join(root, "abs", "x")
        )

    def test_round_server_get_put(self):
        serve = os.path.join(self.tmp, "serve")
        coll = os.path.join(self.tmp, "coll")
        os.makedirs(serve)
        with open(os.path.join(serve, "f.bin"), "wb") as fh:
            fh.write(b"abc")
        with transfer.RoundServer(serve, coll, bind="127.0.0.1") as srv:
            with urllib.request.urlopen(srv.get_base + "/f.bin") as r:
                self.assertEqual(r.read(), b"abc")
            req = urllib.request.Request(
                srv.put_base + "/d/%E6%8A%A5%E5%91%8A.md", data=b"hi", method="PUT"
            )
            with urllib.request.urlopen(req) as r:
                self.assertEqual(r.status, 201)
            bad = urllib.request.Request(
                srv.base + "/put/wrong/x", data=b"no", method="PUT"
            )
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(bad)
            self.assertEqual(cm.exception.code, 403)
            esc = urllib.request.Request(
                srv.put_base + "/..%2F..%2Fx", data=b"no", method="PUT"
            )
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(esc)
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(srv.get_base + "/../coll/d")
        with open(os.path.join(coll, "d", "报告.md"), "rb") as fh:
            self.assertEqual(fh.read(), b"hi")
        self.assertEqual(srv.received, [os.path.join("d", "报告.md")])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "x")))


# ---------------------------------------------------------------------------
# locks
# ---------------------------------------------------------------------------


class LocksTest(Tmp):
    def test_acquire_busy_release(self):
        a = locks.Lock("bench-a", lock_dir=self.tmp, heartbeat=0, skill="s")
        a.acquire()
        data = a.read()
        for k in (
            "lock_id",
            "purpose",
            "holder",
            "acquired_at",
            "heartbeat_at",
            "expires_at",
        ):
            self.assertIn(k, data)
        self.assertEqual(data["skill"], "s")
        b = locks.Lock("bench-a", lock_dir=self.tmp, heartbeat=0)
        with self.assertRaises(locks.LockBusy):
            b.acquire(wait=0)
        a.release()
        self.assertFalse(os.path.exists(a.path))
        with b:
            self.assertTrue(os.path.exists(b.path))
        self.assertFalse(os.path.exists(b.path))

    def test_stale_lock_needs_expiry_and_dead_pid(self):
        path = os.path.join(self.tmp, "x.lock")
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()

        def write(pid, expires):
            with open(path, "w") as fh:
                json.dump(
                    {"lock_id": "x", "holder": {"pid": pid}, "expires_at": expires}, fh
                )

        write(dead.pid, "2000-01-01T00:00:00Z")
        locks.Lock("x", lock_dir=self.tmp, heartbeat=0).acquire().release()
        write(os.getpid(), "2000-01-01T00:00:00Z")  # expired but holder alive
        with self.assertRaises(locks.LockBusy):
            locks.Lock("x", lock_dir=self.tmp, heartbeat=0).acquire()
        write(dead.pid, "2999-01-01T00:00:00Z")  # holder gone but not expired
        with self.assertRaises(locks.LockBusy):
            locks.Lock("x", lock_dir=self.tmp, heartbeat=0).acquire()

    def test_default_dir_is_outside_stellar_output(self):
        self.assertIn(
            os.path.join("Application Support", "agent-skill-acceptance", "locks"),
            locks.LOCK_DIR,
        )
        self.assertNotIn("STELLAR_OUTPUT", locks.LOCK_DIR)


# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------


class CredsTest(Tmp):
    def test_claude_token_lookup_order(self):
        envf = os.path.join(self.tmp, "x.env")
        with open(envf, "w") as fh:
            fh.write(
                "OTHER=1\nexport ACCEPTANCE_CLAUDE_OAUTH_TOKEN='%s'\n" % FAKE_TOKEN
            )
        tok, src = creds.claude_token(environ={}, env_file=envf, use_bw=False)
        self.assertEqual((tok, src), (FAKE_TOKEN, "stellar_env"))
        tok, src = creds.claude_token(
            environ={"ACCEPTANCE_CLAUDE_CODE_OAUTH_TOKEN": "a"},
            env_file=envf,
            use_bw=False,
        )
        self.assertEqual(src, "env:ACCEPTANCE_CLAUDE_CODE_OAUTH_TOKEN")
        tok, why = creds.claude_token(
            environ={}, env_file=os.path.join(self.tmp, "none"), use_bw=False
        )
        self.assertIsNone(tok)
        self.assertNotIn(FAKE_TOKEN, why)

    def test_bitwarden_fallback_never_prints(self):
        item = {"login": {"password": FAKE_TOKEN}, "fields": []}
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            out = b"SESSION" if cmd[0] == "bash" else json.dumps(item).encode()
            return types.SimpleNamespace(returncode=0, stdout=out, stderr=b"")

        old = (creds.BW_SESSION_SH, creds.shutil.which)
        creds.BW_SESSION_SH = __file__
        creds.shutil.which = lambda n: "/x/bw"
        try:
            tok, src = creds.claude_token(
                environ={}, env_file="/nonexistent", run=fake_run
            )
        finally:
            creds.BW_SESSION_SH, creds.shutil.which = old
        self.assertEqual((tok, src), (FAKE_TOKEN, "bitwarden"))
        self.assertEqual(calls[1][:3], ["bw", "get", "item"])
        self.assertIn(creds.BW_ITEM, calls[1])

    def test_codex_auth_rules(self):
        home = os.path.join(self.tmp, "ch")
        os.makedirs(home)
        raw, why = creds.codex_auth(home)
        self.assertIsNone(raw)
        with open(os.path.join(home, "auth.json"), "w") as fh:
            json.dump({"OPENAI_API_KEY": "sk-proj-" + "a" * 40, "tokens": None}, fh)
        raw, why = creds.codex_auth(home)
        self.assertIsNone(raw)
        self.assertIn("API keys are not allowed", why)
        good = {"tokens": {"refresh_token": "r" * 30, "access_token": "a" * 30}}
        with open(os.path.join(home, "auth.json"), "w") as fh:
            json.dump(good, fh)
        raw, why = creds.codex_auth(home)
        self.assertIsNotNone(raw)
        self.assertEqual(
            sorted(creds.secret_values_from_codex_auth(raw)), ["a" * 30, "r" * 30]
        )
        self.assertEqual(creds.write_back_codex_auth(raw, home), "unchanged")
        new = json.dumps({"tokens": {"refresh_token": "n" * 30}}).encode()
        self.assertEqual(creds.write_back_codex_auth(new, home), "updated")
        mode = stat.S_IMODE(os.stat(os.path.join(home, "auth.json")).st_mode)
        self.assertEqual(mode, 0o600)
        self.assertEqual(
            creds.write_back_codex_auth(b"not json", home), "rejected:not json"
        )
        self.assertEqual(creds.write_back_codex_auth(b"{}", home), "rejected:no tokens")
        self.assertEqual(os.listdir(home), ["auth.json"])
        self.assertEqual(creds.codex_home_clean(home), [])
        os.makedirs(os.path.join(home, "memories"))
        self.assertEqual(creds.codex_home_clean(home), ["memories"])

    def test_leak_scan(self):
        d = os.path.join(self.tmp, "ev")
        os.makedirs(os.path.join(d, "sub"))
        files = {
            "ok.txt": b"nothing here",
            "sub/t.jsonl": ("echo %s" % FAKE_TOKEN).encode(),
            "auth.json": b'{"tokens": {"refresh_token": "abcdefghijklmnopqrstuvwxyz", "account_id": "x"}}',
            "exact.txt": b"value=plainsecretvalue123",
            "page.html": b'<script>{"access_token":"publicstorefronttoken12345"} eyJhbGciOiJIUzI1NiJ9abcdefghijklmnop.eyJzdWIiOiIxMjM0NTY3ODkwIn0abcdefgh.sig_abcdefghij</script>',
        }
        for rel, data in files.items():
            with open(os.path.join(d, rel), "wb") as fh:
                fh.write(data)
        hits = creds.leak_scan(d, exact=["plainsecretvalue123"])
        got = {h["path"]: h["hits"] for h in hits}
        self.assertEqual(
            sorted(got),
            sorted(["auth.json", "exact.txt", os.path.join("sub", "t.jsonl")]),
        )
        self.assertIn("exact_secret", got["exact.txt"])
        self.assertTrue(os.path.exists(os.path.join(d, "ok.txt")))
        self.assertFalse(os.path.exists(os.path.join(d, "exact.txt")))


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------


def good_result(**kw):
    res = {
        "skill": "s",
        "version": "0.1.0",
        "package_sha256": "a" * 64,
        "layer": "L2",
        "bench_id": "b",
        "baseline_id": "agent-clients-2026-10-27",
        "client": "script",
        "client_version": "Python 3.15.2",
        "os": "windows-11-25h2",
        "arch": "arm64",
        "case_id": "normal",
        "triggered": None,
        "status": None,
        "verdict": "pass",
        "reasons": [],
        "deliverables": [],
        "duration_s": 1.0,
        "started_at": "2026-10-28T01:00:00Z",
        "verified_at": "2026-10-28T01:01:00Z",
    }
    res.update(kw)
    return res


class ResultsTest(Tmp):
    def test_validate(self):
        self.assertEqual(results.validate(good_result()), [])
        errs = results.validate(
            good_result(verdict="ok", started_at="2026-10-28 01:00", package_sha256="x")
        )
        self.assertEqual(len(errs), 3)
        res = good_result()
        del res["triggered"]
        self.assertIn("missing triggered", results.validate(res))
        with self.assertRaises(ValueError):
            results.write_result(
                os.path.join(self.tmp, "r.json"), good_result(verdict="maybe")
            )

    def test_script_verdict(self):
        self.assertEqual(results.script_verdict(None, {})[0], "inconclusive")
        meta = {"install_ok": True, "steps": [{"exit_code": 0}, {"exit_code": 0}]}
        self.assertEqual(results.script_verdict(meta, {}), ("pass", []))
        meta["steps"][1] = {"exit_code": 1}
        v, r = results.script_verdict(meta, {1: "Traceback (most recent call last):\n"})
        self.assertEqual(v, "fail")
        self.assertEqual(r, ["step 1 exit 1", "step 1 python traceback"])
        meta = {"install_ok": True, "steps": [], "error": "no Python 3.8+ found"}
        self.assertEqual(results.script_verdict(meta, {})[0], "inconclusive")
        meta = {"install_ok": True, "steps": [{"exit_code": 0}], "upload_failed": 2}
        self.assertEqual(results.script_verdict(meta, {})[0], "inconclusive")

    def test_agent_verdict(self):
        meta = {"install_ok": True, "steps": [{"exit_code": 0}]}
        self.assertEqual(
            results.agent_verdict(meta, {"passed": True, "reasons": []}, '{"a":1}', ""),
            ("pass", []),
        )
        v, r = results.agent_verdict(
            meta,
            {"passed": False, "reasons": ["x"]},
            '{"a":1}',
            "HTTP 429 Too Many Requests",
        )
        self.assertEqual((v, r), ("inconclusive", ["rate_limited"]))
        self.assertEqual(
            results.agent_verdict(meta, {"passed": False, "reasons": ["x"]}, "", "")[0],
            "inconclusive",
        )
        self.assertEqual(
            results.agent_verdict(meta, {"passed": False, "reasons": ["x"]}, "{}", "")[
                0
            ],
            "fail",
        )

    def test_deliverable_list_excludes_raw(self):
        d = os.path.join(self.tmp, "deliv", "run 1")
        os.makedirs(os.path.join(d, "原始数据"))
        for p in ("报告.pdf", os.path.join("原始数据", "summary.json")):
            open(os.path.join(d, p), "w").close()
        self.assertEqual(
            results.deliverable_list(os.path.join(self.tmp, "deliv")),
            ["run 1/报告.pdf"],
        )


# ---------------------------------------------------------------------------
# prlctl wrapper
# ---------------------------------------------------------------------------


class FakeRun:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw.get("input")))
        rc, out, err = self.replies.pop(0)
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


class PrlTest(unittest.TestCase):
    def test_protected_vms_refuse_mutation(self):
        for name in ("StellarByte", "Win11-zhCN-demo", "Windows 11 (1)", "macOS"):
            vm = prl.VM(name, run=FakeRun([]))
            for op in (
                lambda: vm.snapshot_switch("{x}"),
                lambda: vm.power("start"),
                lambda: vm.snapshot("n"),
            ):
                with self.assertRaises(prl.ProtectedVM):
                    op()

    def test_exec_retries_transport_errors_only(self):
        run = FakeRun(
            [(255, b"", b"PrlJob_GetRetCode: Invalid argument."), (0, b"ok", b"")]
        )
        vm = prl.VM("t", run=run, sleep=lambda s: None)
        self.assertEqual(vm.exec_raw(["hostname"]).stdout, b"ok")
        self.assertEqual(len(run.calls), 2)
        run = FakeRun([(1, b"", b"real failure")])
        vm = prl.VM("t", run=run, sleep=lambda s: None)
        self.assertEqual(vm.exec_raw(["x"]).returncode, 1)
        run = FakeRun([(255, b"", b"Invalid argument")] * 3)
        with self.assertRaises(prl.TransportError):
            prl.VM("t", run=run, sleep=lambda s: None).exec_raw(["x"])

    def test_ps_goes_over_stdin_with_crlf(self):
        run = FakeRun([(0, b'noise\n{"a": 1}\n', b"")])
        vm = prl.VM("t", run=run, sleep=lambda s: None)
        self.assertEqual(vm.ps_json("Write-Output 1\n'x'"), {"a": 1})
        cmd, data = run.calls[0]
        self.assertEqual(cmd[-2:], ["-Command", "-"])
        self.assertIn(b"Write-Output 1\r\n'x'", data)
        self.assertNotIn("Write-Output", " ".join(cmd))

    def test_wait_ready_needs_user_explorer_twice(self):
        good = json.dumps(
            {"user": "PC\\ming", "explorer": [{"user": "ming", "session": 1}]}
        ).encode()
        notyet = json.dumps({"user": None, "explorer": []}).encode()
        run = FakeRun([(0, notyet, b""), (0, good, b""), (0, good, b"")])
        vm = prl.VM("t", run=run, sleep=lambda s: None)
        info = vm.wait_ready("ming", timeout=60, clock=lambda: 0)
        self.assertEqual(info["explorer"][0]["session"], 1)
        self.assertEqual(len(run.calls), 3)

    def test_ps_quote(self):
        self.assertEqual(prl.ps_quote("a'b"), "'a''b'")

    def test_run_as_user_level(self):
        run = FakeRun([(0, b'{"started":true}', b"")])
        vm = prl.VM("t", run=run, sleep=lambda s: None)
        vm.run_as_user("T", "ming", r"C:\x.ps1", run_level="Limited")
        self.assertIn(b"-RunLevel Limited", run.calls[0][1])
        with self.assertRaises(ValueError):
            vm.run_as_user("T", "ming", r"C:\x.ps1", run_level="System")


# ---------------------------------------------------------------------------
# run_vm planning
# ---------------------------------------------------------------------------

LEDGER_BENCH = {
    "id": "vm-parallels-win11-arm64-zhcn-agent",
    "kind": "parallels_vm",
    "host": {"hostname": "MacPro", "accessMethod": "prlctl", "loginUser": "ming"},
    "arch": "arm64",
    "os": {"family": "windows", "version": "11 25H2 (build 26200.8875) ARM64"},
    "role": ["skill_acceptance"],
    "isolation": "snapshot_revert",
    "status": "ACTIVE",
    "baselines": [
        {
            "baselineId": "agent-clients-2026-10-27",
            "revertCommand": "prlctl snapshot-switch 'skilltest-win11-arm64-zhcn' --id {c597bf5a-17cc-4f57-bd5b-f090fd982084}",
        }
    ],
}


def ns(**kw):
    base = dict(
        claude_allow=None,
        claude_model=None,
        codex_model="gpt-x",
        timeout=900,
        bench="b",
        guest_root_resolved="C:\\Users\\ming\\Documents\\技能验收 runs",
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


class RunVmTest(Tmp):
    def test_bench_from_ledger(self):
        b = run_vm.bench_from_ledger(LEDGER_BENCH)
        self.assertEqual(b["vm"], "skilltest-win11-arm64-zhcn")
        self.assertEqual(b["snapshot_id"], "{c597bf5a-17cc-4f57-bd5b-f090fd982084}")
        self.assertEqual(
            (b["os"], b["arch"], b["user"]), ("windows-11-25h2", "arm64", "ming")
        )
        for patch in ({"kind": "hyperv_vm"}, {"status": "RETIRED"}, {"baselines": []}):
            bad = dict(LEDGER_BENCH, **patch)
            with self.assertRaises(SystemExit):
                run_vm.bench_from_ledger(bad)

    def test_zip_identity(self):
        z = os.path.join(self.tmp, "profit-model-v0.1.0.zip")
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("profit-model/SKILL.md", "x")
            f.writestr("profit-model/scripts/a.py", "x")
        self.assertEqual(run_vm.zip_identity(z), ("profit-model", "0.1.0"))
        z2 = os.path.join(self.tmp, "bad.zip")
        with zipfile.ZipFile(z2, "w") as f:
            f.writestr("a/SKILL.md", "x")
            f.writestr("b/SKILL.md", "x")
        with self.assertRaises(SystemExit):
            run_vm.zip_identity(z2)

    def test_agent_argv_no_bypass(self):
        case = {
            "id": "normal",
            "prompt": "帮我拆解 allbirds.com",
            "needs_network": True,
        }
        codex = run_vm.agent_argv("codex-cli", case, ns())
        self.assertEqual(codex[:2], ["exec", "--sandbox"])
        self.assertIn("sandbox_workspace_write.network_access=true", codex)
        self.assertIn("--ignore-user-config", codex)
        self.assertEqual(codex[-1], case["prompt"])
        claude = run_vm.agent_argv("claude-cli", case, ns())
        self.assertEqual(claude[0], "-p")
        self.assertNotIn(
            "--settings", claude
        )  # allowlist mode: no OS sandbox on Windows
        self.assertIn("Bash(python:*)", claude)
        for argv in (codex, claude):
            for bad in ("--dangerously-skip-permissions", "--bare", "--yolo"):
                self.assertNotIn(bad, argv)

    def test_build_plan_script_and_secret_wire(self):
        acc = load_acc("profit-model")
        case = acc["cases"][0]
        plan = run_vm.build_plan(
            "r",
            "profit-model",
            {"name": "z"},
            "script",
            [case],
            acc,
            ns(),
            "http://h/put/t",
            None,
        )
        c = plan["cases"][0]
        self.assertEqual(c["upload_prefix"], "L2-b-script-normal")
        self.assertTrue(all(s["argv"][0] == "{python}" for s in c["steps"]))
        self.assertIn("技能验收 runs", plan["guest_root"])
        json.dumps(plan, ensure_ascii=False)

        class FakeProc:
            def __init__(self, *a, **kw):
                self.args = a[0]
                self.stdin = self

            def write(self, data):
                self.data = data

            def close(self):
                pass

        old = run_vm.subprocess.Popen
        run_vm.subprocess.Popen = FakeProc
        try:
            proc = run_vm.start_secret_pipe(
                prl.VM("t"),
                "pipe1",
                "ming",
                {
                    "env": {"CLAUDE_CODE_OAUTH_TOKEN": FAKE_TOKEN},
                    "_exact": [FAKE_TOKEN],
                },
            )
        finally:
            run_vm.subprocess.Popen = old
        self.assertNotIn(FAKE_TOKEN, " ".join(proc.args))
        wire = json.loads(base64.b64decode(proc.data.strip()))
        self.assertEqual(wire, {"env": {"CLAUDE_CODE_OAUTH_TOKEN": FAKE_TOKEN}})

    def test_bootstrap_script_checks_sha_and_size(self):
        s = run_vm.bootstrap_script(
            "http://10.211.55.2:1/get",
            [{"name": "a.zip", "size": 3, "sha256": "f" * 64}],
            "ming",
        )
        self.assertIn("Get-FileHash", s)
        self.assertIn("-ne 3", s)
        self.assertIn("'ming:(OI)(CI)M'", s)

    def test_guest_scripts_are_ascii(self):
        gdir = os.path.join(ACC, "vm", "guest")
        for f in os.listdir(gdir):
            if f.endswith(".ps1"):
                with open(os.path.join(gdir, f), "rb") as fh:
                    data = fh.read()
                self.assertTrue(all(b < 128 for b in data), f)

    def test_secret_for_pending_without_credentials(self):
        old = (creds.claude_token, creds.codex_auth)
        creds.claude_token = lambda: (None, "no token")
        creds.codex_auth = lambda: (None, "no login")
        try:
            self.assertEqual(run_vm.secret_for("claude-cli"), (None, "no token"))
            self.assertEqual(run_vm.secret_for("codex-cli"), (None, "no login"))
            self.assertEqual(run_vm.secret_for("script"), (None, None))
        finally:
            creds.claude_token, creds.codex_auth = old


# ---------------------------------------------------------------------------
# clean L1 runners in run.py
# ---------------------------------------------------------------------------


class CleanRunnersTest(Tmp):
    def test_codex_clean_env_and_cmd(self):
        home = os.path.join(self.tmp, "codex-acc")
        os.makedirs(home)
        env, why = host_run.clean_env(
            "codex-clean", {"HOME": "/Users/x"}, self.tmp, codex_home=home
        )
        self.assertIsNone(env)
        self.assertIn("codex login", why)
        with open(os.path.join(home, "auth.json"), "w") as fh:
            json.dump({"tokens": {"refresh_token": "r" * 30}}, fh)
        base = {
            "HOME": "/Users/x",
            "OPENAI_API_KEY": "sk-x",
            "CODEX_HOME": "/Users/x/.codex",
        }
        env, why = host_run.clean_env("codex-clean", base, self.tmp, codex_home=home)
        self.assertEqual(env["CODEX_HOME"], home)
        self.assertEqual(env["HOME"], os.path.join(self.tmp, "_home"))
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertEqual(os.listdir(env["HOME"]), [])
        os.makedirs(os.path.join(home, "skills"))
        env, why = host_run.clean_env("codex-clean", base, self.tmp, codex_home=home)
        self.assertIsNone(env)
        self.assertIn("skills", why)
        args = types.SimpleNamespace(
            codex_model="m", claude_mode="sandbox", claude_allow=None, claude_model=None
        )
        cmd = host_run.build_agent_cmd("codex-clean", {"prompt": "p"}, args, {})
        self.assertIn("sandbox_workspace_write.network_access=true", cmd)
        self.assertIn("--ignore-user-config", cmd)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", cmd)

    def test_claude_clean_env_and_cmd(self):
        env, why = host_run.clean_env(
            "claude-clean",
            {"ANTHROPIC_API_KEY": "k"},
            self.tmp,
            token_lookup=lambda: (FAKE_TOKEN, "env"),
        )
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], FAKE_TOKEN)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertTrue(env["HOME"].startswith(self.tmp))
        env, why = host_run.clean_env(
            "claude-clean", {}, self.tmp, token_lookup=lambda: (None, "none")
        )
        self.assertEqual((env, why), (None, "none"))
        args = types.SimpleNamespace(
            codex_model=None,
            claude_mode="sandbox",
            claude_allow=None,
            claude_model=None,
        )
        cmd = host_run.build_agent_cmd(
            "claude-clean", {"prompt": "p", "id": "x"}, args, {}
        )
        self.assertEqual(cmd[:2], ["claude", "-p"])
        self.assertIn("--settings", cmd)
        self.assertNotIn("--bare", cmd)
        self.assertNotIn("--dangerously-skip-permissions", cmd)

    def test_judge_runner_key(self):
        case = {"expect": {"status_by_runner": {"codex-nonet": ["failed"]}}}
        self.assertEqual(host_run.judge_runner_key("codex-clean", case), "codex-net")
        self.assertEqual(host_run.judge_runner_key("claude-clean", case), "claude")
        case = {"expect": {"expect_by_runner": {"codex-clean": {}}}}
        self.assertEqual(host_run.judge_runner_key("codex-clean", case), "codex-clean")
        self.assertTrue(host_run.is_claude("claude-clean"))


if __name__ == "__main__":
    unittest.main()
