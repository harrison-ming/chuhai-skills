#!/usr/bin/env python3
"""L2 clean-VM acceptance runner (testing protocol sections 4-8).

    python3 tools/acceptance/vm/run_vm.py --bench <bench id> \
        --skill-zip <built zip> --acceptance skills/<name>/acceptance.json \
        [--runners script,codex,claude] [--cases a,b] [--out <run dir>]

One round = one skill zip x one client x one test VM x the selected cases:
  S0 lock -> S1 revert to the client-layer snapshot -> S2 readiness (exec +
  test user's explorer in session 1) -> S3 credentials over stdin + named
  pipe (agent clients only) -> S4 deliver zip + guest scripts over a
  one-round HTTP server bound to 10.211.55.2, sha256 checked in the guest ->
  S5/S6 install + run cases as the test user (scheduled task, session 1) ->
  S7 evidence PUT back to the host -> S8 Codex auth.json back to
  ~/.codex-acceptance, guest copy deleted -> S9 leak scan -> S10 revert,
  power off -> S11 unlock.
Only files that came back count; exit codes and console output do not.

Runners: script (no agent: skill scripts run with the VM's Python, steps
from acceptance.json script_steps / script_cmds), codex (codex-cli; needs
~/.codex-acceptance/auth.json, ChatGPT login, never an API key), claude
(claude-cli; needs the acceptance OAuth token). Agent runners without
credentials are reported as pending, not run.

Bench facts (VM name, snapshot id, baseline id, os, arch, test user) are read
from the clean-room ledger bench.json (--bench-json, or env
AGENT_SKILL_BENCH_JSON). Python 3.8+, stdlib only.
"""

import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import types
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ACC_DIR = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ACC_DIR)

import creds  # noqa: E402
import judge  # noqa: E402
import locks  # noqa: E402
import prl  # noqa: E402
import results  # noqa: E402
import steps as steps_mod  # noqa: E402
import transfer  # noqa: E402

DEFAULT_BENCH_JSON = os.environ.get(
    "AGENT_SKILL_BENCH_JSON",
    os.path.join(
        os.path.expanduser("~"),
        "Documents",
        "STELLAR INFRA",
        "DEPLOY",
        "clean-room",
        "bench.json",
    ),
)
OUT_BASE = os.path.join(os.path.expanduser("~"), "STELLAR_OUTPUT", "_tmp", "acceptance")
ROUND_BASE = os.path.join(OUT_BASE, "vm")
GUEST_DIR = r"C:\skilltest\round"
# Chinese + space on purpose: buyers' Documents paths look like this.
DEFAULT_GUEST_ROOT = "%USERPROFILE%\\Documents\\技能验收 runs"
TASK = "SkilltestAcceptanceRound"
CLIENTS = {
    "script": "script",
    "codex": "codex-cli",
    "codex-cli": "codex-cli",
    "claude": "claude-cli",
    "claude-cli": "claude-cli",
}
ZIP_RE = re.compile(r"^(?P<name>.+?)(?:-pro)?-v(?P<ver>\d+\.\d+\.\d+[^/]*)\.zip$")


def log(msg):
    sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    sys.stderr.flush()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# bench
# ---------------------------------------------------------------------------


def load_bench(bench_json, bench_id):
    with open(bench_json, encoding="utf-8") as fh:
        inv = json.load(fh)
    for b in inv.get("benches") or []:
        if b.get("id") == bench_id:
            return bench_from_ledger(b)
    raise SystemExit("bench %s not in %s" % (bench_id, bench_json))


def bench_from_ledger(b):
    if (
        b.get("kind") != "parallels_vm"
        or (b.get("host") or {}).get("accessMethod") != "prlctl"
    ):
        raise SystemExit(
            "bench %s: only parallels_vm/prlctl is implemented" % b.get("id")
        )
    if b.get("isolation") != "snapshot_revert" or not b.get("baselines"):
        raise SystemExit("bench %s has no baseline to revert to" % b.get("id"))
    if b.get("status") != "ACTIVE":
        raise SystemExit("bench %s is %s" % (b.get("id"), b.get("status")))
    base = b["baselines"][-1]
    m = re.search(
        r"snapshot-switch\s+'([^']+)'\s+--id\s+(\{[0-9a-fA-F-]{36}\})",
        base["revertCommand"],
    )
    if not m:
        raise SystemExit("cannot parse revertCommand of %s" % b["id"])
    osv = (b.get("os") or {}).get("version") or ""
    rel = re.search(r"\b(2\dH\d)\b", osv)
    os_label = "%s-11%s" % (
        (b.get("os") or {}).get("family") or "windows",
        "-" + rel.group(1).lower() if rel else "",
    )
    return {
        "id": b["id"],
        "vm": m.group(1),
        "snapshot_id": m.group(2),
        "baseline_id": base["baselineId"],
        "os": os_label,
        "arch": b.get("arch") or "arm64",
        "user": (b.get("host") or {}).get("loginUser") or "ming",
    }


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


