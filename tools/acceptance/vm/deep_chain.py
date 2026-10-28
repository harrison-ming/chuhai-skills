#!/usr/bin/env python3
"""Deep script round for one skill package: the whole delivery chain.

Runs inside the test VM (as a `script` step of run_vm.py, see
tools/acceptance/deep/) or locally under a simulated legacy locale:

    python deep_chain.py --skill <name> --skill-dir <unzipped skill dir> \
        --fixtures <dir> --out-root <dir> --work <dir> [--pro]

Every command is the one SKILL.md tells the agent to run, executed with this
interpreter (sys.executable) in a child process whose stdout / stderr are
pipes, i.e. exactly how an agent sees them. The agent's writing step is
simulated: report.md = the script's draft plus a line of characters the
legacy Chinese code page cannot encode (£ ¥ ✓ 한), so console printing,
check_report and PDF rendering all have to cope with them.

Also covered: the default delivery root (CHUHAI_OUTPUT_DIR removed for some
steps; whatever lands in Documents is copied back under <work>/default-root),
Chinese + spaces in every path, an Excel-style GBK CSV for profit-model, and
the Pro extra commands that need neither a browser nor long network loops.

Writes <work>/deep-result.json and <work>/deep-NN.out / .err (raw bytes).
Exit code 0 only when every step met its expectation. A step fails on a
Python traceback in stderr, an unexpected exit code, or stdout/stderr that
is neither valid UTF-8 nor valid in the console code page.
Python 3.8+, standard library only.
"""

import argparse
import datetime
import glob
import json
import locale
import os
import shutil
import subprocess
import sys
import time

STRESS_ZH = "压力测试行: 价格 £12.50 / ¥80 / €9, 已核对 ✓, 韩文 한국어, 破折号 —."
STRESS_EN = "Stress line: price £12.50 / ¥80 / €9, checked ✓, Korean 한국어, dash —."
NETWORK_STORE = "allbirds.com"


