"""Acceptance credentials: lookup, write-back, leak scan (protocol section 5).

Rules enforced here:
- Values stay in this process's memory: never printed, logged, written to a
  temp file or put on a command line. Callers pass them to the guest over
  stdin only.
- Claude Code: subscription OAuth token from `claude setup-token`. Lookup
  order: process env ACCEPTANCE_CLAUDE_OAUTH_TOKEN /
  ACCEPTANCE_CLAUDE_CODE_OAUTH_TOKEN, then the same names read (not sourced)
  from ~/STELLAR.env, then process env CLAUDE_CODE_OAUTH_TOKEN, then the
  Bitwarden item "Claude Code - setup-token acceptance". The value is mapped
  to CLAUDE_CODE_OAUTH_TOKEN only inside the process that launches claude.
- Codex: the dedicated ChatGPT login in ~/.codex-acceptance/auth.json, never
  ~/.codex and never an API key. It may be refreshed during a run, so it is
  copied back atomically (0600) afterwards.
"""

import json
import os
import re
import shutil
import subprocess

HOME = os.path.expanduser("~")
STELLAR_ENV = os.path.join(HOME, "STELLAR.env")
CODEX_HOME = os.path.join(HOME, ".codex-acceptance")
CLAUDE_ENV_NAMES = (
    "ACCEPTANCE_CLAUDE_OAUTH_TOKEN",
    "ACCEPTANCE_CLAUDE_CODE_OAUTH_TOKEN",
)
BW_ITEM = "Claude Code - setup-token acceptance"
BW_SESSION_SH = os.path.join(
    HOME,
    "Documents",
    "STELLAR INFRA",
    "DEPLOY",
    "Bitwarden",
    "scripts",
    "bw-session.sh",
)
TOKEN_RE = re.compile(r"^sk-ant-[A-Za-z0-9_\-]{20,}$")


