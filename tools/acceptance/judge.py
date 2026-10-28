"""Pure judging helpers for the acceptance harness (no subprocess, no network).

Everything here works on files already on disk (transcripts, deliverables) so
it can be unit-tested with synthetic fixtures. Python 3.8+, stdlib only.
"""

import fnmatch
import html
import json
import os
import re
import subprocess
import sys

DEFAULT_RAW_DIRS = ("原始数据", "raw")
APPENDIX_RE = re.compile(r"^\s{0,3}#{1,6}\s*(附录|Appendix)", re.IGNORECASE | re.M)
STATUS_WORDS = ("complete", "partial", "failed")
STATUS_RE = re.compile(
    r"(完成状态|采集状态|状态|status)[^\n]{0,60}?\b(complete|partial|failed)\b",
    re.IGNORECASE,
)
BARE_STATUS_RE = re.compile(r"`(complete|partial|failed)`", re.IGNORECASE)


# ---------------------------------------------------------------------------
# transcripts
# ---------------------------------------------------------------------------


def read_jsonl(path):
    """Read a JSONL file, skipping blank / non-JSON lines (stderr noise)."""
    events = []
    if not path or not os.path.isfile(path):
        return events
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    return events


def _norm_path(text):
    # Windows commands quote paths with doubled backslashes (C:\\Users\\...)
    return re.sub(r"[\\/]+", "/", text)


def _skill_path_hit(text, skill):
    text = _norm_path(text)
    return ("skills/%s/SKILL.md" % skill) in text or ("/%s/SKILL.md" % skill) in text


def _skill_script_hit(text, skill):
    return ("skills/%s/scripts/" % skill) in _norm_path(text)


def parse_claude(events, skill):
    """Summarize a `claude -p --output-format stream-json` transcript."""
    out = {
        "agent": "claude",
        "model": None,
        "triggered": False,
        "trigger_evidence": [],
        "tool_calls": 0,
        "commands": [],
        "permission_denials": [],
        "final_message": "",
        "is_error": False,
        "result_subtype": None,
        "cost_usd": None,
        "errors": [],
    }
    last_text = ""
    for ev in events:
        typ = ev.get("type")
        if typ == "system" and ev.get("subtype") == "init":
            out["model"] = ev.get("model")
        elif typ == "assistant":
            for c in (ev.get("message") or {}).get("content") or []:
                if c.get("type") == "text":
                    last_text = c.get("text") or last_text
                elif c.get("type") == "tool_use":
                    out["tool_calls"] += 1
                    name, inp = c.get("name"), c.get("input") or {}
                    if name == "Skill":
                        sk = str(inp.get("skill") or inp.get("command") or "")
                        if sk == skill or sk.endswith(":" + skill):
                            out["triggered"] = True
                            out["trigger_evidence"].append("Skill(%s)" % sk)
                    blob = json.dumps(inp, ensure_ascii=False)
                    if _skill_path_hit(blob, skill):
                        out["triggered"] = True
                        out["trigger_evidence"].append("%s SKILL.md" % name)
                    if name == "Bash":
                        cmd = str(inp.get("command") or "")
                        out["commands"].append(cmd)
                        if _skill_script_hit(cmd, skill):
                            out["trigger_evidence"].append("ran skill script")
        elif typ == "result":
            out["is_error"] = bool(ev.get("is_error"))
            out["result_subtype"] = ev.get("subtype")
            out["cost_usd"] = ev.get("total_cost_usd")
            if ev.get("result"):
                last_text = ev.get("result")
            for d in ev.get("permission_denials") or []:
                inp = d.get("tool_input") or {}
                out["permission_denials"].append(
                    "%s: %s"
                    % (
                        d.get("tool_name"),
                        str(inp.get("command") or inp.get("file_path") or inp)[:200],
                    )
                )
            if ev.get("is_error"):
                out["errors"].append(str(ev.get("result") or ev.get("subtype"))[:300])
    out["final_message"] = last_text
    return out


