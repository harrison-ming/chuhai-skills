"""Validators every skill folder must pass (used by test_skill_md.py and
test_template.py). Each function returns a list of problems (empty = ok).

Python 3.8+, standard library only.
"""

import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import gen_marketplace  # noqa: E402

TOP_KEYS = {"name", "description", "license", "metadata"}
META_KEYS = {"version", "author", "homepage", "status", "review_by"}
STATUSES = ("complete", "partial", "failed")
HARD_LINE_BUDGET = 500
# Agent-specific tool names must not appear in a portable SKILL.md.
TOOL_NAME_RE = re.compile(
    r"WebFetch|WebSearch|web_fetch|web_search|TodoWrite|mcp__|browser_navigate"
    r"|\bBash tool\b|\bRead tool\b|\bWrite tool\b|computer_use"
)
CASE_KEYS = {"id", "prompt", "lang", "needs_network", "expect"}
EXPECT_KEYS = {
    "deliverables",
    "status_in",
    "must_not_contain",
    "must_contain_any",
    "fixed_ending",
}


def real_skills():
    """skills/<name>/ folders that ship (the template is excluded)."""
    return [
        d
        for d in sorted(glob.glob(os.path.join(ROOT, "skills", "*")))
        if os.path.isdir(d) and not os.path.basename(d).startswith(("_", "."))
    ]


def frontmatter_lines(text):
    parts = text.split("---", 2)
    if len(parts) < 3 or parts[0].strip():
        return None
    return parts[1].splitlines()


def check_skill_md(skill_dir):
    path = os.path.join(skill_dir, "SKILL.md")
    problems = []
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return ["missing SKILL.md"]
    lines = frontmatter_lines(text)
    if lines is None:
        return ["missing frontmatter"]
    keys = set()
    for line in lines:
        m = re.match(r"^(\s*)([A-Za-z_][\w-]*):\s?(.*)$", line)
        if not m:
            continue
        if not m.group(1):
            keys.add(m.group(2))
        value = m.group(3)
        if not value or value[0] in "\"'>|[{":
            continue
        # strict YAML parsers reject these inside plain scalars
        if ": " in value or " #" in value:
            problems.append("unsafe plain scalar: %s" % line.strip())
    if keys != TOP_KEYS:
        problems.append(
            "top-level keys must be %s, got %s" % (sorted(TOP_KEYS), sorted(keys))
        )
    if not any(re.match(r"^description:\s*>-\s*$", ln) for ln in lines):
        problems.append("description must be a '>-' block scalar")
    fm = gen_marketplace.parse_frontmatter(text)
    meta = fm.get("metadata") or {}
    if set(meta) != META_KEYS:
        problems.append(
            "metadata keys must be %s, got %s" % (sorted(META_KEYS), sorted(meta))
        )
    if meta.get("author") != "StellarByte":
        problems.append("metadata.author must be StellarByte")
    if not re.match(r"^\d+\.\d+\.\d+$", meta.get("version", "")):
        problems.append("metadata.version must be X.Y.Z")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", meta.get("review_by", "")):
        problems.append("metadata.review_by must be YYYY-MM-DD")
    if fm.get("name") != os.path.basename(skill_dir):
        problems.append("name must equal the folder name")
    body = text.split("---", 2)[2]
    m = TOOL_NAME_RE.search(body)
    if m:
        problems.append("agent-specific tool name in body: %s" % m.group(0))
    for needle in ("py -3", "9009"):
        if needle not in body:
            problems.append("Step 0 Python detection must mention %r" % needle)
    if len(text.splitlines()) > HARD_LINE_BUDGET:
        problems.append("SKILL.md over %d lines" % HARD_LINE_BUDGET)
    return problems


def check_acceptance(skill_dir):
    path = os.path.join(skill_dir, "acceptance.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        return ["acceptance.json unreadable: %s" % e]
    problems = []
    if set(data) != {"skill", "cases"}:
        problems.append("top-level keys must be skill, cases")
    if data.get("skill") != os.path.basename(skill_dir):
        problems.append("skill must equal the folder name")
    cases = data.get("cases") or []
    if not cases:
        problems.append("no cases")
    ids = [c.get("id") for c in cases]
    if len(set(ids)) != len(ids):
        problems.append("duplicate case ids")
    for c in cases:
        cid = c.get("id")
        if set(c) != CASE_KEYS:
            problems.append("%s: keys must be %s" % (cid, sorted(CASE_KEYS)))
            continue
        if c["lang"] not in ("zh", "en"):
            problems.append("%s: lang must be zh or en" % cid)
        if not isinstance(c["needs_network"], bool) or not c["prompt"]:
            problems.append("%s: needs_network bool and prompt required" % cid)
        exp = c["expect"]
        if set(exp) != EXPECT_KEYS:
            problems.append("%s: expect keys must be %s" % (cid, sorted(EXPECT_KEYS)))
            continue
        if not set(exp["status_in"]) <= set(STATUSES) or not exp["status_in"]:
            problems.append("%s: status_in must be a subset of %s" % (cid, STATUSES))
        for key in ("deliverables", "must_not_contain", "must_contain_any"):
            if not all(isinstance(x, str) for x in exp[key]):
                problems.append("%s: %s must be a list of strings" % (cid, key))
        if not isinstance(exp["fixed_ending"], bool):
            problems.append("%s: fixed_ending must be bool" % cid)
    return problems


FULLWIDTH_RE = re.compile("[，。；：！？（）【】、]")


def check_halfwidth(skill_dir):
    """No full-width punctuation in any text file of the skill."""
    problems = []
    for base, dirs, names in os.walk(skill_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in names:
            if not name.endswith((".md", ".py", ".json")):
                continue
            path = os.path.join(base, name)
            with open(path, encoding="utf-8") as fh:
                for i, line in enumerate(fh, 1):
                    if FULLWIDTH_RE.search(line):
                        problems.append("%s:%d" % (os.path.relpath(path, skill_dir), i))
    return problems
