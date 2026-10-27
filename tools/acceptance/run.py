#!/usr/bin/env python3
"""Cross-agent acceptance harness for SKILL.md packages.

Usage:
    python3 tools/acceptance/run.py --skill-dir skills/<name> \
        [--runners claude,codex-net,codex-nonet,script] [--cases normal,l0] \
        [--out <run dir>] [--parallel 3] [--timeout 1500] [--dry-run]

Reads <skill-dir>/acceptance.json:
    {"skill": "<name>",
     "cases": [{"id": "normal", "prompt": "...", "lang": "zh", "needs_network": true,
                "domain": "optional, else first domain-looking token of prompt",
                "expect": {"deliverables": ["*.pdf|*.html", "*.csv"],
                           "status_in": ["complete", "partial"],
                           "must_not_contain": ["products.json"],
                           "must_contain_any": ["[已核实]"],
                           "fixed_ending": true}}],
     "script_cmds": ["{python} {skill_dir}/scripts/x.py {domain} --out-root {out_root}"],
     "fixed_ending_text": "optional; default FIXED_ENDING in scripts/check_report.py"}

Each run gets an isolated workdir
    ~/STELLAR_OUTPUT/_tmp/acceptance/<skill>-<timestamp>/<runner>-<case>/
with CHUHAI_OUTPUT_DIR=<workdir>/deliverables/. Results: results.json and
results.md in the run dir.

Safety: no permission-bypass flag is ever passed. Claude runs with
--permission-mode dontAsk, user settings excluded, no MCP, and a narrow
--allowedTools list; Codex runs with --sandbox workspace-write,
approval_policy=never, user config.toml and exec-policy rules ignored (so the
user's extra writable_roots / MCP servers do not apply).

TODO (out of scope for now): a Windows VM runner (Parallels Win11-zhCN-demo
via prlctl exec) that copies the skill into the guest, runs the script cmds
with the guest Python, and pulls deliverables back for the same judge.

Python 3.8+, stdlib only.
"""

import argparse
import concurrent.futures
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import judge  # noqa: E402

DEFAULT_OUT_BASE = os.path.join(
    os.path.expanduser("~"), "STELLAR_OUTPUT", "_tmp", "acceptance"
)
PY38 = (
    "/Users/ming/.local/share/uv/python/cpython-3.8.20-macos-aarch64-none/bin/python3.8"
)
ALL_RUNNERS = ("claude", "codex-net", "codex-nonet", "script")
CLAUDE_TOOLS = ["Skill", "Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "Bash"]
CLAUDE_ALLOWED = [
    "Skill",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "WebFetch",
    "Bash(python3:*)",
    "Bash(python:*)",
    "Bash(ls:*)",
    "Bash(mkdir:*)",
    "Bash(head:*)",
    "Bash(tail:*)",
]
# Default output root of the skill; anything new here means the agent escaped
# the CHUHAI_OUTPUT_DIR we gave it.
LEAK_DIRS = [os.path.join(os.path.expanduser("~"), "Documents", "出海拆解报告")]
DOMAIN_RE = re.compile(r"\b((?:[a-z0-9-]+\.)+[a-z]{2,})\b", re.IGNORECASE)


def log(msg):
    sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    sys.stderr.flush()


def case_domain(case):
    if case.get("domain"):
        return case["domain"]
    m = DOMAIN_RE.search(case.get("prompt") or "")
    return m.group(1) if m else ""


def snapshot(dirs):
    snap = {}
    for d in dirs:
        snap[d] = set(os.listdir(d)) if os.path.isdir(d) else set()
    return snap


def leaked(before, after):
    out = []
    for d, names in after.items():
        for n in sorted(names - before.get(d, set())):
            out.append(os.path.join(d, n))
    return out


def tail(text, n=1200):
    text = text or ""
    return text[-n:]


def write_shims(run_dir):
    """`open` shim so deliver.py does not pop Finder windows (best effort)."""
    shim_dir = os.path.join(run_dir, "_shims")
    os.makedirs(shim_dir, exist_ok=True)
    path = os.path.join(shim_dir, "open")
    with open(path, "w") as fh:
        fh.write('#!/bin/sh\necho "[acceptance shim] open $*" >&2\nexit 0\n')
    os.chmod(path, 0o755)
    return shim_dir


def base_env(workdir, shim_dir):
    env = dict(os.environ)
    env.pop("CLAUDE_CONFIG_DIR", None)
    env.pop("CLAUDECODE", None)
    env["CHUHAI_OUTPUT_DIR"] = os.path.join(workdir, "deliverables")
    env["PATH"] = shim_dir + os.pathsep + env.get("PATH", "")
    return env


def run_proc(cmd, cwd, env, timeout, stdout_path, stderr_path):
    start = time.time()
    timed_out = False
    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=env, stdout=out, stderr=err, stdin=subprocess.DEVNULL
        )
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.terminate()
            try:
                code = proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                code = proc.wait()
    return code, round(time.time() - start, 1), timed_out


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def codex_model():
    path = os.path.join(os.path.expanduser("~"), ".codex", "config.toml")
    m = re.search(r'^model\s*=\s*"([^"]+)"', read_text(path), re.M)
    return m.group(1) if m else None


