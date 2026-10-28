"""Portable (no-shell) script steps for the `script` runner in a VM.

The host harness runs acceptance.json "script_cmds" through /bin/sh, which a
clean Windows machine does not have. In a VM each step is an argv list, run
without a shell, so a skill is exercised the way an agent on Windows would
call it (`python <skill dir>\\scripts\\x.py ...`).

Source of steps, per skill:
- acceptance.json "script_steps": [{"argv": [...], "when": "..."}], or
- derived from "script_cmds" when a command has no shell syntax (shlex).

Placeholders, substituted inside the guest: {python} (argv[0] only; expands
to the interpreter the skill's step 0 would pick: python, else py -3),
{skill_dir}, {out_root}, {work_dir} (the case's working dir, parent of
{out_root}), {domain}, {first_out_subdir} (newest folder under
{out_root} at step time). "when": domain | no_domain | lang:<x> | case:<id>.
"""

import re
import shlex

SHELL_SYNTAX = re.compile(r"\$\(|`|\|\||&&|[|;<>]|(^|\s)\[\s|\bif\b|\bthen\b|\bfi\b")
PLACEHOLDERS = (
    "{python}",
    "{work_dir}",
    "{skill_dir}",
    "{out_root}",
    "{domain}",
    "{first_out_subdir}",
)


class StepsError(ValueError):
    """acceptance.json has script_cmds that cannot run without a shell."""


def derive(script_cmds):
    """script_cmds -> [{"argv": [...]}]; raises StepsError on shell syntax."""
    out = []
    for cmd in script_cmds or []:
        # quoted code (python -c "...") may legitimately contain ; or |, but
        # $( ) and backticks still expand inside double quotes
        unquoted = re.sub(r"\"[^\"]*\"|'[^']*'", "", cmd)
        dq_only = re.sub(r"'[^']*'", "", cmd)
        if SHELL_SYNTAX.search(unquoted) or re.search(r"\$\(|`", dq_only):
            raise StepsError("needs a shell, add script_steps: %s" % cmd[:120])
        argv = shlex.split(cmd)
        if argv and argv[0] == "python3":
            argv[0] = "{python}"
        out.append({"argv": argv})
    return out


def steps_for(acceptance):
    if acceptance.get("script_steps"):
        return [dict(s) for s in acceptance["script_steps"]]
    return derive(acceptance.get("script_cmds"))


def applies(step, case, domain):
    cond = (step.get("when") or "").strip()
    if not cond:
        return True
    if cond == "domain":
        return bool(domain)
    if cond == "no_domain":
        return not domain
    if cond.startswith("lang:"):
        return (case.get("lang") or "zh") == cond[5:]
    if cond.startswith("case:"):
        return case.get("id") == cond[5:]
    raise StepsError("unknown when: %s" % cond)


def plan_for_case(acceptance, case, domain):
    """Steps that apply to this case, with {domain} filled in on the host."""
    steps = []
    for s in steps_for(acceptance):
        if not applies(s, case, domain):
            continue
        argv = [a.replace("{domain}", domain) for a in s["argv"]]
        if any(a == "" for a in argv):
            continue
        steps.append({"argv": argv})
    return steps