class Chain(object):
    def __init__(self, args):
        self.args = args
        self.skill_dir = os.path.abspath(args.skill_dir)
        self.scripts = os.path.join(self.skill_dir, "scripts")
        self.fixtures = os.path.abspath(args.fixtures)
        self.out_root = os.path.abspath(args.out_root)
        self.work = os.path.abspath(args.work)
        self.steps = []
        self.docs = documents_dir()
        self.docs_before = set(listdir(self.docs))
        os.makedirs(self.work, exist_ok=True)

    # ------------------------------------------------------------ running
    def run(self, name, script, argv, default_root=False, ok=(0,), timeout=900):
        n = len(self.steps)
        env = dict(os.environ)
        env.setdefault("CHUHAI_OUTPUT_DIR", self.out_root)
        env["CHUHAI_NO_OPEN"] = "1"
        if default_root:
            env.pop("CHUHAI_OUTPUT_DIR", None)
        cmd = [sys.executable, os.path.join(self.scripts, script)] + list(argv)
        t0 = time.time()
        try:
            p = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                env=env,
                cwd=self.work,
                timeout=timeout,
            )
            rc, out, err = p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired as e:
            rc, out, err = "timeout", e.stdout or b"", e.stderr or b""
        for ext, data in (("out", out), ("err", err)):
            with open(os.path.join(self.work, "deep-%02d.%s" % (n, ext)), "wb") as fh:
                fh.write(data)
        problems = []
        if b"Traceback (most recent call last)" in err:
            problems.append("traceback")
        if rc not in ok:
            problems.append("exit %s (want %s)" % (rc, "/".join(map(str, ok))))
        for label, data in (("stdout", out), ("stderr", err)):
            enc = decodable(data)
            if enc is None:
                problems.append(
                    "%s not decodable as utf-8 or %s" % (label, console_cp())
                )
        rec = {
            "n": n,
            "name": name,
            "argv": [os.path.basename(cmd[0])] + cmd[1:],
            "default_root": default_root,
            "exit_code": rc,
            "seconds": round(time.time() - t0, 1),
            "stdout_encoding": decodable(out),
            "stderr_tail": text(err)[-1500:],
            "problems": problems,
            "ok": not problems,
        }
        self.steps.append(rec)
        print(
            "[%02d] %-34s %s %s" % (n, name, "ok  " if rec["ok"] else "FAIL", problems),
            flush=True,
        )
        return text(out)

    def note(self, name, ok, detail):
        """A check made by the driver itself (files, BOM, PDF)."""
        rec = {"n": len(self.steps), "name": name, "ok": bool(ok), "detail": detail}
        rec["problems"] = [] if ok else [detail]
        self.steps.append(rec)
        print(
            "[%02d] %-34s %s %s" % (rec["n"], name, "ok  " if ok else "FAIL", detail),
            flush=True,
        )

    # ------------------------------------------------------------ helpers
    def newest(self, root, pattern="*"):
        dirs = [d for d in glob.glob(os.path.join(root, pattern)) if os.path.isdir(d)]
        return max(dirs, key=os.path.getmtime) if dirs else None

    def write_report(self, run_dir, draft_names, lang="zh"):
        raw = raw_dir(run_dir)
        src = None
        for name in draft_names:
            p = os.path.join(raw, name) if not os.path.isabs(name) else name
            if os.path.isfile(p):
                src = p
                break
        if not src:
            self.note("simulate agent report", False, "no draft in %s" % raw)
            return None
        with open(src, encoding="utf-8") as fh:
            body = fh.read()
        body = add_stress(body, STRESS_EN if lang == "en" else STRESS_ZH)
        dst = os.path.join(raw, "report.md")
        with open(dst, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        self.note("simulate agent report", True, os.path.basename(src))
        return dst

    def check_outputs(self, run_dir, label, want_csv=True):
        if not run_dir or not os.path.isdir(run_dir):
            self.note("outputs " + label, False, "run folder missing")
            return
        files = sorted(os.listdir(run_dir))
        pdf = [f for f in files if f.lower().endswith(".pdf")]
        html = [f for f in files if f.lower().endswith(".html")]
        csvs = [f for f in files if f.lower().endswith(".csv")]
        bad_bom = [f for f in csvs if not starts_with_bom(os.path.join(run_dir, f))]
        detail = "pdf=%s html=%s csv=%d" % (pdf[:2], html[:2], len(csvs))
        ok = bool(pdf) and not bad_bom and (csvs or not want_csv)
        if bad_bom:
            detail += " csv-without-BOM=%s" % bad_bom
        if not pdf:
            detail += " (no PDF)"
        self.note("outputs " + label, ok, detail)

    def finish(self):
        copied = []
        dst_root = os.path.join(self.work, "default-root")
        for name in sorted(set(listdir(self.docs)) - self.docs_before):
            src = os.path.join(self.docs, name)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(dst_root, name))
                copied.append(name)
        res = {
            "skill": self.args.skill,
            "pro": self.args.pro,
            "python": sys.version.split()[0],
            "executable": sys.executable,
            "utf8_mode": sys.flags.utf8_mode,
            "preferred_encoding": locale.getpreferredencoding(False),
            "stdout_encoding": sys.stdout.encoding,
            "platform": sys.platform,
            "documents": self.docs,
            "default_root_new": copied,
            "finished_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "steps": self.steps,
            "failed": [s["name"] for s in self.steps if not s["ok"]],
        }
        with open(
            os.path.join(self.work, "deep-result.json"), "w", encoding="utf-8"
        ) as fh:
            json.dump(res, fh, ensure_ascii=False, indent=1)
        print("failed steps: %s" % (res["failed"] or "none"), flush=True)
        return 0 if not res["failed"] else 1


# ---------------------------------------------------------------- skills