def build_agent_cmd(runner, prompt, args):
    if runner == "claude":
        cmd = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--setting-sources",
            "project",
            "--strict-mcp-config",
            "--permission-mode",
            "dontAsk",
            "--no-session-persistence",
            "--tools",
            ",".join(CLAUDE_TOOLS),
            "--allowedTools",
        ] + CLAUDE_ALLOWED
        if args.claude_model:
            cmd += ["--model", args.claude_model]
        return cmd
    net = "true" if runner == "codex-net" else "false"
    cmd = [
        "codex",
        "exec",
        "--sandbox",
        "workspace-write",
        "--skip-git-repo-check",
        "--json",
        "--ignore-user-config",
        "--ignore-rules",
        "-c",
        'approval_policy="never"',
        "-c",
        "sandbox_workspace_write.network_access=%s" % net,
    ]
    model = args.codex_model or codex_model()
    if model:
        cmd += ["-m", model]
    return cmd + [prompt]


def install_skill(runner, skill_dir, name, workdir):
    rel = ".claude/skills" if runner == "claude" else ".agents/skills"
    dest = os.path.join(workdir, rel, name)
    shutil.copytree(
        skill_dir,
        dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
    )
    return dest


def run_agent(runner, case, ctx):
    args, acc = ctx["args"], ctx["acceptance"]
    name = acc["skill"]
    workdir = os.path.join(ctx["run_dir"], "%s-%s" % (runner, case["id"]))
    os.makedirs(os.path.join(workdir, "deliverables"), exist_ok=True)
    installed = install_skill(runner, ctx["skill_dir"], name, workdir)
    env = base_env(workdir, ctx["shim_dir"])
    cmd = build_agent_cmd(runner, case["prompt"], args)
    with open(os.path.join(workdir, "command.json"), "w", encoding="utf-8") as fh:
        json.dump({"cmd": cmd, "cwd": workdir}, fh, ensure_ascii=False, indent=2)
    if args.dry_run:
        return {
            "runner": runner,
            "case": case["id"],
            "dry_run": True,
            "cmd": cmd,
            "verdict": {"passed": True, "reasons": [], "notes": ["dry run"]},
        }
    transcript = os.path.join(workdir, "transcript.jsonl")
    stderr_path = os.path.join(workdir, "stderr.txt")
    before = snapshot(LEAK_DIRS)
    log("start %s/%s" % (runner, case["id"]))
    code, secs, timed_out = run_proc(
        cmd, workdir, env, args.timeout, transcript, stderr_path
    )
    leaks = leaked(before, snapshot(LEAK_DIRS))
    events = judge.read_jsonl(transcript)
    parse = judge.parse_claude if runner == "claude" else judge.parse_codex
    parsed = parse(events, name)
    verdict = judge.judge_agent_run(
        case,
        parsed,
        os.path.join(workdir, "deliverables"),
        installed,
        acc,
        python=sys.executable,
    )
    if code != 0:
        verdict["notes"].append("exit code %s" % code)
    if timed_out:
        verdict["passed"] = False
        verdict["reasons"].append("timeout after %ss" % args.timeout)
    if leaks:
        verdict["passed"] = False
        verdict["reasons"].append("wrote outside workdir: %s" % ", ".join(leaks))
    with open(os.path.join(workdir, "final_message.md"), "w", encoding="utf-8") as fh:
        fh.write(parsed.get("final_message") or "")
    log(
        "done  %s/%s %s %ss"
        % (runner, case["id"], "PASS" if verdict["passed"] else "FAIL", secs)
    )
    return {
        "runner": runner,
        "case": case["id"],
        "workdir": workdir,
        "exit_code": code,
        "duration_s": secs,
        "timed_out": timed_out,
        "model": parsed.get("model"),
        "triggered": parsed.get("triggered"),
        "trigger_evidence": sorted(set(parsed.get("trigger_evidence") or [])),
        "tool_calls": parsed.get("tool_calls"),
        "cost_usd": parsed.get("cost_usd"),
        "permission_denials": parsed.get("permission_denials"),
        "agent_errors": parsed.get("errors"),
        "stderr_tail": tail(read_text(stderr_path), 800),
        "leaks": leaks,
        "verdict": verdict,
    }


