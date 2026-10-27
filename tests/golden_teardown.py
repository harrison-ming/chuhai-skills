"""Golden-output harness for shopify-store-teardown (offline, deterministic).

For every fixture under tests/fixtures/ it copies ``raw/`` into a fresh run
folder, then runs the real CLIs as subprocesses:

    summarize.py <run>                      (summary.json, products.tsv, ...)
    check_report.py <run> --json            (for each sample report)
    deliver.py <run> --html-only --no-open  (report html, 商品清单.csv, 说明.txt)

Every produced file and every stdout/stderr is normalized (run path, today's
date, summarize timestamp) and hashed. The manifest is compared with
tests/golden/teardown-manifest.json by test_golden_teardown.py, which proves
that refactors (e.g. moving code into scripts/_shared/) change no output.

    python tests/golden_teardown.py --write     regenerate the manifest
    python tests/golden_teardown.py --dump DIR  also keep normalized outputs

Python 3.8+, standard library only.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "skills", "shopify-store-teardown", "scripts")
FIXTURES = os.path.join(HERE, "fixtures")
GOLDEN_DIR = os.path.join(HERE, "golden")
MANIFEST = os.path.join(GOLDEN_DIR, "teardown-manifest.json")
RAW_DIRNAME = "原始数据"  # 原始数据
TS_RE = re.compile(r'("summarized_at_utc":\s*")[^"]*(")')
# summarize.py falls back to "now" when a fixture has no _status.json
NOW_RE = re.compile(r"<TODAY>T\d{2}:\d{2}:\d{2}Z")
ELAPSED_RE = re.compile(r'"elapsed_ms":\s*\d+')

# A deliberately broken report: every hard error and warning kind. Full-width
# punctuation is written as escapes so this file stays half-width.
BAD_REPORT = (
    "# Bad Store 对标拆解报告\n\n"
    "## 价格怎么定的\n\n"
    "- 用 products.json 统计在售 132 款 [事实]\n"
    "- 定价策略的核心是一款一价"
    " [已核实]\n"
    "- 店铺自称全球发货 [已核实]\n"
    "- 多国站点 hreflang 也就是多语言"
    "设置 [已核实]\n"
    "- 后台 tag 和 product_type 字段很乱 [推测]\n"
    "- 全角标点\uff0c这里有\u3002\n\n"
    "## 一页看懂\n\n"
    "- 这是 L1 深度拆解, 证据 E2 [推测]"
    " 依据: 页面.\n"
)


def read_golden(name):
    with open(os.path.join(GOLDEN_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def sample_reports(fixture=None):
    """good + bad for every fixture; the unreachable fixture also gets the
    short failed report the failure path asks for."""
    out = [("good", read_golden("report-good.md")), ("bad", BAD_REPORT)]
    if fixture == "unreachable":
        out.append(("failed", read_golden("report-failed.md")))
    return out


def normalize(data, run_dir, today, port=None):
    text = data.decode("utf-8", errors="surrogateescape")
    if port:
        text = text.replace("127.0.0.1:%d" % port, "127.0.0.1:<PORT>")
    text = ELAPSED_RE.sub('"elapsed_ms": 0', text)
    for path in (run_dir, os.path.realpath(run_dir)):
        text = text.replace(path, "<RUN>")
    text = text.replace(today, "<TODAY>")
    text = TS_RE.sub(r"\1<TS>\2", text)
    text = NOW_RE.sub("<NOW>", text)
    return text.replace("\r\n", "\n").encode("utf-8", errors="surrogateescape")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run_cli(script, args, cwd):
    proc = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, script)] + args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1"),
    )
    return proc.returncode, proc.stdout, proc.stderr


def snapshot(run_dir, today, dump_to=None, port=None, keep_raw=False):
    files = {}
    for base, _dirs, names in os.walk(run_dir):
        for name in names:
            path = os.path.join(base, name)
            rel = os.path.relpath(path, run_dir).replace(os.sep, "/")
            if rel.startswith(RAW_DIRNAME + "/raw/") and not keep_raw:
                continue  # inputs, copied verbatim
            with open(path, "rb") as fh:
                data = normalize(fh.read(), run_dir, today, port)
            files[rel] = digest(data)
            if dump_to:
                dest = os.path.join(dump_to, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as fh:
                    fh.write(data)
    return dict(sorted(files.items()))


def collect_case(tmp, today, dump_dir):
    """collect.py (L1, local fake store) -> summarize -> deliver."""
    sys.path.insert(0, HERE)
    from http.server import HTTPServer

    from test_summarize import FakeStore

    server = HTTPServer(("127.0.0.1", 0), FakeStore)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    root = os.path.join(tmp, "collect-root")
    try:
        rc, out, err = run_cli(
            "collect.py",
            ["http://127.0.0.1:%d/" % port, "--depth", "L1", "--skip-offsite"]
            + ["--delay", "0", "--timeout", "5", "--out-root", root],
            tmp,
        )
    finally:
        server.shutdown()
        server.server_close()
    entry = {"collect": [rc, digest(normalize(out + err, root, today, port))]}
    run_dir = os.path.join(root, os.listdir(root)[0])
    entry["run_dir_name"] = os.path.basename(run_dir).replace(today, "<TODAY>")
    with open(
        os.path.join(run_dir, RAW_DIRNAME, "report.md"), "w", encoding="utf-8"
    ) as fh:
        fh.write(sample_reports()[0][1])
    rc, out, err = run_cli("deliver.py", [run_dir, "--html-only", "--no-open"], tmp)
    entry["deliver"] = [rc, digest(normalize(out + err, run_dir, today, port))]
    rc, out, err = run_cli(
        "deliver.py", [run_dir, "--html-only", "--no-open", "--title", "T"], tmp
    )
    entry["deliver_title"] = [rc, digest(normalize(out + err, run_dir, today, port))]
    sub = os.path.join(dump_dir, "collect") if dump_dir else None
    entry["files"] = snapshot(run_dir, today, sub, port, keep_raw=True)
    return entry


def build_manifest(dump_dir=None):
    today = datetime.date.today().isoformat()
    tmp = tempfile.mkdtemp(prefix="golden-teardown-")
    manifest = {}
    try:
        for fixture in sorted(os.listdir(FIXTURES)):
            src_raw = os.path.join(FIXTURES, fixture, "raw")
            if not os.path.isdir(src_raw):
                continue
            for report_name, report in sample_reports(fixture):
                key = "%s/%s" % (fixture, report_name)
                run_dir = os.path.join(tmp, "%s %s" % (fixture, report_name))
                raw_parent = os.path.join(run_dir, RAW_DIRNAME)
                shutil.copytree(src_raw, os.path.join(raw_parent, "raw"))
                entry = {}
                rc, out, err = run_cli("summarize.py", [run_dir], tmp)
                entry["summarize"] = [rc, digest(normalize(out + err, run_dir, today))]
                with open(
                    os.path.join(raw_parent, "report.md"), "w", encoding="utf-8"
                ) as fh:
                    fh.write(report)
                rc, out, err = run_cli("check_report.py", [run_dir, "--json"], tmp)
                entry["check_json"] = [rc, digest(normalize(out + err, run_dir, today))]
                rc, out, err = run_cli("check_report.py", [run_dir], tmp)
                entry["check_text"] = [rc, digest(normalize(out + err, run_dir, today))]
                rc, out, err = run_cli(
                    "deliver.py", [run_dir, "--html-only", "--no-open"], tmp
                )
                entry["deliver"] = [rc, digest(normalize(out + err, run_dir, today))]
                sub = os.path.join(dump_dir, key) if dump_dir else None
                if sub:
                    os.makedirs(sub, exist_ok=True)
                    with open(os.path.join(sub, "_deliver.txt"), "wb") as fh:
                        fh.write(normalize(out + err, run_dir, today))
                entry["files"] = snapshot(run_dir, today, sub)
                manifest[key] = entry
        manifest["collect/fake-store"] = collect_case(tmp, today, dump_dir)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help="rewrite the manifest")
    ap.add_argument("--dump", help="write normalized outputs to this folder")
    args = ap.parse_args(argv)
    manifest = build_manifest(args.dump)
    text = json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if args.write:
        with open(MANIFEST, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("written: %s (%d cases)" % (MANIFEST, len(manifest)))
        return 0
    with open(MANIFEST, encoding="utf-8") as fh:
        expected = json.load(fh)
    if expected == manifest:
        print("golden: identical (%d cases)" % len(manifest))
        return 0
    for key in sorted(set(expected) | set(manifest)):
        if expected.get(key) != manifest.get(key):
            print("DIFF %s" % key)
    return 1


if __name__ == "__main__":
    sys.exit(main())