def zip_identity(path):
    """(skill name, version) from the zip's single top-level dir + file name."""
    with zipfile.ZipFile(path) as z:
        tops = {n.split("/")[0] for n in z.namelist() if n.strip("/")}
        names = [n for n in z.namelist() if n.endswith("/SKILL.md")]
    if len(tops) != 1:
        raise SystemExit(
            "zip must contain exactly one top-level skill dir: %s" % sorted(tops)
        )
    name = tops.pop()
    if "%s/SKILL.md" % name not in names:
        raise SystemExit("zip has no %s/SKILL.md" % name)
    m = ZIP_RE.match(os.path.basename(path))
    return name, (m.group("ver") if m else None)


def agent_argv(client, case, args):
    """argv for the client (without the executable), reusing run.py's choices."""
    import run as host_run  # tools/acceptance/run.py

    ns = types.SimpleNamespace(
        claude_mode="allowlist",  # no Claude Code OS sandbox on Windows; the VM is the boundary
        claude_allow=args.claude_allow,
        claude_model=args.claude_model,
        codex_model=args.codex_model,
    )
    if client == "claude-cli":
        cmd = host_run.build_agent_cmd("claude", case, ns, {})
    else:
        if not args.codex_model:
            ns.codex_model = host_run.codex_model()
        runner = "codex-net" if case.get("needs_network", True) else "codex-nonet"
        cmd = host_run.build_agent_cmd(runner, case, ns, {})
    for bad in (
        "--dangerously-skip-permissions",
        "--yolo",
        "--bare",
        "--dangerously-bypass-approvals-and-sandbox",
    ):
        if bad in cmd:
            raise AssertionError("permission bypass flag in %s argv" % client)
    return cmd[1:]


def build_plan(
    round_id, skill, zip_info, client, cases, acc, args, put_base, secret_pipe
):
    plan_cases = []
    for c in cases:
        dom = judge_domain(c)
        entry = {
            "id": c["id"],
            "client": client,
            "timeout": args.timeout,
            "upload_prefix": case_dir_name(args.bench, client, c["id"]),
        }
        if client == "script":
            entry["steps"] = steps_mod.plan_for_case(acc, c, dom)
        else:
            entry["argv"] = agent_argv(client, c, args)
        plan_cases.append(entry)
    return {
        "round_id": round_id,
        "skill": skill,
        "zip": zip_info,
        "guest_root": args.guest_root_resolved,
        "put_base": put_base,
        "secret_pipe": secret_pipe,
        "cases": plan_cases,
    }


def judge_domain(case):
    import run as host_run

    return host_run.case_domain(case)


def case_dir_name(bench_id, client, case_id):
    return "L2-%s-%s-%s" % (bench_id, client, case_id)


# ---------------------------------------------------------------------------
# one round
# ---------------------------------------------------------------------------


class RoundError(RuntimeError):
    pass


def guest_root(user, template):
    return template.replace("%USERPROFILE%", "C:\\Users\\%s" % user)