def chain_profit_model(c):
    fx = os.path.join(c.fixtures, "profit-model")
    out = c.run(
        "init (default root)",
        "profit.py",
        ["init", "--product", "宠物梳 二号"],
        default_root=True,
    )
    docs_root = os.path.join(c.docs, "出海利润测算")
    run = c.newest(docs_root, "宠物梳 二号*")
    if run:
        shutil.copy(
            os.path.join(fx, "healthy.csv"), os.path.join(raw_dir(run), "inputs.csv")
        )
        c.run(
            "run (default root)",
            "profit.py",
            ["run", run, "--no-fx-fetch"],
            default_root=True,
        )
        c.check_outputs(run, "profit default root")
    else:
        c.note("init made a run folder", False, out[-300:])
    gbk = os.path.join(c.work, "Excel 另存 输入表.csv")
    with open(os.path.join(fx, "healthy.csv"), encoding="utf-8") as fh:
        rows = fh.read()
    with open(gbk, "w", encoding="gbk", newline="\r\n") as fh:
        fh.write(rows)
    c.run(
        "run GBK csv (Excel save)",
        "profit.py",
        ["run", gbk, "--out-root", c.out_root, "--no-fx-fetch"],
    )
    run2 = c.newest(c.out_root)
    c.check_outputs(run2, "profit gbk csv")
    c.run("check_report", "check_report.py", [run2])
    c.run("deliver again", "deliver.py", [run2, "--no-open"])
    c.run(
        "run english",
        "profit.py",
        [
            "run",
            os.path.join(fx, "english.csv"),
            "--lang",
            "en",
            "--out-root",
            c.out_root,
            "--no-fx-fetch",
        ],
    )
    c.check_outputs(c.newest(c.out_root), "profit english")
    if c.args.pro:
        c.run(
            "pro multi-SKU + tornado",
            "profit.py",
            [
                "run",
                os.path.join(fx, "multi_sku.csv"),
                "--out-root",
                c.out_root,
                "--no-fx-fetch",
            ],
        )
        run3 = c.newest(c.out_root)
        c.check_outputs(run3, "profit pro")
        names = os.listdir(run3) if run3 else []
        c.note(
            "pro tables",
            any("商品排名" in n for n in names) and any("价格网格" in n for n in names),
            str(names),
        )
        svg = glob.glob(os.path.join(raw_dir(run3), "*.svg")) if run3 else []
        c.note("pro tornado svg", bool(svg), str([os.path.basename(s) for s in svg]))


def chain_payments(c):
    c.run(
        "collect offline",
        "collect.py",
        ["--offline", "测试 店铺", "--out-root", c.out_root],
    )
    run = c.newest(c.out_root)
    qa = [
        run,
        "--set",
        "entity_country=CN",
        "--set",
        "shopify_plan=basic",
        "--set",
        "aov=45",
        "--set",
        "monthly_orders=300",
        "--set",
        "withdrawals_per_month=1",
        "--set",
        "payout_account=cn_bank",
        "--gateway",
        "paypal:40",
        "--gateway",
        "card_other:60:3.5+0.30",
    ]
    if c.args.pro:
        qa += [
            "--scenario",
            "全走 PayPal=paypal:100",
            "--scenario",
            "Stripe 方案=card_other:100:2.9+0.30;plan=grow",
        ]
    c.run("questionnaire", "questionnaire.py", qa)
    c.run("compute", "compute.py", [run])
    if c.args.pro:
        c.run("pro fees_compare", "fees_compare.py", [run])
    c.write_report(run, ["report-draft.md"])
    c.run("check_report", "check_report.py", [run], ok=(0, 1))
    c.run("deliver", "deliver.py", [run, "--no-open"])
    c.check_outputs(run, "payments offline")
    c.run(
        "collect network (default root)",
        "collect.py",
        [NETWORK_STORE],
        default_root=True,
        ok=(0, 1, 2),
    )
    run2 = c.newest(c.docs, os.path.join("*", NETWORK_STORE + "*"))
    if run2:
        c.run("compute network", "compute.py", [run2])
        c.write_report(run2, ["report-draft.md"])
        c.run("check_report network", "check_report.py", [run2], ok=(0, 1))
        c.run("deliver network", "deliver.py", [run2, "--no-open"])
        c.check_outputs(run2, "payments network")
    else:
        c.note("network run folder in Documents", False, "missing")
    if c.args.pro:
        c.run(
            "collect offline 2",
            "collect.py",
            ["--offline", "测试 店铺", "--out-root", c.out_root],
        )
        run3 = c.newest(c.out_root)
        c.run("questionnaire 2", "questionnaire.py", [run3] + qa[1:])
        c.run("compute 2", "compute.py", [run3])
        c.run("pro recheck", "recheck.py", [run3, run])
        c.run(
            "pro multi_compare",
            "multi_compare.py",
            [run, run3, "--out-root", c.out_root],
        )