def parse_codex(events, skill):
    """Summarize a `codex exec --json` transcript."""
    out = {
        "agent": "codex",
        "model": None,
        "triggered": False,
        "trigger_evidence": [],
        "tool_calls": 0,
        "commands": [],
        "permission_denials": [],
        "final_message": "",
        "is_error": False,
        "result_subtype": None,
        "cost_usd": None,
        "errors": [],
    }
    completed = False
    for ev in events:
        typ = ev.get("type")
        if typ == "turn.completed":
            completed = True
        elif typ in ("turn.failed", "error"):
            out["is_error"] = True
            msg = ev.get("message") or (ev.get("error") or {}).get("message") or ev
            out["errors"].append(str(msg)[:300])
        if typ != "item.completed":
            continue
        item = ev.get("item") or {}
        itype = item.get("type")
        if itype == "agent_message":
            out["final_message"] = item.get("text") or out["final_message"]
        elif itype == "command_execution":
            out["tool_calls"] += 1
            cmd = str(item.get("command") or "")
            out["commands"].append(cmd)
            if _skill_path_hit(cmd, skill):
                out["triggered"] = True
                out["trigger_evidence"].append("read SKILL.md")
            if _skill_script_hit(cmd, skill):
                out["trigger_evidence"].append("ran skill script")
            outp = str(item.get("aggregated_output") or "")
            if item.get("exit_code") not in (0, None) and re.search(
                r"Operation not permitted|Read-only file system|denied by sandbox|sandbox(?:ed)? denied",
                outp,
                re.IGNORECASE,
            ):
                out["permission_denials"].append(
                    "sandbox: %s -> %s" % (cmd[:150], outp.strip()[-150:])
                )
        elif itype in ("file_change", "web_search", "mcp_tool_call", "reasoning"):
            if itype != "reasoning":
                out["tool_calls"] += 1
        elif itype == "error":
            out["errors"].append(str(item.get("message"))[:300])
    if not completed and not out["errors"]:
        out["is_error"] = True
        out["errors"].append("no turn.completed event")
    out["result_subtype"] = "completed" if completed else "incomplete"
    return out


# ---------------------------------------------------------------------------
# deliverables and report text
# ---------------------------------------------------------------------------


def list_files(root):
    found = []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            found.append(os.path.join(dirpath, f))
    return sorted(found)


def is_raw(path, root, raw_dirs=DEFAULT_RAW_DIRS):
    rel = os.path.relpath(path, root)
    parts = rel.replace("\\", "/").split("/")[:-1]
    return any(p in raw_dirs for p in parts)


def match_deliverables(root, patterns, raw_dirs=DEFAULT_RAW_DIRS):
    """Each pattern is "a|b" alternatives on the basename; raw dirs excluded.

    Returns (missing_patterns, {pattern: [matched paths]}).
    """
    files = [] if not os.path.isdir(root) else list_files(root)
    top = [p for p in files if not is_raw(p, root, raw_dirs)]
    matched, missing = {}, []
    for pat in patterns:
        alts = [a.strip() for a in pat.split("|") if a.strip()]
        hits = [
            p for p in top if any(fnmatch.fnmatch(os.path.basename(p), a) for a in alts)
        ]
        matched[pat] = hits
        if not hits:
            missing.append(pat)
    return missing, matched


def html_to_text(doc):
    doc = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", doc)
    doc = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr)>", "\n", doc)
    doc = re.sub(
        r"(?i)<h([1-6])[^>]*>", lambda m: "\n" + "#" * int(m.group(1)) + " ", doc
    )
    doc = re.sub(r"<[^>]+>", "", doc)
    return html.unescape(doc)


def find_report(root, raw_dirs=DEFAULT_RAW_DIRS):
    """Pick the best report source under the deliverables root.

    Preference: report.md (anywhere), then top-level .md, then top-level .html.
    Returns (path or None, text).
    """
    if not os.path.isdir(root):
        return None, ""
    files = list_files(root)
    md = [p for p in files if os.path.basename(p) == "report.md"]
    if not md:
        md = [p for p in files if p.endswith(".md") and not is_raw(p, root, raw_dirs)]
    if md:
        path = max(md, key=os.path.getmtime)
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            return path, fh.read()
    hs = [p for p in files if p.endswith(".html") and not is_raw(p, root, raw_dirs)]
    if hs:
        path = max(hs, key=os.path.getmtime)
        with open(path, encoding="utf-8", errors="replace") as fh:
            return path, html_to_text(fh.read())
    return None, ""


def split_body(text):
    """Return (body, appendix); body is everything before the first appendix heading."""
    m = APPENDIX_RE.search(text)
    if not m:
        return text, ""
    return text[: m.start()], text[m.start() :]


def extract_status(text):
    m = STATUS_RE.search(text or "")
    if m:
        return m.group(2).lower()
    m = BARE_STATUS_RE.search(text or "")
    if m:
        return m.group(1).lower()
    return None


# Default fixed-ending markers per report language (shared/i18n.py).
DEFAULT_FIXED_ENDINGS = {
    "zh": "本报告没有覆盖的",
    "en": "What this report does not cover",
}


