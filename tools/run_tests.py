#!/usr/bin/env python3
"""Run every test suite: tests/ at the repo root, then skills/<name>/tests/.

Each suite runs in its own interpreter (skills share module names such as
collect.py). skills/_template/ is skipped here; tests/test_template.py
generates a skill from it and runs that instead.

    python tools/run_tests.py [-v]
Python 3.8+, standard library only.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def suites():
    out = [os.path.join(ROOT, "tests")]
    skills = os.path.join(ROOT, "skills")
    for name in sorted(os.listdir(skills)):
        tests = os.path.join(skills, name, "tests")
        if not name.startswith("_") and os.path.isdir(tests):
            out.append(tests)
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    failed = []
    for path in suites():
        rel = os.path.relpath(path, ROOT)
        print("== %s" % rel, flush=True)
        cmd = [sys.executable, "-m", "unittest", "discover", "-s", path, "-t", path]
        if subprocess.call(cmd + list(argv), cwd=ROOT) != 0:
            failed.append(rel)
    if failed:
        print("FAILED: %s" % ", ".join(failed))
        return 1
    print("all suites passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