def run_script(case, ctx):
    """No agent: run script_cmds under each interpreter; pass = all exit 0, no traceback."""
    acc, args = ctx["acceptance"], ctx["args"]
    results = []
    for label, py in (("py38", args.py38), ("py3", args.py3)):
        workdir = os.path.join(ctx["run_dir"], "script-%s-%s" % (label, case["id"]))
        out_root = os.path.join(workdir, "deliverables")
        os.makedirs(out_root, exist_ok=True)
        skill_copy = os.path.join(workdir, "skill", acc["skill"])
        if not os.path.isdir(skill_copy):
            shutil.copytree(
                ctx["skill_dir"],
                skill_copy,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
            )
        env = base_env(workdir, ctx["shim_dir"])
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        steps, reasons = [], []
        if not py or not os.path.exists(py) and not shutil.which(py):
            reasons.append("interpreter not found: %s" % py)
        start = time.time()
        for i, tmpl in enumerate(acc.get("script_cmds") or []):
            if reasons:
                break
            line = tmpl.format(
                python=py,
                skill_dir=skill_copy,
                out_root=out_root,
                domain=case_domain(case),
            )
            if line.startswith("python3 "):
                line = py + line[len("python3") :]
            if args.dry_run:
                steps.append({"cmd": line})
                continue
            so, se = (
                os.path.join(workdir, "step%d.%s" % (i, s)) for s in ("out", "err")
            )
            code, secs, to = run_proc(
                ["/bin/sh", "-c", line], workdir, env, args.timeout, so, se
            )
            err = read_text(se)
            steps.append(
                {
                    "cmd": line,
                    "exit_code": code,
                    "duration_s": secs,
                    "stderr_tail": tail(err, 600),
                }
            )
            if to:
                reasons.append("step %d timeout" % i)
            elif code != 0:
                reasons.append("step %d exit %s" % (i, code))
            if "Traceback (most recent call last)" in err:
                reasons.append("step %d python traceback" % i)
        summary = None
        for p in judge.list_files(out_root):
            if os.path.basename(p) == "summary.json":
                try:
                    with open(p, encoding="utf-8") as fh:
                        s = json.load(fh)
                    summary = {
                        "identity": (s.get("identity") or {}).get("status"),
                        "warnings": s.get("warnings"),
                        "failed_endpoints": sorted(
                            k
                            for k, v in (s.get("endpoints") or {}).items()
                            if isinstance(v, dict)
                            and v.get("result") not in ("ok", "not_checked")
                        ),
                    }
                except (OSError, ValueError) as e:
                    summary = {"error": str(e)}
        results.append(
            {
                "runner": "script-%s" % label,
                "case": case["id"],
                "workdir": workdir,
                "duration_s": round(time.time() - start, 1),
                "steps": steps,
                "summary": summary,
                "verdict": {"passed": not reasons, "reasons": reasons, "notes": []},
            }
        )
        log(
            "done  script-%s/%s %s"
            % (label, case["id"], "PASS" if not reasons else "FAIL")
        )
    return results


def schedule(jobs, parallel, fn):
    """Run jobs with at most `parallel` at once and never two of the same case
    (same target store) concurrently, to keep requests polite."""
    results, lock = [], threading.Lock()
    busy, pending = set(), list(jobs)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, parallel)) as ex:
        running = {}
        while pending or running:
            with lock:
                for job in list(pending):
                    if len(running) >= parallel:
                        break
                    if job[1]["id"] in busy:
                        continue
                    pending.remove(job)
                    busy.add(job[1]["id"])
                    running[ex.submit(fn, *job)] = job
            done, _ = concurrent.futures.wait(
                list(running), return_when=concurrent.futures.FIRST_COMPLETED
            )
            for fut in done:
                job = running.pop(fut)
                busy.discard(job[1]["id"])
                try:
                    r = fut.result()
                except Exception as e:  # noqa: BLE001 - record and continue
                    r = {
                        "runner": job[0],
                        "case": job[1]["id"],
                        "verdict": {
                            "passed": False,
                            "reasons": ["harness error: %r" % e],
                            "notes": [],
                        },
                    }
                results.extend(r if isinstance(r, list) else [r])
    return results