def fixed_ending_marker(skill_dir, acceptance, lang=None):
    """Marker text for the skill's fixed ending in the case language.

    Order: acceptance.json "fixed_ending_text" (a string, or a {"zh": ...,
    "en": ...} map); for zh (or no language) the FIXED_ENDING constant of
    scripts/check_report.py; the shared default for the language.
    """
    custom = acceptance.get("fixed_ending_text")
    if isinstance(custom, dict):
        if custom.get(lang or "zh"):
            return custom[lang or "zh"]
    elif custom:
        return custom
    if lang in (None, "zh"):
        path = os.path.join(skill_dir, "scripts", "check_report.py")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                m = re.search(r'^FIXED_ENDING\s*=\s*"([^"]+)"', fh.read(), re.M)
            if m:
                return m.group(1)
    return DEFAULT_FIXED_ENDINGS.get(lang or "zh")


def effective_expect(case, runner=None):
    """The case's "expect" block with per-runner overrides applied.

    expect.status_by_runner {runner: [statuses]} replaces status_in for that
    runner (e.g. accept "failed" as graceful degradation on codex-nonet);
    expect.expect_by_runner {runner: {key: value}} overrides any other key.
    """
    expect = dict(case.get("expect") or {})
    by_status = expect.pop("status_by_runner", None) or {}
    by_runner = expect.pop("expect_by_runner", None) or {}
    if runner and runner in by_status:
        expect["status_in"] = list(by_status[runner])
    if runner and runner in by_runner:
        expect.update(by_runner[runner])
    return expect


def run_check_report(skill_dir, report_path, python=None, timeout=60):
    """Run the skill's own scripts/check_report.py --json; None if absent."""
    script = os.path.join(skill_dir, "scripts", "check_report.py")
    if not report_path or not os.path.isfile(script):
        return None
    if not report_path.endswith(".md"):
        return {"ran": False, "note": "report is not markdown, check skipped"}
    try:
        proc = subprocess.run(
            [python or sys.executable, script, report_path, "--json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ran": False, "note": "check_report failed to run: %s" % e}
    res = {"ran": True, "exit_code": proc.returncode}
    try:
        data = json.loads(proc.stdout.decode("utf-8", "replace"))
        res["errors"] = [
            "%s (line %s): %s" % (e.get("kind"), e.get("line"), e.get("text"))
            for e in data.get("errors", [])
        ]
        res["warning_count"] = len(data.get("warnings", []))
    except ValueError:
        res["errors"] = ["unparseable output: %s" % proc.stdout[:200]]
    return res


# ---------------------------------------------------------------------------
# verdict
# ---------------------------------------------------------------------------


def judge_agent_run(
    case,
    parsed,
    deliverables_root,
    skill_dir,
    acceptance,
    raw_dirs=DEFAULT_RAW_DIRS,
    python=None,
    runner=None,
):
    """Apply a case's "expect" block to one agent run. Returns a verdict dict."""
    expect = effective_expect(case, runner)
    reasons, notes = [], []
    if not parsed.get("triggered"):
        reasons.append("skill not triggered (no SKILL.md read / Skill invocation)")
    missing, matched = match_deliverables(
        deliverables_root, expect.get("deliverables") or [], raw_dirs
    )
    for pat in missing:
        reasons.append("missing deliverable %s" % pat)

    report_path, text = find_report(deliverables_root, raw_dirs)
    source = "deliverable"
    if not text.strip():
        text = parsed.get("final_message") or ""
        source = "final_message"
        report_path = None
    body, _appendix = split_body(text)
    folded = body.casefold()
    hits = [w for w in expect.get("must_not_contain") or [] if w.casefold() in folded]
    if hits:
        reasons.append("technical words before appendix: %s" % ", ".join(hits))
    any_of = expect.get("must_contain_any") or []
    if any_of and not any(w in text for w in any_of):
        reasons.append("none of must_contain_any present: %s" % ", ".join(any_of))
    if expect.get("fixed_ending"):
        marker = fixed_ending_marker(skill_dir, acceptance, case.get("lang"))
        if not marker:
            reasons.append("fixed_ending expected but no marker configured")
        elif marker not in text:
            reasons.append("fixed ending missing (%s)" % marker)
    status = extract_status(text) or extract_status(parsed.get("final_message"))
    allowed = expect.get("status_in") or []
    if allowed and status not in allowed:
        reasons.append("status %s not in %s" % (status, allowed))

    check = run_check_report(skill_dir, report_path, python=python)
    if check and check.get("ran") and check.get("exit_code") not in (0, None):
        reasons.append(
            "check_report.py exit %s: %s"
            % (check.get("exit_code"), "; ".join((check.get("errors") or [])[:3]))
        )
    if parsed.get("permission_denials"):
        notes.append("refused actions: %d" % len(parsed["permission_denials"]))
    if parsed.get("is_error"):
        notes.append("agent error: %s" % "; ".join(parsed.get("errors") or [])[:300])
    return {
        "passed": not reasons,
        "reasons": reasons,
        "notes": notes,
        "status": status,
        "report_source": source,
        "report_path": report_path,
        "deliverables": {k: v for k, v in matched.items()},
        "check_report": check,
    }
