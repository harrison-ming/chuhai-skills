#!/usr/bin/env python3
"""Cross-agent acceptance harness for SKILL.md packages.

Usage:
    python3 tools/acceptance/run.py --skill-dir skills/<name> \
        [--runners claude,codex-net,codex-nonet,script,codex-clean,claude-clean] \
        [--cases normal,l0] \
        [--out <run dir>] [--parallel 3] [--timeout 1500] [--dry-run]

Reads <skill-dir>/acceptance.json:
    {"skill": "<name>",
     "cases": [{"id": "normal", "prompt": "...", "lang": "zh", "needs_network": true,
                "domain": "optional, else first domain-looking token of prompt",
                "expect": {"deliverables": ["*.pdf|*.html", "*.csv"],
                           "status_in": ["complete", "partial"],
                           "must_not_contain": ["products.json"],
                           "must_contain_any": ["[已核实]"],
                           "fixed_ending": true,
                           "status_by_runner": {"codex-nonet": ["failed"]},
                           "expect_by_runner": {"codex-nonet": {"must_contain_any": []}}}}],
     "script_cmds": ["{python} {skill_dir}/scripts/x.py {domain} --out-root {out_root}"],
     "fixed_ending_text": "optional; a string or {zh, en}; default FIXED_ENDING in
                           scripts/check_report.py (zh) or the shared default",
     "network_domains": ["optional extra hosts for the claude sandbox, e.g. rdap.org"]}

Each run gets an isolated workdir
    ~/STELLAR_OUTPUT/_tmp/acceptance/<skill>-<timestamp>/<runner>-<case>/
with CHUHAI_OUTPUT_DIR=<workdir>/deliverables/. Results: results.json and
results.md in the run dir.

Safety: no permission-bypass flag is ever passed. Claude runs (default
--claude-mode sandbox) inside Claude Code's OS sandbox (Bash writes only under
the workdir, network only to the case domain + "network_domains"), with
--permission-mode dontAsk, user settings excluded, no MCP, and a narrow
--allowedTools list; Codex runs with --sandbox workspace-write,
approval_policy=never, user config.toml and exec-policy rules ignored (so the
user's extra writable_roots / MCP servers do not apply).

Clean runners (codex-clean, claude-clean): throwaway HOME per run, dedicated
subscription credentials (see clean_env); pending until those exist.
Windows VM runs (L2): tools/acceptance/vm/run_vm.py.

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

sys.path.insert(0, os.path.join(HERE, "vm"))
import creds  # noqa: E402
import locks  # noqa: E402

DEFAULT_OUT_BASE = os.path.join(
    os.path.expanduser("~"), "STELLAR_OUTPUT", "_tmp", "acceptance"
)
PY38 = (
    "/Users/ming/.local/share/uv/python/cpython-3.8.20-macos-aarch64-none/bin/python3.8"
)
ALL_RUNNERS = (
    "claude",
    "codex-net",
    "codex-nonet",
    "script",
    "codex-clean",
    "claude-clean",
)
# Clean L1 runners (testing protocol 2.1): a throwaway HOME per run so the
# owner's ~/.codex, ~/.claude, ~/.agents (AGENTS.md, memories, skills) are
# invisible; subscription credentials only (never an API key):
#   codex-clean  CODEX_HOME=~/.codex-acceptance (dedicated ChatGPT login),
#                serialized through the codex-acceptance-auth lock
#   claude-clean CLAUDE_CODE_OAUTH_TOKEN from the acceptance token
CLEAN_RUNNERS = ("codex-clean", "claude-clean")


def is_claude(runner):
    return runner in ("claude", "claude-clean")


def judge_runner_key(runner, case):
    """Runner name used for acceptance.json per-runner overrides.

    The clean runners behave like claude / codex-net unless a case names them.
    """
    expect = case.get("expect") or {}
    named = set(expect.get("status_by_runner") or {}) | set(
        expect.get("expect_by_runner") or {}
    )
    if runner in named:
        return runner
    return {"codex-clean": "codex-net", "claude-clean": "claude"}.get(runner, runner)


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
# Sandbox mode: Bash is unrestricted by pattern but runs inside Claude Code's
# OS sandbox (writes only under cwd, network only to allowed domains).
CLAUDE_SANDBOX_ALLOWED = [
    "Skill",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "WebFetch",
    "Bash",
]
# Default delivery roots of the skills (Documents/<root>); anything new there
# means the agent escaped the CHUHAI_OUTPUT_DIR we gave it. leak_dirs() adds
# the roots a skill declares itself.
KNOWN_ROOT_NAMES = ["出海拆解报告", "出海体检报告", "Store checks", "出海测算报告"]
ROOT_NAME_RE = re.compile(r"^ROOT_NAME\s*=\s*(\{[^}]*\}|\"[^\"]+\")", re.M)
LEAK_DIRS = [
    os.path.join(os.path.expanduser("~"), "Documents", name)
    for name in KNOWN_ROOT_NAMES
]


def skill_root_names(skill_dir):
    """ROOT_NAME values declared in scripts/skill_config.py or delivery.py."""
    names = []
    for fname in ("skill_config.py", "delivery.py"):
        path = os.path.join(skill_dir or "", "scripts", fname)
        try:
            with open(path, encoding="utf-8") as fh:
                m = ROOT_NAME_RE.search(fh.read())
        except OSError:
            continue
        if m:
            names.extend(re.findall(r'"([^"]+)"\s*(?:[,}]|$)', m.group(1)))
    return [n for n in names if n and not n.startswith("__")]


def leak_dirs(skill_dir=None):
    """Documents/<root> for every known and skill-declared delivery root."""
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    out = list(LEAK_DIRS)
    for name in skill_root_names(skill_dir):
        path = os.path.join(docs, name)
        if path not in out:
            out.append(path)
    return out


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
    env["CHUHAI_NO_OPEN"] = "1"  # deliver.py must not pop file-manager windows
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


def outside_refs(commands, workdir):
    """Commands that mention the user's home tree outside the workdir (e.g. the
    agent reading the owner's global CLAUDE.md or internal docs)."""
    home = os.path.expanduser("~")
    inside = {os.path.abspath(workdir), os.path.realpath(workdir)}
    hits = []
    for cmd in commands:
        text = cmd
        for w in inside:
            text = text.replace(w, "")
        if home + "/" in text or "~/" in text:
            hits.append(cmd[:200])
    return hits


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