def chain_radar(c):
    c.run("demo", "radar.py", ["demo", "--out-root", c.out_root])
    radar_root = os.path.join(c.out_root, "雷达")
    store = c.newest(radar_root)
    report = None
    if store:
        report = c.newest(store, "*")
    c.note("demo report folder", bool(report), str(report))
    if report:
        c.write_report(report, ["report.md", "diff.md"])
        c.run("check_report demo", "check_report.py", [report], ok=(0, 1))
        c.run("deliver demo", "deliver.py", [report, "--no-open"])
        c.check_outputs(report, "radar demo", want_csv=False)
    if c.args.pro and store:
        domain = os.path.basename(store)
        c.run("pro timeline", "timeline.py", [domain, "--out-root", c.out_root])
        c.run("pro alerts", "alerts.py", [report], ok=(0, 2))
        c.run(
            "pro multi report (offline)",
            "multi.py",
            ["report", domain, "--out-root", c.out_root],
            ok=(0, 1),
        )
    c.run(
        "run network (default root)",
        "radar.py",
        ["run", NETWORK_STORE],
        default_root=True,
        ok=(0, 1, 2),
    )


def chain_launch(c):
    paste = os.path.join(c.fixtures, "F3-paste")
    c.run(
        "paste_ingest",
        "paste_ingest.py",
        [paste, "--name", "测试 店铺", "--markets", "EU,AU", "--out-root", c.out_root],
    )
    run = c.newest(c.out_root)
    c.run("export_fixlist", "export_fixlist.py", [run])
    c.write_report(run, ["report.draft.md"])
    c.run("check_report", "check_report.py", [run], ok=(0, 1))
    c.run("deliver", "deliver.py", [run, "--no-open"])
    c.check_outputs(run, "launch paste")
    c.run(
        "audit_collect network (default root)",
        "audit_collect.py",
        [NETWORK_STORE, "--markets", "US,UK"],
        default_root=True,
        ok=(0, 1, 2),
    )
    run2 = c.newest(c.docs, os.path.join("*", NETWORK_STORE + "*"))
    if run2:
        c.write_report(run2, ["report.draft.md"])
        c.run("check_report network", "check_report.py", [run2], ok=(0, 1))
        c.run("deliver network", "deliver.py", [run2, "--no-open"])
        c.check_outputs(run2, "launch network")
    else:
        c.note("network run folder in Documents", False, "missing")
    if c.args.pro:
        c.run("pro scorecard", "scorecard.py", [run])
        c.run(
            "paste_ingest 2",
            "paste_ingest.py",
            [
                paste,
                "--name",
                "测试 店铺",
                "--markets",
                "EU,AU",
                "--out-root",
                c.out_root,
            ],
        )
        run3 = c.newest(c.out_root)
        c.run("pro recheck", "recheck.py", [run3, "--previous", run])
        if run2:
            c.run("pro benchmark", "benchmark.py", [run, run2])


