#!/usr/bin/env python3
"""Build the free release zips: one ``<skill>-<suffix>.zip`` per skill.

Each zip holds a single top-level folder ``<skill>/`` (upload-ready for
Claude.ai / Coze). Built with the standard zipfile module instead of Info-ZIP
``zip``: zipfile sets the UTF-8 name flag (general-purpose bit 11) on every
non-ASCII entry, so Windows and other unzip tools do not turn such names into
mojibake. Info-ZIP on macOS does not set it.

Deterministic: entries sorted, fixed timestamp (SOURCE_DATE_EPOCH when set),
fixed permissions and creator system, no directory entries.

Never shipped: tests/, acceptance.json, __pycache__/, *.pyc, hidden files
(.DS_Store, .pytest_cache ...) and skills starting with "_" (the template).

    python tools/build_free.py [--out dist] [--suffix v0.2.1] [--skill NAME ...]

Without --suffix each zip is named after its SKILL.md metadata version
(``<skill>-v<version>.zip``); the release workflow passes the git tag.

Python 3.8+, standard library only.
"""

import argparse
import os
import re
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, "skills")
EXCLUDE_DIRS = {"tests", "__pycache__"}
EXCLUDE_FILES = {"acceptance.json"}
EXCLUDE_SUFFIXES = (".pyc", ".pyo")
DEFAULT_DATE = (2026, 1, 1, 0, 0, 0)
VERSION_RE = re.compile(r'^\s+version:\s*"?([0-9][^"\s]*)"?\s*$', re.M)


class BuildError(Exception):
    pass


def zip_date():
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch and epoch.isdigit():
        t = time.gmtime(max(int(epoch), 315532800))  # zip epoch is 1980
        return tuple(t[:6])
    return DEFAULT_DATE


def skill_names():
    return sorted(
        n
        for n in os.listdir(SKILLS)
        if not n.startswith(("_", "."))
        and os.path.isfile(os.path.join(SKILLS, n, "SKILL.md"))
    )


def skill_version(skill_dir):
    with open(os.path.join(skill_dir, "SKILL.md"), encoding="utf-8") as fh:
        m = VERSION_RE.search(fh.read())
    if not m:
        raise BuildError("SKILL.md has no metadata version: %s" % skill_dir)
    return m.group(1)


def excluded(rel):
    """rel is relative to the skill folder, "/" separated."""
    parts = rel.split("/")
    if any(p in EXCLUDE_DIRS or p.startswith(".") for p in parts):
        return True
    if len(parts) == 1 and parts[0] in EXCLUDE_FILES:
        return True
    return rel.endswith(EXCLUDE_SUFFIXES)


def collect(skill_dir):
    """Sorted list of (relative path, absolute path) to ship."""
    out = []
    for base, dirs, files in os.walk(skill_dir):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS)
        for name in files:
            path = os.path.join(base, name)
            rel = os.path.relpath(path, skill_dir).replace(os.sep, "/")
            if not excluded(rel):
                out.append((rel, path))
    return sorted(out)


def build_one(skill_dir, zip_path, top=None):
    """Write one skill folder as a zip; returns the list of entry names."""
    top = top or os.path.basename(os.path.normpath(skill_dir))
    files = collect(skill_dir)
    if not any(rel == "SKILL.md" for rel, _ in files):
        raise BuildError("missing SKILL.md: %s" % skill_dir)
    date = zip_date()
    if os.path.exists(zip_path):
        os.remove(zip_path)
    names = []
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, path in files:
            info = zipfile.ZipInfo(top + "/" + rel, date_time=date)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3  # unix, so the mode bits below apply
            mode = 0o755 if os.access(path, os.X_OK) else 0o644
            info.external_attr = (0o100000 | mode) << 16
            with open(path, "rb") as fh:
                zf.writestr(info, fh.read(), compresslevel=9)
            names.append(info.filename)
    verify(zip_path, top)
    return names


def verify(zip_path, top):
    """Single top folder, SKILL.md present, UTF-8 flag on non-ASCII names."""
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        tops = {i.filename.split("/", 1)[0] for i in infos}
        if tops != {top}:
            raise BuildError("zip top level is not only %s: %s" % (top, sorted(tops)))
        if top + "/SKILL.md" not in zf.namelist():
            raise BuildError("zip has no %s/SKILL.md" % top)
        for i in infos:
            if not i.filename.isascii() and not i.flag_bits & 0x800:
                raise BuildError("non-ASCII name without UTF-8 flag: " + i.filename)
            rel = i.filename.split("/", 1)[1]
            if excluded(rel):
                raise BuildError("zip contains an excluded file: " + i.filename)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"), help="output folder")
    ap.add_argument("--suffix", help="zip name suffix, e.g. a tag v0.2.1")
    ap.add_argument("--skill", action="append", help="only this skill (repeatable)")
    args = ap.parse_args(argv)
    names = args.skill or skill_names()
    os.makedirs(args.out, exist_ok=True)
    try:
        for name in names:
            skill_dir = os.path.join(SKILLS, name)
            if name.startswith("_") or not os.path.isdir(skill_dir):
                raise BuildError("not a shippable skill: %s" % name)
            suffix = args.suffix or "v" + skill_version(skill_dir)
            zip_path = os.path.join(args.out, "%s-%s.zip" % (name, suffix))
            entries = build_one(skill_dir, zip_path, name)
            print("built %s (%d files)" % (zip_path, len(entries)))
    except BuildError as exc:
        print("build failed: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