def claude_sandbox_settings(case, acceptance):
    """Claude Code native sandbox: Bash writes confined to cwd (+ temp), network
    limited to the case domain and the skill's declared network_domains."""
    domains = list(acceptance.get("network_domains") or [])
    dom = case_domain(case)
    if dom and case.get("needs_network", True):
        domains += [dom, "*." + dom]
    return {
        "sandbox": {
            "enabled": True,
            "autoAllowBashIfSandboxed": True,
            "allowUnsandboxedCommands": False,
            "network": {"allowedDomains": sorted(set(domains))},
        }
    }


def build_agent_cmd(runner, case, args, acceptance=None):
    prompt = case["prompt"]
    if is_claude(runner):
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
        ]
        if args.claude_mode == "sandbox":
            settings = claude_sandbox_settings(case, acceptance or {})
            allowed = CLAUDE_SANDBOX_ALLOWED
            cmd += ["--settings", json.dumps(settings)]
        else:
            allowed = CLAUDE_ALLOWED
        cmd += ["--allowedTools"] + allowed + list(args.claude_allow or [])
        if args.claude_model:
            cmd += ["--model", args.claude_model]
        return cmd
    net = "true" if runner in ("codex-net", "codex-clean") else "false"
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
    rel = ".claude/skills" if is_claude(runner) else ".agents/skills"
    dest = os.path.join(workdir, rel, name)
    shutil.copytree(
        skill_dir,
        dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
    )
    return dest