def chain_tracking(c):
    runs = []
    for name in ("well_tracked", "duplicated_broken"):
        run = os.path.join(
            c.out_root, "%s 店 追踪体检 2026-10-27" % name.replace("_", " ")
        )
        shutil.copytree(
            os.path.join(c.fixtures, "tracking-audit", name, "raw"),
            os.path.join(run, "原始数据", "raw"),
        )
        c.run("audit " + name, "audit.py", [run])
        runs.append(run)
    run = runs[0]
    if c.args.pro:
        c.run(
            "pro reconcile",
            "reconcile.py",
            [run, "--dir", os.path.join(c.fixtures, "tracking", "exports")],
        )
        c.run("audit after reconcile", "audit.py", [run])
        c.run(
            "pro compare_tracking", "compare_tracking.py", runs + ["--out", c.out_root]
        )
    c.write_report(run, ["report.draft.md"])
    c.run("check_report", "check_report.py", [run], ok=(0, 1))
    c.run("deliver", "deliver.py", [run, "--no-open"])
    c.check_outputs(run, "tracking fixture")
    c.run(
        "collect network (default root)",
        "collect.py",
        [NETWORK_STORE],
        default_root=True,
        ok=(0, 1, 2),
    )
    run2 = c.newest(c.docs, os.path.join("*", NETWORK_STORE + "*"))
    if run2:
        c.write_report(run2, ["report.draft.md"])
        c.run("check_report network", "check_report.py", [run2], ok=(0, 1))
        c.run("deliver network", "deliver.py", [run2, "--no-open"])
        c.check_outputs(run2, "tracking network")
    else:
        c.note("network run folder in Documents", False, "missing")


TEARDOWN_REPORT = """# {domain} 对标拆解

> 完成状态: partial | 数据截至: 2026-10-27 | 深度: L1

## 一页看懂

- 这是验收脚本模拟代理写的报告, 只用来测交付链路 [推测]
- {stress}

## 附录

- 原始数据在 原始数据/ 文件夹.
"""


def chain_teardown(c):
    c.run(
        "collect L1 (default root)",
        "collect.py",
        [NETWORK_STORE, "--depth", "L1"],
        default_root=True,
        ok=(0, 1, 2),
    )
    run = c.newest(c.docs, os.path.join("*", NETWORK_STORE + "*"))
    if not run:
        c.note("network run folder in Documents", False, "missing")
    else:
        c.run("summarize", "summarize.py", [run])
        with open(os.path.join(raw_dir(run), "report.md"), "w", encoding="utf-8") as fh:
            fh.write(TEARDOWN_REPORT.format(domain=NETWORK_STORE, stress=STRESS_ZH))
        c.run("check_report", "check_report.py", [run], ok=(0, 1))
        c.run("deliver", "deliver.py", [run, "--no-open"])
        c.check_outputs(run, "teardown network")
    if c.args.pro:
        fx = [
            os.path.join(c.fixtures, "teardown", d)
            for d in (
                "alpha-outfitters.example",
                "beta-home.example",
                "gamma-minimal.example",
            )
        ]
        runs = []
        for d in fx:
            date = sorted(os.listdir(d))[-1]
            dst = os.path.join(
                c.out_root, "%s 对标拆解 %s" % (os.path.basename(d), date), "原始数据"
            )
            shutil.copytree(os.path.join(d, date), dst)
            runs.append(os.path.dirname(dst))
        c.run("pro compare", "compare.py", runs + ["--out-root", c.out_root])
        cmp_dir = c.newest(
            os.path.join(c.out_root, "出海拆解报告"), "多店对比*"
        ) or c.newest(c.out_root, "多店对比*")
        c.note("compare folder", bool(cmp_dir), str(cmp_dir))
        if cmp_dir:
            c.run("pro export_csv", "export_csv.py", runs + ["--out-dir", cmp_dir])
            c.write_report(
                cmp_dir, ["compare.md", "report-draft.md", "report.draft.md"]
            )
            c.run("check_report compare", "check_report.py", [cmp_dir], ok=(0, 1))
            c.run(
                "deliver compare",
                "deliver.py",
                [cmp_dir, "--title", "多店对比报告", "--no-open"],
            )
            c.check_outputs(cmp_dir, "teardown compare")


CHAINS = {
    "profit-model": chain_profit_model,
    "payments-readiness": chain_payments,
    "competitor-change-radar": chain_radar,
    "launch-readiness": chain_launch,
    "tracking-audit": chain_tracking,
    "shopify-store-teardown": chain_teardown,
}


# ---------------------------------------------------------------- utils