def bootstrap_script(get_base, files, user):
    """SYSTEM side: fresh round dir, download + verify every file, clean old task."""
    lines = [
        "$ErrorActionPreference='Stop'",
        "$d=%s" % prl.ps_quote(GUEST_DIR),
        "Unregister-ScheduledTask -TaskName %s -Confirm:$false -ErrorAction SilentlyContinue"
        % prl.ps_quote(TASK),
        "Remove-Item -Recurse -Force $d -ErrorAction SilentlyContinue",
        "New-Item -ItemType Directory -Force $d | Out-Null",
        "& icacls.exe $d /grant %s | Out-Null" % prl.ps_quote("%s:(OI)(CI)M" % user),
        "$bad=@()",
    ]
    for f in files:
        lines.append(
            "$p=Join-Path $d %s; Invoke-WebRequest -UseBasicParsing -Uri %s -OutFile $p; "
            "if ((Get-Item $p).Length -ne %d -or (Get-FileHash -Algorithm SHA256 $p).Hash.ToLower() -ne %s) { $bad+=%s }"
            % (
                prl.ps_quote(f["name"]),
                prl.ps_quote("%s/%s" % (get_base, f["name"])),
                f["size"],
                prl.ps_quote(f["sha256"]),
                prl.ps_quote(f["name"]),
            )
        )
    lines.append("@{ok=($bad.Count -eq 0); bad=$bad} | ConvertTo-Json -Compress")
    return "\n".join(lines)


