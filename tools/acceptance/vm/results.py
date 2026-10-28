"""Per-case result.json (testing protocol section 6.1) and the run summary.

Verdicts: pass | fail (the skill's problem) | inconclusive (framework, bench,
credentials, quota). Only files collected back to the host count: a missing
case-meta.json or a missing step result is inconclusive, never pass.
"""

import datetime
import json
import os
import re

REQUIRED = (
    "skill",
    "version",
    "package_sha256",
    "layer",
    "bench_id",
    "baseline_id",
    "client",
    "client_version",
    "os",
    "arch",
    "case_id",
    "triggered",
    "status",
    "verdict",
    "reasons",
    "deliverables",
    "duration_s",
    "started_at",
    "verified_at",
)
VERDICTS = ("pass", "fail", "inconclusive")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
RATE_LIMIT_RE = re.compile(
    r"rate.?limit|usage limit|too many requests|\b429\b|quota exceeded|"
    r"usage_limit_reached|You've hit your",
    re.I,
)


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate(res):
    """List of schema problems (empty = valid)."""
    errs = ["missing %s" % k for k in REQUIRED if k not in res]
    if res.get("verdict") not in VERDICTS:
        errs.append("bad verdict %r" % res.get("verdict"))
    for k in ("started_at", "verified_at"):
        if k in res and not UTC_RE.match(str(res[k] or "")):
            errs.append("%s not UTC Z: %r" % (k, res.get(k)))
    sha = res.get("package_sha256") or ""
    if not re.match(r"^[0-9a-f]{64}$", sha):
        errs.append("package_sha256 not 64 hex")
    if not isinstance(res.get("reasons"), list):
        errs.append("reasons not a list")
    if not isinstance(res.get("deliverables"), list):
        errs.append("deliverables not a list")
    return errs


def deliverable_list(root, raw_dirs=("原始数据", "raw", "data")):
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            rel = os.path.relpath(os.path.join(dirpath, f), root).replace(os.sep, "/")
            parts = rel.split("/")[:-1]
            if not any(p in raw_dirs for p in parts):
                out.append(rel)
    return sorted(out)


def script_verdict(meta, step_errs):
    """meta: guest case-meta.json; step_errs: {i: stderr text}. -> (verdict, reasons)."""
    if meta is None:
        return "inconclusive", ["no case-meta.json collected"]
    if not meta.get("install_ok"):
        return "fail" if meta.get("error") and "SKILL.md" in meta[
            "error"
        ] else "inconclusive", [
            "install failed: %s" % (meta.get("error") or "").splitlines()[0][:200]
        ]
    reasons = []
    if meta.get("error"):
        first = (meta["error"] or "").splitlines()[0][:300]
        if "no Python" in first:
            return "inconclusive", ["bench: %s" % first]
        reasons.append("runner error: %s" % first)
    for i, st in enumerate(meta.get("steps") or []):
        if st.get("timed_out"):
            reasons.append("step %d timeout" % i)
        elif st.get("exit_code") != 0:
            reasons.append("step %d exit %s" % (i, st.get("exit_code")))
        if "Traceback (most recent call last)" in (step_errs.get(i) or ""):
            reasons.append("step %d python traceback" % i)
    if meta.get("upload_failed"):
        return "inconclusive", reasons + [
            "%d evidence uploads failed" % meta["upload_failed"]
        ]
    return ("fail" if reasons else "pass"), reasons


def agent_verdict(meta, judged, transcript_text, stderr_text):
    """Map judge.judge_agent_run output + guest meta to (verdict, reasons)."""
    if meta is None:
        return "inconclusive", ["no case-meta.json collected"]
    if not meta.get("install_ok") or (meta.get("error") and not meta.get("steps")):
        return "inconclusive", [
            "runner: %s" % (meta.get("error") or "").splitlines()[0][:200]
        ]
    st = (meta.get("steps") or [{}])[0]
    blob = (stderr_text or "")[-20000:] + "\n" + (transcript_text or "")[-20000:]
    if RATE_LIMIT_RE.search(blob) and not judged.get("passed"):
        return "inconclusive", ["rate_limited"]
    if not transcript_text.strip():
        return "inconclusive", ["empty transcript (exit %s)" % st.get("exit_code")]
    reasons = list(judged.get("reasons") or [])
    if st.get("timed_out") and not any("timeout" in r for r in reasons):
        reasons.append("timeout")
    return ("pass" if not reasons else "fail"), reasons


def write_result(path, res):
    errs = validate(res)
    if errs:
        raise ValueError("result.json invalid: %s" % "; ".join(errs))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=2)


def render_md(meta, rows, pending):
    lines = [
        "# L2 acceptance: %s %s" % (meta["skill"], meta.get("version") or ""),
        "",
        "- bench: `%s` (baseline `%s`), os %s %s"
        % (meta["bench_id"], meta["baseline_id"], meta.get("os"), meta.get("arch")),
        "- package: `%s` sha256 `%s`" % (meta["zip"], meta["package_sha256"]),
        "- run dir: `%s`" % meta["run_dir"],
        "- started %s, finished %s" % (meta["started_at"], meta.get("finished_at")),
        "",
        "| client | case | verdict | status | triggered | secs | reasons |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda x: (x["client"], x["case_id"])):
        lines.append(
            "| %s | %s | %s | %s | %s | %s | %s |"
            % (
                r["client"],
                r["case_id"],
                r["verdict"],
                r.get("status") or "-",
                {True: "yes", False: "no"}.get(r.get("triggered"), "-"),
                r.get("duration_s"),
                "; ".join(r.get("reasons") or []).replace("|", "/") or "-",
            )
        )
    for p in pending:
        lines.append(
            "| %s | * | pending | - | - | - | %s |" % (p["client"], p["reason"])
        )
    return "\n".join(lines) + "\n"