def read_env_file(path, names):
    """First non-empty value of `names` in a KEY=VALUE file (not executed)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    for name in names:
        m = re.search(r"^\s*(?:export\s+)?%s\s*=\s*(.*)$" % re.escape(name), text, re.M)
        if m:
            val = m.group(1).strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
                val = val[1:-1]
            if val:
                return val
    return None


def _bw_token(run=subprocess.run):
    if not os.path.isfile(BW_SESSION_SH) or not shutil.which("bw"):
        return None
    try:
        s = run(["bash", BW_SESSION_SH], capture_output=True, timeout=120)
        session = s.stdout.decode().strip()
        if s.returncode != 0 or not session:
            return None
        r = run(
            ["bw", "get", "item", BW_ITEM, "--session", session],
            capture_output=True,
            timeout=120,
        )
        if r.returncode != 0:
            return None
        item = json.loads(r.stdout.decode("utf-8"))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    candidates = [((item.get("login") or {}).get("password"))]
    candidates += [f.get("value") for f in item.get("fields") or []]
    candidates += re.findall(r"sk-ant-[A-Za-z0-9_\-]{20,}", item.get("notes") or "")
    for c in candidates:
        if c and TOKEN_RE.match(c.strip()):
            return c.strip()
    return None


def claude_token(environ=None, env_file=STELLAR_ENV, use_bw=True, run=subprocess.run):
    """(token, source) or (None, reason). The source never contains the value."""
    environ = os.environ if environ is None else environ
    for name in CLAUDE_ENV_NAMES:
        if environ.get(name):
            return environ[name], "env:%s" % name
    val = read_env_file(env_file, CLAUDE_ENV_NAMES)
    if val:
        return val, "stellar_env"
    if environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return environ["CLAUDE_CODE_OAUTH_TOKEN"], "env:CLAUDE_CODE_OAUTH_TOKEN"
    if use_bw:
        val = _bw_token(run=run)
        if val:
            return val, "bitwarden"
    return (
        None,
        "no Claude acceptance token (run `claude setup-token`, store as ACCEPTANCE_CLAUDE_OAUTH_TOKEN)",
    )


def codex_auth_path(codex_home=CODEX_HOME):
    return os.path.join(codex_home, "auth.json")


def codex_auth(codex_home=CODEX_HOME):
    """(bytes, None) or (None, reason). Refuses API-key-only logins."""
    path = codex_auth_path(codex_home)
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        data = json.loads(raw.decode("utf-8"))
    except OSError:
        return None, "no %s (run CODEX_HOME=~/.codex-acceptance codex login)" % path
    except ValueError:
        return None, "unparseable %s" % path
    if not data.get("tokens"):
        return None, "%s has no ChatGPT tokens (API keys are not allowed)" % path
    return raw, None


def write_back_codex_auth(raw, codex_home=CODEX_HOME):
    """Atomically replace auth.json with the guest copy if it is valid and differs.

    Returns "updated" | "unchanged" | "rejected:<why>".
    """
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except ValueError:
        return "rejected:not json"
    if not data.get("tokens"):
        return "rejected:no tokens"
    path = codex_auth_path(codex_home)
    try:
        with open(path, "rb") as fh:
            if fh.read() == raw:
                return "unchanged"
    except OSError:
        pass
    tmp = path + ".tmp-%d" % os.getpid()
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(raw)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return "updated"


# Codex writes these itself on its first run in a CODEX_HOME (built-in system
# skills, the curated plugin cache): every fresh install has them.
CODEX_STOCK = {
    "skills": {".system"},
    "plugins": {"cache", ".remote-plugin-install-staging"},
}


def codex_home_clean(codex_home=CODEX_HOME):
    """Entries in CODEX_HOME that could leak user context into a clean run."""
    bad = []
    for name in (
        "AGENTS.md",
        "AGENTS.override.md",
        "memories",
        "plugins",
        "skills",
        "rules",
        "prompts",
    ):
        path = os.path.join(codex_home, name)
        if not os.path.exists(path):
            continue
        stock = CODEX_STOCK.get(name)
        if stock is not None and os.path.isdir(path):
            extra = sorted(set(os.listdir(path)) - stock)
            if not extra:
                continue
            bad.append("%s/%s" % (name, ",".join(extra)))
            continue
        bad.append(name)
    return bad


# ---------------------------------------------------------------------------
# leak scan
# ---------------------------------------------------------------------------

# Credential-specific shapes only. Public web pages (raw evidence of the
# skills) are full of JWT-like strings and storefront "access_token" values, so
# generic JWT / token-field patterns would flag and delete honest evidence.
# Values actually injected this round are caught by exact match instead.
PATTERNS = [
    (
        "anthropic_token",
        re.compile(rb"sk-ant-(?:oat|api|admin)\d{2}-[A-Za-z0-9_\-]{20,}"),
    ),
    ("openai_key", re.compile(rb"\bsk-(?:proj|svcacct|admin)-[A-Za-z0-9_\-]{32,}")),
    (
        "codex_auth_json",
        re.compile(
            rb'"refresh_token"\s*:\s*"[^"]{16,}"[\s\S]{0,4000}"account_id"|'
            rb'"account_id"[\s\S]{0,4000}"refresh_token"\s*:\s*"[^"]{16,}"'
        ),
    ),
]


def secret_values_from_codex_auth(raw):
    """Token strings inside an auth.json, for exact-match scanning."""
    out = []
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, AttributeError):
        return out

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str) and len(o) >= 16:
            out.append(o)

    walk(data.get("tokens") or {})
    if data.get("OPENAI_API_KEY"):
        out.append(data["OPENAI_API_KEY"])
    return out


def scan_bytes(data, exact=()):
    hits = []
    for v in exact:
        if v and v.encode("utf-8") in data:
            hits.append("exact_secret")
            break
    for name, rx in PATTERNS:
        if rx.search(data):
            hits.append(name)
    return hits


def leak_scan(root, exact=(), delete=True):
    """Scan every file under root. Returns [{path, hits}]; deletes hit files."""
    found = []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            try:
                with open(p, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            hits = scan_bytes(data, exact)
            if hits:
                found.append({"path": os.path.relpath(p, root), "hits": hits})
                if delete:
                    os.remove(p)
    return found


def gitleaks_scan(root, report_path, run=subprocess.run):
    """Optional second scanner; None when gitleaks is not installed."""
    if not shutil.which("gitleaks"):
        return None
    r = run(
        [
            "gitleaks",
            "detect",
            "--no-git",
            "--no-banner",
            "--redact",
            "--source",
            root,
            "--report-format",
            "json",
            "--report-path",
            report_path,
        ],
        capture_output=True,
        timeout=600,
    )
    try:
        with open(report_path, encoding="utf-8") as fh:
            findings = json.load(fh) or []
    except (OSError, ValueError):
        findings = []
    return {
        "exit_code": r.returncode,
        "findings": [
            {"file": f.get("File"), "rule": f.get("RuleID")} for f in findings
        ],
    }