def start_secret_pipe(vm, pipe_name, user, payload_obj):
    """Spawn the SYSTEM-side pipe server; the secret goes over stdin only."""
    wire = {k: v for k, v in payload_obj.items() if not k.startswith("_")}
    payload = base64.b64encode(json.dumps(wire).encode("utf-8")) + b"\r\n"
    proc = subprocess.Popen(
        [
            vm.prlctl,
            "exec",
            vm.name,
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            GUEST_DIR + r"\secret-pipe.ps1",
            "-Name",
            pipe_name,
            "-User",
            user,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    proc.stdin.write(payload)
    proc.stdin.close()
    payload = None
    return proc


def run_round(ctx, client, cases, secret=None):
    """Returns list of per-case rows (result dicts) or raises RoundError."""
    args, bench, acc = ctx["args"], ctx["bench"], ctx["acc"]
    vm = prl.VM(bench["vm"])
    round_id = "%s-%s-%s" % (
        ctx["skill"],
        client,
        datetime.datetime.now().strftime("%Y%m%d-%H%M%S"),
    )
    round_dir = os.path.join(ROUND_BASE, round_id)
    serve = os.path.join(round_dir, "serve")
    os.makedirs(serve, exist_ok=True)
    shutil.copy(ctx["zip"], os.path.join(serve, ctx["zip_info"]["name"]))
    for f in ("round.ps1", "secret-pipe.ps1"):
        shutil.copy(os.path.join(HERE, "guest", f), os.path.join(serve, f))

    log("[%s] S1 revert %s -> %s" % (client, bench["vm"], bench["baseline_id"]))
    vm.snapshot_switch(bench["snapshot_id"])
    if vm.state() != "running":
        vm.power("start")
    log("[%s] S2 readiness" % client)
    ready = vm.wait_ready(bench["user"], timeout=args.ready_timeout)

    srv = transfer.RoundServer(serve, ctx["run_dir"]).start()
    pipe_proc = None
    codex_auth_state = None
    try:
        pipe_name = None
        if secret:
            pipe_name = "skilltest-%s" % os.urandom(8).hex()
        plan = build_plan(
            round_id,
            ctx["skill"],
            ctx["zip_info"],
            client,
            cases,
            acc,
            args,
            srv.put_base,
            pipe_name,
        )
        with open(os.path.join(serve, "plan.json"), "w", encoding="utf-8") as fh:
            json.dump(plan, fh, ensure_ascii=False, indent=1)
        files = []
        for name in (
            ctx["zip_info"]["name"],
            "round.ps1",
            "secret-pipe.ps1",
            "plan.json",
        ):
            p = os.path.join(serve, name)
            files.append(
                {"name": name, "size": os.path.getsize(p), "sha256": sha256_file(p)}
            )
        log("[%s] S4 deliver %d files" % (client, len(files)))
        boot = vm.ps_json(  # idempotent: recreates the round dir from scratch
            bootstrap_script(srv.get_base, files, bench["user"]), timeout=300, tries=3
        )
        if not boot.get("ok"):
            raise RoundError("delivery check failed in guest: %s" % boot.get("bad"))

        if secret:
            log("[%s] S3 credentials via stdin -> named pipe" % client)
            pipe_proc = start_secret_pipe(vm, pipe_name, bench["user"], secret)

        log("[%s] S5/S6 run %d case(s) as %s" % (client, len(cases), bench["user"]))
        started = time.time()
        vm.run_as_user(
            TASK,
            bench["user"],
            GUEST_DIR + r"\round.ps1",
            '-Dir "%s"' % GUEST_DIR,
            run_level="Limited",  # the buyer's normal, non-elevated token
        )
        done = os.path.join(ctx["run_dir"], "_round_done.json")
        if os.path.exists(done):
            os.remove(done)
        budget = args.timeout * len(cases) + 900
        last_check = 0
        while not os.path.exists(done):
            if time.time() - started > budget:
                raise RoundError(
                    "round timed out after %ss (no _round_done.json)" % budget
                )
            if time.time() - last_check > 120:
                last_check = time.time()
                try:
                    st = vm.task_state(TASK)
                    if (
                        st.get("state") == "Ready"
                        and time.time() - started > 120
                        and not os.path.exists(done)
                    ):
                        time.sleep(20)
                        if not os.path.exists(done):
                            raise RoundError(
                                "guest task ended without _round_done.json (last=%s)"
                                % st.get("last")
                            )
                except prl.TransportError as e:
                    log("task state probe failed: %s" % e)
                ctx["lock"].update(round_id=round_id)
            time.sleep(5)
        with open(done, encoding="utf-8-sig") as fh:
            round_res = json.load(fh)
        os.replace(done, os.path.join(round_dir, "_round_done.json"))
        for extra in ("env.json", "round.log"):
            p = os.path.join(ctx["run_dir"], extra)
            if os.path.exists(p):
                os.replace(p, os.path.join(round_dir, extra))
        if pipe_proc:
            try:
                out, _err = pipe_proc.communicate(timeout=60)
                ok = b'"ok":true' in out
            except subprocess.TimeoutExpired:
                pipe_proc.kill()
                ok = False
            if not ok:
                log("secret pipe did not report ok")
        if client == "codex-cli":
            codex_auth_state = collect_codex_auth(vm)
            log("[%s] S8 codex auth: %s" % (client, codex_auth_state))
    finally:
        srv.stop()
        if client == "codex-cli" and pipe_proc is not None and codex_auth_state is None:
            # The round failed after the login went in: the guest copy may hold
            # the only valid (refreshed) token. Bring it back before any revert.
            codex_auth_state = collect_codex_auth(vm, missing_ok=True)
            log("[%s] S8 (after failure) codex auth: %s" % (client, codex_auth_state))

    env = {}
    try:
        with open(os.path.join(round_dir, "env.json"), encoding="utf-8-sig") as fh:
            env = json.load(fh)
    except (OSError, ValueError):
        pass
    rows = []
    exact = (secret or {}).get("_exact") or []
    for c in cases:
        rows.append(judge_case(ctx, client, c, env, ready, round_res, exact))
    return rows, codex_auth_state


def collect_codex_auth(vm, missing_ok=False):
    """S8: guest auth.json -> ~/.codex-acceptance (atomic, 0600), then delete it.

    Any failure to bring a present file back raises RoundError("... NOT
    reverting ..."): the caller then keeps the VM and the locks for a human.
    missing_ok: the round may have died before the guest wrote the file.
    """
    path = GUEST_DIR + r"\codex-home\auth.json"
    try:
        raw = vm.read_file(path)
    except prl.TransportError as e:
        raise RoundError("cannot read guest auth.json (%s); NOT reverting" % e)
    if raw is None and missing_ok:
        return "absent"
    if raw is None:
        raise RoundError(
            "codex auth.json missing in guest; NOT reverting, check by hand"
        )
    state = creds.write_back_codex_auth(raw)
    if state.startswith("rejected"):
        raise RoundError("guest auth.json %s; NOT reverting, check by hand" % state)
    vm.ps(
        "Remove-Item -Recurse -Force %s" % prl.ps_quote(GUEST_DIR + r"\codex-home"),
        tries=1,
    )
    if vm.read_file(path) is not None:
        raise RoundError("guest auth.json still present after delete")
    return state


def judge_case(ctx, client, case, env, ready, round_res, exact):
    bench, acc = ctx["bench"], ctx["acc"]
    cdir = os.path.join(ctx["run_dir"], case_dir_name(bench["id"], client, case["id"]))
    meta = None
    try:
        with open(os.path.join(cdir, "case-meta.json"), encoding="utf-8-sig") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        pass
    leaks = (
        creds.leak_scan(cdir, exact=exact, delete=True) if os.path.isdir(cdir) else []
    )
    gl = None
    if os.path.isdir(cdir) and client != "script":
        gl = creds.gitleaks_scan(
            cdir,
            os.path.join(ctx["run_dir"], "_gitleaks-%s-%s.json" % (client, case["id"])),
        )
    deliv_root = os.path.join(cdir, "deliverables")
    status, triggered, judged = None, None, {}
    if client == "script":
        errs = {}
        for i in range(len((meta or {}).get("steps") or [])):
            errs[i] = read_text(os.path.join(cdir, "step%d.err" % i))
        verdict, reasons = results.script_verdict(meta, errs)
        rpath, rtext = judge.find_report(deliv_root)
        status = judge.extract_status(rtext) if rtext else None
        client_version = env.get("python")
    else:
        transcript = os.path.join(cdir, "transcript.jsonl")
        events = judge.read_jsonl(transcript)
        parse = judge.parse_claude if client == "claude-cli" else judge.parse_codex
        parsed = parse(events, ctx["skill"])
        judged = judge.judge_agent_run(
            case,
            parsed,
            deliv_root,
            ctx["skill_dir"],
            acc,
            python=sys.executable,
            runner=client,
        )
        verdict, reasons = results.agent_verdict(
            meta,
            judged,
            read_text(transcript),
            read_text(os.path.join(cdir, "stderr.txt")),
        )
        status = judged.get("status")
        triggered = parsed.get("triggered")
        client_version = (env.get("clients") or {}).get(client)
        with open(os.path.join(cdir, "judge.json"), "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "verdict": judged,
                    "parsed": {k: v for k, v in parsed.items() if k != "final_message"},
                },
                fh,
                ensure_ascii=False,
                indent=1,
                default=str,
            )
    if gl and gl.get("findings"):
        # scraped public pages (raw evidence) trip generic rules; only agent
        # output outside the raw-data folders counts as a leak candidate
        raw_parts = ("原始数据", "raw", "data")
        gl["findings"] = [
            f
            for f in gl["findings"]
            if not any(
                p in raw_parts
                for p in str(f.get("file") or "").replace("\\", "/").split("/")
            )
        ]
    if leaks or (gl and gl.get("findings")):
        verdict = "inconclusive"
        hit_paths = [hit["path"] for hit in leaks]
        if gl and gl.get("findings"):
            hit_paths += ["gitleaks:%s" % f.get("file") for f in gl["findings"]]
        reasons = reasons + ["credential_leak_in_evidence: %s" % ", ".join(hit_paths)]
    started = (meta or {}).get("started_at") or ctx["started_at"]
    dur = sum(float(s.get("duration_s") or 0) for s in (meta or {}).get("steps") or [])
    res = {
        "skill": ctx["skill"],
        "version": ctx["version"],
        "package_sha256": ctx["zip_info"]["sha256"],
        "layer": "L2",
        "bench_id": bench["id"],
        "baseline_id": bench["baseline_id"],
        "client": client,
        "client_version": client_version,
        "os": bench["os"],
        "arch": bench["arch"],
        "case_id": case["id"],
        "triggered": triggered,
        "status": status,
        "verdict": verdict,
        "reasons": reasons,
        "deliverables": results.deliverable_list(deliv_root),
        "permission_mode": {
            "script": "n/a",
            "codex-cli": "workspace-write",
            "claude-cli": "allowlist",
        }[client],
        "driver": "headless",
        "duration_s": round(dur, 1),
        "started_at": started,
        "verified_at": results.utc_now(),
        "env": env,
        "guest": {"host": ready.get("host"), "session_user": ready.get("user")},
    }
    if client == "script":
        res["steps"] = [
            {k: s.get(k) for k in ("argv", "exit_code", "timed_out", "duration_s")}
            for s in (meta or {}).get("steps") or []
        ]
    results.write_result(os.path.join(cdir, "result.json"), res)
    return res


def read_text(path):
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return ""
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def secret_for(client):
    """(payload dict, None) or (None, pending reason). Never logs values."""
    if client == "script":
        return None, None
    if client == "claude-cli":
        tok, why = creds.claude_token()
        if not tok:
            return None, why
        return {"env": {"CLAUDE_CODE_OAUTH_TOKEN": tok}, "_exact": [tok]}, None
    raw, why = creds.codex_auth()
    if raw is None:
        return None, why
    return (
        {
            "codex_auth_b64": base64.b64encode(raw).decode("ascii"),
            "_exact": creds.secret_values_from_codex_auth(raw),
        },
        None,
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description="L2 clean-VM acceptance runner")
    ap.add_argument("--bench", required=True)
    ap.add_argument("--skill-zip", required=True)
    ap.add_argument("--acceptance", required=True)
    ap.add_argument("--runners", default="script")
    ap.add_argument("--cases", default="")
    ap.add_argument("--out")
    ap.add_argument("--bench-json", default=DEFAULT_BENCH_JSON)
    ap.add_argument("--timeout", type=int, default=1500, help="seconds per case")
    ap.add_argument("--ready-timeout", type=int, default=600)
    ap.add_argument("--guest-root", default=DEFAULT_GUEST_ROOT)
    ap.add_argument("--rate-limit-retries", type=int, default=2)
    ap.add_argument("--rate-limit-wait", type=int, default=900)
    ap.add_argument("--leave", choices=["stopped", "running"], default="stopped")
    ap.add_argument("--codex-model")
    ap.add_argument("--claude-model")
    ap.add_argument("--claude-allow", action="append")
    ap.add_argument(
        "--dry-run", action="store_true", help="print the plan, touch nothing"
    )
    args = ap.parse_args(argv)

    bench = load_bench(args.bench_json, args.bench)
    args.guest_root_resolved = guest_root(bench["user"], args.guest_root)
    zip_path = os.path.abspath(args.skill_zip)
    skill, version = zip_identity(zip_path)
    with open(args.acceptance, encoding="utf-8") as fh:
        acc = json.load(fh)
    if acc.get("skill") != skill:
        ap.error("acceptance.json is for %s, zip is %s" % (acc.get("skill"), skill))
    wanted = {c.strip() for c in args.cases.split(",") if c.strip()}
    cases = [c for c in acc["cases"] if not wanted or c["id"] in wanted]
    clients = []
    for r in [r.strip() for r in args.runners.split(",") if r.strip()]:
        if r not in CLIENTS:
            ap.error("unknown runner %s" % r)
        if CLIENTS[r] not in clients:
            clients.append(CLIENTS[r])
    zip_info = {
        "name": os.path.basename(zip_path),
        "sha256": sha256_file(zip_path),
        "size": os.path.getsize(zip_path),
    }
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = os.path.abspath(args.out or os.path.join(OUT_BASE, "%s-%s" % (skill, ts)))
    if run_dir.startswith(("/tmp", "/private/tmp")):
        ap.error("refusing to write under /tmp")

    if args.dry_run:
        for client in clients:
            plan = build_plan(
                "dry", skill, zip_info, client, cases, acc, args, "<put>", None
            )
            print(json.dumps(plan, ensure_ascii=False, indent=1))
        return 0

    os.makedirs(run_dir, exist_ok=True)
    skill_dir_tmp = tempfile.mkdtemp(prefix="skill-", dir=run_dir)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(skill_dir_tmp)
    ctx = {
        "args": args,
        "bench": bench,
        "acc": acc,
        "skill": skill,
        "version": version,
        "zip": zip_path,
        "zip_info": zip_info,
        "run_dir": run_dir,
        "skill_dir": os.path.join(skill_dir_tmp, skill),
        "started_at": results.utc_now(),
    }
    meta = {
        "skill": skill,
        "version": version,
        "zip": zip_info["name"],
        "package_sha256": zip_info["sha256"],
        "bench_id": bench["id"],
        "baseline_id": bench["baseline_id"],
        "os": bench["os"],
        "arch": bench["arch"],
        "run_dir": run_dir,
        "started_at": ctx["started_at"],
    }
    rows, pending = [], []
    exit_code = 0
    for client in clients:
        secret, why = secret_for(client)
        if client != "script" and secret is None:
            pending.append({"client": client, "reason": "pending: %s" % why})
            log("[%s] pending: %s" % (client, why))
            continue
        todo = list(cases)
        attempt = 0
        while todo:
            bench_lock = locks.Lock(
                bench["id"],
                ttl=args.timeout * len(todo) + 3600,
                run_id=os.path.basename(run_dir),
                skill=skill,
                client=client,
            )
            codex_lock = None
            keep_locks = False
            try:
                bench_lock.acquire(wait=1800)
                if client == "codex-cli":
                    codex_lock = locks.Lock(
                        locks.CODEX_AUTH_LOCK,
                        ttl=args.timeout * len(todo) + 3600,
                        location=bench["id"],
                        run_id=os.path.basename(run_dir),
                    )
                    codex_lock.acquire(wait=3600)
                    secret, why = secret_for(
                        client
                    )  # re-read: another run may have refreshed it
                    if secret is None:
                        pending.append(
                            {"client": client, "reason": "pending: %s" % why}
                        )
                        break
                ctx["lock"] = bench_lock
                got = None
                for framework_try in (1, 2):  # one retry for bench/transport hiccups
                    try:
                        got, _auth = run_round(ctx, client, todo, secret)
                        break
                    except (RoundError, prl.TransportError) as e:
                        log(
                            "[%s] round inconclusive (try %d): %s"
                            % (client, framework_try, e)
                        )
                        if "NOT reverting" in str(e):
                            keep_locks = True
                            raise
                        got = [inconclusive_row(ctx, client, c, str(e)) for c in todo]
                    finally:
                        if not keep_locks:
                            finish_vm(bench, args)
            finally:
                if not keep_locks:
                    if codex_lock:
                        codex_lock.release()
                    bench_lock.release()
            limited = [
                r
                for r in got
                if r["verdict"] == "inconclusive" and "rate_limited" in r["reasons"]
            ]
            rows.extend(r for r in got if r not in limited)
            if limited and attempt < args.rate_limit_retries:
                attempt += 1
                wait = args.rate_limit_wait * attempt
                log(
                    "[%s] %d case(s) rate limited; waiting %ss before retry %d"
                    % (client, len(limited), wait, attempt)
                )
                time.sleep(wait)
                ids = {r["case_id"] for r in limited}
                todo = [c for c in cases if c["id"] in ids]
                continue
            rows.extend(limited)
            todo = []
    shutil.rmtree(skill_dir_tmp, ignore_errors=True)
    meta["finished_at"] = results.utc_now()
    with open(os.path.join(run_dir, "results.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {"meta": meta, "results": rows, "pending": pending},
            fh,
            ensure_ascii=False,
            indent=2,
        )
    md = results.render_md(meta, rows, pending)
    with open(os.path.join(run_dir, "results.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    print(md)
    print("results: %s" % os.path.join(run_dir, "results.json"))
    if any(r["verdict"] != "pass" for r in rows):
        exit_code = 1
    return exit_code


def inconclusive_row(ctx, client, case, why):
    bench = ctx["bench"]
    res = {
        "skill": ctx["skill"],
        "version": ctx["version"],
        "package_sha256": ctx["zip_info"]["sha256"],
        "layer": "L2",
        "bench_id": bench["id"],
        "baseline_id": bench["baseline_id"],
        "client": client,
        "client_version": None,
        "os": bench["os"],
        "arch": bench["arch"],
        "case_id": case["id"],
        "triggered": None,
        "status": None,
        "verdict": "inconclusive",
        "reasons": ["framework: %s" % why[:300]],
        "deliverables": [],
        "driver": "headless",
        "duration_s": 0,
        "started_at": ctx["started_at"],
        "verified_at": results.utc_now(),
    }
    cdir = os.path.join(ctx["run_dir"], case_dir_name(bench["id"], client, case["id"]))
    results.write_result(os.path.join(cdir, "result.json"), res)
    return res


def finish_vm(bench, args):
    """S10: discard the round's state; leave the VM stopped by default."""
    vm = prl.VM(bench["vm"])
    try:
        vm.snapshot_switch(bench["snapshot_id"])
        if args.leave == "stopped" and vm.state() == "running":
            vm._run(
                [vm.prlctl, "stop", vm.name, "--kill"], capture_output=True, timeout=300
            )
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        log("post-round revert failed: %s" % e)


if __name__ == "__main__":
    sys.exit(main())