def evaluate_agent(
    runner, case, acc, workdir, installed, code, timed_out, leaks, timeout
):
    """Parse the transcript in `workdir` and judge it (also used by --rejudge)."""
    transcript = os.path.join(workdir, "transcript.jsonl")
    events = judge.read_jsonl(transcript)
    parse = judge.parse_claude if is_claude(runner) else judge.parse_codex
    parsed = parse(events, acc["skill"])
    verdict = judge.judge_agent_run(
        case,
        parsed,
        os.path.join(workdir, "deliverables"),
        installed,
        acc,
        python=sys.executable,
        runner=judge_runner_key(runner, case),
    )
    expected = (case.get("expect") or {}).get("deliverables") or []
    if expected and any(
        r.startswith("missing deliverable") for r in verdict["reasons"]
    ):
        ignore = judge.DEFAULT_RAW_DIRS + (".claude", ".agents", "deliverables")
        _missing, elsewhere = judge.match_deliverables(workdir, expected, ignore)
        found = sorted({p for v in elsewhere.values() for p in v})
        if found:
            verdict["notes"].append(
                "deliverables written outside CHUHAI_OUTPUT_DIR: %s"
                % ", ".join(os.path.relpath(p, workdir) for p in found)
            )
    outside = outside_refs(parsed.get("commands") or [], workdir)
    if outside:
        verdict["notes"].append(
            "commands touching paths outside workdir: %d" % len(outside)
        )
    if code != 0:
        verdict["notes"].append("exit code %s" % code)
    if timed_out:
        verdict["passed"] = False
        verdict["reasons"].append("timeout after %ss" % timeout)
    if leaks:
        verdict["passed"] = False
        verdict["reasons"].append("wrote outside workdir: %s" % ", ".join(leaks))
    return {"_parsed": parsed, "verdict": verdict, "outside_refs": outside}


def rejudge(run_dir, acc):
    """Recompute agent verdicts from saved transcripts (after a judge fix)."""
    path = os.path.join(run_dir, "results.json")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    cases = {c["id"]: c for c in acc["cases"]}
    for r in data["results"]:
        if r["runner"].startswith("script") or "workdir" not in r:
            continue
        sub = ".claude/skills" if is_claude(r["runner"]) else ".agents/skills"
        installed = os.path.join(r["workdir"], sub, acc["skill"])
        row = evaluate_agent(
            r["runner"],
            cases[r["case"]],
            acc,
            r["workdir"],
            installed,
            r.get("exit_code"),
            r.get("timed_out"),
            r.get("leaks") or [],
            0,
        )
        parsed = row["_parsed"]
        r.update(
            verdict=row["verdict"],
            outside_refs=row["outside_refs"],
            triggered=parsed.get("triggered"),
            trigger_evidence=sorted(set(parsed.get("trigger_evidence") or [])),
            permission_denials=parsed.get("permission_denials"),
        )
    data["meta"]["rejudged"] = datetime.datetime.now().isoformat(timespec="seconds")
    return data


def clean_env(runner, env, workdir, codex_home=None, token_lookup=None):
    """Environment for a clean runner, or (None, pending reason).

    HOME becomes <workdir>/_home (empty), so the owner's ~/.codex, ~/.claude
    and ~/.agents are never read. Credentials: codex-clean points CODEX_HOME
    at the dedicated ~/.codex-acceptance login (no copy: a copied refresh
    token would be reused twice); claude-clean gets CLAUDE_CODE_OAUTH_TOKEN
    in this child environment only. OPENAI_API_KEY / ANTHROPIC_API_KEY are
    removed so neither client can fall back to an API key.
    """
    home = os.path.join(workdir, "_home")
    os.makedirs(home, exist_ok=True)
    env = dict(env)
    env["HOME"] = home
    for k in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CODEX_HOME",
    ):
        env.pop(k, None)
    if runner == "codex-clean":
        codex_home = codex_home or creds.CODEX_HOME
        raw, why = creds.codex_auth(codex_home)
        if raw is None:
            return None, why
        bad = creds.codex_home_clean(codex_home)
        if bad:
            return None, "%s is not clean: %s" % (codex_home, ", ".join(bad))
        env["CODEX_HOME"] = codex_home
    elif runner == "claude-clean":
        token, why = (token_lookup or creds.claude_token)()
        if not token:
            return None, why
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return env, None