def documents_dir():
    if sys.platform.startswith("win"):
        return os.path.join(
            os.environ.get("USERPROFILE") or os.path.expanduser("~"), "Documents"
        )
    return os.path.join(os.path.expanduser("~"), "Documents")


def listdir(path):
    try:
        return os.listdir(path)
    except OSError:
        return []


def raw_dir(run):
    for name in ("原始数据", "data"):
        p = os.path.join(run, name)
        if os.path.isdir(p):
            return p
    return run


def add_stress(body, line):
    lines = body.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("## "):
            lines.insert(i + 1, "")
            lines.insert(i + 2, line)
            break
    else:
        lines.append(line)
    return "\n".join(lines) + "\n"


def console_cp():
    if sys.platform.startswith("win"):
        try:
            import ctypes

            return "cp%d" % ctypes.windll.kernel32.GetACP()
        except Exception:  # noqa: BLE001
            pass
    return locale.getpreferredencoding(False)


def decodable(data):
    for enc in ("utf-8", console_cp()):
        try:
            data.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def text(data):
    enc = decodable(data) or "utf-8"
    return data.decode(enc, "replace")


def starts_with_bom(path):
    with open(path, "rb") as fh:
        return fh.read(3) == b"\xef\xbb\xbf"


FIXTURE_SOURCES = (
    # (repo, source path, destination under the fixtures dir)
    (
        "core",
        "skills/profit-model/tests/fixtures/healthy/inputs.csv",
        "profit-model/healthy.csv",
    ),
    (
        "core",
        "skills/profit-model/tests/fixtures/english/inputs.csv",
        "profit-model/english.csv",
    ),
    ("pro", "tests/fixtures/profit-model/multi_sku.csv", "profit-model/multi_sku.csv"),
    ("core", "skills/launch-readiness/tests/fixtures/F3-paste", "F3-paste"),
    (
        "core",
        "skills/tracking-audit/tests/fixtures/well_tracked",
        "tracking-audit/well_tracked",
    ),
    (
        "core",
        "skills/tracking-audit/tests/fixtures/duplicated_broken",
        "tracking-audit/duplicated_broken",
    ),
    ("pro", "tests/fixtures/tracking/exports", "tracking/exports"),
    (
        "pro",
        "tests/fixtures/alpha-outfitters.example",
        "teardown/alpha-outfitters.example",
    ),
    ("pro", "tests/fixtures/beta-home.example", "teardown/beta-home.example"),
    ("pro", "tests/fixtures/gamma-minimal.example", "teardown/gamma-minimal.example"),
)


def make_fixtures(dst, core, pro):
    """Host side: copy the fixtures the chains use into one folder."""
    roots = {"core": core, "pro": pro}
    for repo, src, rel in FIXTURE_SOURCES:
        s = os.path.join(roots[repo], src)
        d = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        if os.path.isdir(s):
            shutil.copytree(s, d, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy(s, d)
    return dst


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["make-fixtures"]:
        mk = argparse.ArgumentParser(prog="deep_chain.py make-fixtures")
        mk.add_argument("dst")
        mk.add_argument("--core", required=True)
        mk.add_argument("--pro-repo", required=True)
        a = mk.parse_args(argv[1:])
        print(make_fixtures(os.path.abspath(a.dst), a.core, a.pro_repo))
        return 0
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--skill", required=True, choices=sorted(CHAINS))
    ap.add_argument("--skill-dir", required=True)
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--out-root", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--pro", action="store_true")
    args = ap.parse_args(argv)
    c = Chain(args)
    print(
        "python %s utf8_mode=%s preferred=%s"
        % (
            sys.version.split()[0],
            sys.flags.utf8_mode,
            locale.getpreferredencoding(False),
        ),
        flush=True,
    )
    try:
        CHAINS[args.skill](c)
    except Exception as e:  # noqa: BLE001 - record and keep the evidence
        c.note("driver", False, "%s: %s" % (type(e).__name__, e))
    return c.finish()


if __name__ == "__main__":
    sys.exit(main())
