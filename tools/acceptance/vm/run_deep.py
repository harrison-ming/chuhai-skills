#!/usr/bin/env python3
"""Deep script round on a test VM: the whole delivery chain per package.

    python3 tools/acceptance/vm/run_deep.py [--bench <bench id>] \
        --pro-repo <chuhai-skills-pro checkout> <zip> [<zip> ...]

For each skill zip (free or Pro) this writes a one-case acceptance file
(case "deep", script runner only) whose steps unpack the fixtures and run
deep_chain.py inside the guest, then calls run_vm.py with deep_chain.py and
the fixtures zip delivered as --extra files. Verdicts and evidence land
where run_vm.py puts them; each case folder holds deep-result.json (one row
per command, with exit code, problems and stderr tail) and default-root/
(whatever the skill wrote to Documents when CHUHAI_OUTPUT_DIR was unset).
Python 3.8+, stdlib only.
"""

import argparse
import datetime
import json
import os
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
CORE = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))

import deep_chain  # noqa: E402
import run_vm  # noqa: E402

OUT_BASE = os.path.join(os.path.expanduser("~"), "STELLAR_OUTPUT", "_tmp", "acceptance")


def fixtures_zip(dst_dir, pro_repo):
    src = os.path.join(dst_dir, "fixtures")
    shutil.rmtree(src, ignore_errors=True)
    deep_chain.make_fixtures(src, CORE, pro_repo)
    path = os.path.join(dst_dir, "deep-fixtures.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for base, _dirs, names in os.walk(src):
            for n in sorted(names):
                full = os.path.join(base, n)
                z.write(full, os.path.relpath(full, src))
    shutil.rmtree(src)
    return path


def acceptance_for(name):
    base = name[: -len("-pro")] if name.endswith("-pro") else name
    argv = [
        "{python}",
        "{round_dir}\\deep_chain.py",
        "--skill",
        base,
        "--skill-dir",
        "{skill_dir}",
        "--fixtures",
        "{work_dir}\\fixtures",
        "--out-root",
        "{out_root}",
        "--work",
        "{work_dir}",
    ]
    if name.endswith("-pro"):
        argv.append("--pro")
    return {
        "skill": name,
        "cases": [{"id": "deep", "prompt": "deep script round", "needs_network": True}],
        "script_steps": [
            {
                "argv": [
                    "{python}",
                    "-c",
                    "import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])",
                    "{round_dir}\\deep-fixtures.zip",
                    "{work_dir}\\fixtures",
                ]
            },
            {"argv": argv},
        ],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--bench", help="bench id; default: the single ACTIVE skill_acceptance bench"
    )
    ap.add_argument("--pro-repo", required=True)
    ap.add_argument("--timeout", type=int, default=3000, help="seconds per package")
    ap.add_argument("--label", default="deep")
    ap.add_argument("zips", nargs="+")
    args = ap.parse_args(argv)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    if not args.bench:
        args.bench = run_vm.load_bench(run_vm.DEFAULT_BENCH_JSON)["id"]
    top = os.path.join(OUT_BASE, "%s-%s-%s" % (args.label, args.bench, stamp))
    os.makedirs(top)
    fx = fixtures_zip(top, os.path.abspath(args.pro_repo))
    driver = os.path.join(HERE, "deep_chain.py")
    summary = []
    for z in args.zips:
        name, _ver = run_vm.zip_identity(z)
        acc = os.path.join(top, "%s.acceptance.json" % name)
        with open(acc, "w", encoding="utf-8") as fh:
            json.dump(acceptance_for(name), fh, ensure_ascii=False, indent=1)
        out = os.path.join(top, name)
        rc = run_vm.main(
            [
                "--bench",
                args.bench,
                "--skill-zip",
                z,
                "--acceptance",
                acc,
                "--runners",
                "script",
                "--timeout",
                str(args.timeout),
                "--out",
                out,
                "--extra",
                driver,
                "--extra",
                fx,
            ]
        )
        summary.append({"package": name, "rc": rc, "out": out})
        print("== %s rc=%s" % (name, rc), flush=True)
    with open(os.path.join(top, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
    print(top)
    return 0 if all(s["rc"] == 0 for s in summary) else 1


if __name__ == "__main__":
    sys.exit(main())