def run_agent(runner, case, ctx):
    args, acc = ctx["args"], ctx["acceptance"]
    name = acc["skill"]
    workdir = os.path.join(ctx["run_dir"], "%s-%s" % (runner, case["id"]))
    os.makedirs(os.path.join(workdir, "deliverables"), exist_ok=True)
    installed = install_skill(runner, ctx["skill_dir"], name, workdir)
    env = base_env(workdir, ctx["shim_dir"])
    if runner in CLEAN_RUNNERS:
        env, pending = clean_env(runner, env, workdir)
        if env is None:
            log("pending %s/%s: %s" % (runner, case["id"], pending))
            return {
                "runner": runner,
                "case": case["id"],
                "pending": True,
                "verdict": {
                    "passed": False,
                    "reasons": ["pending: %s" % pending],
                    "notes": [],
                },
            }
    cmd = build_agent_cmd(runner, case, args, acc)
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
    watched = leak_dirs(ctx["skill_dir"])
    before = snapshot(watched)
    log("start %s/%s" % (runner, case["id"]))
    lock = None
    if runner == "codex-clean":
        # one holder of the dedicated login at a time (host or any VM)
        lock = locks.Lock(
            locks.CODEX_AUTH_LOCK,
            ttl=args.timeout + 600,
            location="host",
            run_id=os.path.basename(ctx["run_dir"]),
            skill=name,
            client=runner,
        ).acquire(wait=args.timeout * 4)
    try:
        code, secs, timed_out = run_proc(
            cmd, workdir, env, args.timeout, transcript, stderr_path
        )
    finally:
        if lock:
            lock.release()
    leaks = leaked(before, snapshot(watched))
    row = evaluate_agent(
        runner, case, acc, workdir, installed, code, timed_out, leaks, args.timeout
    )
    parsed, verdict, outside = row["_parsed"], row["verdict"], row["outside_refs"]
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
        "outside_refs": outside,
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
                "PENDING"
                if r.get("pending")
                else ("PASS" if v.get("passed") else "FAIL"),
                v.get("status") or (r.get("summary") or {}).get("identity") or "-",
                {True: "yes", False: "no"}.get(r.get("triggered"), "-"),
                r.get("duration_s", "-"),
                why.replace("|", "/"),
            )
        )
    return "\n".join(lines) + "\n"


def write_results(meta, results, run_dir):
    with open(os.path.join(run_dir, "results.json"), "w", encoding="utf-8") as fh:
        json.dump({"meta": meta, "results": results}, fh, ensure_ascii=False, indent=2)
    md = render_md(meta, results)
    with open(os.path.join(run_dir, "results.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    print(md)
    print("results: %s" % os.path.join(run_dir, "results.json"))
    return 0 if all((r.get("verdict") or {}).get("passed") for r in results) else 1


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
    ap.add_argument("--runners", default="claude,codex-net,codex-nonet,script")
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
    ap.add_argument(
        "--claude-mode",
        choices=["sandbox", "allowlist"],
        default="sandbox",
        help="sandbox: Bash inside Claude Code's OS sandbox (default); "
        "allowlist: no sandbox, Bash limited to interpreter patterns",
    )
    ap.add_argument(
        "--claude-allow",
        action="append",
        help='extra --allowedTools rule for claude, repeatable (e.g. "Bash(cd:*)")',
    )
    ap.add_argument("--py38", default=PY38)
    ap.add_argument("--py3", default="python3")
    ap.add_argument(
        "--dry-run", action="store_true", help="prepare workdirs, print commands"
    )
    ap.add_argument(
        "--rejudge",
        metavar="RUN_DIR",
        help="re-evaluate saved transcripts in RUN_DIR, no agent is run",
    )
    args = ap.parse_args(argv)

    skill_dir = os.path.abspath(args.skill_dir)
    with open(os.path.join(skill_dir, "acceptance.json"), encoding="utf-8") as fh:
        acceptance = json.load(fh)
    if args.rejudge:
        data = rejudge(os.path.abspath(args.rejudge), acceptance)
        return write_results(data["meta"], data["results"], data["meta"]["run_dir"])
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
        "claude_mode": args.claude_mode,
        "claude_allowed_tools": (
            CLAUDE_SANDBOX_ALLOWED if args.claude_mode == "sandbox" else CLAUDE_ALLOWED
        )
        + list(args.claude_allow or []),
    }
    log("run dir: %s" % run_dir)

    jobs = [(r, c, ctx) for c in cases for r in runners if r != "script"]
    results = schedule(jobs, args.parallel, run_agent)
    if "script" in runners:
        for c in cases:
            results.extend(run_script(c, ctx))

    meta["finished"] = datetime.datetime.now().isoformat(timespec="seconds")
    return write_results(meta, results, run_dir)


if __name__ == "__main__":
    sys.exit(main())