def render_md(meta, results):
    lines = [
        "# Acceptance: %s" % meta["skill"],
        "",
        "- run dir: `%s`" % meta["run_dir"],
        "- started: %s, finished: %s" % (meta["started"], meta["finished"]),
        "- skill commit: %s" % meta.get("git_commit"),
        "",
        "| runner | case | result | status | triggered | secs | reasons / notes |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in sorted(results, key=lambda x: (x["runner"], x["case"])):
        v = r.get("verdict") or {}
        why = "; ".join((v.get("reasons") or []) + (v.get("notes") or [])) or "-"
        lines.append(
            "| %s | %s | %s | %s | %s | %s | %s |"
            % (
                r["runner"],
                r["case"],
                "PASS" if v.get("passed") else "FAIL",
                v.get("status") or (r.get("summary") or {}).get("identity") or "-",
                {True: "yes", False: "no"}.get(r.get("triggered"), "-"),
                r.get("duration_s", "-"),
                why.replace("|", "/"),
            )
        )
    return "\n".join(lines) + "\n"


def git_commit(path):
    try:
        out = subprocess.run(
            ["git", "-C", path, "rev-parse", "--short", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        return out.stdout.decode().strip() or None
    except OSError:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Cross-agent acceptance harness for a skill."
    )
    ap.add_argument("--skill-dir", required=True)
    ap.add_argument("--runners", default=",".join(ALL_RUNNERS))
    ap.add_argument("--cases", default="", help="comma list of case ids (default all)")
    ap.add_argument(
        "--out", help="run dir (default ~/STELLAR_OUTPUT/_tmp/acceptance/<skill>-<ts>)"
    )
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=1500, help="seconds per run")
    ap.add_argument("--claude-model", default=None)
    ap.add_argument(
        "--codex-model", default=None, help="default: model in ~/.codex/config.toml"
    )
    ap.add_argument("--py38", default=PY38)
    ap.add_argument("--py3", default="python3")
    ap.add_argument(
        "--dry-run", action="store_true", help="prepare workdirs, print commands"
    )
    args = ap.parse_args(argv)

    skill_dir = os.path.abspath(args.skill_dir)
    with open(os.path.join(skill_dir, "acceptance.json"), encoding="utf-8") as fh:
        acceptance = json.load(fh)
    runners = [r.strip() for r in args.runners.split(",") if r.strip()]
    for r in runners:
        if r not in ALL_RUNNERS:
            ap.error("unknown runner %s" % r)
    wanted = {c.strip() for c in args.cases.split(",") if c.strip()}
    cases = [c for c in acceptance["cases"] if not wanted or c["id"] in wanted]
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = os.path.abspath(
        args.out or os.path.join(DEFAULT_OUT_BASE, "%s-%s" % (acceptance["skill"], ts))
    )
    if run_dir.startswith("/tmp") or run_dir.startswith("/private/tmp"):
        ap.error("refusing to write under /tmp")
    os.makedirs(run_dir, exist_ok=True)
    ctx = {
        "args": args,
        "acceptance": acceptance,
        "skill_dir": skill_dir,
        "run_dir": run_dir,
        "shim_dir": write_shims(run_dir),
    }
    meta = {
        "skill": acceptance["skill"],
        "run_dir": run_dir,
        "started": datetime.datetime.now().isoformat(timespec="seconds"),
        "git_commit": git_commit(skill_dir),
        "runners": runners,
        "cases": [c["id"] for c in cases],
        "claude_allowed_tools": CLAUDE_ALLOWED,
    }
    log("run dir: %s" % run_dir)

    jobs = [(r, c, ctx) for c in cases for r in runners if r != "script"]
    results = schedule(jobs, args.parallel, run_agent)
    if "script" in runners:
        for c in cases:
            results.extend(run_script(c, ctx))

    meta["finished"] = datetime.datetime.now().isoformat(timespec="seconds")
    with open(os.path.join(run_dir, "results.json"), "w", encoding="utf-8") as fh:
        json.dump({"meta": meta, "results": results}, fh, ensure_ascii=False, indent=2)
    md = render_md(meta, results)
    with open(os.path.join(run_dir, "results.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    print(md)
    print("results: %s" % os.path.join(run_dir, "results.json"))
    return 0 if all((r.get("verdict") or {}).get("passed") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
