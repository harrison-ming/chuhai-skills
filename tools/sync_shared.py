#!/usr/bin/env python3
"""Copy <repo>/shared/ into every skill as skills/<name>/scripts/_shared/.

Each skill zip must be self-contained, so the shared library is committed
inside every skill. Edit only <repo>/shared/, then run this script.

    python tools/sync_shared.py           copy (adds, updates, deletes)
    python tools/sync_shared.py --check   exit 1 when any copy is out of sync

Skills without a scripts/ folder are skipped. skills/_template/ is synced
too, so new skills start with the current copy.
Python 3.8+, standard library only.
"""

import argparse
import filecmp
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARED = os.path.join(ROOT, "shared")
SKILLS = os.path.join(ROOT, "skills")
DEST_NAME = "_shared"


def source_files(src=SHARED):
    """Relative paths of the shared library (python files only, no caches)."""
    out = []
    for base, dirs, names in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(names):
            if name.endswith(".py"):
                out.append(os.path.relpath(os.path.join(base, name), src))
    return out


def targets(skills_dir=SKILLS):
    out = []
    for name in sorted(os.listdir(skills_dir)):
        scripts = os.path.join(skills_dir, name, "scripts")
        if os.path.isdir(scripts):
            out.append(os.path.join(scripts, DEST_NAME))
    return out


def diff(dest, src=SHARED):
    """List of human-readable differences between src and dest."""
    problems = []
    want = set(source_files(src))
    have = set(source_files(dest)) if os.path.isdir(dest) else set()
    for rel in sorted(want - have):
        problems.append("missing %s" % rel)
    for rel in sorted(have - want):
        problems.append("extra %s" % rel)
    for rel in sorted(want & have):
        a, b = os.path.join(src, rel), os.path.join(dest, rel)
        if not filecmp.cmp(a, b, shallow=False):
            problems.append("changed %s" % rel)
    return problems


def sync(dest, src=SHARED):
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    for rel in source_files(src):
        target = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(os.path.join(src, rel), target)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Sync shared/ into every skill.")
    ap.add_argument("--check", action="store_true", help="only report drift")
    args = ap.parse_args(argv)
    bad = 0
    for dest in targets():
        rel = os.path.relpath(dest, ROOT)
        problems = diff(dest)
        if not problems:
            print("ok      %s" % rel)
            continue
        if args.check:
            bad += 1
            print("DRIFT   %s: %s" % (rel, "; ".join(problems)))
        else:
            sync(dest)
            print("synced  %s (%d changes)" % (rel, len(problems)))
    if bad:
        print("run: python tools/sync_shared.py")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
